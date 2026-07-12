from __future__ import annotations

"""
Shared Reddit HTTP client — single source of truth for Reddit access (issue #78).

Root cause of #78: unauthenticated requests to www.reddit.com/*.json and .rss
are aggressively rate-limited (429) and blocked (403), especially from
datacenter IPs. The generic/placeholder User-Agent the collectors used made it
worse — Reddit's API rules require a unique, descriptive UA. The supported fix
is OAuth2, so this module centralises ALL Reddit HTTP here and both collectors
(track AI `reddit_collector` + track Drama `reddit_drama_collector`) go through
it, instead of each hand-rolling urllib with its own UA.

Behaviour:
- If REDDIT_CLIENT_ID/SECRET are configured: app-only OAuth2 (client_credentials
  grant) → requests hit https://oauth.reddit.com (documented ~100 req/min),
  which bypasses the block. The bearer token is cached until ~1 min before it
  expires and refreshed transparently.
- If NOT configured: fall back to unauthenticated https://www.reddit.com/<path>.json
  with the compliant UA (best-effort — may still be throttled, but the pipeline
  keeps running and logs a clear one-time hint to set up OAuth).
- A single module-level rate limiter spaces out every call (token requests
  included) so a burst across subreddits can't trip the throttle.
- 429 honours the Retry-After header (capped so a bad value can't stall cron);
  403 is treated as a hard block and NOT retried (retrying a block wastes the
  whole cron window); 401 refreshes the token once; 5xx/network back off + retry.

Why JSON-only (no RSS): oauth.reddit.com serves JSON, not .rss, and the JSON
listing endpoints already carry score/selftext/over_18 — the very fields the
drama collector previously made a second per-post call to fetch. Moving to JSON
both unblocks us and collapses the old 1-RSS-plus-N-detail-calls pattern into
one request per subreddit.
"""

import base64
import json
import logging
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

logger = logging.getLogger(__name__)

_OAUTH_BASE = "https://oauth.reddit.com"
_PUBLIC_BASE = "https://www.reddit.com"
_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"

# Token cache + rate-limiter state, guarded by a lock so a threaded caller
# can't race the refresh or double up requests.
_lock = threading.Lock()
_token: dict = {"access_token": None, "expires_at": 0.0}
_last_call_at = 0.0
_warned_no_oauth = False


def has_oauth_credentials() -> bool:
    """True when both a client id and secret are configured."""
    return bool(config.REDDIT_CLIENT_ID and config.REDDIT_CLIENT_SECRET)


def reset_state() -> None:
    """Clear the cached token + rate-limiter clock (test helper)."""
    global _last_call_at, _warned_no_oauth
    with _lock:
        _token["access_token"] = None
        _token["expires_at"] = 0.0
        _last_call_at = 0.0
        _warned_no_oauth = False


def _throttle() -> None:
    """Sleep so consecutive Reddit calls are >= REDDIT_MIN_INTERVAL apart."""
    global _last_call_at
    with _lock:
        now = time.monotonic()
        wait = config.REDDIT_MIN_INTERVAL - (now - _last_call_at)
        if wait > 0:
            time.sleep(wait)
        _last_call_at = time.monotonic()


def _parse_retry_after(err: HTTPError) -> float | None:
    """Seconds to wait from a 429's Retry-After header, or None if absent/odd.

    Reddit sends an integer number of seconds. An HTTP-date value (rare here) is
    ignored rather than parsed — we fall back to exponential backoff for it.
    The value is capped so a pathological Retry-After can't park cron for hours.
    """
    try:
        raw = err.headers.get("Retry-After") if err.headers else None
    except Exception:
        return None
    if not raw:
        return None
    try:
        secs = float(raw)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(secs, config.REDDIT_RETRY_AFTER_CAP))


