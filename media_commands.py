from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass
import unicodedata

from audit import ToolAuditLog
from internet import InternetManager
from media_config import MediaConfigurationError, MediaDisplayConfig, normalize_creator_alias, read_youtube_api_key
from media_display import LocalDisplayController
from media_models import CreatorCandidate, CreatorTarget, MediaVideoQuery, VideoQueryOrdering, YouTubeMediaPayload
from media_providers import YouTubeDataProvider
from spotify_media import SpotifyPlaybackService
from music_console import MusicDisplayController


@dataclass(frozen=True)
class ParsedMediaCommand:
    action: str
    creator_alias: str | None = None
    error: str | None = None


def normalize_creator_comparison(value: str, *, handle: bool = False) -> str:
    """Normalize only presentation differences for exact creator matching."""

    normalized = " ".join(unicodedata.normalize("NFC", value).casefold().strip().split())
    normalized = normalized.strip()
    while normalized and unicodedata.category(normalized[0]).startswith("P") and normalized[0] != "@":
        normalized = normalized[1:].lstrip()
    while normalized and unicodedata.category(normalized[-1]).startswith("P"):
        normalized = normalized[:-1].rstrip()
    if handle and normalized.startswith("@"):
        normalized = normalized[1:].lstrip()
    return normalized


def unique_exact_creator_match(query: str, candidates: tuple[CreatorCandidate, ...]) -> CreatorCandidate | None:
    """Return the sole best exact match; never infer or fuzzy-correct."""

    normalized_query = normalize_creator_comparison(query, handle=True)
    if not normalized_query:
        return None
    exact_handles = tuple(
        candidate
        for candidate in candidates
        if candidate.handle is not None
        and normalize_creator_comparison(candidate.handle, handle=True) == normalized_query
    )
    if len(exact_handles) == 1:
        return exact_handles[0]
    if exact_handles:
        return None
    exact_titles = tuple(
        candidate
        for candidate in candidates
        if normalize_creator_comparison(candidate.title) == normalized_query
    )
    return exact_titles[0] if len(exact_titles) == 1 else None


def parse_media_command(user_input: str) -> ParsedMediaCommand | None:
    """Parse only the explicit Media Display command surface."""

    command, _, remainder = user_input.partition(" ")
    normalized = command.casefold()
    if normalized == "/play-latest":
        creator = remainder.strip()
        return ParsedMediaCommand(
            action="play_latest",
            creator_alias=creator or None,
            error=None if creator else "Elise: Usage: /play-latest <creator>",
        )
    if normalized == "/creator-select":
        selection = remainder.strip()
        return ParsedMediaCommand(action="creator_select", creator_alias=selection or None, error=None if selection.isdigit() else "Elise: Usage: /creator-select <number>")
    if normalized == "/creator-cancel":
        return ParsedMediaCommand(action="creator_cancel", error=None if not remainder.strip() else "Elise: Usage: /creator-cancel")
    if normalized == "/video-select":
        selection = remainder.strip()
        return ParsedMediaCommand(action="video_select", creator_alias=selection or None, error=None if selection.isdigit() else "Elise: Usage: /video-select <number>")
    if normalized == "/video-cancel":
        return ParsedMediaCommand(action="video_cancel", error=None if not remainder.strip() else "Elise: Usage: /video-cancel")
    if normalized == "/creator":
        argument = remainder.strip()
        if argument.casefold() == "aliases":
            return ParsedMediaCommand(action="creator_aliases")
        subcommand, _, alias = argument.partition(" ")
        if subcommand.casefold() == "forget" and alias.strip():
            return ParsedMediaCommand(action="creator_forget", creator_alias=alias.strip())
        return ParsedMediaCommand(action="invalid", error="Elise: Usage: /creator aliases | /creator forget <alias>")
    if normalized != "/display":
        return None
    action, separator, trailing = remainder.strip().partition(" ")
    if action.casefold() in {"pause", "resume", "close", "status"} and not trailing.strip():
        return ParsedMediaCommand(action=action.casefold())
    return ParsedMediaCommand(
        action="invalid",
        error="Elise: Usage: /display pause|resume|close|status",
    )


