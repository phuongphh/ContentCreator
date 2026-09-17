from __future__ import annotations

"""Núi Trúc TTS provider (P2) — wraps the existing tts_client HTTP logic.

This is the default provider. It delegates to tts_client._tts_single, which
picks the API version from config.TTS_API_VERSION: v2 (default) posts once to
the synchronous /v1/audio/speech endpoint, v1 drives the older async job API
(submit -> poll /status -> download /result). Either way the secure SSL
handling (P0), retry logic and fail-fast bounds (issue #58) stay in one place.
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
                   voice_id: str | None = None,
                   speed: float | None = None) -> str | None:
        # Mỗi version đọc một biến URL riêng — kiểm tra đúng cái đang dùng,
        # không để TTS_API_URL (v1) chặn nhầm đường v2 và ngược lại.
        from video.tts_client import _tts_single, _use_v2, _v2_endpoint
        if _use_v2():
            if not _v2_endpoint():
                logger.error("TTS_V2_API_URL not configured — nuitruc v2 unavailable")
                return None
        elif not config.TTS_API_URL:
            logger.error("TTS_API_URL not configured — nuitruc unavailable")
            return None
        # Reuse the hardened HTTP call (secure SSL + retry) from tts_client.
        return _tts_single(text, output_path, voice_id=voice_id, speed=speed)
