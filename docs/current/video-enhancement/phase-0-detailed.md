# Phase 0 — Stabilize & Secure (Detailed)

> **Mục tiêu:** Trước khi thêm tính năng mới, làm cho nền video pipeline **an
> toàn, ổn định và có thể bật/tắt từng phần**. Đây là blocker cho P1/P2.
>
> **Tại sao P0 trước:** thêm Whisper/multi-clip lên một composer còn O(N)-input
> và một TTS client còn `CERT_NONE` = xây nhà trên nền nứt. P0 vá nền.

---

## 1. Phạm vi

| # | Hạng mục | Vấn đề hiện tại | Kết quả mong muốn |
|---|----------|-----------------|-------------------|
| 1 | **Security TTS** | `video/tts_client.py` đặt `check_hostname=False` + `ssl.CERT_NONE` cho mọi request → MITM risk | Verify cert bằng CA bundle hệ thống; chỉ cho phép permissive khi opt-in rõ ràng qua env |
| 2 | **Composer O(N)** | `_compose_with_overlay` tạo 1 input ffmpeg / dòng phụ đề → 100+ input cho video dài | Phụ đề gộp thành **1 lớp** (ASS nếu có libass, else 1 video phụ đề transparent) → input không phụ thuộc N |
| 3 | **Feature-flag scaffold** | Hành vi hard-code, khó rollback khi thêm tính năng | `config.py` có nhóm flag video; default = hành vi cũ |
| 4 | **Test nền** | PR #55 đã phủ logic thuần; còn thiếu test cho code mới của P0 | Test cho SSL helper + composer builder + flags |

> **"Có thể làm tốt hơn không?"** — P0 cố tình *không* đổi chất lượng đầu ra
> (giữ nguyên timing word-count, nền single-clip). Chỉ đổi *cách dựng* để vừa
> nhanh hơn vừa an toàn hơn. Tách bạch "refactor an toàn" khỏi "tính năng mới"
> giúp review dễ và rollback gọn.

---

## 2. Thiết kế

### 2.1 Security — TLS cho TTS (`video/tts_client.py`)

Hiện tại:
```python
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE   # ⚠️ tắt xác thực cho MỌI request
```

Thiết kế mới — *secure by default, permissive by opt-in*:
```python
def _build_opener(*, insecure: bool) -> OpenerDirector:
    ctx = ssl.create_default_context()          # verify ON theo CA hệ thống
    if insecure:                                 # chỉ khi TTS_ALLOW_INSECURE_SSL=1
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        logger.warning("TTS SSL verification DISABLED via TTS_ALLOW_INSECURE_SSL")
    return build_opener(HTTPSHandler(context=ctx))
```
- Thêm `TTS_ALLOW_INSECURE_SSL` (mặc định `0`) vào `config.py` + `.env.example`.
- Nếu endpoint `tts.nuitruc.ai` chỉ phục vụ `http://` (không TLS) thì không có
  vấn đề SSL — nhưng nếu là `https://` với cert tự ký, phải opt-in *có ý thức*
  thay vì tắt mặc định.
- **Không log** body/token. `Authorization` header không xuất hiện trong log.

### 2.2 Composer — hạ O(N) → O(1) (`video/video_composer.py`)

Chiến lược 2 nhánh, chọn lúc runtime theo khả năng ffmpeg:

```
detect_libass() ?
 ├─ CÓ  → render 1 file .ass (styling + timing) → ffmpeg -vf "ass=subtitle.ass"
 │         (1 filter, 0 input ảnh)                       ← path ưu tiên
 └─ KHÔNG → render chuỗi PNG → ghép thành 1 video phụ đề RGBA transparent
             (concat theo timing) → ffmpeg overlay 1 lần  ← fallback, vẫn O(1) input
```

- Tách phần *xây lệnh ffmpeg* ra hàm thuần `build_compose_command(...)` trả về
  `list[str]` để **unit-test không cần chạy ffmpeg**.
- `_srt_to_ass(entries, style)` là hàm thuần → test trực tiếp.
- Giữ API `compose_video(...)` y nguyên (facade) → `main.py` không đổi.
- Số input ffmpeg = hằng số (bg + audio + ≤1 lớp phụ đề), không phụ thuộc N.

### 2.3 Feature-flag scaffold (`config.py`)

```python
# --- Video engine flags (P0 scaffold; default = hành vi cũ) ---
SUBTITLE_TIMING_MODE = os.getenv("SUBTITLE_TIMING_MODE", "wordcount")  # |whisper (P1)
BACKGROUND_MODE      = os.getenv("BACKGROUND_MODE", "single")          # |multi (P1)
TTS_PROVIDER         = os.getenv("TTS_PROVIDER", "nuitruc")            # |edge (P2)
COMPOSER_ENGINE      = os.getenv("COMPOSER_ENGINE", "ffmpeg")          # |moviepy (P2)
TTS_ALLOW_INSECURE_SSL = os.getenv("TTS_ALLOW_INSECURE_SSL", "0") == "1"
ENABLE_BGM           = os.getenv("ENABLE_BGM", "0") == "1"             # (P1)
```
- Một hàm `config.validate_flags()` log cảnh báo nếu giá trị lạ → tránh lỗi âm thầm.

---

## 3. Acceptance Criteria (Definition of Done — Phase 0)

- [ ] TTS verify cert mặc định; `TTS_ALLOW_INSECURE_SSL=1` mới tắt (có log cảnh báo).
- [ ] Composer dùng số input ffmpeg **cố định** với video có 5 hay 200 dòng phụ đề.
- [ ] Render video dài (≥ 8') hoàn tất không lỗi; đo thời gian render giảm hoặc tương đương, RAM không tăng theo N.
- [ ] Tất cả flag mới mặc định = hành vi cũ; pipeline cũ chạy y hệt khi không set env.
- [ ] `python -m unittest discover -s tests` xanh (bao gồm test mới của P0).
- [ ] Không secret nào bị log; `.env.example` cập nhật.

## 4. Rủi ro & giảm thiểu

| Rủi ro | Giảm thiểu |
|--------|-----------|
| FFmpeg bản máy không có libass | Có nhánh fallback PNG→1 video phụ đề; detect tự động |
| Đổi composer làm vỡ output | Giữ facade + so sánh thủ công 1 video long + 1 short trước khi merge |
| Endpoint TTS thực chất là self-signed | Flag opt-in giữ vận hành chạy được, vẫn an toàn mặc định |

## 5. Không làm trong P0 (để tránh scope creep)

- Không thêm Whisper (P1), không multi-clip/BGM (P1), không Edge TTS (P2),
  không MoviePy (P2), không Web UI (P2).
</content>
