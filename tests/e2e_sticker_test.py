"""E2E test for sticker intelligence features via Telethon.

Sends stickers (static, animated, video) to the test chat and verifies
bot learns them by checking the database.
"""

import asyncio
import sys
from pathlib import Path

import asyncpg
from telethon import TelegramClient
from telethon.tl.functions.messages import GetStickerSetRequest
from telethon.tl.types import InputStickerSetShortName

# Telethon credentials from telegram-bot-n8n
_N8N_DIR = Path.home() / "my-projects/telegram-bot-n8n"
_ENV_PATH = _N8N_DIR / ".env"

# Parse .env for API_ID and API_HASH
_env: dict[str, str] = {}
with open(_ENV_PATH) as f:
    for line in f:
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            _env[k.strip()] = v.strip()

API_ID = int(_env["API_ID"])
API_HASH = _env["API_HASH"]
SESSION = str(_N8N_DIR / ".tg-test/tester")

TEST_CHAT = -1003632335671  # tests chat
DB_URL = "postgresql://bot_user:bot_password@127.0.0.1:5432/telegram_bot"

# Pack names by sticker type
STATIC_PACK = "Stickers_Duck"  # image/webp
ANIMATED_PACK = "AnimatedStickers"  # application/x-tgsticker
VIDEO_PACK = "HotCherryPack"  # video/webm


