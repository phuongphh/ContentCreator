from __future__ import annotations

"""TTS provider package (P2) — đa-provider với fallback chain.

`tts_client.text_to_speech` là facade gọi vào `factory.synthesize`, chọn provider
theo `config.TTS_PROVIDER` và tự fallback sang provider còn lại nếu lỗi. Nhờ vậy
phần còn lại của pipeline không phụ thuộc vào một endpoint TTS duy nhất.
"""
