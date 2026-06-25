# Phase 2 — Extensibility (Detailed)

> **Mục tiêu:** Mở khả năng mở rộng *khi đã chứng minh giá trị ở P0/P1*: TTS
> đa-provider (giảm phụ thuộc 1 endpoint), engine MoviePy tuỳ chọn (timeline
> phức tạp), và công cụ preview/biên tập web tuỳ chọn.
>
> **P2 là OPTIONAL.** Chỉ kích hoạt từng Epic khi có nhu cầu thật — tránh kéo
> phụ thuộc nặng làm hỏng mục tiêu ~$4/tháng & vận hành nhẹ.

---

## 1. Phạm vi (mỗi Epic độc lập, bật theo nhu cầu)

| # | Hạng mục | Động lực | Khi nào nên làm |
|---|----------|----------|-----------------|
| A | **TTS đa-provider** | Núi Trúc là single point of failure | Khi endpoint từng down hoặc cần A/B giọng |
| B | **MoviePy engine** | FFmpeg flag-based chạm trần với timeline phức tạp | Khi cần transition/overlay nâng cao vượt P0/P1 |
| C | **Web preview UI** | Cần xem trước/chọn biến thể trước khi duyệt | Khi sản lượng tăng, cần biên tập nhiều |

---

## 2. Thiết kế

### 2.1 (P2.A) TTS đa-provider — `video/tts/`

```
video/tts/
  ├─ base.py      # TTSProvider: synthesize(text, out, voice, speed) -> path|None
  ├─ nuitruc.py   # provider hiện tại (chuyển từ tts_client)
  ├─ edge.py      # Edge TTS (vi-VN-HoaiMyNeural / NamMinhNeural) — miễn phí
  └─ factory.py   # get_provider(config.TTS_PROVIDER), fallback chain
```

- `tts_client.text_to_speech()` giữ nguyên làm **facade** → factory (consistency,
  `main.py` không đổi).
- **Fallback chain:** primary lỗi → thử provider kế (vd nuitruc → edge). Log rõ.
- **Quan trọng:** `text_preprocessor` chạy *trước* mọi provider → Edge cũng đọc
  số đúng. (Đây là lý do Edge ở MPT đọc số sai, còn ở đây thì không.)
- **Security:** mỗi provider tự quản endpoint/secret qua `.env`; không hard-code.
- **"Tốt hơn không?"** — interface 1 method, dễ thêm provider (Azure...) sau.
  Không over-engineer: chỉ 2 provider lúc đầu.

### 2.2 (P2.B) MoviePy engine tuỳ chọn — `video/composer_moviepy.py`

```
COMPOSER_ENGINE == "moviepy"?
 ├─ yes → composer_moviepy.compose(...)   # timeline layer-based
 └─ no  → video_composer.compose_video()  # FFmpeg (P0/P1) — mặc định
```

- Cùng chữ ký `compose(audio, subtitle, output, video_type, bg)` → hoán đổi được.
- MoviePy mạnh cho transition/animation phụ đề; nhưng nặng hơn → để sau FFmpeg.
- **Performance:** so bench với FFmpeg; chỉ khuyến nghị khi giá trị > chi phí thời
  gian render.
- Giữ FFmpeg là default; MoviePy chỉ là lựa chọn cắm thêm.

### 2.3 (P2.C) Web preview UI tuỳ chọn — `webui/` (Streamlit, local-only)

```
streamlit app (chạy local) → liệt kê video pending_approval
   → xem trước video + script → nút Approve/Reject (gọi cùng publish_callback)
```

- **Bổ sung, KHÔNG thay Telegram.** Telegram vẫn là cổng duyệt mobile chính.
- Web UI dùng khi cần *biên tập/chọn biến thể* tại bàn làm việc.
- **Security:** bind `127.0.0.1`, không expose ra ngoài; không nhúng secret vào
  client; thao tác ghi đi qua cùng lớp DB/publish đã có (1 nguồn sự thật).
- **UI/UX:** danh sách gọn, preview nhúng, trạng thái màu (pending/approved/
  published), tiếng Việt.

---

## 3. Acceptance Criteria (DoD — Phase 2, theo từng Epic bật)

- [ ] (A) `TTS_PROVIDER=edge` đọc tiếng Việt + số đúng (qua preprocessor); primary lỗi → fallback provider; facade cũ không đổi.
- [ ] (B) `COMPOSER_ENGINE=moviepy` ra video tương đương; default vẫn FFmpeg.
- [ ] (C) Web UI local liệt kê & duyệt được, dùng chung DB/publish; không expose ra ngoài.
- [ ] Mọi Epic default = tắt → pipeline P0/P1 không đổi.
- [ ] `unittest discover` xanh; test cho factory/fallback, engine-selector, webui logic (tách khỏi Streamlit runtime).

## 4. Rủi ro & giảm thiểu

| Rủi ro | Giảm thiểu |
|--------|-----------|
| Phụ thuộc nặng (MoviePy/Streamlit) phình | Mỗi Epic optional + default tắt; import lazy |
| Edge TTS đổi giọng/giới hạn | Fallback chain; vẫn giữ Núi Trúc primary |
| Web UI thành bề mặt tấn công | Local-only `127.0.0.1`, không secret client-side |
| Hai cổng duyệt lệch trạng thái | Dùng chung DB + `publish_callback` (single source of truth) |

## 5. Quyết định cần Phuong chốt (label `decision`)

- Có thực sự cần Web UI không, hay Telegram là đủ? (mặc định: khoan làm C)
- Provider TTS thứ 2 ưu tiên Edge hay Azure? (mặc định: Edge, miễn phí)
</content>
