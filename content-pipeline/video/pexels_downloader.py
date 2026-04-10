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
from urllib.parse import quote
from urllib.request import Request, urlopen

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

logger = logging.getLogger(__name__)

PEXELS_API_BASE = "https://api.pexels.com"

# Generic search queries cho background video phù hợp với kênh AI/tech
SEARCH_QUERIES = [
    "abstract technology",
    "futuristic particles",
    "digital network",
    "blue gradient motion",
    "minimal abstract loop",
]

CACHE_DIR = os.path.join(os.path.dirname(__file__), "assets", "backgrounds", "cache")


def get_background(keywords: list[str] | None = None,
                   orientation: str = "landscape") -> str | None:
    """Find or download a background video matching article keywords.

    Search order:
    1. Cached video matching one of the keywords
    2. Download from Pexels using keywords
    3. Cached video matching generic tech queries
    4. Download from Pexels using generic tech queries
    5. Any cached video with matching orientation
    6. Default background path from config

    Args:
        keywords: Article-derived search terms (e.g. ["ChatGPT", "AI productivity"]).
        orientation: "landscape" (16:9) or "portrait" (9:16).

    Returns:
        Path to a video file, or None if nothing available.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)

    queries = list(keywords or []) + SEARCH_QUERIES

    # Pass 1: check cache, then try downloading for each query
    for query in queries:
        cached = _cached_path(query, orientation)
        if os.path.exists(cached):
            logger.info("Using cached background: %s (%s)", cached, query)
            return cached

        if config.PEXELS_API_KEY:
            path = _search_and_download(query, orientation)
            if path:
                return path

    # Pass 2: fall back to any cached file matching orientation
    fallback = _any_cached(orientation)
    if fallback:
        logger.info("Falling back to cached background: %s", fallback)
        return fallback

    # Pass 3: default path from config
    default = config.BG_VIDEO_PORTRAIT if orientation == "portrait" else config.BG_VIDEO_LANDSCAPE
    if os.path.exists(default):
        logger.info("Falling back to default background: %s", default)
        return default

    logger.warning("No background video available for orientation=%s", orientation)
    return None


def download_backgrounds(force: bool = False) -> bool:
    """Download generic background videos if not already cached.

    Kept for backward compatibility with main.py Phase 0.
    Returns True if at least one background is ready per orientation.
    """
    landscape = get_background(orientation="landscape")
    portrait = get_background(orientation="portrait")

    if force and config.PEXELS_API_KEY:
        landscape = _search_and_download(SEARCH_QUERIES[0], "landscape")
        portrait = _search_and_download(SEARCH_QUERIES[0], "portrait")

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


def _any_cached(orientation: str) -> str | None:
    """Return path to any cached video matching orientation, or None."""
    if not os.path.isdir(CACHE_DIR):
        return None
    suffix = f"_{orientation}_"
    for fname in sorted(os.listdir(CACHE_DIR)):
        if fname.endswith(".mp4") and suffix in fname:
            return os.path.join(CACHE_DIR, fname)
    return None


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
    pexels_orient = "portrait" if orientation == "portrait" else "landscape"
    encoded_query = quote(query)
    url = (f"{PEXELS_API_BASE}/videos/search"
           f"?query={encoded_query}&per_page={per_page}&orientation={pexels_orient}")

    try:
        req = Request(url, headers={"Authorization": config.PEXELS_API_KEY})
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        return data.get("videos", [])
    except Exception as e:
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
