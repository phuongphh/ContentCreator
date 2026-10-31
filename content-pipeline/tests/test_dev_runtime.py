"""Fresh-interpreter tests keep Dev mode out of the production unit suite."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

APP = Path(__file__).resolve().parents[1]


class DevRuntimeTests(unittest.TestCase):
    def run_script(self, script, development=True):
        with tempfile.TemporaryDirectory() as temporary:
            env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'SYSTEMROOT')}
            env.update(PYTHONPATH=str(APP), CONTENT_RUNTIME_MODE='development' if development else 'production')
            setup = ''
            if development:
                env['CONTENT_STATE_DIR'] = str(Path(temporary) / 'state')
                setup = 'from runtime_mode import initialize_dev_state\ninitialize_dev_state(__import__("os").environ["CONTENT_STATE_DIR"])\n'
            result = subprocess.run([sys.executable, '-c', setup + textwrap.dedent(script)],
                                    env=env, cwd=temporary, capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-4000:])

    def test_separate_paths_and_no_dotenv_lookup(self):
        self.run_script('''
            from unittest.mock import patch
            with patch('dotenv.load_dotenv', side_effect=AssertionError('Dev loaded dotenv')):
                import config
            import os
            from pathlib import Path
            root = Path(os.environ['CONTENT_STATE_DIR'])
            for path in (config.DB_PATH, config.VIDEO_OUTPUT_DIR, config.YOUTUBE_TOKEN_FILE, config.TIKTOK_QUEUE_DIR):
                assert Path(path).is_relative_to(root), path
            assert config.ANTHROPIC_API_KEY == config.TELEGRAM_BOT_TOKEN == ''
        ''')

    def test_unmarked_or_source_state_is_rejected(self):
        self.run_script('''
            import os, tempfile
            from pathlib import Path
            from runtime_mode import initialize_dev_state, state_path
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root/'existing').write_text('preserve')
                try: initialize_dev_state(root)
                except ValueError: pass
                else: raise AssertionError('Adopted existing data')
                os.environ['CONTENT_STATE_DIR'] = directory
                try: state_path('content.db', 'fallback')
                except ValueError: pass
                else: raise AssertionError('Accepted unmarked root')
        ''')

    def test_every_publishing_entry_rejects_before_io(self):
        self.run_script('''
            import importlib, inspect
            from unittest.mock import patch
            entries = {
                'publisher.youtube_uploader': ['_get_authenticated_service', 'upload_video', 'upload_to_youtube', 'upload_caption'],
                'publisher.tiktok_uploader': ['upload_video'],
                'publisher.tiktok_manual': ['export_for_manual_upload'],
                'scheduler.post_scheduler': ['run_tick'],
                'main': ['publish_video'],
            }
            for module, names in entries.items():
                target = importlib.import_module(module)
                for name in names:
                    fn = getattr(target, name)
                    args = [None for p in inspect.signature(fn).parameters.values()
                            if p.default is p.empty and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
                    with patch('builtins.open', side_effect=AssertionError('publishing I/O')):
                        try: fn(*args)
                        except RuntimeError as exc: assert 'disabled' in str(exc)
                        else: raise AssertionError(module + '.' + name)
            from video.review_service import approve
            with patch('video.review_service.get_video', side_effect=AssertionError('DB mutated')):
                try: approve(1, publish_callback=lambda _: None)
                except RuntimeError: pass
                else: raise AssertionError('Callback crossed Dev barrier')
        ''')

    def test_existing_ui_approves_and_rejects_synthetic_rows(self):
        self.run_script('''
            from pathlib import Path
            from streamlit.testing.v1 import AppTest
            from storage.migrate import migrate_up
            from storage.database import insert_video, update_video_status, get_video
            import config
            migrate_up()
            ids = [insert_video('short', 'Synthetic Dev script', youtube_title='DEV test '+str(i)) for i in range(2)]
            for ident in ids: update_video_status(ident, 'pending_approval')
            app = AppTest.from_file(str(Path(config.__file__).parent/'webui/app.py')).run()
            assert not app.exception, app.exception
            assert any('MÔI TRƯỜNG DEV' in value.value for value in app.warning)
            app.button(key='approve_'+str(ids[0])).click().run()
            assert not app.exception, app.exception
            assert get_video(ids[0])['status'] == 'approved'
            app.button(key='reject_'+str(ids[1])).click().run()
            assert get_video(ids[1])['status'] == 'rejected'
            assert any('không đăng' in value.value for value in app.success) or get_video(ids[0])['status'] == 'approved'
        ''')
