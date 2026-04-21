"""Admin panel repository — stats, unauthorized attempts, sticker sessions."""

from __future__ import annotations

import contextlib
import json
from datetime import timedelta
from typing import Any

import asyncpg
import structlog

logger = structlog.get_logger(__name__)


class AdminRepository:
    """Database operations for the admin panel."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # -- Unauthorized attempts --

    async def log_unauthorized(
        self,
        *,
        chat_id: int,
        chat_title: str | None = None,
        chat_type: str | None = None,
        user_id: int | None = None,
        user_first_name: str | None = None,
        user_username: str | None = None,
        message_text: str | None = None,
    ) -> int:
        """Record an unauthorized access attempt. Returns the attempt ID."""
        row = await self._pool.fetchrow(
            """
            INSERT INTO unauthorized_attempts
                (chat_id, chat_title, chat_type, user_id,
                 user_first_name, user_username, message_text)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id
            """,
            chat_id,
            chat_title,
            chat_type,
            user_id,
            user_first_name,
            user_username,
            (message_text or "")[:200] if message_text else None,
        )
        return int(row["id"])

    async def get_pending_attempts(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """Get pending unauthorized attempts (newest first)."""
        rows = await self._pool.fetch(
            """
            SELECT id, chat_id, chat_title, chat_type,
                   user_id, user_first_name, user_username,
                   message_text, created_at
            FROM unauthorized_attempts
            WHERE status = 'pending'
            ORDER BY created_at DESC
            LIMIT $1
            """,
            limit,
        )
        return [dict(r) for r in rows]

    _VALID_STATUSES = frozenset({"pending", "approved", "rejected"})

    async def update_attempt_status(self, attempt_id: int, status: str) -> dict[str, Any] | None:
        """Update attempt status (approved/rejected). Returns the attempt."""
        if status not in self._VALID_STATUSES:
            raise ValueError(f"Invalid attempt status: {status!r}")
        row = await self._pool.fetchrow(
            """
            UPDATE unauthorized_attempts
            SET status = $2
            WHERE id = $1
            RETURNING *
            """,
            attempt_id,
            status,
        )
        return dict(row) if row else None

    async def approve_all_for_chat(self, chat_id: int) -> int:
        """Mark all pending attempts for a chat as approved. Returns count."""
        result = await self._pool.execute(
            """
            UPDATE unauthorized_attempts
            SET status = 'approved'
            WHERE chat_id = $1 AND status = 'pending'
            """,
            chat_id,
        )
        # asyncpg execute returns "UPDATE N" string
        return int(result.split()[-1]) if result else 0

    # -- Statistics --

    async def get_message_count(self, interval: timedelta) -> int:
        """Count messages within an interval."""
        return (
            await self._pool.fetchval(
                """
            SELECT COUNT(*) FROM chat_messages
            WHERE created_at > NOW() - $1::interval
            """,
                interval,
            )
            or 0
        )

    async def get_response_count(self, interval: timedelta) -> int:
        """Count bot responses within an interval."""
        return (
            await self._pool.fetchval(
                """
            SELECT COUNT(*) FROM response_log
            WHERE created_at > NOW() - $1::interval
            """,
                interval,
            )
            or 0
        )

    async def get_unauth_count(self, interval: timedelta) -> int:
        """Count unauthorized attempts within an interval."""
        return (
            await self._pool.fetchval(
                """
            SELECT COUNT(*) FROM unauthorized_attempts
            WHERE created_at > NOW() - $1::interval
            """,
                interval,
            )
            or 0
        )

    async def get_active_chats_count(self, interval: timedelta) -> int:
        """Count distinct active chats within an interval."""
        return (
            await self._pool.fetchval(
                """
            SELECT COUNT(DISTINCT chat_id) FROM chat_messages
            WHERE created_at > NOW() - $1::interval
            """,
                interval,
            )
            or 0
        )

    async def get_enabled_chats_count(self) -> int:
        """Count chats in whitelist."""
        return (
            await self._pool.fetchval(
                "SELECT COUNT(*) FROM chat_settings WHERE enabled = true",
            )
            or 0
        )

    # -- Admin sticker session --

    async def create_sticker_session(self, admin_user_id: int) -> None:
        """Create or replace a sticker wizard session."""
        await self._pool.execute(
            """
            INSERT INTO admin_sticker_session (admin_user_id)
            VALUES ($1)
            ON CONFLICT (admin_user_id) DO UPDATE SET started_at = NOW()
            """,
            admin_user_id,
        )

    async def delete_sticker_session(self, admin_user_id: int) -> None:
        """Delete a sticker wizard session."""
        await self._pool.execute(
            "DELETE FROM admin_sticker_session WHERE admin_user_id = $1",
            admin_user_id,
        )

    async def has_sticker_session(self, admin_user_id: int) -> bool:
        """Check if a sticker wizard session exists."""
        return bool(
            await self._pool.fetchval(
                "SELECT 1 FROM admin_sticker_session WHERE admin_user_id = $1",
                admin_user_id,
            )
        )

    # -- Whitelist management --

    async def get_enabled_chats_page(
        self, page: int, per_page: int = 5
    ) -> tuple[list[dict[str, Any]], int]:
        """Paginated enabled chats with title/type. Returns (chats, total)."""
        total = (
            await self._pool.fetchval(
                "SELECT COUNT(*) FROM chat_settings WHERE enabled = true",
            )
            or 0
        )
        rows = await self._pool.fetch(
            """
            SELECT chat_id, chat_title, chat_type
            FROM chat_settings
            WHERE enabled = true
            ORDER BY chat_title NULLS LAST, chat_id
            LIMIT $1 OFFSET $2
            """,
            per_page,
            page * per_page,
        )
        return [dict(r) for r in rows], int(total)

    async def get_pending_attempts_page(
        self, page: int, per_page: int = 5
    ) -> tuple[list[dict[str, Any]], int]:
        """Paginated pending attempts (newest first). Returns (attempts, total).

        Excludes attempts for chats that are already whitelisted (enabled).
        """
        _where = """
            ua.status = 'pending'
            AND NOT EXISTS (
                SELECT 1 FROM chat_settings cs
                WHERE cs.chat_id = ua.chat_id AND cs.enabled = true
            )
        """
        total = (
            await self._pool.fetchval(
                f"SELECT COUNT(*) FROM unauthorized_attempts ua WHERE {_where}",  # noqa: S608
            )
            or 0
        )
        rows = await self._pool.fetch(
            f"""
            SELECT ua.id, ua.chat_id, ua.chat_title, ua.chat_type,
                   ua.user_id, ua.user_first_name, ua.user_username,
                   ua.message_text, ua.created_at
            FROM unauthorized_attempts ua
            WHERE {_where}
            ORDER BY ua.created_at DESC
            LIMIT $1 OFFSET $2
            """,  # noqa: S608
            per_page,
            page * per_page,
        )
        return [dict(r) for r in rows], int(total)

    async def get_rejected_attempts_page(
        self, page: int, per_page: int = 5
    ) -> tuple[list[dict[str, Any]], int]:
        """Paginated rejected attempts (newest first). Returns (attempts, total).

        Excludes rejected attempts for chats that are now enabled (stale).
        """
        _where = """
            ua.status = 'rejected'
            AND NOT EXISTS (
                SELECT 1 FROM chat_settings cs
                WHERE cs.chat_id = ua.chat_id AND cs.enabled = true
            )
        """
        total = (
            await self._pool.fetchval(
                f"SELECT COUNT(*) FROM unauthorized_attempts ua WHERE {_where}",  # noqa: S608
            )
            or 0
        )
        rows = await self._pool.fetch(
            f"""
            SELECT ua.id, ua.chat_id, ua.chat_title, ua.chat_type,
                   ua.user_id, ua.user_first_name, ua.user_username,
                   ua.message_text, ua.created_at
            FROM unauthorized_attempts ua
            WHERE {_where}
            ORDER BY ua.created_at DESC
            LIMIT $1 OFFSET $2
            """,  # noqa: S608
            per_page,
            page * per_page,
        )
        return [dict(r) for r in rows], int(total)

    async def has_rejected_attempt(self, chat_id: int) -> bool:
        """True if the chat has any attempt with status='rejected'.

        Used by access-control middleware to suppress notifications for
        already-rejected chats ("real blacklist" semantics). Admin must
        explicitly restore or delete the rejection to re-enable notifications.
        """
        result = await self._pool.fetchval(
            """
            SELECT EXISTS(
                SELECT 1 FROM unauthorized_attempts
                WHERE chat_id = $1 AND status = 'rejected'
            )
            """,
            chat_id,
        )
        return bool(result)

    async def delete_attempt(self, attempt_id: int) -> bool:
        """Hard-delete an unauthorized attempt. Returns True if deleted."""
        result = await self._pool.execute(
            "DELETE FROM unauthorized_attempts WHERE id = $1",
            attempt_id,
        )
        return result.endswith(" 1") if result else False

    async def get_attempt(self, attempt_id: int) -> dict[str, Any] | None:
        """Get single unauthorized attempt by ID."""
        row = await self._pool.fetchrow(
            "SELECT * FROM unauthorized_attempts WHERE id = $1",
            attempt_id,
        )
        return dict(row) if row else None

    # -- Admin language --

    async def get_admin_language(self, bot_config_repo: Any) -> str:
        """Get admin panel language from bot_config.admin_settings."""
        raw = await bot_config_repo.get("admin_settings")
        if raw:
            try:
                data = json.loads(raw) if isinstance(raw, str) else raw
                return str(data.get("lang", "ru"))
            except (json.JSONDecodeError, AttributeError):
                pass
        return "ru"

    # -- Health status --

    async def get_latest_health_check(self) -> dict[str, Any] | None:
        """Get the most recent health check result (for admin panel)."""
        row = await self._pool.fetchrow(
            "SELECT * FROM health_log ORDER BY checked_at DESC LIMIT 1",
        )
        return dict(row) if row else None

    async def set_admin_language(self, bot_config_repo: Any, lang: str) -> None:
        """Save admin panel language to bot_config.admin_settings."""
        raw = await bot_config_repo.get("admin_settings")
        data: dict[str, Any] = {}
        if raw:
            with contextlib.suppress(json.JSONDecodeError, AttributeError):
                data = json.loads(raw) if isinstance(raw, str) else raw
        data["lang"] = lang
        await bot_config_repo.set("admin_settings", json.dumps(data))

    # -- Notification settings --

    _NOTIFICATION_DEFAULTS: dict[str, Any] = {
        "sticker": "on",
        "unauthorized": True,
        "jailbreak": True,
        "blacklist": True,
        "ai_fallback": True,
    }

    async def get_notification_settings(self, bot_config_repo: Any) -> dict[str, Any]:
        """Get admin notification settings with defaults for missing keys."""
        raw = await bot_config_repo.get("admin_settings")
        notifications: dict[str, Any] = {}
        if raw:
            with contextlib.suppress(json.JSONDecodeError, AttributeError):
                data = json.loads(raw) if isinstance(raw, str) else raw
                notifications = data.get("notifications") or {}
        return {**self._NOTIFICATION_DEFAULTS, **notifications}

    async def set_notification_setting(self, bot_config_repo: Any, key: str, value: Any) -> None:
        """Update a single notification setting, preserving other data."""
        raw = await bot_config_repo.get("admin_settings")
        data: dict[str, Any] = {}
        if raw:
            with contextlib.suppress(json.JSONDecodeError, AttributeError):
                data = json.loads(raw) if isinstance(raw, str) else raw
        notifications = data.get("notifications") or {}
        notifications[key] = value
        data["notifications"] = notifications
        await bot_config_repo.set("admin_settings", json.dumps(data))
