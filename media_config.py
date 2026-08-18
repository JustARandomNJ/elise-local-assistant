from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from media_models import CreatorTarget


YOUTUBE_API_KEY_ENVIRONMENT_VARIABLE = "ELISE_YOUTUBE_API_KEY"
_CHANNEL_ID_PATTERN = re.compile(r"^UC[A-Za-z0-9_-]{22}$")


class MediaConfigurationError(ValueError):
    """Raised for invalid public-only media configuration."""


def normalize_creator_alias(value: str) -> str:
    return " ".join(value.strip().casefold().split())


@dataclass(frozen=True)
class MediaDisplayConfig:
    creator_aliases: dict[str, CreatorTarget]
    fullscreen: bool = False
    autoplay: bool = True
    autoplay_with_sound: bool = True
    volume: int = 100
    edge_autoplay_override: bool = True
    open_music_console_on_play: bool = True

    @classmethod
    def load(cls, path: str | Path) -> "MediaDisplayConfig":
        config_path = Path(path)
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise MediaConfigurationError(
                "Media display configuration was not found."
            ) from error
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise MediaConfigurationError(
                "Media display configuration could not be read."
            ) from error

        if not isinstance(raw, dict):
            raise MediaConfigurationError("Media display configuration must be an object.")

        if set(raw) - {"version", "youtube", "display", "spotify"}:
            raise MediaConfigurationError("Media display configuration contains unsupported fields.")
        if raw.get("version") != 1:
            raise MediaConfigurationError("Media display configuration requires version 1.")

        youtube = raw.get("youtube")
        if not isinstance(youtube, dict) or set(youtube) != {"creator_aliases"}:
            raise MediaConfigurationError(
                "YouTube configuration must contain only creator_aliases."
            )
        aliases = youtube.get("creator_aliases")
        if not isinstance(aliases, dict):
            raise MediaConfigurationError("creator_aliases must be an object.")

        targets: dict[str, CreatorTarget] = {}
        for alias, value in aliases.items():
            if not isinstance(alias, str) or not isinstance(value, dict):
                raise MediaConfigurationError("Each creator alias must map to an object.")
            if set(value) != {"channel_id"}:
                raise MediaConfigurationError("Creator aliases may contain only channel_id.")
            channel_id = value.get("channel_id")
            normalized = normalize_creator_alias(alias)
            if not normalized or not isinstance(channel_id, str) or not _CHANNEL_ID_PATTERN.fullmatch(channel_id):
                raise MediaConfigurationError("Creator aliases require a valid public YouTube channel ID.")
            if normalized in targets:
                raise MediaConfigurationError("Creator aliases must be unique after normalization.")
            targets[normalized] = CreatorTarget(alias=alias.strip(), channel_id=channel_id)

        display = raw.get("display", {})
        if not isinstance(display, dict) or set(display) - {"fullscreen", "autoplay", "autoplay_with_sound", "volume", "edge_autoplay_override"}:
            raise MediaConfigurationError("Display configuration contains unsupported fields.")
        fullscreen = display.get("fullscreen", False)
        if not isinstance(fullscreen, bool):
            raise MediaConfigurationError("display.fullscreen must be true or false.")
        autoplay = display.get("autoplay", True)
        autoplay_with_sound = display.get("autoplay_with_sound", True)
        edge_autoplay_override = display.get("edge_autoplay_override", True)
        volume = display.get("volume", 100)
        if not isinstance(autoplay, bool) or not isinstance(autoplay_with_sound, bool) or not isinstance(edge_autoplay_override, bool):
            raise MediaConfigurationError("Display playback settings must be true or false.")
        if isinstance(volume, bool) or not isinstance(volume, (int, float)) or not math.isfinite(volume):
            raise MediaConfigurationError("display.volume must be a number.")
        volume = max(0, min(100, int(volume)))
        spotify = raw.get("spotify", {})
        if not isinstance(spotify, dict) or set(spotify) - {"open_music_console_on_play"}:
            raise MediaConfigurationError("Spotify configuration contains unsupported fields.")
        open_music_console_on_play = spotify.get("open_music_console_on_play", True)
        if not isinstance(open_music_console_on_play, bool):
            raise MediaConfigurationError("spotify.open_music_console_on_play must be true or false.")
        return cls(targets, fullscreen, autoplay, autoplay_with_sound, volume, edge_autoplay_override, open_music_console_on_play)

    def _display_dict(self) -> dict[str, bool | int]:
        return {"fullscreen": self.fullscreen, "autoplay": self.autoplay, "autoplay_with_sound": self.autoplay_with_sound, "volume": self.volume, "edge_autoplay_override": self.edge_autoplay_override}

    def resolve_creator(self, alias: str) -> CreatorTarget | None:
        return self.creator_aliases.get(normalize_creator_alias(alias))

    def save_alias(self, path: str | Path, alias: str, channel_id: str) -> "MediaDisplayConfig":
        normalized = normalize_creator_alias(alias)
        if not normalized or not _CHANNEL_ID_PATTERN.fullmatch(channel_id):
            raise MediaConfigurationError("Creator aliases require a valid public YouTube channel ID.")
        updated = dict(self.creator_aliases)
        existing = updated.get(normalized)
        if existing is not None and existing.channel_id != channel_id:
            raise MediaConfigurationError("That normalized creator alias is already assigned to another channel.")
        updated[normalized] = CreatorTarget(alias=normalized, channel_id=channel_id)
        payload = {
            "version": 1,
            "youtube": {"creator_aliases": {key: {"channel_id": target.channel_id} for key, target in sorted(updated.items())}},
            "display": self._display_dict(),
            "spotify": {"open_music_console_on_play": self.open_music_console_on_play},
        }
        config_path = Path(path)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = config_path.with_suffix(config_path.suffix + ".tmp")
        try:
            temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            temporary.replace(config_path)
        except OSError as error:
            raise MediaConfigurationError("Media display configuration could not be saved.") from error
        return MediaDisplayConfig(updated, self.fullscreen, self.autoplay, self.autoplay_with_sound, self.volume, self.edge_autoplay_override, self.open_music_console_on_play)

    def forget_alias(self, path: str | Path, alias: str) -> tuple["MediaDisplayConfig", bool]:
        normalized = normalize_creator_alias(alias)
        if normalized not in self.creator_aliases:
            return self, False
        updated = dict(self.creator_aliases)
        del updated[normalized]
        # Reuse the same strict, public-only serialization shape.
        payload = {"version": 1, "youtube": {"creator_aliases": {key: {"channel_id": target.channel_id} for key, target in sorted(updated.items())}}, "display": self._display_dict(), "spotify": {"open_music_console_on_play": self.open_music_console_on_play}}
        config_path = Path(path)
        temporary = config_path.with_suffix(config_path.suffix + ".tmp")
        try:
            temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            temporary.replace(config_path)
        except OSError as error:
            raise MediaConfigurationError("Media display configuration could not be saved.") from error
        return MediaDisplayConfig(updated, self.fullscreen, self.autoplay, self.autoplay_with_sound, self.volume, self.edge_autoplay_override, self.open_music_console_on_play), True


def read_youtube_api_key() -> str:
    """Read the only supported YouTube credential source without displaying it."""

    value = os.environ.get(YOUTUBE_API_KEY_ENVIRONMENT_VARIABLE, "").strip()
    if not value:
        raise MediaConfigurationError(
            f"{YOUTUBE_API_KEY_ENVIRONMENT_VARIABLE} is not set."
        )
    return value
