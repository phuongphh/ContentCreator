# Google Sheets drama bridge — phễu nguồn ngoài cho track Drama

## Tại sao là Google Sheets, không phải N collector?

Các nguồn drama "béo bở" còn lại đều **không có API ổn định** để pipeline cào
trực tiếp:

| Nguồn | Thực trạng (kiểm chứng 07/2026) |
|---|---|
| Reddit RSS (`/hot/.rss`) | Vẫn tồn tại, nhưng Reddit **chặn fetcher datacenter từng đợt** (403) — Make.com, FreshRSS, RSS-Bridge đều có báo cáo dính chặn kéo dài từ vài phút tới vài ngày. KHÔNG phải "không bị chặn vì đi qua cổng phân phối công khai" — RSS vẫn là server Reddit, chỉ là IP của Make chưa/ít bị flag. Dùng được, nhưng là **best-effort**. |
| Quora topic RSS (`/topic/X/rss`) | **Đã khai tử** từ nhiều năm (hỏng từ ~2017). Không dùng được. |
| Facebook confession/group | **Không có RSS chính thức**; API chặn gắt. RSS.app/PageUnify là dịch vụ trả phí (free trial ngắn), độ bền phụ thuộc bên thứ ba. |
| Zhihu/Douyin (dịch qua group VN) | Không có đường tự động; bản chất là **dán tay** truyện đã dịch. |

Vì tầng cào nào cũng mỏng manh, kiến trúc đúng là **tách tầng cào khỏi tầng
nạp**: mọi nguồn đổ vào MỘT Google Sheet (miễn phí, bền, không rate-limit khi
đọc CSV export), pipeline chỉ đọc sheet:

```
[Make.com: RSS Reddit]   ┐
[Make.com: RSS bất kỳ]   ├──► Google Sheet ──► collect_all_gsheet() ──► stories
[RSS.app / Zapier]       │        (CSV export)      (dedupe + insert)
[dán tay drama VN]       ┘
```

- Reddit chặn Make → scenario nghỉ vài ngày, sheet còn nguyên, pipeline không hỏng.
- Thêm/đổi nguồn = sửa scenario Make hoặc dán thêm dòng — **không đổi code**.
- Nguồn drama TIN CẬY hàng ngày vẫn là HF AITA dump (issue #90); sheet này là
  kênh bổ sung cho nội dung tươi + drama Việt bản địa.

## Bước 1 — Tạo Google Sheet

1. Tạo sheet mới, hàng đầu tiên đặt header (không phân biệt hoa thường, nhận
   cả tiếng Việt có dấu):
   - `Title` (hoặc `Tiêu đề`) — **bắt buộc**
   - `Content` (hoặc `Nội dung`, `Body`) — **bắt buộc**
   - `URL` (hoặc `Link`) — nên có: dùng làm khoá dedupe ổn định
   - `Source` (hoặc `Nguồn`) — tuỳ chọn, ghi feed gốc để trace
2. Share: **Anyone with the link – Viewer** (hoặc File → Share → Publish to
   web → chọn CSV). KHÔNG cần quyền edit công khai — Make ghi bằng OAuth riêng.
3. Dán link sheet (dạng `/edit#gid=...` bình thường là được) vào `.env`:

   ```
   GSHEET_DRAMA_URL=https://docs.google.com/spreadsheets/d/<ID>/edit#gid=0
   ```

Chạy thử: `cd content-pipeline && python -m collectors.gsheet_drama_importer`

## Bước 2 — Dựng scenario Make.com (tự động hoá RSS)

Tài khoản Free của Make.com cho 1.000 operations/tháng — đủ cho vài feed chạy
2-4 lần/ngày.

1. Make.com → Create a new scenario.
2. Module 1: **RSS → Watch RSS feed items**, URL ví dụ:
   `https://www.reddit.com/r/AmItheAsshole/hot/.rss`
   (các sub khác: `relationship_advice`, `ProRevenge`, `MaliciousCompliance` —
   thay tên sub trong URL). Nếu Make báo 403: đặt custom User-Agent trong
   module RSS (Show advanced settings), hoặc chấp nhận chờ hết đợt chặn.
3. Module 2: **Google Sheets → Add a Row**, map:
   - `Title` ← RSS Title
   - `Content` ← RSS Content/Description (importer tự gỡ HTML)
   - `URL` ← RSS URL
   - `Source` ← tên feed (gõ tay, vd `reddit/AmItheAsshole`)
4. Schedule: 2-4 lần/ngày là đủ (top-of-day đổi chậm; tiết kiệm ops).

Bài RSS chỉ có link không có nội dung sẽ bị importer bỏ qua
(`GSHEET_MIN_BODY_CHARS`) — chọn feed có full content.

## Bước 3 — Dán tay drama Việt (không cần Make)

Confession FB, group "hóng biến", truyện Trung dịch sẵn: mở sheet, dán 1 dòng
`Title` + `Content` (cột URL để trống — importer dedupe bằng hash nội dung, dán
trùng không sao). Lần chạy `main_drama` kế tiếp sẽ nạp, chấm điểm, Việt hoá như
mọi story khác. Với truyện đã là tiếng Việt, rewriter vẫn chạy để chuẩn format
hook/script/commentary.

> **Bản quyền & an toàn:** nội dung sheet là nguồn NGOÀI chưa kiểm chứng — vẫn
> đi qua đủ rubric SAFE của `drama_scorer` và luật không-tên-thật của rewriter
> như mọi nguồn. Không dán nội dung nhận diện người thật.

## Vận hành

- `main_drama.py` bước collect gọi `collect_all_gsheet()` best-effort (nguồn
  lỗi không kéo sập bước collect; lỗi hiện trong summary Telegram).
- Mỗi lần chạy nạp tối đa `GSHEET_IMPORT_LIMIT` (mặc định 30) story MỚI; sheet
  dài hơn sẽ tự nạp tiếp các lần sau (dedupe theo `source_id`).
- Sheet chưa share đúng → lỗi rõ ràng "got an HTML page instead of CSV".
- Không cần dọn sheet: hàng đã nạp bị dedupe vĩnh viễn (hash URL/nội dung).
