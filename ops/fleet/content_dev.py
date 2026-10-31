"""Serve the existing approval UI with synthetic data and no publishing services."""

import json
import os
from pathlib import Path
import subprocess
import sys

APPLICATION = Path(__file__).resolve().parents[2]/'content-pipeline'
sys.path.insert(0,str(APPLICATION))


def seed():
    from runtime_mode import DEVELOPMENT
    if not DEVELOPMENT:
        raise ValueError('The Dev launcher requires explicit development mode')
    import config
    from storage.migrate import migrate_up
    from storage.database import get_connection, insert_video, update_video_paths, update_video_status
    migrate_up()
    output=Path(config.VIDEO_OUTPUT_DIR);output.mkdir(mode=0o700,parents=True,exist_ok=True)
    clip=output/'fleet-dev-preview.mp4'
    if not clip.exists():
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i','color=c=blue:s=320x240:d=1','-c:v','libx264','-pix_fmt','yuv420p','-an',str(clip)],check=True,timeout=30)
    for title in ('DEV: mẫu duyệt video', 'DEV: mẫu nghiệm thu tiếp theo'):
        connection=get_connection()
        try:
            exists=connection.execute('SELECT id FROM videos WHERE youtube_title=?',(title,)).fetchone()
        finally:
            connection.close()
        if not exists:
            ident=insert_video('short','Nội dung thử nghiệm Dev, không đăng lên kênh.',youtube_title=title)
            update_video_paths(ident,video_path=str(clip));update_video_status(ident,'pending_approval')
    return {'fixture_ready':True,'database':config.DB_PATH,'publishing_enabled':False}


def main():
    result=seed()
    print(json.dumps(result),flush=True)
    os.execv(sys.executable,[sys.executable,'-m','streamlit','run',str(APPLICATION/'webui/app.py'),
             '--server.address','0.0.0.0','--server.port','8501','--server.headless','true',
             '--browser.gatherUsageStats','false','--server.fileWatcherType','none'])


if __name__=='__main__':
    main()
