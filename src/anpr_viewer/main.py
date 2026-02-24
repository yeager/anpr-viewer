"""ANPR Viewer — License plate recognition from video streams and files."""
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Gtk, Adw, Gdk, Gio, GLib

import gettext
import locale
import os
import sys
import json
import threading
import time
import datetime
import re
import subprocess
import shutil
import tempfile
from anpr_viewer.accessibility import AccessibilityManager

LOCALE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "po")
if not os.path.isdir(LOCALE_DIR):
    LOCALE_DIR = "/usr/share/locale"
locale.bindtextdomain("anpr-viewer", LOCALE_DIR)
gettext.bindtextdomain("anpr-viewer", LOCALE_DIR)
gettext.textdomain("anpr-viewer")
_ = gettext.gettext

APP_ID = "se.danielnylander.anpr-viewer"
SETTINGS_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    "anpr-viewer"
)
SETTINGS_FILE = os.path.join(SETTINGS_DIR, "settings.json")
LOG_DIR = os.path.join(SETTINGS_DIR, "logs")

# Supported URL schemes
SUPPORTED_SCHEMES = {
    "file": _("Local video files (mp4, avi, mkv, mov, webm, etc.)"),
    "http/https": _("Direct video URLs and web streams"),
    "rtsp": _("Real-Time Streaming Protocol (IP cameras)"),
    "rtmp": _("Real-Time Messaging Protocol (live streams)"),
    "youtube": _("YouTube URLs (via yt-dlp)"),
    "yt-dlp": _("Any site supported by yt-dlp (1000+ sites)"),
}


def _find_yt_dlp():
    """Find yt-dlp binary."""
    path = shutil.which("yt-dlp")
    if path:
        return path
    # Check common user install location
    local_bin = os.path.expanduser("~/.local/bin/yt-dlp")
    if os.path.isfile(local_bin) and os.access(local_bin, os.X_OK):
        return local_bin
    return None


def _is_yt_dlp_url(url):
    """Check if a URL should be resolved via yt-dlp (YouTube, etc.)."""
    yt_patterns = [
        r'(youtube\.com|youtu\.be)',
        r'(vimeo\.com)',
        r'(twitch\.tv)',
        r'(dailymotion\.com)',
        r'(facebook\.com.*/videos/)',
        r'(twitter\.com|x\.com)',
        r'(instagram\.com)',
        r'(tiktok\.com)',
    ]
    for pat in yt_patterns:
        if re.search(pat, url, re.IGNORECASE):
            return True
    return False


def _resolve_url(url, status_callback=None):
    """Resolve a URL to a direct video URL using yt-dlp if needed.

    Returns (resolved_url, title, duration_seconds_or_None, error_or_None).
    """
    if not url.startswith(("http://", "https://")):
        return url, None, None, None

    if not _is_yt_dlp_url(url):
        # Direct URL — try to get duration via ffprobe
        return url, None, None, None

    yt_dlp = _find_yt_dlp()
    if not yt_dlp:
        return None, None, None, _("yt-dlp not found. Install with: pip3 install yt-dlp")

    if status_callback:
        GLib.idle_add(status_callback, _("Resolving URL via yt-dlp..."))

    try:
        r = subprocess.run(
            [yt_dlp, "--dump-json", "--no-playlist", url],
            capture_output=True, text=True, timeout=30
        )
        if r.returncode != 0:
            return None, None, None, _("yt-dlp error: %s") % r.stderr.strip()[:200]
        info = json.loads(r.stdout)
        video_url = info.get("url")
        title = info.get("title", "")
        duration = info.get("duration")

        if not video_url:
            # Fall back to getting the URL directly
            r2 = subprocess.run(
                [yt_dlp, "-g", "--no-playlist", "-f", "best[ext=mp4]/best", url],
                capture_output=True, text=True, timeout=30
            )
            if r2.returncode == 0:
                video_url = r2.stdout.strip().split('\n')[0]
            else:
                return None, None, None, _("Could not extract video URL")

        return video_url, title, duration, None
    except subprocess.TimeoutExpired:
        return None, None, None, _("yt-dlp timed out")
    except Exception as e:
        return None, None, None, str(e)


def _load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    return {"welcome_shown": False, "ocr_engine": "tesseract", "confidence_threshold": 60,
            "log_to_file": True, "auto_start": False, "region": "EU"}


def _save_settings(s):
    os.makedirs(SETTINGS_DIR, exist_ok=True)
    with open(SETTINGS_FILE, "w") as f:
        json.dump(s, f, indent=2)


# ── Plate detection ──────────────────────────────────────────

