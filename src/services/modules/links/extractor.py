"""Link extractor service — detect video URLs and fetch YouTube metadata."""

from __future__ import annotations

import asyncio
import re
from typing import Any

import aiohttp
import structlog

from src.services.modules.links.formatters import format_view_count, parse_iso_duration
from src.services.modules.links.models import (
    LinkContext,
    VideoLink,
    VideoMetadata,
    YouTubeComment,
)

logger = structlog.get_logger(__name__)

_YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"

# URL detection patterns — supports YouTube, TikTok, Instagram.
# TikTok and Instagram are detected for future extensibility,
# but only YouTube is enriched with metadata for now.
_VIDEO_URL_PATTERN = re.compile(
    r"https?://(?:www\.)?"
    r"(?:"
    r"youtube\.com/watch\?[^\s)>\]]*v="
    r"|youtu\.be/"
    r"|youtube\.com/shorts/"
    r"|tiktok\.com/@[^/]+/video/"
    r"|vm\.tiktok\.com/"
    r"|m\.tiktok\.com/v/"
    r"|instagram\.com/(?:p|reel)/"
    r")[^\s)>\]]*",
    re.IGNORECASE,
)

_YOUTUBE_ID_PATTERN = re.compile(
    r"(?:v=|youtu\.be/|shorts/)([a-zA-Z0-9_-]{11})"
)


def _classify_platform(url: str) -> str:
    """Determine the platform from a video URL."""
    lower = url.lower()
    if "youtube.com" in lower or "youtu.be" in lower:
        return "youtube"
    if "tiktok.com" in lower:
        return "tiktok"
    if "instagram.com" in lower:
        return "instagram"
    return "unknown"


def _extract_youtube_id(url: str) -> str | None:
    """Extract YouTube video ID (11 chars) from a URL."""
    match = _YOUTUBE_ID_PATTERN.search(url)
    return match.group(1) if match else None


class LinkExtractorService:
    """Detect video links in messages and fetch YouTube metadata."""

    def __init__(self, youtube_api_key: str | None = None) -> None:
        self._youtube_api_key = youtube_api_key

    def detect_links(self, text: str) -> list[VideoLink]:
        """Extract video links from message text. Pure function, no I/O."""
        urls = _VIDEO_URL_PATTERN.findall(text)
        links: list[VideoLink] = []
        seen: set[str] = set()

        for url in urls:
            if url in seen:
                continue
            seen.add(url)

            platform = _classify_platform(url)
            video_id = _extract_youtube_id(url) if platform == "youtube" else None
            links.append(VideoLink(url=url, platform=platform, video_id=video_id))

        return links

    async def extract(self, text: str) -> LinkContext | None:
        """Full pipeline: detect links -> fetch YouTube metadata -> return context.

        Returns None if no YouTube links with video IDs are found.
        TikTok/Instagram links are ignored — the AI sees them in raw message text.
        """
        all_links = self.detect_links(text)
        youtube_links = [
            link for link in all_links
            if link.platform == "youtube" and link.video_id
        ]

        if not youtube_links:
            return None

        # Only enrich the first YouTube link to limit API quota
        first = youtube_links[0]
        assert first.video_id is not None  # guaranteed by filter above
        metadata = await self._fetch_youtube_metadata(first.video_id)

        if metadata is None:
            return None

        return LinkContext(
            youtube_links=youtube_links,
            metadata={first.video_id: metadata},
        )

    async def _fetch_youtube_metadata(self, video_id: str) -> VideoMetadata | None:
        """Fetch video metadata + top comments from YouTube Data API v3."""
        if not self._youtube_api_key:
            logger.debug("No YouTube API key, skipping metadata fetch")
            return None

        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            ) as session:
                # Parallel: video info + comments
                video_task = self._fetch_video_info(session, video_id)
                comments_task = self._fetch_comments(session, video_id)

                video_info, comments = await asyncio.gather(
                    video_task, comments_task, return_exceptions=True
                )

                # Video info is required
                if isinstance(video_info, BaseException) or video_info is None:
                    if isinstance(video_info, BaseException):
                        logger.warning(
                            "YouTube video API failed",
                            video_id=video_id,
                            error=str(video_info),
                        )
                    return None

                # Comments are optional
                if isinstance(comments, BaseException):
                    logger.debug(
                        "YouTube comments API failed (non-critical)",
                        video_id=video_id,
                    )
                    comments = []

                return VideoMetadata(
                    title=video_info["title"],
                    channel=video_info["channel"],
                    views=video_info["views"],
                    duration=video_info.get("duration"),
                    description=video_info.get("description", ""),
                    tags=video_info.get("tags", []),
                    top_comments=comments,
                )

        except Exception:
            logger.exception("YouTube metadata fetch failed", video_id=video_id)
            return None

    async def _fetch_video_info(
        self,
        session: aiohttp.ClientSession,
        video_id: str,
    ) -> dict[str, Any] | None:
        """GET /youtube/v3/videos — fetch snippet, statistics, contentDetails."""
        url = f"{_YOUTUBE_API_BASE}/videos"
        params: dict[str, str] = {
            "id": video_id,
            "key": self._youtube_api_key or "",
            "part": "snippet,statistics,contentDetails",
        }

        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                logger.warning(
                    "YouTube API error",
                    status=resp.status,
                    video_id=video_id,
                )
                return None

            data = await resp.json()
            items = data.get("items", [])
            if not items:
                return None

            item = items[0]
            snippet = item.get("snippet", {})
            stats = item.get("statistics", {})
            content = item.get("contentDetails", {})

            # Format views
            raw_views = int(stats.get("viewCount", 0))
            views_str = format_view_count(raw_views)

            # Format duration
            duration_str = parse_iso_duration(content.get("duration", ""))

            # Tags (up to 5)
            tags = (snippet.get("tags") or [])[:5]

            # Description (truncated)
            description = (snippet.get("description") or "")[:500]

            return {
                "title": snippet.get("title", ""),
                "channel": snippet.get("channelTitle", ""),
                "views": views_str,
                "duration": duration_str,
                "description": description,
                "tags": tags,
            }

    async def _fetch_comments(
        self,
        session: aiohttp.ClientSession,
        video_id: str,
    ) -> list[YouTubeComment]:
        """GET /youtube/v3/commentThreads — fetch top 5 comments by relevance."""
        url = f"{_YOUTUBE_API_BASE}/commentThreads"
        params: dict[str, str] = {
            "videoId": video_id,
            "key": self._youtube_api_key or "",
            "part": "snippet",
            "maxResults": "5",
            "order": "relevance",
        }

        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                return []

            data = await resp.json()
            comments: list[YouTubeComment] = []

            for item in data.get("items", []):
                snippet = (
                    item.get("snippet", {})
                    .get("topLevelComment", {})
                    .get("snippet", {})
                )
                if not snippet:
                    continue

                comments.append(
                    YouTubeComment(
                        author=snippet.get("authorDisplayName", ""),
                        text=(snippet.get("textDisplay") or "")[:300],
                        likes=int(snippet.get("likeCount", 0)),
                    )
                )

            return comments
