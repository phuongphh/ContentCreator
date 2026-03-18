from __future__ import annotations

"""
Script Generator — Tạo script dài (YouTube) và ngắn (Shorts/TikTok) từ bài tổng hợp.

Dùng Claude Sonnet để viết lại script phù hợp từng format.
"""

import json
import logging

import anthropic

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

logger = logging.getLogger(__name__)

LONG_SCRIPT_PROMPT = """Bạn là scriptwriter cho kênh "AI 5 Phút Mỗi Ngày" — kênh YouTube
giúp người Việt đi làm văn phòng (22-35 tuổi) hiểu và dùng AI.

Từ bài tổng hợp dưới đây, viết lại thành SCRIPT ĐỌC cho video YouTube dài 4-6 phút.

YÊU CẦU:
- Viết dạng văn nói tự nhiên, như đang kể chuyện
- Câu ngắn, nhịp rõ ràng (vì sẽ đọc bằng TTS)
- Mở đầu bằng hook gây tò mò (câu đầu tiên rất quan trọng!)
- Giữa bài có câu chuyển đoạn tự nhiên
- Kết thúc bằng lời khuyên cụ thể + CTA nhẹ nhàng
- KHÔNG dùng emoji
- KHÔNG dùng bullet point — viết liền mạch
- Tối đa 800 từ

BÀI TỔNG HỢP:
{narrative}

Trả về JSON:
{{"script": "nội dung script", "youtube_title": "tiêu đề video (dưới 60 ký tự)", "youtube_description": "mô tả video (2-3 câu)"}}"""

SHORT_SCRIPT_PROMPT = """Bạn là scriptwriter cho kênh TikTok/YouTube Shorts "AI 5 Phút Mỗi Ngày"
— giúp người Việt đi làm hiểu AI nhanh gọn.

Từ bài tổng hợp dưới đây, viết SCRIPT NGẮN cho video 45-60 giây.

YÊU CẦU:
- Chỉ chọn 1-2 tin HAY NHẤT, gây tò mò nhất
- Câu HOOK đầu tiên phải gây sốc/tò mò ngay lập tức
- Câu cực ngắn, nhịp nhanh (mỗi câu tối đa 15 từ)
- Tổng tối đa 150 từ
- Kết bằng CTA: "Follow để cập nhật AI mỗi ngày"
- KHÔNG emoji, KHÔNG bullet point
- Ngôn ngữ đời thường, như nói chuyện

BÀI TỔNG HỢP:
{narrative}

Trả về JSON:
{{"script": "nội dung script", "tiktok_caption": "caption ngắn gọn (dưới 100 ký tự)", "tiktok_hashtags": "#AI #CongNghe #AIVietNam #AI5Phut"}}"""


def generate_long_script(narrative: str) -> dict | None:
    """Generate a 4-6 min YouTube script from the narrative report."""
    return _call_ai(LONG_SCRIPT_PROMPT.format(narrative=narrative), "long")


def generate_short_script(narrative: str) -> dict | None:
    """Generate a 45-60s TikTok/Shorts script from the narrative report."""
    return _call_ai(SHORT_SCRIPT_PROMPT.format(narrative=narrative), "short")


def _call_ai(prompt: str, script_type: str) -> dict | None:
    """Call Claude Sonnet to generate a script."""
    if not config.ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY not set")
        return None

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    for attempt in range(3):
        try:
            message = client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            )
            text = message.content[0].text.strip()
            # Parse JSON — handle markdown code blocks
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            result = json.loads(text)
            logger.info("Generated %s script (%d chars)", script_type, len(result.get("script", "")))
            return result
        except json.JSONDecodeError as e:
            logger.error("JSON parse error for %s script: %s", script_type, e)
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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sample = "Hôm nay OpenAI ra mắt GPT-5 với khả năng suy luận vượt trội. Claude cũng cập nhật mới."
    result = generate_long_script(sample)
    if result:
        print("Long script:", result["script"][:200])
    result = generate_short_script(sample)
    if result:
        print("Short script:", result["script"][:200])
