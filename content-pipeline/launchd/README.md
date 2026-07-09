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
./install.sh status      # service nào đã load / còn thiếu
./install.sh uninstall   # gỡ toàn bộ service
```

**Watchdog:** kể cả khi quên chạy installer, pipeline AI 07:00 (`main.py`) và
job drama-health tự kiểm tra `launchctl list` mỗi lần chạy và alert Telegram
nếu có plist trong repo chưa được load (`storage/launchd_status.py`); endpoint
`GET /health` cũng có section `launchd`.

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
