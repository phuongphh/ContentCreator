# Phân tích luồng tạo video: Hiện tại vs MoneyPrinterTurbo

> Tài liệu đánh giá hệ thống (System review) — đứng ở góc nhìn **systems engineer + product manager + UI/UX**.
> Mục tiêu: trả lời câu hỏi *"giữ cách tạo video hiện tại hay chuyển sang MoneyPrinterTurbo?"*,
> và ở mỗi bước luôn tự hỏi **"có thể làm tốt hơn không?"**.
>
> Kết luận ngắn (TL;DR): **Không thay thế toàn bộ. Giữ backbone hiện tại, hấp thụ
> kỹ thuật dựng video của MoneyPrinterTurbo (MPT) một cách chọn lọc** — đây là
> phương án tối ưu về chất lượng, chi phí và rủi ro.

---

## 1. Toàn cảnh luồng hoạt động hiện tại

```
Thu thập (RSS/Twitter/Reddit/ProductHunt)
  → rule_filter (miễn phí)
  → ai_scorer (Haiku, rẻ)
  → ai_analyzer (Sonnet, top bài)
  → narrative (bản tổng hợp)
  ─────────────────────────────────────────────  ← ranh giới "content"
  → script_generator (Sonnet: long/short + metadata)
  → text_preprocessor (số → chữ tiếng Việt)        ★ tài sản riêng
  → tts_client (Núi Trúc Vietnamese TTS)           ★ tài sản riêng
  → subtitle_generator (SRT theo word-count)
  → pexels_downloader (background + duration-match + cache)
  → video_composer (FFmpeg + Pillow overlay PNG)
  → Telegram: gửi script + video để DUYỆT          ★ human-in-the-loop
  → /approve_<id> → publisher (YouTube / TikTok)
```

Điểm mạnh cốt lõi (moat) nằm ở 3 chỗ có dấu ★:
1. **Localization tiếng Việt sâu** — `text_preprocessor` chuyển `"$1,000"`,
   `"5-7%"`, `"GPT-4"`, `"năm 2024"` thành chữ đọc đúng; TTS giọng Việt; font
   NotoSans render dấu tiếng Việt.
2. **Tích hợp chặt với pipeline research** — video sinh ra *từ bản tin AI hằng
   ngày đã được chấm điểm và phân tích*, không phải từ một "topic" rời rạc.
3. **Human-in-the-loop qua Telegram** — duyệt trên điện thoại, `/approve` →
   đăng ngay. UX kiểm soát rất phù hợp cho kênh 1 người vận hành.

---

## 2. MoneyPrinterTurbo là gì (đối chiếu thực tế)

| Khía cạnh | MoneyPrinterTurbo |
|---|---|
| Dựng video | **MoviePy 2.x** trên nền FFmpeg — ghép nhiều clip, chuyển cảnh, nhạc nền |
| TTS | **Edge TTS** (miễn phí) mặc định; Azure TTS V2 (trả phí) |
| Tiếng Việt | Không quảng cáo, **nhưng** Edge TTS có 2 giọng `vi-VN-HoaiMyNeural` (nữ) & `vi-VN-NamMinhNeural` (nam) |
| Subtitle | **Whisper / faster-whisper** → timing chính xác theo audio (cần GPU để nhanh); hoặc dùng timestamp của Edge TTS |
| Background | Pexels / Pixabay / Coverr + material local; **đổi clip mỗi N giây** |
| Giao diện | **Streamlit Web UI** + **FastAPI REST API** |
| LLM | 15+ provider (OpenAI, Gemini, Ollama, DeepSeek, …) |
| Khác | Batch generation, nhạc nền, một-click publish |

Nguồn: README chính thức của dự án (MoviePy 2.x, Edge TTS, Pexels/Pixabay/Coverr,
Streamlit+FastAPI) và xác nhận Edge TTS có giọng vi-VN (xem mục Nguồn cuối tài liệu).

---

## 3. So sánh từng bước — *"có thể làm tốt hơn không?"*

### 3.1 Sinh script
- **Hiện tại:** Sonnet, prompt riêng cho long/short, tách script/metadata bằng
  delimiter `===SCRIPT===` (tránh vỡ JSON). Gắn liền narrative đã phân tích.
- **MPT:** sinh script từ keyword/topic — *generic*, không có ngữ cảnh bản tin.
- **Tốt hơn?** Pipeline hiện tại **thắng rõ**. Giữ nguyên. (Cải tiến nhỏ:
  thêm kiểm tra trùng câu tự động trước khi TTS.)

