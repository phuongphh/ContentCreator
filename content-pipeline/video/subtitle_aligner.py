from __future__ import annotations

"""
Subtitle Aligner — Căn timing phụ đề theo audio bằng faster-whisper (P1).

Khác với subtitle_generator (chia timing theo tỉ lệ số từ, có thể trôi), module
này lấy **timing thực tế** từ Whisper word-timestamps, nhưng GIỮ NGUYÊN text
script gốc (không dùng transcript của Whisper) để bảo toàn:
  - Chính tả tiếng Việt (Whisper có thể nghe sai)
  - Cách hiển thị số/ký hiệu trong script

Cơ chế: forced-alignment đơn giản — chia script thành cùng các segment như
subtitle_generator, rồi gán cho mỗi segment khoảng thời gian của đúng số từ
tương ứng trong chuỗi word-timestamps của Whisper.

faster-whisper là dependency tuỳ chọn. Thiếu thư viện / lỗi / timeout → trả None
để caller fallback về word-count, pipeline không bao giờ chết.
"""

import logging
import os

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from video.subtitle_generator import _split_into_segments

logger = logging.getLogger(__name__)

# Cache the loaded model between videos (loading is the slow part).
_MODEL = None
_MODEL_SIZE = None


def align(audio_path: str, script_text: str,
          model_size: str | None = None) -> list[tuple[float, float, str]] | None:
    """Return audio-aligned subtitle entries, or None to signal fallback.

    Args:
        audio_path: Narration audio to transcribe for timing.
        script_text: Original script — its text is what gets displayed.
        model_size: Override Whisper model size (default config.WHISPER_MODEL_SIZE).

    Returns:
        List of (start, end, text) using script text + Whisper timing, or None
        if alignment is unavailable (missing lib, error, empty transcription).
    """
    if not os.path.exists(audio_path):
        logger.error("Audio not found for alignment: %s", audio_path)
        return None

    segments = _split_into_segments(script_text)
    if not segments:
        return None

    words = _transcribe(audio_path, model_size)
    if not words:
        logger.info("Whisper produced no word timings — falling back")
        return None

    entries = _map_segments_to_words(segments, words)
    return entries or None


def _map_segments_to_words(
    segments: list[str],
    words: list[tuple[str, float, float]],
) -> list[tuple[float, float, str]]:
    """Assign each script segment the time span of its word run (pure).

    Walks the Whisper word list, consuming as many words as the segment has,
    and uses the first word's start and last word's end as the segment timing.
    Guarantees non-decreasing, non-overlapping, start<=end timing.
    """
    result: list[tuple[float, float, str]] = []
    wi = 0
    n = len(words)
    prev_end = 0.0

    for seg in segments:
        if wi >= n:
            break
        count = max(1, len(seg.split()))
        start = words[wi][1]
        end_idx = min(wi + count, n) - 1
        end = words[end_idx][2]

        start = max(start, prev_end)
        if end < start:
            end = start
        result.append((start, end, seg))
        prev_end = end
        wi = end_idx + 1

    return result


def _get_model(model_size: str):
    """Load (and cache) a faster-whisper model. Returns None if unavailable."""
    global _MODEL, _MODEL_SIZE
    if _MODEL is not None and _MODEL_SIZE == model_size:
        return _MODEL
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        logger.warning(
            "faster-whisper not installed — Whisper subtitle alignment "
            "unavailable. Install with: pip install faster-whisper"
        )
        return None
    try:
        # CPU + int8 keeps it light enough for a Mac Mini.
        _MODEL = WhisperModel(model_size, device="cpu", compute_type="int8")
        _MODEL_SIZE = model_size
        return _MODEL
    except Exception as e:
        logger.error("Failed to load Whisper model %r: %s", model_size, e)
        return None


def _transcribe(audio_path: str,
                model_size: str | None = None) -> list[tuple[str, float, float]] | None:
    """Transcribe audio to a flat list of (word, start, end). None on failure."""
    size = model_size or getattr(config, "WHISPER_MODEL_SIZE", "base")
    model = _get_model(size)
    if model is None:
        return None
    try:
        seg_iter, _info = model.transcribe(
            audio_path, language="vi", word_timestamps=True
        )
        words: list[tuple[str, float, float]] = []
        for seg in seg_iter:
            for w in (getattr(seg, "words", None) or []):
                words.append((w.word.strip(), float(w.start), float(w.end)))
        return words or None
    except Exception as e:
        logger.error("Whisper transcription failed: %s", e)
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(f"Whisper model size: {getattr(config, 'WHISPER_MODEL_SIZE', 'base')}")
    try:
        import faster_whisper  # noqa: F401
        print("faster-whisper: installed")
    except ImportError:
        print("faster-whisper: NOT installed (pipeline will fall back to word-count)")
