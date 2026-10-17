# Iris dependencies

This document lists dependencies from [the team plan](plan.md), plus the model runtime libraries needed for the planned OWLv2 baseline. Install the dependencies for the component you are working on. Package versions are not pinned yet; this document is a setup reference, not a reproducible environment lockfile.

## Python environment

Use Python and pip in a virtual environment. From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

On macOS or Linux, activate it with `source .venv/bin/activate` instead.

## Server — Person B

```shell
python -m pip install ultralytics fastapi uvicorn python-multipart opencv-python
```

| Package | Purpose |
| --- | --- |
| `ultralytics` | YOLO26 inference and model loading |
| `fastapi` | Frame upload API and server endpoints |
| `uvicorn` | Runs the FastAPI application |
| `python-multipart` | Parses multipart JPEG uploads |
| `opencv-python` | Image decoding, annotation, and frame processing |
| `google-genai` | Optional Gemini scene descriptions |

For the Gemini integration:

```shell
python -m pip install google-genai
```

The plan assigns the server owner responsibility for recording the server environment in `server/requirements.txt`.

## Model training — Person C

```shell
python -m pip install ultralytics albumentations
```

| Package or resource | Purpose |
| --- | --- |
| `ultralytics` | Fine-tunes the YOLO26 detector |
| `albumentations` | Adds rain, fog, blur, darkness, and noise to training images |
| Roboflow account | Accesses and exports selected datasets in YOLO format |
| Colab or Vultr GPU access | Runs the planned training jobs |
| `yolo26s.pt` | Starting checkpoint for the plain and storm-augmented runs |

C provides the merged dataset, final class names, held-out test split, and both trained checkpoints to the relevant teammates.

## Model evaluation — Person D

```shell
python -m pip install ultralytics transformers torch torchvision pillow opencv-python
```

| Package | Purpose |
| --- | --- |
| `ultralytics` | Evaluates stock and fine-tuned YOLO models |
| `transformers` | Loads the OWLv2 processor and model |
| `torch` and `torchvision` | Model inference runtime and vision operations |
| `pillow` | Image loading and conversion for the OWLv2 workflow |
| `opencv-python` | Reads demo clips and processes video frames |

PyTorch is also used by Ultralytics. For GPU execution, select a PyTorch build compatible with the machine's GPU and drivers using the [official installation selector](https://pytorch.org/get-started/locally/).

Required evaluation assets:

- Stock checkpoint: `yolo26s.pt`.
- Zero-shot model: `google/owlv2-base-patch16-ensemble` from Hugging Face.
- C's plain and storm-augmented checkpoints, with their class names.
- C's held-out test dataset and independent demo clips.

Model downloads require network access on first use.

### Video conversion

Install the standalone FFmpeg executable with `libx265` support and make it available on `PATH`. A Python FFmpeg wrapper alone does not provide this executable.

The planned demo conversion is:

```shell
ffmpeg -i clip.mp4 -c:v libx265 -tag:v hvc1 clip_h265.mp4
```

## iOS app — Person A

| Tool or resource | Purpose |
| --- | --- |
| Mac with Xcode | Builds and runs the iOS app |
| iPhone with Developer Mode enabled | Runs the app on a physical device |
| [Meta Wearables DAT iOS SDK and CameraAccess sample](https://github.com/facebook/meta-wearables-dat-ios) | Camera access and starting app integration |
| Meta Mock Device Kit | Supplies mock camera footage for development and demos |
| Wearables Developer Center organization, project, and Application ID | Configures the Meta integration |
| Meta glasses | Supplies the camera feed and audio output for the intended wearable workflow |

The app uses Apple's `URLSession` for frame uploads and `AVSpeechSynthesizer` for speech. These are platform APIs rather than pip packages. The plan also requires local-network permissions in the app configuration.

## Optional services and shared resources

| Service or resource | Purpose |
| --- | --- |
| Google AI Studio / Gemini API key | Enables optional scene descriptions |
| Vultr Ubuntu instance | Hosts the server remotely; a laptop is the local fallback |
| Shared Google Drive folder | Stores datasets, weights, and demo clips outside Git |
| Git and individual GitHub credentials | Repository collaboration |

Keep API keys and other credentials out of Git. The plan specifies environment variables and a local `.env` file for secrets; any automatic `.env` loading must be configured by the server implementation.
