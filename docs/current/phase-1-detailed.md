# Phase 1 — Multi-channel Foundation

> **Mục tiêu:** Tái cấu trúc repo + branding 2 kênh YouTube và 1 TikTok để pipeline có thể chạy multi-track (AI + Drama) và multi-destination. Đây là phase "đặt khung", chưa code logic mới của Drama.

**Thời lượng dự kiến:** 5–7 ngày làm việc.
**Phụ thuộc trước đó:** Không (entry phase).
**Khoá các phase sau:** Phase 2, 3, 4, 5 đều cần config đa kênh ở phase này.

---

## 1. Bối cảnh

Repo hiện tại (`content-pipeline/`) được thiết kế cho **một** kênh AI duy nhất. Strategy 2.0 yêu cầu hỗ trợ:

- **2 kênh YouTube độc lập**: `ai_youtube` (AI/Tech cho dân văn phòng) và `drama_youtube` (Drama/Twist).
- **1 tài khoản TikTok mix** với 2 series hashtag riêng.
- **Pipeline chia track**: mọi item dữ liệu phải mang `track ∈ {ai, drama}` và `destination ∈ {ai_youtube, drama_youtube, tiktok}` để route đúng.

Phase 1 chỉ làm 3 việc: (a) tách config theo channel, (b) thêm trường `track` xuyên suốt code base, (c) tạo branding asset cho 2 kênh + TikTok.

---

## 2. Phạm vi

### Trong phạm vi (In-scope)

- Refactor `config.py` thành cấu hình đa kênh (channel registry).
- Cập nhật DB schema để thêm cột `track`, `destination` vào bảng `items`/`stories`.
- Tạo branding pack (logo, banner, channel description, link tree) cho 2 kênh YouTube và 1 TikTok.
- Đăng ký 2 kênh YouTube trên 2 Gmail riêng (không dùng chung tài khoản chính).
- Thiết lập Brand Account trên YouTube để uỷ quyền OAuth sau này.
- Tạo `.env.example` mới có biến cho từng kênh (`YOUTUBE_AI_TOKEN`, `YOUTUBE_DRAMA_TOKEN`, ...).
- Cập nhật `CLAUDE.md` để Claude Code hiểu kiến trúc mới.

### Ngoài phạm vi (Out-of-scope)

- Logic Drama (để Phase 2–3).
- Logic upload đa kênh (để Phase 5).
- Bất kỳ test data Drama thực tế nào.

---

## 3. Thiết kế kỹ thuật

### 3.1 Channel Registry

Tạo `content-pipeline/channels.py`:

```python
# Channel registry - source of truth cho mọi destination
CHANNELS = {
    "ai_youtube": {
        "platform": "youtube",
        "track": "ai",
        "name": "AI Đi Làm",                   # placeholder, user chốt
        "format_long": True,
        "format_shorts": True,
        "oauth_token_env": "YOUTUBE_AI_TOKEN",
        "tts_voice_profile": "neutral_female",
    },
    "drama_youtube": {
        "platform": "youtube",
        "track": "drama",
        "name": "Chuyện Đời",                  # placeholder
        "format_long": True,
        "format_shorts": True,
        "oauth_token_env": "YOUTUBE_DRAMA_TOKEN",
        "tts_voice_profile": "storyteller_female",
    },
    "tiktok_main": {
        "platform": "tiktok",
        "track": "mixed",                      # cả 2 track đăng cùng tài khoản
        "name": "@phuong.contentlab",          # placeholder
        "format_long": False,
        "format_shorts": True,
        "oauth_token_env": "TIKTOK_TOKEN",
        "tts_voice_profile": "auto",           # chọn theo track của video
    },
}

def get_channel(key: str) -> dict:
    if key not in CHANNELS:
        raise ValueError(f"Channel {key} not in registry")
    return CHANNELS[key]
```

> Lý do tách registry: mọi module sau (uploader, scheduler, analytics) đều `import channels` thay vì hard-code. Khi đổi tên kênh hoặc thêm kênh mới, chỉ sửa 1 chỗ.

### 3.2 DB schema migration

Bảng `items` (thay đổi):

```sql
ALTER TABLE items ADD COLUMN track TEXT NOT NULL DEFAULT 'ai';
ALTER TABLE items ADD COLUMN destination TEXT;     -- NULL nếu chưa quyết định kênh
CREATE INDEX idx_items_track ON items(track);
CREATE INDEX idx_items_destination ON items(destination);
```

Bảng mới `stories` (chuẩn bị cho Drama Phase 2):

```sql
CREATE TABLE stories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,           -- 'reddit', 'vn_original', 'manual'
  source_id TEXT,                 -- Reddit post_id, hoặc UUID cho VN
  raw_content TEXT,
  rewritten_content TEXT,
  track TEXT NOT NULL,            -- 'drama' (sau này có thể mở rộng)
  rubric_score INTEGER,
  status TEXT DEFAULT 'pending',  -- pending, approved, rejected, produced
  destination TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  produced_at TIMESTAMP
);
```

Migration script: `content-pipeline/storage/migrations/001_multi_track.sql`.

### 3.3 Branding asset

Mỗi kênh cần:

| Asset | YouTube AI | YouTube Drama | TikTok |
|-------|------------|---------------|--------|
| Avatar 800×800 | ✅ | ✅ | ✅ |
| Banner 2560×1440 | ✅ | ✅ | — |
| Channel description | ✅ | ✅ | ✅ |
| Link in bio (Linktree/Beacons) | ✅ | ✅ | ✅ |
| Watermark/end-screen template | ✅ | ✅ | — |

> Avatar có thể tạo bằng Midjourney/Ideogram nếu chưa có designer. Banner tối thiểu cần bản 1 màu + tên kênh + tagline.

---

## 4. Acceptance criteria

- [ ] `channels.py` tồn tại, có ≥3 entry, có `get_channel()` raise nếu key sai.
- [ ] DB migration 001 chạy được idempotent (chạy 2 lần không lỗi).
- [ ] `config.py` không còn hard-code kênh nào, mọi tham chiếu đi qua `channels.py`.
- [ ] 2 kênh YouTube đã tạo, có branding tối thiểu (avatar + banner + description).
- [ ] 1 TikTok account đã tạo + bio có đủ 2 hashtag series.
- [ ] `CLAUDE.md` cập nhật mục "Kiến trúc thư mục" để phản ánh `track`/`destination`.
- [ ] `.env.example` có placeholder cho `YOUTUBE_AI_TOKEN`, `YOUTUBE_DRAMA_TOKEN`, `TIKTOK_TOKEN`.
- [ ] Tất cả test cũ vẫn pass (track mặc định = `ai`, không break logic hiện tại).

---

## 5. Rủi ro & cảnh báo

- **Đổi tên kênh sau khi đã chạy**: YouTube cho phép đổi tên 3 lần/90 ngày. Đặt tên cẩn thận ngay từ đầu, hoặc dùng tên placeholder ngắn rồi đổi 1 lần khi chốt.
- **Token Brand Account**: Brand Account khác Personal Channel. Khi OAuth, phải chọn đúng Brand. Document rõ trong README.
- **Migration đồng thời với code mới**: Triển khai theo thứ tự (1) chạy migration trên dev, (2) merge code dùng trường mới, (3) chạy migration trên prod, (4) deploy. Không bao giờ ngược lại.

---

## 6. Liên kết

- Strategy doc: `docs/current/strategy.md`
- Issues master file: `docs/current/phase-1-issues.md`
- Phase tiếp theo: [`phase-2-detailed.md`](phase-2-detailed.md)
