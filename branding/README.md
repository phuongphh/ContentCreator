# Branding — Phase 1 (EPIC #1.3)

Thư mục này chứa asset branding cho 2 kênh YouTube (`ai_youtube`,
`drama_youtube`) và 1 tài khoản TikTok (`tiktok_main`), khớp với
`content-pipeline/channels.py`.

## Trạng thái

EPIC #1.3 là task **thủ công** (không code). Tiến độ thực tế:

- [x] Đăng ký 2 kênh YouTube trên 2 Gmail riêng:
      `[2P] AI Hôm Nay` (`2p.broadcast@gmail.com`),
      `[2P] Chuyện Đời` (`2p.drama@gmail.com`).
- [x] Đăng ký 1 tài khoản TikTok (bằng `2p.broadcast@gmail.com`).
- [x] Thiết kế avatar + banner + description cho 2 kênh YouTube, đã upload.
- [ ] Đặt bio + link-in-bio cho TikTok (xem `tiktok/bio.md` — hướng dẫn từng
      bước) và ghi lại handle thật vào `naming.md`.
- [ ] Setup OAuth2 cho kênh `[2P] Chuyện Đời` (đã có OAuth2 cho `AI Hôm Nay`
      từ trước) — xem `docs/current/oauth-setup.md` mục 1.4.

## Cấu trúc

```
branding/
├── naming.md                    # 5 tên đề xuất/kênh + rationale (draft)
├── ai_youtube/
│   ├── description.md           # Draft mô tả kênh YouTube AI
│   ├── avatar.png               # (chưa có — cần thiết kế thủ công)
│   └── banner.png               # (chưa có — cần thiết kế thủ công)
├── drama_youtube/
│   ├── description.md           # Draft mô tả kênh YouTube Drama
│   ├── avatar.png               # (chưa có)
│   └── banner.png               # (chưa có)
└── tiktok/
    └── bio.md                   # Draft bio TikTok (2 hashtag series + link bio)
```

## Liên kết

- Thiết kế kỹ thuật: `docs/current/phase-1-detailed.md` mục 3.3.
- Channel registry: `content-pipeline/channels.py`.
- OAuth setup sau khi có kênh: `docs/current/oauth-setup.md`.
