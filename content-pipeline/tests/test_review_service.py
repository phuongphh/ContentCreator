"""Tests for video.review_service (P2 / V2.3) — shared approve/reject logic."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import video.review_service as rs


class TestApprove(unittest.TestCase):
    def test_missing_video(self):
        with patch.object(rs, "get_video", return_value=None):
            ok, msg = rs.approve(7)
        self.assertFalse(ok)
        self.assertIn("không tồn tại", msg)

    def test_wrong_status_blocked(self):
        # claim fails (no row in pending_approval) -> no publish.
        with patch.object(rs, "get_video",
                          return_value={"id": 7, "status": "published"}), \
             patch.object(rs, "claim_video_status", return_value=False):
            ok, msg = rs.approve(7, publish_callback=MagicMock())
        self.assertFalse(ok)

    def test_approve_transitions_and_publishes(self):
        publish = MagicMock()
        with patch.object(rs, "get_video",
                          return_value={"id": 7, "status": "pending_approval"}), \
             patch.object(rs, "claim_video_status", return_value=True) as claim:
            ok, msg = rs.approve(7, publish_callback=publish)
        self.assertTrue(ok)
        claim.assert_called_once_with(7, "approved", "pending_approval")
        publish.assert_called_once_with(7)

    def test_lost_race_does_not_publish(self):
        # Another reviewer already claimed it: claim returns False -> no publish.
        publish = MagicMock()
        with patch.object(rs, "get_video",
                          return_value={"id": 7, "status": "pending_approval"}), \
             patch.object(rs, "claim_video_status", return_value=False):
            ok, _ = rs.approve(7, publish_callback=publish)
        self.assertFalse(ok)
        publish.assert_not_called()

    def test_approve_without_callback(self):
        with patch.object(rs, "get_video",
                          return_value={"id": 7, "status": "pending_approval"}), \
             patch.object(rs, "claim_video_status", return_value=True):
            ok, _ = rs.approve(7)
        self.assertTrue(ok)


class TestReject(unittest.TestCase):
    def test_missing_video(self):
        with patch.object(rs, "get_video", return_value=None):
            ok, _ = rs.reject(9)
        self.assertFalse(ok)

    def test_reject_transitions(self):
        with patch.object(rs, "get_video",
                          return_value={"id": 9, "status": "pending_approval"}), \
             patch.object(rs, "update_video_status") as upd:
            ok, _ = rs.reject(9)
        self.assertTrue(ok)
        upd.assert_called_once_with(9, "rejected")


class TestListPending(unittest.TestCase):
    def test_delegates_to_db(self):
        with patch.object(rs, "get_videos_by_status",
                          return_value=[{"id": 1}]) as q:
            result = rs.list_pending()
        self.assertEqual(result, [{"id": 1}])
        q.assert_called_once_with("pending_approval")


if __name__ == "__main__":
    unittest.main()