def _find_plates_tesseract(frame_path):
    """Use Tesseract OCR to find license plates in an image."""
    try:
        r = subprocess.run(
            ["tesseract", frame_path, "stdout", "--psm", "11", "-c",
             "tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 "],
            capture_output=True, text=True, timeout=10
        )
        if r.returncode == 0:
            text = r.stdout.strip()
            patterns = [
                r'[A-Z]{3}\s?\d{3}',
                r'[A-Z]{2,3}\s?\d{2,4}\s?[A-Z]?',
                r'\d{1,4}\s?[A-Z]{2,3}',
            ]
            plates = []
            for pat in patterns:
                for m in re.finditer(pat, text):
                    plate = m.group().strip()
                    if len(plate) >= 5:
                        plates.append({"plate": plate, "confidence": 75, "source": "tesseract"})
            return plates
    except FileNotFoundError:
        return [{"error": _("tesseract not installed — install with: sudo apt install tesseract-ocr")}]
    except Exception as e:
        return [{"error": str(e)}]
    return []


def _extract_frame(video_path, timestamp=0):
    """Extract a single frame from video using ffmpeg."""
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.close()
    try:
        subprocess.run(
            ["ffmpeg", "-ss", str(timestamp), "-i", video_path,
             "-frames:v", "1", "-y", tmp.name],
            capture_output=True, timeout=15
        )
        if os.path.exists(tmp.name) and os.path.getsize(tmp.name) > 0:
            return tmp.name
    except:
        pass
    return None


