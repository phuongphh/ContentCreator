from __future__ import annotations

"""
Pexels Video Downloader — Tự động tải background video miễn phí từ Pexels.

Pexels API miễn phí, chỉ cần API key (đăng ký tại pexels.com/api).

Tải video cho 2 format:
- Landscape (16:9) cho YouTube dài
- Portrait (9:16) cho Shorts/TikTok
"""

import json
import logging
import os
from urllib.request import Request, urlopen

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

logger = logging.getLogger(__name__)

PEXELS_API_BASE = "https://api.pexels.com"

# Search queries cho background video phù hợp với kênh AI/tech
SEARCH_QUERIES = [
    "abstract technology",
    "futuristic particles",
    "digital network",
    "blue gradient motion",
    "minimal abstract loop",
]


def download_backgrounds(force: bool = False) -> bool:
    """Download background videos from Pexels if not already present.

    Downloads one landscape and one portrait video.
    Skips if files already exist (unless force=True).

    Returns True if backgrounds are ready.
    """
    if not config.PEXELS_API_KEY:
        logger.error("PEXELS_API_KEY not configured — cannot download backgrounds")
        return False

    bg_dir = os.path.join(os.path.dirname(__file__), "assets", "backgrounds")
    os.makedirs(bg_dir, exist_ok=True)

    landscape_path = os.path.join(bg_dir, "landscape.mp4")
    portrait_path = os.path.join(bg_dir, "portrait.mp4")

    landscape_ok = os.path.exists(landscape_path) and not force
    portrait_ok = os.path.exists(portrait_path) and not force

    if landscape_ok and portrait_ok:
        logger.info("Background videos already exist, skipping download")
        return True

    for query in SEARCH_QUERIES:
        videos = _search_videos(query, per_page=5)
        if not videos:
            continue

        for video in videos:
            files = video.get("video_files", [])

            if not landscape_ok:
                landscape_file = _find_best_file(files, orientation="landscape")
                if landscape_file:
                    if _download_file(landscape_file["link"], landscape_path):
                        landscape_ok = True
                        logger.info("Downloaded landscape background: %s", query)

            if not portrait_ok:
                portrait_file = _find_best_file(files, orientation="portrait")
                if portrait_file:
                    if _download_file(portrait_file["link"], portrait_path):
                        portrait_ok = True
                        logger.info("Downloaded portrait background: %s", query)

            if landscape_ok and portrait_ok:
                return True

    if not landscape_ok:
        logger.warning("Could not find landscape background video")
    if not portrait_ok:
        logger.warning("Could not find portrait background video")

    return landscape_ok and portrait_ok


def refresh_backgrounds() -> bool:
    """Force re-download fresh background videos."""
    return download_backgrounds(force=True)


def _search_videos(query: str, per_page: int = 5) -> list[dict]:
    """Search Pexels for videos."""
    url = f"{PEXELS_API_BASE}/videos/search?query={query}&per_page={per_page}&orientation=landscape"

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
        quality = f.get("quality", "")

        if orientation == "landscape" and w >= h and w >= 1280:
            candidates.append(f)
        elif orientation == "portrait" and h >= w and h >= 1280:
            candidates.append(f)

    if not candidates and orientation == "portrait":
        # Fallback: many stock videos are landscape only —
        # we'll use FFmpeg to crop/rotate later
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
