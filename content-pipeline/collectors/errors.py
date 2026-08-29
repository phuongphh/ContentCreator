from __future__ import annotations

"""
Lỗi dùng chung cho tầng collector.

`CollectorAuthError` = credential của nguồn bị TỪ CHỐI (HTTP 401/403): sai,
hết hạn, hoặc bị thu hồi. Khác lỗi mạng/5xx ở hai điểm quyết định cách xử lý:

1. **Không tự khỏi.** Thử lại trong cùng lần chạy (hoặc ngày mai) vẫn 401 →
   fail fast, đừng nã thêm request (issue #117: Twitter/Product Hunt 401 mỗi
   sáng, log ERROR rồi trả 0 như thể "hôm nay không có tin").
2. **Cần người sửa.** Nên lỗi được NÉM LÊN cho `main.run_pipeline` gom vào
   `errors` → đi thẳng vào pipeline summary Telegram, thay vì chỉ nằm trong
   file log mà chỉ ai đọc log mới thấy.

Quy ước: message phải nói rõ cần làm gì (biến env nào, cấp lại ở đâu) và
TUYỆT ĐỐI không chứa giá trị token/key (message này được gửi qua Telegram).
"""


class CollectorAuthError(Exception):
    """Nguồn từ chối credential (401/403) — cần cấp lại key, không phải lỗi tạm thời."""

    def __init__(self, source: str, status: int, hint: str = ""):
        self.source = source
        self.status = status
        self.hint = hint
        message = f"🔑 {source} từ chối credential (HTTP {status})"
        if hint:
            message += f" — {hint}"
        super().__init__(message)
