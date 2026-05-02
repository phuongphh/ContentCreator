# Phase 4 — Drama Video Production & TTS Upgrade

> **Mục tiêu:** Biến script Drama (đã có ở Phase 3) thành video MP4 sẵn upload. Bao gồm: nâng cấp TTS engine cho giọng cảm xúc, mở rộng `video_composer.py` thành multi-track (AI dùng template cũ, Drama dùng template mới), và thêm scene cho `vn_commentary`.

**Thời lượng dự kiến:** 7–10 ngày.
**Phụ thuộc:** Phase 3 (cần `rewritten_content` trong DB).
**Khoá phase sau:** Phase 5 (Distribution).

---

## 1. Bối cảnh

Repo đã có `content-pipeline/video/`:
- `tts_client.py` — đang dùng provider gì cũng chưa rõ (cần audit)
- `video_composer.py` — render video AI track
- `subtitle_generator.py` — Whisper
- `pexels_downloader.py` — stock background

Phase 4 không xây lại từ đầu mà **mở rộng** để:
1. TTS chọn voice profile theo `track`.
2. `video_composer` accept `track` parameter, áp template scene khác nhau.
3. Drama có 2 scene mới: `vn_commentary_overlay` và `lower_third` (hiện tên giả nhân vật).

---

## 2. Phạm vi

### Trong phạm vi
- Audit + refactor `tts_client.py` thành abstraction đa provider.
- Implement ≥2 provider TTS: ElevenLabs (drama) và provider Việt rẻ (FPT.AI / Zalo / Viettel) cho AI track.
- Mở rộng `video_composer.py` với param `track`.
- Tạo `video/templates/drama.py` và `video/templates/ai.py`.
- Scene `vn_commentary_overlay`: full-screen text với background gradient + giọng đọc khác.
- Scene `lower_third`: dải tên giả góc dưới khi nhắc nhân vật.
- Test render end-to-end 1 video drama 60s + 1 video AI 30s.

### Ngoài phạm vi
- Drama compiler render long-form (Phase 4 chỉ render TikTok-format trước, long-form sang Phase 5).
- Distribution (Phase 5).

---

## 3. Thiết kế kỹ thuật

### 3.1 TTS abstraction

```python
# content-pipeline/video/tts_client.py (refactored)

class TTSProvider(Protocol):
    def synthesize(self, text: str, voice: str, output_path: str) -> dict:
        """Returns {'duration': float, 'cost_usd': float}"""

class ElevenLabsProvider:
    def __init__(self, api_key: str): ...
    def synthesize(self, text, voice, output_path): ...

class FPTAIProvider:
    def __init__(self, api_key: str): ...
    def synthesize(self, text, voice, output_path): ...

PROVIDER_REGISTRY = {
    "elevenlabs": ElevenLabsProvider,
    "fpt_ai": FPTAIProvider,
}

# Mapping track → provider + voice
TTS_PROFILES = {
    "ai":    {"provider": "fpt_ai",     "voice": "banmai"},
    "drama": {"provider": "elevenlabs", "voice": "<voice_id_VN_female>"},
}

def synthesize_for_track(text: str, track: str, output_path: str) -> dict:
    profile = TTS_PROFILES[track]
    provider_cls = PROVIDER_REGISTRY[profile["provider"]]
    provider = provider_cls(api_key=os.getenv(...))
    return provider.synthesize(text, profile["voice"], output_path)
```

> Lý do tách provider: Drama cần giọng cảm xúc (ElevenLabs làm tốt), AI tip chỉ cần giọng đọc rõ ràng (FPT.AI rẻ hơn 5×). Tránh khoá vào 1 provider để dễ swap.

### 3.2 Video composer refactor