def _get_video_duration(path):
    """Get video duration in seconds. Returns None on failure."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=10
        )
        return float(r.stdout.strip())
    except:
        return None


def _capture_device_frame(device_path):
    """Capture a single frame from a video device (webcam)."""
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.close()
    try:
        if sys.platform == "darwin":
            cmd = ["ffmpeg", "-f", "avfoundation", "-framerate", "1",
                   "-i", device_path, "-frames:v", "1", "-y", tmp.name]
        else:
            cmd = ["ffmpeg", "-f", "v4l2", "-framerate", "1",
                   "-i", device_path, "-frames:v", "1", "-y", tmp.name]
        subprocess.run(cmd, capture_output=True, timeout=10)
        if os.path.exists(tmp.name) and os.path.getsize(tmp.name) > 0:
            return tmp.name
    except:
        pass
    return None


def _list_video_devices():
    """List available video capture devices."""
    devices = []
    if sys.platform == "darwin":
        try:
            r = subprocess.run(
                ["ffmpeg", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
                capture_output=True, text=True, timeout=5
            )
            in_video = False
            for line in r.stderr.splitlines():
                if "AVFoundation video devices:" in line:
                    in_video = True
                    continue
                if "AVFoundation audio devices:" in line:
                    break
                if in_video:
                    m = re.search(r'\[(\d+)\]\s*(.*)', line)
                    if m:
                        idx, name = m.group(1), m.group(2).strip()
                        devices.append({"id": idx, "name": name, "path": idx})
        except:
            pass
    else:
        import glob
        for dev in sorted(glob.glob("/dev/video*")):
            name = dev
            try:
                r = subprocess.run(
                    ["v4l2-ctl", "-d", dev, "--info"],
                    capture_output=True, text=True, timeout=3
                )
                for line in r.stdout.splitlines():
                    if "Card type" in line:
                        name = line.split(":", 1)[1].strip()
                        break
            except:
                pass
            dev_num = re.search(r'\d+', dev)
            devices.append({"id": dev_num.group() if dev_num else dev,
                            "name": name, "path": dev})
    return devices


# ── Logging ──────────────────────────────────────────────────

class PlateLog:
    def __init__(self):
        os.makedirs(LOG_DIR, exist_ok=True)
        self.entries = []
        self._load()

    def _log_path(self):
        return os.path.join(LOG_DIR, f"plates-{datetime.date.today().isoformat()}.json")

    def _load(self):
        p = self._log_path()
        if os.path.exists(p):
            with open(p) as f:
                self.entries = json.load(f)

    def add(self, plate, confidence, source="", frame_path=""):
        entry = {
            "plate": plate,
            "confidence": confidence,
            "time": datetime.datetime.now().isoformat(),
            "source": source,
            "frame": frame_path,
        }
        self.entries.append(entry)
        self._save()
        return entry

    def _save(self):
        with open(self._log_path(), "w") as f:
            json.dump(self.entries, f, indent=2)

    def clear(self):
        self.entries.clear()
        self._save()


# ── Main Window ──────────────────────────────────────────────

class ANPRWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=_("ANPR Viewer"), default_width=1000, default_height=700)
        self.settings = _load_settings()
        self.plate_log = PlateLog()
        self._processing = False
        self._video_path = None
        self._original_url = None  # Original URL before yt-dlp resolution
        self._video_duration = None
        self._device_path = None
        self._local_video_file = None  # For yt-dlp downloaded files

        # Main layout
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Header
        headerbar = Adw.HeaderBar()
        headerbar.set_title_widget(Gtk.Label(label=_("ANPR Viewer — License Plate Recognition")))

        # Open button
        open_btn = Gtk.Button(icon_name="document-open-symbolic", tooltip_text=_("Open video file"))
        open_btn.connect("clicked", self._on_open_video)
        headerbar.pack_start(open_btn)

        # Camera device button
        cam_btn = Gtk.Button(icon_name="camera-web-symbolic", tooltip_text=_("Open video device"))
        cam_btn.connect("clicked", self._on_open_device)
        headerbar.pack_start(cam_btn)

        # Stream URL button
        stream_btn = Gtk.Button(icon_name="network-server-symbolic", tooltip_text=_("Open video stream URL"))
        stream_btn.connect("clicked", self._on_open_stream)
        headerbar.pack_start(stream_btn)

        # Process button
        self._process_btn = Gtk.Button(label=_("Scan Video"))
        self._process_btn.add_css_class("suggested-action")
        self._process_btn.set_sensitive(False)
        self._process_btn.connect("clicked", self._on_process)
        headerbar.pack_end(self._process_btn)

        # Hamburger menu
        menu = Gio.Menu()
        menu.append(_("Settings"), "app.settings")
        menu.append(_("Export Log"), "app.export-log")
        menu.append(_("Clear Log"), "app.clear-log")
        menu.append(_("Copy Debug Info"), "app.copy-debug")
        menu.append(_("Keyboard Shortcuts"), "app.shortcuts")
        menu.append(_("About ANPR Viewer"), "app.about")
        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu)
        headerbar.pack_end(menu_btn)

        main_box.append(headerbar)

        # Content: paned view (video left, plates right)
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_vexpand(True)

        # Left: video preview
        self._left_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._left_box.set_size_request(500, -1)

        # Drop target for drag & drop (on the whole left box)
        drop_target = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop_target.connect("drop", self._on_drop)
        self._left_box.add_controller(drop_target)

        # Also accept text drops (for URLs)
        drop_target_text = Gtk.DropTarget.new(GLib.GType.from_name("gchararray"), Gdk.DragAction.COPY)
        drop_target_text.connect("drop", self._on_drop_text)
        self._left_box.add_controller(drop_target_text)

        self._video_status = Adw.StatusPage()
        self._video_status.set_icon_name("video-x-generic-symbolic")
        self._video_status.set_title(_("No video loaded"))
        desc_text = _(
            "Open a video file, connect a camera, or drag & drop a file or URL.\n"
            "You can also enter a stream URL (YouTube, RTSP, HTTP, etc.)."
        )
        self._video_status.set_description(desc_text)
        self._video_status.set_vexpand(True)

        # Video player widget
        self._video_widget = Gtk.Video()
        self._video_widget.set_vexpand(True)
        self._video_widget.set_visible(False)
        self._video_widget.set_autoplay(False)
        self._video_widget.set_loop(False)

        self._left_box.append(self._video_status)
        self._left_box.append(self._video_widget)

        # Progress bar
        self._progress = Gtk.ProgressBar()
        self._progress.set_visible(False)
        self._left_box.append(self._progress)

        paned.set_start_child(self._left_box)

        # Right: plate list
        right_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        right_box.set_size_request(300, -1)

        right_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        right_header.set_margin_start(12)
        right_header.set_margin_end(12)
        right_header.set_margin_top(8)
        right_label = Gtk.Label(label=_("Detected Plates"), xalign=0, hexpand=True)
        right_label.add_css_class("heading")
        right_header.append(right_label)

        self._plate_count = Gtk.Label(label="0")
        self._plate_count.add_css_class("dim-label")
        right_header.append(self._plate_count)
        right_box.append(right_header)

        # Plate list
        scroll = Gtk.ScrolledWindow(vexpand=True)
        self._plate_list = Gtk.ListBox()
        self._plate_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._plate_list.add_css_class("boxed-list")
        self._plate_list.set_margin_start(8)
        self._plate_list.set_margin_end(8)
        self._plate_list.set_margin_bottom(8)
        scroll.set_child(self._plate_list)
        right_box.append(scroll)

        paned.set_end_child(right_box)
        paned.set_position(600)

        main_box.append(paned)

        # Status bar
        self._status = Gtk.Label(label=_("Ready"), xalign=0)
        self._status.set_margin_start(12)
        self._status.set_margin_end(12)
        self._status.set_margin_top(4)
        self._status.set_margin_bottom(4)
        self._status.add_css_class("dim-label")
        main_box.append(self._status)

        self.set_content(main_box)

        # Apply plate CSS
        css = Gtk.CssProvider()
        css.load_from_string("""
            .plate-frame {
                background: #FFFFFF;
                border: 2px solid #000000;
                border-radius: 6px;
                min-width: 160px;
                min-height: 44px;
            }
            .plate-eu-band {
                background: #003DA5;
                color: #FFFFFF;
                border-radius: 4px 0 0 4px;
                font-size: 14px;
                min-width: 28px;
            }
            .plate-text {
                font-family: "FE-Schrift", "DIN 1451", "Arial Black", monospace;
                font-size: 22px;
                font-weight: 900;
                color: #000000;
                letter-spacing: 3px;
            }
        """)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        # Show welcome
        if not self.settings.get("welcome_shown"):
            GLib.idle_add(self._show_welcome)

        # Load previous log
        self._refresh_plate_list()

    def _show_welcome(self):
        dialog = Adw.Dialog()
        dialog.set_title(_("Welcome"))
        dialog.set_content_width(500)
        dialog.set_content_height(580)

        box = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        hb.set_show_title(False)
        box.add_top_bar(hb)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content_box.set_margin_start(24)
        content_box.set_margin_end(24)
        content_box.set_margin_top(16)
        content_box.set_margin_bottom(24)

        # Icon
        icon = Gtk.Image.new_from_icon_name("camera-video-symbolic")
        icon.set_pixel_size(64)
        icon.add_css_class("dim-label")
        content_box.append(icon)

        # Title
        title = Gtk.Label(label=_("Welcome to ANPR Viewer"))
        title.add_css_class("title-1")
        title.set_wrap(True)
        title.set_wrap_mode(2)  # WORD_CHAR
        title.set_justify(Gtk.Justification.CENTER)
        content_box.append(title)

        # Description
        desc = Gtk.Label(label=_("Automatic license plate recognition from video files, live streams, and cameras."))
        desc.set_wrap(True)
        desc.set_wrap_mode(2)
        desc.set_justify(Gtk.Justification.CENTER)
        desc.add_css_class("dim-label")
        content_box.append(desc)

        # Features
        features_group = Adw.PreferencesGroup(title=_("Features"))
        features = [
            ("document-open-symbolic", _("Open video files or paste URLs")),
            ("camera-web-symbolic", _("Live capture from webcams and USB cameras")),
            ("emblem-shared-symbolic", _("Drag & drop files and URLs")),
            ("view-list-symbolic", _("Detected plates listed in real-time")),
            ("document-save-symbolic", _("Log and export results as CSV/JSON")),
        ]
        for icon_name, label_text in features:
            row = Adw.ActionRow(title=label_text)
            row.add_prefix(Gtk.Image.new_from_icon_name(icon_name))
            features_group.add(row)
        content_box.append(features_group)

        # Supported sources
        sources_group = Adw.PreferencesGroup(title=_("Supported Sources"))
        for scheme, description in SUPPORTED_SCHEMES.items():
            row = Adw.ActionRow(title=scheme.upper(), subtitle=description)
            row.set_subtitle_lines(2)
            sources_group.add(row)
        content_box.append(sources_group)

        # yt-dlp status
        yt_dlp = _find_yt_dlp()
        if yt_dlp:
            yt_label = Gtk.Label(label=_("✓ yt-dlp found — YouTube and 1000+ sites supported"))
            yt_label.add_css_class("success")
        else:
            yt_label = Gtk.Label(label=_("⚠ yt-dlp not found — install for YouTube support: pip3 install yt-dlp"))
            yt_label.add_css_class("warning")
        yt_label.set_wrap(True)
        yt_label.set_wrap_mode(2)
        content_box.append(yt_label)

        btn = Gtk.Button(label=_("Get Started"))
        btn.add_css_class("suggested-action")
        btn.add_css_class("pill")
        btn.set_halign(Gtk.Align.CENTER)
        btn.connect("clicked", self._on_welcome_close, dialog)
        content_box.append(btn)

        scroll.set_child(content_box)
        box.set_content(scroll)
        dialog.set_child(box)
        dialog.present(self)

    def _on_welcome_close(self, btn, dialog):
        self.settings["welcome_shown"] = True
        _save_settings(self.settings)
        dialog.close()

    def _on_open_video(self, btn):
        dialog = Gtk.FileDialog()
        dialog.set_title(_("Open Video File"))
        ff = Gtk.FileFilter()
        ff.set_name(_("Video files"))
        for ext in ["*.mp4", "*.avi", "*.mkv", "*.mov", "*.wmv", "*.flv", "*.webm", "*.m4v"]:
            ff.add_pattern(ext)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(ff)
        dialog.set_filters(filters)
        dialog.open(self, None, self._on_file_opened)

    def _on_file_opened(self, dialog, result):
        try:
            f = dialog.open_finish(result)
            path = f.get_path()
            self._load_video(path)
            self._prompt_scan()
        except:
            pass

    def _on_open_stream(self, btn):
        dialog = Adw.AlertDialog()
        dialog.set_heading(_("Open Video Stream"))
        dialog.set_body(_(
            "Enter a URL to scan for license plates.\n\n"
            "Supported: YouTube, HTTP/HTTPS, RTSP, RTMP, "
            "and any URL supported by yt-dlp."
        ))
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("open", _("Open"))
        dialog.set_response_appearance("open", Adw.ResponseAppearance.SUGGESTED)

        entry = Gtk.Entry()
        entry.set_placeholder_text("https://youtube.com/watch?v=... or rtsp://...")
        entry.set_hexpand(True)
        dialog.set_extra_child(entry)

        def on_response(dlg, response):
            if response == "open":
                url = entry.get_text().strip()
                if url:
                    self._load_url(url)

        dialog.connect("response", on_response)
        dialog.present(self)

    def _on_open_device(self, btn):
        devices = _list_video_devices()
        if not devices:
            dialog = Adw.AlertDialog()
            dialog.set_heading(_("No Video Devices"))
            dialog.set_body(_("No video capture devices were found.\n"
                              "Connect a webcam or USB camera and try again."))
            dialog.add_response("ok", _("OK"))
            dialog.present(self)
            return

        dialog = Adw.AlertDialog()
        dialog.set_heading(_("Select Video Device"))
        dialog.set_body(_("Choose a camera to use for live plate detection."))
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("open", _("Open"))
        dialog.set_response_appearance("open", Adw.ResponseAppearance.SUGGESTED)

        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        listbox.add_css_class("boxed-list")

        for dev in devices:
            row = Adw.ActionRow(title=dev["name"], subtitle=dev["path"])
            row._device_path = dev["path"]
            listbox.append(row)

        listbox.select_row(listbox.get_row_at_index(0))
        dialog.set_extra_child(listbox)

        def on_response(dlg, response):
            if response == "open":
                selected = listbox.get_selected_row()
                if selected:
                    self._start_live_device(selected._device_path)

        dialog.connect("response", on_response)
        dialog.present(self)

    def _start_live_device(self, device_path):
        self._video_path = None
        self._device_path = device_path
        self._video_widget.set_visible(False)
        self._video_status.set_visible(True)
        self._video_status.set_icon_name("camera-web-symbolic")
        self._video_status.set_title(_("Live Camera — %s") % device_path)
        self._video_status.set_description(_("Scanning for plates in real-time..."))
        self._process_btn.set_label(_("Stop"))
        self._process_btn.set_sensitive(True)
        self._process_btn.remove_css_class("suggested-action")
        self._process_btn.add_css_class("destructive-action")
        self._processing = True
        self._progress.set_visible(True)
        self._progress.pulse()
        threading.Thread(target=self._scan_device, args=(device_path,), daemon=True).start()

    def _scan_device(self, device_path):
        """Live scan from a video device."""
        seen_plates = set()
        frame_count = 0
        while self._processing:
            frame = _capture_device_frame(device_path)
            if frame:
                frame_count += 1
                plates = _find_plates_tesseract(frame)
                for p in plates:
                    if "error" in p:
                        GLib.idle_add(self._status.set_text, p["error"])
                        GLib.idle_add(self._scan_done)
                        return
                    plate_text = p["plate"]
                    if plate_text not in seen_plates:
                        seen_plates.add(plate_text)
                        entry = self.plate_log.add(
                            plate_text, p["confidence"], p.get("source", ""),
                            frame_path=frame
                        )
                        GLib.idle_add(self._add_plate_row, entry)
                try:
                    os.unlink(frame)
                except:
                    pass
                GLib.idle_add(self._progress.pulse)
                GLib.idle_add(self._status.set_text,
                              _("Live scan — %(frames)d frames, %(count)d plates found") %
                              {"frames": frame_count, "count": len(seen_plates)})
            time.sleep(1)
        GLib.idle_add(self._scan_done)

    def _on_drop(self, drop_target, value, x, y):
        if isinstance(value, Gio.File):
            path = value.get_path()
            if path:
                self._load_video(path)
                # Auto-start scanning on drag & drop
                GLib.idle_add(self._start_scan)
                return True
        return False

    def _on_drop_text(self, drop_target, value, x, y):
        """Handle text drops (URLs)."""
        if isinstance(value, str):
            url = value.strip()
            if url.startswith(("http://", "https://", "rtsp://", "rtmp://")):
                self._load_url(url)
                return True
        return False

    def _load_url(self, url):
        """Load a URL, resolving via yt-dlp if needed."""
        self._original_url = url
        self._status.set_text(_("Loading: %s") % url)

        if _is_yt_dlp_url(url):
            # Resolve in background thread
            self._video_status.set_visible(True)
            self._video_widget.set_visible(False)
            self._video_status.set_icon_name("content-loading-symbolic")
            self._video_status.set_title(_("Resolving URL..."))
            self._video_status.set_description(url)
            threading.Thread(target=self._resolve_and_load, args=(url,), daemon=True).start()
        else:
            self._load_video(url)
            self._prompt_scan()

    def _resolve_and_load(self, url):
        """Resolve URL via yt-dlp in background, then load."""
        resolved, title, duration, error = _resolve_url(url, self._status.set_text)
        if error:
            GLib.idle_add(self._status.set_text, error)
            GLib.idle_add(self._video_status.set_title, _("Error"))
            GLib.idle_add(self._video_status.set_description, error)
            GLib.idle_add(self._video_status.set_icon_name, "dialog-error-symbolic")
            return

        self._video_duration = duration
        if title:
            GLib.idle_add(self._status.set_text, _("Resolved: %s") % title)

        GLib.idle_add(self._load_video, resolved, title)
        GLib.idle_add(self._prompt_scan)

    def _load_video(self, path, title=None):
        """Load a video path/URL into the viewer."""
        self._video_path = path
        self._process_btn.set_sensitive(True)
        display_name = title or (os.path.basename(path) if os.path.isfile(path) else path)
        self._status.set_text(_("Loaded: %s") % display_name)

        # Show video preview
        try:
            if os.path.isfile(path):
                f = Gio.File.new_for_path(path)
                media = Gtk.MediaFile.new_for_file(f)
                media.set_muted(True)
                self._video_widget.set_media_stream(media)
                self._video_widget.set_visible(True)
                self._video_status.set_visible(False)
                # Get duration
                if not self._video_duration:
                    self._video_duration = _get_video_duration(path)
            else:
                # Stream/URL — show info in status page
                self._video_widget.set_visible(False)
                self._video_status.set_visible(True)
                self._video_status.set_icon_name("emblem-ok-symbolic")
                self._video_status.set_title(title or path)
                dur_str = ""
                if self._video_duration:
                    m, s = divmod(int(self._video_duration), 60)
                    dur_str = f" ({m}:{s:02d})"
                self._video_status.set_description(
                    _("Ready to scan.") + dur_str
                )
        except Exception:
            self._video_status.set_visible(True)
            self._video_widget.set_visible(False)
            self._video_status.set_title(display_name)
            self._video_status.set_description(_("Ready to scan."))
            self._video_status.set_icon_name("emblem-ok-symbolic")

    def _prompt_scan(self):
        """Show a dialog asking user if they want to start scanning."""
        if self._processing:
            return
        dialog = Adw.AlertDialog()
        dialog.set_heading(_("Start Scanning?"))
        name = self._original_url or self._video_path or ""
        if len(name) > 60:
            name = name[:57] + "..."
        dialog.set_body(_("Video loaded: %s\n\nStart scanning for license plates now?") % name)
        dialog.add_response("later", _("Later"))
        dialog.add_response("scan", _("Start Scanning"))
        dialog.set_response_appearance("scan", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("scan")

        def on_response(dlg, response):
            if response == "scan":
                self._start_scan()

        dialog.connect("response", on_response)
        dialog.present(self)

    def _start_scan(self):
        """Programmatically start scanning."""
        if self._video_path and not self._processing:
            self._on_process(self._process_btn)

    def _on_process(self, btn):
        if not self._video_path:
            return
        if self._processing:
            self._processing = False
            self._process_btn.set_label(_("Scan Video"))
            self._status.set_text(_("Scan stopped"))
            return

        self._processing = True
        self._process_btn.set_label(_("Stop"))
        self._process_btn.remove_css_class("suggested-action")
        self._process_btn.add_css_class("destructive-action")
        self._progress.set_visible(True)
        self._status.set_text(_("Scanning..."))

        threading.Thread(target=self._scan_video, daemon=True).start()

    def _scan_video(self):
        """Scan video for plates by extracting frames."""
        path = self._video_path

        # Get duration — no artificial limits
        duration = self._video_duration or _get_video_duration(path)
        if duration is None:
            # Last resort: scan up to 10 minutes for unknown-duration streams
            duration = 600
            GLib.idle_add(self._status.set_text,
                          _("Unknown duration — scanning up to 10 minutes..."))

        interval = 2  # seconds between frames
        seen_plates = set()
        t = 0

        while t < duration and self._processing:
            frame = _extract_frame(path, timestamp=t)
            if frame:
                plates = _find_plates_tesseract(frame)
                for p in plates:
                    if "error" in p:
                        GLib.idle_add(self._status.set_text, p["error"])
                        GLib.idle_add(self._scan_done)
                        return
                    plate_text = p["plate"]
                    if plate_text not in seen_plates:
                        seen_plates.add(plate_text)
                        entry = self.plate_log.add(
                            plate_text, p["confidence"], p.get("source", ""),
                            frame_path=frame
                        )
                        GLib.idle_add(self._add_plate_row, entry)
                try:
                    os.unlink(frame)
                except:
                    pass

            progress = min(t / duration, 1.0)
            GLib.idle_add(self._progress.set_fraction, progress)

            # Sync video preview with scan progress
            GLib.idle_add(self._seek_preview, t)

            GLib.idle_add(self._status.set_text,
                          _("Scanning... %(time).0fs / %(total).0fs — %(count)d plates found") %
                          {"time": t, "total": duration, "count": len(seen_plates)})
            t += interval

        GLib.idle_add(self._scan_done)

    def _seek_preview(self, timestamp_sec):
        """Seek the video preview to match scan progress."""
        try:
            stream = self._video_widget.get_media_stream()
            if stream and hasattr(stream, 'seek'):
                # GTK MediaStream uses microseconds
                stream.seek(int(timestamp_sec * 1_000_000))
        except Exception:
            pass

    def _scan_done(self):
        self._processing = False
        self._process_btn.set_label(_("Scan Video"))
        self._process_btn.remove_css_class("destructive-action")
        self._process_btn.add_css_class("suggested-action")
        self._progress.set_visible(False)
        count = len(self.plate_log.entries)
        self._plate_count.set_text(str(count))
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self._status.set_text(_("%(time)s — Scan complete. %(count)d plates in log.") %
                              {"time": ts, "count": count})

    def _add_plate_row(self, entry):
        row = Gtk.ListBoxRow()
        row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row_box.set_margin_start(8)
        row_box.set_margin_end(8)
        row_box.set_margin_top(6)
        row_box.set_margin_bottom(6)

        # EU-style license plate widget
        plate_frame = Gtk.Frame()
        plate_inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)

        # Blue EU band (left)
        eu_band = Gtk.Label(label="🇸🇪")
        eu_band.set_size_request(28, 44)
        eu_band.set_valign(Gtk.Align.CENTER)
        eu_band.set_halign(Gtk.Align.CENTER)
        eu_band.add_css_class("plate-eu-band")
        plate_inner.append(eu_band)

        # Plate text
        plate_label = Gtk.Label(label=entry["plate"])
        plate_label.add_css_class("plate-text")
        plate_label.set_halign(Gtk.Align.CENTER)
        plate_label.set_hexpand(True)
        plate_label.set_margin_start(8)
        plate_label.set_margin_end(8)
        plate_inner.append(plate_label)

        plate_frame.set_child(plate_inner)
        plate_frame.add_css_class("plate-frame")
        row_box.append(plate_frame)

        # Info (time + confidence)
        info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        info_box.set_valign(Gtk.Align.CENTER)
        info_box.set_hexpand(True)
        time_label = Gtk.Label(label=entry["time"][11:19], xalign=0)
        time_label.add_css_class("dim-label")
        time_label.add_css_class("caption")
        info_box.append(time_label)
        conf_label = Gtk.Label(label=f'{entry["confidence"]}%', xalign=0)
        conf_label.add_css_class("caption")
        info_box.append(conf_label)
        row_box.append(info_box)

        # Copy button
        copy_btn = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER)
        copy_btn.add_css_class("flat")
        copy_btn.connect("clicked", self._on_copy_plate, entry["plate"])
        row_box.append(copy_btn)

        row.set_child(row_box)
        self._plate_list.prepend(row)
        self._plate_count.set_text(str(len(self.plate_log.entries)))

    def _on_copy_plate(self, btn, plate):
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set(plate)
        self._status.set_text(_("Copied: %s") % plate)

    def _refresh_plate_list(self):
        while True:
            row = self._plate_list.get_row_at_index(0)
            if row is None:
                break
            self._plate_list.remove(row)
        for entry in self.plate_log.entries:
            self._add_plate_row(entry)


# ── Application ──────────────────────────────────────────────

class ANPRApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.window = None

        for name, callback in [
            ("settings", self._on_settings),
            ("export-log", self._on_export_log),
            ("clear-log", self._on_clear_log),
            ("copy-debug", self._on_copy_debug),
            ("shortcuts", self._on_shortcuts),
            ("about", self._on_about),
            ("quit", self._on_quit),
        ]:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

        self.set_accels_for_action("app.quit", ["<Ctrl>q"])
        self.set_accels_for_action("app.shortcuts", ["<Ctrl>slash"])
        self.set_accels_for_action("app.export-log", ["<Ctrl>e"])

    def do_activate(self):
        if not self.window:
            self.window = ANPRWindow(self)
        self.window.present()

    def _on_settings(self, *_args):
        if not self.window:
            return
        win = self.window
        dialog = Adw.PreferencesDialog()
        dialog.set_title(_("Settings"))

        page = Adw.PreferencesPage()

        # OCR group
        ocr_group = Adw.PreferencesGroup(title=_("OCR Engine"))
        engine_row = Adw.ComboRow(title=_("Engine"))
        engine_row.set_model(Gtk.StringList.new(["Tesseract", "EasyOCR"]))
        engine_row.set_selected(0 if win.settings.get("ocr_engine") == "tesseract" else 1)
        ocr_group.add(engine_row)

        conf_row = Adw.SpinRow.new_with_range(0, 100, 5)
        conf_row.set_title(_("Minimum Confidence (%)"))
        conf_row.set_value(win.settings.get("confidence_threshold", 60))
        ocr_group.add(conf_row)
        page.add(ocr_group)

        # Region group
        region_group = Adw.PreferencesGroup(title=_("Region"))
        region_row = Adw.ComboRow(title=_("Plate Format"))
        region_row.set_model(Gtk.StringList.new(["EU", "US", "UK"]))
        region_group.add(region_row)
        page.add(region_group)

        # Logging group
        log_group = Adw.PreferencesGroup(title=_("Logging"))
        log_row = Adw.SwitchRow(title=_("Log detections to file"))
        log_row.set_active(win.settings.get("log_to_file", True))
        log_group.add(log_row)
        page.add(log_group)

        dialog.add(page)
        dialog.present(win)

    def _on_export_log(self, *_args):
        if not self.window or not self.window.plate_log.entries:
            return
        dialog = Gtk.FileDialog()
        dialog.set_title(_("Export Plate Log"))
        dialog.set_initial_name(f"plates-{datetime.date.today().isoformat()}.csv")
        dialog.save(self.window, None, self._on_export_done)

    def _on_export_done(self, dialog, result):
        try:
            f = dialog.save_finish(result)
            path = f.get_path()
            import csv
            with open(path, "w", newline="") as csvf:
                w = csv.writer(csvf)
                w.writerow(["Plate", "Confidence", "Time", "Source"])
                for e in self.window.plate_log.entries:
                    w.writerow([e["plate"], e["confidence"], e["time"], e.get("source", "")])
            self.window._status.set_text(_("Exported to %s") % path)
        except:
            pass

    def _on_clear_log(self, *_args):
        if self.window:
            self.window.plate_log.clear()
            self.window._refresh_plate_list()
            self.window._plate_count.set_text("0")
            self.window._status.set_text(_("Log cleared"))

    def _on_copy_debug(self, *_args):
        if not self.window:
            return
        from . import __version__
        yt_dlp = _find_yt_dlp()
        info = (
            f"ANPR Viewer {__version__}\n"
            f"Python {sys.version}\n"
            f"GTK {Gtk.MAJOR_VERSION}.{Gtk.MINOR_VERSION}\n"
            f"Adw {Adw.MAJOR_VERSION}.{Adw.MINOR_VERSION}\n"
            f"OS: {os.uname().sysname} {os.uname().release}\n"
            f"OCR: {self.window.settings.get('ocr_engine', 'tesseract')}\n"
            f"yt-dlp: {yt_dlp or 'not found'}\n"
        )
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set(info)
        self.window._status.set_text(_("Debug info copied"))

    def _on_shortcuts(self, *_args):
        if self.window:
            dialog = Gtk.ShortcutsWindow(transient_for=self.window)
            section = Gtk.ShortcutsSection(visible=True)
            group = Gtk.ShortcutsGroup(title=_("General"), visible=True)
            for accel, title in [
                ("<Ctrl>q", _("Quit")),
                ("<Ctrl>e", _("Export log")),
                ("<Ctrl>slash", _("Keyboard shortcuts")),
            ]:
                group.append(Gtk.ShortcutsShortcut(accelerator=accel, title=title, visible=True))
            section.append(group)
            dialog.append(section)
            dialog.present()

    def _on_about(self, *_args):
        from . import __version__
        dialog = Adw.AboutDialog(
            application_name=_("ANPR Viewer"),
            application_icon="camera-video-symbolic",
            version=__version__,
            developer_name="Daniel Nylander",
            website="https://github.com/yeager/anpr-viewer",
            license_type=Gtk.License.GPL_3_0,
            issue_url="https://github.com/yeager/anpr-viewer/issues",
            comments=_("Automatic license plate recognition from video files, streams, and cameras.\n\n"
                        "Supports YouTube, HTTP/HTTPS, RTSP, RTMP streams via yt-dlp and ffmpeg."),
        )
        dialog.present(self.window)

    def _on_quit(self, *_args):
        self.quit()


def main():
    app = ANPRApp()
    app.run(sys.argv)


if __name__ == "__main__":
    main()


# --- Session restore ---
import json as _json
import os as _os

def _save_session(window, app_name):
    config_dir = _os.path.join(_os.path.expanduser('~'), '.config', app_name)
    _os.makedirs(config_dir, exist_ok=True)
    state = {'width': window.get_width(), 'height': window.get_height(),
             'maximized': window.is_maximized()}
    try:
        with open(_os.path.join(config_dir, 'session.json'), 'w') as f:
            _json.dump(state, f)
    except OSError:
        pass

def _restore_session(window, app_name):
    path = _os.path.join(_os.path.expanduser('~'), '.config', app_name, 'session.json')
    try:
        with open(path) as f:
            state = _json.load(f)
        window.set_default_size(state.get('width', 800), state.get('height', 600))
        if state.get('maximized'):
            window.maximize()
    except (FileNotFoundError, _json.JSONDecodeError, OSError):
        pass


# --- Fullscreen toggle (F11) ---
def _setup_fullscreen(window, app):
    """Add F11 fullscreen toggle."""
    from gi.repository import Gio
    if not app.lookup_action('toggle-fullscreen'):
        action = Gio.SimpleAction.new('toggle-fullscreen', None)
        action.connect('activate', lambda a, p: (
            window.unfullscreen() if window.is_fullscreen() else window.fullscreen()
        ))
        app.add_action(action)
        app.set_accels_for_action('app.toggle-fullscreen', ['F11'])


# --- Plugin system ---
import importlib.util
import os as _pos

def _load_plugins(app_name):
    """Load plugins from ~/.config/<app>/plugins/."""
    plugin_dir = _pos.path.join(_pos.path.expanduser('~'), '.config', app_name, 'plugins')
    plugins = []
    if not _pos.path.isdir(plugin_dir):
        return plugins
    for fname in sorted(_pos.listdir(plugin_dir)):
        if fname.endswith('.py') and not fname.startswith('_'):
            path = _pos.path.join(plugin_dir, fname)
            try:
                spec = importlib.util.spec_from_file_location(fname[:-3], path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                plugins.append(mod)
            except Exception as e:
                print(f"Plugin {fname}: {e}")
    return plugins
