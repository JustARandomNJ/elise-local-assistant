from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import threading
import time
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen
import webbrowser

from media_models import MediaMusicQuery, SpotifyDevice, SpotifySearchResult, SpotifyTrackResult
from music_console import SpotifyPlaybackState


SPOTIFY_CLIENT_ID_ENVIRONMENT_VARIABLE = "ELISE_SPOTIFY_CLIENT_ID"
SPOTIFY_SCOPES = ("user-modify-playback-state", "user-read-playback-state")
SPOTIFY_AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_ROOT = "https://api.spotify.com/v1"


def spotify_track_search_query(query: MediaMusicQuery) -> str:
    """Build the single constrained query used for list and playback searches."""

    parts = []
    if query.query:
        parts.append(f'track:"{query.query}"')
    if query.artist:
        parts.append(f'artist:"{query.artist}"')
    if query.album:
        parts.append(f'album:"{query.album}"')
    return " ".join(parts)


class SpotifyError(RuntimeError):
    def __init__(self, code: str, message: str, *, retry_after: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.retry_after = retry_after


@dataclass(frozen=True)
class SpotifyToken:
    access_token: str
    refresh_token: str
    expires_at: float
    scopes: tuple[str, ...]

    @property
    def is_expired(self) -> bool:
        return self.expires_at <= time.time() + 30


class SpotifyTokenStore:
    """Narrow ignored local credential store; never returns token values for display."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> SpotifyToken | None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SpotifyError("malformed_token", "Spotify credentials are unreadable. Log in again.") from error
        if not isinstance(raw, dict) or set(raw) != {"version", "access_token", "refresh_token", "expires_at", "scopes"}:
            raise SpotifyError("malformed_token", "Spotify credentials are malformed. Log in again.")
        access, refresh, expiry, scopes = raw.get("access_token"), raw.get("refresh_token"), raw.get("expires_at"), raw.get("scopes")
        if raw.get("version") != 1 or not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh or isinstance(expiry, bool) or not isinstance(expiry, (int, float)) or not isinstance(scopes, list) or not all(isinstance(scope, str) for scope in scopes):
            raise SpotifyError("malformed_token", "Spotify credentials are malformed. Log in again.")
        return SpotifyToken(access, refresh, float(expiry), tuple(scopes))

    def save(self, token: SpotifyToken) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = {"version": 1, "access_token": token.access_token, "refresh_token": token.refresh_token, "expires_at": token.expires_at, "scopes": list(token.scopes)}
        try:
            temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            temporary.replace(self.path)
        except OSError as error:
            raise SpotifyError("token_store", "Spotify credentials could not be stored locally.") from error

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError as error:
            raise SpotifyError("token_store", "Spotify credentials could not be removed.") from error


HttpRequester = Callable[[str, str, Mapping[str, str], bytes | None], tuple[int, Mapping[str, str], bytes]]


def default_http_request(method: str, url: str, headers: Mapping[str, str], body: bytes | None) -> tuple[int, Mapping[str, str], bytes]:
    request = Request(url, data=body, headers=dict(headers), method=method)
    try:
        with urlopen(request, timeout=15) as response:
            return int(response.status), dict(response.headers.items()), response.read(1_000_000)
    except HTTPError as error:
        return int(error.code), dict(error.headers.items()), error.read(64_000)
    except (URLError, TimeoutError, OSError) as error:
        raise SpotifyError("network", "Spotify is unavailable because the network request failed.") from error


def _json_body(body: bytes) -> Any:
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SpotifyError("malformed_response", "Spotify returned a malformed response.") from error


def _safe_http_error(status: int, headers: Mapping[str, str], body: bytes) -> SpotifyError:
    retry_after = next((value for key, value in headers.items() if key.casefold() == "retry-after"), None)
    if status == 401:
        return SpotifyError("unauthenticated", "Spotify authentication expired or was revoked. Log in again.")
    if status == 403:
        return SpotifyError("forbidden", "Spotify refused playback. Spotify Premium or different device permissions may be required.")
    if status == 429:
        suffix = f" Try again after {retry_after} seconds." if retry_after and retry_after.isdigit() else " Try again later."
        return SpotifyError("rate_limited", "Spotify rate-limited the request." + suffix, retry_after=retry_after)
    if status >= 500:
        return SpotifyError("unavailable", "Spotify is temporarily unavailable.")
    return SpotifyError("api_error", f"Spotify rejected the request (HTTP {status}).")


class SpotifyAuthManager:
    def __init__(self, token_store: SpotifyTokenStore, *, client_id: str | None = None, requester: HttpRequester = default_http_request) -> None:
        self.token_store = token_store
        self.client_id = (client_id if client_id is not None else os.environ.get(SPOTIFY_CLIENT_ID_ENVIRONMENT_VARIABLE, "")).strip()
        self.requester = requester
        self._pending: dict[str, tuple[str, str]] = {}

    @property
    def configured(self) -> bool:
        return bool(self.client_id)

    def authorization_url(self, redirect_uri: str) -> tuple[str, str]:
        if not self.configured:
            raise SpotifyError("not_configured", f"Spotify is not configured. Set {SPOTIFY_CLIENT_ID_ENVIRONMENT_VARIABLE} locally.")
        parsed = urlsplit(redirect_uri)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.path != "/callback":
            raise SpotifyError("redirect_uri", "Spotify login requires an http://127.0.0.1 loopback callback.")
        verifier = secrets.token_urlsafe(64)[:96]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        state = secrets.token_urlsafe(32)
        self._pending[state] = (verifier, redirect_uri)
        parameters = {"client_id": self.client_id, "response_type": "code", "redirect_uri": redirect_uri, "state": state, "scope": " ".join(SPOTIFY_SCOPES), "code_challenge_method": "S256", "code_challenge": challenge}
        return f"{SPOTIFY_AUTHORIZE_URL}?{urlencode(parameters)}", state

    def complete_authorization(self, parameters: Mapping[str, list[str]], expected_state: str) -> None:
        pending = self._pending.pop(expected_state, None)
        received_state = parameters.get("state", [""])[0]
        if pending is None or not secrets.compare_digest(received_state, expected_state):
            raise SpotifyError("authorization_state", "Spotify login callback validation failed.")
        if parameters.get("error"):
            raise SpotifyError("authorization_denied", "Spotify authorization was denied.")
        code = parameters.get("code", [""])[0]
        if not code:
            raise SpotifyError("authorization_code", "Spotify login callback did not include an authorization code.")
        verifier, redirect_uri = pending
        body = urlencode({"client_id": self.client_id, "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri, "code_verifier": verifier}).encode("ascii")
        status, headers, response_body = self.requester("POST", SPOTIFY_TOKEN_URL, {"Content-Type": "application/x-www-form-urlencoded"}, body)
        if status != 200:
            raise _safe_http_error(status, headers, response_body)
        self.token_store.save(self._parse_token(response_body, previous_refresh=None))

    @staticmethod
    def _parse_token(body: bytes, previous_refresh: str | None) -> SpotifyToken:
        payload = _json_body(body)
        if not isinstance(payload, dict):
            raise SpotifyError("malformed_token", "Spotify returned malformed credentials.")
        access, refresh, expires, scope = payload.get("access_token"), payload.get("refresh_token", previous_refresh), payload.get("expires_in"), payload.get("scope", " ".join(SPOTIFY_SCOPES))
        if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh or isinstance(expires, bool) or not isinstance(expires, (int, float)) or expires <= 0 or not isinstance(scope, str):
            raise SpotifyError("malformed_token", "Spotify returned malformed credentials.")
        return SpotifyToken(access, refresh, time.time() + float(expires), tuple(scope.split()))

    def refresh(self, token: SpotifyToken) -> SpotifyToken:
        if not self.configured:
            raise SpotifyError("not_configured", "Spotify is not configured.")
        body = urlencode({"client_id": self.client_id, "grant_type": "refresh_token", "refresh_token": token.refresh_token}).encode("ascii")
        status, headers, response_body = self.requester("POST", SPOTIFY_TOKEN_URL, {"Content-Type": "application/x-www-form-urlencoded"}, body)
        if status != 200:
            if status in {400, 401}:
                self.token_store.clear()
                raise SpotifyError("refresh_failed", "Spotify authentication expired or was revoked. Log in again.")
            raise _safe_http_error(status, headers, response_body)
        refreshed = self._parse_token(response_body, previous_refresh=token.refresh_token)
        self.token_store.save(refreshed)
        return refreshed

    def access_token(self) -> str:
        token = self.token_store.load()
        if token is None:
            raise SpotifyError("unauthenticated", "Spotify is not authenticated. Use `/spotify-login`.")
        if not set(SPOTIFY_SCOPES).issubset(token.scopes):
            self.token_store.clear()
            raise SpotifyError("insufficient_scope", "Spotify authorization is missing required playback permissions. Log in again.")
        if token.is_expired:
            token = self.refresh(token)
        return token.access_token

    def login(self, *, timeout: float = 180.0, browser_open: Callable[[str], object] = webbrowser.open) -> str:
        if not self.configured:
            raise SpotifyError("not_configured", f"Spotify is not configured. Set {SPOTIFY_CLIENT_ID_ENVIRONMENT_VARIABLE} locally.")
        result: dict[str, object] = {}
        manager = self

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                parsed = urlsplit(self.path)
                if parsed.path != "/callback":
                    self.send_error(404)
                    return
                result["parameters"] = parse_qs(parsed.query, keep_blank_values=True)
                message = b"Spotify authorization received. You can close this window and return to Elise."
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(message)))
                self.end_headers()
                self.wfile.write(message)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        server = HTTPServer(("127.0.0.1", 0), CallbackHandler)
        server.timeout = 0.25
        redirect_uri = f"http://127.0.0.1:{server.server_port}/callback"
        authorization_url, state = self.authorization_url(redirect_uri)
        if browser_open(authorization_url) is False:
            server.server_close()
            self._pending.pop(state, None)
            raise SpotifyError("browser", "Spotify login could not open the authorization page.")
        deadline = time.monotonic() + timeout
        try:
            while "parameters" not in result and time.monotonic() < deadline:
                server.handle_request()
        finally:
            server.server_close()
        if "parameters" not in result:
            self._pending.pop(state, None)
            raise SpotifyError("authorization_timeout", "Spotify login timed out before the callback arrived.")
        self.complete_authorization(result["parameters"], state)  # type: ignore[arg-type]
        return "Elise: Spotify authentication succeeded."


class SpotifyWebApi:
    def __init__(self, auth: SpotifyAuthManager, *, requester: HttpRequester = default_http_request) -> None:
        self.auth = auth
        self.requester = requester

    def _request(self, method: str, resource: str, *, parameters: Mapping[str, str] | None = None, payload: object = None, retry_401: bool = True) -> Any:
        url = f"{SPOTIFY_API_ROOT}{resource}"
        if parameters:
            url += "?" + urlencode(parameters)
        body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
        token = self.auth.access_token()
        status, headers, response_body = self.requester(method, url, {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, body)
        if status == 401 and retry_401:
            old = self.auth.token_store.load()
            if old is None:
                raise SpotifyError("unauthenticated", "Spotify is not authenticated. Use `/spotify-login`.")
            self.auth.refresh(old)
            return self._request(method, resource, parameters=parameters, payload=payload, retry_401=False)
        if status not in {200, 204}:
            raise _safe_http_error(status, headers, response_body)
        return _json_body(response_body)

    @staticmethod
    def _track(item: object) -> SpotifyTrackResult | None:
        if not isinstance(item, dict):
            return None
        track_id, uri, name, artists, album, duration, explicit = item.get("id"), item.get("uri"), item.get("name"), item.get("artists"), item.get("album"), item.get("duration_ms"), item.get("explicit")
        artist_names = tuple(artist.get("name", "").strip() for artist in artists if isinstance(artist, dict) and isinstance(artist.get("name"), str) and artist.get("name", "").strip()) if isinstance(artists, list) else ()
        album_name = album.get("name") if isinstance(album, dict) else None
        if not isinstance(track_id, str) or not track_id or not isinstance(uri, str) or not uri.startswith("spotify:track:") or not isinstance(name, str) or not name.strip() or not artist_names or not isinstance(album_name, str) or not album_name.strip() or isinstance(duration, bool) or not isinstance(duration, int) or duration <= 0 or not isinstance(explicit, bool):
            return None
        restrictions = item.get("restrictions")
        if item.get("is_playable") is False or item.get("is_local") is True or (isinstance(restrictions, dict) and bool(restrictions)):
            return None
        return SpotifyTrackResult(track_id, uri, name.strip(), artist_names, album_name.strip(), duration, explicit)

    def search_tracks(self, query: MediaMusicQuery) -> SpotifySearchResult:
        search = spotify_track_search_query(query)
        try:
            payload = self._request("GET", "/search", parameters={"q": search, "type": "track", "limit": "5"})
        except SpotifyError as error:
            return SpotifySearchResult(False, error_code=error.code, error_message=error.safe_message)
        tracks_payload = payload.get("tracks") if isinstance(payload, dict) else None
        items = tracks_payload.get("items") if isinstance(tracks_payload, dict) else None
        if not isinstance(items, list):
            return SpotifySearchResult(False, error_code="malformed_response", error_message="Spotify returned malformed search results.")
        if not items:
            return SpotifySearchResult(False, error_code="zero_results", error_message="Spotify returned no search results.")
        tracks = tuple(track for track in (self._track(item) for item in items) if track is not None)
        if not tracks:
            return SpotifySearchResult(False, error_code="all_results_filtered", error_message="Spotify returned search results, but none were eligible for playback.")
        return SpotifySearchResult(True, tracks=tracks)

    def devices(self) -> tuple[SpotifyDevice, ...]:
        payload = self._request("GET", "/me/player/devices")
        items = payload.get("devices") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise SpotifyError("malformed_response", "Spotify returned malformed device information.")
        devices: list[SpotifyDevice] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            device_id, name, kind = item.get("id"), item.get("name"), item.get("type")
            if isinstance(device_id, str) and device_id and isinstance(name, str) and name.strip() and isinstance(kind, str) and kind.strip() and isinstance(item.get("is_active"), bool) and isinstance(item.get("is_restricted"), bool):
                devices.append(SpotifyDevice(device_id, name.strip(), kind.strip(), item["is_active"], item["is_restricted"]))
        return tuple(devices)

    def transfer(self, device_id: str) -> None:
        self._request("PUT", "/me/player", payload={"device_ids": [device_id], "play": False})

    def play_track(self, uri: str, device_id: str | None) -> None:
        parameters = {"device_id": device_id} if device_id else None
        self._request("PUT", "/me/player/play", parameters=parameters, payload={"uris": [uri]})

    def player_action(self, action: str, device_id: str | None = None) -> None:
        parameters = {"device_id": device_id} if device_id else None
        method, resource = {"pause": ("PUT", "/me/player/pause"), "resume": ("PUT", "/me/player/play"), "next": ("POST", "/me/player/next"), "previous": ("POST", "/me/player/previous")}[action]
        self._request(method, resource, parameters=parameters)

    def current(self) -> tuple[SpotifyTrackResult | None, bool, str | None]:
        state = self.playback_state()
        if state.duration_ms <= 0:
            return None, False, state.device_name
        track = SpotifyTrackResult("current", "spotify:track:current", state.track_title, state.artists, state.album, state.duration_ms, False)
        return track, state.is_playing, state.device_name

    def playback_state(self) -> SpotifyPlaybackState:
        payload = self._request("GET", "/me/player")
        if payload is None:
            return SpotifyPlaybackState(error="No active Spotify device.", fetched_at=time.monotonic())
        if not isinstance(payload, dict) or not isinstance(payload.get("is_playing"), bool):
            raise SpotifyError("malformed_response", "Spotify returned malformed playback state.")
        device = payload.get("device")
        device_name = device.get("name") if isinstance(device, dict) and isinstance(device.get("name"), str) else None
        track = self._track(payload.get("item"))
        progress = payload.get("progress_ms", 0)
        volume = device.get("volume_percent") if isinstance(device, dict) else None
        shuffle, repeat = payload.get("shuffle_state"), payload.get("repeat_state")
        return SpotifyPlaybackState(
            track_title=track.name if track else "Nothing playing", artists=track.artists if track else (),
            album=track.album if track else "", is_playing=payload["is_playing"],
            progress_ms=max(0, progress) if isinstance(progress, int) and not isinstance(progress, bool) else 0,
            duration_ms=track.duration_ms if track else 0,
            volume_percent=max(0, min(100, volume)) if isinstance(volume, int) and not isinstance(volume, bool) else None,
            shuffle=shuffle if isinstance(shuffle, bool) else None,
            repeat=repeat if repeat in {"off", "context", "track"} else None,
            device_name=device_name, fetched_at=time.monotonic(), error=None if track else "No active Spotify playback.",
        )

    def set_volume(self, volume: int, device_id: str | None = None) -> None:
        self._request("PUT", "/me/player/volume", parameters={"volume_percent": str(max(0, min(100, volume))), **({"device_id": device_id} if device_id else {})})

    def set_shuffle(self, enabled: bool, device_id: str | None = None) -> None:
        self._request("PUT", "/me/player/shuffle", parameters={"state": str(enabled).lower(), **({"device_id": device_id} if device_id else {})})

    def set_repeat(self, mode: str, device_id: str | None = None) -> None:
        if mode not in {"off", "context", "track"}:
            raise SpotifyError("invalid_repeat", "Spotify repeat mode is invalid.")
        self._request("PUT", "/me/player/repeat", parameters={"state": mode, **({"device_id": device_id} if device_id else {})})


class SpotifyPlaybackService:
    def __init__(self, *, token_path: str | Path, internet_enabled: Callable[[], bool], audit: Callable[[str, str, bool, str, dict[str, str]], None] | None = None, client_id: str | None = None, requester: HttpRequester = default_http_request) -> None:
        self.store = SpotifyTokenStore(token_path)
        self.auth = SpotifyAuthManager(self.store, client_id=client_id, requester=requester)
        self.api = SpotifyWebApi(self.auth, requester=requester)
        self.internet_enabled = internet_enabled
        self.audit = audit
        self.pending_music_selection: tuple[SpotifyTrackResult, ...] | None = None
        self.pending_device_selection: tuple[SpotifyDevice, ...] | None = None
        self.selected_device_id: str | None = None
        self._last_state: SpotifyPlaybackState | None = None
        self._last_state_request = 0.0

    def _record(self, request: str, action: str, success: bool, summary: str, arguments: dict[str, str] | None = None) -> None:
        if self.audit:
            self.audit(request, action, success, summary, arguments or {})

    def _online(self) -> str | None:
        return None if self.internet_enabled() else "Elise: Internet access is disabled. Spotify requires internet; normal offline Elise remains available."

    @staticmethod
    def _error(error: SpotifyError) -> str:
        return f"Elise: {error.safe_message}"

    def status(self) -> str:
        if not self.auth.configured:
            return f"Elise: Spotify status: not configured. Set {SPOTIFY_CLIENT_ID_ENVIRONMENT_VARIABLE} locally."
        try:
            token = self.store.load()
        except SpotifyError as error:
            return self._error(error)
        if token is None:
            return "Elise: Spotify status: configured but not authenticated. Use `/spotify-login`."
        offline = self._online()
        if offline:
            return "Elise: Spotify status: authenticated; device availability requires internet."
        try:
            usable = tuple(device for device in self.api.devices() if not device.is_restricted)
        except SpotifyError as error:
            return self._error(error)
        return "Elise: Spotify status: authenticated." if usable else "Elise: Spotify status: authenticated but no playback device is available."

    def login(self) -> str:
        offline = self._online()
        if offline:
            return offline
        try:
            return self.auth.login()
        except SpotifyError as error:
            return self._error(error)

    def logout(self) -> str:
        try:
            self.store.clear()
        except SpotifyError as error:
            return self._error(error)
        self.pending_music_selection = None
        self.pending_device_selection = None
        self.selected_device_id = None
        return "Elise: Spotify credentials were removed from this device."

    def list_devices(self) -> str:
        offline = self._online()
        if offline:
            return offline
        try:
            devices = self.api.devices()
        except SpotifyError as error:
            return self._error(error)
        if not devices:
            return "Elise: No Spotify Connect devices are available. Open Spotify on a Premium account device and try again."
        usable = tuple(device for device in devices if not device.is_restricted)
        self.pending_device_selection = usable
        lines = ["Elise: Spotify Connect devices:"]
        lines.extend(f"{index}. {device.name} ({device.type}){' — active' if device.is_active else ''}" for index, device in enumerate(usable, 1))
        lines.extend(f"- {device.name} ({device.type}) — restricted" for device in devices if device.is_restricted)
        if self.pending_device_selection:
            lines.append("Use `/spotify-device <number>` with the number of a usable device.")
        return "\n".join(lines)

    def select_device(self, selection: str) -> str:
        if self.pending_device_selection is None:
            return "Elise: No Spotify device selection is pending. Use `/spotify-devices`."
        number = int(selection)
        if number < 1 or number > len(self.pending_device_selection):
            return f"Elise: Selection must be between 1 and {len(self.pending_device_selection)}."
        device = self.pending_device_selection[number - 1]
        if device.is_restricted:
            return "Elise: That Spotify device is restricted and cannot be controlled."
        self.selected_device_id = device.id
        self.pending_device_selection = None
        return f"Elise: Selected Spotify device {device.name!r}."

    def _device_for_playback(self) -> tuple[str | None, str | None]:
        devices = self.api.devices()
        usable = tuple(device for device in devices if not device.is_restricted)
        active = tuple(device for device in usable if device.is_active)
        if active:
            return active[0].id, None
        if self.selected_device_id:
            selected = next((device for device in usable if device.id == self.selected_device_id), None)
            if selected:
                return selected.id, None
            self.selected_device_id = None
        if len(usable) == 1:
            return usable[0].id, None
        if not usable:
            return None, "Elise: No usable Spotify device is available. Open Spotify on a Premium account device and try again."
        self.pending_device_selection = usable
        lines = ["Elise: Multiple Spotify devices are available; choose one before playback:"]
        lines.extend(f"{index}. {device.name} ({device.type})" for index, device in enumerate(usable, 1))
        lines.append("Use `/spotify-device <number>`. Then repeat the playback request.")
        return None, "\n".join(lines)

    @staticmethod
    def _format_tracks(tracks: tuple[SpotifyTrackResult, ...]) -> str:
        lines = ["Elise: Matching Spotify tracks:"]
        lines.extend(f"{index}. {track.name} — {', '.join(track.artists)} — {track.album}" for index, track in enumerate(tracks[:5], 1))
        lines.append("Use `/music-select <number>` or `/music-cancel`.")
        return "\n".join(lines)

    def search_and_play(self, request_text: str, query: str, artist: str | None = None, album: str | None = None, list_only: bool = False) -> str:
        offline = self._online()
        if offline:
            return offline
        music_query = MediaMusicQuery(query=query, artist=artist, album=album, list_only=list_only)
        search_arguments = {
            "query": music_query.query,
            "artist": music_query.artist or "",
            "album": music_query.album or "",
            "list_only": str(music_query.list_only),
            "spotify_q": spotify_track_search_query(music_query),
        }
        result = self.api.search_tracks(music_query)
        if not result.success:
            self._record(request_text, "spotify_search", False, result.error_code or "search_failed", search_arguments)
            return f"Elise: Spotify search failed: {result.error_message or 'No playable tracks matched.'}"
        self._record(request_text, "spotify_search", True, "Spotify returned an eligible playback result.", search_arguments)
        exact_title = tuple(track for track in result.tracks if not query or track.name.casefold() == query.casefold())
        exact_artists = tuple(track for track in exact_title if artist is None or any(name.casefold() == artist.casefold() for name in track.artists))
        candidates = exact_artists or result.tracks
        materially_ambiguous = len({(track.name.casefold(), tuple(name.casefold() for name in track.artists)) for track in exact_title}) > 1 and artist is None
        if list_only or materially_ambiguous:
            self.pending_music_selection = candidates[:5]
            return self._format_tracks(self.pending_music_selection)
        self.pending_music_selection = None
        return self.play_track(request_text, candidates[0])

    def play_track(self, request_text: str, track: SpotifyTrackResult) -> str:
        try:
            device_id, error = self._device_for_playback()
            if error:
                return error
            self.api.play_track(track.uri, device_id)
        except SpotifyError as spotify_error:
            self._record(request_text, "spotify_play", False, spotify_error.code)
            return self._error(spotify_error)
        self._record(request_text, "spotify_play", True, "Spotify accepted playback control.", {"track_id": track.id})
        return f"Elise: Spotify started {track.name!r} by {', '.join(track.artists)}."

    def select_music(self, request_text: str, selection: str) -> str:
        if self.pending_music_selection is None:
            return "Elise: No music selection is pending."
        number = int(selection)
        if number < 1 or number > len(self.pending_music_selection):
            return f"Elise: Selection must be between 1 and {len(self.pending_music_selection)}."
        track = self.pending_music_selection[number - 1]
        self.pending_music_selection = None
        return self.play_track(request_text, track)

    def cancel_music(self) -> str:
        if self.pending_music_selection is None:
            return "Elise: No music selection is pending."
        self.pending_music_selection = None
        return "Elise: Music selection cancelled."

    def control(self, request_text: str, action: str) -> str:
        offline = self._online()
        if offline:
            return offline
        try:
            if action == "current":
                track, playing, device = self.api.current()
                if track is None:
                    return "Elise: Spotify is not currently playing a track."
                state = "playing" if playing else "paused"
                location = f" on {device}" if device else ""
                return f"Elise: Spotify is {state} {track.name!r} by {', '.join(track.artists)}{location}."
            if action in {"volume_up", "volume_down"}:
                state = self.playback_state(force=True)
                current = state.volume_percent if state.volume_percent is not None else 50
                self.set_volume(current + (5 if action == "volume_up" else -5))
            else:
                self.api.player_action(action, self.selected_device_id)
        except SpotifyError as error:
            self._record(request_text, f"spotify_{action}", False, error.code)
            return self._error(error)
        self._record(request_text, f"spotify_{action}", True, "Spotify accepted playback control.")
        verb = {"pause": "paused", "resume": "resumed", "next": "skipped to the next track", "previous": "skipped to the previous track", "volume_up": "volume increased", "volume_down": "volume decreased"}[action]
        return f"Elise: Spotify {verb}."

    def playback_state(self, *, force: bool = False) -> SpotifyPlaybackState:
        now = time.monotonic()
        if not force and self._last_state is not None and now - self._last_state_request < 1.0:
            return self._last_state.estimated(now)
        self._last_state_request = now
        try:
            self._last_state = self.api.playback_state()
        except SpotifyError as error:
            self._last_state = SpotifyPlaybackState(error=error.safe_message, fetched_at=now)
        return self._last_state

    def playback_action(self, action: str) -> None:
        self.api.player_action(action, self.selected_device_id)
        self._last_state_request = 0.0

    def set_volume(self, volume: int) -> None:
        self.api.set_volume(max(0, min(100, int(volume))), self.selected_device_id)
        self._last_state_request = 0.0

    def set_shuffle(self, enabled: bool) -> None:
        self.api.set_shuffle(enabled, self.selected_device_id)
        self._last_state_request = 0.0

    def set_repeat(self, mode: str) -> None:
        self.api.set_repeat(mode, self.selected_device_id)
        self._last_state_request = 0.0

    def handle_command(self, request_text: str) -> str | None:
        command, _, remainder = request_text.partition(" ")
        normalized, argument = command.casefold(), remainder.strip()
        if normalized == "/spotify-login":
            return self.login() if not argument else "Elise: Usage: /spotify-login"
        if normalized == "/spotify-status":
            return self.status() if not argument else "Elise: Usage: /spotify-status"
        if normalized == "/spotify-logout":
            return self.logout() if not argument else "Elise: Usage: /spotify-logout"
        if normalized == "/spotify-devices":
            return self.list_devices() if not argument else "Elise: Usage: /spotify-devices"
        if normalized == "/spotify-device":
            return self.select_device(argument) if argument.isdigit() else "Elise: Usage: /spotify-device <number>"
        if normalized == "/music-select":
            return self.select_music(request_text, argument) if argument.isdigit() else "Elise: Usage: /music-select <number>"
        if normalized == "/music-cancel":
            return self.cancel_music() if not argument else "Elise: Usage: /music-cancel"
        return None