```python
# content-pipeline/video/video_composer.py

def compose_video(script_id: int, track: str, format: str = "shorts") -> str:
    """
    track: 'ai' | 'drama'
    format: 'shorts' (9:16, 60s) | 'long' (16:9, 8-15min)
    Returns: path to rendered MP4.
    """
    template = load_template(track, format)
    # template chứa: list scene + transition + audio mix recipe
    audio = synthesize_for_track(script.text, track, ...)
    subtitles = generate_subtitles(audio)
    
    scenes = []
    for scene_def in template.scenes:
        scene = render_scene(scene_def, script, audio, subtitles)
        scenes.append(scene)
    
    return ffmpeg_concat(scenes, output_path)
```

### 3.3 Drama template scenes

```python
# content-pipeline/video/templates/drama.py

DRAMA_SHORTS_TEMPLATE = {
    "format": "9:16",
    "duration_target": 75,  # seconds
    "scenes": [
        {"type": "hook", "duration": 3, "background": "ai_illustration", "subtitle_size": "xl"},
        {"type": "setup", "duration": 12, "background": "gradient_warm"},
        {"type": "escalation", "duration": 30, "background": "ai_illustration", "lower_third": True},
        {"type": "twist", "duration": 25, "background": "ai_illustration_dark"},
        {"type": "vn_commentary_overlay", "duration": 8, "background": "solid_blue"},
        {"type": "reflection_cta", "duration": 12, "background": "gradient_cool"},
    ],
    "transitions": "match_cut",
    "music_track": "tense_minimal_loop.mp3",
}
```

**AI illustration:** thay vì Pexels stock (không phù hợp drama), dùng prompt từ `rewritten_content.thumbnail_prompt` đẩy qua API ảnh AI (Replicate/Ideogram). Tạo 3 ảnh khác góc cho 3 scene khác nhau, cache.

**Lower third:** overlay PNG transparent với tên nhân vật + nhãn ("Mẹ chồng", "Hàng xóm"). Dùng FFmpeg `overlay` filter.

### 3.4 AI template (giữ tương thích)

```python
AI_SHORTS_TEMPLATE = {
    "format": "9:16",
    "duration_target": 45,
    "scenes": [
        {"type": "hook", "duration": 3, "background": "screen_record"},
        {"type": "tip_demo", "duration": 35, "background": "screen_record", "callouts": True},
        {"type": "cta", "duration": 7, "background": "solid_brand"},
    ],
}
```

> AI track vẫn cần Phuong quay screen-record manually cho phần demo. Pipeline chỉ ghép.

---

## 4. Acceptance criteria

- [ ] `tts_client.py` có ≥2 provider, swap qua env không cần đổi code.
- [ ] Render thành công 5 video drama TikTok format từ 5 script khác nhau.
- [ ] Render thành công 3 video AI TikTok format.
- [ ] Có overlay `vn_commentary` xuất hiện đủ ≥10% thời lượng video drama.
- [ ] Subtitle bám audio chuẩn (delta < 200ms).
- [ ] Cost per drama video render < $0.15 (TTS + AI image).

---

## 5. Rủi ro & cảnh báo

- **TTS giọng giật/robot:** test kỹ giọng, đặc biệt với từ Hán Việt và tên riêng. ElevenLabs v2 Vietnamese đôi khi đọc sai dấu. Có thể cần phonetic preprocessing trong `text_preprocessor.py`.
- **AI image cost trượt:** Mỗi video drama 3 ảnh × $0.04 = $0.12 (Replicate Flux). 30 video/ngày × 30 = ~$108. Cần cache theo theme và thumbnail tương đồng. Dùng style consistent (1 LoRA/preset cố định).
- **FFmpeg memory peak:** Render 9:16 với 6 scene + overlay có thể dùng 4–6 GB RAM. Mac Mini 24GB ổn nhưng cần queue tuần tự (không parallel).
- **Bản quyền nhạc:** `tense_minimal_loop.mp3` phải royalty-free. Mua 1 bundle Epidemic Sound hoặc dùng YouTube Audio Library.

---

## 6. Liên kết

- Phase trước: [`phase-3-detailed.md`](phase-3-detailed.md)
- Phase tiếp theo: [`phase-5-detailed.md`](phase-5-detailed.md)
- Issues: [`phase-4-issues.md`](phase-4-issues.md)
