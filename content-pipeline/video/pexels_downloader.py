from __future__ import annotations

"""
Pexels Video Downloader — Tự động tải background video miễn phí từ Pexels.

Pexels API miễn phí, chỉ cần API key (đăng ký tại pexels.com/api).

Features:
- Tìm background phù hợp theo keywords của bài viết
- Cache local với tên semantic (tránh tải lại)
- Fallback: article keywords → generic tech queries → cached files → default path
"""

import hashlib
import json
import logging
import os
import random
import subprocess
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

logger = logging.getLogger(__name__)

PEXELS_API_BASE = "https://api.pexels.com"

# NotoSans variable font for Vietnamese subtitle rendering (Google Fonts)
# NotoSans-Bold.ttf was replaced by a variable font in the google/fonts repo
_NOTO_FONT_URL = (
    "https://raw.githubusercontent.com/google/fonts/main/ofl/notosans/NotoSans%5Bwdth%2Cwght%5D.ttf"
)
_FONT_PATH = os.path.join(os.path.dirname(__file__), "assets", "fonts", "NotoSans-Bold.ttf")

# Search queries cho background video phù hợp với kênh AI/tech
SEARCH_QUERIES = [
    "abstract technology",
    "futuristic particles",
    "digital network",
    "blue gradient motion",
    "minimal abstract loop",
]

CACHE_DIR = os.path.join(os.path.dirname(__file__), "assets", "backgrounds", "cache")

# Tracks the last Pexels API error type so callers can stop retrying on auth failures.
# Set to "auth" on HTTP 401/403, reset to None on success.
_last_pexels_error: str | None = None


