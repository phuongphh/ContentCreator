# Hướng dẫn cài đặt LaunchD trên Mac Mini

## Bước 1: Sửa đường dẫn

Mở 2 file `.plist` và thay `YOU` bằng username thật trên Mac:

```bash
sed -i '' 's|/Users/YOU|/Users/your-username|g' com.ai5phut.*.plist
```

## Bước 2: Copy vào LaunchAgents

```bash
cp com.ai5phut.pipeline.plist ~/Library/LaunchAgents/
cp com.ai5phut.bot.plist ~/Library/LaunchAgents/
cp com.ai5phut.reddit-drama.plist ~/Library/LaunchAgents/
cp com.ai5phut.drama-health.plist ~/Library/LaunchAgents/
cp com.ai5phut.drama-pipeline.plist ~/Library/LaunchAgents/
cp com.ai5phut.post-scheduler.plist ~/Library/LaunchAgents/
cp com.ai5phut.metrics-pull.plist ~/Library/LaunchAgents/
cp com.ai5phut.weekly-retro.plist ~/Library/LaunchAgents/
```

## Bước 3: Load services

```bash
# Load pipeline (chạy mỗi sáng 7:00)
launchctl load ~/Library/LaunchAgents/com.ai5phut.pipeline.plist

# Load bot (chạy liên tục, approve/reject + Drama seed commands)
launchctl load ~/Library/LaunchAgents/com.ai5phut.bot.plist

# Load Reddit Drama collector (chạy mỗi sáng 6:06 — Phase 2)
launchctl load ~/Library/LaunchAgents/com.ai5phut.reddit-drama.plist

# Load Drama collector health check (chạy 06:30 + 18:30 — alert Telegram nếu
# reddit_drama chưa chạy thành công quá 2 ngày)
launchctl load ~/Library/LaunchAgents/com.ai5phut.drama-health.plist

# Load Drama orchestrator (chạy 06:40 sáng — collect→score→rewrite→render→review, Phase 5)
launchctl load ~/Library/LaunchAgents/com.ai5phut.drama-pipeline.plist

# Load post scheduler (tick mỗi 5 phút — upload video đã duyệt đúng giờ cadence, Phase 5)
launchctl load ~/Library/LaunchAgents/com.ai5phut.post-scheduler.plist

# Load metrics puller (23h mỗi đêm — kéo số liệu YouTube Analytics, Phase 6)
launchctl load ~/Library/LaunchAgents/com.ai5phut.metrics-pull.plist

# Load weekly retro (Chủ nhật 19h — báo cáo tuần qua Telegram, Phase 6)
launchctl load ~/Library/LaunchAgents/com.ai5phut.weekly-retro.plist
```

## Bước 4: Kiểm tra

```bash
# Xem trạng thái
launchctl list | grep ai5phut

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
