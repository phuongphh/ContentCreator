# Phase 0 — Issues Master List

> Bản nháp Epic + sub-issues cho **Phase 0 (Stabilize & Secure)**. Push lên
> GitHub theo convention: Epic trước, sub-issue link `Part of #<epic>`.
> Mỗi sub-issue code có mục **Test required** — không merge nếu test đỏ.

---

## EPIC #V0.1 — Security: TLS-an-toàn cho TTS client

**Loại:** `epic` `video-enh` `phase-0` `security` `audio`
**Mô tả:** Bỏ `ssl.CERT_NONE` mặc định trong `video/tts_client.py`, chuyển sang
verify-by-default + opt-in permissive qua env. Đây là nợ bảo mật ưu tiên cao nhất.

**Definition of Done:**
- Verify cert bật mặc định; chỉ tắt khi `TTS_ALLOW_INSECURE_SSL=1`.
- Có log cảnh báo khi chạy chế độ insecure.
- Không token/secret nào bị log.

### Sub-issues

#### `[security]` Refactor opener TTS sang secure-by-default
- **Labels:** `video-enh` `phase-0` `security` `audio`
- **Estimate:** S
- **Mô tả:** Thay `_build_opener_with_ssl()` bằng `_build_opener(insecure: bool)`; mặc định dùng `ssl.create_default_context()` (verify ON). Đọc cờ từ `config.TTS_ALLOW_INSECURE_SSL`.
- **Acceptance:**
  - [ ] Mặc định: context có `check_hostname=True`, `verify_mode=CERT_REQUIRED`
  - [ ] `insecure=True`: log `WARNING` đúng một lần
  - [ ] **Test required:** `tests/test_tts_client.py` — assert thuộc tính context ở 2 chế độ (mock, không gọi mạng)

#### `[infra]` Thêm `TTS_ALLOW_INSECURE_SSL` vào config + `.env.example`
- **Labels:** `video-enh` `phase-0` `infra`
- **Estimate:** S
- **Acceptance:**
  - [ ] `config.TTS_ALLOW_INSECURE_SSL` (bool, default False)
  - [ ] `.env.example` có biến + comment giải thích rủi ro

#### `[test]` Đảm bảo không log secret
- **Labels:** `video-enh` `phase-0` `security` `test`
- **Estimate:** S
- **Mô tả:** Test rằng `Authorization`/token không xuất hiện trong log record khi gọi `_tts_single` (mock opener + capture logging).
- **Acceptance:**
  - [ ] **Test required:** assertNoLogContains("Bearer")

---

## EPIC #V0.2 — Composer: hạ O(N) input → O(1)

**Loại:** `epic` `video-enh` `phase-0` `video` `performance`
**Mô tả:** Gộp phụ đề thành **1 lớp** (ASS nếu libass khả dụng, else 1 video phụ
đề transparent) để số input ffmpeg không phụ thuộc số dòng phụ đề. Tách phần xây
lệnh thành hàm thuần để test được.

**Definition of Done:**
- Video 5–200 dòng phụ đề đều dùng số input ffmpeg cố định.
- API `compose_video()` không đổi chữ ký.
- Render long video ≥8' không lỗi; RAM không tăng theo N.

### Sub-issues

#### `[feat]` Hàm thuần `_srt_to_ass(entries, style)`
- **Labels:** `video-enh` `phase-0` `video` `subtitle` `feat`
- **Estimate:** M
- **Mô tả:** Chuyển list `(start, end, text)` → chuỗi file `.ass` hợp lệ (header `[Script Info]`/`[V4+ Styles]`/`[Events]`, style font/size/outline lấy từ `config`). Hỗ trợ tiếng Việt (UTF-8, font NotoSans).
- **Acceptance:**
  - [ ] Mỗi entry → đúng 1 dòng `Dialogue:` với timecode `H:MM:SS.cs`
  - [ ] Escape ký tự đặc biệt (`{`, `}`, newline)
  - [ ] **Test required:** `tests/test_ass_builder.py` — parse lại số Dialogue = số entry; timecode đúng; tiếng Việt giữ nguyên

