from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from subprocess import Popen
from threading import Lock, Thread
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit
import os
import secrets
import shutil

from media_models import DisplayActionResult, DisplayReportStatus, DisplaySnapshot, YouTubeMediaPayload


_ALLOWED_REPORTS: set[str] = {"player_ready", "playing", "playing_muted", "paused", "ended", "autoplay_blocked", "player_error"}


class LocalDisplayController:
    """One token-protected loopback display server and reusable Windows app window."""

    def __init__(
        self,
        *,
        assets_directory: str | Path,
        fullscreen: bool = False,
        autoplay: bool = True,
        autoplay_with_sound: bool = True,
        volume: int = 100,
        edge_autoplay_override: bool = True,
        profile_directory: str | Path | None = None,
        browser_launcher: Callable[[list[str]], Any] | None = None,
    ) -> None:
        self._assets_directory = Path(assets_directory)
        self._fullscreen = fullscreen
        self._browser_launcher = browser_launcher or (lambda command: Popen(command, shell=False))
        self._autoplay = autoplay
        self._autoplay_with_sound = autoplay_with_sound
        self._volume = max(0, min(100, int(volume)))
        self._edge_autoplay_override = edge_autoplay_override
        self._profile_directory = Path(profile_directory) if profile_directory else self._assets_directory.parent / "data" / "edge_display_profile"
        self._lock = Lock()
        self._token = secrets.token_urlsafe(32)
        self._server: ThreadingHTTPServer | None = None
        self._server_thread: Thread | None = None
        self._process: Any | None = None
        self._revision = 0
        self._desired_action: str = "idle"
        self._reported_status: DisplayReportStatus | None = None
        self._media: YouTubeMediaPayload | None = None
        self._message: str | None = None

    def _window_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def snapshot(self) -> DisplaySnapshot:
        with self._lock:
            return DisplaySnapshot(
                server_running=self._server is not None,
                window_running=self._window_running(),
                desired_action=self._desired_action,  # type: ignore[arg-type]
                reported_status=self._reported_status,
                media=self._media,
                message=self._message,
            )

    def _set_action(self, action: str, message: str | None = None) -> None:
        with self._lock:
            self._desired_action = action
            self._message = message
            self._revision += 1

    def _make_handler(self):
        controller = self

        class DisplayHandler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def _authorized(self) -> bool:
                token = parse_qs(urlsplit(self.path).query).get("token", [""])[0]
                return secrets.compare_digest(token, controller._token)

            def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlsplit(self.path)
                if parsed.path == "/":
                    if not self._authorized():
                        self.send_error(HTTPStatus.FORBIDDEN)
                        return
                    body = (controller._assets_directory / "index.html").read_bytes()
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if parsed.path == "/display.js" or parsed.path == "/display.css":
                    filename = parsed.path.lstrip("/")
                    body = (controller._assets_directory / filename).read_bytes()
                    content_type = "application/javascript; charset=utf-8" if filename.endswith(".js") else "text/css; charset=utf-8"
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if parsed.path == "/api/state":
                    if not self._authorized():
                        self.send_error(HTTPStatus.FORBIDDEN)
                        return
                    with controller._lock:
                        media = controller._media.to_display_dict() if controller._media else None
                        self._send_json({"revision": controller._revision, "action": controller._desired_action, "media": media, "playback": {"autoplay": controller._autoplay, "autoplay_with_sound": controller._autoplay_with_sound, "volume": controller._volume}})
                    return
                self.send_error(HTTPStatus.NOT_FOUND)

            def do_POST(self) -> None:  # noqa: N802
                if urlsplit(self.path).path != "/api/report" or not self._authorized():
                    self.send_error(HTTPStatus.FORBIDDEN)
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if size < 1 or size > 1_024:
                        raise ValueError
                    payload = json.loads(self.rfile.read(size).decode("utf-8"))
                    status = payload["status"]
                    video_id = payload.get("video_id")
                except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeDecodeError):
                    self.send_error(HTTPStatus.BAD_REQUEST)
                    return
                if status not in _ALLOWED_REPORTS:
                    self.send_error(HTTPStatus.BAD_REQUEST)
                    return
                with controller._lock:
                    if controller._media is None or video_id != controller._media.video_id:
                        self.send_error(HTTPStatus.CONFLICT)
                        return
                    controller._reported_status = status
                    controller._message = None if status not in {"autoplay_blocked", "player_error"} else status.replace("_", " ")
                self._send_json({"accepted": True})

        return DisplayHandler

    def _start_server(self) -> None:
        if self._server is not None:
            return
        if not self._assets_directory.is_dir():
            raise RuntimeError("Display assets are unavailable.")
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._make_handler())
        self._server_thread = Thread(target=self._server.serve_forever, daemon=True)
        self._server_thread.start()

    def _browser_command(self) -> list[str]:
        browser = shutil.which("msedge.exe") or shutil.which("msedge")
        if browser is None:
            for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
                base = os.environ.get(variable)
                if not base:
                    continue
                candidate = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
                if candidate.is_file():
                    browser = str(candidate)
                    break
        if browser is None:
            raise RuntimeError("Microsoft Edge was not found for the local display window.")
        assert self._server is not None
        port = int(self._server.server_address[1])
        url = f"http://127.0.0.1:{port}/?token={self._token}"
        self._profile_directory.mkdir(parents=True, exist_ok=True)
        command = [browser, f"--app={url}", "--no-first-run", "--new-window", f"--user-data-dir={self._profile_directory.resolve()}"]
        if self._edge_autoplay_override:
            command.append("--autoplay-policy=no-user-gesture-required")
        if self._fullscreen:
            command.append("--start-fullscreen")
        return command

    def _ensure_window(self) -> None:
        if self._window_running():
            return
        self._process = self._browser_launcher(self._browser_command())

    def load_and_play(self, media: YouTubeMediaPayload) -> DisplayActionResult:
        try:
            self._start_server()
            with self._lock:
                self._media = media
                self._reported_status = None
            self._set_action("load", "Waiting for the display player.")
            self._ensure_window()
        except (OSError, RuntimeError):
            self.close()
            self._set_action("idle", "Display window could not be opened.")
            return DisplayActionResult(False, "Display window could not be opened.", self.snapshot())
        return DisplayActionResult(True, "Display requested; waiting for player status.", self.snapshot())

    def pause(self) -> DisplayActionResult:
        if not self.snapshot().window_running:
            return DisplayActionResult(False, "No display window is open.", self.snapshot())
        self._set_action("pause")
        return DisplayActionResult(True, "Pause requested; waiting for display status.", self.snapshot())

    def resume(self) -> DisplayActionResult:
        if not self.snapshot().window_running:
            return DisplayActionResult(False, "No display window is open.", self.snapshot())
        self._set_action("resume")
        return DisplayActionResult(True, "Resume requested; waiting for display status.", self.snapshot())

    def close(self) -> DisplayActionResult:
        self._set_action("close")
        if self._process is not None and self._window_running():
            self._process.terminate()
        self._process = None
        self._reported_status = None
        server = self._server
        thread = self._server_thread
        self._server = None
        self._server_thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=2)
        return DisplayActionResult(True, "Display window closed.", self.snapshot())

    def shutdown(self) -> None:
        self.close()
