# Disaster-condition demo clips

Start with the two aerial candidates below for distant-person review, followed by the three closer survivor candidates. All MP4s are local in `data/demo_clips/`. The earlier four clips remain intact as extra integration footage. Footage, originals, and generated manifests are ignored by Git; share them separately through the team's Drive with this guide and their credits. [demo_sources.json](demo_sources.json) records download URLs, source hashes, trim/crop recipes, conditions, and selection roles.

## User-provided lake-with-rain clip

`lakewithrain.mp4` is the original 17.92-second, 3840 x 2160 H.264 upload at about 59.94 fps. It is preserved unchanged. Use `lakewithrain_mock.mp4` (17.917 seconds, 960 x 540, 24 fps HEVC Main/hvc1) for mock-camera playback. No crop or added weather effect was applied. The converted copy fully decoded with no frame reordering and a largest packet of 19,610 bytes, below the 200,000-byte limit. The original also passed full decoding.

The scene shows distant people in a small boat through heavy rain and low visibility. This is a challenging visible-person candidate; the source, author/license, authenticity, and actual survivor/disaster context are not yet confirmed. It is not an aerial view. C still needs to check training overlap.

Both filenames have matching `.manifest.json`, `.review.draft.json`, and `.sightings.draft.json` files. The draft presence key proposes a person visible from 0 to that version's actual duration. One-second review samples support that proposal, but watch full playback before approval. The review JSON leaves the exact person count and person boxes unknown. After correcting/approving, rename `.sightings.draft.json` to `.sightings.json` for replay; `--expect` takes the sightings list, not the manifest or review object. These intervals test person-presence alerts; they cannot measure bounding-box mAP.

Example from `server/`, after reviewing the draft and saving the approved sightings file:

```powershell
python -m scripts.replay ../data/demo_clips/lakewithrain_mock.mp4 --url http://localhost:8001 --expect ../data/demo_clips/lakewithrain_mock.sightings.json --preflight
```

The original and converted copy are one scene, not independent test samples. Provenance fields remain unknown pending the uploader's source details.

## Aerial and distant-person candidates

| File | Length | What to look for |
| --- | --- | --- |
| `aerial_harvey_distant_flood_group.mp4` | 6.96 s | Small figures in floodwater among trees and submerged street features, viewed from a helicopter. This is the harder small-person case; water obscures much of each body. |
| `aerial_harvey_rooftop_survivors.mp4` | 16 s | People at the outer corner of a shingled roof surrounded by floodwater, viewed from above. Targets are larger than in the first clip, making it a useful comparison. |

The full source frames are preserved without added target crops or zoom; original camera motion and changes of framing remain. These are actual aerial viewpoints, not artificially shrunken people. Camera altitude and ground distance are unknown, so do not report an estimated distance in metres. Both sources cover Hurricane Harvey, with separate footage; C still needs to check source overlap and near duplicates. Their `*.review.draft.json` files identify the intended people but leave unreviewed intervals and boxes as `null`. Neither is scored yet.

A detection here means a visible person was found. These clips do not establish that the system can predict where an unseen survivor is likely to be. Keep that claim separate from person-detection results.

Both clips passed full decoding, 24 fps HEVC Main/hvc1, no frame reordering, and the mock-camera packet limit. Peak packets: 25,079 bytes (distant flood group) and 30,748 bytes (rooftop), below 200,000 bytes. The first requested 7-second trim yields 167 frames (6.958 seconds); annotate against the actual output duration.

## Closer survivor candidates to review

| File | Length | Visible target and conditions | Limitation |
| --- | --- | --- | --- |
| `rubble_survivor_extraction.mp4` | 20 s | Survivor in light trousers with a blue covering, on a rescue board, emerging beneath a low collapsed structure. Partial body visibility, debris, and rescuer occlusion. | Extraction is already underway. Rescuers remain prominent; this is not initial discovery of a buried person. |
| `florence_rooftop_survivor.mp4` | 24 s | Barefoot man in black sleeveless shirt and shorts, stranded on a roof after Hurricane Florence, entering a rescue basket. Camera motion and partial views from a rescuer's camera. | Selected view shows the roof and survivor; surrounding flooding is not continuously visible. |
| `harvey_night_flood_survivors.mp4` | 17 s | Civilians, including a person in yellow outerwear, wading through floodwater at night during Hurricane Harvey rescue operations. Darkness, reflections, and water obscure parts of people. | Responders are also present. Heavy rainfall is not established by the selected frames. |

