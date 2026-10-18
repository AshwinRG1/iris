# Iris dependencies

Use the requirements file for your component rather than installing every package. The server's requirements are pinned; training dependencies have their own version policy. Datasets, checkpoints, and video footage are additional assets shared outside Git.

| Component | Runtime and install command, from repository root |
| --- | --- |
| Server API | Standard CPython **3.12+**; `python -m pip install -r server/requirements.txt` |
| Server YOLO inference | `python -m pip install -r server/requirements-ml.txt`; on CPU hosts install CPU PyTorch first as described in that file |
| Server development | `python -m pip install -r server/requirements-dev.txt` |
| Model training | `python -m pip install -r training/requirements.txt` |
| YOLO evaluation | Standard CPython 3.11+; `python -m pip install -r training/requirements-eval.txt` |
| OWLv2 evaluation | `python -m pip install -r training/requirements-zeroshot.txt` |
| Demo conversion | `python -m pip install -r training/requirements-demo.txt`; FFmpeg/ffprobe, or the included `imageio-ffmpeg` fallback |
| HTTP integration checker | Evaluation environment plus `httpx==0.28.1` (also installed by the server API requirements) |
| Lightweight evaluation checks | `python -m pip install -r training/requirements-checks.txt` |

Create an environment with standard CPython. On Windows the MSYS Python installation is unsuitable for the standard Windows ML wheels:

```powershell
python3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r server/requirements.txt
```

Select a standard CPython executable installed on your machine; `python3.12` may resolve to MSYS on some setups. On macOS/Linux activate with `source .venv/bin/activate`. Keep local environments outside version control.

## Python packages and assets

FastAPI/Uvicorn serve the API; Pydantic parses settings/replies; Pillow and OpenCV handle frames. Ultralytics/PyTorch run YOLO. `google-genai` is imported lazily for optional Gemini descriptions; install it separately (`python -m pip install google-genai`) if using that feature. It requires a Gemini API key configured in the server environment. No key is needed for a visual-only detector trial.

C's training uses Ultralytics and Albumentations. The evaluation tooling adds fixed-threshold precision/recall/F2, AP export, dataset audits, and optional OWLv2 via Transformers. See [EVALUATION.md](training/EVALUATION.md) and [OWLV2.md](training/OWLV2.md). Model downloads require network access on first use.

Required assets are stock `yolo26s.pt`, C's `best_plain_v2.pt` and `best_storm_v2.pt`, the repaired test images/labels, C's exact split records, and reviewed demo footage. Weights belong in `server/weights/` on the actual server host. The [MVP handoff](docs/MVP_HANDOFF.md) records hashes and trial settings.

## iOS and hardware

Use the versions and setup instructions in [app/README.md](app/README.md): iOS 17.2+, Xcode 26.4+, Swift 6.3+, the Meta Wearables Device Access Toolkit, and Meta Ray-Ban Display glasses or the Mock Device Kit. A macOS/Xcode environment is required to build and run the app; Windows Python checks cannot validate physical glasses behavior.

Apple's URLSession and AVSpeechSynthesizer are platform APIs, not pip dependencies. The app's current speech path reads Gemini descriptions; directional server alerts are display-only.

## Shared services

Git/GitHub support collaboration; the team's Drive carries data, weights, footage and large run artifacts. Google AI Studio/Gemini supplies optional descriptions. A Vultr instance can host the server; local hosting remains available. Roboflow provides the selected source exports and Colab/GPU access supports training.

Keep keys in the existing server environment or explicitly configured local env file, and outside Git. Preserve footage credits and licenses when sharing demo clips; see [DEMO_CLIPS.md](docs/DEMO_CLIPS.md).
