"""Tests for the TTS provider factory + fallback chain (P2 / V2.1)."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import video.tts.factory as factory
from video.tts.factory import get_provider, synthesize, _provider_order


class FakeProvider:
    def __init__(self, name, result):
        self.name = name
        self.result = result
        self.called = False

    def synthesize(self, text, output_path, voice_id=None):
        self.called = True
        self.voice_id = voice_id
        return self.result


class TestGetProvider(unittest.TestCase):
    def test_nuitruc(self):
        self.assertEqual(get_provider("nuitruc").name, "nuitruc")

    def test_edge(self):
        self.assertEqual(get_provider("edge").name, "edge")

    def test_unknown_defaults_to_nuitruc(self):
        self.assertEqual(get_provider("bogus").name, "nuitruc")


class TestProviderOrder(unittest.TestCase):
    def test_primary_first(self):
        with patch.object(factory.config, "TTS_PROVIDER", "edge"):
            order = _provider_order()
        self.assertEqual(order[0], "edge")
        self.assertIn("nuitruc", order)

    def test_no_duplicates(self):
        with patch.object(factory.config, "TTS_PROVIDER", "nuitruc"):
            order = _provider_order()
        self.assertEqual(len(order), len(set(order)))

    def test_unknown_provider_normalized(self):
        # A typo must not add a bogus first attempt (which would double the
        # slow nuitruc retry budget); it normalizes to nuitruc.
        with patch.object(factory.config, "TTS_PROVIDER", "nuitrucc"):
            order = _provider_order()
        self.assertEqual(order, ["nuitruc", "edge"])


class TestSynthesizeFallback(unittest.TestCase):
    def test_primary_success_skips_fallback(self):
        primary = FakeProvider("nuitruc", "out.mp3")
        secondary = FakeProvider("edge", "edge.mp3")
        providers = {"nuitruc": primary, "edge": secondary}
        with patch.object(factory.config, "TTS_PROVIDER", "nuitruc"), \
             patch.object(factory, "get_provider", side_effect=lambda n: providers[n]), \
             patch("os.makedirs"):
            result = synthesize("xin chào", "out.mp3")
        self.assertEqual(result, "out.mp3")
        self.assertTrue(primary.called)
        self.assertFalse(secondary.called)

    def test_voice_id_passed_through_to_provider(self):
        primary = FakeProvider("nuitruc", "out.mp3")
        with patch.object(factory.config, "TTS_PROVIDER", "nuitruc"), \
             patch.object(factory, "get_provider", return_value=primary), \
             patch("os.makedirs"):
            synthesize("xin chào", "out.mp3", voice_id="preset_custom")
        self.assertEqual(primary.voice_id, "preset_custom")

    def test_none_voice_id_passed_through_by_default(self):
        primary = FakeProvider("nuitruc", "out.mp3")
        with patch.object(factory.config, "TTS_PROVIDER", "nuitruc"), \
             patch.object(factory, "get_provider", return_value=primary), \
             patch("os.makedirs"):
            synthesize("xin chào", "out.mp3")
        self.assertIsNone(primary.voice_id)

    def test_falls_back_when_primary_fails(self):
        primary = FakeProvider("nuitruc", None)
        secondary = FakeProvider("edge", "edge.mp3")
        providers = {"nuitruc": primary, "edge": secondary}
        with patch.object(factory.config, "TTS_PROVIDER", "nuitruc"), \
             patch.object(factory, "get_provider", side_effect=lambda n: providers[n]), \
             patch("os.makedirs"):
            result = synthesize("xin chào", "out.mp3")
        self.assertEqual(result, "edge.mp3")
        self.assertTrue(secondary.called)

    def test_all_fail_returns_none(self):
        providers = {"nuitruc": FakeProvider("nuitruc", None),
                     "edge": FakeProvider("edge", None)}
        with patch.object(factory.config, "TTS_PROVIDER", "nuitruc"), \
             patch.object(factory, "get_provider", side_effect=lambda n: providers[n]), \
             patch("os.makedirs"):
            self.assertIsNone(synthesize("xin chào", "out.mp3"))

    def test_text_passed_through_unmodified(self):
        # The factory must NOT re-run preprocessing — text arrives already
        # speech-normalized from upstream.
        captured = {}

        class Cap:
            name = "nuitruc"

            def synthesize(self, text, output_path, voice_id=None):
                captured["text"] = text
                return output_path

        with patch.object(factory.config, "TTS_PROVIDER", "nuitruc"), \
             patch.object(factory, "get_provider", return_value=Cap()), \
             patch("os.makedirs"):
            synthesize("năm mươi phần trăm", "out.mp3")
        self.assertEqual(captured["text"], "năm mươi phần trăm")


if __name__ == "__main__":
    unittest.main()
