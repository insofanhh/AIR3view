# Báo cáo xử lý lỗi lời kể không khớp lịch dựng

Ngày kiểm tra: 22/09/2026. PM điều phối hai sub-agent Sol High: backend sửa lỗi, QA tái hiện và kiểm tra độc lập.

## Kế hoạch và kết quả

1. Đối chiếu ảnh với job thật: job của dự án Hiker dừng ở 92%, trước bước OmniVoice.
2. Truy nguyên từ `plan_story` đến nhánh dự phòng `write_scheduled`.
3. Sửa việc thử lại cả nhóm và tái sử dụng câu trả lời lỗi trong cache.
4. Review chéo checkpoint, giới hạn số lần gọi, ID trùng/thiếu, thao tác hủy và lỗi provider.
5. Chạy test đầy đủ, build frontend, khởi động lại backend và chạy lại dự án thật.

## Nguyên nhân

- Các đoạn 3, 4, 5 lần lượt có 37, 34, 36 đơn vị lời kể; yêu cầu tương ứng là 39–57, 41–60, 40–59.
- Structured Output kiểm tra cấu trúc `id/text`, không bảo đảm số từ phù hợp thời lượng.
- Cơ chế cũ viết lại cả nhóm tối đa ba lần, kể cả các đoạn đã đạt.
- Provider cache lưu các câu trả lời đúng cấu trúc nhưng chưa đạt số từ. Cùng cấu hình và prompt dẫn tới cùng chuỗi trả lời lỗi khi bấm Thử lại.

## Bản sửa

- Prompt `TIMED NARRATION v2` tách khỏi chuỗi cache cũ.
- Giữ nguyên đoạn đã đạt, chỉ sửa các ID chưa đạt; tối đa sáu lượt mỗi nhóm mỗi lần chạy.
- Checkpoint lưu các đoạn đã đạt và số lượt sinh. Lần chạy tiếp theo tiếp tục phần còn thiếu với prompt mới.
- Fingerprint bao gồm ngôn ngữ, model/provider, tốc độ đọc, quy tắc viết, hook, toàn bộ lịch cảnh và ngữ cảnh nguồn.
- Ghi checkpoint qua file tạm rồi thay thế; bỏ qua cache sai cấu trúc và giá trị không phải chuỗi.
- Giữ kiểm tra số từ 80–120%, tỷ lệ thoại gốc, thời lượng và `validate_plan`. Không thêm chữ lặp hoặc bỏ kiểm tra để ép thành công.
- Hủy tác vụ và lỗi provider được báo ngay, không bị biến thành vòng sửa nội dung.

## Xác minh

- 179 test đạt, 1 test dành cho nền tảng khác được bỏ qua; gồm test FFmpeg và Windows DPAPI.
- Frontend build thành công.
- 13 test chuyên biệt kiểm tra sửa có mục tiêu, tiếp tục sau lỗi, ID thiếu/trùng, checkpoint hỏng, lỗi API/hủy và xác thực cuối cùng.
- Chạy thật dự án Hiker đã tạo đủ 46 đoạn lời kể đạt kiểm tra, lịch dựng 54 đoạn gồm thoại gốc, tổng 595,2 giây. Đã chuyển sang OmniVoice.
- Trạng thái kiểm tra xuất MP4: đang chạy; chưa xác nhận hoàn tất khi lập báo cáo này.

## Giới hạn

Bản sửa ngăn việc thử lại lặp đúng chuỗi lỗi cache và tránh viết lại đoạn đã đạt. Không thể cam kết mọi video luôn chạy thành công bất kể hạn mức API, kết nối, khả năng model, tài nguyên máy hoặc tình trạng OmniVoice. Các kiểm tra âm thanh thực và bản xuất vẫn cần được giữ để tránh tạo video lỗi.
