"""Tests for LinkExtractorService."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.modules.links.extractor import LinkExtractorService


class TestDetectLinks:
    """Test URL detection (pure function, no I/O)."""

    def setup_method(self):
        self.service = LinkExtractorService(youtube_api_key=None)

    def test_youtube_watch_url(self):
        links = self.service.detect_links(
            "Check this https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        )
        assert len(links) == 1
        assert links[0].platform == "youtube"
        assert links[0].video_id == "dQw4w9WgXcQ"

    def test_youtube_short_url(self):
        links = self.service.detect_links("See https://youtu.be/dQw4w9WgXcQ")
        assert len(links) == 1
        assert links[0].platform == "youtube"
        assert links[0].video_id == "dQw4w9WgXcQ"

    def test_youtube_shorts_url(self):
        links = self.service.detect_links(
            "https://youtube.com/shorts/abcdefghijk"
        )
        assert len(links) == 1
        assert links[0].platform == "youtube"
        assert links[0].video_id == "abcdefghijk"

    def test_tiktok_url(self):
        links = self.service.detect_links(
            "https://tiktok.com/@user/video/1234567890"
        )
        assert len(links) == 1
        assert links[0].platform == "tiktok"
        assert links[0].video_id is None

    def test_tiktok_short_url(self):
        links = self.service.detect_links("https://vm.tiktok.com/abc123/")
        assert len(links) == 1
        assert links[0].platform == "tiktok"

    def test_instagram_reel(self):
        links = self.service.detect_links(
            "https://instagram.com/reel/abc123"
        )
        assert len(links) == 1
        assert links[0].platform == "instagram"

    def test_instagram_post(self):
        links = self.service.detect_links("https://instagram.com/p/xyz789")
        assert len(links) == 1
        assert links[0].platform == "instagram"

    def test_no_video_urls(self):
        links = self.service.detect_links("Hello world, no links here!")
        assert len(links) == 0

    def test_non_video_url(self):
        links = self.service.detect_links("https://google.com/search?q=test")
        assert len(links) == 0

    def test_multiple_urls(self):
        text = (
            "Watch these: https://youtu.be/dQw4w9WgXcQ "
            "and https://youtube.com/watch?v=abc12345678"
        )
        links = self.service.detect_links(text)
        assert len(links) == 2
        assert all(l.platform == "youtube" for l in links)

    def test_deduplicates_same_url(self):
        text = "https://youtu.be/dQw4w9WgXcQ https://youtu.be/dQw4w9WgXcQ"
        links = self.service.detect_links(text)
        assert len(links) == 1

    def test_mixed_platforms(self):
        text = (
            "YouTube: https://youtu.be/dQw4w9WgXcQ "
            "TikTok: https://tiktok.com/@user/video/123"
        )
        links = self.service.detect_links(text)
        assert len(links) == 2
        platforms = {l.platform for l in links}
        assert platforms == {"youtube", "tiktok"}


class TestExtract:
    """Test the full extract pipeline."""

    @pytest.mark.asyncio
    async def test_returns_none_for_no_links(self):
        service = LinkExtractorService(youtube_api_key="test-key")
        result = await service.extract("Hello, no links here")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_tiktok_only(self):
        """TikTok links should NOT produce link context."""
        service = LinkExtractorService(youtube_api_key="test-key")
        result = await service.extract("https://tiktok.com/@user/video/123")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_instagram_only(self):
        """Instagram links should NOT produce link context."""
        service = LinkExtractorService(youtube_api_key="test-key")
        result = await service.extract("https://instagram.com/reel/abc123")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_without_api_key(self):
        """No API key → no enrichment → returns None."""
        service = LinkExtractorService(youtube_api_key=None)
        result = await service.extract("https://youtu.be/dQw4w9WgXcQ")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_context_on_youtube_success(self):
        """Successful YouTube API call returns LinkContext."""
        service = LinkExtractorService(youtube_api_key="test-key")

        mock_video_response = MagicMock()
        mock_video_response.status = 200
        mock_video_response.json = AsyncMock(return_value={
            "items": [{
                "snippet": {
                    "title": "Test Video",
                    "channelTitle": "Test Channel",
                    "description": "A great video",
                    "tags": ["tag1", "tag2"],
                },
                "statistics": {"viewCount": "1500000"},
                "contentDetails": {"duration": "PT4M33S"},
            }]
        })
        mock_video_response.__aenter__ = AsyncMock(return_value=mock_video_response)
        mock_video_response.__aexit__ = AsyncMock(return_value=False)

        mock_comments_response = MagicMock()
        mock_comments_response.status = 200
        mock_comments_response.json = AsyncMock(return_value={
            "items": [{
                "snippet": {
                    "topLevelComment": {
                        "snippet": {
                            "authorDisplayName": "Commenter",
                            "textDisplay": "Nice video!",
                            "likeCount": 42,
                        }
                    }
                }
            }]
        })
        mock_comments_response.__aenter__ = AsyncMock(return_value=mock_comments_response)
        mock_comments_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        call_count = 0

        def mock_get(url, params=None):
            nonlocal call_count
            call_count += 1
            if "commentThreads" in url:
                return mock_comments_response
            return mock_video_response

        mock_session.get = mock_get
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            result = await service.extract(
                "Check this out: https://youtu.be/dQw4w9WgXcQ"
            )

        assert result is not None
        assert len(result.youtube_links) == 1
        assert "dQw4w9WgXcQ" in result.metadata
        meta = result.metadata["dQw4w9WgXcQ"]
        assert meta.title == "Test Video"
        assert meta.channel == "Test Channel"
        assert meta.views == "1.5M"
        assert meta.duration == "4:33"
        assert len(meta.top_comments) == 1
        assert meta.top_comments[0].text == "Nice video!"

    @pytest.mark.asyncio
    async def test_returns_none_on_api_failure(self):
        """YouTube API error → returns None (non-blocking)."""
        service = LinkExtractorService(youtube_api_key="test-key")

        mock_response = MagicMock()
        mock_response.status = 403
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            result = await service.extract("https://youtu.be/dQw4w9WgXcQ")

        assert result is None

    @pytest.mark.asyncio
    async def test_comments_optional_on_failure(self):
        """Comments API failure → metadata still returned without comments."""
        service = LinkExtractorService(youtube_api_key="test-key")

        mock_video_response = MagicMock()
        mock_video_response.status = 200
        mock_video_response.json = AsyncMock(return_value={
            "items": [{
                "snippet": {
                    "title": "Video",
                    "channelTitle": "Channel",
                    "description": "",
                },
                "statistics": {"viewCount": "100"},
                "contentDetails": {"duration": "PT1M"},
            }]
        })
        mock_video_response.__aenter__ = AsyncMock(return_value=mock_video_response)
        mock_video_response.__aexit__ = AsyncMock(return_value=False)

        mock_comments_response = MagicMock()
        mock_comments_response.status = 403  # Comments disabled
        mock_comments_response.__aenter__ = AsyncMock(return_value=mock_comments_response)
        mock_comments_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()

        def mock_get(url, params=None):
            if "commentThreads" in url:
                return mock_comments_response
            return mock_video_response

        mock_session.get = mock_get
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            result = await service.extract("https://youtu.be/dQw4w9WgXcQ")

        assert result is not None
        meta = result.metadata["dQw4w9WgXcQ"]
        assert meta.title == "Video"
        assert meta.top_comments == []
