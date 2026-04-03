from __future__ import annotations

"""
Script Generator — Tạo script dài (YouTube) và ngắn (Shorts/TikTok) từ bài tổng hợp.

Dùng Claude Sonnet để viết lại script phù hợp từng format.

Dùng delimiter ===METADATA=== để tách script (plain text) khỏi metadata (JSON).
Tránh lỗi JSON parse khi script dài chứa newlines/quotes.
"""

import json
import logging
import re

import anthropic

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

logger = logging.getLogger(__name__)

LONG_SCRIPT_PROMPT = """Bạn là scriptwriter cho kênh "AI 5 Phút Mỗi Ngày" — kênh YouTube
giúp người Việt đi làm văn phòng (22-35 tuổi) hiểu và dùng AI.

Từ bài tổng hợp dưới đây, viết SCRIPT ĐỌC cho video YouTube dài 5-10 phút (bản tin dài).

CẤU TRÚC BẮT BUỘC:
1. MỞ ĐẦU (30 giây): Hook gây tò mò + giới thiệu nhanh các tin hôm nay
2. THÂN BÀI (5-7 tin, mỗi tin 45-90 giây): Trình bày chi tiết từng tin
3. PHÂN TÍCH (1-2 phút): Bình luận, liên hệ thực tế Việt Nam, nhận định xu hướng
4. KẾT (30 giây): Lời khuyên cụ thể + CTA subscribe

YÊU CẦU:
- Chọn 5-7 headline quan trọng nhất từ bài tổng hợp
- Viết dạng văn nói tự nhiên, như đang kể chuyện
- Câu ngắn, nhịp rõ ràng (vì sẽ đọc bằng TTS)
- Giữa bài có câu chuyển đoạn tự nhiên
- Phần phân tích phải sâu, có góc nhìn riêng
- KHÔNG dùng emoji
- KHÔNG dùng bullet point — viết liền mạch
- Từ 800 đến 1200 từ

BÀI TỔNG HỢP:
{narrative}

QUAN TRỌNG — trả lời theo format sau (giữ đúng delimiter):

===SCRIPT===
(viết toàn bộ nội dung script ở đây, plain text, nhiều dòng)
===METADATA===
{{"youtube_title": "tiêu đề video (dưới 60 ký tự)", "youtube_description": "mô tả video (2-3 câu)"}}"""

SHORT_SCRIPT_PROMPT = """Bạn là scriptwriter cho kênh TikTok/YouTube Shorts "AI 5 Phút Mỗi Ngày"
— giúp người Việt đi làm hiểu AI nhanh gọn.

Từ bài tổng hợp dưới đây, viết SCRIPT NGẮN cho video 60-90 giây (bản tin ngắn).

CẤU TRÚC BẮT BUỘC:
1. HOOK (3 giây): Câu mở đầu gây sốc/tò mò ngay lập tức
2. 3 TIN NÓNG (mỗi tin 15-20 giây): Chọn top 3 headline, trình bày nhanh gọn
3. CTA (5 giây): "Follow để cập nhật AI mỗi ngày"

YÊU CẦU:
- Chọn đúng 3 tin HAY NHẤT, gây tò mò nhất từ bài tổng hợp
- Câu HOOK đầu tiên phải gây sốc/tò mò ngay lập tức (3 giây đầu quyết định!)
- Câu cực ngắn, nhịp nhanh (mỗi câu tối đa 15 từ)
- Tổng từ 150 đến 200 từ
- KHÔNG emoji, KHÔNG bullet point
- Ngôn ngữ đời thường, như nói chuyện

BÀI TỔNG HỢP:
{narrative}

QUAN TRỌNG — trả lời theo format sau (giữ đúng delimiter):

===SCRIPT===
(viết toàn bộ nội dung script ở đây, plain text, nhiều dòng)
===METADATA===
{{"tiktok_caption": "caption ngắn gọn (dưới 100 ký tự)", "tiktok_hashtags": "#AI #CongNghe #AIVietNam #AI5Phut"}}"""


def generate_long_script(narrative: str) -> dict | None:
    """Generate a 5-10 min YouTube script from the narrative report."""
    return _call_ai(LONG_SCRIPT_PROMPT.format(narrative=narrative), "long")


def generate_short_script(narrative: str) -> dict | None:
    """Generate a 60-90s TikTok/Shorts script from the narrative report."""
    return _call_ai(SHORT_SCRIPT_PROMPT.format(narrative=narrative), "short")


def _call_ai(prompt: str, script_type: str) -> dict | None:
    """Call Claude Sonnet to generate a script."""
    if not config.ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY not set")
        return None

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    for attempt in range(3):
        try:
            max_tok = 3000 if script_type == "long" else 1500
            message = client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=max_tok,
                messages=[{"role": "user", "content": prompt}],
            )
            text = message.content[0].text.strip()
            result = _parse_response(text, script_type)
            if result:
                return result

            # If parse failed, retry with a clearer nudge
            if attempt < 2:
                logger.warning("Parse failed for %s script (attempt %d), retrying...",
                               script_type, attempt + 1)
                continue
            return None

        except anthropic.RateLimitError:
            import time
            wait = 2 ** (attempt + 1)
            logger.warning("Rate limited, waiting %ds...", wait)
            time.sleep(wait)
        except anthropic.APIError as e:
            logger.error("API error generating %s script: %s", script_type, e)
            return None

    logger.error("Failed to generate %s script after 3 attempts", script_type)
    return None


def _parse_response(text: str, script_type: str) -> dict | None:
    """Parse AI response using ===SCRIPT=== / ===METADATA=== delimiters.

    Falls back to regex extraction if delimiters are missing.
    """
    script = ""
    metadata = {}

    # Strategy 1: Delimiter-based parsing
    if "===SCRIPT===" in text and "===METADATA===" in text:
        parts = text.split("===METADATA===", 1)
        script_part = parts[0]
        metadata_part = parts[1].strip() if len(parts) > 1 else ""

        # Extract script (between ===SCRIPT=== and ===METADATA===)
        if "===SCRIPT===" in script_part:
            script = script_part.split("===SCRIPT===", 1)[1].strip()

        # Parse metadata JSON
        if metadata_part:
            metadata = _safe_parse_json(metadata_part)

    # Strategy 2: Fallback — try to parse entire response as JSON
    if not script:
        result = _safe_parse_json(text)
        if result and result.get("script"):
            return result

    # Strategy 3: Regex extraction for script content
    if not script:
        # Try to find the longest paragraph block (likely the script)
        paragraphs = text.split("\n\n")
        # Filter out JSON-looking blocks and short lines
        candidates = [p.strip() for p in paragraphs
                      if len(p.strip()) > 100 and not p.strip().startswith("{")]
        if candidates:
            script = "\n\n".join(candidates)
            logger.warning("Used fallback regex to extract %s script (%d chars)",
                           script_type, len(script))

    if not script:
        logger.error("Could not extract script from AI response for %s", script_type)
        return None

    result = {"script": script}
    result.update(metadata)
    logger.info("Generated %s script (%d chars)", script_type, len(script))
    return result


def _safe_parse_json(text: str) -> dict:
    """Try to parse JSON from text, handling common issues."""
    text = text.strip()

    # Remove markdown code blocks
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find JSON object in text
    match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return {}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sample = "Hôm nay OpenAI ra mắt GPT-5 với khả năng suy luận vượt trội. Claude cũng cập nhật mới."
    result = generate_long_script(sample)
    if result:
        print("Long script:", result["script"][:200])
    result = generate_short_script(sample)
    if result:
        print("Short script:", result["script"][:200])
