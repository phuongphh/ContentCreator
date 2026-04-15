from __future__ import annotations

"""
Subtitle Generator — Tạo file SRT từ script text + audio duration.

Chia text thành các segment ngắn (~8-12 từ mỗi dòng),
phân bố timing đều theo tổng duration của audio.
"""

import logging
import os
import re

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logger = logging.getLogger(__name__)

WORDS_PER_SEGMENT = 10  # Số từ tối đa mỗi dòng subtitle


def generate_srt(text: str, audio_duration: float, output_path: str) -> str | None:
    """Generate SRT subtitle file from text and audio duration.

    Splits text into segments of ~WORDS_PER_SEGMENT words,
    distributes timing proportionally across audio duration.

    Args:
        text: Full script text.
        audio_duration: Total audio duration in seconds.
        output_path: Path to save .srt file.

    Returns:
        Path to the SRT file, or None on failure.
    """
    if not text or audio_duration <= 0:
        logger.error("Invalid input: text=%d chars, duration=%.1f", len(text or ""), audio_duration)
        return None

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    segments = _split_into_segments(text)
    if not segments:
        logger.error("No segments generated from text")
        return None

    # Count total words for proportional timing
    word_counts = [len(seg.split()) for seg in segments]
    total_words = sum(word_counts)

    srt_lines = []
    current_time = 0.0

    for i, (segment, wcount) in enumerate(zip(segments, word_counts), 1):
        # Duration proportional to word count
        segment_duration = (wcount / total_words) * audio_duration
        start = current_time
        end = current_time + segment_duration

        srt_lines.append(str(i))
        srt_lines.append(f"{_format_time(start)} --> {_format_time(end)}")
        srt_lines.append(segment)
        srt_lines.append("")  # blank line between entries

        current_time = end

    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(srt_lines))
        logger.info("SRT saved: %s (%d segments, %.1fs)", output_path, len(segments), audio_duration)
        return output_path
    except OSError as e:
        logger.error("Failed to write SRT: %s", e)
        return None


def _split_into_segments(text: str) -> list[str]:
    """Split text into subtitle segments at sentence/clause boundaries."""
    # First split by sentences
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())

    segments = []
    for sentence in sentences:
        words = sentence.split()
        if len(words) <= WORDS_PER_SEGMENT:
            segments.append(sentence)
        else:
            # Split long sentences at commas or by word count
            parts = re.split(r'(?<=,)\s+', sentence)
            buffer = []
            buffer_len = 0
            for part in parts:
                part_words = len(part.split())
                if buffer_len + part_words > WORDS_PER_SEGMENT and buffer:
                    segments.append(" ".join(buffer))
                    buffer = [part]
                    buffer_len = part_words
                else:
                    buffer.append(part)
                    buffer_len += part_words
            if buffer:
                # If remaining buffer is still too long, split by word count
                remaining = " ".join(buffer)
                remaining_words = remaining.split()
                while len(remaining_words) > WORDS_PER_SEGMENT:
                    segments.append(" ".join(remaining_words[:WORDS_PER_SEGMENT]))
                    remaining_words = remaining_words[WORDS_PER_SEGMENT:]
                if remaining_words:
                    segments.append(" ".join(remaining_words))

    # Remove empty segments and deduplicate consecutive identical segments.
    # AI-generated scripts sometimes repeat the closing sentence in both the
    # body and the outro, which would cause the subtitle to appear twice at
    # the end of the video.
    result: list[str] = []
    for seg in segments:
        if seg.strip() and (not result or seg.strip() != result[-1].strip()):
            result.append(seg)
        elif seg.strip() and result and seg.strip() == result[-1].strip():
            logger.warning("Duplicate subtitle segment removed: '%s...'", seg[:60])
    return result


def _format_time(seconds: float) -> str:
    """Format seconds to SRT time format: HH:MM:SS,mmm"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sample = ("Hôm nay thế giới AI lại có tin nóng. "
              "OpenAI vừa ra mắt GPT-5 với khả năng suy luận vượt trội, "
              "khiến cộng đồng công nghệ xôn xao. "
              "Điều đặc biệt là bạn có thể dùng ngay hôm nay. "
              "Chỉ cần vào ChatGPT và chọn model mới nhất.")
    result = generate_srt(sample, 30.0, "/tmp/test_subtitle.srt")
    if result:
        with open(result) as f:
            print(f.read())
