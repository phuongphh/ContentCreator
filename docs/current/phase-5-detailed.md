# Phase 5 — Distribution & Multi-channel Upload

> **Mục tiêu:** Tự động upload video tới đúng kênh đích (2 YouTube + 1 TikTok) với metadata, thumbnail, lịch đăng. Cuối phase, một video drama có thể đi end-to-end từ Reddit RSS → render → upload mà không cần touch tay (trừ Telegram review gate).

**Thời lượng dự kiến:** 7 ngày.
**Phụ thuộc:** Phase 4 (cần video MP4 sẵn sàng) + Phase 1 (channel registry + OAuth setup).
**Khoá phase sau:** Phase 6 (Analytics).

---

## 1. Bối cảnh

Hiện tại pipeline render xong → file nằm local. Phase 5 thêm:

1. **Telegram review gate** (final gate trước upload) — đã thiết kế sơ ở pipeline cũ, hoàn thiện nốt.
2. **YouTube uploader đa kênh** — chọn kênh dựa vào `destination` của video.
3. **TikTok uploader** — chia 2 giai đoạn: phase đầu manual (export queue, Phuong upload tay), phase sau API.
4. **Scheduler** — thay vì upload ngay lúc render xong, queue theo cadence chuẩn (TikTok 12h/21h, YouTube long-form Chủ nhật 20h, ...).

---

## 2. Phạm vi

### Trong phạm vi
- `notifier/review_bot.py` — bot review video preview qua Telegram, accept/reject.
- `uploaders/youtube_uploader.py` — multi-channel.
- `uploaders/tiktok_uploader_manual.py` — export queue thư mục cho upload tay.
- `uploaders/tiktok_uploader_api.py` — TikTok Content Posting API (có thể defer sang Phase 5.5 nếu khó).
- `scheduler/post_scheduler.py` — queue theo cadence.
- `main_drama.py` orchestrator (Drama track end-to-end).

### Ngoài phạm vi
- Analytics đo hiệu quả sau upload (Phase 6).
- A/B test thumbnail (Phase 6).

---

## 3. Thiết kế kỹ thuật

### 3.1 Telegram Review Gate

```python
# notifier/review_bot.py

# Khi video render xong, gọi:
push_review(video_path, story_id, channel_key)

# Bot gửi:
# - Video preview (compress xuống <50MB)
# - Caption: "Story #X | Kênh: drama_youtube_shorts | Hook: <...>"
# - Inline buttons: ✅ Approve | ❌ Reject | ✏️ Edit metadata
```

Action handlers:
- `approve` → mark `videos.status = 'approved'`, push vào scheduler queue.
- `reject` → mark `'rejected'`, lưu lý do.
- `edit_metadata` → bot hỏi từng field (title, description, tags), update DB.

### 3.2 YouTube Uploader Multi-channel

```python
# uploaders/youtube_uploader.py

def upload_to_youtube(video_id: int, channel_key: str) -> str:
    """
    channel_key: 'ai_youtube' | 'drama_youtube'
    Returns: youtube_video_id sau upload thành công.
    """
    channel = get_channel(channel_key)
    token_path = f"tokens/{channel_key}.json"
    creds = load_oauth_credentials(token_path)
    
    youtube = build("youtube", "v3", credentials=creds)
    
    request = youtube.videos().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": video.title,
                "description": video.description,
                "tags": video.tags,
                "categoryId": "22" if channel.track == "ai" else "24",  # Education vs Entertainment
            },
            "status": {"privacyStatus": video.privacy or "public"},
        },
        media_body=MediaFileUpload(video.file_path, chunksize=8*1024*1024, resumable=True),
    )
    response = execute_with_retry(request)
    
    # Upload thumbnail riêng nếu có
    if video.thumbnail_path:
        youtube.thumbnails().set(videoId=response["id"], media_body=MediaFileUpload(video.thumbnail_path)).execute()
    
    return response["id"]
```

**OAuth token storage:** mỗi channel 1 file JSON riêng tại `tokens/{channel_key}.json`. Refresh token tự động khi expire. Document quy trình initial OAuth ở `docs/current/oauth-setup.md` (đã tạo ở Phase 1).

