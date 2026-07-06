from __future__ import annotations

"""Núi Trúc TTS provider (P2) — wraps the existing tts_client HTTP logic.

This is the default provider. It delegates to tts_client._tts_single, which now
drives the async job API (submit -> poll /status -> download /result) so long
scripts no longer time out, while the secure SSL handling (P0), retry logic and
fail-fast bounds (issue #58) stay in one place.
"""

import logging

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import config
from video.tts.base import TTSProvider

logger = logging.getLogger(__name__)


class NuiTrucProvider(TTSProvider):
    name = "nuitruc"

    def synthesize(self, text: str, output_path: str,
                   voice_id: str | None = None) -> str | None:
        if not config.TTS_API_URL:
            logger.error("TTS_API_URL not configured — nuitruc unavailable")
            return None
        # Reuse the hardened HTTP call (secure SSL + retry) from tts_client.
        from video.tts_client import _tts_single
        return _tts_single(text, output_path, voice_id=voice_id)