#### `[feat]` Detect libass + chọn nhánh render
- **Labels:** `video-enh` `phase-0` `video` `feat`
- **Estimate:** S
- **Mô tả:** `_ffmpeg_has_libass()` chạy `ffmpeg -filters`/`-version` parse khả năng. Cache kết quả.
- **Acceptance:**
  - [ ] Trả bool; không crash khi thiếu ffmpeg
  - [ ] **Test required:** mock `subprocess.run` cho 2 trường hợp có/không libass

#### `[feat]` Fallback: render 1 video phụ đề transparent (no-libass)
- **Labels:** `video-enh` `phase-0` `video` `feat`
- **Estimate:** L
- **Mô tả:** Khi không có libass, render PNG mỗi entry (tái dùng `_render_one_subtitle` hiện có) rồi **ghép thành 1 video RGBA** theo timing (concat/`overlay` 1 lần) thay vì N input.
- **Acceptance:**
  - [ ] Số input ffmpeg = hằng (bg + audio + 1 phụ đề)
  - [ ] **Test required:** `build_compose_command(...)` trả lệnh có đúng 3 `-i` bất kể N

#### `[refactor]` Tách `build_compose_command()` thuần khỏi `_run_ffmpeg`
- **Labels:** `video-enh` `phase-0` `video` `refactor` `test`
- **Estimate:** M
- **Mô tả:** Phần lắp `cmd: list[str]` không I/O → hàm riêng để test; `compose_video` gọi nó rồi `_run_ffmpeg`.
- **Acceptance:**
  - [ ] `compose_video()` giữ nguyên signature + hành vi
  - [ ] **Test required:** assert cấu trúc lệnh (scale/pad, map, codec) cho long & short

#### `[test]` Bench: render time & input-count không tăng theo N
- **Labels:** `video-enh` `phase-0` `video` `test` `performance`
- **Estimate:** S
- **Mô tả:** Test tham số hoá: N ∈ {5, 50, 200} → số `-i` không đổi. (Bench thời gian thực ghi tay vào PR description.)
- **Acceptance:**
  - [ ] **Test required:** input-count bất biến theo N

---

## EPIC #V0.3 — Feature-flag scaffold + consistency

**Loại:** `epic` `video-enh` `phase-0` `infra`
**Mô tả:** Thêm nhóm flag video vào `config.py` (default = hành vi cũ) để P1/P2
bật/tắt an toàn, rollback gọn.

**Definition of Done:**
- Flag tồn tại, default giữ pipeline cũ y hệt.
- `validate_flags()` cảnh báo giá trị lạ.

### Sub-issues

#### `[feat]` Thêm nhóm flag video vào `config.py`
- **Labels:** `video-enh` `phase-0` `infra` `feat`
- **Estimate:** S
- **Mô tả:** `SUBTITLE_TIMING_MODE`, `BACKGROUND_MODE`, `TTS_PROVIDER`, `COMPOSER_ENGINE`, `ENABLE_BGM`, `TTS_ALLOW_INSECURE_SSL` (xem `phase-0-detailed.md` §2.3).
- **Acceptance:**
  - [ ] Default = hành vi cũ
  - [ ] **Test required:** `tests/test_config_flags.py` — default values + `validate_flags()` cảnh báo khi sai

#### `[docs]` Cập nhật `.env.example` + `CLAUDE.md` mục Video
- **Labels:** `video-enh` `phase-0` `docs`
- **Estimate:** S
- **Acceptance:**
  - [ ] Mỗi flag có comment ý nghĩa + giá trị hợp lệ

#### `[chore]` Test runner trong CI/launchd
- **Labels:** `video-enh` `phase-0` `infra` `chore`
- **Estimate:** S
- **Mô tả:** Đảm bảo `python -m unittest discover -s tests` chạy trong workflow/script trước khi deploy (tận dụng `code-review.yml` hoặc thêm bước test).
- **Acceptance:**
  - [ ] Bước chạy test tồn tại; đỏ thì chặn

---

## Tóm tắt Phase 0

| Epic | Issues | Estimate |
|------|--------|----------|
| #V0.1 Security TTS | 3 | ~1 ngày |
| #V0.2 Composer O(1) | 5 | ~2.5 ngày |
| #V0.3 Flags + consistency | 3 | ~1 ngày |
| **Tổng** | **11** | **~4.5 ngày** |
</content>
