from __future__ import annotations

"""Edge TTS provider (P2) — free Microsoft neural voices, incl. Vietnamese.

Uses the optional `edge-tts` package (lazy import). Voices: vi-VN-HoaiMyNeural
(female) / vi-VN-NamMinhNeural (male). Receives speech-normalized text from
upstream, so Vietnamese numbers are already expanded correctly.
"""

import logging
import os

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import config
from video.tts.base import TTSProvider

logger = logging.getLogger(__name__)


def _speed_to_rate(speed: float) -> str:
    """Convert a 1.0-relative speed to an edge-tts rate string like '+10%'."""
    pct = int(round((speed - 1.0) * 100))
    return f"{pct:+d}%"


class EdgeProvider(TTSProvider):
    name = "edge"

    def synthesize(self, text: str, output_path: str,
                   voice_id: str | None = None,
                   speed: float | None = None) -> str | None:
        try:
            import asyncio
            import edge_tts
        except ImportError:
            logger.warning(
                "edge-tts not installed — Edge provider unavailable. "
                "Install with: pip install edge-tts"
            )
            return None

        voice = voice_id or getattr(config, "EDGE_VOICE", "vi-VN-HoaiMyNeural")
        if speed is None:
            speed = getattr(config, "TTS_VOICE_SPEED", 1.0)
        rate = _speed_to_rate(speed)
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        async def _run():
            communicate = edge_tts.Communicate(text, voice, rate=rate)
            await communicate.save(output_path)

        try:
            asyncio.run(_run())
        except Exception as e:
            logger.error("Edge TTS synthesis failed: %s", e)
            return None

        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            logger.info("Edge TTS saved: %s (voice=%s)", output_path, voice)
            return output_path
        logger.error("Edge TTS produced no audio")
        return None
