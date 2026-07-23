"""Tests for video/image_generator.py (Phase 4 — Drama Visual Assets)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import video.image_generator as image_generator


class _FakeResp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _json_resp(obj):
    return _FakeResp(json.dumps(obj).encode("utf-8"))


class ImageGeneratorTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._cache_patch = patch.object(image_generator, "CACHE_DIR", self.tmp)
        self._cache_patch.start()
        self._cfg_patch = patch.multiple(
            image_generator.config,
            REPLICATE_API_TOKEN="fake-token",
            REPLICATE_MODEL_VERSION="fake-version-hash",
        )
        self._cfg_patch.start()
        self._sleep_patch = patch.object(image_generator.time, "sleep")
        self._sleep_patch.start()

    def tearDown(self):
        self._sleep_patch.stop()
        self._cfg_patch.stop()
        self._cache_patch.stop()


class TestGenerateIllustrationGuards(ImageGeneratorTestBase):
    def test_empty_prompt_returns_none_without_network(self):
        with patch.object(image_generator, "urlopen") as mocked:
            result = image_generator.generate_illustration("")
        self.assertIsNone(result)
        mocked.assert_not_called()

    def test_no_api_token_returns_none_without_network(self):
        with patch.object(image_generator.config, "REPLICATE_API_TOKEN", ""), \
             patch.object(image_generator, "urlopen") as mocked:
            result = image_generator.generate_illustration("a dramatic scene")
        self.assertIsNone(result)
        mocked.assert_not_called()

    def test_no_model_version_returns_none(self):
        with patch.object(image_generator.config, "REPLICATE_MODEL_VERSION", ""):
            result = image_generator.generate_illustration("a dramatic scene")
        self.assertIsNone(result)

    def test_cache_hit_skips_network(self):
        cache_path = image_generator._cache_path("a dramatic scene", 0)
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "wb") as f:
            f.write(b"cached-bytes")
        with patch.object(image_generator, "urlopen") as mocked:
            result = image_generator.generate_illustration("a dramatic scene")
        self.assertEqual(result, cache_path)
        mocked.assert_not_called()


class TestGenerateIllustrationHappyPath(ImageGeneratorTestBase):
    def test_full_flow_downloads_and_caches(self):
        responses = [
            _json_resp({"id": "pred123"}),                                  # create
            _json_resp({"status": "processing"}),                          # poll 1
            _json_resp({"status": "succeeded", "output": ["http://x/img.png"]}),  # poll 2
            _FakeResp(b"PNGDATA"),                                          # download
        ]
        with patch.object(image_generator, "urlopen", side_effect=responses):
            result = image_generator.generate_illustration("a dramatic scene")
        self.assertIsNotNone(result)
        with open(result, "rb") as f:
            self.assertEqual(f.read(), b"PNGDATA")

    def test_second_call_hits_cache(self):
        responses = [
            _json_resp({"id": "pred123"}),
            _json_resp({"status": "succeeded", "output": ["http://x/img.png"]}),
            _FakeResp(b"PNGDATA"),
        ]
        with patch.object(image_generator, "urlopen", side_effect=responses):
            first = image_generator.generate_illustration("a dramatic scene")
        with patch.object(image_generator, "urlopen") as mocked_second:
            second = image_generator.generate_illustration("a dramatic scene")
        self.assertEqual(first, second)
        mocked_second.assert_not_called()

    def test_string_output_also_accepted(self):
        responses = [
            _json_resp({"id": "pred123"}),
            _json_resp({"status": "succeeded", "output": "http://x/img.png"}),
            _FakeResp(b"PNGDATA"),
        ]
        with patch.object(image_generator, "urlopen", side_effect=responses):
            result = image_generator.generate_illustration("a dramatic scene")
        self.assertIsNotNone(result)


class TestGenerateIllustrationFailureModes(ImageGeneratorTestBase):
    def test_create_http_error_returns_none(self):
        with patch.object(image_generator, "urlopen",
                          side_effect=HTTPError("url", 500, "boom", None, None)):
            result = image_generator.generate_illustration("a dramatic scene")
        self.assertIsNone(result)

    def test_create_returns_no_id(self):
        with patch.object(image_generator, "urlopen", return_value=_json_resp({})):
            result = image_generator.generate_illustration("a dramatic scene")
        self.assertIsNone(result)

    def test_prediction_failed_status_returns_none(self):
        responses = [
            _json_resp({"id": "pred123"}),
            _json_resp({"status": "failed", "error": "model exploded"}),
        ]
        with patch.object(image_generator, "urlopen", side_effect=responses):
            result = image_generator.generate_illustration("a dramatic scene")
        self.assertIsNone(result)

    def test_poll_timeout_returns_none(self):
        # First monotonic() call computes the deadline (0 + POLL_TIMEOUT_SECONDS);
        # the second is the in-loop deadline check, made to exceed it — so the
        # loop returns None after exactly one poll fetch (matching the single
        # "processing" response provided below).
        with patch.object(image_generator, "urlopen") as mocked, \
             patch.object(image_generator.time, "monotonic", side_effect=[0, 1000]):
            mocked.side_effect = [
                _json_resp({"id": "pred123"}),
                _json_resp({"status": "processing"}),
            ]
            result = image_generator.generate_illustration("a dramatic scene")
        self.assertIsNone(result)

    def test_download_failure_returns_none(self):
        responses = [
            _json_resp({"id": "pred123"}),
            _json_resp({"status": "succeeded", "output": ["http://x/img.png"]}),
        ]
        with patch.object(image_generator, "urlopen") as mocked:
            mocked.side_effect = responses + [HTTPError("url", 404, "gone", None, None)]
            result = image_generator.generate_illustration("a dramatic scene")
        self.assertIsNone(result)

    def test_malformed_output_list_entry_returns_none_without_raising(self):
        # A non-image model or unexpected Replicate response could return an
        # empty/non-URL first list entry — this must degrade to None (the
        # gradient fallback), not raise ValueError out of generate_illustration.
        responses = [
            _json_resp({"id": "pred123"}),
            _json_resp({"status": "succeeded", "output": [""]}),
        ]
        with patch.object(image_generator, "urlopen", side_effect=responses):
            result = image_generator.generate_illustration("a dramatic scene")
        self.assertIsNone(result)

    def test_malformed_url_reaching_download_returns_none_without_raising(self):
        # Defense in depth: even if a schemeless/malformed string reaches
        # _download_image, urlopen's ValueError ("unknown url type") must be
        # caught like any other download failure, not propagate.
        responses = [
            _json_resp({"id": "pred123"}),
            _json_resp({"status": "succeeded", "output": ["not-a-real-url"]}),
        ]
        with patch.object(image_generator, "urlopen") as mocked:
            mocked.side_effect = responses + [ValueError("unknown url type: 'not-a-real-url'")]
            result = image_generator.generate_illustration("a dramatic scene")
        self.assertIsNone(result)


class TestUserAgentHeaders(ImageGeneratorTestBase):
    """Issue #97 — Cloudflare blocks urllib's default UA with 403; every
    Replicate request must carry the shared HTTP_USER_AGENT."""

    def test_create_prediction_sends_user_agent(self):
        with patch.object(image_generator, "urlopen",
                          return_value=_json_resp({"id": "pred123"})) as mocked:
            image_generator._create_prediction("a scene", "fake-token")
        req = mocked.call_args[0][0]
        self.assertEqual(req.get_header("User-agent"),
                         image_generator.config.HTTP_USER_AGENT)

    def test_poll_prediction_sends_user_agent(self):
        resp = _json_resp({"status": "succeeded", "output": ["http://x/img.png"]})
        with patch.object(image_generator, "urlopen", return_value=resp) as mocked:
            image_generator._poll_prediction("pred123", "fake-token")
        req = mocked.call_args[0][0]
        self.assertEqual(req.get_header("User-agent"),
                         image_generator.config.HTTP_USER_AGENT)

    def test_download_image_sends_user_agent(self):
        out = os.path.join(self.tmp, "img.png")
        with patch.object(image_generator, "urlopen",
                          return_value=_FakeResp(b"png-bytes")) as mocked:
            image_generator._download_image("https://x/img.png", out)
        req = mocked.call_args[0][0]
        self.assertEqual(req.get_header("User-agent"),
                         image_generator.config.HTTP_USER_AGENT)


class TestGenerateIllustrations(ImageGeneratorTestBase):
    def test_returns_only_successful_variants(self):
        call_count = {"n": 0}

        def fake_generate(prompt, index=0):
            call_count["n"] += 1
            return f"/fake/path_{index}.png" if index != 1 else None

        with patch.object(image_generator, "generate_illustration", side_effect=fake_generate):
            results = image_generator.generate_illustrations("a scene", count=3)
        self.assertEqual(results, ["/fake/path_0.png", "/fake/path_2.png"])
        self.assertEqual(call_count["n"], 3)


class TestCachedIllustration(ImageGeneratorTestBase):
    """cached_illustration — zero-cost reuse of on-disk variants (issue #103)."""

    def _write_variant(self, prompt: str, index: int) -> str:
        path = image_generator._cache_path(prompt, index)
        with open(path, "wb") as f:
            f.write(b"png")
        return path

    def test_empty_prompt_returns_none(self):
        self.assertIsNone(image_generator.cached_illustration(""))

    def test_no_cache_returns_none(self):
        self.assertIsNone(image_generator.cached_illustration("a prompt", 2))

    def test_exact_variant_preferred(self):
        self._write_variant("a prompt", 0)
        want = self._write_variant("a prompt", 2)
        self.assertEqual(image_generator.cached_illustration("a prompt", 2), want)

    def test_missing_variant_reuses_another_deterministically(self):
        only = self._write_variant("a prompt", 0)
        self.assertEqual(image_generator.cached_illustration("a prompt", 5), only)

    def test_other_prompts_cache_not_reused(self):
        self._write_variant("some other prompt", 0)
        self.assertIsNone(image_generator.cached_illustration("a prompt", 0))


class TestCachedIllustrationVariants(ImageGeneratorTestBase):
    """cached_illustration_variants — the composer's full-pool view (issue #105)."""

    def _write_variant(self, prompt: str, index: int) -> str:
        path = image_generator._cache_path(prompt, index)
        with open(path, "wb") as f:
            f.write(b"png")
        return path

    def test_empty_prompt_returns_empty(self):
        self.assertEqual(image_generator.cached_illustration_variants(""), [])

    def test_no_cache_returns_empty(self):
        self.assertEqual(image_generator.cached_illustration_variants("a prompt"), [])

    def test_returns_all_variants_sorted(self):
        v2 = self._write_variant("a prompt", 2)
        v0 = self._write_variant("a prompt", 0)
        self._write_variant("some other prompt", 0)
        self.assertEqual(
            image_generator.cached_illustration_variants("a prompt"), [v0, v2],
        )

    def test_missing_cache_dir_returns_empty(self):
        with patch.object(image_generator, "CACHE_DIR",
                          os.path.join(self.tmp, "does-not-exist")):
            self.assertEqual(
                image_generator.cached_illustration_variants("a prompt"), [],
            )


if __name__ == "__main__":
    unittest.main()
