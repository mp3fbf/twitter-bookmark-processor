---
title: "X API OAuth Token Silently Expired — 11 Days Undetected"
date: 2026-03-16
category: integration-issues
severity: medium
component: twitter-bookmark-processor
tags: [oauth, twitter-api, monitoring, launchd, data-quality, backfill]
symptoms:
  - "435 packages (36%) missing author_username"
  - "49 threads not expanded"
  - "Pipeline processing normally with zero errors in logs"
  - "No alerts, no health check failures"
root_cause: "Passive token refresh architecture (token only refreshes when daemon calls get_valid_token()) combined with disabled launchd daemon and zero monitoring — silent degradation with no observability"
resolution_time: "11 days undetected (2026-03-05 to 2026-03-16)"
files_modified:
  - src/insight/capture.py
  - src/insight/pipeline.py
  - src/main.py
  - deploy/setup-launchd.sh
files_created:
  - scripts/keepalive_token.py
  - scripts/backfill_authors.py
  - scripts/enrich_via_oauth.py
  - deploy/com.mp3fbf.twitter-keepalive.plist
related_commits:
  - "b0306f9 — feat: X API auth resilience + degradation detection + backfill scripts"
  - "b6d467e — fix: rate limit handling in enrich_via_oauth thread expansion"
---

# X API OAuth Token Silently Expired — 11 Days Undetected

## Problem

On 2026-03-05, the X API OAuth access token expired. Nobody noticed for 11 days because:

1. **Daemon disabled** — launchd jobs had been moved to `~/.config/launchd-disabled/`
2. **Passive refresh** — token only refreshes when `get_valid_token()` is called, which only happens inside the daemon
3. **Zero monitoring** — no health check, no alert, no quality metric
4. **Silent degradation** — pipeline processes backlog normally but without enrichment (no threads, no author, no media)

**Impact:** 435 packages (36%) without `author_username`, 49 threads not expanded, data quality degraded with zero alerts.

The refresh token was still valid (6-month lifetime, expires ~September 2026). Recovery was possible with a single refresh call.

## Root Cause

The auth architecture had a single point of failure: token refresh was **passive and coupled to the daemon**. When the daemon stopped, the token expired and nothing noticed. The pipeline's error handling was designed for per-bookmark resilience (a dead link shouldn't kill the whole bookmark), but this same pattern made auth failure invisible — exceptions were caught, logged as warnings, and the pipeline continued with degraded data.

```python
# The silent failure pattern in capture.py (before fix):
results = await asyncio.gather(*tasks.values(), return_exceptions=True)
if isinstance(result_map["thread"], Exception):
    logger.warning("Thread expansion failed: %s", result_map["thread"])
    # thread_tweets stays empty — pipeline continues
```

## Solution

Three layers of defense, plus data recovery.

### Layer 1 — Token Keepalive (Independent of Daemon)

**File:** `scripts/keepalive_token.py`

Standalone script running every 12 hours via launchd. Two-step validation:

1. `get_valid_token()` — auto-refreshes if expired
2. Canary `GET /2/users/me` — proves the token works end-to-end

On failure: Telegram alert + health log to `data/token_health.json` (keeps last 50 checks).

```python
async def keepalive(client_id: str) -> bool:
    auth = XApiAuth(client_id=client_id, token_file=TOKEN_FILE)
    token = await auth.get_valid_token()
    username = await validate_token(token)
    if username:
        save_health("healthy", username=username)
        return True
    # Force refresh + retry
    await auth.refresh_tokens()
    token = await auth.get_valid_token()
    username = await validate_token(token)
    if not username:
        notify("X API token invalid after refresh.", "error")
        save_health("invalid", error="Canary failed after refresh")
        return False
    return True
```

**Deploy:** `deploy/com.mp3fbf.twitter-keepalive.plist` — `StartInterval: 43200` (12h), `RunAtLoad: true`.

### Layer 2 — Auth Smoke Test on Startup

**File:** `src/main.py` — in `run_insight_daemon()`, `_run_insight()`, `_run_capture_only()`

On every startup, tests whether the token actually works — not just that the file exists. If expired, tries explicit refresh. If refresh fails, sends Telegram alert and continues without auth.

