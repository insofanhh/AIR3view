# Reaction COPS: phân tích log và kế hoạch duration-first

## Kết quả đối chiếu log ngày 07/10/2026

- Chuẩn bị nguồn: 13:36:35–13:42:27, khoảng 5 phút 52 giây.
- Job đầu: 13:44:55–14:40:35, khoảng 55 phút 40 giây; thất bại khi lời kể sel0 phủ định chi tiết gián đã được xác nhận trong cue. Chỉ khả năng còn lại chưa được xác nhận.
- Job chạy lại: 14:42:23–15:46:15, khoảng 63 phút 52 giây; hoàn tất video 2 phút 30 giây. Các lô evidence/phân loại nguồn được đọc từ cache gần như ngay lập tức, không thực hiện lại toàn bộ request AI.
- Tạo/sửa voice: 14:47:45–15:45:10, khoảng 57 phút 25 giây; 12 đoạn VieNeu CPU/ONNX, nhiều vòng sửa lời để ép vào lịch cảnh. PyTorch không nạp được nên fallback ONNX/CPU.
- Dựng/mix/ghép/kiểm tra output: 15:45:10–15:46:15, khoảng 65 giây. Render hình không phải nút thắt chính của lần chạy này.

## Phạm vi bản v0.1.17

- Tăng lô transcript evidence từ 6.000 lên 9.000 ký tự; vẫn tự chia nếu OpenAI vượt giới hạn TPM/output.
- Tăng lô phân loại nguồn từ 32/80 lên 64/128 cue; recovery chỉ thử lại các cue thiếu/sai. Không suy diễn lời bình nguồn thành chứng cứ hiện trường.
- Nguồn trên 500 cue trong Efficient giữ nguyên từng cue và mốc nguồn thay vì tối ưu lại toàn bộ bằng AI. Không bỏ cue rỗng/trùng ID để che lỗi.
- Full review gom cửa sổ đọc ưu tiên khoảng 18–24 giây, tối đa 25 giây, giữ các cảnh hình ngắn riêng. Giọng và tốc độ vẫn cố định; không thay đổi tempo riêng để che lỗi.
- Bước viết/kiểm chứng giữ những đoạn đã duyệt và gửi câu bị từ chối cùng lỗi cụ thể vào lượt sửa tiếp. Sau ba lượt thường, thêm hai lượt bảo thủ nhưng vẫn bắt buộc qua kiểm chứng độc lập. Nếu tiếp tục sai, lưu audit và dừng với lỗi rõ ràng, không dùng câu mẫu chưa có chứng cứ.

## Luồng mục tiêu cần triển khai tiếp

Luồng script → toàn bộ voice → chọn/cắt cảnh chưa hoàn thành trong v0.1.17. Hiện vẫn chọn lịch footage trước khi viết lời và đo voice. Các hạng mục sau là kế hoạch, không phải tính năng đã phát hành:

1. Tạo evidence toàn nguồn một lần, cache theo nguồn/transcript/rules; kiểm tra coverage mở đầu, diễn biến và trạng thái cuối.
2. Tạo script độc lập với cảnh: các đoạn có chứng cứ, chương, thứ tự và thời lượng dự kiến theo tổng thời lượng user chọn.
3. Tạo voice từng đoạn bằng cùng profile/tốc độ, lưu WAV trước mọi bước khác. Đo tổng audio thật; chỉ chỉnh nội dung những đoạn cần thiết để đạt ngân sách tổng.
4. Chọn footage có nội dung phù hợp từng đoạn đã có voice; phân bổ nhiều cảnh cho một đoạn lời. Không lặp footage, freeze frame hoặc tự kéo dài chứng cứ.
5. Kiểm tra chronology, evidence, thời lượng và coverage toàn timeline; khóa lịch sau khi đủ audio.
6. Canh phụ đề và render một lần; cache đoạn hoàn tất để tiếp tục khi bị ngắt.

## Tiêu chí kiểm thử giai đoạn tiếp theo

- Giả lập và chạy thực nguồn ít/nhiều thoại, tiếng Việt/English, output 15/20 phút và các lịch cảnh 1–25 giây.
- Không chấp nhận kết quả sai phủ định/số liệu, không đưa lời bình nguồn vào chứng cứ, không bịa nội dung để kéo dài.
- Lần chạy lại giữ evidence/voice hoàn tất; không tạo lại voice vì thay nhạc hoặc bố cục.
- Đo thời gian riêng của từng bước, số request AI và số lần TTS/đoạn. Không cam kết thời gian 15–20 phút output trước khi benchmark trên thiết bị thực và provider đang dùng.
