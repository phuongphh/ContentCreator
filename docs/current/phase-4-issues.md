# Phase 4 — Issues Master List

> Drama Video Production. 4 Epic, ~17 sub-issues, ~10 ngày.

---

## EPIC #4.1 — TTS Multi-provider Abstraction

**Loại:** `epic` `phase-4` `backend` `audio`
**Mô tả:** Refactor `tts_client.py` thành abstraction, implement ≥2 provider.

**Definition of Done:**
- Swap provider qua env không cần đổi code.
- 2 provider chạy được: ElevenLabs (drama), FPT.AI (AI).

### Sub-issues

#### `[chore]` Audit `tts_client.py` hiện tại
- **Labels:** `phase-4` `audio` `chore`
- **Estimate:** S
- **Mô tả:** Đọc code hiện tại, xác định provider đang dùng, dependency, format output. Document lại ở `docs/current/tts-audit.md`.

#### `[refactor]` Refactor thành protocol/interface
- **Labels:** `phase-4` `audio` `refactor`
- **Estimate:** M
- **Mô tả:** Tạo `TTSProvider` Protocol, di chuyển code cũ thành `LegacyProvider`. Backward compatible.

#### `[feat]` Implement `ElevenLabsProvider`
- **Labels:** `phase-4` `audio` `feat` `drama`
- **Estimate:** M
- **Mô tả:** Dùng `elevenlabs` SDK, support voice settings (stability, similarity). Lưu file MP3, return duration.
- **Acceptance:**
  - [ ] Test với voice ID Vietnamese đã chọn
  - [ ] Cost tracking đúng

#### `[feat]` Implement `FPTAIProvider`
- **Labels:** `phase-4` `audio` `feat`
- **Estimate:** M
- **Mô tả:** REST API FPT.AI TTS. Voice "banmai" hoặc "leminh".

#### `[feat]` `synthesize_for_track()` helper
- **Labels:** `phase-4` `audio` `feat`
- **Estimate:** S
- **Mô tả:** Đọc `TTS_PROFILES`, gọi đúng provider.

#### `[test]` Sample 5 đoạn drama + 5 đoạn AI
- **Labels:** `phase-4` `test`
- **Estimate:** M
- **Mô tả:** Synthesize 10 sample, nghe tay, đánh giá theo checklist (rõ chữ, đúng dấu, cảm xúc).

---

## EPIC #4.2 — Video Composer Multi-track

**Loại:** `epic` `phase-4` `backend` `video`
**Mô tả:** Mở rộng `video_composer.py` accept `track` + `format` parameter.

### Sub-issues

#### `[refactor]` Tách template ra file riêng
- **Labels:** `phase-4` `video` `refactor`
- **Estimate:** M
- **Mô tả:** Tạo `video/templates/__init__.py`, `ai.py`, `drama.py`. Function `load_template(track, format)`.

#### `[feat]` Drama Shorts template
- **Labels:** `phase-4` `video` `feat` `drama`
- **Estimate:** L
- **Mô tả:** Implement `DRAMA_SHORTS_TEMPLATE` với 6 scene + transition + lower-third overlay.
- **Acceptance:**
  - [ ] Render 1 video test 75s đủ 6 scene
  - [ ] Transition mượt
  - [ ] Lower-third hiển thị đúng tên nhân vật

#### `[feat]` Hàm `compose_video()` mới
- **Labels:** `phase-4` `video` `feat`
- **Estimate:** L
- **Mô tả:** Refactor entry point cũ thành `compose_video(script_id, track, format)`. Backward-compatible alias cho code AI cũ.

#### `[feat]` Subtitle integration với multi-style
- **Labels:** `phase-4` `video` `feat`
- **Estimate:** M
- **Mô tả:** Drama dùng subtitle font lớn, có outline đậm. AI dùng subtitle nhỏ hơn, font khác.

---

## EPIC #4.3 — Drama Visual Assets

**Loại:** `epic` `phase-4` `creative` `drama`
**Mô tả:** AI illustration generation, lower-third, music.

### Sub-issues

#### `[feat]` AI image generator client
- **Labels:** `phase-4` `creative` `feat`
- **Estimate:** M
- **Mô tả:** Wrap Replicate/Ideogram API. Input: thumbnail_prompt từ rewriter. Output: 3 ảnh khác góc + cache theo hash.
- **Acceptance:**
  - [ ] Cost <$0.15 cho 3 ảnh
  - [ ] Cache hit rate >50% sau 1 tuần

#### `[feat]` Lower-third overlay generator
- **Labels:** `phase-4` `video` `feat` `drama`
- **Estimate:** M
- **Mô tả:** Tạo PNG transparent runtime với Pillow: background semi-transparent + text "Tên (Vai trò)".

#### `[task]` Bộ nhạc nền royalty-free
- **Labels:** `phase-4` `creative` `task`
- **Estimate:** S
- **Mô tả:** Mua/tải 5 track music drama từ YouTube Audio Library hoặc Pixabay. Lưu vào `assets/music/drama/`.

#### `[feat]` Audio mixer
- **Labels:** `phase-4` `audio` `feat`
- **Estimate:** M
- **Mô tả:** FFmpeg ducking — giảm music khi voice xuất hiện. Voice -3 dB, music -18 dB khi voice on.

---

## EPIC #4.4 — End-to-end render test

**Loại:** `epic` `phase-4` `test`
**Mô tả:** Validate toàn bộ phase bằng 5 video drama + 3 video AI.

### Sub-issues

#### `[test]` Render 5 video drama từ pipeline đầu-đến-cuối
- **Labels:** `phase-4` `test` `drama`
- **Estimate:** M
- **Mô tả:** Lấy 5 story đã rewrite (từ Phase 3), chạy `compose_video`, kiểm tra:
  - [ ] File MP4 chạy được
  - [ ] Subtitle đúng audio
  - [ ] Đủ 6 scene
  - [ ] vn_commentary scene xuất hiện
  - [ ] Lower-third đúng tên

#### `[test]` Render 3 video AI giữ tương thích cũ
- **Labels:** `phase-4` `test`
- **Estimate:** S
- **Mô tả:** Đảm bảo refactor không phá pipeline AI hiện có.

#### `[infra]` Render queue + concurrency limit
- **Labels:** `phase-4` `infra`
- **Estimate:** M
- **Mô tả:** Queue tuần tự (max 1 render đồng thời) để tránh OOM trên Mac Mini. Dùng SQLite làm queue đơn giản.

---

## Tóm tắt Phase 4

| Epic | Issues | Estimate |
|------|--------|----------|
| #4.1 TTS abstraction | 6 | ~3 ngày |
| #4.2 Video composer | 4 | ~3 ngày |
| #4.3 Drama assets | 4 | ~2 ngày |
| #4.4 E2E test | 3 | ~2 ngày |
| **Tổng** | **17** | **~10 ngày** |
