# Phase 3 — Issues Master List

> Drama Generation Layer. 4 Epic, ~16 sub-issues, ~10 ngày.

---

## EPIC #3.1 — Rubric Scorer (Haiku)

**Loại:** `epic` `phase-3` `backend` `ai`
**Mô tả:** Module chấm story theo 6 tiêu chí Hook/Stakes/Twist/Localizable/Comment-bait/Safe.

**Definition of Done:**
- Chấm 10 story test với accuracy ≥80%.
- Cost <$0.5 cho 100 story.

### Sub-issues

#### `[feat]` Tạo `processors/drama_scorer.py` skeleton
- **Labels:** `phase-3` `backend` `feat` `drama`
- **Estimate:** S
- **Mô tả:** File với hàm `score_story(story_id) -> dict`. Đọc story từ DB, gọi Haiku, lưu lại score.

#### `[feat]` Viết prompt `prompts/drama/scorer.v1.txt`
- **Labels:** `phase-3` `prompt` `drama`
- **Estimate:** M
- **Mô tả:** Prompt như trong `phase-3-detailed.md` mục 3.1. Có placeholder `{raw_content}`.
- **Acceptance:**
  - [ ] Prompt được commit như file riêng (không hard-code trong .py)

#### `[feat]` Implement Haiku call + JSON parse + validation
- **Labels:** `phase-3` `backend` `feat`
- **Estimate:** M
- **Phụ thuộc:** 2 issue trên
- **Mô tả:** Dùng `anthropic` SDK. Validate JSON schema bằng pydantic. Retry 1 lần nếu parse fail.

#### `[feat]` Lưu rubric score vào `stories` (cập nhật field)
- **Labels:** `phase-3` `database` `feat`
- **Estimate:** S
- **Mô tả:** Update `stories.rubric_score` (tổng điểm) + thêm bảng `story_rubrics` chi tiết 6 cột nếu cần debug.

#### `[test]` Test harness 10 story manual labeled
- **Labels:** `phase-3` `test`
- **Estimate:** M
- **Mô tả:** Bạn chấm tay 10 story → so với output Haiku → tính accuracy. Nếu <80% → tune prompt v2.

---

## EPIC #3.2 — Drama Rewriter (Sonnet)

**Loại:** `epic` `phase-3` `backend` `ai` `drama`
**Mô tả:** Module Việt hoá story Reddit, thêm bình luận góc nhìn Việt 20%.

**Definition of Done:**
- Generate 10 script chạy được, đủ cấu trúc Hook→Setup→Escalation→Twist→Reflection.
- Tên/địa điểm thuần Việt 100%.
- `vn_commentary` ≥ 200 từ.

### Sub-issues

#### `[feat]` Viết prompt `prompts/drama/rewriter.v1.txt`
- **Labels:** `phase-3` `prompt` `drama`
- **Estimate:** L
- **Mô tả:** Prompt như mục 3.2 trong detailed doc. Có 7 quy tắc bắt buộc + JSON output schema.

#### `[feat]` Tạo `processors/drama_rewriter.py`
- **Labels:** `phase-3` `backend` `feat` `drama`
- **Estimate:** M
- **Mô tả:** Hàm `rewrite_story(story_id) -> RewriteResult`. Output lưu vào `stories.rewritten_content` (JSON string với title, hook, script, vn_commentary, thumbnail_prompt, tags).

#### `[feat]` Validation post-rewrite
- **Labels:** `phase-3` `backend` `feat`
- **Estimate:** M
- **Mô tả:** Sau khi Sonnet trả về, check:
  - Tên nhân vật khớp regex tên VN (whitelist)
  - Không xuất hiện từ ngoại lai phổ biến (`mall`, `prom`, `dollars`...)
  - Word count 800–1200
  - `vn_commentary` ≥ 200 từ
  Nếu fail → mark `status='needs_review'`, push Telegram alert.

#### `[feat]` Cost & token tracking
- **Labels:** `phase-3` `backend` `observability`
- **Estimate:** S
- **Mô tả:** Log mỗi call: input tokens, output tokens, cost. Daily aggregate.

#### `[test]` Sanity test 10 story Reddit
- **Labels:** `phase-3` `test`
- **Estimate:** M
- **Mô tả:** Chạy rewriter trên 10 story đã có, đọc tay từng cái, đánh giá quality theo checklist:
  - [ ] Tên VN tự nhiên
  - [ ] Bối cảnh VN consistent
  - [ ] Hook có "twist seed"
  - [ ] vn_commentary có giá trị, không chung chung

---

## EPIC #3.3 — Drama Compiler (long-form)

**Loại:** `epic` `phase-3` `backend` `drama`
**Mô tả:** Gom 3–5 story cùng theme thành script long-form 8–15 phút cho YouTube.

### Sub-issues

#### `[feat]` Theme detection job (weekly)
- **Labels:** `phase-3` `backend` `feat`
- **Estimate:** M
- **Mô tả:** Job chạy mỗi Thứ 6, lấy story `status='produced'` trong tuần, prompt Sonnet tìm top 1 theme + danh sách story phù hợp.

#### `[feat]` Compiler script generator
- **Labels:** `phase-3` `backend` `feat`
- **Estimate:** L
- **Mô tả:** Nhận list story + theme, sinh:
  - Intro 15s teaser (giới thiệu 3-5 story sẽ kể)
  - Bridge text giữa các story
  - Outro CTA
  - Chapter markers theo định dạng YouTube (`00:00 Intro\n00:30 Story 1...`)
- **Acceptance:**
  - [ ] Output có chapter markers đúng format YouTube
  - [ ] Tổng độ dài 8–15 phút (tính theo số từ)

#### `[feat]` Lưu compiled script vào bảng `compiled_videos`
- **Labels:** `phase-3` `database` `feat`
- **Estimate:** S
- **Mô tả:** Bảng mới chứa long-form script + metadata + danh sách story_id thành phần.

---

## EPIC #3.4 — Prompt versioning & A/B harness

**Loại:** `epic` `phase-3` `backend` `infra`
**Mô tả:** Hạ tầng để rollback prompt và A/B test.

### Sub-issues

#### `[feat]` Prompt loader theo version
- **Labels:** `phase-3` `backend` `feat`
- **Estimate:** M
- **Mô tả:** Đọc `PROMPT_VERSION` từ env, load file `prompts/{module}/{name}.{version}.txt`.

#### `[feat]` A/B harness
- **Labels:** `phase-3` `backend` `feat` `experiment`
- **Estimate:** L
- **Mô tả:** Chia traffic 50/50 giữa 2 version, lưu kết quả riêng vào bảng `ab_runs`. Compare bằng heuristic score sau 10 sample.

#### `[docs]` Tài liệu prompt v1 + decision log
- **Labels:** `phase-3` `docs`
- **Estimate:** S
- **Mô tả:** File `docs/current/prompts-decisions.md` ghi quyết định prompt v1 + timestamp. Sau này update khi tune.

---

## Tóm tắt Phase 3

| Epic | Issues | Estimate |
|------|--------|----------|
| #3.1 Rubric Scorer | 5 | ~3 ngày |
| #3.2 Drama Rewriter | 5 | ~4 ngày |
| #3.3 Drama Compiler | 3 | ~2 ngày |
| #3.4 Prompt versioning | 3 | ~1 ngày |
| **Tổng** | **16** | **~10 ngày** |
