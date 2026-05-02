# Phase 6 — Issues Master List

> Analytics & Iteration. 4 Epic, ~13 sub-issues, ~7 ngày.

---

## EPIC #6.1 — Metrics Schema & Pullers

**Loại:** `epic` `phase-6` `backend` `analytics`
**Mô tả:** DB schema + YouTube/TikTok puller.

### Sub-issues

#### `[feat]` Migration `002_metrics_schema.sql`
- **Labels:** `phase-6` `database` `feat`
- **Estimate:** S
- **Mô tả:** Bảng `video_metrics` như mô tả trong detailed doc. Index theo `video_id`, `snapshot_at`.

#### `[feat]` `analytics/youtube_puller.py`
- **Labels:** `phase-6` `backend` `feat` `analytics`
- **Estimate:** L
- **Mô tả:** Pull qua YouTube Analytics API v2. Hỗ trợ 2 channel. Upsert metric snapshot.

#### `[feat]` TikTok CSV parser (giai đoạn 1)
- **Labels:** `phase-6` `backend` `feat` `analytics`
- **Estimate:** M
- **Mô tả:** Bot command `/import_tiktok_csv` nhận file CSV từ TikTok Studio, parse vào DB.

#### `[feat]` TikTok API puller (giai đoạn 2, defer-able)
- **Labels:** `phase-6` `backend` `feat` `defer-able`
- **Estimate:** L
- **Phụ thuộc:** TikTok dev app approval (từ Phase 5)

#### `[infra]` Cron 23h pull metrics
- **Labels:** `phase-6` `infra`
- **Estimate:** S

---

## EPIC #6.2 — KPI Dashboard

**Loại:** `epic` `phase-6` `frontend` `analytics`
**Mô tả:** Streamlit local dashboard.

### Sub-issues

#### `[feat]` Streamlit app skeleton
- **Labels:** `phase-6` `frontend` `feat`
- **Estimate:** M
- **Mô tả:** `dashboard/app.py` với sidebar chọn channel + date range. 4 tab: Overview, Top videos, Format analysis, Cost.

#### `[feat]` Charts: views, sub growth, retention
- **Labels:** `phase-6` `frontend` `feat`
- **Estimate:** M
- **Mô tả:** Line chart 30 ngày gần nhất, bar chart top 10 video.

#### `[feat]` Cost tracking integration
- **Labels:** `phase-6` `frontend` `feat`
- **Estimate:** S
- **Mô tả:** Đọc bảng `cost_logs` (đã ghi từ Phase 3, 4) hiển thị daily total.

---

## EPIC #6.3 — A/B Experiment Helper

**Loại:** `epic` `phase-6` `backend` `experiment`
**Mô tả:** Tag video với experiment_id/arm, compare.

### Sub-issues

#### `[feat]` DB schema cho experiment
- **Labels:** `phase-6` `database` `feat`
- **Estimate:** S
- **Mô tả:** Thêm cột `experiment_id`, `experiment_arm` vào bảng `videos`.

#### `[feat]` `analytics/experiment_compare.py`
- **Labels:** `phase-6` `backend` `feat`
- **Estimate:** M
- **Mô tả:** Hàm `compare_arms(experiment_id)` trả về delta + simple stats (mean, n, t-test p-value).

#### `[task]` Define 3 thí nghiệm đầu
- **Labels:** `phase-6` `experiment` `task`
- **Estimate:** S
- **Mô tả:** Document 3 experiment trong `docs/current/experiments-log.md`: thumbnail style, hook variant, length.

---

## EPIC #6.4 — Weekly Retro Automation

**Loại:** `epic` `phase-6` `backend`
**Mô tả:** Báo cáo tuần tự động qua Telegram.

### Sub-issues

#### `[feat]` `analytics/weekly_retro.py`
- **Labels:** `phase-6` `backend` `feat`
- **Estimate:** M
- **Mô tả:** Generate report đủ 5 mục: top 3, bottom 3, sub growth, cost, action items.
- **Acceptance:**
  - [ ] Report ≤ 1500 ký tự (vừa Telegram message)
  - [ ] Có link tới dashboard cho deep dive

#### `[infra]` Cron Chủ nhật 19h
- **Labels:** `phase-6` `infra`
- **Estimate:** S

---

## Tóm tắt Phase 6

| Epic | Issues | Estimate |
|------|--------|----------|
| #6.1 Metrics & pullers | 5 | ~3 ngày |
| #6.2 Dashboard | 3 | ~2 ngày |
| #6.3 A/B helper | 3 | ~1 ngày |
| #6.4 Weekly retro | 2 | ~1 ngày |
| **Tổng** | **13** | **~7 ngày** |

---

## Sau Phase 6

Phase 6 kết thúc cycle 1.0 của chiến lược. Bước tiếp theo:

1. Đọc 4 tuần dữ liệu thực.
2. Viết `docs/current/strategy-2.1.md` cập nhật giả định nào đúng/sai.
3. Plan Phase 7+ dựa trên insight: có thể là monetization (course/affiliate), hoặc mở thêm trụ cột thứ 3 nếu drama+AI đã ổn định.