These were selected by visible scene content, independently of model predictions. They have not been scored. All three still require full playback review and C's training-overlap check. They do not cover every condition: an initial-search rubble sequence with a survivor visible before rescuers arrive and a clear heavy-rain survivor sequence remain gaps. Artificial darkness/rain effects were not added.

Each candidate has a `*.review.draft.json` beside it, identifying the survivor target, scene conditions, and proposed clip-relative sighting intervals. Those intervals are draft suggestions based on sampled review; the rubble target may initially be fully obscured. No target boxes have been finalized. Unknown annotations are `null`, never an empty list meaning a confirmed negative.

## Earlier footage retained as extras

| File | Length | Use |
| --- | --- | --- |
| `rubble_rescuers.mp4` | 12 s | Basic person detection and integration; visible rescuers, no verified visible trapped survivor. |
| `flood_rescue.mp4` | 12 s | Basic person detection of the helicopter rescue crew. |
| `rainy_night_street.mp4` | 6 s | Exploratory dark/rainy scene with small pedestrians near the bottom. No established disaster/survivor context. |
| `rainy_night_negative.mp4` | 6 s | Same rainy scene with bottom quarter removed to exclude pedestrians; draft no-person control. |

The original `*.sightings.draft.json` files are retained for integration review only. They are not approved survivor answer keys. The two rainy files share a source and must not count as independent scenes. These MP4s and their existing video hashes were preserved.

Downloaded candidates not selected for the pack remain under `raw/`: the Hatay extraction is short and heavily obscured by rescuers; the Fleet Week rubble exercise shows mostly workers; the Matthew aerial sequence is dominated by hoisting operations. A source title alone does not establish a suitable survivor-detection test.

## What counts as success

The current detector labels people. It does not determine medical condition or distinguish a rescuer from a survivor. In these mixed scenes, an alert for any person can come from a rescuer while the actual survivor is missed.

For survivor-specific evaluation, review the complete video, mark the intended survivor's visible intervals, and annotate their bounding boxes/identity on evaluation frames. Match predictions to that target. Preserve ordinary person annotations separately if also computing general person mAP. Do not report a generic presence/replay PASS as proof the survivor was found. Partial visibility and total occlusion must be distinguished; do not require the RGB detector to see a completely hidden person.

Ask C to confirm that none of each original source, extracted frames, or near duplicates appeared in training/validation before reporting independent clip accuracy. Do not train on accepted evaluation clips. Temporal sighting intervals do not supply bounding-box mAP or validate alert direction.

## Checks performed

The nine earlier prepared MP4s passed full-stream decoding and HEVC Main / `hvc1`, 24 fps, even dimensions within 960 x 540 (or portrait equivalent), increasing timestamps without frame reordering, and compressed packets below 200,000 bytes. New clip peak packets were 53,976 bytes (rubble), 32,749 (rooftop), and 23,343 (night flood). Each `.manifest.json` records source/clip SHA-256, conversion command, dimensions, crop, and rights. Review contact sheets are in `runs/demo_previews/candidates/` locally. These technical checks do not establish phone playback or model accuracy.

## Prepare another clip

From the repository root:

```powershell
python -m pip install -r training/requirements-demo.txt
python training/prepare_demo_clip.py path/to/source.mp4 data/demo_clips/new_clip.mp4 --start 15 --duration 12 --source-url SOURCE_PAGE --credit AUTHOR --license LICENSE_NAME --license-url LICENSE_PAGE
```

The helper uses FFmpeg from `PATH`, or the executable bundled by `imageio-ffmpeg`. It trims, removes audio, resizes without upscaling, encodes, validates, and writes a manifest. Existing outputs are refused. SDR input is required; use the app importer for HDR input.

`--crop-box X Y WIDTH HEIGHT` applies a fixed even rectangle before scaling. The rooftop recipe uses `--crop-box 350 0 324 576` to remove baked-in black sidebars; no scene enhancement or upscaling is applied. `--crop-bottom 0.25` removes the bottom quarter, as recorded for the earlier rainy control. Crops must be considered when annotating coordinates. Use `demo_sources.json` to reproduce the trim/crop/credit arguments after downloading each source.

## Handoff to A and B

