"""Dev-only preview of the drama rewriter with canned model replies.

Runs the real processors.drama_rewriter.rewrite_story against a temporary
SQLite file (config.DB_PATH swapped for the duration), with the Anthropic
client and Telegram alerts replaced by in-process fakes. No network, no API key.
"""
from __future__ import annotations

import json
import os
import tempfile
from types import SimpleNamespace

SAMPLE_RAW = (
    "My roommate keeps eating my food and then lies about it. Last week I put a "
    "label on my leftovers and they still vanished. I confronted her and she "
    "said I was being dramatic. Now the whole group chat thinks I am the villain "
    "and I do not know whether to apologise or move out."
)

_VN_SENTENCE = "Cô ấy lặng lẽ nhìn cuộc trò chuyện rồi quyết định nói ra sự thật với cả nhà. "


def _script(words: int) -> str:
    base = _VN_SENTENCE.split()
    out = []
    while len(out) < words:
        out.extend(base)
    return " ".join(out[:words])


def _full(hook: str = "Lan mở tủ lạnh và thấy hộp cơm của mình biến mất lần nữa") -> dict:
    return {
        "title": "Cô bạn cùng phòng ăn trộm đồ ăn",
        "hook": hook,
        "script": _script(300),
        "vn_commentary": _script(80),
        "thumbnail_prompt": "a girl staring at an empty fridge, dramatic light",
        "tags": ["drama", "bạn cùng phòng"],
    }


def _dialogue_case() -> dict:
    data = _full()
    turns = []
    for i in range(8):
        who = "A" if i % 2 == 0 else "B"
        turns.append({
            "speaker": who,
            "display_name": "Lan" if who == "A" else "Minh",
            "text": _script(40),
        })
    data["dialogue"] = turns
    data["script"] = "Hội thoại giữa Lan và Minh."
    return data


def _msg(text: str, stop_reason: str = "end_turn"):
    # The rewriter prefills "{" and the API does not echo it back.
    return SimpleNamespace(
        content=[SimpleNamespace(text=text)],
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=0, output_tokens=0),
        model="canned",
    )


def _body(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False)[1:]


def build_cases() -> list[dict]:
    full = _full()
    missing = {k: v for k, v in full.items() if k != "tags"}
    long_hook = _full(hook=" ".join(["từ"] * 40))
    truncated = _body(full)[:200]
    return [
        {"name": "Hợp lệ (kể chuyện)", "narrative_format": "narrative",
         "replies": [_msg(_body(full))]},
        {"name": "Bị cắt cụt ở max_tokens", "narrative_format": "narrative",
         "replies": [_msg(truncated, "max_tokens")] * 3},
        {"name": "Thiếu trường, kết thúc bình thường", "narrative_format": "narrative",
         "replies": [_msg(_body(missing))] * 3},
        {"name": "Hook quá dài rồi hợp lệ khi sinh lại", "narrative_format": "narrative",
         "replies": [_msg(_body(long_hook)), _msg(_body(full))]},
        {"name": "Hội thoại có mảng dialogue hợp lệ", "narrative_format": "conversation",
         "replies": [_msg(_body(_dialogue_case()))]},
    ]


class _FakeClient:
    def __init__(self, replies):
        self._replies = list(replies)
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        if not self._replies:
            raise AssertionError("canned replies exhausted")
        return self._replies.pop(0)


def _run_case(case: dict) -> dict:
    import config
    import storage.database as database
    import storage.migrate as migrate
    import storage.stories as stories
    import storage.drama_narratives as narratives
    import processors.drama_rewriter as rewriter

    old_db = config.DB_PATH
    tmpdir = tempfile.mkdtemp(prefix="rewrite_preview_")
    config.DB_PATH = os.path.join(tmpdir, "preview.db")
    saved = (rewriter.anthropic.Anthropic, rewriter._alert_validation_failure,
             rewriter.time.sleep, rewriter.select_narrative)
    try:
        migrate.migrate_up()
        story_id = stories.insert_story("preview", "preview_1", SAMPLE_RAW,
                                        title="Mẫu")
        client = _FakeClient(case["replies"])
        rewriter.anthropic.Anthropic = lambda *a, **k: client
        rewriter._alert_validation_failure = lambda *a, **k: None
        rewriter.time.sleep = lambda *_: None
        fmt = case["narrative_format"]
        if fmt != "narrative":
            rewriter.select_narrative = lambda story: {"sequence": 1, "format": fmt}
        result = rewriter.rewrite_story(story_id)
        row = stories.get_story(story_id) or {}
        status = row.get("status")
        content = row.get("rewritten_content")
        try:
            payload = json.loads(content) if content else {}
        except json.JSONDecodeError:
            payload = {}
        reason = ""
        if status == "needs_review":
            if "_rewrite_error" in payload:
                reason = payload["_rewrite_error"]
            elif isinstance(payload, dict):
                issues, _ = rewriter.validate_rewrite_verdict(payload)
                reason = "; ".join(issues)
        return {
            "name": case["name"],
            "reply": " | ".join(
                (m.content[0].text[:120] + ("…" if len(m.content[0].text) > 120 else ""))
                for m in case["replies"]),
            "status": status,
            "reason": reason,
            "title": (result or {}).get("title"),
            "hook": (result or {}).get("hook"),
            "script": (result or {}).get("script"),
        }
    finally:
        (rewriter.anthropic.Anthropic, rewriter._alert_validation_failure,
         rewriter.time.sleep, rewriter.select_narrative) = saved
        config.DB_PATH = old_db
        for name in os.listdir(tmpdir):
            try:
                os.remove(os.path.join(tmpdir, name))
            except OSError:
                pass
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass


def run_preview() -> list[dict]:
    """Run all canned cases through the real rewrite_story path."""
    return [_run_case(case) for case in build_cases()]


def render_section(st) -> None:  # pragma: no cover - needs Streamlit runtime
    st.header("Viết lại kịch bản (Dev)")
    st.caption("Chạy đường rewrite_story thật với câu trả lời ghi sẵn · DB tạm · không mạng")
    if not st.button("Chạy xem trước", key="rewrite_preview_run"):
        return
    for item in run_preview():
        with st.expander(f"{item['name']} — {item['status']}", expanded=True):
            st.text_area("Câu trả lời ghi sẵn (rút gọn)", item["reply"], height=80,
                         key=f"rp_reply_{item['name']}")
            st.write(f"Trạng thái: **{item['status']}**")
            if item["reason"]:
                st.write(f"Lý do: {item['reason']}")
            if item["title"]:
                st.write(f"Title: {item['title']}")
                st.write(f"Hook: {item['hook']}")
                st.write(f"Script: {item['script']}")
