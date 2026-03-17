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
```

## Bước 3: Load services

```bash
# Load pipeline (chạy mỗi sáng 7:00)
launchctl load ~/Library/LaunchAgents/com.ai5phut.pipeline.plist

# Load bot (chạy liên tục, approve → publish ngay)
launchctl load ~/Library/LaunchAgents/com.ai5phut.bot.plist
```

## Bước 4: Kiểm tra

```bash
# Xem trạng thái
launchctl list | grep ai5phut

# Xem log
tail -f ~/ContentCreator/content-pipeline/logs/bot_stdout.log
tail -f ~/ContentCreator/content-pipeline/logs/pipeline_stdout.log
```

## Quản lý

```bash
# Dừng bot
launchctl unload ~/Library/LaunchAgents/com.ai5phut.bot.plist

# Chạy pipeline thủ công
cd ~/ContentCreator/content-pipeline && python3 main.py

# Chạy bot thủ công (foreground)
cd ~/ContentCreator/content-pipeline && python3 main.py --bot
```
