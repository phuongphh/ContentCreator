# Phase 3 — Drama Generation Layer

> **Mục tiêu:** Biến raw story (Reddit hoặc VN seed) thành kịch bản tiếng Việt sẵn-render. Bao gồm: chấm điểm rubric 6 tiêu chí, rewrite Việt hoá có "góc nhìn Việt", và compiler gom 3–5 story thành long-form.

**Thời lượng dự kiến:** 7–10 ngày.
**Phụ thuộc:** Phase 2 (cần story trong DB).
**Khoá phase sau:** Phase 4 (Drama Video Production).

---

## 1. Bối cảnh

Đây là phase "trí tuệ" của Drama track. 3 thành phần chính:

1. **Rubric scorer:** Haiku chấm theo 6 tiêu chí (Hook, Stakes, Twist, Localizable, Comment-bait, Safe).
2. **Drama rewriter:** Sonnet dịch + Việt hoá nhân vật/bối cảnh + thêm 20% "bình luận góc nhìn Việt" để giảm rủi ro reused content.
3. **Drama compiler:** gom 3–5 story cùng theme thành kịch bản long-form 8–15 phút cho YouTube.

Mỗi thành phần có prompt template riêng được lưu version trong `prompts/` để có thể A/B test.

---

## 2. Phạm vi

### Trong phạm vi
- `processors/drama_scorer.py` (Haiku)
- `processors/drama_rewriter.py` (Sonnet)
- `processors/drama_compiler.py` (Sonnet)
- Prompt templates trong `prompts/drama/`
- Test harness so sánh output của 2 prompt version

### Ngoài phạm vi
- TTS / video render (Phase 4)
- AI track migration (Phase 4)
- Chấm chất lượng tự động bằng AI judge (để Phase 6)

---

## 3. Thiết kế kỹ thuật

### 3.1 Drama Scorer (rubric 6 tiêu chí)

**Model:** `claude-haiku-4-5`. Cùng pattern với `ai_scorer.py` hiện tại.

```python
# processors/drama_scorer.py

RUBRIC_PROMPT = """
Bạn là content editor cho kênh YouTube Drama tiếng Việt.
Chấm story sau theo 6 tiêu chí (mỗi tiêu chí 0 hoặc 1):

1. HOOK_3S: Có thể tóm tắt mâu thuẫn trong 1 câu khiến người xem khựng lại không?
2. STAKES: Nhân vật đang mất gì rõ ràng? (tiền, danh dự, mối quan hệ, công việc)
3. TWIST: Có khoảnh khắc 'À HÓA RA' hoặc cú lật ngược không?
4. LOCALIZABLE: Có thể chuyển sang bối cảnh Việt Nam tự nhiên không?
5. COMMENT_BAIT: Khán giả có lý do để bình luận phân định ai sai/đúng không?
6. SAFE: Không có sex/tự sát/bạo lực cụ thể/hate speech?

STORY:
{raw_content}

Trả lời JSON CHỈ: {"hook_3s": 0|1, "stakes": 0|1, "twist": 0|1, "localizable": 0|1, "comment_bait": 0|1, "safe": 0|1, "total": <tổng>, "reason": "<1 câu>"}
"""
```

**Ngưỡng:** `total >= 5/6` mới đưa sang rewriter. Story `safe=0` luôn loại, dù total cao.

### 3.2 Drama Rewriter

**Model:** `claude-sonnet-4-6`. Đây là module quan trọng nhất.

Prompt structure:

