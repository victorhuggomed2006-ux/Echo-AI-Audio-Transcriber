# Changelog

## 1.0.2

Maintenance update. No changes to features.

- Updated dependencies: FastAPI 0.162, pydantic-settings 2.16.2, pytest 9.2, mypy 2.4.1 and the optional NVIDIA cuBLAS and cuDNN packages.
- `av` stays below version 16 on purpose: version 19 removes an argument that faster-whisper still passes when it opens an audio file, which breaks transcription (found while testing the update). Dependabot is set to ignore it until faster-whisper catches up.
- The code now passes `mypy --strict`, and ruff requires a type annotation on every function.
- The test suite also runs on Windows (portable fake programs; the model-unloading timer no longer depends on the clock resolution).
- Added a gitleaks configuration and a fuller `.gitignore`.

## 1.0.1

Security update. No changes to features.

- Rebuilt the installers with updated libraries that fix known vulnerabilities (certifi, filelock, gitpython, opentelemetry-api, setuptools).
- The dependency audit (`pip-audit`) now passes in CI.

## 1.0.0

The first public release of Echo-AI (Echo AI Audio Transcriber).

- Transcribe any audio or video file, or record from the microphone, on your own computer with Whisper.
- Live transcript with timestamps, playback with the current line highlighted, `.txt` and `.srt` export.
- Ask a local AI (1 to 8 billion parameters) about one recording or your whole history, with cited excerpts.
- Six Whisper models and six AIs compared side by side, downloaded from the model picker with a progress bar.
- Automatic GPU or CPU; optional NVIDIA acceleration package and AI engine installed by the app.
- Interface in English, Portuguese and Spanish; dark and light themes.
- Installers for Windows (MSI), macOS (DMG) and Ubuntu (DEB), with the Whisper Small model included.
- No administrator rights needed on Windows and macOS, or install from the terminal on any system with `uv`.
