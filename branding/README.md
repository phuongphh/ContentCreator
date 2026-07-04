# Branding — Phase 1 (EPIC #1.3)

Thư mục này chứa asset branding cho 2 kênh YouTube (`ai_youtube`,
`drama_youtube`) và 1 tài khoản TikTok (`tiktok_main`), khớp với
`content-pipeline/channels.py`.

## Trạng thái

EPIC #1.3 là task **thủ công** (không code) — phần này chỉ scaffold nội dung
text (naming draft, description draft, bio draft) để người quyết định branding
duyệt/chỉnh sửa. Các việc sau **cần làm thủ công ngoài phiên làm việc này**
(không thể tự động hoá từ môi trường coding):

- [ ] Chốt tên cuối cùng cho từng kênh (xem `naming.md` — 5 candidate/kênh,
      cần kiểm tra tên trống trên YouTube/TikTok trước khi chốt).
- [ ] Thiết kế avatar (800×800) + banner (2560×1440) — Midjourney/Ideogram +
      Canva, hoặc thuê designer.
- [ ] Đăng ký 2 kênh YouTube trên 2 Gmail riêng, tạo Brand Account (xem
      `docs/current/oauth-setup.md`).
- [ ] Đăng ký tài khoản TikTok, viết bio + link bio (Linktree/Beacons).
- [ ] Upload avatar/banner/description lên từng kênh.

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
