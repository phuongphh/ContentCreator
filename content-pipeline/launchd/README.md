# Hướng dẫn cài đặt LaunchD trên Mac Mini

## Cài đặt (1 lệnh — khuyến nghị)

```bash
cd ~/ContentCreator/content-pipeline/launchd && ./install.sh
```

`install.sh` là installer **idempotent** (issue #72 — quy trình copy/load thủ công
từng file trước đây khiến các plist Phase 5/6 chưa từng được load):

- Tự glob **toàn bộ** `com.ai5phut.*.plist` — plist mới thêm vào repo tự động
  được cài, không cần cập nhật README/script.
- Tự render placeholder `/Users/YOU/...` sang đường dẫn thật khi copy vào
  `~/Library/LaunchAgents/` — **không** sửa file trong repo.
- Chạy lại bao nhiêu lần cũng an toàn (service đang load được reload; bot có
  `RunAtLoad` nên tự chạy lại ngay).

Sau mỗi lần `git pull` có thêm/sửa plist, chỉ cần chạy lại `./install.sh`.

```bash
./install.sh status              # service nào đã load / còn thiếu
./install.sh reload [label]      # re-bootstrap toàn bộ (hoặc 1 label)
./install.sh uninstall           # gỡ toàn bộ service
```

## Vì sao plist bỏ WorkingDirectory/StandardOutPath (issue #74/#75)

**Root cause (đã kiểm chứng):** launchd/`xpcproxy` dựng `WorkingDirectory` +
`StandardOutPath`/`StandardErrorPath` **trước khi** exec binary, và giữ **handle
inode** của các thư mục đó. Khi thư mục bị **xoá & tạo lại** (rebuild venv,
re-clone repo, reconfig `.env`/token) → handle stale → xpcproxy setup fail, trả
`EX_CONFIG` (exit **78**) *trước khi binary chạy* (log file không hề được tạo,
foreground vẫn OK). Exit 78 **khoá** job vào "spawn scheduled" — KeepAlive cũng
không restart — cho tới khi `reload`. (Vì thế `pipeline` dù đã dùng wrapper vẫn
chết, còn daemon `bot` thường trú thì sống sót.)

**Fix tận gốc:** plist chỉ còn trỏ vào **một path string** — wrapper tĩnh
(`run_pipeline.sh` cho pipeline/bot, `run_module.sh` cho các job còn lại; launchd
re-resolve path mỗi spawn, KHÔNG giữ handle thư mục nào). Plist **không** khai báo
`WorkingDirectory`/`StandardOutPath`/`StandardErrorPath` nữa; wrapper tự `cd` +
redirect log vào `logs/${LOG_BASENAME}_stdout.log`/`_stderr.log` ở **runtime với
inode tươi**. Rebuild venv/re-clone không còn phá scheduled run; nếu vẫn kẹt (vd
job đã 78 từ trước khi cài bản mới), chạy `./install.sh reload`.

**Watchdog + self-heal:** kể cả khi quên chạy installer, pipeline AI 07:00
(`main.py`) và job drama-health (06:30/18:30) tự soi `launchctl list` mỗi lần chạy
(`storage/launchd_status.py`) và:
- alert Telegram nếu có plist trong repo **chưa được load** (issue #72);
- phát hiện service **đã load nhưng lần chạy gần nhất fail** (cột Status của
  `launchctl list`), **tự `reload`** cái kẹt `EX_CONFIG` (78) và alert (issue
  #74/#75). Watchdog bỏ qua chính service đang chạy nó (tránh tự bootout mình).

endpoint `GET /health` cũng có section `launchd`.

Quy ước: **tên file plist == Label bên trong** — cả `install.sh` lẫn
`storage/launchd_status.py` dựa vào điều này; giữ quy ước khi thêm plist mới.

## Danh sách service

| Service | Lịch chạy |
|---------|-----------|
| `com.ai5phut.pipeline` | 07:00 sáng — pipeline AI |
| `com.ai5phut.bot` | liên tục — Telegram bot (approve/reject, seed, review) |
| `com.ai5phut.reddit-drama` | 06:06 sáng — Reddit drama collector (Phase 2) |
| `com.ai5phut.drama-health` | 06:30 + 18:30 — alert collector im lặng >2 ngày |
| `com.ai5phut.drama-pipeline` | 06:40 sáng — Drama orchestrator end-to-end (Phase 5) |
| `com.ai5phut.post-scheduler` | tick 5 phút — upload video đã duyệt theo cadence (Phase 5) |
| `com.ai5phut.metrics-pull` | 23:00 đêm — YouTube Analytics (Phase 6) |
| `com.ai5phut.weekly-retro` | CN 19:00 — báo cáo tuần Telegram (Phase 6) |

## Kiểm tra

```bash
# Xem trạng thái
./install.sh status          # hoặc: launchctl list | grep ai5phut

# Xem log
tail -f ~/ContentCreator/content-pipeline/logs/bot_stdout.log
tail -f ~/ContentCreator/content-pipeline/logs/pipeline_stdout.log
tail -f ~/ContentCreator/content-pipeline/logs/reddit_drama.log       # rotating app log (14 ngày)
tail -f ~/ContentCreator/content-pipeline/logs/reddit_drama_stdout.log
```

## Quản lý

```bash
# Dừng bot
launchctl unload ~/Library/LaunchAgents/com.ai5phut.bot.plist

# Chạy pipeline thủ công
cd ~/ContentCreator/content-pipeline && python3 main.py

# Chạy bot thủ công (foreground)
cd ~/ContentCreator/content-pipeline && python3 main.py --bot

# Chạy Reddit Drama collector thủ công
cd ~/ContentCreator/content-pipeline && python3 -m collectors.reddit_drama_collector

# Chạy health check thủ công
cd ~/ContentCreator/content-pipeline && python3 -m storage.collector_health

# Chạy Drama orchestrator thủ công (Phase 5; --step render để chạy riêng 1 bước)
cd ~/ContentCreator/content-pipeline && python3 main_drama.py

# Xem/kick queue upload thủ công (Phase 5)
cd ~/ContentCreator/content-pipeline && python3 -m scheduler.post_scheduler list
cd ~/ContentCreator/content-pipeline && python3 -m scheduler.post_scheduler tick

# Analytics (Phase 6)
# Cấp token analytics cho từng kênh (1 lần, mở browser — scope chỉ-đọc)
cd ~/ContentCreator/content-pipeline && python3 -m analytics.youtube_puller auth ai_youtube
cd ~/ContentCreator/content-pipeline && python3 -m analytics.youtube_puller auth drama_youtube
# Pull số liệu thủ công
cd ~/ContentCreator/content-pipeline && python3 -m analytics.youtube_puller pull
# In thử báo cáo tuần (không gửi Telegram)
cd ~/ContentCreator/content-pipeline && python3 -m analytics.weekly_retro --print
# Dashboard KPI (cần: pip install streamlit)
cd ~/ContentCreator/content-pipeline && streamlit run dashboard/app.py
```
