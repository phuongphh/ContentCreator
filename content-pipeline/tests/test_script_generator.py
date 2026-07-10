"""Tests for video/script_generator._parse_response — delimiter parsing.

Trọng tâm: KHÔNG BAO GIỜ để delimiter (===SCRIPT===/===METADATA===) hay
markdown lọt vào script cuối — script được lưu DB rồi đi thẳng vào TTS
(đọc thành tiếng "bằng bằng bằng...") và phụ đề (hiện rác lên màn hình).
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from video.script_generator import _parse_response

_BODY = ("Xin chào các bạn, hôm nay chúng ta nói về công cụ AI mới giúp dân "
         "văn phòng tiết kiệm hàng giờ mỗi ngày. Đây là nội dung đủ dài để "
         "vượt ngưỡng một trăm ký tự của fallback paragraph.")
_META = '{"youtube_title": "Tiêu đề test", "youtube_description": "Mô tả."}'


class TestDelimiterParsing(unittest.TestCase):
    def test_well_formed_response(self):
        text = f"===SCRIPT===\n{_BODY}\n===METADATA===\n{_META}"
        result = _parse_response(text, "long")
        self.assertEqual(result["script"], _BODY)
        self.assertEqual(result["youtube_title"], "Tiêu đề test")

    def test_missing_metadata_delimiter_still_splits_trailing_json(self):
        # Model quên ===METADATA=== — trước đây case này rơi xuống fallback
        # paragraph và giữ nguyên dòng "===SCRIPT===" trong script.
        text = f"===SCRIPT===\n{_BODY}\n\n{_META}"
        result = _parse_response(text, "long")
        self.assertEqual(result["script"], _BODY)
        self.assertEqual(result["youtube_title"], "Tiêu đề test")

    def test_missing_script_delimiter(self):
        text = f"{_BODY}\n===METADATA===\n{_META}"
        result = _parse_response(text, "long")
        self.assertEqual(result["script"], _BODY)
        self.assertEqual(result["youtube_title"], "Tiêu đề test")

    def test_no_delimiter_at_all_falls_back_to_paragraphs(self):
        result = _parse_response(f"{_BODY}\n\n{_META}", "short")
        self.assertEqual(result["script"], _BODY)

    def test_json_only_response(self):
        text = f'{{"script": "{_BODY}", "youtube_title": "T"}}'
        result = _parse_response(text, "long")
        self.assertEqual(result["script"], _BODY)
        self.assertEqual(result["youtube_title"], "T")


class TestNoArtifactsLeak(unittest.TestCase):
    """Bất kể model trả format nào, script cuối không được chứa ký tự lạ."""

    CASES = [
        # Delimiter dính liền paragraph (không có dòng trống) — thủ phạm
        # chính của bug "TTS đọc ===SCRIPT===".
        f"===SCRIPT===\n{_BODY}",
        # Delimiter lặp lại giữa nội dung
        f"===SCRIPT===\n{_BODY}\n===SCRIPT===\n===METADATA===\n{_META}",
        # Bọc trong code fence markdown
        f"```\n===SCRIPT===\n{_BODY}\n===METADATA===\n{_META}\n```",
        # Delimiter biến thể có chữ khác
        f"=== KỊCH BẢN ===\n{_BODY}\n===METADATA===\n{_META}",
    ]

    def test_no_delimiter_or_markdown_in_final_script(self):
        for text in self.CASES:
            with self.subTest(text=text[:50]):
                result = _parse_response(text, "long")
                self.assertIsNotNone(result)
                script = result["script"]
                self.assertNotIn("===", script)
                self.assertNotIn("SCRIPT", script)
                self.assertNotIn("METADATA", script)
                self.assertNotIn("```", script)
                # Nội dung thật vẫn còn nguyên
                self.assertIn("Xin chào các bạn", script)

    def test_markdown_emphasis_stripped(self):
        text = f"===SCRIPT===\n**Chú ý!** {_BODY}\n===METADATA===\n{_META}"
        result = _parse_response(text, "long")
        self.assertNotIn("**", result["script"])
        self.assertIn("Chú ý!", result["script"])

    def test_empty_response_returns_none(self):
        self.assertIsNone(_parse_response("", "long"))

    def test_delimiter_only_returns_none(self):
        # Chỉ có delimiter, không có nội dung — không được trả script rác
        self.assertIsNone(_parse_response("===SCRIPT===\n===METADATA===", "long"))


if __name__ == "__main__":
    unittest.main()
