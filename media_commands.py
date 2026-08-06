from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass

from audit import ToolAuditLog
from internet import InternetManager
from media_config import MediaConfigurationError, MediaDisplayConfig, normalize_creator_alias, read_youtube_api_key
from media_display import LocalDisplayController
from media_models import CreatorCandidate, CreatorTarget
from media_providers import YouTubeDataProvider


@dataclass(frozen=True)
class ParsedMediaCommand:
    action: str
    creator_alias: str | None = None
    error: str | None = None


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

    def _record(self, *, request_text: str, action: str, success: bool, summary: str, arguments: dict[str, str]) -> None:
        self._audit_log.record(
            source="explicit_command",
            request_text=request_text,
            tool_name="media_display",
            arguments={"action": action, **arguments},
            policy={"access_mode": "network_read" if action in {"play_latest", "creator_discovery"} else "local_display", "risk_level": "medium", "permission_mode": "automatic", "requires_confirmation": False},
            approved=True,
            result={"success": success},
            result_summary=summary,
        )

    def _controller(self, config: MediaDisplayConfig) -> LocalDisplayController:
        if self._display is None:
            self._display = LocalDisplayController(assets_directory=self._assets_directory, fullscreen=config.fullscreen)
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

    def play_latest(self, request_text: str, creator_alias: str) -> str:
        if not self._internet_manager.is_enabled:
            self._record(request_text=request_text, action="play_latest", success=False, summary="Media lookup blocked because internet is disabled.", arguments={"creator_alias": creator_alias})
            return "Elise: Internet access is disabled. Use `/internet on` to enable media lookup and playback."
        # Every new play request deterministically replaces any prior prompt.
        self._pending_selection = None
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
            return "Elise: Creator selection cancelled."
        if parsed.action == "creator_aliases":
            return self.creator_aliases()
        if parsed.action == "creator_forget":
            assert parsed.creator_alias is not None
            return self.creator_forget(parsed.creator_alias)
        if parsed.action == "status":
            return self.display_status(request_text)
        return self.display_action(request_text, parsed.action)

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
        return f"Elise: Display status: {status}; window={'open' if snapshot.window_running else 'closed'}.{media}"

    def shutdown(self) -> None:
        if self._display is not None:
            self._display.shutdown()
