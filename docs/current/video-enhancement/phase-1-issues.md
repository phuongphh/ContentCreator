# Phase 1 — Issues Master List

> Bản nháp Epic + sub-issues cho **Phase 1 (Quality Uplift)**. Tiền đề: Phase 0
> đã merge (composer O(1), flags, security). Mỗi sub-issue code có **Test required**.

---

## EPIC #V1.1 — Whisper subtitle alignment (timing bám audio)

**Loại:** `epic` `video-enh` `phase-1` `subtitle` `audio`
**Mô tả:** Thêm `video/subtitle_aligner.py` dùng faster-whisper lấy **timing**
chính xác, giữ **text script gốc** (bảo toàn localization). Chọn qua
`SUBTITLE_TIMING_MODE`; lỗi → fallback word-count.

**Definition of Done:**
- `whisper` mode bám tiếng; fallback tự động khi lỗi/không cài.
- Không bao giờ làm pipeline chết.

### Sub-issues

#### `[chore]` Thêm `faster-whisper` (optional dependency)
- **Labels:** `video-enh` `phase-1` `infra` `chore`
- **Estimate:** S
- **Mô tả:** Thêm vào `requirements.txt` dạng optional/extra; import lazy + try/except.
- **Acceptance:**
  - [ ] Thiếu thư viện → log cảnh báo, không crash
  - [ ] **Test required:** import-guard test (mock ImportError → fallback)

#### `[feat]` `subtitle_aligner.align(audio, script_text)`
- **Labels:** `video-enh` `phase-1` `subtitle` `feat`
- **Estimate:** L
- **Mô tả:** Gọi Whisper lấy segment timing; map về câu script gần nhất để giữ text gốc (forced-alignment đơn giản). Trả list `(start,end,text)`.
- **Acceptance:**
  - [ ] Output dùng được trực tiếp bởi `_srt_to_ass` (P0)
  - [ ] Text trả về là câu script gốc (không phải transcript Whisper)
  - [ ] **Test required:** `tests/test_subtitle_aligner.py` — mock Whisper segments, assert mapping timing↔text đúng; assert giữ số đã chuẩn hoá

#### `[feat]` Selector trong `main._create_video` theo flag + timeout/fallback
- **Labels:** `video-enh` `phase-1` `subtitle` `feat`
- **Estimate:** M
- **Mô tả:** Nếu `SUBTITLE_TIMING_MODE=whisper` → thử align; timeout/exception → `generate_srt()`.
- **Acceptance:**
  - [ ] **Test required:** mock aligner raise → assert dùng fallback và vẫn ra SRT

#### `[test]` Đo độ lệch timing trên mẫu
- **Labels:** `video-enh` `phase-1` `subtitle` `test`
- **Estimate:** S
- **Mô tả:** Mẫu audio ngắn + ground-truth → assert sai lệch trung bình < ngưỡng. (Có thể đánh dấu `slow`/skip nếu không có model trong CI.)

---

## EPIC #V1.2 — Multi-clip background

**Loại:** `epic` `video-enh` `phase-1` `bg` `video`
**Mô tả:** Cho phép nền nhiều cảnh đổi mỗi N giây, qua `BACKGROUND_MODE=multi`.
Tái dùng cache + duration logic của `pexels_downloader`.

**Definition of Done:**
- `multi` đổi cảnh; `single` giữ nguyên P0; thiếu clip → lặp, không chết.

### Sub-issues

#### `[feat]` `get_backgrounds(..., count) -> list[str]`
- **Labels:** `video-enh` `phase-1` `bg` `feat`
- **Estimate:** M
- **Mô tả:** Bản số nhiều của `get_background`; gom đủ clip để tổng duration ≥ audio.
- **Acceptance:**
  - [ ] Dùng lại `_cache_*`, `_select_best_background`
  - [ ] **Test required:** mock cache/download → assert số clip & fallback khi thiếu

#### `[feat]` Composer dựng timeline nền nhiều clip
- **Labels:** `video-enh` `phase-1` `bg` `video` `feat`
- **Estimate:** L
- **Mô tả:** Ghép clip tuần tự, cắt mỗi `BG_CLIP_SECONDS`, crossfade nhẹ; vẫn giữ phụ đề 1 lớp (P0). Hàm thuần `build_multi_bg_filter(clips, durations)`.
- **Acceptance:**
  - [ ] Số input phụ đề vẫn O(1)
  - [ ] **Test required:** assert filter concat/xfade đúng số đoạn theo audio_duration

#### `[infra]` Flag `BG_CLIP_SECONDS` + default
- **Labels:** `video-enh` `phase-1` `infra`
- **Estimate:** S

---

## EPIC #V1.3 — Background music (BGM) + ducking

**Loại:** `epic` `video-enh` `phase-1` `audio`
**Mô tả:** Trộn nhạc nền royalty-free dưới giọng đọc với ducking, qua `ENABLE_BGM`.

**Definition of Done:**
- BGM bật → có nhạc, giọng vẫn rõ; tắt → chỉ giọng.

### Sub-issues

#### `[feat]` `audio_mixer.build_mix_command(voice, music, out, music_db)`
- **Labels:** `video-enh` `phase-1` `audio` `feat`
- **Estimate:** M
- **Mô tả:** Hàm thuần dựng lệnh ffmpeg `amix`/`sidechaincompress` (ducking). `mix_audio()` gọi nó + `_run_ffmpeg`.
- **Acceptance:**
  - [ ] **Test required:** `tests/test_audio_mixer.py` — assert lệnh có ducking + volume đúng; music thiếu → trả voice gốc

#### `[task]` Bổ sung nhạc royalty-free + CREDITS
- **Labels:** `video-enh` `phase-1` `audio` `task`
- **Estimate:** S
- **Acceptance:**
  - [ ] `video/assets/music/` có ≥3 track + `CREDITS.md` ghi nguồn/giấy phép

#### `[feat]` Telegram caption báo có BGM
- **Labels:** `video-enh` `phase-1` `bot` `feat`
- **Estimate:** S
- **Mô tả:** Thêm dòng "🎵 Nhạc nền: <tên>" vào caption duyệt để người duyệt kiểm tra.
- **Acceptance:**
  - [ ] **Test required:** assert caption chứa nhãn nhạc khi `ENABLE_BGM=1`

---

## Tóm tắt Phase 1

| Epic | Issues | Estimate |
|------|--------|----------|
| #V1.1 Whisper timing | 4 | ~3 ngày |
| #V1.2 Multi-clip BG | 3 | ~2.5 ngày |
| #V1.3 BGM + ducking | 3 | ~1.5 ngày |
| **Tổng** | **10** | **~7 ngày** |
</content>
