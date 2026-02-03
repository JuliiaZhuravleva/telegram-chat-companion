"""Tests for src.database.connection — pool creation and teardown (mocked)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.database.connection import close_pool, create_pool


class TestCreatePool:
    """Test create_pool() with mocked asyncpg."""

    @pytest.mark.asyncio
    async def test_creates_pool_and_returns_it(self):
        mock_pool = MagicMock()

        with patch("src.database.connection.asyncpg.create_pool", new_callable=AsyncMock) as mock_create:
            mock_create.return_value = mock_pool
            pool = await create_pool("postgresql://test:test@localhost/db")

        assert pool is mock_pool
        mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_passes_min_max_size(self):
        mock_pool = MagicMock()

        with patch("src.database.connection.asyncpg.create_pool", new_callable=AsyncMock) as mock_create:
            mock_create.return_value = mock_pool
            await create_pool("postgresql://test:test@localhost/db", min_size=2, max_size=10)

        _, kwargs = mock_create.call_args
        assert kwargs["min_size"] == 2
        assert kwargs["max_size"] == 10

    @pytest.mark.asyncio
    async def test_raises_on_none_pool(self):
        with patch("src.database.connection.asyncpg.create_pool", new_callable=AsyncMock) as mock_create:
            mock_create.return_value = None
            with pytest.raises(RuntimeError, match="Failed to create"):
                await create_pool("postgresql://test:test@localhost/db")


class TestClosePool:
    """Test close_pool()."""

    @pytest.mark.asyncio
    async def test_calls_pool_close(self):
        mock_pool = MagicMock()
        mock_pool.close = AsyncMock()

        await close_pool(mock_pool)

        mock_pool.close.assert_awaited_once()
