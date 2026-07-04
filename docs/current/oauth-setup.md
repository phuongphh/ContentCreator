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
5. Đặt `YOUTUBE_CLIENT_SECRETS` trong `.env` trỏ tới file này, rồi chạy:

   ```bash
   cd content-pipeline
   python publisher/youtube_uploader.py
   ```

   → trình duyệt mở lên → **đăng nhập đúng Gmail của Brand Account đó** (dễ
   nhầm sang tài khoản cá nhân) → cấp quyền → token được lưu tại
   `YOUTUBE_TOKEN_FILE` (mặc định `publisher/.youtube_token.json`). Lệnh này
   in ra tên kênh vừa xác thực (`Authenticated as channel: ...`) — **luôn
   kiểm tra tên kênh in ra khớp với kênh bạn định dùng** trước khi chạy upload
   thật.

> ⚠️ **Lưu ý quan trọng:** OAuth consent luôn hỏi "chọn kênh nào để uỷ
> quyền" nếu Gmail quản lý nhiều Brand Account. Chọn nhầm kênh sẽ khiến video
> bị upload lên sai kênh. Luôn kiểm tra lại tên kênh trước khi bấm "Cho phép".

### 1.3 Lấy Channel ID

YouTube Studio → **Cài đặt → Kênh → Thông tin nâng cao** → copy
**ID kênh** (`UC...`) → điền vào `YOUTUBE_AI_CHANNEL_ID` /
`YOUTUBE_DRAMA_CHANNEL_ID`.

### 1.4 Setup OAuth2 cho kênh thứ 2 (cùng project Google Cloud)

Không cần tạo project/OAuth client mới cho kênh thứ 2 — **1 OAuth2 Client ID
(Desktop app) dùng chung được cho nhiều Google Account/Brand Account khác
nhau**; điểm khác nhau là *token nào được sinh ra khi bạn đăng nhập tài khoản
nào* lúc chạy flow. Vì vậy nếu đã setup OAuth2 xong cho kênh 1 (`AI Hôm Nay`,
`2p.broadcast@gmail.com`), để setup cho kênh 2 (`Chuyện Đời`,
`2p.drama@gmail.com`) trong cùng project:

1. **Nếu OAuth consent screen đang ở chế độ Testing** (chưa submit Google
   verify — thường đúng ở giai đoạn này): vào **APIs & Services → OAuth
   consent screen → Audience/Test users** → **Add users** → thêm
   `2p.drama@gmail.com` vào danh sách test user. Thiếu bước này, Google sẽ
   chặn tài khoản `2p.drama@gmail.com` ngay ở màn hình consent với lỗi
   "app has not completed verification"/"access blocked".
2. Chạy lại uploader nhưng chỉ định **token file riêng** cho kênh 2 (không
   dùng chung path với kênh 1, kẻo bị ghi đè):

   ```bash
   cd content-pipeline
   python publisher/youtube_uploader.py \
       --token-file publisher/.youtube_token_drama.json
   ```

3. Trình duyệt mở lên → **đăng xuất Google trước nếu trình duyệt đang đăng
   nhập sẵn `2p.broadcast@gmail.com`**, rồi đăng nhập bằng `2p.drama@gmail.com`
   → cấp quyền cho kênh `[2P] Chuyện Đời`.
4. Kiểm tra dòng in ra `Authenticated as channel: ...` đúng là
   `[2P] Chuyện Đời` (không phải `[2P] AI Hôm Nay`) trước khi coi như xong.
5. Điền `YOUTUBE_DRAMA_TOKEN=publisher/.youtube_token_drama.json` vào `.env`
   (đường dẫn tới file token vừa tạo). Việc đọc đúng biến này theo từng kênh
   khi upload thật sẽ được nối dây ở Phase 5 — Phase 1 chỉ cần token đã sẵn
   sàng.

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
