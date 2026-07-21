# Prompt decisions — Drama track (Phase 3)

> Decision log cho các prompt trong `content-pipeline/prompts/drama/`. Cập
> nhật file này mỗi khi tune sang version mới, kèm timestamp + lý do.

## Cách hoạt động

- Mỗi prompt có version riêng: `prompts/drama/{name}.{version}.txt`.
- `config.PROMPT_VERSION` (env var, mặc định `v1`) chọn version dùng cho
  toàn bộ processor. Đổi env var để rollback — không cần sửa code.
- `processors/prompt_loader.py::load_prompt(module, name, version=None)`
  đọc file; `render(template, **values)` điền placeholder dạng `{{KEY}}`.
- A/B test có kiểm soát: `processors/ab_harness.py::choose_version()` chọn
  version theo hash ổn định của `(experiment, story_id)` — không cần
  `config.PROMPT_VERSION` toàn cục nếu muốn so sánh 2 version song song trên
  cùng 1 batch story (xem "A/B harness" bên dưới).

## v1 — 2026-07-06 (khởi tạo)

| Prompt | File | Model | Ghi chú |
|--------|------|-------|---------|
| Rubric scorer | `scorer.v1.txt` | claude-haiku-4-5 | 6 tiêu chí 0/1; `total` luôn được tính lại server-side, không tin số model tự báo cáo |
| Rewriter | `rewriter.v1.txt` | claude-sonnet-4-5 | 9 quy tắc bắt buộc (2 quy tắc thêm so với bản gốc phase-3-detailed.md — xem "Cải tiến so với thiết kế gốc" bên dưới) |
| Theme detection | `theme_detect.v1.txt` | claude-sonnet-4-5 | Chạy weekly, tìm theme ≥3 story |
| Long-form compiler | `longform.v1.txt` | claude-sonnet-4-5 | Gộp 3-5 story cùng theme, target 8-15 phút (~1100-2100 từ, xem `drama_compiler.py`) |

### Cải tiến so với thiết kế gốc (`phase-3-detailed.md`)

`phase-3-detailed.md` mục 5 ("Rủi ro & cảnh báo") liệt kê 2 vấn đề chất
lượng dự kiến sẽ gặp và đề xuất sửa ở v2 sau khi thấy lỗi thực tế:

1. "Hallucination tên VN không tự nhiên... Thêm rule 'tên thuần Việt 2-3 từ'"
2. "Bias dịch sang văn hoá Mỹ... Prompt phải nhấn mạnh 'đặt câu chuyện hoàn
   toàn ở VN, KHÔNG nhắc tới $1 USD, mall, prom, Thanksgiving...'"

Thay vì đợi v2, `rewriter.v1.txt` đã đưa thẳng 2 rule này vào (rule 8, 9)
— không có lý do để đợi lỗi xảy ra rồi mới sửa khi rủi ro đã biết trước.
`processors/drama_rewriter.py::validate_rewrite()` cũng chấm lại bằng
heuristic (blacklist tên Tây phổ biến + từ văn hoá Mỹ) như một lớp phòng vệ
thứ 2 độc lập với prompt.

### Word count — điểm cần lưu ý khi đọc `phase-3-detailed.md`

Tài liệu gốc ghi rewriter script "800-1200 từ (tương đương 60-90 giây TTS)".
Con số 800-1200 từ khớp với **acceptance criteria tường minh** ở
`phase-3-issues.md` EPIC #3.2 ("Generate 10 script... đủ cấu trúc... 800-1200
từ") nên `validate_rewrite()` dùng đúng ngưỡng này. Nhưng "60-90 giây" nhiều
khả năng là lỗi copy-paste: ở tốc độ đọc ~120-160 từ/phút (tốc độ mà chính
codebase này dùng cho video AI dài — xem `video/script_generator.py`,
800-1200 từ ứng với 5-10 PHÚT chứ không phải 60-90 giây). Cấu trúc timing
(Hook 3s → ... → 15s, tổng ~85s) trong tài liệu gốc có vẻ mới là phần bị
nhầm — không sửa số liệu này vì acceptance criteria đã rõ ràng và không có
bằng chứng nào cho biết bên nào đúng; chỉ ghi chú lại ở đây để người tune
prompt sau này không bị nhầm lẫn thêm.

