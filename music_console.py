from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path
import shutil
import secrets
import subprocess
import sys
import time
from typing import Callable, Protocol


@dataclass(frozen=True)
class SpotifyPlaybackState:
    track_title: str = "Nothing playing"
    artists: tuple[str, ...] = ()
    album: str = ""
    is_playing: bool = False
    progress_ms: int = 0
    duration_ms: int = 0
    volume_percent: int | None = None
    shuffle: bool | None = None
    repeat: str | None = None
    device_name: str | None = None
    fetched_at: float = 0.0
    error: str | None = None

    @property
    def progress_fraction(self) -> float:
        if self.duration_ms <= 0:
            return 0.0
        return max(0.0, min(1.0, self.progress_ms / self.duration_ms))

    def estimated(self, now: float | None = None) -> "SpotifyPlaybackState":
        if not self.is_playing or self.duration_ms <= 0:
            return self
        elapsed = max(0.0, (time.monotonic() if now is None else now) - self.fetched_at)
        return replace(self, progress_ms=min(self.duration_ms, self.progress_ms + int(elapsed * 1000)))


class PlaybackBackend(Protocol):
    def playback_state(self, *, force: bool = False) -> SpotifyPlaybackState: ...
    def playback_action(self, action: str) -> None: ...
    def set_volume(self, volume: int) -> None: ...
    def set_shuffle(self, enabled: bool) -> None: ...
    def set_repeat(self, mode: str) -> None: ...


class MusicDisplayController:
    """Owns only the console process; Spotify audio remains on Spotify Connect."""

    def __init__(self, *, project_dir: str | Path, popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen) -> None:
        self.project_dir = Path(project_dir).resolve()
        self._popen = popen
        self._process: subprocess.Popen[bytes] | None = None
        self._session_id: str | None = None

    def _marker(self, suffix: str) -> Path | None:
        if self._session_id is None:
            return None
        return self.project_dir / "data" / "music_console_sessions" / f"{self._session_id}.{suffix}"

    @property
    def is_open(self) -> bool:
        active = self._marker("active")
        return bool(self._session_id and ((self._process is not None and self._process.poll() is None) or (active is not None and active.exists())))

    def open(self) -> bool:
        if self.is_open:
            return True
        script = str(self.project_dir / "terminal_music_display.py")
        self._session_id = secrets.token_hex(8)
        python = sys.executable
        wt = shutil.which("wt.exe") if os.name == "nt" else None
        child = [python, script, "--session", self._session_id]
        args = [wt, "new-tab", "--title", "Elise Music", *child] if wt else child
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        try:
            self._process = self._popen(args, cwd=str(self.project_dir), shell=False, creationflags=creationflags)
        except OSError:
            self._process = None
            self._session_id = None
            return False
        return True

    def close(self) -> bool:
        if self._session_id is None:
            self._process = None
            return False
        marker = self._marker("close")
        assert marker is not None
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch(exist_ok=True)
        active = self._marker("active")
        if self._process is not None and self._process.poll() is None and (active is None or not active.exists()):
            self._process.terminate()
        self._process = None
        self._session_id = None
        return True
