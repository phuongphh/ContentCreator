# Experiments Log — A/B thí nghiệm nội dung (Phase 6)

> Nhật ký các thí nghiệm A/B chạy trên video đã đăng. Mỗi video được gắn
> `experiment_id` + `experiment_arm` (cột trên bảng `videos`, migration 007)
> và so sánh bằng `analytics/experiment_compare.py`
> (`compare_arms(experiment_id, metric)`).

## Nguyên tắc (đọc trước khi kết luận)

- **Mẫu tối thiểu:** ≥5 video/arm để xem xu hướng (acceptance criteria); ≥10
  video/arm trước khi thực sự cắt/nhân format (phase-6-detailed.md §5). Công cụ
  báo `enough_samples` / `recommended_samples_met` tương ứng.
- **Độ trễ:** metric YouTube trễ 24–48h — đừng so sánh trước 72h sau khi đăng.
- **Metric ưu tiên:** `retention_50_pct` và `avg_view_duration_seconds` quan
  trọng hơn `views` (view cao mà retention thấp → thuật toán không đẩy tiếp).
  So sánh cả hai, đừng chỉ nhìn views.
- **1 biến / thí nghiệm:** mỗi experiment chỉ đổi ĐÚNG một yếu tố; giữ mọi thứ
  khác như nhau giữa 2 arm.

## Cách gắn video vào thí nghiệm

```python
from storage.database import set_video_experiment
set_video_experiment(video_id, "thumbnail_style_v1", "A")  # hoặc "B"
```

So sánh:

```bash
python -m analytics.experiment_compare thumbnail_style_v1 --metric retention_50_pct
```

---

## Thí nghiệm #1 — Thumbnail style

| | |
|---|---|
| `experiment_id` | `thumbnail_style_v1` |
| Arm A | Thumbnail = ảnh minh hoạ AI (Replicate illustration) |
| Arm B | Thumbnail = text overlay lớn phong cách "reaction/drama" |
| Giả thuyết | Text overlay CTR cao hơn với khán giả lướt feed |
| Metric chính | `views` (proxy CTR khi impressions ngang nhau), phụ: `retention_50_pct` |
| Trạng thái | 🔲 Chưa bắt đầu |
| Kết luận | — |

## Thí nghiệm #2 — Hook variant

| | |
|---|---|
| `experiment_id` | `hook_variant_v1` |
| Arm A | Hook dạng **khẳng định** ("Sếp tôi đã làm điều không tưởng...") |
| Arm B | Hook dạng **câu hỏi** ("Bạn sẽ làm gì nếu sếp...?") |
| Giả thuyết | Câu hỏi giữ chân 3s đầu tốt hơn |
| Metric chính | `retention_50_pct`, phụ: `avg_view_duration_seconds` |
| Trạng thái | 🔲 Chưa bắt đầu |
| Kết luận | — |

## Thí nghiệm #3 — Độ dài (Drama Shorts)

| | |
|---|---|
| `experiment_id` | `length_drama_v1` |
| Arm A | 60s |
| Arm B | 90s |
| Giả thuyết | 60s hoàn thành video (retention tới cuối) cao hơn, đổi lại ít
| | chiều sâu; đo xem đánh đổi có đáng không |
| Metric chính | `retention_50_pct` + `views` |
| Trạng thái | 🔲 Chưa bắt đầu |
| Kết luận | — |

---

## Liên kết

- Thiết kế: [`phase-6-detailed.md`](phase-6-detailed.md) §3.5
- Công cụ so sánh: `analytics/experiment_compare.py`
- Thống kê (Welch t-test, không cần scipy): `analytics/stats.py`
