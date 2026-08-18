from __future__ import annotations

from pathlib import Path
import argparse
import time

from music_console import PlaybackBackend, SpotifyPlaybackState
from spotify_media import SpotifyPlaybackService


def _clock(milliseconds: int) -> str:
    seconds = max(0, milliseconds // 1000)
    return f"{seconds // 60}:{seconds % 60:02d}"


def build_app(backend: PlaybackBackend, close_marker: Path | None = None):
    from textual.app import App, ComposeResult
    from textual.containers import Vertical
    from textual.widgets import Footer, Header, Label, ProgressBar, Static

    class TerminalMusicDisplay(App[None]):
        TITLE = "Elise Music / Spotify"
        CSS = "Vertical { padding: 1 3; } #title { text-style: bold; color: #1ed760; } ProgressBar { margin-top: 1; }"
        BINDINGS = [("space", "play_pause", "Pause/resume"), ("right,n", "next", "Next"), ("left,p", "previous", "Previous"), ("up", "volume_up", "Volume +"), ("down", "volume_down", "Volume -"), ("m", "mute", "Mute"), ("r", "repeat", "Repeat"), ("s", "shuffle", "Shuffle"), ("q", "quit", "Close")]

        def __init__(self) -> None:
            super().__init__()
            self.state = SpotifyPlaybackState(fetched_at=time.monotonic())
            self.last_volume = 50

        def compose(self) -> ComposeResult:
            yield Header()
            with Vertical():
                yield Static("♫ Elise Music / Spotify", id="title")
                yield Label("Nothing playing", id="track")
                yield Label("", id="artist")
                yield Label("", id="album")
                yield Label("No active playback", id="state")
                yield ProgressBar(total=100, show_eta=False, id="progress")
                yield Label("0:00 / 0:00", id="time")
                yield Label("Volume: —  Shuffle: —  Repeat: —", id="details")
                yield Static("▁▃▅▇▅▃▁", id="visualizer")
            yield Footer()

        def on_mount(self) -> None:
            self.set_interval(0.25, self._tick)
            self._refresh()

        def _refresh(self) -> None:
            try:
                self.state = backend.playback_state()
            except Exception:
                self.state = SpotifyPlaybackState(error="Spotify playback state is temporarily unavailable.", fetched_at=time.monotonic())

        def _tick(self) -> None:
            if close_marker is not None and close_marker.exists():
                self.exit()
                return
            if time.monotonic() - self.state.fetched_at >= 1.0:
                self._refresh()
            state = self.state.estimated()
            self.query_one("#track", Label).update(state.track_title)
            self.query_one("#artist", Label).update(", ".join(state.artists))
            self.query_one("#album", Label).update(state.album)
            label = state.error or ("Playing" if state.is_playing else "Paused")
            if state.device_name:
                label += f" on {state.device_name}"
            self.query_one("#state", Label).update(label)
            self.query_one("#progress", ProgressBar).update(progress=state.progress_fraction * 100)
            self.query_one("#time", Label).update(f"{_clock(state.progress_ms)} / {_clock(state.duration_ms)}")
            volume = "—" if state.volume_percent is None else f"{state.volume_percent}%"
            self.query_one("#details", Label).update(f"Volume: {volume}  Shuffle: {state.shuffle if state.shuffle is not None else '—'}  Repeat: {state.repeat or '—'}")

        def _action(self, action: str) -> None:
            try:
                backend.playback_action(action)
                self._refresh()
            except Exception:
                pass

        def action_play_pause(self) -> None: self._action("pause" if self.state.is_playing else "resume")
        def action_next(self) -> None: self._action("next")
        def action_previous(self) -> None: self._action("previous")
        def _volume(self, delta: int) -> None:
            current = self.state.volume_percent if self.state.volume_percent is not None else 50
            try: backend.set_volume(max(0, min(100, current + delta))); self._refresh()
            except Exception: pass
        def action_volume_up(self) -> None: self._volume(5)
        def action_volume_down(self) -> None: self._volume(-5)
        def action_mute(self) -> None:
            current = self.state.volume_percent or 0
            if current: self.last_volume = current
            try: backend.set_volume(0 if current else self.last_volume); self._refresh()
            except Exception: pass
        def action_shuffle(self) -> None:
            if self.state.shuffle is not None:
                try: backend.set_shuffle(not self.state.shuffle); self._refresh()
                except Exception: pass
        def action_repeat(self) -> None:
            modes = ("off", "context", "track")
            try: backend.set_repeat(modes[(modes.index(self.state.repeat or "off") + 1) % 3]); self._refresh()
            except Exception: pass

    return TerminalMusicDisplay()


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--session", default="")
    options = parser.parse_args()
    root = Path(__file__).resolve().parent
    session = options.session if len(options.session) == 16 and all(char in "0123456789abcdef" for char in options.session) else ""
    directory = root / "data" / "music_console_sessions"
    active = directory / f"{session}.active" if session else None
    close = directory / f"{session}.close" if session else None
    if active is not None:
        directory.mkdir(parents=True, exist_ok=True)
        active.touch(exist_ok=True)
    service = SpotifyPlaybackService(token_path=root / "data" / "spotify_tokens.json", internet_enabled=lambda: True)
    try:
        build_app(service, close).run()
    finally:
        for marker in (active, close):
            if marker is not None:
                try:
                    marker.unlink()
                except FileNotFoundError:
                    pass


if __name__ == "__main__":
    main()
