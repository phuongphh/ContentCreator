# Video Enhancement — Hybrid Roadmap (P0 → P1 → P2)

> Triển khai khuyến nghị **hybrid** từ
> [`../video-pipeline-analysis.md`](../video-pipeline-analysis.md): giữ backbone
> localized + research-driven + Telegram approval, hấp thụ chọn lọc kỹ thuật
> dựng video của MoneyPrinterTurbo (MPT).
>
> Mỗi phase có 2 file: `phase-N-detailed.md` (kiến trúc + acceptance) và
> `phase-N-issues.md` (Epic + sub-issues sẵn để push GitHub).
>
> **Lưu ý đặt tên:** phase ở đây (P0/P1/P2) là track *Video Enhancement*, độc
> lập với `phase-1..6` của roadmap Content Creator 2.0 ở thư mục cha. Đặt trong
> thư mục riêng để tránh trùng số.

## Nguyên tắc xuyên suốt (áp cho mọi issue)

Ở mỗi bước luôn tự hỏi *"có thể làm tốt hơn không?"* và bảo đảm 5 trục:

| Trục | Cam kết cụ thể |
|---|---|
| **Consistency** | Mọi thay đổi đi qua **feature flag** trong `config.py`; module mới giữ chữ ký hàm cũ (facade) để không vỡ `main.py`; tuân convention `tests/` hiện có (`unittest`). |
| **Performance** | Mỗi flag có path "cũ" làm fallback; composer phải hạ từ **O(N) input → O(1)**; Whisper chạy model nhỏ trên CPU; đo thời gian render trước/sau. |
| **UI/UX** | Giữ Telegram là cổng duyệt chính (mobile-first). Thêm tuỳ chọn chứ không thay luồng duyệt. Thông báo lỗi rõ ràng bằng tiếng Việt. |
| **Security** | Bỏ `CERT_NONE`; secrets chỉ qua `.env`; validate đường dẫn file đầu vào ffmpeg/whisper; không log token. |
| **Unit test** | Mỗi issue code có mục **Test required**. Logic thuần phải có test; phần I/O (ffmpeg/network/whisper) mock. Không merge nếu CI test đỏ. |

## Sơ đồ kiến trúc — Hybrid target

```
        CONTENT BACKBONE (giữ nguyên)                 VIDEO ENGINE (nâng cấp)
 ┌───────────────────────────────────┐   ┌──────────────────────────────────────┐
 │ collectors → rule_filter           │   │ script_generator (Sonnet)            │
 │  → ai_scorer(Haiku)                │   │        │                              │
 │  → ai_analyzer(Sonnet)            │   │        ▼                              │
 │  → narrative                       │──►│ text_preprocessor (số→chữ VN) ★giữ   │
 └───────────────────────────────────┘   │        │                              │
                                          │        ▼                              │
                                          │  TTS LAYER  (P2: đa-provider)         │
                                          │   ┌──────────────┐                    │
                                          │   │ tts/factory  │─► nuitruc (primary)│
                                          │   │ (facade cũ)  │─► edge   (fallback)│
                                          │   └──────────────┘                    │
                                          │        │ audio.mp3                    │
                                          │        ▼                              │
                                          │  SUBTITLE LAYER (P1: Whisper)         │
                                          │   flag SUBTITLE_TIMING_MODE:          │
                                          │    • wordcount  (cũ, fallback)        │
                                          │    • whisper    (mới, bám audio)      │
                                          │        │ subtitle.srt                 │
                                          │        ▼                              │
                                          │  BACKGROUND LAYER (P1: multi-clip+BGM)│
                                          │   pexels_downloader (★giữ cache)      │
                                          │    • single (cũ)  • multi (mới)       │
                                          │        │                              │
                                          │        ▼                              │
                                          │  COMPOSER (P0: O(1); P2: MoviePy)     │
                                          │   render subtitle 1 track (ASS/PNG)   │
                                          │        │ video.mp4                    │
                                          └────────┼──────────────────────────────┘
                                                   ▼
        DISTRIBUTION (giữ nguyên)
 ┌───────────────────────────────────────────────────────────────────────────┐
 │ Telegram review gate  →  /approve_<id>  →  publisher (YouTube / TikTok)       │
 │                          scheduler dual-format (T2/4/6 short, T3/5/7 long)    │
 └───────────────────────────────────────────────────────────────────────────┘

 ★ = moat tiếng Việt — không thay thế.
```

### Composer: vì sao P0 ưu tiên (O(N) → O(1))

```
 HIỆN TẠI (rủi ro):  ffmpeg -i bg -i audio -i sub0.png -i sub1.png ... -i subN.png
                     filter: overlay→overlay→overlay (chuỗi N mắt xích)
                     → video 5-10' có N≈100+ input → chậm, ngốn RAM, dễ vỡ

 TARGET P0:          ffmpeg -i bg -i audio -i subtitles.(ass|mov)   ← 1 lớp phụ đề
                     → số input không phụ thuộc số dòng phụ đề
```

## Tổng quan phase

| # | Phase | Mục tiêu | Rủi ro | Estimate | Status |
|---|-------|----------|--------|----------|--------|
| 0 | [Stabilize & Secure](phase-0-detailed.md) — [issues](phase-0-issues.md) | Sửa SSL TTS, hạ composer O(N)→O(1), feature-flag scaffold, hoàn thiện test nền | Thấp | ~4–5 ngày | ✅ done (PR #56) |
| 1 | [Quality Uplift](phase-1-detailed.md) — [issues](phase-1-issues.md) | Whisper subtitle timing + multi-clip background + nhạc nền | Trung bình | ~6–8 ngày | ✅ done (PR #56) |
| 2 | [Extensibility](phase-2-detailed.md) — [issues](phase-2-issues.md) | TTS đa-provider (Núi Trúc + Edge), engine MoviePy tuỳ chọn, web preview tuỳ chọn | Trung bình–cao | ~8–10 ngày | ✅ done |

**Tổng:** ~18–23 ngày làm việc (solo).

## Phụ thuộc giữa phase

```
P0 (nền: flags + composer ổn định + security)
 └─► P1 (Whisper/BG/BGM cắm vào composer đã ổn định)
      └─► P2 (đa-provider + MoviePy — chỉ làm khi P0/P1 đã chứng minh giá trị)
```

- **P0 là blocker.** Không làm Whisper/multi-clip trên composer còn O(N).
- P1 có thể chia đôi: subtitle (P1.A) và background/BGM (P1.B) chạy song song.
- P2 là *optional* — chỉ kích hoạt nếu nhu cầu thực tế vượt khả năng FFmpeg flag-based.

## Convention (kế thừa từ `../README.md`)

- Label tối thiểu: `video-enh`, `phase-0|1|2`, loại con (`feat`/`fix`/`chore`/`docs`/`test`/`refactor`/`security`), domain (`video`/`audio`/`subtitle`/`bg`/`infra`).
- Estimate: **S** ≤ 0.5 ngày · **M** = 0.5–1.5 ngày · **L** = 2–3 ngày.
- Push: tạo Epic trước → sub-issue link `Part of #<epic>`.
- Mỗi sub-issue code phải có checkbox `[ ] Unit test ... pass` trong Acceptance.
</content>