class MediaCommandService:
    """Explicit media command orchestration; it never invokes the language model."""

    def __init__(self, *, config_path: str | Path, assets_directory: str | Path, internet_manager: InternetManager, audit_log: ToolAuditLog) -> None:
        self._config_path = Path(config_path)
        self._assets_directory = Path(assets_directory)
        self._internet_manager = internet_manager
        self._audit_log = audit_log
        self._display: LocalDisplayController | None = None
        self._pending_selection: tuple[str, tuple[CreatorCandidate, ...]] | None = None
        self._pending_creator_query: MediaVideoQuery | None = None
        self._pending_video_selection: tuple[YouTubeMediaPayload, ...] | None = None
        self.spotify = SpotifyPlaybackService(
            token_path=self._config_path.parent / "spotify_tokens.json",
            internet_enabled=lambda: bool(self._internet_manager.is_enabled),
            audit=self._record_spotify,
        )
        self._music_display = MusicDisplayController(project_dir=self._assets_directory.parent)

    def pending_natural_selections(self) -> dict[str, tuple[str, ...]]:
        """Expose presentation labels only, keeping selection namespaces typed."""

        creators = self._pending_selection[1] if self._pending_selection else ()
        videos = self._pending_video_selection or ()
        music = self.spotify.pending_music_selection or ()
        devices = self.spotify.pending_device_selection or ()
        return {
            "creator": tuple(candidate.title for candidate in creators),
            "video": tuple(video.title for video in videos),
            "music": tuple(f"{track.name} {' '.join(track.artists)}" for track in music),
            "spotify_device": tuple(device.name for device in devices),
        }

    def _record_spotify(self, request_text: str, action: str, success: bool, summary: str, arguments: dict[str, str]) -> None:
        self._audit_log.record(
            source="explicit_command",
            request_text=request_text,
            tool_name="spotify_web_api",
            arguments={"action": action, **arguments},
            policy={"access_mode": "network_write", "risk_level": "medium", "permission_mode": "automatic", "requires_confirmation": False},
            approved=True,
            result={"success": success},
            result_summary=summary,
        )

    def _record(self, *, request_text: str, action: str, success: bool, summary: str, arguments: dict[str, str]) -> None:
        self._audit_log.record(
            source="explicit_command",
            request_text=request_text,
            tool_name="media_display",
            arguments={"action": action, **arguments},
            policy={"access_mode": "network_read" if action in {"play_latest", "play_query", "creator_discovery"} else "local_display", "risk_level": "medium", "permission_mode": "automatic", "requires_confirmation": False},
            approved=True,
            result={"success": success},
            result_summary=summary,
        )

    def _controller(self, config: MediaDisplayConfig) -> LocalDisplayController:
        if self._display is None:
            self._display = LocalDisplayController(
                assets_directory=self._assets_directory,
                fullscreen=config.fullscreen,
                autoplay=config.autoplay,
                autoplay_with_sound=config.autoplay_with_sound,
                volume=config.volume,
                edge_autoplay_override=config.edge_autoplay_override,
            )
        return self._display

    @staticmethod
    def _provider_error(message: str | None) -> str:
        return f"Elise: YouTube creator resolution failed: {message or 'Unable to resolve that creator.'}"

    @staticmethod
    def _format_candidates(candidates: tuple[CreatorCandidate, ...]) -> str:
        lines = ["Elise: Multiple YouTube channels matched. Nothing was selected:"]
        for number, candidate in enumerate(candidates[:5], 1):
            handle = candidate.handle or "handle unavailable"
            subscribers = f"{candidate.subscriber_count:,} subscribers" if candidate.subscriber_count is not None else "subscriber count hidden/unavailable"
            excerpt = " ".join(candidate.description.split())[:160] or "No description."
            lines.append(f"{number}. {candidate.title} — {handle} — {subscribers} — {excerpt}")
        lines.append("Use `/creator-select <number>` or `/creator-cancel`.")
        return "\n".join(lines)

    def _play_target(self, request_text: str, config: MediaDisplayConfig, creator: CreatorTarget) -> str:
        provider = YouTubeDataProvider(api_key=read_youtube_api_key(), json_fetcher=self._internet_manager.fetch_public_json)
        lookup = provider.resolve_latest(creator)
        if not lookup.success or lookup.media is None:
            message = lookup.error_message or "Unable to resolve an eligible upload."
            self._record(request_text=request_text, action="play_latest", success=False, summary="YouTube lookup failed.", arguments={"creator_alias": creator.alias, "channel_id": creator.channel_id})
            return f"Elise: YouTube lookup failed: {message}"
        result = self._controller(config).load_and_play(lookup.media)
        self._record(request_text=request_text, action="play_latest", success=result.success, summary="Display load requested." if result.success else "Display launch failed.", arguments={"creator_alias": creator.alias, "channel_id": creator.channel_id, "video_id": lookup.media.video_id})
        if result.success:
            return f"Elise: Selected {lookup.media.title!r}. Display requested; waiting for player status."
        return f"Elise: {result.message}"

    def _play_media(self, request_text: str, config: MediaDisplayConfig, media: YouTubeMediaPayload, creator: CreatorTarget) -> str:
        result = self._controller(config).load_and_play(media)
        self._record(request_text=request_text, action="play_query", success=result.success, summary="Display load requested." if result.success else "Display launch failed.", arguments={"creator_alias": creator.alias, "channel_id": creator.channel_id, "video_id": media.video_id})
        return f"Elise: Selected {media.title!r}. Display requested; waiting for player status." if result.success else f"Elise: {result.message}"

    def _query_target(self, request_text: str, config: MediaDisplayConfig, creator: CreatorTarget, request: MediaVideoQuery) -> str:
        provider = YouTubeDataProvider(api_key=read_youtube_api_key(), json_fetcher=self._internet_manager.fetch_public_json)
        lookup = provider.search_videos(creator, request.query, request.ordering)
        if not lookup.success or not lookup.media:
            self._record(request_text=request_text, action="play_query", success=False, summary="YouTube query failed.", arguments={"creator_alias": creator.alias, "channel_id": creator.channel_id, "query": request.query, "ordering": request.ordering})
            return f"Elise: YouTube lookup failed: {lookup.error_message or 'No matching eligible videos were found.'}"
        if request.list_only:
            self._pending_video_selection = lookup.media[:5]
            lines = [f"Elise: Matching {lookup.media[0].channel_title} videos:"]
            lines.extend(f"{number}. {media.title}" for number, media in enumerate(self._pending_video_selection, 1))
            lines.append("Use `/video-select <number>` or `/video-cancel`.")
            return "\n".join(lines)
        self._pending_video_selection = None
        return self._play_media(request_text, config, lookup.media[0], creator)

    def play_latest(self, request_text: str, creator_alias: str) -> str:
        if not self._internet_manager.is_enabled:
            self._record(request_text=request_text, action="play_latest", success=False, summary="Media lookup blocked because internet is disabled.", arguments={"creator_alias": creator_alias})
            return "Elise: Internet access is disabled. Use `/internet on` to enable media lookup and playback."
        # Every new play request deterministically replaces any prior prompt.
        self._pending_selection = None
        self._pending_creator_query = None
        self._pending_video_selection = None
        try:
            config = MediaDisplayConfig.load(self._config_path)
            creator = config.resolve_creator(creator_alias)
            provider = YouTubeDataProvider(api_key=read_youtube_api_key(), json_fetcher=self._internet_manager.fetch_public_json)
            if creator is not None:
                return self._play_target(request_text, config, creator)
            direct_id = creator_alias.startswith("UC")
            explicit_handle = creator_alias.startswith("@")
            if direct_id:
                resolution = provider.validate_channel_id(creator_alias)
            elif explicit_handle:
                resolution = provider.resolve_exact_handle(creator_alias)
            else:
                resolution = None
            if resolution is not None and resolution.success and resolution.candidate is not None:
                target = CreatorTarget(normalize_creator_alias(creator_alias), resolution.candidate.channel_id)
                return self._play_target(request_text, config, target)
            if resolution is not None:
                self._record(request_text=request_text, action="play_latest", success=False, summary="Exact creator resolution failed.", arguments={"creator_alias": creator_alias})
                return self._provider_error(resolution.error_message)
            discovery = provider.discover_channels(creator_alias)
            if discovery.candidates:
                exact = unique_exact_creator_match(creator_alias, discovery.candidates)
                if exact is not None:
                    config = config.save_alias(self._config_path, creator_alias, exact.channel_id)
                    self._record(request_text=request_text, action="creator_discovery", success=True, summary="Unique exact creator match selected.", arguments={"creator_alias": creator_alias, "channel_id": exact.channel_id})
                    return self._play_target(request_text, config, config.resolve_creator(creator_alias) or CreatorTarget(normalize_creator_alias(creator_alias), exact.channel_id))
                self._pending_selection = (creator_alias, discovery.candidates[:5])
                self._record(request_text=request_text, action="creator_discovery", success=True, summary="Creator candidates require explicit selection.", arguments={"creator_alias": creator_alias})
                return self._format_candidates(discovery.candidates)
            self._pending_selection = None
            self._record(request_text=request_text, action="creator_discovery", success=False, summary="Creator discovery failed.", arguments={"creator_alias": creator_alias})
            if discovery.error_code == "channel_not_found":
                return f"Elise: No YouTube channel matched {creator_alias!r}."
            return self._provider_error(discovery.error_message)
        except MediaConfigurationError as error:
            self._record(request_text=request_text, action="play_latest", success=False, summary="Media configuration was unavailable or invalid.", arguments={"creator_alias": creator_alias})
            return f"Elise: Media display configuration error: {error}"

    def play_query(self, request_text: str, creator_alias: str, query: str, ordering: VideoQueryOrdering = "relevance", list_only: bool = False) -> str:
        request = MediaVideoQuery(creator_alias, query, ordering, list_only)
        if ordering not in {"relevance", "date", "view_count"} or not query.strip():
            return "Elise: A non-empty video topic is required."
        if not self._internet_manager.is_enabled:
            return "Elise: Internet access is disabled. Use `/internet on` to enable media lookup and playback."
        self._pending_selection = None
        self._pending_creator_query = None
        self._pending_video_selection = None
        try:
            config = MediaDisplayConfig.load(self._config_path)
            creator = config.resolve_creator(creator_alias)
            provider = YouTubeDataProvider(api_key=read_youtube_api_key(), json_fetcher=self._internet_manager.fetch_public_json)
            if creator is not None:
                return self._query_target(request_text, config, creator, request)
            resolution = provider.validate_channel_id(creator_alias) if creator_alias.startswith("UC") else provider.resolve_exact_handle(creator_alias) if creator_alias.startswith("@") else None
            if resolution is not None and resolution.success and resolution.candidate is not None:
                return self._query_target(request_text, config, CreatorTarget(normalize_creator_alias(creator_alias), resolution.candidate.channel_id), request)
            if resolution is not None:
                return self._provider_error(resolution.error_message)
            discovery = provider.discover_channels(creator_alias)
            if discovery.candidates:
                exact = unique_exact_creator_match(creator_alias, discovery.candidates)
                if exact is not None:
                    config = config.save_alias(self._config_path, creator_alias, exact.channel_id)
                    return self._query_target(request_text, config, config.resolve_creator(creator_alias) or CreatorTarget(normalize_creator_alias(creator_alias), exact.channel_id), request)
                self._pending_selection = (creator_alias, discovery.candidates[:5])
                self._pending_creator_query = request
                return self._format_candidates(discovery.candidates)
            return f"Elise: No YouTube channel matched {creator_alias!r}." if discovery.error_code == "channel_not_found" else self._provider_error(discovery.error_message)
        except MediaConfigurationError as error:
            return f"Elise: Media display configuration error: {error}"

    def creator_select(self, request_text: str, selection: str) -> str:
        if self._pending_selection is None:
            return "Elise: No creator selection is pending."
        if not self._internet_manager.is_enabled:
            return "Elise: Internet access is disabled. Use `/internet on` to enable media lookup and playback."
        alias, candidates = self._pending_selection
        number = int(selection)
        if number < 1 or number > len(candidates):
            return f"Elise: Selection must be between 1 and {len(candidates)}."
        candidate = candidates[number - 1]
        try:
            config = MediaDisplayConfig.load(self._config_path).save_alias(self._config_path, alias, candidate.channel_id)
            self._pending_selection = None
            pending_query = self._pending_creator_query
            self._pending_creator_query = None
            if pending_query is not None:
                return self._query_target(request_text, config, config.resolve_creator(alias) or CreatorTarget(normalize_creator_alias(alias), candidate.channel_id), pending_query)
            return self._play_target(request_text, config, config.resolve_creator(alias) or CreatorTarget(normalize_creator_alias(alias), candidate.channel_id))
        except MediaConfigurationError as error:
            return f"Elise: Media display configuration error: {error}"

    def creator_aliases(self) -> str:
        try:
            aliases = MediaDisplayConfig.load(self._config_path).creator_aliases
        except MediaConfigurationError as error:
            return f"Elise: Media display configuration error: {error}"
        if not aliases:
            return "Elise: No YouTube creator aliases are saved."
        return "Elise: Saved YouTube creator aliases:\n" + "\n".join(f"- {alias}: {target.channel_id}" for alias, target in sorted(aliases.items()))

    def creator_forget(self, alias: str) -> str:
        try:
            _, removed = MediaDisplayConfig.load(self._config_path).forget_alias(self._config_path, alias)
        except MediaConfigurationError as error:
            return f"Elise: Media display configuration error: {error}"
        return f"Elise: Forgot creator alias {normalize_creator_alias(alias)!r}." if removed else f"Elise: No saved creator alias matches {alias!r}."

    def handle_command(self, request_text: str) -> str | None:
        normalized = " ".join(request_text.casefold().split())
        if normalized == "/music":
            return "Elise: Music console opened." if self._music_display.open() else "Elise: Music console could not be opened."
        if normalized == "/music close":
            self._music_display.close()
            return "Elise: Music console closed; Spotify playback continues."
        if normalized == "/music status":
            return self.spotify.control(request_text, "current")
        spotify_response = self.spotify.handle_command(request_text)
        if spotify_response is not None:
            if normalized.startswith("/music-select "):
                self._open_music_console_after_play(spotify_response)
            return spotify_response
        parsed = parse_media_command(request_text)
        if parsed is None:
            return None
        if parsed.error:
            return parsed.error
        if parsed.action == "play_latest":
            assert parsed.creator_alias is not None
            return self.play_latest(request_text, parsed.creator_alias)
        if parsed.action == "creator_select":
            assert parsed.creator_alias is not None
            return self.creator_select(request_text, parsed.creator_alias)
        if parsed.action == "creator_cancel":
            if self._pending_selection is None:
                return "Elise: No creator selection is pending."
            self._pending_selection = None
            self._pending_creator_query = None
            return "Elise: Creator selection cancelled."
        if parsed.action == "video_select":
            assert parsed.creator_alias is not None
            if self._pending_video_selection is None:
                return "Elise: No video selection is pending."
            number = int(parsed.creator_alias)
            if number < 1 or number > len(self._pending_video_selection):
                return f"Elise: Selection must be between 1 and {len(self._pending_video_selection)}."
            media = self._pending_video_selection[number - 1]
            self._pending_video_selection = None
            try:
                config = MediaDisplayConfig.load(self._config_path)
                return self._play_media(request_text, config, media, CreatorTarget(media.channel_title, media.channel_id))
            except MediaConfigurationError as error:
                return f"Elise: Media display configuration error: {error}"
        if parsed.action == "video_cancel":
            if self._pending_video_selection is None:
                return "Elise: No video selection is pending."
            self._pending_video_selection = None
            return "Elise: Video selection cancelled."
        if parsed.action == "creator_aliases":
            return self.creator_aliases()
        if parsed.action == "creator_forget":
            assert parsed.creator_alias is not None
            return self.creator_forget(parsed.creator_alias)
        if parsed.action == "status":
            return self.display_status(request_text)
        return self.display_action(request_text, parsed.action)

    def play_music(self, request_text: str, query: str, artist: str | None = None, album: str | None = None, list_only: bool = False) -> str:
        response = self.spotify.search_and_play(request_text, query, artist, album, list_only)
        self._open_music_console_after_play(response)
        return response

    def _open_music_console_after_play(self, response: str) -> None:
        if response.startswith("Elise: Spotify started"):
            try:
                enabled = MediaDisplayConfig.load(self._config_path).open_music_console_on_play
            except MediaConfigurationError:
                enabled = True
            if enabled:
                self._music_display.open()

    def spotify_control(self, request_text: str, action: str) -> str:
        return self.spotify.control(request_text, action)

    def display_action(self, request_text: str, action: str) -> str:
        if self._display is None:
            self._record(request_text=request_text, action=action, success=False, summary="No display session is active.", arguments={})
            return "Elise: No display session is active."
        result = {"pause": self._display.pause, "resume": self._display.resume, "close": self._display.close}[action]()
        self._record(request_text=request_text, action=action, success=result.success, summary=result.message, arguments={})
        return f"Elise: {result.message}"

    def display_status(self, request_text: str | None = None) -> str:
        if self._display is None:
            if request_text is not None:
                self._record(request_text=request_text, action="status", success=True, summary="Display status is closed.", arguments={})
            return "Elise: Display status: closed."
        snapshot = self._display.snapshot()
        status = (
            snapshot.reported_status or "waiting_for_player"
            if snapshot.window_running
            else "closed"
        )
        media = f" {snapshot.media.title!r}" if snapshot.media else ""
        if request_text is not None:
            self._record(request_text=request_text, action="status", success=True, summary=f"Display reported {status}.", arguments={})
        suffix = "; click the display to enable sound" if status == "playing_muted" else ""
        return f"Elise: Display status: {status}; window={'open' if snapshot.window_running else 'closed'}{suffix}.{media}"

    def shutdown(self) -> None:
        self._music_display.close()
        if self._display is not None:
            self._display.shutdown()