### 3.2 Chuẩn hoá text cho TTS
- **Hiện tại:** `text_preprocessor` — xử lý số/%, range, tiền tệ, dấu phẩy nghìn.
  Đây là thứ **MPT không có** và là lý do giọng đọc tiếng Việt nghe "đúng".
- **Tốt hơn?** Đây là tài sản phải giữ. MPT thua. ✅ Giữ.

### 3.3 TTS
- **Hiện tại:** Núi Trúc TTS (giọng Việt chuyên dụng). Rủi ro: phụ thuộc 1
  endpoint bên thứ ba, và **đang tắt xác thực SSL** (`CERT_NONE`) — nợ bảo mật.
- **MPT/Edge TTS:** miễn phí, ổn định (hạ tầng Microsoft), có vi-VN nhưng chỉ
  2 giọng và **không** chuẩn hoá số tiếng Việt.
- **Tốt hơn?** Có. → **Thêm Edge TTS làm provider thứ 2 (fallback/A-B test)**
  qua một interface `tts_client` chung. Giữ Núi Trúc làm primary vì chất lượng +
  `text_preprocessor`. Sửa lỗ hổng SSL (xem §6).

### 3.4 Subtitle / timing
- **Hiện tại:** chia theo *word-count tỉ lệ* với tổng duration. Nhanh, không cần
  GPU, **nhưng timing trôi** so với lời đọc thực tế → phụ đề lệch tiếng.
- **MPT:** Whisper → timing bám audio chính xác.
- **Tốt hơn?** **Có, đây là nâng cấp chất lượng đáng giá nhất.** → Bổ sung tuỳ
  chọn dùng `faster-whisper` để căn lại timing SRT (chạy CPU vẫn được với model
  `tiny/base`). Giữ thuật toán word-count làm fallback khi không có Whisper.

### 3.5 Background video
- **Hiện tại:** 1 clip Pexels loop cho cả video, có duration-match + cache.
  → **đơn điệu** với video dài 5-10 phút.
- **MPT:** ghép nhiều clip, đổi cảnh mỗi N giây, có nhạc nền.
- **Tốt hơn?** **Có.** → Nâng `video_composer` để (a) ghép nhiều clip nền theo
  segment, (b) thêm nhạc nền âm lượng thấp. Giữ pexels_downloader (đã có cache +
  fallback tốt), chỉ mở rộng để lấy *nhiều* clip.

### 3.6 Dựng video (composition) — *điểm yếu kỹ thuật lớn nhất hiện tại*
- **Hiện tại:** `_compose_with_overlay` tạo **một input FFmpeg cho mỗi dòng phụ
  đề** rồi `overlay` nối chuỗi. Video dài có 100+ segment → 100+ input + một
  `filter_complex` khổng lồ. Hệ quả: **chậm, ngốn RAM, dễ vỡ** (giới hạn độ dài
  dòng lệnh / số arg của FFmpeg). Đây là rủi ro performance & độ ổn định thật.
- **MPT:** MoviePy quản lý layer/timeline gọn gàng, scale tốt theo số phụ đề.
- **Tốt hơn?** **Có, cần sửa.** 2 lựa chọn:
  - (A) Chuyển `video_composer` sang **MoviePy** (mượn cách làm của MPT).
  - (B) Giữ FFmpeg nhưng render phụ đề thành **1 chuỗi PNG/ASS theo thời gian**
    thay vì N input (giảm từ O(N) input xuống O(1)).
  → Khuyến nghị (A) trung hạn, (B) là quick-win ngắn hạn.

### 3.7 Duyệt & UI/UX
- **Hiện tại:** Telegram — duyệt mobile, `/approve_<id>`, `/script_<id>`,
  `/status`. Nhẹ, đúng nhu cầu 1 người vận hành. Có PID-lock chống 409.
- **MPT:** Streamlit Web UI — mạnh khi *chỉnh sửa & xem trước nhiều biến thể*,
  nhưng cần mở port/đăng nhập, không tiện duyệt trên điện thoại lúc 7:30 sáng.
- **Tốt hơn?** Tuỳ ngữ cảnh. Cho luồng *duyệt hằng ngày* → Telegram thắng. Có
  thể bổ sung Web UI sau như công cụ *biên tập* khi cần nhiều biến thể. Không
  thay thế Telegram.

### 3.8 Publish
- **Hiện tại:** YouTube (OAuth) + TikTok, gắn với scheduler dual-format (T2/4/6
  short, T3/5/7 long, CN nghỉ).
