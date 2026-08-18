from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlencode
import re

from media_models import CreatorCandidate, CreatorResolutionResult, CreatorTarget, ProviderLookupResult, ProviderSearchResult, VideoQueryOrdering, YouTubeMediaPayload


_VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YOUTUBE_API_ROOT = "https://www.googleapis.com/youtube/v3"
_CHANNEL_ID_PATTERN = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_HANDLE_SEPARATORS = frozenset("._-·")


class YouTubeDataProvider:
    """Deterministic YouTube lookup using a configured channel uploads playlist."""

    def __init__(
        self,
        *,
        api_key: str,
        json_fetcher: Callable[[str], dict[str, Any]],
    ) -> None:
        self._api_key = api_key
        self._json_fetcher = json_fetcher

    def _request(self, resource: str, parameters: dict[str, str]) -> dict[str, Any]:
        encoded = urlencode({**parameters, "key": self._api_key})
        response = self._json_fetcher(f"{_YOUTUBE_API_ROOT}/{resource}?{encoded}")
        if not isinstance(response, dict):
            return {"success": False, "error_code": "malformed_response", "error": "YouTube returned a malformed API response."}
        if response.get("success") is True:
            if not isinstance(response.get("data"), dict):
                return {"success": False, "error_code": "malformed_response", "error": "YouTube returned a malformed API response."}
        return response

    @staticmethod
    def _items(response: dict[str, Any]) -> list[Any] | None:
        if not isinstance(response, dict) or response.get("success") is not True:
            return None
        payload = response.get("data")
        if not isinstance(payload, dict):
            return None
        items = payload.get("items")
        return items if isinstance(items, list) else None

    @staticmethod
    def _timestamp(value: object) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _failure(response: dict[str, Any], fallback: str) -> ProviderLookupResult:
        return ProviderLookupResult(
            success=False,
            error_code=str(response.get("error_code", "provider_error")),
            error_message=str(response.get("error", fallback)),
        )

    @staticmethod
    def _resolution_failure(response: dict[str, Any], fallback: str) -> CreatorResolutionResult:
        return CreatorResolutionResult(False, error_code=str(response.get("error_code", "provider_error")), error_message=str(response.get("error", fallback)))

    @staticmethod
    def _candidate(item: object) -> CreatorCandidate | None:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not _CHANNEL_ID_PATTERN.fullmatch(item["id"]):
            return None
        snippet = item.get("snippet")
        statistics = item.get("statistics", {})
        if not isinstance(snippet, dict) or not isinstance(statistics, dict):
            return None
        title = snippet.get("title")
        if not isinstance(title, str) or not title.strip():
            return None
        custom_url = snippet.get("customUrl")
        handle = custom_url.strip() if isinstance(custom_url, str) and custom_url.strip().startswith("@") else None
        subscribers = None
        if statistics.get("hiddenSubscriberCount") is not True:
            raw_count = statistics.get("subscriberCount")
            if isinstance(raw_count, str) and raw_count.isdigit():
                subscribers = int(raw_count)
        description = snippet.get("description")
        return CreatorCandidate(item["id"], title.strip(), handle, subscribers, description.strip() if isinstance(description, str) else "")

    @staticmethod
    def is_valid_handle(value: str) -> bool:
        """Validate the public handle shape before sending an exact lookup."""

        if not value.startswith("@"):
            return False
        handle = value[1:]
        return (
            3 <= len(handle) <= 30
            and handle[0].isalnum()
            and handle[-1].isalnum()
            and all(character.isalnum() or character in _HANDLE_SEPARATORS for character in handle)
        )

    def resolve_exact_handle(self, value: str) -> CreatorResolutionResult:
        if not self.is_valid_handle(value):
            return CreatorResolutionResult(False, error_code="invalid_handle", error_message="The YouTube handle is invalid.")
        response = self._request("channels", {"part": "snippet,contentDetails,statistics", "forHandle": value})
        if not response.get("success"):
            return self._resolution_failure(response, "Unable to resolve that YouTube handle.")
        items = self._items(response)
        if items is None:
            return CreatorResolutionResult(False, error_code="malformed_response", error_message="YouTube returned a malformed channel response.")
        if not items:
            return CreatorResolutionResult(False, error_code="channel_not_found", error_message="No exact YouTube handle matched.")
        candidates = tuple(candidate for candidate in (self._candidate(item) for item in items) if candidate is not None)
        if not candidates:
            return CreatorResolutionResult(False, error_code="malformed_response", error_message="YouTube returned a malformed channel response.")
        if len(candidates) != 1:
            return CreatorResolutionResult(False, error_code="channel_not_found", error_message="No exact YouTube handle matched.")
        return CreatorResolutionResult(True, candidate=candidates[0])

    def validate_channel_id(self, channel_id: str) -> CreatorResolutionResult:
        if not _CHANNEL_ID_PATTERN.fullmatch(channel_id):
            return CreatorResolutionResult(False, error_code="invalid_channel_id", error_message="The YouTube channel ID is invalid.")
        response = self._request("channels", {"part": "snippet,contentDetails,statistics", "id": channel_id})
        if not response.get("success"):
            return self._resolution_failure(response, "Unable to validate that YouTube channel ID.")
        items = self._items(response)
        if items is None:
            return CreatorResolutionResult(False, error_code="malformed_response", error_message="YouTube returned a malformed channel response.")
        candidates = tuple(candidate for candidate in (self._candidate(item) for item in items) if candidate is not None)
        if len(candidates) != 1 or candidates[0].channel_id != channel_id:
            return CreatorResolutionResult(False, error_code="channel_not_found", error_message="That exact YouTube channel ID was not found.")
        return CreatorResolutionResult(True, candidate=candidates[0])

    def discover_channels(self, query: str) -> CreatorResolutionResult:
        search = self._request("search", {"part": "snippet", "type": "channel", "q": query, "maxResults": "5"})
        if not search.get("success"):
            return self._resolution_failure(search, "Unable to search for YouTube channels.")
        items = self._items(search)
        if items is None:
            return CreatorResolutionResult(False, error_code="malformed_response", error_message="YouTube returned a malformed search response.")
        ids: list[str] = []
        for item in items[:5]:
            snippet = item.get("snippet") if isinstance(item, dict) else None
            channel_id = snippet.get("channelId") if isinstance(snippet, dict) else None
            if not isinstance(channel_id, str) and isinstance(item, dict):
                channel_id = item.get("id", {}).get("channelId") if isinstance(item.get("id"), dict) else None
            if isinstance(channel_id, str) and _CHANNEL_ID_PATTERN.fullmatch(channel_id) and channel_id not in ids:
                ids.append(channel_id)
        if not ids:
            error_code = "channel_not_found" if not items else "malformed_response"
            error_message = "No matching YouTube channels were found." if not items else "YouTube returned a malformed search response."
            return CreatorResolutionResult(False, error_code=error_code, error_message=error_message)
        details = self._request("channels", {"part": "snippet,contentDetails,statistics", "id": ",".join(ids)})
        if not details.get("success"):
            return self._resolution_failure(details, "Unable to read discovered YouTube channels.")
        detail_items = self._items(details)
        if detail_items is None:
            return CreatorResolutionResult(False, error_code="malformed_response", error_message="YouTube returned malformed channel details.")
        if not detail_items:
            return CreatorResolutionResult(False, error_code="channel_not_found", error_message="No matching YouTube channels were found.")
        by_id = {candidate.channel_id: candidate for candidate in (self._candidate(item) for item in detail_items) if candidate is not None}
        candidates = tuple(by_id[channel_id] for channel_id in ids if channel_id in by_id)
        if not candidates:
            return CreatorResolutionResult(False, error_code="malformed_response", error_message="YouTube returned malformed channel details.")
        return CreatorResolutionResult(False, candidates=candidates, error_code="selection_required", error_message="Creator selection is required.")

    def resolve_latest(self, creator: CreatorTarget) -> ProviderLookupResult:
        channels = self._request(
            "channels",
            {"part": "contentDetails", "id": creator.channel_id},
        )
        if not channels.get("success"):
            return self._failure(channels, "Unable to read the configured channel.")
        channel_items = self._items(channels)
        if channel_items is None:
            return ProviderLookupResult(False, error_code="malformed_response", error_message="YouTube returned a malformed channel response.")
        if not channel_items:
            return ProviderLookupResult(False, error_code="channel_not_found", error_message="Configured channel was not found.")
        try:
            uploads_playlist = channel_items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
        except (KeyError, IndexError, TypeError):
            return ProviderLookupResult(False, error_code="uploads_playlist_missing", error_message="Configured channel did not expose an uploads playlist.")
        if not isinstance(uploads_playlist, str) or not uploads_playlist:
            return ProviderLookupResult(False, error_code="uploads_playlist_missing", error_message="Configured channel did not expose an uploads playlist.")

        playlist = self._request(
            "playlistItems",
            {
                "part": "contentDetails,snippet",
                "playlistId": uploads_playlist,
                "maxResults": "25",
            },
        )
        if not playlist.get("success"):
            return self._failure(playlist, "Unable to read recent uploads.")
        candidates: list[str] = []
        playlist_items = self._items(playlist)
        if playlist_items is None:
            return ProviderLookupResult(False, error_code="malformed_response", error_message="YouTube returned a malformed uploads response.")
        for item in playlist_items:
            if not isinstance(item, dict):
                continue
            video_id = item.get("contentDetails", {}).get("videoId")
            if isinstance(video_id, str) and _VIDEO_ID_PATTERN.fullmatch(video_id) and video_id not in candidates:
                candidates.append(video_id)
        if not candidates:
            return ProviderLookupResult(False, error_code="no_recent_candidates", error_message="No valid recent upload candidates were available.")

        videos = self._request(
            "videos",
            {
                "part": "snippet,status,liveStreamingDetails",
                "id": ",".join(candidates),
                "maxResults": str(len(candidates)),
            },
        )
        if not videos.get("success"):
            return self._failure(videos, "Unable to verify recent uploads.")

        eligible: list[YouTubeMediaPayload] = []
        video_items = self._items(videos)
        if video_items is None:
            return ProviderLookupResult(False, error_code="malformed_response", error_message="YouTube returned a malformed videos response.")
        for item in video_items:
            if not isinstance(item, dict):
                continue
            video_id = item.get("id")
            snippet = item.get("snippet")
            status = item.get("status")
            if not isinstance(video_id, str) or not _VIDEO_ID_PATTERN.fullmatch(video_id):
                continue
            if not isinstance(snippet, dict) or not isinstance(status, dict):
                continue
            if status.get("privacyStatus") != "public" or status.get("embeddable") is not True:
                continue
            if snippet.get("liveBroadcastContent") == "upcoming":
                continue
            published_at = self._timestamp(snippet.get("publishedAt"))
            title = snippet.get("title")
            channel_title = snippet.get("channelTitle")
            channel_id = snippet.get("channelId")
            if not published_at or not all(isinstance(value, str) and value.strip() for value in (title, channel_title, channel_id)):
                continue
            eligible.append(YouTubeMediaPayload(video_id, title.strip(), published_at, channel_id, channel_title.strip()))

        if not eligible:
            return ProviderLookupResult(False, error_code="no_eligible_upload", error_message="No eligible public upload was found.")
        selected = max(eligible, key=lambda item: (item.published_at, item.video_id))
        return ProviderLookupResult(success=True, media=selected)

    def search_videos(self, creator: CreatorTarget, query: str, ordering: VideoQueryOrdering) -> ProviderSearchResult:
        """Search only within a validated creator channel, then validate every result."""

        order_map = {"relevance": "relevance", "date": "date", "view_count": "viewCount"}
        if ordering not in order_map or not _CHANNEL_ID_PATTERN.fullmatch(creator.channel_id) or not query.strip():
            return ProviderSearchResult(False, error_code="invalid_query", error_message="The video search request was invalid.")
        search = self._request("search", {
            "part": "snippet", "type": "video", "channelId": creator.channel_id,
            "q": query, "maxResults": "10", "videoEmbeddable": "true", "order": order_map[ordering],
        })
        if not search.get("success"):
            failure = self._failure(search, "Unable to search this creator's videos.")
            return ProviderSearchResult(False, error_code=failure.error_code, error_message=failure.error_message)
        items = self._items(search)
        if items is None:
            return ProviderSearchResult(False, error_code="malformed_response", error_message="YouTube returned a malformed video search response.")
        ids: list[str] = []
        for item in items:
            raw_id = item.get("id", {}).get("videoId") if isinstance(item, dict) and isinstance(item.get("id"), dict) else None
            if isinstance(raw_id, str) and _VIDEO_ID_PATTERN.fullmatch(raw_id) and raw_id not in ids:
                ids.append(raw_id)
        if not ids:
            return ProviderSearchResult(False, error_code="no_matching_videos", error_message="No matching eligible videos were found.")
        videos = self._request("videos", {
            "part": "snippet,status,liveStreamingDetails,contentDetails", "id": ",".join(ids), "maxResults": str(len(ids)),
        })
        if not videos.get("success"):
            failure = self._failure(videos, "Unable to validate matching videos.")
            return ProviderSearchResult(False, error_code=failure.error_code, error_message=failure.error_message)
        video_items = self._items(videos)
        if video_items is None:
            return ProviderSearchResult(False, error_code="malformed_response", error_message="YouTube returned malformed video details.")
        by_id: dict[str, YouTubeMediaPayload] = {}
        for item in video_items:
            if not isinstance(item, dict):
                continue
            video_id, snippet, status = item.get("id"), item.get("snippet"), item.get("status")
            if not isinstance(video_id, str) or not _VIDEO_ID_PATTERN.fullmatch(video_id) or video_id not in ids:
                continue
            if not isinstance(snippet, dict) or not isinstance(status, dict):
                continue
            if status.get("privacyStatus") != "public" or status.get("embeddable") is not True:
                continue
            if snippet.get("liveBroadcastContent") == "upcoming":
                continue
            published_at = self._timestamp(snippet.get("publishedAt"))
            title, channel_id, channel_title = snippet.get("title"), snippet.get("channelId"), snippet.get("channelTitle")
            if channel_id != creator.channel_id or not published_at or not all(isinstance(v, str) and v.strip() for v in (title, channel_title)):
                continue
            by_id[video_id] = YouTubeMediaPayload(video_id, title.strip(), published_at, channel_id, channel_title.strip())
        eligible = tuple(by_id[video_id] for video_id in ids if video_id in by_id)
        if not eligible:
            return ProviderSearchResult(False, error_code="no_matching_videos", error_message="No matching eligible videos were found.")
        return ProviderSearchResult(True, media=eligible)
