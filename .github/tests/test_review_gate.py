"""A reviewer must explicitly PASS; unknown model output never clears a gate."""
import contextlib
import io
import os
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/code_review.py'


class ReviewGateTests(unittest.TestCase):
    def run_review(self, answer):
        client = Mock()
        client.messages.create.return_value.content = [Mock(text=answer)]
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
        self.assertEqual(self.run_review('PASS'), 0)

    def test_explicit_failure(self):
        self.assertEqual(self.run_review('FAIL: unsafe change'), 1)

    def test_ambiguous_output_is_not_success(self):
        self.assertEqual(self.run_review('Unable to determine a verdict'), 1)
