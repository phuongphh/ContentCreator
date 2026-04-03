# Issue #19

[Feature] Dual Content Format — Short bulletin (Mon/Wed/Fri) & Long bulletin (Tue/Thu/Sat)

## Overview
Implement two distinct content formats for the ContentCreator pipeline to support multi-platform publishing:
- **Short bulletin** — published to **YouTube Shorts** & **TikTok** on Mon / Wed / Fri
- **Long bulletin** — published to **YouTube** on Tue / Thu / Sat

> ⚠️ **Giữ nguyên luồng phê duyệt qua Telegram hiện tại** — pipeline KHÔNG tự động publish. Mọi nội dung phải được gửi qua **Bé MC** để admin phê duyệt trước khi publish.

---

## Requirements

### 1. Short Bulletin (YouTube Shorts & TikTok)
**Schedule:** Every Monday, Wednesday, Friday

**Content characteristics:**
- Duration: 60–90 seconds (vertical video format 9:16)
- Format: Fast-paced, hook in first 3 seconds
- Structure:
  - Intro hook (3–5 giây): câu dẫn gây chú ý
  - Top 3 tin nổi bật trong ngày (mỗi tin 15–20 giây)
  - CTA cuối (5 giây): Subscribe để xem bản tin đầy đủ
- Script length: ~150–200 words
- Platforms: YouTube Shorts + TikTok (same video, same format)

---

### 2. Long Bulletin (YouTube)
**Schedule:** Every Tuesday, Thursday, Saturday

**Content characteristics:**
- Duration: 5–10 phút (horizontal video format 16:9)
- Format: In-depth, structured news bulletin
- Structure:
  - Intro & agenda (30 giây)
  - Top 5–7 tin chính (mỗi tin 45–90 giây)
  - Phân tích/bình luận ngắn (1–2 phút)
  - Outro + CTA (30 giây): Like, Subscribe, bật thông báo
- Script length: ~800–1200 words
- Platform: YouTube only

---

## Pipeline Changes

### Scheduler
- Detect day of week at pipeline start
- Monday / Wednesday / Friday → trigger **short bulletin** workflow
- Tuesday / Thursday / Saturday → trigger **long bulletin** workflow
- Sunday → no publish (rest day)

### Script Generator
- Add  parameter:  | 
- Short format: extract top 3 headlines, generate concise script
- Long format: extract top 5–7 headlines, generate detailed script with analysis

### Video Renderer
- Short: render in **9:16 vertical** format (1080x1920), max 90s
- Long: render in **16:9 horizontal** format (1920x1080), 5–10 min

### Telegram Approval Flow (Giữ nguyên — KHÔNG thay đổi)
Luồng phê duyệt hiện tại phải được bảo toàn hoàn toàn:

1. **Bước 1 — Gửi article text:** Pipeline gửi nội dung bài viết (script) qua **Bé MC** trên Telegram để admin review
2. **Bước 2 — Gửi video:** Sau khi render xong, video được gửi qua **Bé MC** trên Telegram
3. **Bước 3 — Chờ phê duyệt:** Pipeline dừng lại, chờ admin bấm **Approve**
4. **Bước 4 — Publish:** Chỉ sau khi nhận được approval mới publish lên YouTube / TikTok

> ❌ Pipeline **KHÔNG ĐƯỢC** tự động publish mà không có approval từ admin

### Publisher
- Short bulletin → publish to **YouTube Shorts** + **TikTok** (sau khi được approve)
- Long bulletin → publish to **YouTube** (sau khi được approve)

---

## Acceptance Criteria
- [ ] Pipeline correctly detects day of week and selects the right format
- [ ] Short bulletin script is generated (150–200 words, top 3 headlines)
- [ ] Long bulletin script is generated (800–1200 words, top 5–7 headlines)
- [ ] Short video renders in 9:16 vertical format, ≤ 90 seconds
- [ ] Long video renders in 16:9 horizontal format, 5–10 minutes
- [ ] Article text (script) is sent to Bé MC on Telegram for review before publishing
- [ ] Video is sent to Bé MC on Telegram for review before publishing
- [ ] Pipeline waits for admin approval — does NOT auto-publish
- [ ] Short bulletin is published to both YouTube Shorts and TikTok only after approval
- [ ] Long bulletin is published to YouTube only after approval
- [ ] Sunday → pipeline skips publishing without error
- [ ] Pipeline summary report correctly shows format type and approval status
