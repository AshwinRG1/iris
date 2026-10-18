"""Trim SDR footage into verified HEVC clips for the Iris mock camera."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import uuid

MAX_PACKET_BYTES = 200_000


def ffmpeg_binary():
    binary = shutil.which('ffmpeg')
    if binary:
        return binary
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError as exc:
        raise RuntimeError('Install training/requirements-demo.txt or add ffmpeg to PATH.') from exc


def checksum(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def run(command):
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace')
    if result.returncode:
        raise RuntimeError(result.stderr[-5000:])
    return result


def verify_video(binary, path):
    import cv2
    packet_dump = run([binary, '-hide_banner', '-i', str(path), '-map', '0:v:0',
                       '-c:v', 'copy', '-f', 'framecrc', '-'])
    if 'Video: hevc (Main)' not in packet_dump.stderr or 'hvc1' not in packet_dump.stderr:
        raise ValueError('Expected HEVC Main with hvc1 container tag.')
    packets = []
    for line in packet_dump.stdout.splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        parts = [part.strip() for part in line.split(',')]
        packets.append(tuple(int(value) for value in parts[:5]))
    if not packets:
        raise ValueError('No video packets found.')
    largest = max(packet[4] for packet in packets)
    if largest > MAX_PACKET_BYTES:
        raise ValueError(f'Compressed frame is {largest} bytes; limit is {MAX_PACKET_BYTES}.')
    if any(packet[1] != packet[2] for packet in packets):
        raise ValueError('Reordered video: packet DTS differs from PTS.')
    if any(right[2] <= left[2] for left, right in zip(packets, packets[1:])):
        raise ValueError('Non-increasing presentation timestamps.')
    cap = cv2.VideoCapture(str(path))
    width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    if abs(fps - 24) > 0.01 or min(width, height) <= 0:
        raise ValueError(f'Unexpected video dimensions/frame rate: {width}x{height}, {fps} fps.')
    if max(width, height) > 960 or min(width, height) > 540 or width % 2 or height % 2:
        raise ValueError('Video exceeds the mock converter dimensions or has odd dimensions.')
    # Packet checks alone do not prove the video decodes; validate the full stream too.
    run([binary, '-v', 'error', '-xerror', '-i', str(path), '-map', '0:v:0', '-f', 'null', '-'])
    return {'codec': 'hevc', 'profile': 'Main', 'tag': 'hvc1', 'fps': fps,
            'width': width, 'height': height, 'frames': len(packets),
            'duration_seconds': len(packets)/fps, 'largest_packet_bytes': largest,
            'packet_limit_bytes': MAX_PACKET_BYTES, 'reordered_frames': False,
            'full_decode_passed': True}


def prepare(source, output, *, start=0, duration=12, source_url='', credit='',
            license_name='', license_url='', crop_bottom=0, crop_box=None):
    source, output = source.resolve(), output.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if output.exists() or output.with_suffix('.manifest.json').exists():
        raise FileExistsError(f'Refusing to replace an existing clip/manifest: {output}')
    if output.suffix.lower() != '.mp4' or source == output:
        raise ValueError('Use a new .mp4 output file.')
    import math
    if not math.isfinite(start) or not math.isfinite(duration) or start < 0 or duration < 1:
        raise ValueError('Start must be nonnegative; duration must be at least one second.')
    if not math.isfinite(crop_bottom) or not 0 <= crop_bottom < 0.9:
        raise ValueError('Crop bottom must be a fraction from 0 to less than 0.9.')
    if crop_box is not None:
        if len(crop_box) != 4 or any(not isinstance(v, int) for v in crop_box):
            raise ValueError('Crop box must be four integers: X Y WIDTH HEIGHT.')
        x, y, w, h = crop_box
        import cv2
        cap = cv2.VideoCapture(str(source))
        source_w, source_h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()
        if x < 0 or y < 0 or w < 2 or h < 2 or x+w > source_w or y+h > source_h or any(v % 2 for v in crop_box):
            raise ValueError('Use an even crop box inside the source image.')
    binary = ffmpeg_binary()
    description = subprocess.run([binary, '-hide_banner', '-i', str(source)],
                    capture_output=True, text=True, encoding='utf-8', errors='replace').stderr
    if any(transfer in description for transfer in ('smpte2084', 'arib-std-b67')):
        raise ValueError('HDR source: use the app\'s SDR-converting importer first.')
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name('.' + output.stem + '-' + uuid.uuid4().hex + '.tmp.mp4')
    original_hash = checksum(source)
    last_error = None
    for long_side, bitrate in [(960, 1200), (640, 600)]:
        short_side = long_side*9//16
        factor = f'min(1,min({long_side}/max(iw,ih),{short_side}/min(iw,ih)))'
        box_filter = f"crop={crop_box[2]}:{crop_box[3]}:{crop_box[0]}:{crop_box[1]}," if crop_box else ""
        filters = (box_filter + (f"crop=iw:trunc(ih*(1-{crop_bottom})/2)*2:0:0," if crop_bottom else "") + "scale=w='max(2,trunc(iw*sar/2)*2)':h=ih,setsar=1,"
                   f"scale=w='max(2,trunc(iw*{factor}/2)*2)':"
                   f"h='max(2,trunc(ih*{factor}/2)*2)',fps=24,format=yuv420p")
        command = [binary, '-hide_banner', '-loglevel', 'error', '-y', '-ss', str(start),
                   '-i', str(source), '-t', str(duration), '-map', '0:v:0', '-an', '-sn', '-dn',
                   '-map_metadata', '-1', '-vf', filters, '-c:v', 'libx265', '-preset', 'veryfast',
                   '-profile:v', 'main', '-pix_fmt', 'yuv420p', '-b:v', f'{bitrate}k',
                   '-maxrate', f'{bitrate}k', '-bufsize', f'{bitrate}k', '-x265-params',
                   'bframes=0:keyint=24:min-keyint=24:scenecut=0:open-gop=0:repeat-headers=1:pools=4:frame-threads=2:log-level=error',
                   '-tag:v', 'hvc1', '-movflags', '+faststart', str(temp)]
        run(command)
        try:
            info = verify_video(binary, temp)
            if info['duration_seconds'] < 1:
                raise ValueError('Clip is shorter than one second; check the source start time.')
        except ValueError as exc:
            last_error = exc
            continue
        if checksum(source) != original_hash:
            raise RuntimeError('Source changed during encoding.')
        temp.replace(output)
        manifest = {'created_utc': datetime.now(timezone.utc).isoformat(),
            'source': str(source), 'source_sha256': original_hash,
            'source_url': source_url, 'credit': credit, 'license': license_name,
            'license_url': license_url, 'source_start_seconds': start,
            'requested_duration_seconds': duration, 'crop_bottom_fraction': crop_bottom, 'crop_box_xywh': crop_box,
            'changes': 'Trimmed, cropped if requested, resized, made silent, and transcoded to 24 fps HEVC Main.',
            'output': str(output), 'sha256': checksum(output), 'bytes': output.stat().st_size,
            'video': info, 'command': command,
            'training_independence': 'Candidate independent footage; confirm with C before reporting clip accuracy.',
            'annotations': 'No automatic ground truth. Review and add clip-relative sightings separately.'}
        output.with_suffix('.manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
        print(json.dumps({'clip': str(output), **info}, indent=2), flush=True)
        return manifest
    raise RuntimeError(f'Both encodes failed validation: {last_error}. Temporary file: {temp}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--start', type=float, default=0)
    parser.add_argument('--duration', type=float, default=12)
    parser.add_argument('--crop-box', type=int, nargs=4, metavar=('X', 'Y', 'WIDTH', 'HEIGHT'), help='Crop a fixed even rectangle before bottom cropping and scaling.')
    parser.add_argument('--crop-bottom', type=float, default=0, help='Fraction to remove from the bottom before scaling (default: 0).')
    parser.add_argument('--source-url', default='')
    parser.add_argument('--credit', default='')
    parser.add_argument('--license', dest='license_name', default='')
    parser.add_argument('--license-url', default='')
    args = parser.parse_args()
    try:
        prepare(**vars(args))
    except (ValueError, FileNotFoundError, FileExistsError, RuntimeError) as exc:
        parser.exit(1, f'Clip preparation failed: {exc}\n')


if __name__ == '__main__':
    main()
