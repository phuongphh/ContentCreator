"""Tests for video.text_preprocessor — number/symbol normalization for TTS."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import video.text_preprocessor as tp
from video.text_preprocessor import (
    preprocess_for_tts,
    _int_to_vi_fallback,
    _decimal_to_vi,
)


class TestIntToViFallback(unittest.TestCase):
    """The internal fallback converter (used when num2words is absent)."""

    def test_units(self):
        self.assertEqual(_int_to_vi_fallback(0), "không")
        self.assertEqual(_int_to_vi_fallback(7), "bảy")

    def test_teens(self):
        self.assertEqual(_int_to_vi_fallback(15), "mười lăm")

    def test_tens_special_cases(self):
        self.assertEqual(_int_to_vi_fallback(20), "hai mươi")
        self.assertEqual(_int_to_vi_fallback(21), "hai mươi mốt")  # "mốt"
        self.assertEqual(_int_to_vi_fallback(25), "hai mươi lăm")  # "lăm"

    def test_hundreds(self):
        self.assertEqual(_int_to_vi_fallback(100), "một trăm")
        self.assertIn("lẻ", _int_to_vi_fallback(105))  # "một trăm lẻ năm"

    def test_thousands(self):
        self.assertIn("nghìn", _int_to_vi_fallback(1000))

    def test_millions(self):
        self.assertIn("triệu", _int_to_vi_fallback(1_000_000))

    def test_negative(self):
        self.assertTrue(_int_to_vi_fallback(-5).startswith("âm"))


class TestDecimalToVi(unittest.TestCase):
    def test_simple_decimal(self):
        self.assertEqual(_decimal_to_vi("1.5"), "một phẩy năm")

    def test_reads_each_decimal_digit(self):
        # 3.14 -> "... phẩy một bốn" (digit-by-digit)
        self.assertIn("phẩy một bốn", _decimal_to_vi("3.14"))

    def test_integer_part_only(self):
        self.assertNotIn("phẩy", _decimal_to_vi("42"))


class TestPreprocessForTts(unittest.TestCase):
    """End-to-end normalization cases (mirrors the module's manual cases)."""

    CASES = [
        ("100 người dùng", "một trăm người dùng"),
        ("tăng 1.5 lần", "một phẩy năm"),
        ("năm 2024", "hai nghìn"),
        ("50% người dùng", "năm mươi phần trăm"),
        ("$10 mỗi tháng", "mười đô la"),
        ("tiết kiệm $1,000", "một nghìn đô la"),
        ("thứ 3 trong tuần", "thứ ba"),
        ("có 1,000,000 người", "một triệu"),
        ("tốc độ 3.14 lần", "ba phẩy một bốn"),
        ("5-7% tăng trưởng", "năm đến bảy phần trăm"),
        ("10-15 phút", "mười đến mười lăm"),
        ("3-5 triệu đồng", "ba đến năm triệu"),
        ("1.5-2.5 lần", "một phẩy năm đến hai phẩy năm"),
        ("GPT-4 là model", "GPT-bốn"),
    ]

    def test_all_cases(self):
        for text_in, expected in self.CASES:
            with self.subTest(text=text_in):
                self.assertIn(expected, preprocess_for_tts(text_in))

    def test_empty_string(self):
        self.assertEqual(preprocess_for_tts(""), "")

    def test_none_is_passed_through(self):
        self.assertIsNone(preprocess_for_tts(None))

    def test_text_without_numbers_unchanged(self):
        text = "Hôm nay trời rất đẹp và mát mẻ."
        self.assertEqual(preprocess_for_tts(text), text)

    def test_no_digits_remain_for_plain_numbers(self):
        # After preprocessing a plain sentence, raw ASCII digits should be gone.
        result = preprocess_for_tts("Có 100 công cụ và 50 người dùng")
        self.assertFalse(any(c.isdigit() for c in result))

    def test_percent_range_takes_priority_over_plain_range(self):
        # "5-7%" must become a percent range, not "năm đến bảy" + stray "%".
        result = preprocess_for_tts("khoảng 5-7% mỗi năm")
        self.assertIn("năm đến bảy phần trăm", result)
        self.assertNotIn("%", result)


class TestStripNonspeechArtifacts(unittest.TestCase):
    """strip_nonspeech_artifacts — gỡ delimiter/markdown lọt từ LLM output.

    Defense-in-depth: preprocess_for_tts gọi hàm này đầu tiên nên MỌI đường
    TTS (track AI + Drama, kể cả script cũ đã lưu DB) đều được bảo vệ.
    """

    def test_script_delimiter_removed(self):
        text = "===SCRIPT===\nXin chào các bạn."
        result = tp.strip_nonspeech_artifacts(text)
        self.assertNotIn("===", result)
        self.assertNotIn("SCRIPT", result)
        self.assertIn("Xin chào các bạn.", result)

    def test_metadata_delimiter_removed(self):
        result = tp.strip_nonspeech_artifacts("Nội dung chính.\n===METADATA===")
        self.assertNotIn("METADATA", result)
        self.assertIn("Nội dung chính.", result)

    def test_delimiter_with_vietnamese_label_removed(self):
        result = tp.strip_nonspeech_artifacts("=== TIN NÓNG 1 ===\nOpenAI ra mắt.")
        self.assertNotIn("===", result)
        self.assertIn("OpenAI ra mắt.", result)

    def test_code_fence_removed(self):
        result = tp.strip_nonspeech_artifacts("```json\nNội dung.\n```")
        self.assertNotIn("```", result)
        self.assertIn("Nội dung.", result)

    def test_markdown_heading_and_emphasis_removed(self):
        result = tp.strip_nonspeech_artifacts("## Mở đầu\n**Quan trọng:** nghe kỹ.")
        self.assertNotIn("#", result)
        self.assertNotIn("**", result)
        self.assertIn("Quan trọng: nghe kỹ.", result)

    def test_decor_line_removed(self):
        result = tp.strip_nonspeech_artifacts("Đoạn một.\n---\nĐoạn hai.")
        self.assertNotIn("---", result)
        self.assertIn("Đoạn một.", result)
        self.assertIn("Đoạn hai.", result)

    def test_plain_speech_untouched(self):
        text = "Hôm nay trời rất đẹp. GPT-4 và Claude đều mạnh - thật đấy."
        self.assertEqual(tp.strip_nonspeech_artifacts(text), text)

    def test_empty_and_none_passthrough(self):
        self.assertEqual(tp.strip_nonspeech_artifacts(""), "")
        self.assertIsNone(tp.strip_nonspeech_artifacts(None))

    def test_preprocess_for_tts_strips_artifacts_first(self):
        # Tích hợp: đường TTS thật phải vừa gỡ delimiter vừa đổi số thành chữ
        result = preprocess_for_tts("===SCRIPT===\nCó 100 công cụ AI mới.")
        self.assertNotIn("===", result)
        self.assertNotIn("SCRIPT", result)
        self.assertIn("một trăm công cụ", result)


class TestFallbackPath(unittest.TestCase):
    """Force the no-num2words code path to ensure the fallback still works."""

    def setUp(self):
        self._orig = tp._HAS_NUM2WORDS
        tp._HAS_NUM2WORDS = False

    def tearDown(self):
        tp._HAS_NUM2WORDS = self._orig

    def test_fallback_converts_integers(self):
        self.assertIn("một trăm", preprocess_for_tts("100 đồng"))

    def test_fallback_converts_decimals(self):
        self.assertIn("một phẩy năm", preprocess_for_tts("1.5 lần"))


if __name__ == "__main__":
    unittest.main()
