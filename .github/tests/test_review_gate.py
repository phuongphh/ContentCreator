"""A reviewer must explicitly PASS; unknown model output never clears a gate."""
import contextlib
import io
import os
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/code_review.py'


class ReviewGateTests(unittest.TestCase):
    def run_review(self, data=None, stop_reason="tool_use", text=None):
        client = Mock()
        client.messages.create.return_value.content = ([Mock(type="text", text=text)] if text is not None else [SimpleNamespace(type="tool_use", name="submit_review", input=data)])
        client.messages.create.return_value.stop_reason = stop_reason
        sdk = Mock(Anthropic=Mock(return_value=client))
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'pr_diff.txt').write_text('diff --git a/code.py b/code.py\n+value = 1\n')
            previous = Path.cwd()
            try:
                os.chdir(tmp)
                with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'test-placeholder', 'GITHUB_TOKEN': ''}, clear=True), patch.dict('sys.modules', {'anthropic': sdk, 'requests': Mock()}), patch.object(subprocess, 'run', return_value=Mock(stdout='code.py')), contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        runpy.run_path(str(SCRIPT), run_name='__main__')
                    return raised.exception.code
            finally:
                os.chdir(previous)

    def test_explicit_pass(self):
        self.assertEqual(self.run_review({"verdict": "PASS", "findings": []}), 0)

    def test_explicit_failure(self):
        self.assertEqual(self.run_review({"verdict": "FAIL", "findings": ["unsafe change"]}), 1)

    def test_plain_pass_is_not_structured_verdict(self):
        self.assertEqual(self.run_review(text="PASS", stop_reason="end_turn"), 1)

    def test_truncated_verdict_fails(self):
        self.assertEqual(self.run_review({"verdict": "PASS", "findings": []}, "max_tokens"), 1)

    def test_pass_with_findings_fails(self):
        self.assertEqual(self.run_review({"verdict": "PASS", "findings": ["unsafe"]}), 1)

    def test_unknown_or_malformed_verdict_fails(self):
        for data in [None, {}, {"verdict": "UNKNOWN", "findings": []}, {"verdict": "PASS", "findings": "none"}]:
            with self.subTest(data=data):
                self.assertEqual(self.run_review(data), 1)
