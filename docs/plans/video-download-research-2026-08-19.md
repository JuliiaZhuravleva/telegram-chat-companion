# Short-video download — research & solution options

_Date: 2026-08-19. Status: research draft, feature **deferred** (no plan, no envelope). Source artifact for a future `/plan-fixes` run._

## 1. Problem statement

When a chat member posts a link to a short video (Instagram Reel, YouTube Short, TikTok, tweet, …), the bot downloads the video and replies with it in the chat, so nobody has to leave Telegram. Scope is deliberately **short videos only** — long-form is out.

## 2. Verdict

Feasible with well-trodden tooling. The standard stack is **yt-dlp** (actively maintained, ~1800 supported sites). No LLM involved — zero AI spend; the costs are bandwidth, CPU, and maintenance of a dependency that platforms actively fight. Reliability is per-platform: TikTok/YouTube/Twitter work anonymously today; **Instagram is the weak spot** (login wall — requires cookies from a burner account).

## 3. Telegram-side constraints (verified)

- Bots can **send** video files up to **50 MB** via the standard Bot API — enough for virtually all sub-minute videos (typically 5–20 MB). ([limit discussion](https://github.com/tdlib/telegram-bot-api/issues/583))
- If that ever becomes a ceiling, a [self-hosted Bot API server](https://github.com/tdlib/telegram-bot-api) (one more Docker service) raises it to ~2 GB. **Not needed for the stated scope** — recorded as an escape hatch only.
- aiogram send path: `message.answer_video(FSInputFile(path), supports_streaming=True, width=…, height=…, duration=…)`. Pass dimensions/duration from extractor metadata, otherwise Telegram may render the video as a plain file without the inline player.

## 4. Platform status (as of 2026-08-19)

| Platform | Anonymous? | Status | Notes |
|----------|-----------|--------|-------|
| TikTok | yes | works, breaks periodically | Anti-bot challenge requires browser impersonation → install `yt-dlp[default,curl-cffi]`. Extractor broke 2026-08-04 ([#17403](https://github.com/yt-dlp/yt-dlp/issues/17403)), fixed within days ([#17452](https://github.com/yt-dlp/yt-dlp/pull/17452)). Expect a few-day outage every month or two until the next fix; nightly channel sometimes needed. |
| YouTube (Shorts) | mostly | works, intermittent bot-detection | "Sign in to confirm you're not a bot" / [PO Token system](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide). Blocking targets **datacenter IP ranges** most aggressively — egress IP reputation of the host decides how much mitigation is needed. If detection starts firing, [bgutil-ytdlp-pot-provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider) (sidecar container + yt-dlp plugin) is the established fix. |
| Instagram (Reels) | **no** | login wall | Anonymous requests fail with "Requested content is not available, rate-limit reached or login required" ([#11166](https://github.com/yt-dlp/yt-dlp/issues/11166), still current: [#14241](https://github.com/yt-dlp/yt-dlp/issues/14241)). Working recipe: pass cookies of a logged-in session (`--cookies` file). Requires a **burner account** (Instagram bans accounts exhibiting scraping patterns); cookies expire → highest-maintenance platform by far. |
| Twitter/X, VK, Reddit | yes | stable | Work anonymously via yt-dlp; come nearly for free. |

## 5. Solution options

### Option A — yt-dlp directly (recommended)

Add `yt-dlp[default,curl-cffi]` to the image dependencies plus `ffmpeg` (needed to merge separate video+audio tracks). Invoke as a **subprocess with a timeout**, not as an in-process library: isolates extractor crashes from the event loop and makes kill/timeout trivial.

Flow per link:
1. `yt-dlp --dump-json` (metadata only, no download) → gate on **duration** (configurable cap, e.g. 3–5 min) and reject live/unavailable content.
2. Download with a format selector that bounds size under the Bot API limit (`b[filesize_approx<45M]`-style, with fallback chain) into a temp dir.
3. Send via `answer_video` as a reply to the triggering message; clean up the temp file in `finally`.

Maintenance contract: **yt-dlp is the one dependency that rots** — platforms change, extractors chase. It needs regular bumps (upstream releases ~monthly); a pinned, never-updated yt-dlp silently loses TikTok/YouTube within months.

### Option B — self-hosted [cobalt](https://github.com/imputnet/cobalt)

Open-source "link → file" API service, 20+ platforms, runs as a container; the bot would call it over HTTP ([API docs](https://github.com/imputnet/cobalt/blob/main/docs/api.md)). The public cobalt.tools instance is blocked by YouTube; a self-hosted instance works. Honest assessment: one more production service, it hits the **same walls** (Instagram login, YouTube detection) as yt-dlp, and its extractor coverage is smaller. Only attractive if we later want the download capability shared by multiple consumers.

### Option C — paid scraper APIs

Third-party resolver APIs (RapidAPI-style, per-platform). Only worth it if reliable Instagram without cookie babysitting becomes a hard requirement. Ongoing cost, external dependency, ToS exposure shifts to the vendor.

## 6. Fit with this codebase

- **Handler**: new router filtering on URL entities + a domain regex, placed before the AI pipeline (it's a separate route, no LLM). Gate through a per-chat module toggle following the settings conventions: **nullable `chat_settings` column, no DEFAULT** (three-layer merge reads NULL as "not overridden" — see migrations 014/015 precedent).
- **Concurrency/abuse**: at most one download in flight per chat; reuse the existing cooldown so ten links pasted at once don't become ten parallel downloads.
- **Degradation**: extraction failures must fail **silently** (or with a reaction at most) — never error spam in the chat. This is the one feature whose uptime depends on an external arms race; design for "quietly skip", not "explain".
- **Docker**: `config/default.yml` is COPY'd at build time and so is src — a yt-dlp bump is an image rebuild, same as any code change.

## 7. Risks

1. **ToS**: downloading violates YouTube/Instagram/TikTok terms. Common gray zone for private bots (dozens of public Telegram bots do exactly this), but the feature lives "as long as platforms allow".
2. **Fragility**: extractor breakage is routine (see TikTok row above). Budget for it in expectations, not just code.
3. **Instagram account burn**: if Instagram is in scope, only a burner account, and assume it will eventually be banned.

## 8. Recommended slicing

- **Slice 1**: TikTok + YouTube Shorts + Twitter/VK (all anonymous today), duration gate, size-bounded format selection, per-chat toggle default-off, silent-skip degradation.
- **Slice 2**: Instagram via burner-account cookies — separate decision (account provisioning + cookie refresh runbook), only after Slice 1 proves the plumbing.
- **Non-goals**: long-form video, audio extraction, local Bot API server (recorded above as escape hatch).
