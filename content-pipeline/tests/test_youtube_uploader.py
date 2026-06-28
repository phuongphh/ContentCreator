"""Tests for publisher.youtube_uploader caption helpers (caption-track upload).

The Google API client is never invoked here — we test the pure id parser and the
guard paths of upload_caption that run before any network/auth call.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from publisher.youtube_uploader import (
    _video_id_from_url, upload_caption, _has_required_scopes, SCOPES,
)


class TestHasRequiredScopes(unittest.TestCase):
    def test_all_scopes_present(self):
        self.assertTrue(_has_required_scopes(SCOPES))

    def test_superset_ok(self):
        self.assertTrue(_has_required_scopes(SCOPES + ["https://extra/scope"]))

    def test_missing_force_ssl_fails(self):
        # A token minted before caption upload only had youtube.upload.
        self.assertFalse(
            _has_required_scopes(["https://www.googleapis.com/auth/youtube.upload"])
        )

    def test_none_fails(self):
        self.assertFalse(_has_required_scopes(None))


class TestVideoIdFromUrl(unittest.TestCase):
    def test_youtu_be(self):
        self.assertEqual(_video_id_from_url("https://youtu.be/abc123"), "abc123")

    def test_youtu_be_with_query(self):
        self.assertEqual(_video_id_from_url("https://youtu.be/abc123?t=10"), "abc123")

    def test_watch_url(self):
        self.assertEqual(
            _video_id_from_url("https://www.youtube.com/watch?v=xyz789&list=1"),
            "xyz789",
        )

    def test_bare_id_passthrough(self):
        self.assertEqual(_video_id_from_url("rawid"), "rawid")

    def test_empty(self):
        self.assertEqual(_video_id_from_url(""), "")


class TestUploadCaptionGuards(unittest.TestCase):
    def test_no_video_id_returns_false(self):
        self.assertFalse(upload_caption("", "/whatever.srt"))

    def test_missing_srt_returns_false(self):
        self.assertFalse(
            upload_caption("https://youtu.be/abc123", "/no/such/file.srt")
        )


if __name__ == "__main__":
    unittest.main()
