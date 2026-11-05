import importlib
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _load_config(env=None):
    env = env or {}
    with mock.patch.dict(os.environ, env, clear=False):
        for k in ("TTS_VOICE_ID_DRAMA_GIRL", "TTS_VOICE_ID_DRAMA_BOY"):
            if k not in env:
                os.environ.pop(k, None)
        import config
        return importlib.reload(config)


class ConversationVoiceTest(unittest.TestCase):
    def test_defaults(self):
        cfg = _load_config()
        self.assertEqual(cfg.TTS_VOICE_ID_DRAMA_GIRL, "Mai Anh")
        self.assertEqual(cfg.TTS_VOICE_ID_DRAMA_BOY, "Thiền Tâm Đức")

    def test_override(self):
        cfg = _load_config({"TTS_VOICE_ID_DRAMA_GIRL": "X",
                            "TTS_VOICE_ID_DRAMA_BOY": "Y"})
        self.assertEqual(cfg.DRAMA_CONVERSATION_VOICES, {"A": "X", "B": "Y"})
        _load_config()

    def test_only_conversation_uses_new_voices(self):
        _load_config()
        from video import drama_composer as dc
        self.assertEqual(dc.voice_for_speaker("A", "conversation"), "Mai Anh")
        self.assertEqual(dc.voice_for_speaker("B", "conversation"), "Thiền Tâm Đức")
        self.assertNotEqual(dc.voice_for_speaker("A", "debate"), "Mai Anh")
        self.assertNotEqual(dc.voice_for_speaker("A"), "Mai Anh")

    def test_synthesize_passes_voices_verbatim(self):
        _load_config()
        from video import drama_composer as dc
        calls = []

        def synth(text, voice, path):
            calls.append(voice)
            return False  # stop after first call; we only inspect voice

        turns = [{"speaker": "A" if i % 2 == 0 else "B",
                  "display_name": "Lan" if i % 2 == 0 else "Nam",
                  "text": "x"} for i in range(6)]
        dc.synthesize_dialogue(turns, "/tmp/out.mp3", synth=synth,
                               narrative_format="conversation")
        self.assertEqual(calls, ["Mai Anh"])


if __name__ == "__main__":
    unittest.main()
