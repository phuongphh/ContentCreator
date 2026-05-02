# `docs/current/` — Phase Roadmap

> File index 6 phase của chiến lược Content Creator 2.0. Mỗi phase có 2 file: `*-detailed.md` (kiến trúc + acceptance) và `*-issues.md` (Epic + sub-issues sẵn để push lên GitHub).

## Tổng quan 6 phase

| # | Phase | Mục tiêu chính | Estimate | Status |
|---|-------|----------------|----------|--------|
| 1 | [Foundation & Multi-channel](phase-1-detailed.md) — [issues](phase-1-issues.md) | Channel registry, DB migration, branding 2 kênh YouTube + 1 TikTok | ~7 ngày | 📋 |
| 2 | [Drama Source Layer](phase-2-detailed.md) — [issues](phase-2-issues.md) | Reddit drama collector + VN seed bot + storage | ~7 ngày | 📋 |
| 3 | [Drama Generation Layer](phase-3-detailed.md) — [issues](phase-3-issues.md) | Rubric scorer + Việt-hoá rewriter + long-form compiler | ~10 ngày | 📋 |
| 4 | [Drama Video Production](phase-4-detailed.md) — [issues](phase-4-issues.md) | TTS multi-provider + video composer multi-track + drama assets | ~10 ngày | 📋 |
| 5 | [Distribution](phase-5-detailed.md) — [issues](phase-5-issues.md) | Telegram review gate + YouTube multi-channel + TikTok upload + scheduler | ~7 ngày | 📋 |
| 6 | [Analytics & Iteration](phase-6-detailed.md) — [issues](phase-6-issues.md) | Metrics puller + Streamlit dashboard + A/B helper + weekly retro | ~7 ngày | 📋 |

**Tổng estimate:** ~48 ngày làm việc (≈10 tuần với buffer).
**Số issue dự kiến:** ~90 issue (15 Epic × 5–6 sub-issue trung bình).

## Cách dùng

1. Đọc `strategy.md` (sẽ cập nhật) để nắm vision.
2. Đọc `phase-N-detailed.md` của phase đang làm.
3. Mở `phase-N-issues.md`, duyệt từng Epic + sub-issue.
4. Push lên GitHub theo convention: tạo Epic trước (label `epic` + `phase-N`), tạo sub-issue sau (link tới Epic bằng `Part of #<epic-number>`).
5. GitHub Action `issue-lifecycle.yml` sẽ tự sinh `docs/issues/active/issue-N.md` cho mỗi issue.
6. Trigger Claude Code: "implement issue N" — Claude đọc `issues/active/issue-N.md` + phase detailed làm context.

## Phụ thuộc giữa các phase

```
Phase 1 ──► Phase 2 ──► Phase 3 ──► Phase 4 ──► Phase 5 ──► Phase 6
                ╲                    ╲
                 ╲                    ╲
                  └─► chỉ cần channel  └─► dùng channel registry để upload
                      registry để
                      route track
```

- Phase 1 là blocker cho mọi phase sau.
- Phase 2 và Phase 3 có thể start song song một phần (Phase 3 cần ≥10 sample story để test, Phase 2 cung cấp).
- Phase 4 phải chờ Phase 3 hoàn tất rewriter.
- Phase 5 chỉ cần Phase 4 (render pipeline) + Phase 1 (channel/OAuth).
- Phase 6 chờ Phase 5 (cần video uploaded có ID).

## Convention nhãn (label)

Mỗi issue tối thiểu có 3 label:
- `phase-N` (1–6)
- `epic` HOẶC loại con (`feat`/`task`/`chore`/`docs`/`infra`/`test`/`refactor`)
- Domain: `backend`/`database`/`video`/`audio`/`upload`/`branding`/`creative`/`analytics`/`bot`/...

Ngoài ra có flag bổ sung: `drama` (gắn cho mọi issue thuần Drama track), `defer-able` (có thể hoãn nếu blocker), `decision` (cần Phuong chốt trước khi code).

## Estimate scale

- **S** ≤ 0.5 ngày làm việc
- **M** = 0.5–1.5 ngày
- **L** = 2–3 ngày

Ước lượng theo solo developer (Phuong) trên Mac Mini, không tính thời gian chờ external (TikTok app approval, YouTube quota request).
