from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock

from command_router import parse_natural_command
from media_commands import MediaCommandService
from music_console import MusicDisplayController, SpotifyPlaybackState
from spotify_media import SpotifyAuthManager, SpotifyError, SpotifyPlaybackService, SpotifyToken, SpotifyTokenStore, SpotifyWebApi
from terminal_music_display import build_app


def playback_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "is_playing": True, "progress_ms": 30_000, "shuffle_state": True, "repeat_state": "context",
        "device": {"name": "Laptop", "volume_percent": 55},
        "item": {"id": "track-id", "uri": "spotify:track:track-id", "name": "Pink + White", "artists": [{"name": "Frank Ocean"}], "album": {"name": "Blonde"}, "duration_ms": 184_000, "explicit": False},
    }
    payload.update(overrides)
    return payload


class MusicConsoleTests(unittest.TestCase):
    def test_state_mapping_and_bounded_progress(self) -> None:
        body = json.dumps(playback_payload()).encode()
        store = Mock(load=Mock(return_value=SpotifyToken("access", "refresh", time.time() + 3600, ("user-modify-playback-state", "user-read-playback-state", "user-read-currently-playing"))))
        api = SpotifyWebApi(SpotifyAuthManager(store, client_id="client", requester=lambda *_: (200, {}, body)), requester=lambda *_: (200, {}, body))
        state = api.playback_state()
        self.assertEqual(("Pink + White", ("Frank Ocean",), "Blonde", 184_000), (state.track_title, state.artists, state.album, state.duration_ms))
        self.assertEqual(1.0, SpotifyPlaybackState(progress_ms=500, duration_ms=100).progress_fraction)
        self.assertEqual(0.0, SpotifyPlaybackState(progress_ms=-5, duration_ms=100).progress_fraction)

    def test_launcher_uses_argument_list_shell_false_and_no_token(self) -> None:
        process = Mock(poll=Mock(return_value=None))
        popen = Mock(return_value=process)
        controller = MusicDisplayController(project_dir=Path.cwd(), popen=popen)
        self.assertTrue(controller.open())
        args, kwargs = popen.call_args
        self.assertIsInstance(args[0], list)
        self.assertIs(kwargs["shell"], False)
        self.assertNotIn("access-token", " ".join(args[0]))
        controller.close()

    def test_q_close_does_not_touch_playback(self) -> None:
        process = Mock(poll=Mock(return_value=None))
        controller = MusicDisplayController(project_dir=Path.cwd(), popen=Mock(return_value=process))
        controller.open(); controller.close()
        process.terminate.assert_called_once_with()

    def test_no_device_and_sanitized_expired_token(self) -> None:
        requester = lambda *_: (204, {}, b"")
        store = Mock(load=Mock(return_value=SpotifyToken("secret-access", "secret-refresh", time.time() + 3600, ("user-modify-playback-state", "user-read-playback-state", "user-read-currently-playing"))))
        state = SpotifyWebApi(SpotifyAuthManager(store, client_id="client", requester=requester), requester=requester).playback_state()
        self.assertIn("No active", state.error or "")
        error = SpotifyError("unauthenticated", "Spotify authentication expired or was revoked. Log in again.")
        self.assertNotIn("secret", error.safe_message)

    def test_refresh_rate_limit_volume_and_actions(self) -> None:
        service = SpotifyPlaybackService(token_path="unused", internet_enabled=lambda: True)
        service.api = Mock()
        service.api.playback_state.return_value = SpotifyPlaybackState(volume_percent=98, fetched_at=time.monotonic())
        service.playback_state(); service.playback_state()
        service.api.playback_state.assert_called_once_with()
        service.set_volume(105); service.set_volume(-5)
        self.assertEqual([unittest.mock.call(100, None), unittest.mock.call(0, None)], service.api.set_volume.call_args_list)
        service.playback_action("pause"); service.playback_action("resume"); service.playback_action("next"); service.playback_action("previous")
        self.assertEqual(["pause", "resume", "next", "previous"], [call.args[0] for call in service.api.player_action.call_args_list])

    def test_natural_language_routes_and_negation(self) -> None:
        cases = {"pause the music": "pause", "resume my music": "resume", "skip this song": "next", "go back": "previous", "turn it down": "volume_down", "what song is playing?": "current"}
        for text, action in cases.items():
            parsed = parse_natural_command(text)
            self.assertEqual(action, parsed.arguments["action"] if parsed and parsed.intent == "media.spotify_control" else None)
        self.assertIsNone(parse_natural_command("don't pause the music"))
        self.assertEqual("media.play_music", parse_natural_command("put on Pink + White").intent)

    def test_auto_open_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            base = {"version": 1, "youtube": {"creator_aliases": {}}, "display": {}, "spotify": {"open_music_console_on_play": True}}
            path.write_text(json.dumps(base), encoding="utf-8")
            service = MediaCommandService.__new__(MediaCommandService)
            service._config_path = path
            service.spotify = Mock(search_and_play=Mock(return_value="Elise: Spotify started 'Pink + White' by Frank Ocean."))
            service._music_display = Mock()
            service.play_music("request", "Pink + White")
            service._music_display.open.assert_called_once_with()
            base["spotify"]["open_music_console_on_play"] = False
            path.write_text(json.dumps(base), encoding="utf-8")
            service._music_display.reset_mock(); service.play_music("request", "Pink + White")
            service._music_display.open.assert_not_called()

    def test_api_failure_becomes_display_safe_state(self) -> None:
        service = SpotifyPlaybackService(token_path="unused", internet_enabled=lambda: True)
        service.api = Mock(playback_state=Mock(side_effect=SpotifyError("network", "Spotify is temporarily unavailable.")))
        self.assertEqual("Spotify is temporarily unavailable.", service.playback_state().error)


class TextualKeyboardTests(unittest.IsolatedAsyncioTestCase):
    async def test_keyboard_controls_and_q_only_close_ui(self) -> None:
        backend = Mock()
        backend.playback_state.return_value = SpotifyPlaybackState(is_playing=True, volume_percent=50, duration_ms=100_000, fetched_at=time.monotonic())
        app = build_app(backend)
        async with app.run_test() as pilot:
            await pilot.press("space", "right", "left", "up", "down", "q")
        self.assertEqual(["pause", "next", "previous"], [call.args[0] for call in backend.playback_action.call_args_list])
        self.assertEqual([55, 45], [call.args[0] for call in backend.set_volume.call_args_list])


if __name__ == "__main__":
    unittest.main()
