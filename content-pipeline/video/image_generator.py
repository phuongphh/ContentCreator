from __future__ import annotations

"""
AI Image Generator (Phase 4 EPIC #4.3 — Drama Visual Assets).

Turns a `thumbnail_prompt` (from processors/drama_rewriter.py's rewrite
output) into a background illustration for Drama scenes, via Replicate's
prediction API (create -> poll -> download). Cached by prompt hash + index so
the same story's illustrations are never regenerated (cost control — see
phase-4-detailed.md's risk section on AI image cost).

Falls back to None (never raises) on a missing API token/model version,
network failure, or unexpected response shape — video/drama_composer.py then
uses a solid/gradient background instead. This mirrors this codebase's
existing external-service fallback philosophy (pexels_downloader.py,
video/tts/factory.py): an optional paid service being unavailable must never
crash the pipeline.
"""

import hashlib
import json
import logging
import os
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

logger = logging.getLogger(__name__)

REPLICATE_API_BASE = "https://api.replicate.com/v1"
CACHE_DIR = os.path.join(os.path.dirname(__file__), "assets", "illustrations", "cache")

POLL_INTERVAL_SECONDS = 2
POLL_TIMEOUT_SECONDS = 90


def _cache_path(prompt: str, index: int) -> str:
    """Deterministic cache filename for (prompt, index).

    Same prompt+index always maps to the same file, so re-running the
    pipeline for the same story/scene hits cache instead of re-generating.
    """
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
    return os.path.join(CACHE_DIR, f"{digest}_{index}.png")


def generate_illustration(prompt: str, index: int = 0) -> str | None:
    """Generate (or reuse a cached) AI illustration for `prompt`.

    Returns the local file path, or None if Replicate isn't configured or
    generation fails for any reason. Callers must treat None as "no
    illustration available, use a fallback background."
    """
    if not prompt or not prompt.strip():
        return None

    cache_path = _cache_path(prompt, index)
    if os.path.exists(cache_path):
        logger.debug("Illustration cache hit: %s", cache_path)
        return cache_path

    api_token = getattr(config, "REPLICATE_API_TOKEN", "")
    if not api_token:
        logger.info("REPLICATE_API_TOKEN not configured — skipping AI illustration")
        return None

    prediction_id = _create_prediction(prompt, api_token)
    if prediction_id is None:
        return None

    image_url = _poll_prediction(prediction_id, api_token)
    if image_url is None:
        return None

    return _download_image(image_url, cache_path)


def generate_illustrations(prompt: str, count: int = 3) -> list[str]:
    """Generate up to `count` illustration variants for `prompt`.

    One API call per variant (Replicate model support for n>1 outputs isn't
    consistent across models). Returns only the ones that succeeded — may be
    fewer than `count`, or empty if generation isn't available at all.
    """
    results = []
    for i in range(count):
        path = generate_illustration(prompt, index=i)
        if path:
            results.append(path)
    return results


def _create_prediction(prompt: str, api_token: str) -> str | None:
    model_version = getattr(config, "REPLICATE_MODEL_VERSION", "")
    if not model_version:
        logger.error("REPLICATE_MODEL_VERSION not configured — cannot generate illustration")
        return None

    payload = json.dumps({
        "version": model_version,
        "input": {"prompt": prompt},
    }).encode("utf-8")
    req = Request(
        f"{REPLICATE_API_BASE}/predictions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except (HTTPError, URLError, TimeoutError) as e:
        logger.error("Replicate prediction create failed: %s", e)
        return None
    except (ValueError, json.JSONDecodeError) as e:
        logger.error("Replicate prediction create returned invalid JSON: %s", e)
        return None

    prediction_id = data.get("id")
    if not prediction_id:
        logger.error("Replicate prediction create returned no id: %r", data)
        return None
    return prediction_id


def _poll_prediction(prediction_id: str, api_token: str) -> str | None:
    """Poll until the prediction succeeds/fails/times out. Returns the first
    output image URL, or None."""
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    req = Request(
        f"{REPLICATE_API_BASE}/predictions/{prediction_id}",
        headers={"Authorization": f"Bearer {api_token}"},
    )
    while True:
        try:
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except (HTTPError, URLError, TimeoutError) as e:
            logger.error("Replicate prediction poll failed: %s", e)
            return None
        except (ValueError, json.JSONDecodeError) as e:
            logger.error("Replicate prediction poll returned invalid JSON: %s", e)
            return None

        status = data.get("status")
        if status == "succeeded":
            output = data.get("output")
            if isinstance(output, list) and output:
                return output[0]
            if isinstance(output, str) and output:
                return output
            logger.error("Replicate prediction succeeded but no usable output: %r", output)
            return None
        if status in ("failed", "canceled"):
            logger.error("Replicate prediction %s: %s", status, data.get("error"))
            return None

        if time.monotonic() >= deadline:
            logger.error("Replicate prediction %s timed out after %ds",
                         prediction_id, POLL_TIMEOUT_SECONDS)
            return None
        time.sleep(POLL_INTERVAL_SECONDS)


def _download_image(url: str, output_path: str) -> str | None:
    try:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        req = Request(url)
        with urlopen(req, timeout=30) as resp:
            data = resp.read()
        with open(output_path, "wb") as f:
            f.write(data)
    except (HTTPError, URLError, OSError, TimeoutError) as e:
        logger.error("Failed to download illustration from %s: %s", url, e)
        return None
    return output_path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(f"REPLICATE_API_TOKEN configured: {bool(config.REPLICATE_API_TOKEN)}")
    print(f"REPLICATE_MODEL_VERSION: {config.REPLICATE_MODEL_VERSION or '(not set)'}")