def get_video_duration(video_path: str) -> float:
    """Return the duration of a video file in seconds using ffprobe.

    Returns 0.0 if ffprobe is unavailable or the file cannot be probed.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                video_path,
            ],
            capture_output=True, text=True, timeout=10,
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def _choose_variety(ranked: list[str], avoid: set[str], top_k: int) -> str:
    """Pick from the *top_k* best-ranked clips, avoiding recently-used ones (pure).

    Keeps the duration-match guarantee (only ever picks among the closest-fit
    clips) while rotating across them so same-length videos don't all reuse the
    single closest clip. Falls back to the full top_k slice when every candidate
    in it was used recently. top_k=1 reproduces the old deterministic pick.
    """
    k = max(1, min(top_k, len(ranked)))
    top = ranked[:k]
    preferred = [p for p in top if os.path.basename(p) not in avoid]
    return random.choice(preferred or top)


def _select_best_background(paths: list[str], audio_duration: float,
                            avoid: set[str] | None = None,
                            top_k: int = 1) -> str:
    """Select a background video whose duration best matches the audio.

    Strategy:
    - Prefer the video whose duration is closest to *audio_duration* to
      minimise looping (for shorter videos) or wasted footage (for longer ones).
    - When audio_duration is unknown (≤ 0) fall back to random selection.
    - With *top_k* > 1, choose randomly among the top_k closest clips while
      avoiding the *avoid* set (recently-used basenames) — this adds variety
      without straying from a good duration fit. The default (top_k=1, no avoid)
      preserves the legacy deterministic closest-fit pick.

    Args:
        paths: Non-empty list of candidate video file paths.
        audio_duration: Target duration in seconds (0 means unknown).
        avoid: Basenames to skip when an equally-good alternative exists.
        top_k: How many of the closest clips to randomise among.

    Returns:
        The chosen video path.
    """
    avoid = avoid or set()

    if len(paths) == 1:
        logger.info("Background selected (only candidate): %s",
                    os.path.basename(paths[0]))
        return paths[0]

    if audio_duration <= 0:
        pool = [p for p in paths if os.path.basename(p) not in avoid] or paths
        chosen = random.choice(pool)
        logger.info("Background selected (random, %d candidates): %s",
                    len(paths), os.path.basename(chosen))
        return chosen

    scored: list[tuple[float, str]] = []
    for path in paths:
        dur = get_video_duration(path)
        if dur <= 0:
            continue  # ffprobe unavailable or corrupt file — skip
        diff = abs(dur - audio_duration)
        scored.append((diff, path))
        logger.debug("Candidate %s: duration=%.1fs, diff=%.1fs",
                     os.path.basename(path), dur, diff)

    if not scored:
        chosen = random.choice(paths)
        logger.info("Background selected (no probeable durations, random): %s",
                    os.path.basename(chosen))
        return chosen

    scored.sort(key=lambda x: x[0])
    ranked = [p for _diff, p in scored]
    chosen = _choose_variety(ranked, avoid, top_k)
    logger.info(
        "Background selected (duration-match, audio=%.1fs, top_k=%d): %s",
        audio_duration, max(1, min(top_k, len(ranked))), os.path.basename(chosen),
    )
    return chosen


def _recent_file() -> str:
    """Path of the small JSON log of recently-used background basenames."""
    return os.path.join(CACHE_DIR, ".recent_backgrounds.json")


def _load_recent() -> list[str]:
    """Load the recently-used background basenames (newest last); [] on error."""
    try:
        with open(_recent_file(), encoding="utf-8") as f:
            data = json.load(f)
        return [str(x) for x in data] if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _record_used(path: str | None, window: int) -> None:
    """Append *path*'s basename to the recent-use log, capped at *window*.

    Best-effort: a failure to persist history must never break composition.
    """
    if not path:
        return
    name = os.path.basename(path)
    recent = [n for n in _load_recent() if n != name]
    recent.append(name)
    recent = recent[-max(1, window):]
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(_recent_file(), "w", encoding="utf-8") as f:
            json.dump(recent, f)
    except OSError as e:
        logger.debug("Could not persist background history: %s", e)


def _select_with_variety(paths: list[str], audio_duration: float) -> str | None:
    """Duration-match selection with cross-video anti-repeat (production path).

    Wraps _select_best_background with the recent-use history + config knobs so
    callers get rotation across runs. Returns None only for an empty pool.
    """
    if not paths:
        return None
    chosen = _select_best_background(paths, audio_duration,
                                     avoid=set(_load_recent()),
                                     top_k=config.BG_VARIETY_TOPK)
    _record_used(chosen, config.BG_RECENT_WINDOW)
    return chosen


def get_background(keywords: list[str] | None = None,
                   orientation: str = "landscape",
                   audio_duration: float = 0.0) -> str | None:
    """Find or download a background video matching article keywords.

    Selection strategy:
    1. Collect ALL cached videos matching orientation (from keywords + generic queries)
    2. If cached files exist → pick the one whose duration is closest to
       *audio_duration* (minimises looping); falls back to random if duration
       is unknown.
    3. If no cache → download from Pexels using keywords, then generic queries
    4. Fall back to any cached file with matching orientation (duration-matched)
    5. Fall back to default background path from config

    Args:
        keywords: Article-derived search terms (e.g. ["ChatGPT", "AI productivity"]).
        orientation: "landscape" (16:9) or "portrait" (9:16).
        audio_duration: TTS audio length in seconds used to pick the best-fit
            background (minimises looping). Pass 0 to skip duration matching.

    Returns:
        Path to a video file, or None if nothing available.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)

    queries = list(keywords or []) + SEARCH_QUERIES

    # Pass 1: collect ALL cached backgrounds matching this orientation.
    cached_paths: list[str] = []
    for query in queries:
        cached = _cached_path(query, orientation)
        if os.path.exists(cached) and cached not in cached_paths:
            cached_paths.append(cached)

    if cached_paths:
        return _select_with_variety(cached_paths, audio_duration)

    # Pass 2: nothing cached — try downloading for each query until one succeeds.
    if config.PEXELS_API_KEY:
        for query in queries:
            path = _search_and_download(query, orientation)
            if path is None and _last_pexels_error == "auth":
                logger.error("Pexels API key is invalid or expired — skipping remaining queries")
                break
            if path:
                return path
    else:
        logger.warning("PEXELS_API_KEY not configured — will use cached backgrounds only")

    # Pass 3: fall back to any cached file matching orientation (duration-matched)
    fallback = _any_cached(orientation, audio_duration)
    if fallback:
        logger.info("Falling back to cached background: %s", fallback)
        return fallback

    # Pass 4: default path from config
    default = config.BG_VIDEO_PORTRAIT if orientation == "portrait" else config.BG_VIDEO_LANDSCAPE
    if os.path.exists(default):
        logger.info("Falling back to default background: %s", default)
        return default

    logger.warning("No background video available for orientation=%s", orientation)
    return None


