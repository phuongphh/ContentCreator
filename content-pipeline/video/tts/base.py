from __future__ import annotations

"""Base interface for TTS providers (P2)."""

import abc


class TTSProvider(abc.ABC):
    """A text-to-speech backend.

    Implementations receive text that has ALREADY been normalized for speech
    (numbers/symbols expanded by text_preprocessor upstream) — they must not
    re-process it. They write audio to ``output_path`` and return that path on
    success, or None on failure (so the factory can try the next provider).
    """

    name: str = "base"

    @abc.abstractmethod
    def synthesize(self, text: str, output_path: str) -> str | None:
        """Synthesize *text* to ``output_path``; return the path or None."""
        raise NotImplementedError
