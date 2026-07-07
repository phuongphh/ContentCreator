# Drama Background Music Credits

Nhạc nền riêng cho track Drama (khác thư mục `video/assets/music/` của track
AI) — nên chọn nhạc **tense/dramatic**, hợp không khí kịch tính hơn nhạc nền
tips/news. Chỉ dùng nhạc **royalty-free / Creative Commons**. Mỗi track liệt
kê tại đây kèm nguồn + giấy phép trước khi commit/sử dụng.

| File | Nguồn | Giấy phép | Ghi chú |
|------|-------|-----------|---------|
| _(chưa có track nào)_ | | | Thêm file `.mp3` vào thư mục này rồi điền dòng tương ứng |

> `video/templates/drama.py` khai báo `music_track: "tense_minimal_loop.mp3"`
> làm gợi ý tên file ưu tiên (`pick_music(preferred_name=...)` trong
> `video/audio_mixer.py`) — nếu file đó chưa có trong thư mục này, hệ thống
> tự chọn ngẫu nhiên trong các track khác đã thêm, không chặn render.
>
> Gợi ý nguồn: YouTube Audio Library, Pixabay Music, Free Music Archive (CC0/CC-BY).
> Bật bằng `ENABLE_BGM=1`. Âm lượng nhạc điều chỉnh qua `BGM_VOLUME_DB`.
