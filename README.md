# ANPR Viewer

![Version](https://img.shields.io/badge/version-0.4.0-blue)
![License](https://img.shields.io/badge/license-GPL--3.0-green)
![Python](https://img.shields.io/badge/python-3.10+-blue)

## Description

GTK4 application for automatic license plate recognition (ANPR) from video streams and files. ANPR Viewer combines Tesseract OCR with ffmpeg to extract and recognize license plates from various video sources, providing a user-friendly interface for security and monitoring applications.

Built with modern GTK4/Adwaita design principles, the application offers real-time processing capabilities and supports multiple video formats for comprehensive license plate analysis, including live streams and popular video sharing platforms.

## Features

- **Video files**: MP4, AVI, MKV, MOV, WebM, and more
- **Live cameras**: Webcams and USB capture devices (V4L2 / AVFoundation)
- **Stream URLs**: RTSP, RTMP, HTTP/HTTPS direct video URLs
- **YouTube & more**: Any URL supported by yt-dlp (1000+ sites)
- **Drag & drop**: Drop files or URLs to start scanning immediately
- **Video preview**: See the video during analysis, synced with scan progress
- **Real-time detection**: Plates listed as they are found
- **Export capabilities**: Save results as CSV or JSON
- **EU plate display**: Swedish/EU-style plate rendering
- **GTK4/Adwaita interface**: Modern, responsive user interface

## Installation

### APT (Debian/Ubuntu)
```bash
echo "deb https://yeager.github.io/debian-repo stable main" | sudo tee /etc/apt/sources.list.d/yeager-l10n.list
curl -fsSL https://yeager.github.io/debian-repo/yeager-l10n.gpg | sudo gpg --dearmor -o /etc/apt/trusted.gpg.d/yeager-l10n.gpg
sudo apt update && sudo apt install anpr-viewer
```

### DNF (Fedora)
```bash
sudo dnf config-manager --add-repo https://yeager.github.io/rpm-repo/yeager-l10n.repo
sudo dnf install anpr-viewer
```

### pip
```bash
pip install anpr-viewer
```

## Building from source

```bash
git clone https://github.com/yeager/anpr-viewer
cd anpr-viewer
pip install -e .
```

## Supported Sources

| Source | Example |
|--------|---------|
| Local files | `/path/to/video.mp4` |
| HTTP/HTTPS | `https://example.com/stream.mp4` |
| RTSP | `rtsp://192.168.1.100:554/stream` |
| RTMP | `rtmp://live.example.com/app/stream` |
| YouTube | `https://youtube.com/watch?v=...` |
| yt-dlp sites | Vimeo, Twitch, Dailymotion, TikTok, etc. |

## Dependencies

- Python 3.10+
- GTK4 / libadwaita
- ffmpeg & ffprobe
- tesseract-ocr
- yt-dlp (optional, for YouTube and other sites)

### yt-dlp (for YouTube support)
```bash
pip3 install yt-dlp
```

## Translation

Translations are managed on Transifex: https://app.transifex.com/danielnylander/anpr-viewer/

Currently supported: Swedish, Danish, German, Spanish, Finnish, French, Italian, Norwegian Bokmål, Dutch, Polish, Portuguese (Brazil)

Contributions welcome!

## Changelog

- **0.4.0**: Added fullscreen support and plugin system
- **0.3.x**: Enhanced video processing and GTK4 interface improvements
- **0.2.x**: Initial Tesseract integration and basic UI
- **0.1.x**: Core video processing and basic ANPR functionality

## License

GPL-3.0-or-later

## Author

Daniel Nylander (daniel@danielnylander.se)