def get_backgrounds(keywords: list[str] | None = None,
                    orientation: str = "landscape",
                    audio_duration: float = 0.0,
                    count: int = 1) -> list[str]:
    """Return up to *count* distinct background clips (multi-clip mode, P1).

    Reuses the cache + download + fallback logic of get_background. For count<=1
    this is equivalent to a single-element get_background result.

    Returns a (possibly empty) list of distinct file paths.
    """
    if count <= 1:
        single = get_background(keywords, orientation, audio_duration)
        return [single] if single else []

    os.makedirs(CACHE_DIR, exist_ok=True)
    queries = list(keywords or []) + SEARCH_QUERIES
    collected: list[str] = []

    # Pass 1: cached clips for this orientation.
    for query in queries:
        cached = _cached_path(query, orientation)
        if os.path.exists(cached) and cached not in collected:
            collected.append(cached)
        if len(collected) >= count:
            return collected[:count]

    # Pass 2: download more until we reach count.
    if config.PEXELS_API_KEY:
        for query in queries:
            if len(collected) >= count:
                break
            path = _search_and_download(query, orientation)
            if path is None and _last_pexels_error == "auth":
                logger.error("Pexels API key invalid — stopping multi-bg download")
                break
            if path and path not in collected:
                collected.append(path)

    # Pass 3: fall back to any single cached clip so we never return empty
    # when something usable exists.
    if not collected:
        fallback = _any_cached(orientation, audio_duration)
        if fallback:
            collected.append(fallback)

    return collected[:count]


def download_font(force: bool = False) -> bool:
    """Download NotoSans-Bold.ttf from Google Fonts if not already present.

    Returns True if the font is ready (already exists or downloaded successfully).
    """
    if os.path.exists(_FONT_PATH) and not force:
        logger.debug("Font already exists: %s", _FONT_PATH)
        return True

    os.makedirs(os.path.dirname(_FONT_PATH), exist_ok=True)
    logger.info("Downloading NotoSans-Bold.ttf for Vietnamese subtitle rendering...")

    try:
        req = Request(_NOTO_FONT_URL, headers={"User-Agent": "ContentPipeline/1.0"})
        with urlopen(req, timeout=30) as resp:
            with open(_FONT_PATH, "wb") as f:
                f.write(resp.read())
        size_kb = os.path.getsize(_FONT_PATH) / 1024
        logger.info("Font downloaded: %.1f KB → %s", size_kb, _FONT_PATH)
        return True
    except Exception as e:
        logger.error("Font download failed: %s", e)
        if os.path.exists(_FONT_PATH):
            os.remove(_FONT_PATH)
        return False


def download_backgrounds(force: bool = False) -> bool:
    """Download generic background videos if not already cached.

    With force=True, re-downloads ALL generic SEARCH_QUERIES for both
    orientations, refreshing the cache pool and ensuring maximum variety
    for future background selection.

    Returns True if at least one background is ready per orientation.
    """
    if force and config.PEXELS_API_KEY:
        logger.info("Force-refreshing background cache for all %d generic queries...",
                    len(SEARCH_QUERIES))
        for query in SEARCH_QUERIES:
            for orient in ("landscape", "portrait"):
                result = _search_and_download(query, orient)
                if _last_pexels_error == "auth":
                    logger.error("Pexels API key invalid — aborting force refresh")
                    break
                if not result:
                    logger.warning("Could not download '%s' %s background", query, orient)
            if _last_pexels_error == "auth":
                break

    landscape = get_background(orientation="landscape")
    portrait = get_background(orientation="portrait")

    ok = True
    if not landscape:
        logger.warning("Could not find landscape background video")
        ok = False
    if not portrait:
        logger.warning("Could not find portrait background video")
        ok = False
    return ok


def refresh_backgrounds() -> bool:
    """Force re-download fresh background videos."""
    return download_backgrounds(force=True)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _cache_key(query: str, orientation: str) -> str:
    """Create a filesystem-safe cache key from query + orientation.

    Format: {sanitized_query}_{orientation}_{hash8}
    """
    sanitized = query.lower().strip().replace(" ", "_")
    # Keep only safe chars
    sanitized = "".join(c for c in sanitized if c.isalnum() or c == "_")
    # Add short hash to avoid collisions from sanitization
    h = hashlib.md5(f"{query}|{orientation}".encode()).hexdigest()[:8]
    return f"{sanitized}_{orientation}_{h}"