def _fetch_access_token() -> str | None:
    """Return a cached-or-fresh app-only OAuth bearer token, or None on failure."""
    with _lock:
        if _token["access_token"] and time.monotonic() < _token["expires_at"]:
            return _token["access_token"]

    creds = f"{config.REDDIT_CLIENT_ID}:{config.REDDIT_CLIENT_SECRET}".encode("utf-8")
    basic = base64.b64encode(creds).decode("ascii")
    body = urlencode({"grant_type": "client_credentials"}).encode("ascii")
    req = Request(_TOKEN_URL, data=body, method="POST")
    req.add_header("Authorization", f"Basic {basic}")
    req.add_header("User-Agent", config.REDDIT_USER_AGENT)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    _throttle()
    try:
        with urlopen(req, timeout=config.REDDIT_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except HTTPError as e:
        # 401 here means the client id/secret themselves are wrong — retrying
        # won't help, so surface it clearly.
        logger.error("Reddit OAuth token request failed: HTTP %s %s", e.code, e.reason)
        return None
    except Exception as e:
        logger.error("Reddit OAuth token request failed: %s", e)
        return None

    access_token = data.get("access_token")
    if not access_token:
        logger.error("Reddit OAuth response had no access_token: %s", data)
        return None
    expires_in = data.get("expires_in", 3600)
    with _lock:
        _token["access_token"] = access_token
        # Refresh a minute early so an in-flight request never uses a token
        # that expires mid-call.
        _token["expires_at"] = time.monotonic() + max(0.0, float(expires_in) - 60.0)
    logger.info("Obtained Reddit app-only OAuth token (expires in %ss)", expires_in)
    return access_token


def _build_request(path: str, query: str, token: str | None) -> Request:
    if token:
        url = f"{_OAUTH_BASE}{path}"
    else:
        url = f"{_PUBLIC_BASE}{path}.json"
    if query:
        url = f"{url}?{query}"
    req = Request(url)
    req.add_header("User-Agent", config.REDDIT_USER_AGENT)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    return req


def get_json(path: str, params: dict | None = None):
    """GET a Reddit JSON endpoint and return the parsed body (or None on failure).

    Args:
        path: API path beginning with '/', WITHOUT the '.json' suffix, e.g.
            '/r/AskReddit/top' or '/r/ChatGPT/hot'. The suffix is added only on
            the unauthenticated fallback (oauth.reddit.com doesn't use it).
        params: query params, e.g. {"t": "day", "limit": 25}.

    Returns None (not raising) after exhausting retries, so callers keep the
    "one source failing doesn't sink the run" resilience the codebase favours.
    403 is a hard block and returns immediately without burning retries.
    """
    global _warned_no_oauth
    query_params = dict(params or {})
    use_oauth = has_oauth_credentials()
    if use_oauth:
        # Ask Reddit not to HTML-escape unicode in JSON string values.
        query_params.setdefault("raw_json", 1)
    elif not _warned_no_oauth:
        _warned_no_oauth = True
        logger.warning(
            "Reddit OAuth not configured (REDDIT_CLIENT_ID/SECRET empty) — using "
            "unauthenticated www.reddit.com, which Reddit heavily rate-limits/blocks "
            "(issue #78). Create a 'script' app at reddit.com/prefs/apps to fix."
        )
    query = urlencode(query_params)

    last_error = None
    for attempt in range(config.REDDIT_MAX_RETRIES):
        token = _fetch_access_token() if use_oauth else None
        if use_oauth and not token:
            # Couldn't authenticate this cycle; degrade to the public endpoint
            # rather than give up entirely.
            logger.warning("Falling back to unauthenticated Reddit for %s (no token)", path)
            token = None

        req = _build_request(path, query, token)
        _throttle()
        try:
            with urlopen(req, timeout=config.REDDIT_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except HTTPError as e:
            last_error = e
            if e.code == 401 and token:
                # Stale/invalid token — drop it and let the next attempt refetch.
                logger.warning("Reddit 401 on %s — refreshing token and retrying", path)
                with _lock:
                    _token["access_token"] = None
                    _token["expires_at"] = 0.0
                continue
            if e.code == 403:
                hint = "" if use_oauth else " (set up REDDIT_CLIENT_ID/SECRET for OAuth — issue #78)"
                logger.error("Reddit 403 (blocked) on %s — not retrying%s", path, hint)
                return None
            if e.code == 429:
                retry_after = _parse_retry_after(e)
                wait = retry_after if retry_after is not None \
                    else float(config.REDDIT_RETRY_BACKOFF ** (attempt + 1))
                logger.warning(
                    "Reddit 429 (rate-limited) on %s — waiting %.0fs (attempt %d/%d)",
                    path, wait, attempt + 1, config.REDDIT_MAX_RETRIES,
                )
                if attempt < config.REDDIT_MAX_RETRIES - 1:
                    time.sleep(wait)
                continue
            # Other HTTP status (5xx, etc.) — back off and retry.
            logger.warning(
                "Reddit HTTP %s on %s (attempt %d/%d): %s",
                e.code, path, attempt + 1, config.REDDIT_MAX_RETRIES, e.reason,
            )
            if attempt < config.REDDIT_MAX_RETRIES - 1:
                time.sleep(config.REDDIT_RETRY_BACKOFF ** (attempt + 1))
        except (URLError, TimeoutError, json.JSONDecodeError) as e:
            last_error = e
            logger.warning(
                "Reddit request error on %s (attempt %d/%d): %s",
                path, attempt + 1, config.REDDIT_MAX_RETRIES, e,
            )
            if attempt < config.REDDIT_MAX_RETRIES - 1:
                time.sleep(config.REDDIT_RETRY_BACKOFF ** (attempt + 1))

    logger.error(
        "Reddit request to %s failed after %d attempts: %s",
        path, config.REDDIT_MAX_RETRIES, last_error,
    )
    return None
