# Phase 2 — Issues Master List

> Bản nháp Epic + sub-issues cho **Phase 2 (Extensibility)**. **Optional** — bật
> từng Epic theo nhu cầu. Tiền đề: P0 + P1 đã merge. Mỗi sub-issue code có
> **Test required**.

---

## EPIC #V2.1 — TTS đa-provider (Núi Trúc + Edge)

**Loại:** `epic` `video-enh` `phase-2` `audio`
**Mô tả:** Tách TTS thành interface `video/tts/` với factory + fallback chain.
`tts_client.text_to_speech` thành facade. Edge TTS làm provider thứ 2 (miễn phí,
giọng vi-VN), vẫn chạy qua `text_preprocessor` để đọc số đúng.

**Definition of Done:**
- Đổi provider qua `TTS_PROVIDER`; primary lỗi → fallback; facade cũ không đổi.

### Sub-issues

#### `[refactor]` Định nghĩa `tts/base.py` + chuyển Núi Trúc sang `tts/nuitruc.py`
- **Labels:** `video-enh` `phase-2` `audio` `refactor`
- **Estimate:** M
- **Mô tả:** Interface `TTSProvider.synthesize(text, out, voice, speed)`. Di chuyển logic hiện có (kèm SSL secure của P0) vào provider.
- **Acceptance:**
  - [ ] `tts_client.text_to_speech` vẫn hoạt động y hệt (facade)
  - [ ] **Test required:** `tests/test_tts_factory.py` — facade gọi đúng provider

#### `[feat]` `tts/edge.py` (Edge TTS vi-VN)
- **Labels:** `video-enh` `phase-2` `audio` `feat`
- **Estimate:** M
- **Mô tả:** Provider dùng edge-tts (`vi-VN-HoaiMyNeural`/`NamMinhNeural`). Import lazy.
- **Acceptance:**
  - [ ] Thiếu thư viện → báo lỗi rõ, không crash factory
  - [ ] **Test required:** mock edge-tts → assert gọi đúng voice + ghi file

#### `[feat]` `tts/factory.py` + fallback chain
- **Labels:** `video-enh` `phase-2` `audio` `feat`
- **Estimate:** M
- **Mô tả:** `get_provider(name)`; nếu primary `synthesize` trả None → thử provider kế theo thứ tự cấu hình.
- **Acceptance:**
  - [ ] **Test required:** primary trả None → fallback được gọi; cả hai lỗi → trả None + log

#### `[test]` Bảo đảm preprocessor chạy trước mọi provider
- **Labels:** `video-enh` `phase-2` `audio` `test`
- **Estimate:** S
- **Acceptance:**
  - [ ] **Test required:** input có "$1,000" → text gửi provider đã là "một nghìn đô la"

---

## EPIC #V2.2 — MoviePy composer engine (tuỳ chọn)

**Loại:** `epic` `video-enh` `phase-2` `video`
**Mô tả:** Thêm engine MoviePy hoán đổi qua `COMPOSER_ENGINE`, cùng chữ ký với
composer FFmpeg. Default vẫn FFmpeg.

**Definition of Done:**
- `moviepy` ra video tương đương; default FFmpeg không đổi.

### Sub-issues

#### `[chore]` Thêm MoviePy (optional dependency, import lazy)
- **Labels:** `video-enh` `phase-2` `video` `chore`
- **Estimate:** S
- **Acceptance:**
  - [ ] Thiếu MoviePy + `COMPOSER_ENGINE=moviepy` → báo lỗi rõ, gợi ý cài

#### `[feat]` `composer_moviepy.compose(...)` cùng signature
- **Labels:** `video-enh` `phase-2` `video` `feat`
- **Estimate:** L
- **Mô tả:** Dựng bg + audio + phụ đề bằng MoviePy timeline; hỗ trợ multi-clip/BGM của P1.
- **Acceptance:**
  - [ ] Chữ ký == `compose_video`
  - [ ] **Test required:** mock MoviePy clip → assert layer/timeline lắp đúng (không render thật trong unit test)

#### `[feat]` Engine selector trong `main`
- **Labels:** `video-enh` `phase-2` `video` `feat`
- **Estimate:** S
- **Acceptance:**
  - [ ] **Test required:** flag → chọn đúng engine; default FFmpeg

---

## EPIC #V2.3 — Web preview UI (tuỳ chọn, local-only)

**Loại:** `epic` `video-enh` `phase-2` `bot` `decision`
**Mô tả:** Streamlit app local liệt kê video `pending_approval`, xem trước, duyệt
qua cùng `publish_callback`. Bổ sung, không thay Telegram. **Cần Phuong chốt
trước khi code.**

**Definition of Done:**
- Liệt kê + preview + approve/reject hoạt động; dùng chung DB; bind 127.0.0.1.

### Sub-issues

#### `[feat]` Tách logic duyệt khỏi Telegram → `review_service.py`
- **Labels:** `video-enh` `phase-2` `backend` `refactor`
- **Estimate:** M
- **Mô tả:** Trích `approve(video_id)`/`reject(video_id)` dùng chung cho Telegram & Web (single source of truth).
- **Acceptance:**
  - [ ] Telegram bot dùng service mới, hành vi không đổi
  - [ ] **Test required:** `tests/test_review_service.py` — chuyển trạng thái đúng, chặn duyệt sai trạng thái

#### `[feat]` Streamlit app `webui/app.py` (local)
- **Labels:** `video-enh` `phase-2` `frontend` `feat`
- **Estimate:** L
- **Mô tả:** Danh sách pending + video preview + nút Approve/Reject gọi `review_service`. Bind `127.0.0.1`.
- **Acceptance:**
  - [ ] Không expose ra ngoài; không secret client-side
  - [ ] **Test required:** logic tải danh sách/format tách khỏi runtime Streamlit và có test

#### `[docs]` Hướng dẫn chạy Web UI + cảnh báo security
- **Labels:** `video-enh` `phase-2` `docs`
- **Estimate:** S

---

## Tóm tắt Phase 2

| Epic | Issues | Estimate | Ghi chú |
|------|--------|----------|---------|
| #V2.1 TTS đa-provider | 4 | ~2.5 ngày | Khuyến nghị làm |
| #V2.2 MoviePy engine | 3 | ~3 ngày | Chỉ khi cần timeline phức tạp |
| #V2.3 Web UI | 3 | ~3 ngày | `decision` — mặc định hoãn |
| **Tổng (nếu làm hết)** | **10** | **~8.5 ngày** | |
</content>
