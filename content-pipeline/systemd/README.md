# Hướng dẫn cài đặt trên Linux (systemd)

## Bước 1: Sửa đường dẫn (nếu cần)

Mở các file `.service` và `.timer`, sửa đường dẫn cho đúng:
- `WorkingDirectory=` → thư mục chứa `main.py`
- `ExecStart=` → đường dẫn python3 (`which python3`)
- `EnvironmentFile=` → đường dẫn file `.env`

## Bước 2: Copy vào systemd

```bash
sudo cp ai5phut-pipeline.service /etc/systemd/system/
sudo cp ai5phut-pipeline.timer /etc/systemd/system/
sudo cp ai5phut-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
```

## Bước 3: Enable và start

```bash
# Pipeline timer — chạy mỗi sáng 7:00
sudo systemctl enable --now ai5phut-pipeline.timer

# Telegram bot — chạy liên tục
sudo systemctl enable --now ai5phut-bot.service
```

## Bước 4: Kiểm tra

```bash
# Xem timer có active không
systemctl list-timers | grep ai5phut

# Xem bot status
systemctl status ai5phut-bot

# Xem log pipeline
journalctl -u ai5phut-pipeline --since today

# Xem log bot
journalctl -u ai5phut-bot -f
```

## Chạy pipeline thủ công

```bash
sudo systemctl start ai5phut-pipeline
```

## Quản lý

```bash
# Dừng bot
sudo systemctl stop ai5phut-bot

# Tắt timer
sudo systemctl disable ai5phut-pipeline.timer

# Restart bot
sudo systemctl restart ai5phut-bot
```