**Key change:** Previously, auth failure silently set `x_api_auth = None`. Now it's explicit:

```python
try:
    token = await auth.get_valid_token()
    x_api_auth = auth
except RuntimeError:
    try:
        await auth.refresh_tokens()
        x_api_auth = auth
    except Exception as refresh_err:
        _notify(f"X API auth failed on startup: {refresh_err}.", "error")
        # Continue without auth — graceful degradation
```

### Layer 3 — Per-Cycle Health Check

**Files:** `src/insight/capture.py`, `src/insight/pipeline.py`

`ContentCapture._auth_degraded` flag, tested once per pipeline cycle (not per bookmark). `InsightPipeline.check_auth_health()` wraps the check and sends Telegram alert.

```python
# capture.py
async def check_auth_health(self) -> bool:
    if not self._x_api_auth:
        return True  # No auth configured — not degraded, just absent
    try:
        await self._x_api_auth.get_valid_token()
        self._auth_degraded = False
        return True
    except Exception:
        self._auth_degraded = True
        return False

# pipeline.py — called at start of each daemon cycle
async def check_auth_health(self) -> bool:
    healthy = await self._capture.check_auth_health()
    if not healthy:
        notify("X API auth degraded — run keepalive_token.py to fix.", "error")
    return healthy
```

### Data Recovery

#### Pass 1 — fxtwitter backfill (`scripts/backfill_authors.py`)

Free API, no OAuth needed, requires `User-Agent` header. Fills `author_username`, `author_name`, `tweet_text`.

**Result: 435/435 recovered, 100% success rate.**

Limitations: does not return `conversation_id`, `media_keys`, or thread tweets.

#### Pass 2 — OAuth re-enrichment (`scripts/enrich_via_oauth.py`)

Batch `GET /2/tweets` (100 IDs per request) for `conversation_id`, `author_id`, `media_keys`. Thread expansion via `search/recent` with rate limit protection: 5s delay between searches, 429 retry with exponential backoff.

**Result: 886 packages enriched with metadata.**

Thread expansion yielded 0 results — all bookmarks were older than the 7-day `search/recent` window.

## Alert Matrix

| Event | Alert | Source |
|-------|-------|--------|
| Keepalive refresh fails | Telegram `error` | `keepalive_token.py` |
| Canary fails after refresh | Telegram `error` | `keepalive_token.py` |
| Startup smoke test + refresh fails | Telegram `error` | `main.py` |
| Per-cycle health check degraded | Telegram `error` | `pipeline.check_auth_health()` |

## Prevention — Remaining Gaps

The current mitigations address token expiry. These gaps remain:

### 1. Daemon Liveness Detection

The keepalive proves the token is alive, not that the daemon is running. A heartbeat file written per cycle + external watchdog cron would catch disabled-daemon scenarios directly.

### 2. Data Quality Metrics

When auth degrades, `ContentPackage` objects are still created with empty enrichment. Tracking `enrichment_rate` per cycle (packages with threads / total) would surface degradation even if auth checks fail silently.

### 3. Persistent Degradation State

`_auth_degraded` is in-memory. If the daemon restarts while tokens are broken, the flag resets. Writing degradation state to `data/auth_status.json` would survive restarts.

### 4. 429 as Degradation Signal

Rate limit responses (429) should set `_auth_degraded = True` to stop hammering the API for the rest of the cycle, rather than just logging a warning.

## Related Files

- `src/sources/x_api_auth.py` — Core OAuth 2.0 PKCE module
- `SPEC.md` — Auth flow spec, graceful degradation notes
- `deploy/run-processor.sh` — Secret loading from macOS Keychain
- `deploy/setup-launchd.sh` — Installs all launchd jobs including keepalive
- `data/x_api_tokens.json` — Token storage (access_token, refresh_token, expires_at)
- `data/token_health.json` — Keepalive health log (last 50 checks)

## Verification

```bash
# Token alive
python3 scripts/keepalive_token.py --status

# Backfill complete
python3 scripts/backfill_authors.py status
# Expected: Missing author: 0 (0%)

# Enrichment complete
python3 scripts/enrich_via_oauth.py status
```
