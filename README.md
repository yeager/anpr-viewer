# ANPR Viewer

License plate recognition from video streams and files using Tesseract OCR and ffmpeg.

Built with GTK4/Adwaita. Part of the [Danne L10n Suite](https://github.com/yeager/debian-repo).

## Features

- **Video files** — MP4, AVI, MKV, MOV, WebM, and more
- **Live cameras** — Webcams and USB capture devices (V4L2 / AVFoundation)
- **Stream URLs** — RTSP, RTMP, HTTP/HTTPS direct video URLs
- **YouTube & more** — Any URL supported by yt-dlp (1000+ sites)
- **Drag & drop** — Drop files or URLs to start scanning immediately
- **Video preview** — See the video during analysis, synced with scan progress
- **Real-time detection** — Plates listed as they are found
- **Export** — Save results as CSV or JSON
- **EU plate display** — Swedish/EU-style plate rendering

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

## Installation

### Debian/Ubuntu
```bash
sudo apt install anpr-viewer
```

### Fedora/RPM
```bash
sudo dnf install anpr-viewer
```

### yt-dlp (for YouTube support)
```bash
pip3 install yt-dlp
```

## License

GPL-3.0

## Author

Daniel Nylander — [danielnylander.se](https://danielnylander.se)
