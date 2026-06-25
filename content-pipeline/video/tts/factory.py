from __future__ import annotations

"""TTS factory + fallback chain (P2)."""

import logging
import os

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import config

logger = logging.getLogger(__name__)

_KNOWN = ("nuitruc", "edge")


def get_provider(name: str):
    """Return a provider instance for *name* (defaults to nuitruc)."""
    if name == "edge":
        from video.tts.edge import EdgeProvider
        return EdgeProvider()
    from video.tts.nuitruc import NuiTrucProvider
    return NuiTrucProvider()


def _provider_order() -> list[str]:
    """Configured primary first, then the remaining known providers."""
    primary = getattr(config, "TTS_PROVIDER", "nuitruc")
    order = [primary] + [p for p in _KNOWN if p != primary]
    # De-dupe while keeping order (in case primary isn't a known name).
    seen = set()
    return [p for p in order if not (p in seen or seen.add(p))]


def synthesize(text: str, output_path: str) -> str | None:
    """Synthesize via the configured provider, falling back to the others.

    The text is assumed already speech-normalized (preprocess_for_tts ran
    upstream); providers must not re-process it.
    """
    if output_path:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    for name in _provider_order():
        provider = get_provider(name)
        result = provider.synthesize(text, output_path)
        if result:
            return result
        logger.warning("TTS provider %r failed — trying next", name)

    logger.error("All TTS providers failed")
    return None
