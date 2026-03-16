---
name: Content Pipeline Feature
about: Feature template cho dự án Content Pipeline "AI 5 Phút Mỗi Ngày"
title: "[FEATURE] "
labels: feature
assignees: ''
---

## 1. Business Context
- Đối tượng: (Người đi làm văn phòng / Content creator / Cả hai)
- Module liên quan: (Collector / Processor / Notifier / Pipeline)
- Mức độ ưu tiên: (Low / Medium / High)
- Ảnh hưởng chi phí API: (Không / Thấp / Trung bình / Cao)

## 2. Problem Statement
Mô tả rõ vấn đề cần giải quyết.

## 3. Functional Requirements
- [ ] Requirement 1
- [ ] Requirement 2
- [ ] Requirement 3

## 4. Acceptance Criteria
### Case A:
### Case B:
### Edge Case:

## 5. Content Pipeline Constraints (nếu có)
- Nguồn dữ liệu liên quan:
- AI model sử dụng: (Haiku / Sonnet / Không dùng AI)
- Ngưỡng scoring:
- Giới hạn token/chi phí:

## 6. Technical Constraints
- Python 3.10+
- Mỗi module phải chạy độc lập được
- Không hardcode API keys
- Xử lý lỗi gracefully — pipeline không crash nếu 1 nguồn lỗi
- Log đầy đủ mỗi bước

## 7. Non-Goals (Out of Scope)
Những gì không được làm.

## 8. Test Requirements
- [ ] Module chạy độc lập thành công
- [ ] Pipeline end-to-end không crash
- [ ] Edge case covered
- [ ] Log output đúng format