- **MPT:** one-click publish nhưng không có logic lịch theo cadence kênh.
- **Tốt hơn?** Giữ nguyên. Pipeline hiện tại phù hợp chiến lược kênh hơn.

---

## 4. Bảng tổng hợp quyết định

| Bước | Giữ hiện tại | Mượn từ MPT | Hành động |
|---|:--:|:--:|---|
| Research → narrative | ✅ | | Giữ |
| Script (long/short) | ✅ | | Giữ |
| Chuẩn hoá số tiếng Việt | ✅ | | Giữ (moat) |
| TTS | ✅ primary | ➕ Edge TTS fallback | Interface đa-provider + sửa SSL |
| Subtitle timing | fallback | ✅ Whisper | Thêm faster-whisper |
| Background | ✅ cache | ✅ multi-clip + BGM | Mở rộng composer |
| Composition engine | ⚠️ | ✅ MoviePy/timeline | Refactor (điểm yếu lớn nhất) |
| Duyệt (UX) | ✅ Telegram | (Web UI optional) | Giữ Telegram |
| Publish + scheduler | ✅ | | Giữ |

**Vì sao KHÔNG thay thế toàn bộ bằng MPT:**
1. MPT là *topic → video*, không hiểu pipeline *bản tin AI đã chấm điểm*.
2. Mất toàn bộ localization tiếng Việt (`text_preprocessor`, Núi Trúc TTS).
3. Mất luồng duyệt Telegram + scheduler dual-format.
4. Kéo theo phụ thuộc nặng (MoviePy, Streamlit, FastAPI, torch/whisper-GPU) —
   nghịch với mục tiêu **~$4/tháng**, chạy nhẹ qua launchd trên máy cá nhân.

**Vì sao KHÔNG đứng yên:** composition O(N)-input dễ vỡ, subtitle trôi, nền đơn
điệu — đều là những điểm MPT làm tốt hơn rõ rệt.

→ **Hybrid là phương án tốt nhất.**

---

## 5. Lộ trình đề xuất (giảm rủi ro, làm tốt dần)

> Chi tiết kiến trúc + chia issue theo phase: xem
> [`video-enhancement/`](video-enhancement/README.md)
> (P0 detailed/issues · P1 detailed/issues · P2 detailed/issues).


- **P0 — Quick wins (ít rủi ro):**
  - Sửa lỗ hổng SSL trong `tts_client` (§6).
  - Giảm O(N) input trong composer (dùng chuỗi PNG/ASS theo timeline).
  - **Bổ sung unit test cho toàn bộ module video** *(đã làm trong PR này)*.
- **P1 — Chất lượng:**
  - Thêm `faster-whisper` để căn timing SRT (tuỳ chọn, fallback word-count).
  - Multi-clip background + nhạc nền.
- **P2 — Tuỳ chọn:**
  - Interface TTS đa-provider (Núi Trúc + Edge TTS).
  - Refactor composer sang MoviePy nếu cần timeline phức tạp hơn.
  - Web UI biên tập (chỉ khi cần nhiều biến thể/clip).

---

## 6. Ghi chú Security / Performance / Consistency

- **Security:** `video/tts_client.py` đang đặt `check_hostname=False` +
  `CERT_NONE`. Đây là MITM risk. Nên dùng CA bundle hợp lệ; chỉ fallback
  permissive khi có biến môi trường opt-in rõ ràng. (Ngoài phạm vi PR test này
  nhưng cần xử lý ở P0.)
- **Performance:** nút thắt là composition (§3.6) và việc gọi TTS 1 request dài
  (timeout 400s). Whisper nên dùng model nhỏ trên CPU để giữ chi phí.
- **Consistency:** mọi module đã theo cùng convention (chạy độc lập, `logging`,
  fallback graceful, `sys.path.insert`). Bộ test mới giữ đúng convention
  `unittest` như `tests/test_telegram_split.py`.

---

## 7. Nguồn

- MoneyPrinterTurbo — kiến trúc, MoviePy 2.x, Edge TTS, Pexels/Pixabay/Coverr,
  Streamlit + FastAPI, Whisper subtitle: <https://github.com/harry0703/MoneyPrinterTurbo>
- Edge TTS có giọng tiếng Việt (`vi-VN-HoaiMyNeural`, `vi-VN-NamMinhNeural`):
  [edge-tts voices list](https://gist.github.com/BettyJJ/17cbaa1de96235a7f5773b8690a20462),
  [Azure Vietnamese voices](https://json2video.com/ai-voices/azure/languages/vietnamese/)
</content>
</invoke>
