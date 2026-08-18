from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


VideoQueryOrdering = Literal["relevance", "date", "view_count"]


@dataclass(frozen=True)
class MediaVideoQuery:
    creator: str
    query: str
    ordering: VideoQueryOrdering = "relevance"
    list_only: bool = False


@dataclass(frozen=True)
class MediaMusicQuery:
    query: str
    artist: str | None = None
    album: str | None = None
    list_only: bool = False
    provider: Literal["spotify"] = "spotify"


@dataclass(frozen=True)
class SpotifyTrackResult:
    id: str
    uri: str
    name: str
    artists: tuple[str, ...]
    album: str
    duration_ms: int
    explicit: bool


@dataclass(frozen=True)
class SpotifyDevice:
    id: str
    name: str
    type: str
    is_active: bool
    is_restricted: bool


@dataclass(frozen=True)
class SpotifySearchResult:
    success: bool
    tracks: tuple[SpotifyTrackResult, ...] = ()
    error_code: str | None = None
    error_message: str | None = None


DisplayReportStatus = Literal[
    "player_ready",
    "playing",
    "playing_muted",
    "paused",
    "ended",
    "autoplay_blocked",
    "player_error",
]


@dataclass(frozen=True)
class CreatorTarget:
    """One configured public creator identity."""

    alias: str
    channel_id: str


@dataclass(frozen=True)
class CreatorCandidate:
    channel_id: str
    title: str
    handle: str | None
    subscriber_count: int | None
    description: str


@dataclass(frozen=True)
class CreatorResolutionResult:
    success: bool
    candidate: CreatorCandidate | None = None
    candidates: tuple[CreatorCandidate, ...] = ()
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class YouTubeMediaPayload:
    """Validated public data that may be sent to the local display."""

    video_id: str
    title: str
    published_at: datetime
    channel_id: str
    channel_title: str

    def to_display_dict(self) -> dict[str, str]:
        return {
            "kind": "youtube",
            "video_id": self.video_id,
            "title": self.title,
            "published_at": self.published_at.isoformat(),
            "channel_id": self.channel_id,
            "channel_title": self.channel_title,
        }


@dataclass(frozen=True)
class ProviderLookupResult:
    success: bool
    media: YouTubeMediaPayload | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class ProviderSearchResult:
    success: bool
    media: tuple[YouTubeMediaPayload, ...] = ()
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class DisplaySnapshot:
    server_running: bool
    window_running: bool
    desired_action: Literal["idle", "load", "pause", "resume", "close"]
    reported_status: DisplayReportStatus | None
    media: YouTubeMediaPayload | None
    message: str | None


@dataclass(frozen=True)
class DisplayActionResult:
    success: bool
    message: str
    snapshot: DisplaySnapshot