def _cached_path(query: str, orientation: str) -> str:
    """Return the expected cache file path for a query + orientation."""
    return os.path.join(CACHE_DIR, f"{_cache_key(query, orientation)}.mp4")


def _any_cached(orientation: str, audio_duration: float = 0.0) -> str | None:
    """Return the best-fit cached video matching orientation, or None.

    Uses duration-matching when audio_duration is known, otherwise random.
    """
    if not os.path.isdir(CACHE_DIR):
        return None
    suffix = f"_{orientation}_"
    matches = [
        os.path.join(CACHE_DIR, fname)
        for fname in os.listdir(CACHE_DIR)
        if fname.endswith(".mp4") and suffix in fname
    ]
    return _select_with_variety(matches, audio_duration) if matches else None


def _search_and_download(query: str, orientation: str) -> str | None:
    """Search Pexels for a video matching query and download to cache."""
    output_path = _cached_path(query, orientation)

    videos = _search_videos(query, per_page=5, orientation=orientation)
    if not videos:
        return None

    for video in videos:
        files = video.get("video_files", [])
        best = _find_best_file(files, orientation=orientation)
        if best:
            if _download_file(best["link"], output_path):
                logger.info("Downloaded Pexels background: '%s' → %s", query, output_path)
                return output_path

    return None


def _search_videos(query: str, per_page: int = 5,
                   orientation: str = "landscape") -> list[dict]:
    """Search Pexels for videos."""
    global _last_pexels_error
    pexels_orient = "portrait" if orientation == "portrait" else "landscape"
    encoded_query = quote(query)
    url = (f"{PEXELS_API_BASE}/videos/search"
           f"?query={encoded_query}&per_page={per_page}&orientation={pexels_orient}")

    try:
        req = Request(url, headers={"Authorization": config.PEXELS_API_KEY})
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        _last_pexels_error = None  # Reset on success
        return data.get("videos", [])
    except HTTPError as e:
        if e.code in (401, 403):
            _last_pexels_error = "auth"
            logger.error("Pexels API key invalid or expired (HTTP %d) — check PEXELS_API_KEY in .env", e.code)
        else:
            _last_pexels_error = None
            logger.error("Pexels search failed for '%s': %s", query, e)
        return []
    except Exception as e:
        _last_pexels_error = None
        logger.error("Pexels search failed for '%s': %s", query, e)
        return []


def _find_best_file(files: list[dict], orientation: str = "landscape") -> dict | None:
    """Find the best video file matching orientation and quality.

    For landscape: prefer ~1920x1080
    For portrait: prefer ~1080x1920
    """
    candidates = []

    for f in files:
        w = f.get("width", 0)
        h = f.get("height", 0)

        if orientation == "landscape" and w >= h and w >= 1280:
            candidates.append(f)
        elif orientation == "portrait" and h >= w and h >= 1280:
            candidates.append(f)

    if not candidates and orientation == "portrait":
        # Fallback: many stock videos are landscape only
        for f in files:
            w = f.get("width", 0)
            if w >= 1080:
                candidates.append(f)

    if not candidates:
        return None

    # Prefer HD quality, then by resolution
    def sort_key(f):
        is_hd = 1 if f.get("quality") == "hd" else 0
        resolution = f.get("width", 0) * f.get("height", 0)
        return (is_hd, resolution)

    candidates.sort(key=sort_key, reverse=True)
    return candidates[0]


def _download_file(url: str, output_path: str) -> bool:
    """Download a file from URL to output_path."""
    try:
        req = Request(url, headers={"User-Agent": "ContentPipeline/1.0"})
        with urlopen(req, timeout=120) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0

            with open(output_path, "wb") as f:
                while True:
                    chunk = resp.read(1024 * 1024)  # 1MB chunks
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        pct = (downloaded / total) * 100
                        logger.debug("Download progress: %.0f%%", pct)

        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        logger.info("Downloaded %.1f MB → %s", size_mb, output_path)
        return True

    except Exception as e:
        logger.error("Download failed: %s", e)
        if os.path.exists(output_path):
            os.remove(output_path)
        return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if not config.PEXELS_API_KEY:
        print("Set PEXELS_API_KEY in .env first!")
        print("Get your free API key at: https://www.pexels.com/api/")
    else:
        print("Downloading background videos from Pexels...")
        success = download_backgrounds()
        print("Done!" if success else "Failed — check logs")
