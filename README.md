# ANPR Viewer

GTK4/Adwaita application for automatic license plate recognition (ANPR) from video files and streams.

## Features

- Open video files (MP4, AVI, MKV, MOV, etc.)
- Open RTSP/HTTP video streams
- Drag & drop video files
- Real-time plate detection with Tesseract OCR
- Plate list with timestamps and confidence scores
- Copy plates to clipboard
- Export log as CSV
- Configurable OCR engine and confidence threshold
- Swedish/English UI

## Dependencies

- Python 3.10+
- GTK4, libadwaita
- Tesseract OCR (`brew install tesseract` / `apt install tesseract-ocr`)
- FFmpeg (`brew install ffmpeg` / `apt install ffmpeg`)

## Install

```bash
pip install -e .
anpr-viewer
```

## Screenshot

![ANPR Viewer](screenshots/anpr-viewer_en.png)

## License

GPL-3.0-or-later