```python
SYSTEM_PROMPT = """
Bạn là người kể chuyện cho kênh YouTube/TikTok drama tiếng Việt.
Phong cách: kể chuyện ngôi thứ nhất, ngôn ngữ đời thường, có cảm xúc.
Đối tượng: phụ nữ Việt 22-50 tuổi, nghe khi nấu cơm/đi xe.

Quy tắc bắt buộc:
1. Đổi tên nhân vật sang tên Việt (Linh, Tuấn, Mai, Hùng, Thu...)
2. Đổi địa điểm sang Việt Nam (Quận 1, Hà Đông, chung cư X, công ty Y)
3. Đổi văn hoá đặc thù: mâm cơm Việt, lì xì Tết, sếp người Việt, mẹ chồng nàng dâu, hội bạn nhậu
4. THÊM 1-2 đoạn 'bình luận góc nhìn Việt' chiếm tối thiểu 20% tổng thời lượng - đây là điểm tạo unique value (không có trong Reddit gốc)
5. Cấu trúc: Hook 3s → Setup 12s → Escalation 30s → Twist 25s → Reflection + CTA 15s
6. Tổng độ dài: 800-1200 từ (tương đương 60-90 giây TTS)
7. KHÔNG dùng ngôn từ 18+, không nhắc tên người thật, không vu khống

Output JSON:
{
  "title": "<tiêu đề câu hook 1 dòng>",
  "hook": "<3 giây đầu>",
  "script": "<full script>",
  "vn_commentary": "<đoạn bình luận góc nhìn Việt, riêng>",
  "thumbnail_prompt": "<mô tả ảnh AI cho thumbnail>",
  "tags": ["<tag1>", "<tag2>", ...]
}
"""
```

> Lưu ý: trường `vn_commentary` được lưu RIÊNG để Phase 4 chèn dưới dạng overlay text hoặc segment độc lập trong video — đảm bảo "20% transformative" có thể chứng minh được nếu YouTube khiếu nại.

### 3.3 Drama Compiler (long-form)

Gom 3–5 story cùng theme:

```python
def compile_long_form(stories: list[Story], theme: str) -> CompiledScript:
    """
    Tạo intro tóm tắt 3-5 story + outro CTA + chapter markers.
    Mỗi story chiếm ~2-3 phút trong long-form.
    """
```

Theme detection: dùng Sonnet với prompt "Trong các story sau, theme nào xuất hiện ≥3 lần? Trả về top 1 theme và list story ID phù hợp." Chạy weekly trên Friday.

### 3.4 Prompt versioning

Cấu trúc:
```
prompts/
  drama/
    scorer.v1.txt
    scorer.v2.txt           # cải thiện sau A/B test
    rewriter.v1.txt
    rewriter.v2.txt
    compiler.v1.txt
```

Code load prompt theo `PROMPT_VERSION` trong `config.py` để có thể rollback.

---

## 4. Acceptance criteria

- [ ] `drama_scorer.py` chấm 10 story test, output đúng JSON schema, accuracy ≥80% so với judgment thủ công.
- [ ] `drama_rewriter.py` produce script 800–1200 từ với 5/5 quy tắc bắt buộc.
- [ ] `drama_compiler.py` tạo long-form 8–15 phút có chapter markers chuẩn YouTube.
- [ ] Mỗi prompt có v1 lưu trong `prompts/drama/`.
- [ ] Test harness `tests/test_drama_quality.py` chấm output bằng heuristics (số từ, tên VN, structure).
- [ ] Cost monitor: log token usage mỗi run, tổng < $15 cho 30 story rewrite/ngày.

---

## 5. Rủi ro & cảnh báo

- **Hallucination tên VN không tự nhiên:** Sonnet đôi khi đặt tên nửa Tây nửa Việt ("Linh Smith"). Thêm rule "tên thuần Việt 2-3 từ" và regex check sau output.
- **Bias dịch sang văn hoá Mỹ:** Prompt phải nhấn mạnh "đặt câu chuyện hoàn toàn ở VN, KHÔNG nhắc tới $1 USD, mall, prom, Thanksgiving...".
- **Chi phí trượt:** Mỗi story rewrite ~3000 input + 1500 output token. 30 story/ngày × 30 ngày = ~135M token Sonnet/tháng → ~$15-20. Đặt budget alert.
- **Reuse content YouTube:** Nếu `vn_commentary` quá ngắn hoặc chung chung, vẫn bị flag. Thêm test: `vn_commentary` ≥ 200 từ.

---

## 6. Liên kết

- Phase trước: [`phase-2-detailed.md`](phase-2-detailed.md)
- Phase tiếp theo: [`phase-4-detailed.md`](phase-4-detailed.md)
- Issues: [`phase-3-issues.md`](phase-3-issues.md)
