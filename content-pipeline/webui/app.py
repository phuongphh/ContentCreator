from __future__ import annotations

"""
Web preview UI (P2, OPTIONAL, local-only) — thin Streamlit front-end for the
daily approval queue. Complements (does NOT replace) the Telegram approval flow.

Run locally only:
    streamlit run webui/app.py --server.address 127.0.0.1

Security: bind to 127.0.0.1 only; never expose publicly. No secrets are rendered
client-side. All state changes go through video.review_service (the same path
the Telegram bot uses), so the two surfaces can't diverge.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _publish(video_id: int) -> None:
    """Publish via the main pipeline callback (imported lazily)."""
    from main import publish_video
    publish_video(video_id)


def render() -> None:  # pragma: no cover - requires Streamlit runtime
    import streamlit as st
    from video.review_service import list_pending, approve, reject

    st.set_page_config(page_title="Duyệt video — AI 5 Phút", layout="wide")
    st.title("🎬 Hàng chờ duyệt video")
    st.caption("Local-only · dùng chung trạng thái với Telegram bot")

    pending = list_pending()
    if not pending:
        st.success("✨ Không có video nào đang chờ duyệt.")
        return

    for video in pending:
        vid = video["id"]
        title = video.get("youtube_title") or video.get("tiktok_caption") or ""
        with st.expander(f"#{vid} — {title}", expanded=True):
            path = video.get("video_path")
            if path and os.path.exists(path):
                st.video(path)
            else:
                st.warning("Không tìm thấy file video.")
            st.text_area("Script", video.get("script_text", ""),
                         height=200, key=f"script_{vid}")
            col1, col2 = st.columns(2)
            if col1.button("✅ Duyệt & đăng", key=f"approve_{vid}"):
                ok, msg = approve(vid, publish_callback=_publish)
                (st.success if ok else st.error)(msg)
            if col2.button("❌ Từ chối", key=f"reject_{vid}"):
                ok, msg = reject(vid)
                (st.success if ok else st.error)(msg)


if __name__ == "__main__":  # pragma: no cover
    render()