`processors/drama_compiler.py`'s `TARGET_MIN_WORDS`/`TARGET_MAX_WORDS`
(1100-2100 từ cho video long-form 8-15 phút) được suy ra từ tốc độ đọc
~140 từ/phút này — xem comment trong code.

## v2 (rewriter-only) — 2026-07-21 (short 2-3 phút)

`rewriter.v2.txt` — các prompt khác giữ v1. Chọn qua env
`DRAMA_REWRITER_PROMPT_VERSION` (mặc định `v2`, per-prompt) thay vì bump
`PROMPT_VERSION` global (sẽ bắt scorer/theme_detect/longform cũng phải có v2).

Lý do đổi:

1. **Độ dài (yêu cầu chủ kênh):** v1 nhắm 800-1200 từ và ghi chú "tương đương
   60-90 giây TTS" — SAI thực nghiệm: video thật ra ~6 phút, tức giọng drama
   đọc ~210-230 từ/phút. Mục tiêu mới 2-3 phút ⇒ script 250-400 từ,
   commentary 80-120 từ, tổng các phần đọc ~400-550 từ. Dải validate trong
   `config.py` hạ theo (soft 250-400, hard 150-600, commentary min 60).
2. **Cấm lặp hook (rule 7 mới):** model hay mở đầu script bằng chính câu
   hook/title → video đọc tiêu đề 2 lần. v2 cấm tường minh;
   `main_drama.build_narration` vẫn giữ check `_spoken_duplicate()` làm
   enforcement (story cũ + model không nghe lời).
3. `vn_reactions` rút còn 2-3 câu (<60 từ) cho vừa format ngắn.

Rollback format 6 phút: `DRAMA_REWRITER_PROMPT_VERSION=v1` + nới lại
`DRAMA_SCRIPT_*_WORDS`/`DRAMA_COMMENTARY_MIN_WORDS` qua env.

## A/B harness

`processors/ab_harness.py` — thiết kế rút gọn so với `phase-3-issues.md`
(bản gốc muốn hạ tầng traffic-split đầy đủ + bảng `ab_runs` chi tiết):

- `choose_version(experiment, story_id)` — deterministic (hash), không phải
  random thật, để cùng 1 story luôn ra cùng 1 version (nhất quán giữa
  scorer/rewriter, và giữa các lần retry).
- `record_ab_result(experiment, version, story_id, heuristic_score)` — ghi
  vào bảng `ab_runs` (migration 005).
- `compare_ab_results(experiment, min_samples=10)` — so sánh mean
  heuristic_score mỗi version, trả `None` nếu chưa đủ mẫu.

`heuristic_score` cần được tính bởi caller (vd: điểm rubric, hoặc số lỗi từ
`validate_rewrite()` quy đổi ngược thành điểm) — hàm này không tự tính điểm,
chỉ lưu/so sánh những gì được truyền vào.

## Cách tune sang v2 (khi cần)

1. Tạo file mới `prompts/drama/{name}.v2.txt`.
2. Test trên vài chục story bằng `ab_harness.choose_version()` (hoặc set
   `PROMPT_VERSION=v2` tạm thời cho một run thủ công).
3. Ghi lại kết quả (`compare_ab_results()` sau khi đủ mẫu) + quyết định vào
   bảng ở trên (thêm dòng "v2 — <ngày>", giữ lại dòng v1 cũ làm lịch sử).
4. Nếu v2 tốt hơn rõ ràng: đổi `PROMPT_VERSION=v2` trong `.env` để chuyển hẳn.