### 3.3 TikTok Uploader

**Giai đoạn 1 — Manual export (tuần đầu):**

```python
# uploaders/tiktok_uploader_manual.py

def export_for_manual_upload(video_id: int) -> Path:
    """
    Copy video + caption + tags vào thư mục queue_tiktok/YYYY-MM-DD/.
    Tạo file .txt kèm chứa caption + hashtag để Phuong copy nhanh.
    """
```

Phuong mở thư mục mỗi sáng, upload tay 5–10 phút. Đỡ phụ thuộc API trong giai đoạn không ổn định.

**Giai đoạn 2 — TikTok API:**

TikTok Content Posting API (release 2024) cho phép upload trực tiếp. Yêu cầu:
- TikTok Developer App với product "Content Posting API"
- OAuth flow: user grant `video.upload` scope
- Upload theo 3 bước: init → upload chunks → publish

> Đặt riêng thành issue defer-able. Nếu blocker (chưa được approve API), giữ manual.

### 3.4 Scheduler

```python
# scheduler/post_scheduler.py

CADENCE = {
    "drama_youtube_shorts": ["12:00", "21:00"],
    "drama_youtube_long":   ["sunday 20:00"],
    "ai_youtube_shorts":    ["12:00"],
    "ai_youtube_long":      ["tuesday 19:00", "saturday 19:00"],
    "tiktok_drama":         ["12:00", "21:00"],
    "tiktok_ai":            ["19:00"],
}

def schedule_video(video_id: int, channel_key: str):
    """Queue video cho slot tiếp theo theo CADENCE."""

def run_scheduler():
    """Chạy mỗi 5 phút, nếu có slot ≤ now → trigger upload."""
```

> Có thể dùng cron đơn giản (mỗi 5 phút) hoặc APScheduler trong process Python dài hạn.

### 3.5 Orchestrator

```python
# main_drama.py

def run_daily():
    # 1. Collect (Reddit + VN seed): đã có Phase 2
    # 2. Score (Haiku): Phase 3
    # 3. Rewrite (Sonnet): Phase 3
    # 4. Render (FFmpeg): Phase 4
    # 5. Push review: Phase 5
    # 6. Khi approved → schedule upload: Phase 5
```

Mỗi bước commit progress vào DB để có thể resume nếu crash giữa chừng.

---

## 4. Acceptance criteria

- [ ] Review bot push được preview, accept/reject hoạt động.
- [ ] Upload thành công 1 video lên kênh YouTube AI test.
- [ ] Upload thành công 1 video lên kênh YouTube Drama test.
- [ ] Manual queue thư mục có 5 video sẵn cho TikTok.
- [ ] Scheduler tự động trigger upload đúng giờ ±5 phút.
- [ ] Resume từ crash: chạy lại pipeline không upload lại video đã upload.

---

## 5. Rủi ro & cảnh báo

- **YouTube quota:** Data API v3 default quota 10.000 unit/ngày. 1 upload = ~1.600 unit. Tức tối đa ~6 video/ngày/project. Nếu cần nhiều hơn, request quota tăng (free) hoặc tách 2 Google Cloud Project (1 cho mỗi kênh).
- **OAuth token expire:** Refresh token YouTube không có thời hạn nhưng có thể bị revoke nếu user đổi password Gmail. Có job kiểm tra token hợp lệ mỗi tuần, alert nếu fail.
- **TikTok API approval chậm:** Có thể mất 2–4 tuần. Plan giai đoạn manual để không bị block.
- **Upload trùng:** Nếu pipeline bị restart giữa upload, có thể tạo 2 video trên YouTube. Lưu `youtube_video_id` ngay khi upload thành công + check trước khi upload lại.

---

## 6. Liên kết

- Phase trước: [`phase-4-detailed.md`](phase-4-detailed.md)
- Phase tiếp theo: [`phase-6-detailed.md`](phase-6-detailed.md)
- Issues: [`phase-5-issues.md`](phase-5-issues.md)
