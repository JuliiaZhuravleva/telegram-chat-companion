"""Formatting utilities for link extraction data."""

from __future__ import annotations

import re

from src.services.modules.links.models import LinkContext
from src.services.text.prompt_sanitizer import sanitize_prompt_content


def parse_iso_duration(iso_duration: str) -> str | None:
    """Convert ISO 8601 duration to human-readable format.

    Examples:
        PT4M33S  -> "4:33"
        PT1H2M3S -> "1:02:03"
        PT30S    -> "0:30"
    """
    match = re.match(
        r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$",
        iso_duration,
    )
    if not match:
        return None

    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)

    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def format_view_count(views: int) -> str:
    """Convert numeric view count to human-readable format.

    Examples:
        1_500_000 -> "1.5M"
        45_200    -> "45.2K"
        999       -> "999"
    """
    if views >= 1_000_000:
        value = views / 1_000_000
        if value == int(value):
            return f"{int(value)}M"
        return f"{value:.1f}M"
    if views >= 1_000:
        value = views / 1_000
        if value == int(value):
            return f"{int(value)}K"
        return f"{value:.1f}K"
    return str(views)


def format_link_context_section(ctx: LinkContext) -> str:
    """Format LinkContext into a system prompt section.

    All YouTube metadata (titles, comments, etc.) is sanitized
    to prevent prompt injection.
    """
    parts: list[str] = []
    parts.append(
        "The user shared a YouTube video link. "
        "Here is the video metadata — use it to make a relevant, "
        "natural comment about the video:"
    )

    for link in ctx.youtube_links:
        video_id = link.video_id
        if not video_id:
            continue
        meta = ctx.metadata.get(video_id)
        if meta is None:
            continue

        safe_title = sanitize_prompt_content(meta.title)
        safe_channel = sanitize_prompt_content(meta.channel)

        info = f"- \"{safe_title}\" by {safe_channel}"
        if meta.duration:
            info += f" ({meta.duration})"
        info += f", {meta.views} views"
        parts.append(info)

        if meta.description:
            safe_desc = sanitize_prompt_content(meta.description[:300])
            parts.append(f"  Description: {safe_desc}")

        if meta.tags:
            safe_tags = [sanitize_prompt_content(t) for t in meta.tags[:5]]
            parts.append(f"  Tags: {', '.join(safe_tags)}")

        if meta.top_comments:
            parts.append("  Top comments:")
            for comment in meta.top_comments[:3]:
                safe_author = sanitize_prompt_content(comment.author)
                safe_text = sanitize_prompt_content(comment.text[:200])
                parts.append(f"    - {safe_author}: \"{safe_text}\" ({comment.likes} likes)")

    return "\n".join(parts)
