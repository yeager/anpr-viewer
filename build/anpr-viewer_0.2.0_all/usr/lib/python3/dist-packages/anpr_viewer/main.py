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
import tempfile

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
            # Look for plate-like patterns (EU/Swedish: ABC 123 or ABC123)
            patterns = [
                r'[A-Z]{3}\s?\d{3}',        # Swedish: ABC 123
                r'[A-Z]{2,3}\s?\d{2,4}\s?[A-Z]?',  # Generic EU
                r'\d{1,4}\s?[A-Z]{2,3}',     # Some EU reversed
            ]
            plates = []
            for pat in patterns:
                for m in re.finditer(pat, text):
                    plate = m.group().strip()
                    if len(plate) >= 5:
                        plates.append({"plate": plate, "confidence": 75, "source": "tesseract"})
            return plates
    except FileNotFoundError:
        return [{"error": _("tesseract not installed — install with: brew install tesseract")}]
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

        # Main layout
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Header
        headerbar = Adw.HeaderBar()
        headerbar.set_title_widget(Gtk.Label(label=_("ANPR Viewer — License Plate Recognition")))

        # Open button
        open_btn = Gtk.Button(icon_name="document-open-symbolic", tooltip_text=_("Open video file"))
        open_btn.connect("clicked", self._on_open_video)
        headerbar.pack_start(open_btn)

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
        left_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        left_box.set_size_request(500, -1)

        # Drop target for drag & drop
        drop_target = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop_target.connect("drop", self._on_drop)

        self._video_status = Adw.StatusPage()
        self._video_status.set_icon_name("video-x-generic-symbolic")
        self._video_status.set_title(_("No video loaded"))
        self._video_status.set_description(_("Open a video file or drag & drop one here.\nYou can also enter a stream URL."))
        self._video_status.add_controller(drop_target)
        self._video_status.set_vexpand(True)

        # Video player widget (GTK4 built-in)
        self._video_widget = Gtk.Video()
        self._video_widget.set_vexpand(True)
        self._video_widget.set_visible(False)
        self._video_widget.set_autoplay(True)
        self._video_widget.set_loop(True)

        left_box.append(self._video_status)
        left_box.append(self._video_widget)

        # Progress bar
        self._progress = Gtk.ProgressBar()
        self._progress.set_visible(False)
        left_box.append(self._progress)

        paned.set_start_child(left_box)

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
        dialog.set_content_width(420)
        dialog.set_content_height(480)

        page = Adw.StatusPage()
        page.set_icon_name("camera-video-symbolic")
        page.set_title(_("Welcome to ANPR Viewer"))
        page.set_description(_(
            "Automatic license plate recognition from video.\\n\\n"
            "✓ Open video files or live streams\\n"
            "✓ Drag & drop support\\n"
            "✓ Detected plates listed in real-time\\n"
            "✓ Log results to file\\n"
            "✓ Export as CSV or JSON"
        ))

        btn = Gtk.Button(label=_("Get Started"))
        btn.add_css_class("suggested-action")
        btn.add_css_class("pill")
        btn.set_halign(Gtk.Align.CENTER)
        btn.set_margin_top(12)
        btn.connect("clicked", self._on_welcome_close, dialog)
        page.set_child(btn)

        box = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        hb.set_show_title(False)
        box.add_top_bar(hb)
        box.set_content(page)
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
        except:
            pass

    def _on_open_stream(self, btn):
        dialog = Adw.AlertDialog()
        dialog.set_heading(_("Open Video Stream"))
        dialog.set_body(_("Enter the URL of a video stream (RTSP, HTTP, etc.)"))
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("open", _("Open"))
        dialog.set_response_appearance("open", Adw.ResponseAppearance.SUGGESTED)

        entry = Gtk.Entry()
        entry.set_placeholder_text("rtsp://192.168.1.100:554/stream")
        dialog.set_extra_child(entry)

        def on_response(dlg, response):
            if response == "open":
                url = entry.get_text().strip()
                if url:
                    self._load_video(url)

        dialog.connect("response", on_response)
        dialog.present(self)

    def _on_drop(self, drop_target, value, x, y):
        if isinstance(value, Gio.File):
            path = value.get_path()
            if path:
                self._load_video(path)
                return True
        return False

    def _load_video(self, path):
        self._video_path = path
        self._process_btn.set_sensitive(True)
        self._status.set_text(_("Loaded: %s") % path)

        # Show video in player
        try:
            if path.startswith(("rtsp://", "http://", "https://")):
                self._video_widget.set_filename(None)
                # For streams, show status instead
                self._video_status.set_title(path)
                self._video_status.set_description(_("Stream loaded. Click 'Scan Video' to detect plates."))
                self._video_status.set_icon_name("emblem-ok-symbolic")
            else:
                self._video_widget.set_filename(path)
                self._video_widget.set_visible(True)
                self._video_status.set_visible(False)
        except Exception:
            self._video_status.set_title(os.path.basename(path) if os.path.exists(path) else path)
            self._video_status.set_description(_("Video loaded. Click 'Scan Video' to detect plates."))
            self._video_status.set_icon_name("emblem-ok-symbolic")

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
        # Get duration
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", path],
                capture_output=True, text=True, timeout=10
            )
            duration = float(r.stdout.strip())
        except:
            duration = 60  # default

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
            GLib.idle_add(self._status.set_text,
                          _("Scanning... %(time).0fs / %(total).0fs — %(count)d plates found") %
                          {"time": t, "total": duration, "count": len(seen_plates)})
            t += interval

        GLib.idle_add(self._scan_done)

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
        # Clear and rebuild from log
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

        # Actions
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

    def _on_settings(self, *_):
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

    def _on_export_log(self, *_):
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

    def _on_clear_log(self, *_):
        if self.window:
            self.window.plate_log.clear()
            self.window._refresh_plate_list()
            self.window._plate_count.set_text("0")
            self.window._status.set_text(_("Log cleared"))

    def _on_copy_debug(self, *_):
        if not self.window:
            return
        from . import __version__
        info = (
            f"ANPR Viewer {__version__}\n"
            f"Python {sys.version}\n"
            f"GTK {Gtk.MAJOR_VERSION}.{Gtk.MINOR_VERSION}\n"
            f"Adw {Adw.MAJOR_VERSION}.{Adw.MINOR_VERSION}\n"
            f"OS: {os.uname().sysname} {os.uname().release}\n"
            f"OCR: {self.window.settings.get('ocr_engine', 'tesseract')}\n"
        )
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set(info)
        self.window._status.set_text(_("Debug info copied"))

    def _on_shortcuts(self, *_):
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

    def _on_about(self, *_):
        from . import __version__
        dialog = Adw.AboutDialog(
            application_name=_("ANPR Viewer"),
            application_icon="camera-video-symbolic",
            version=__version__,
            developer_name="Daniel Nylander",
            website="https://github.com/yeager/anpr-viewer",
            license_type=Gtk.License.GPL_3_0,
            issue_url="https://github.com/yeager/anpr-viewer/issues",
            comments=_("Automatic license plate recognition from video streams and files."),
        )
        dialog.present(self.window)

    def _on_quit(self, *_):
        self.quit()


def main():
    app = ANPRApp()
    app.run(sys.argv)


if __name__ == "__main__":
    main()
