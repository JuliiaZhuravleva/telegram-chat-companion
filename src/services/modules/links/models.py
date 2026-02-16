"""Data models for link extraction."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class VideoLink:
    """A detected video link in a message."""

    url: str
    platform: str  # "youtube" | "tiktok" | "instagram"
    video_id: str | None = None  # YouTube 11-char ID


@dataclass(frozen=True)
class YouTubeComment:
    """A top comment from a YouTube video."""

    author: str
    text: str
    likes: int


@dataclass(frozen=True)
class VideoMetadata:
    """Enriched metadata for a YouTube video."""

    title: str
    channel: str
    views: str  # Human-readable ("1.5M")
    duration: str | None = None  # Human-readable ("4:33")
    description: str = ""  # Truncated to 500 chars
    tags: list[str] = field(default_factory=list)  # Up to 5
    top_comments: list[YouTubeComment] = field(default_factory=list)  # Up to 5


@dataclass(frozen=True)
class LinkContext:
    """YouTube link context for prompt injection.

    Only created when there is YouTube metadata to inject.
    For non-YouTube links (TikTok, Instagram), no LinkContext is created —
    the AI sees the raw URL in the message text naturally.
    """

    youtube_links: list[VideoLink] = field(default_factory=list)
    metadata: dict[str, VideoMetadata] = field(default_factory=dict)  # Keyed by video_id