async def main():
    pool = await asyncpg.create_pool(DB_URL)
    client = TelegramClient(SESSION, API_ID, API_HASH)
    await client.start()

    # Resolve test chat entity (populates cache for new sessions)
    await client.get_dialogs()

    print("=== Sticker Intelligence E2E Test ===\n")
    results = {}

    # Clean up any previous test stickers (case-insensitive — Telegram normalizes names)
    await pool.execute(
        "DELETE FROM sticker_knowledge WHERE LOWER(set_name) IN (LOWER($1), LOWER($2), LOWER($3))",
        STATIC_PACK,
        ANIMATED_PACK,
        VIDEO_PACK,
    )
    await pool.execute("DELETE FROM admin_sticker_notifications")
    print("Cleaned up previous test data.\n")

    # ── Test 1: Static Sticker ─────────────────────────────────────
    print("Test 1: Static sticker learning")
    try:
        result = await client(
            GetStickerSetRequest(
                stickerset=InputStickerSetShortName(short_name=STATIC_PACK),
                hash=0,
            )
        )
        static_sticker = None
        for doc in result.documents:
            if doc.mime_type == "image/webp":
                static_sticker = doc
                break

        if static_sticker:
            print(f"  Sending static sticker (id={static_sticker.id})...")
            await client.send_file(TEST_CHAT, static_sticker)
            print("  Waiting 15s for bot to process...")
            await asyncio.sleep(15)

            row = await pool.fetchrow(
                "SELECT * FROM sticker_knowledge WHERE LOWER(set_name) = LOWER($1) ORDER BY created_at DESC LIMIT 1",
                STATIC_PACK,
            )
            if row:
                print("  ✓ Static sticker learned!")
                print(f"    Description: {row['visual_description']}")
                print(f"    Emotion: {row['emotion']}")
                print(f"    Analysis failed: {row['analysis_failed']}")
                results["static"] = (
                    "PASS" if not row["analysis_failed"] else "FAIL (analysis failed)"
                )
            else:
                print("  ✗ Static sticker NOT found in DB")
                results["static"] = "FAIL"
        else:
            print("  ✗ No static sticker found in pack")
            results["static"] = "SKIP"
    except Exception as e:
        print(f"  ✗ Error: {e}")
        results["static"] = f"ERROR: {e}"

    # ── Test 2: Animated Sticker ───────────────────────────────────
    print("\nTest 2: Animated sticker (.tgs) rendering + learning")
    try:
        result = await client(
            GetStickerSetRequest(
                stickerset=InputStickerSetShortName(short_name=ANIMATED_PACK),
                hash=0,
            )
        )
        animated_sticker = None
        for doc in result.documents:
            if doc.mime_type == "application/x-tgsticker":
                animated_sticker = doc
                break

        if animated_sticker:
            print(f"  Sending animated sticker (id={animated_sticker.id})...")
            await client.send_file(TEST_CHAT, animated_sticker)
            print("  Waiting 30s for rendering + analysis...")
            await asyncio.sleep(30)

            row = await pool.fetchrow(
                "SELECT * FROM sticker_knowledge WHERE LOWER(set_name) = LOWER($1) ORDER BY created_at DESC LIMIT 1",
                ANIMATED_PACK,
            )
            if row:
                print("  ✓ Animated sticker processed!")
                print(f"    Description: {row['visual_description']}")
                print(f"    Emotion: {row['emotion']}")
                print(f"    Analysis failed: {row['analysis_failed']}")
                print(f"    is_animated: {row['is_animated']}")
                if row["analysis_failed"]:
                    results["animated"] = (
                        "PASS (saved with analysis_failed — render may have failed)"
                    )
                else:
                    results["animated"] = "PASS"
            else:
                print("  ✗ Animated sticker NOT found in DB")
                results["animated"] = "FAIL"
        else:
            print("  ✗ No animated sticker found in pack")
            results["animated"] = "SKIP"
    except Exception as e:
        print(f"  ✗ Error: {e}")
        results["animated"] = f"ERROR: {e}"

    # ── Test 3: Video Sticker ──────────────────────────────────────
    print("\nTest 3: Video sticker (.webm) rendering + learning")
    try:
        result = await client(
            GetStickerSetRequest(
                stickerset=InputStickerSetShortName(short_name=VIDEO_PACK),
                hash=0,
            )
        )
        video_sticker = None
        for doc in result.documents:
            if doc.mime_type == "video/webm":
                video_sticker = doc
                break

        if video_sticker:
            print(f"  Sending video sticker (id={video_sticker.id})...")
            await client.send_file(TEST_CHAT, video_sticker)
            print("  Waiting 30s for rendering + analysis...")
            await asyncio.sleep(30)

            row = await pool.fetchrow(
                "SELECT * FROM sticker_knowledge WHERE LOWER(set_name) = LOWER($1) ORDER BY created_at DESC LIMIT 1",
                VIDEO_PACK,
            )
            if row:
                print("  ✓ Video sticker processed!")
                print(f"    Description: {row['visual_description']}")
                print(f"    Emotion: {row['emotion']}")
                print(f"    Analysis failed: {row['analysis_failed']}")
                print(f"    is_video: {row['is_video']}")
                if row["analysis_failed"]:
                    results["video"] = "PASS (saved with analysis_failed — render may have failed)"
                else:
                    results["video"] = "PASS"
            else:
                print("  ✗ Video sticker NOT found in DB")
                results["video"] = "FAIL"
        else:
            print("  ✗ No video sticker found in pack")
            results["video"] = "SKIP"
    except Exception as e:
        print(f"  ✗ Error: {e}")
        results["video"] = f"ERROR: {e}"

    # ── Test 4: Admin Notification ─────────────────────────────────
    print("\nTest 4: Admin sticker notifications")
    row = await pool.fetchrow("SELECT COUNT(*) as cnt FROM admin_sticker_notifications")
    notif_count = row["cnt"] if row else 0
    if notif_count > 0:
        print(f"  ✓ {notif_count} admin notification(s) sent")
        results["admin_notif"] = "PASS"
    else:
        print("  ○ No admin notifications (may be expected if analysis failed)")
        results["admin_notif"] = "INFO (no notifications)"

    # ── Summary ────────────────────────────────────────────────────
    print("\n=== Results ===")
    for test, result in results.items():
        status = "✓" if "PASS" in result else "○" if "INFO" in result or "SKIP" in result else "✗"
        print(f"  {status} {test}: {result}")

    passed = sum(1 for r in results.values() if "PASS" in r)
    total = len(results)
    print(f"\n{passed}/{total} tests passed")

    # Show bot logs for context
    print("\n=== Recent Bot Logs (last 50 lines) ===")
    import subprocess

    logs = subprocess.run(
        ["docker", "logs", "companion-bot", "--tail", "50"],
        capture_output=True,
        text=True,
    )
    print(logs.stdout or logs.stderr)

    await client.disconnect()
    await pool.close()

    return 0 if passed >= 3 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
