# OAuth Setup — YouTube Brand Accounts + TikTok Developer Portal

> Hướng dẫn thủ công để lấy OAuth credentials cho từng kênh trong
> `content-pipeline/channels.py` (`ai_youtube`, `drama_youtube`, `tiktok_main`).
> Đây là task thủ công (không code) — không có bước nào chạy được tự động.

---

## 1. YouTube Data API v3 — Brand Account cho từng kênh

Mỗi kênh YouTube (`ai_youtube`, `drama_youtube`) nên là một **Brand Account**
riêng (không phải channel cá nhân gắn trực tiếp vào Gmail chính), để:
- Uỷ quyền OAuth độc lập, không lộ Gmail cá nhân làm owner trực tiếp.
- Dễ chuyển quyền quản lý (thêm manager) sau này mà không đổi tài khoản gốc.

### 1.1 Tạo Brand Account

1. Đăng nhập Gmail riêng cho kênh (khuyến nghị: 1 Gmail/kênh, không dùng chung).
2. Vào [youtube.com](https://youtube.com) → avatar góc phải → **Tạo kênh** →
   chọn **Sử dụng tên tuỳ chỉnh** → đặt tên kênh (xem `branding/naming.md`).
   Đây chính là Brand Account.
3. YouTube Studio → **Tuỳ chỉnh** → upload avatar (800×800) + banner
   (2560×1440) + mô tả kênh (xem `branding/<channel>/description.md`).

### 1.2 Tạo OAuth2 Client (Google Cloud Console)

1. Vào [console.cloud.google.com](https://console.cloud.google.com) → tạo
   project mới (hoặc dùng chung 1 project cho cả 2 kênh, chỉ khác OAuth
   client/token).
2. **APIs & Services → Library** → bật **YouTube Data API v3**.
3. **APIs & Services → OAuth consent screen** → loại **External** (hoặc
   **Internal** nếu dùng Google Workspace) → điền tên app, scope
   `youtube.upload` + `youtube.force-ssl` (dùng để upload caption track, xem
   `publisher/youtube_uploader.py`).
4. **APIs & Services → Credentials → Create Credentials → OAuth client ID**
   → loại **Desktop app** → tải file `client_secret.json`.
5. Chạy `publisher/youtube_uploader.py` (hoặc pipeline) lần đầu với
   `YOUTUBE_CLIENT_SECRETS` trỏ tới file này → trình duyệt mở lên → **đăng
   nhập đúng Gmail của Brand Account đó** (dễ nhầm sang tài khoản cá nhân) →
   cấp quyền → token được lưu lại (`YOUTUBE_TOKEN_FILE`).

> ⚠️ **Lưu ý quan trọng:** OAuth consent luôn hỏi "chọn kênh nào để uỷ
> quyền" nếu Gmail quản lý nhiều Brand Account. Chọn nhầm kênh sẽ khiến video
> bị upload lên sai kênh. Luôn kiểm tra lại tên kênh trước khi bấm "Cho phép".

6. Vì mỗi kênh cần một token riêng, đặt tên file token theo kênh (ví dụ
   `.youtube_token_ai.json`, `.youtube_token_drama.json`) và trỏ biến môi
   trường tương ứng (`YOUTUBE_AI_TOKEN`, `YOUTUBE_DRAMA_TOKEN` trong
   `.env.example`) tới đường dẫn token đó — logic đọc đúng biến theo kênh sẽ
   được nối dây ở Phase 5, hiện tại các biến này chỉ cần tồn tại.

### 1.3 Lấy Channel ID

YouTube Studio → **Cài đặt → Kênh → Thông tin nâng cao** → copy
**ID kênh** (`UC...`) → điền vào `YOUTUBE_AI_CHANNEL_ID` /
`YOUTUBE_DRAMA_CHANNEL_ID`.

---

## 2. TikTok Developer Portal — Content Posting API

1. Đăng ký tài khoản tại [developers.tiktok.com](https://developers.tiktok.com).
2. **Manage apps → Create an app** → điền thông tin app.
3. Ở phần **Products**, thêm **Content Posting API** → xin quyền
   `video.publish` (cần TikTok review trước khi dùng production; sandbox mode
   dùng được ngay để test).
4. **Login Kit** → cấu hình OAuth redirect URI cho flow ủy quyền.
5. Thực hiện OAuth authorization code flow (theo tài liệu TikTok) với tài
   khoản TikTok chính (`tiktok_main` trong `channels.py`, dùng chung cho cả 2
   track) → nhận `access_token` + `open_id`.
6. Điền `TIKTOK_TOKEN` (access token) và `TIKTOK_OPEN_ID` vào `.env`.

> TikTok access token hết hạn định kỳ (thường 24h cho access token, có
> refresh token riêng) — cần script refresh token định kỳ trước khi dùng cho
> cron tự động (nằm ngoài phạm vi Phase 1, sẽ xử lý ở Phase 5).

---

## 3. Checklist sau khi hoàn tất

- [ ] `ai_youtube`: Brand Account tạo xong, branding tối thiểu, OAuth token lưu tại đường dẫn `YOUTUBE_AI_TOKEN`.
- [ ] `drama_youtube`: tương tự.
- [ ] `tiktok_main`: tài khoản tạo xong, bio có đủ 2 hashtag series, `TIKTOK_TOKEN`/`TIKTOK_OPEN_ID` đã điền.
- [ ] `.env` (không commit) đã điền đủ; `.env.example` chỉ chứa placeholder.

## Liên kết

- Channel registry: `content-pipeline/channels.py`
- Branding draft: `branding/naming.md`
- Phase 1 design doc: `docs/current/phase-1-detailed.md`
