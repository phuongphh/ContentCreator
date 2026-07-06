from __future__ import annotations

"""
Prompt loader — đọc prompt template theo version, cho phép rollback/A-B test
mà không cần sửa code (Phase 3 EPIC #3.4).

Convention: `content-pipeline/prompts/{module}/{name}.{version}.txt`.
Placeholder trong prompt dùng dạng `{{TEN_BIEN}}` (không phải `{ten_bien}` của
`str.format`) vì các prompt JSON-output chứa dấu ngoặc `{`/`}` thật trong ví
dụ schema — dùng `str.format` sẽ phải escape toàn bộ, dễ sai sót. Điền giá
trị bằng `render()` (str.replace đơn giản) thay vì `.format()`.
"""

import os

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "prompts")


def load_prompt(module: str, name: str, version: str | None = None) -> str:
    """Đọc nội dung prompt từ `prompts/{module}/{name}.{version}.txt`.

    Args:
        module: thư mục con, vd 'drama'.
        name: tên prompt, vd 'scorer', 'rewriter'.
        version: mặc định lấy từ `config.PROMPT_VERSION` — đổi env var
            `PROMPT_VERSION` để rollback mà không cần sửa code.

    Raises:
        FileNotFoundError: nếu không tìm thấy file prompt.
    """
    version = version or config.PROMPT_VERSION
    path = os.path.join(PROMPTS_DIR, module, f"{name}.{version}.txt")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Prompt not found: {path}")
    with open(path, encoding="utf-8") as f:
        return f.read()


def render(template: str, **values: str) -> str:
    """Thay các placeholder `{{KEY}}` trong `template` bằng `values[KEY]`."""
    result = template
    for key, value in values.items():
        result = result.replace(f"{{{{{key}}}}}", str(value))
    return result
