# Phase 6 — Analytics & Iteration

> **Mục tiêu:** Đo lường để học. Pull metric từ YouTube/TikTok, dựng KPI dashboard, A/B test thumbnail/hook, weekly retro để quyết định cắt format kém và nhân format tốt.

**Thời lượng dự kiến:** 5–7 ngày (ổn định, không gấp).
**Phụ thuộc:** Phase 5 (cần video đã upload có ID).
**Khoá phase sau:** Không (loop iteration).

---

## 1. Bối cảnh

Sau khi Phase 5 chạy ổn ~2 tuần, bạn sẽ có ~50–100 video đã upload trên 2–3 destination. Phase 6 trả lời các câu hỏi:

- Format nào có retention cao nhất?
- Hook pattern nào CTR tốt nhất?
- Theme drama nào viral nhất?
- Lịch đăng nào tối ưu?

Nguyên tắc: KHÔNG quyết định bằng cảm tính. Chỉ cắt/đổi khi có ≥10 sample của cùng format.

---

## 2. Phạm vi

### Trong phạm vi
- YouTube Analytics API puller.
- TikTok Analytics scraping (manual phase đầu, API phase sau khi có).
- DB schema lưu metrics theo thời gian (snapshot mỗi 24h).
- KPI dashboard: Notion embed hoặc Streamlit local.
- Format A/B helper: tag mỗi video với `experiment_id`, compare.
- Weekly retro template + automation Telegram.

### Ngoài phạm vi
- Predictive model "video này sẽ viral".
- Auto-tuning prompt từ analytics (Phase 7+ nếu có).

---

## 3. Thiết kế kỹ thuật

### 3.1 Metrics schema

```sql
CREATE TABLE video_metrics (
  id INTEGER PRIMARY KEY,
  video_id INTEGER NOT NULL,           -- FK videos
  platform TEXT NOT NULL,              -- 'youtube' | 'tiktok'
  external_id TEXT NOT NULL,           -- youtube_video_id hoặc tiktok_id
  snapshot_at TIMESTAMP NOT NULL,
  views INTEGER,
  likes INTEGER,
  comments INTEGER,
  shares INTEGER,
  watch_time_minutes REAL,
  avg_view_duration_seconds REAL,
  retention_50_pct REAL,               -- % người xem tới giữa video
  ctr REAL,                            -- click-through rate (YouTube)
  UNIQUE(video_id, snapshot_at)
);

CREATE INDEX idx_metrics_video ON video_metrics(video_id);
CREATE INDEX idx_metrics_snapshot ON video_metrics(snapshot_at);
```

Snapshot mỗi 24h trong 30 ngày đầu, sau đó mỗi tuần.

### 3.2 YouTube Analytics puller

```python
# analytics/youtube_puller.py

def pull_metrics_for_channel(channel_key: str, days_back: int = 7):
    youtube_analytics = build("youtubeAnalytics", "v2", credentials=...)
    response = youtube_analytics.reports().query(
        ids=f"channel=={channel_id}",
        startDate=...,
        endDate=...,
        metrics="views,likes,comments,shares,estimatedMinutesWatched,averageViewDuration",
        dimensions="video",
    ).execute()
    
    for row in response["rows"]:
        upsert_metrics(...)
```

Chạy mỗi đêm 23h.

### 3.3 TikTok Analytics

**Giai đoạn 1 (trước khi có API):** export CSV thủ công từ TikTok Studio mỗi tuần, bot Telegram parser CSV vào DB.

**Giai đoạn 2:** TikTok Display API + Insights API (sau khi đăng ký dev app ở Phase 5).

### 3.4 Dashboard

**Lựa chọn 1: Streamlit local (recommend)**

```python
# dashboard/app.py
# Run: streamlit run dashboard/app.py
# Chạy local trên Mac Mini, mở qua browser
```

Trang chính:
- KPI snapshot (sub, view, watch time) cho từng kênh
- Top 10 video tuần này
- Bottom 5 video (cần phân tích)
- Format breakdown (retention theo template)
- Cost tracking (Anthropic + ElevenLabs + Replicate)

**Lựa chọn 2: Notion DB embed** — đẹp hơn nhưng cần auto-sync, phức tạp hơn.

### 3.5 A/B helper

```python
# Mỗi video có thể tag:
videos.experiment_id = "thumbnail_style_v2"
videos.experiment_arm = "A"  # hoặc "B"

def compare_arms(experiment_id: str):
    """So 2 arm sau ≥5 sample/arm. Trả về metric delta + significance."""
```

Bắt đầu với 3 thí nghiệm:
1. **Thumbnail style:** AI illustration vs text overlay phong cách thumbnail.
2. **Hook variant:** statement vs question.
3. **Length:** 60s vs 90s drama.

### 3.6 Weekly Retro

Cron Chủ nhật 19h:

```python
# analytics/weekly_retro.py
def generate_retro_report():
    """
    Output gửi Telegram:
    - Top 3 video tuần (kèm lý do)
    - Bottom 3 video tuần (kèm lý do giả định)
    - Sub growth từng kênh
    - Cost tuần
    - Action items đề xuất (cắt format X, thử format Y)
    """
```

Bot push report → Phuong đọc Chủ nhật tối, vào Monday đã có quyết định.

---

## 4. Acceptance criteria

- [ ] Metrics 30 video đầu được pull thành công.
- [ ] Streamlit dashboard chạy được, mở browser thấy KPI live.
- [ ] Tag được experiment_id cho video, compare 2 arm với ≥5 sample mỗi arm.
- [ ] Weekly retro report tự push Telegram đúng giờ.
- [ ] Cost tracking đúng so với hoá đơn Anthropic + ElevenLabs.

---

## 5. Rủi ro & cảnh báo

- **YouTube Analytics latency:** Metric có độ trễ 24–48h. Đừng compare A/B trước 72h.
- **Sample nhỏ → false signal:** Đừng cắt format chỉ sau 3 video. Quy tắc cứng: ≥10 sample/arm.
- **Vanity metrics:** Đừng chỉ nhìn views. Drama có thể view cao nhưng retention thấp → thuật toán không push tiếp. Ưu tiên `retention_50_pct` và `avg_view_duration_seconds`.
- **Khoá vào dashboard quá sớm:** Tuần đầu không cần dashboard đẹp. Bảng SQL query là đủ. Build dashboard sau khi có data thật.

---

## 6. Liên kết

- Phase trước: [`phase-5-detailed.md`](phase-5-detailed.md)
- Issues: [`phase-6-issues.md`](phase-6-issues.md)
- Sau Phase 6: review chiến lược, viết `strategy-2.1.md`.