1. Review the five survivor-candidate MP4s and their `*.review.draft.json` notes. Confirm target identity and visibility before scoring. Keep the older files for extra integration checks.
2. Share MP4s, manifests, reviewed annotations, and this guide through Drive, including source credits.
3. On the iPhone, save a clip to Photos. In Debug / Mock Device Kit controls, pair the mock device, enable its states, and choose Video file as camera source. Wait for preparation, dismiss the debug sheet, then start the session. See [the app guide](../app/README.md).
4. With B's server URL configured, check advancing preview, arriving frames, detections on the intended survivor, and, when Gemini is enabled, spoken scene descriptions. Directional alert text is display-only in the current app. Record model, thresholds, device, server commit, misses, and latency.
5. For general HTTP/alert integration, use the server's Python 3.12+ environment. From `server/`, after reviewing and renaming an extra clip's sightings file:

```powershell
python -m scripts.replay ../data/demo_clips/rubble_rescuers.mp4 --url http://localhost:8001 --expect ../data/demo_clips/rubble_rescuers.sightings.json --preflight
```

Use B's actual URL/port and API-key setup. Replay checks alerts against any-person intervals; it cannot establish which person triggered them. New target review JSON is not the replay `--expect` format. Isolated local HTTP checks and unscored clip replays are recorded in [the MVP handoff](MVP_HANDOFF.md); the live phone/glasses loop still needs team acceptance.

## Source credits

New aerial selections:

- **Distant flood group:** Petty Officer 3rd Class Corinne Zilnicki / U.S. Coast Guard, [Coast Guard rescues 5 stranded in Houston floodwaters](https://www.dvidshub.net/video/546164/coast-guard-rescues-5-stranded-houston-floodwaters), public domain designation on DVIDS. Changes: excerpt 37-44 seconds, resized, audio removed, HEVC conversion.
- **Aerial rooftop:** Carmen Castro / U.S. Customs and Border Protection, [CBP Air and Marine Operations rescues stranded residents](https://www.dvidshub.net/video/546398/cbp-air-and-marine-operations-rescues-stranded-residents), public domain designation on DVIDS. Changes: excerpt 20-36 seconds, resized, audio removed, HEVC conversion.

Earlier survivor selections:

- **Collapsed-building survivor:** Mark CHURMS / U.S. Department of State, [U.S. Search and Rescue Teams Save Woman Trapped in Collapsed Building](https://www.dvidshub.net/video/1012768), public domain designation on DVIDS. Changes: excerpt 258-278 seconds, resized, audio removed, HEVC conversion.
- **Rooftop survivor:** U.S. Coast Guard / Petty Officer 3rd Class Ronald Hodges, [Air Station Elizabeth City rescues man from rooftop](https://www.dvidshub.net/video/626817/air-station-elizabeth-city-rescues-man-rooftop), public domain designation on DVIDS. Changes: excerpt 84-108 seconds, black sidebars removed, audio removed, HEVC conversion.
- **Night flood:** Sgt. Samuel De Leon / Texas National Guard, [Texas National Guard Night Rescues - Hurricane Harvey](https://www.dvidshub.net/video/546494/texas-national-guard-night-rescues-hurricane-harvey), public domain designation on DVIDS. Changes: excerpt 53-70 seconds, resized, audio removed, HEVC conversion.

See [DVIDS use terms](https://www.dvidshub.net/about/copyright). Credits do not imply endorsement.

Earlier extras:

- Prof. Isaac Ashkenazi, [International rescue effort collapsed building](https://commons.wikimedia.org/wiki/File:International_rescue_effort_collapsed_building.webm), [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). Derived clip also CC BY-SA 4.0. Changes: excerpt 15-27 seconds, resized, audio removed, HEVC conversion.
- U.S. Navy, [Hurricane Harvey Search and Rescue](https://commons.wikimedia.org/wiki/File:Hurricane_Harvey_Search_and_Rescue.webm), public domain U.S. government footage. Changes: excerpt 36-48 seconds, resized, audio removed, HEVC conversion.
- Andy Li, [Rainy night in Sham Shui Po](https://commons.wikimedia.org/wiki/File:Rainy_night_in_Sham_Shui_Po.webm), [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/). Changes: first six seconds, resized, audio removed, HEVC conversion; negative variant also removes the bottom quarter.

Footage licenses apply separately from repository code.
