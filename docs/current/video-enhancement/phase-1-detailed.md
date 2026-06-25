# Phase 1 — Quality Uplift (Detailed)

> **Mục tiêu:** Nâng chất lượng cảm nhận của video lên ngang MPT ở đúng những
> khâu MPT thắng: **phụ đề bám tiếng (Whisper)** và **nền nhiều cảnh + nhạc nền**.
> Tất cả cắm vào composer đã ổn định ở P0, qua feature flag.
>
> **Tiền đề:** P0 đã xong (composer O(1), flags, security).

---

## 1. Phạm vi

| # | Hạng mục | Trước (P0) | Sau (P1) |
|---|----------|------------|----------|
| A | **Subtitle timing** | word-count tỉ lệ → trôi so với tiếng | Whisper align → bám audio chính xác |
| B | **Background** | 1 clip loop cả video | Nhiều clip, đổi cảnh mỗi N giây |
| C | **Audio** | chỉ giọng đọc | + nhạc nền volume thấp (ducking) |

---

## 2. Thiết kế

### 2.1 (P1.A) Whisper subtitle alignment — `video/subtitle_aligner.py`

```
audio.mp3 ──► faster-whisper (model "base"/"small", CPU) ──► segments[(start,end,text)]
                                  │
                       SUBTITLE_TIMING_MODE == "whisper"?
                        ├─ yes → dùng segments của Whisper
                        └─ no/lỗi/không cài → fallback generate_srt() (P0)
```

- Module mới `subtitle_aligner.align(audio_path, script_text) -> list[entries]`.
  - Whisper cho timing chính xác theo audio; *script_text* dùng để **hiệu đính
    chính tả tiếng Việt** (Whisper có thể nghe sai từ) — ưu tiên text gốc, chỉ
    lấy timing từ Whisper (kỹ thuật forced-alignment đơn giản: map segment text
    về câu script gần nhất).
- **Selector** trong `main._create_video`: chọn aligner theo flag; lỗi → fallback,
  pipeline không bao giờ chết vì Whisper.
- **Performance:** model `base` chạy CPU; cache model giữa các lần; chỉ chạy 1
  lần/video. Đo thời gian, đặt timeout; quá ngưỡng → fallback.
- **"Tốt hơn không?"** — Không dùng Whisper để *tạo lại text* (rủi ro sai tiếng
  Việt + mất số đã chuẩn hoá ở `text_preprocessor`). Chỉ mượn **timing**. Giữ
  text gốc = giữ moat.

### 2.2 (P1.B) Multi-clip background — mở rộng `video/pexels_downloader.py` + composer

```
BACKGROUND_MODE == "multi"?
 ├─ yes → lấy K clip (theo keywords + generic), tổng duration ≥ audio
 │        → composer ghép tuần tự, cắt mỗi BG_CLIP_SECONDS (vd 5s), crossfade nhẹ
 └─ no  → giữ single-clip loop (P0)
```

- `get_backgrounds(keywords, orientation, audio_duration, count) -> list[str]`
  (số nhiều) — tái dùng cache + duration logic đã có.
- Composer: dựng danh sách segment nền theo timeline; vẫn giữ nguyên tắc P0
  (input cố định cho phụ đề; nền dùng concat filter, không 1 input/dòng).
- Giữ fallback: thiếu clip → lặp lại clip sẵn có (không chết).

### 2.3 (P1.C) Background music — `video/audio_mixer.py`

```
voice.mp3 + music.mp3 ──► ffmpeg amix/sidechaincompress (ducking) ──► final_audio.mp3
                          music volume ~ -18dB, tự giảm khi có giọng
```

- Nhạc từ thư mục `video/assets/music/` (royalty-free, commit kèm hoặc tải về),
  chọn ngẫu nhiên/cố định; `ENABLE_BGM` flag.
- Hàm thuần `build_mix_command(voice, music, out, music_db)` để test lệnh.
- **UI/UX:** caption Telegram cho biết video có nhạc nền hay không, để người
  duyệt biết kiểm tra bản quyền/âm lượng.

---

## 3. Acceptance Criteria (DoD — Phase 1)

- [ ] `SUBTITLE_TIMING_MODE=whisper` → phụ đề bám tiếng (sai lệch < ~0.3s trên mẫu); flag tắt hoặc Whisper lỗi → fallback word-count tự động.
- [ ] `BACKGROUND_MODE=multi` → video đổi cảnh; single vẫn chạy như cũ.
- [ ] `ENABLE_BGM=1` → có nhạc nền, giọng vẫn nghe rõ (ducking); tắt → chỉ giọng.
- [ ] Mọi flag default vẫn = hành vi P0 (không hồi quy).
- [ ] Whisper không cài/không sẵn → pipeline vẫn tạo được video.
- [ ] `unittest discover` xanh; có test cho aligner-selector, multi-bg builder, mix builder.
- [ ] Ghi nhận bench thời gian render trước/sau (Whisper thêm bao lâu) trong PR.

## 4. Rủi ro & giảm thiểu

| Rủi ro | Giảm thiểu |
|--------|-----------|
| faster-whisper nặng/chậm trên Mac Mini | Model `base`, CPU, timeout + fallback; cache model |
| Whisper nghe sai tiếng Việt | Chỉ lấy timing, giữ text script gốc |
| Bản quyền nhạc nền | Chỉ dùng nguồn royalty-free; ghi nguồn trong `assets/music/CREDITS.md` |
| Ghép nhiều clip làm vỡ aspect/looping | Tái dùng scale/pad + duration-match đã test ở P0 |

## 5. Không làm trong P1

- Không Edge TTS / đa-provider (P2), không MoviePy engine (P2), không Web UI (P2).
</content>
