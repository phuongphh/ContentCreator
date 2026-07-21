"""Drama rewrite quality test harness (Phase 3 acceptance criteria,
phase-3-detailed.md: "Test harness tests/test_drama_quality.py chấm output
bằng heuristics (số từ, tên VN, structure)").

Unlike tests/test_drama_rewriter.py's TestValidateRewrite (which isolates
one failure mode per test case), this module runs whole realistic-looking
rewrite outputs — the kind processors/drama_rewriter.py actually produces
under the v2 prompt (2-3 minute short: script ~250-400 words, commentary
~80-120) — through validate_rewrite() and checks the aggregate verdict. It's the
fixture set a human would use to sanity-check a new prompt version against
(see docs/current/prompts-decisions.md "Cách tune sang v2").
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from processors.drama_rewriter import validate_rewrite


def _script(paragraphs: list[str], target_words: int) -> str:
    """Pad/repeat `paragraphs` with filler Vietnamese-ish words to hit
    `target_words`, keeping the paragraph breaks (rough structure proxy)."""
    text = "\n\n".join(paragraphs)
    words = text.split()
    if len(words) >= target_words:
        return " ".join(words[:target_words])
    filler_needed = target_words - len(words)
    words.extend(["rồi"] * filler_needed)
    return " ".join(words)


GOOD_REWRITE_1 = {
    "title": "Chị dâu tôi đòi chia tài sản ông bà để lại",
    "hook": "Chưa đầy một tuần sau đám tang, chị dâu đã đòi chia nhà.",
    "script": _script([
        "Tôi tên Nguyễn Thị Lan, năm nay ba mươi hai tuổi, sống ở Hà Đông.",
        "Ông bà tôi mất, để lại căn nhà cho cả gia đình.",
        "Chị dâu tôi, tên Trần Thị Hoa, bắt đầu đòi chia tài sản ngay lập tức.",
        "Cả nhà tôi sốc, không ai nghĩ chị lại làm vậy giữa lúc tang gia bối rối.",
        "Cuối cùng, sự thật về khoản nợ chị giấu kín mới lộ ra.",
    ], target_words=320),
    "vn_commentary": " ".join(
        ["Góc nhìn của tôi là chuyện tài sản trong gia đình Việt luôn nhạy cảm."] * 8
    ),
    "thumbnail_prompt": "shocked Vietnamese woman standing in front of family house, dramatic lighting",
    "tags": ["#giadinh", "#drama", "#chiadau"],
}

GOOD_REWRITE_2 = {
    "title": "Sếp bắt tôi làm việc cuối tuần không trả lương",
    "hook": "Ba tháng liền, tôi làm việc không công vào mỗi cuối tuần.",
    "script": _script([
        "Tôi là Phạm Văn Đức, nhân viên văn phòng tại một công ty ở Quận 1.",
        "Sếp tôi, anh Hùng, liên tục giao thêm việc vào cuối tuần mà không trả thêm lương.",
        "Tôi cắn răng chịu đựng vì sợ mất việc trong lúc kinh tế khó khăn.",
        "Đến khi phát hiện đồng nghiệp khác cũng bị vậy, tôi quyết định lên tiếng.",
        "Kết quả bất ngờ hơn tôi nghĩ rất nhiều.",
    ], target_words=350),
    "vn_commentary": " ".join(
        ["Văn hoá làm thêm giờ không lương ở nhiều công ty Việt Nam vẫn còn phổ biến."] * 7
    ),
    "thumbnail_prompt": "tired Vietnamese office worker at desk late at night, dramatic office lighting",
    "tags": ["#congso", "#drama", "#luong"],
}


BAD_REWRITE_TOO_SHORT = {**GOOD_REWRITE_1, "script": "Một câu chuyện ngắn."}

BAD_REWRITE_WESTERN_NAME = {
    **GOOD_REWRITE_1,
    "title": "Linh Smith và câu chuyện chia tài sản",
}

BAD_REWRITE_US_CULTURE = {
    **GOOD_REWRITE_2,
    "script": GOOD_REWRITE_2["script"] + " chúng tôi gặp nhau ở mall cuối tuần",
}

BAD_REWRITE_SHORT_COMMENTARY = {
    **GOOD_REWRITE_1,
    "vn_commentary": "Quá ngắn.",
}

BAD_REWRITE_MISSING_FIELD = {k: v for k, v in GOOD_REWRITE_1.items() if k != "thumbnail_prompt"}


class TestGoodRewritesPassQualityGate(unittest.TestCase):
    """Realistic, well-formed rewrites must produce zero heuristic issues."""

    def test_good_rewrite_1_passes(self):
        self.assertEqual(validate_rewrite(GOOD_REWRITE_1), [])

    def test_good_rewrite_2_passes(self):
        self.assertEqual(validate_rewrite(GOOD_REWRITE_2), [])


class TestBadRewritesFailQualityGate(unittest.TestCase):
    """Each fixture violates exactly one rule — confirms the harness catches
    every category the doc calls out (số từ, tên VN, structure/culture)."""

    def test_too_short_script_fails(self):
        issues = validate_rewrite(BAD_REWRITE_TOO_SHORT)
        self.assertTrue(issues)
        self.assertTrue(any("word count" in i for i in issues))

    def test_western_name_fails(self):
        issues = validate_rewrite(BAD_REWRITE_WESTERN_NAME)
        self.assertTrue(issues)
        self.assertTrue(any("smith" in i for i in issues))

    def test_us_culture_term_fails(self):
        issues = validate_rewrite(BAD_REWRITE_US_CULTURE)
        self.assertTrue(issues)
        self.assertTrue(any("mall" in i for i in issues))

    def test_short_commentary_fails(self):
        issues = validate_rewrite(BAD_REWRITE_SHORT_COMMENTARY)
        self.assertTrue(issues)
        self.assertTrue(any("vn_commentary" in i for i in issues))

    def test_missing_field_fails(self):
        issues = validate_rewrite(BAD_REWRITE_MISSING_FIELD)
        self.assertTrue(issues)
        self.assertTrue(any("missing/empty" in i for i in issues))


class TestQualityGateSummary(unittest.TestCase):
    """Aggregate pass-rate check, mirroring how a human would eyeball a batch
    of rewrites when tuning a new prompt version (see prompts-decisions.md)."""

    def test_all_good_fixtures_pass_all_bad_fixtures_fail(self):
        good = [GOOD_REWRITE_1, GOOD_REWRITE_2]
        bad = [
            BAD_REWRITE_TOO_SHORT, BAD_REWRITE_WESTERN_NAME, BAD_REWRITE_US_CULTURE,
            BAD_REWRITE_SHORT_COMMENTARY, BAD_REWRITE_MISSING_FIELD,
        ]
        good_pass_rate = sum(1 for r in good if not validate_rewrite(r)) / len(good)
        bad_fail_rate = sum(1 for r in bad if validate_rewrite(r)) / len(bad)
        self.assertEqual(good_pass_rate, 1.0)
        self.assertEqual(bad_fail_rate, 1.0)


if __name__ == "__main__":
    unittest.main()
