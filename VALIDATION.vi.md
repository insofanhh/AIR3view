# Xác minh AIR3view — 20/09/2026

Nguồn xử lý: https://www.youtube.com/watch?v=2Sj2fd6b_Cg (1127,659 giây, 1920×1080). Hai video ban đầu được dùng làm mẫu thành phẩm, không dùng làm nguồn cho bản dựng này.

## Đã kiểm chứng

- 62 kiểm thử Python đạt; frontend TypeScript/Vite build thành công.
- Hình chạy tiếp khi AI nói; audio nguồn bị tắt trong khoảng lời AI và bật lại sau đó, kể cả qua ranh giới hai phần.
- Có lựa chọn English cho title, phụ đề và OmniVoice. Tạo giọng tự chuyển text trước; xuất báo lỗi nếu nội dung còn ở ngôn ngữ cũ. Phân tích lại vẫn dịch title cũ khi đổi ngôn ngữ.
- Preset Theo video mẫu: nền mờ, title trắng trên hộp tối, hình gần vuông ở giữa, phụ đề sát mép hình, nhãn PART phía dưới. Đã đối chiếu trực quan preview và MP4 thực.
- Sửa thiếu RAM khi hook lấy từ đoạn sau rồi quay lại đầu nguồn: mỗi clip được đọc bằng input riêng có seek và giới hạn. Test kiểm tra thứ tự màu khung hình đạt; phần đầu Full HD đã xuất thành công trên máy 8 GB RAM.
- Audio TTS được lưu trước ASR, có kiểm thử tiếp tục từ cache sau lỗi canh phụ đề. Đã phục hồi một câu OmniVoice sau timeout và xác nhận độc lập nội dung bằng ASR.

- Phụ đề karaoke có mốc từng từ, tô đúng từ đang đọc, trả màu thường khi nghỉ. Preview và MP4/ASS dùng cùng timeline; SRT giữ văn bản thuần. Kiểm thử FFmpeg đo pixel xác nhận highlight di chuyển và tắt trong khoảng nghỉ; chỉnh text/mốc câu sẽ xóa mốc từ cũ.

## File đã xuất và kiểm tra

Dự án: `9e787ddf48214b7fa1e0f633564f28fe`.

- `karaoke-first-part/part-001.mp4`: bản mới nhất, 60 giây, 1080×1920, highlight từng từ và làm mờ phụ đề đóng sẵn. Giải mã đủ 1.800 khung hình và 2.813 audio frame. Khung hình `test-results/karaoke-fullhd-20.9.jpg` xác nhận từ “patrol” đang tô xanh.
- `review-preview/part-001.mp4`: bản xem trước 30 giây, 540×960; giải mã đủ 900 khung hình và audio.
- `review-first-part/part-001.mp4`: phần đầu đủ 60 giây, 1080×1920, 30 fps; giải mã đủ 1800 khung hình. Kèm ASS và SRT.
- Hai đoạn AI của phần đầu có tương quan waveform với WAV OmniVoice lần lượt 0,999689 và 0,999825; sai số dư tương đối 2,49% và 1,87% sau mã hóa AAC. Kiểm tra này xác nhận bản xuất phát voice riêng trong những đoạn đó. Kiểm thử âm đơn tần riêng xác nhận âm gốc bị tắt rồi bật lại.
- Chi tiết waveform: `test-results/english-part1-validation.json`. Khung hình: `test-results/english-part1-1080.jpg`.

## Đang hoàn tất

Kịch bản đầy đủ đã có 34 đoạn tiếng Anh. OmniVoice chạy trên CPU; tác vụ cũ đã dừng theo yêu cầu cập nhật công cụ, chờ người dùng chọn cấu hình mới. Dự kiến 19 phần với hook 4,9 giây và giữ toàn bộ nguồn. Chưa tuyên bố đã xuất xong cả 19 phần.

Server đã nạp bản sửa karaoke và API xóa mốc từ cũ sau khi biên tập. OmniVoice đã khởi động lại sau lỗi thiếu RAM; 11/34 giọng đã được giữ trong cache và đã canh từng từ.

## Giới hạn

Karaoke đã canh được 402/549 câu nguồn; 147 câu (gồm tiếng động, câu ngắn và đoạn khó nghe) thiếu mốc đủ tin cậy nên vẫn hiển thị chữ thường. Subtitle ASR cần kiểm tra tên riêng và mốc khó nghe; có thể sửa trong editor rồi bấm canh lại. Tùy chọn tiếng Anh chuyển text và voice AI; không dịch lồng tiếng toàn bộ audio gốc ngoài các đoạn AI.


## Cập nhật hai chế độ đầu ra và cấu trúc câu chuyện

- Đã thêm tab Đầu ra: một video tóm tắt, hoặc đúng số phần và số giây mỗi phần. Cấu hình được lưu trước khi chạy; dự án cũ yêu cầu chọn chế độ.
- Phân tích từng đoạn của toàn bộ nguồn xong mới gọi bước biên tập tổng thể. Kịch bản bắt buộc mở đầu, diễn biến có lời tóm tắt, kết thúc có kết quả và bài học; mở đầu trễ ít nhất khoảng hình gốc đã đặt sau hook.
- Kiểm thử phát hiện thiếu kết, chồng cảnh, vượt ngân sách, sai số phần, sai mốc nguồn, lời dài hơn cảnh; render FFmpeg thực xác nhận chuỗi màu hook → đầu → giữa → kết được lấy từ các đoạn nguồn cách nhau. Phụ đề và giọng ánh xạ sang timeline chọn cảnh.
- Giao diện đã kiểm tra trên server QA riêng: chọn nhiều phần, lưu 4 phần × 75 giây, tổng mục tiêu 5 phút; không tự khởi chạy job.
- Theo yêu cầu mới nhất, chỉ cập nhật công cụ. Đã hủy job voice cũ và dừng tiến trình nối xuất cũ, giữ các file đã làm. Không chạy AI/TTS hoặc export mới cho nguồn YouTube; người dùng sẽ tự chọn rồi bấm Chạy toàn bộ.
- Kiểm thử phần lập kịch bản dùng câu trả lời AI giả lập để xác minh luồng toàn nguồn, cấu trúc và kiểm soát lỗi; chưa đánh giá lại nội dung AI thực cho hai chế độ mới.
