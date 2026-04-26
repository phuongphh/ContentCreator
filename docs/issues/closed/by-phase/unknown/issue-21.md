---
issue_number: 21
title: "[Bug] Update TTS API integration to use Núi Trúc TTS endpoint"
phase: unknown
status: closed
labels: [bug]
github_url: https://github.com/phuongphh/ContentCreator/issues/21
updated_at: 2026-04-09T10:59:49Z
closed_at: 2026-04-09T10:59:49Z
---

# [Bug] Update TTS API integration to use Núi Trúc TTS endpoint

> GitHub: [#21](https://github.com/phuongphh/ContentCreator/issues/21) • Phase: unknown

## Context

Cập nhật lại phần generate audio trong pipeline để sử dụng API TTS mới của **Núi Trúc** thay vì API hiện tại.

---

## New TTS API Spec

**Endpoint:** 

**Request:**


**Parameters:**
| Field | Type | Description |
|-------|------|-------------|
|  | string | Nội dung cần chuyển thành giọng nói |
|  | string | ID giọng đọc (mặc định: ) |
|  | float | Tốc độ đọc (mặc định: ) |

**Output:** File MP3 trả về trực tiếp trong response body

---

## Required Changes

- Tìm và cập nhật phần code hiện tại đang gọi TTS API cũ
- Thay thế bằng endpoint mới: 
- Cập nhật request body theo đúng format mới (, , )
- Đảm bảo output MP3 được lưu/xử lý đúng như trước
- Cập nhật config/env nếu TTS endpoint đang được lưu dạng biến môi trường

---

## Acceptance Criteria

- [ ] Pipeline generate audio thành công bằng API mới
- [ ] Output MP3 có chất lượng và format đúng như mong đợi
- [ ]  và  có thể cấu hình được (không hardcode)
- [ ] Xử lý lỗi khi API TTS không phản hồi (timeout, 5xx) — không crash pipeline
- [ ] Log rõ ràng khi TTS call thành công/thất bại
