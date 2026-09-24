# Sửa vòng lặp phân loại thoại nguồn

## Lỗi xác nhận trên dữ liệu thật

Dự án `5a453bbbf7de4a669f16fb9a7b46af17` có 859 cue. Lô yêu cầu 480–559 bị dừng vì ba phản hồi trong `analysis-cache` đều chứa 86 dòng: đủ 80 ID yêu cầu và thêm 560–565 từ ngữ cảnh. Thông báo cũ ghi “thiếu/lặp” nên không mô tả đúng nguyên nhân. Retry cũ gọi lại cùng prompt, đọc cùng phản hồi cached và lặp lỗi.

## Cơ chế mới

1. Lọc theo tập ID yêu cầu. ID ngữ cảnh dư không phải lỗi của các cue hợp lệ.
2. Phân biệt thiếu ID, trùng ID và dư ID. Không chọn một bản bất kỳ nếu ID trùng, kể cả hai bản giống nhau.
3. Khóa kết quả hợp lệ, chỉ hỏi lại ID chưa xong. Các phản hồi sau không được ghi đè cue đã chấp nhận.
4. Tối đa 4 vòng; lô sửa nhỏ dần 80 → 40 → 20 → 10. Một lô 80 cue có tối đa 15 lần gọi trong trường hợp tất cả đều lỗi. Không có vòng lặp vô hạn.
5. Lưu checkpoint trước/sau từng lần gọi. Mã thế hệ tăng trước request; bấm Thử lại tiếp tục với request mới, tránh đọc lại phản hồi lỗi mãi. Không cần xóa cache lô đã hoàn thành hoặc đổi API key/model.
6. Phản hồi sai schema được yêu cầu sửa; lỗi API, kết nối, hủy tác vụ không bị che thành lỗi ID hoặc tự thay bằng `unknown`.
7. Ghi cache qua file tạm, đọc lại, kiểm tra đủ/duy nhất rồi mới công bố lô hoàn tất. Khôi phục cache hoàn tất bị hỏng từ checkpoint hợp lệ nếu có.
8. Khi hết vòng vẫn thiếu, báo lô nào, đã lưu bao nhiêu, ID chưa xong; giữ nguyên dữ liệu đúng cho lần tiếp theo. Không biến phần chưa phân loại thành hội thoại thật hoặc lời bình giả định.

## Kiểm chứng

- Kiểm thử mô phỏng trên đúng số lượng transcript: 11 lô, 859/859 ID duy nhất; phản hồi dư không gây dừng. Phân loại trong phép mô phỏng không được đưa vào dự án thật.
- Phát lại đúng cache phản hồi thực `d2282c0c...`: 86 dòng được lọc thành 80 ID 480–559. Đã lưu lô hợp lệ vào cache của dự án. Sáu lô đầu được dùng lại, không gọi provider.
- Dự án thật có 560/859 cue hoàn tất sau khôi phục; dừng trước khi gọi AI ngoài máy cho 299 cue chưa có cache. Chưa chạy toàn bộ TTS/render.
- Bộ kiểm thử đầy đủ: 397 đạt, 1 bỏ qua. Các trường hợp bổ sung: ID dư, thiếu, trùng giống nhau/trùng mâu thuẫn, schema sai, cache hỏng, thay đổi thế hệ giữa các lần retry, lưu cue tốt trước lỗi mạng, hủy không công bố cache thiếu và chia nhỏ lô.

Mã chính: `backend/speech_recovery.py`, `backend/source_speech.py`. Checkpoint theo từng lô nằm trong `source-speech-cache/recovery/` của dự án. File này chỉ ghi dữ liệu/lỗi cần thiết của lô, không chứa khóa API.

Không cam kết mọi lỗi của toàn pipeline được loại bỏ; thay đổi này xử lý có kiểm thử các lỗi ánh xạ ID và vòng cache của bước phân loại thoại. Dự án khác tự dùng cùng cơ chế mới.
