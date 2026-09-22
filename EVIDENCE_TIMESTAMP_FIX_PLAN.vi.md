# Plan sửa mốc evidence — 2026-09-22

## Phạm vi yêu cầu

Chẩn đoán lỗi và lập kế hoạch giao sub-agent Sol/high. Chưa thay đổi code hoặc chạy lại AI trả phí trong lượt kiểm tra này.

## Bằng chứng đã kiểm tra

- Dự án: `738ee9569a314202aff3678b01cb8fad` — Gen Z Driver Takes Police on 117mph Chase.
- Workflow: efficient; provider Gemini; model `gemini-3.1-flash-lite`.
- Nguồn dài 750.566 giây. Job thất bại ở 56%, trong batch hình thứ hai.
- Batch này có ảnh từ 127.467 đến 748.067 giây; khoảng hợp lệ truyền vào validator là 127.467–750.566 giây.
- Output cuối có scene `start=748.067, end=748.067`. Mốc không nằm ngoài video; nó là quan sát tại một thời điểm. Validator gộp lỗi `start >= end` vào thông báo chung «Mốc scene evidence nằm ngoài đoạn nguồn».
- Output còn có uncertainty `0–127.467`, nằm ngoài batch hiện tại; lỗi này sẽ lộ ra sau khi giải quyết scene điểm.
- Phản hồi ban đầu `8040b067adedfa71b4e8f02482b3c091ca6d32294a8db6c9ec936d7b65dc9bab.json` và phản hồi sửa `6aac6bce225157fdda3287b11593156bb895a2f2968f66773782215e235fd6fc.json` giống nhau về các mốc lỗi. Cả hai ở thư mục analysis-cache của dự án.
- Ảnh trong batch có khoảng trống từ 411.067 đến 748.067 giây. Thuật toán chọn ảnh giữ nhiều mốc chuyển cảnh ở phần đầu và có thể loại các ảnh phủ đều ở phần sau khi đạt trần 36 ảnh.

## Nguyên nhân

1. Prompt yêu cầu chỉ khẳng định điều quan sát tại một khung hình, nhưng mô hình dữ liệu chỉ biểu diễn khoảng thời gian có độ dài dương. Quan sát ảnh đơn vì vậy có thể bị từ chối dù timestamp đúng.
2. Context transcript được lọc theo giao nhau với batch nhưng không phân biệt rõ phạm vi context và phạm vi output. Uncertainty toàn nguồn bị kiểm tra như uncertainty của batch.
3. Lỗi sửa gửi lại quá chung, không nêu scene/field/số giây cụ thể; model lặp lại JSON lỗi.
4. Phân bổ ngân sách khung hình ưu tiên các mốc bổ sung theo thứ tự, làm thiếu coverage ở cuối nguồn.
5. Provider cache lưu JSON hợp lệ về schema trước khi validator nghiệp vụ kiểm tra; cache bước evidence chưa được ghi cho batch lỗi. Bấm thử lại nguyên cấu hình có thể đọc lại cả bản nháp lỗi và bản sửa lỗi, không tiến thêm.

## Phân công triển khai đề xuất

| Thứ tự | Agent Sol/high | Phần việc | Tiêu chí nghiệm thu |
| --- | --- | --- | --- |
| 1 | Agent hợp đồng evidence | Phân biệt quan sát điểm trong ảnh với khoảng diễn biến transcript. Giữ timestamp ảnh gốc; không tự kéo dài quan sát thành hành động. Kiểm tra điểm khớp ảnh được gửi và nằm trong nguồn; khoảng phải có start < end. | Quan sát cuối 748.067s được xử lý đúng loại; khoảng đảo chiều và mốc vượt nguồn vẫn bị từ chối. |
| 2, song song 1 | Agent chọn khung hình | Giữ ngân sách ảnh phủ đều toàn nguồn trước, rồi bổ sung điểm im lặng/chưa rõ/chuyển cảnh. Giữ đầu/cuối; chia batch với biên liên tục, tách context rộng khỏi output hợp lệ. | Bộ mẫu thật không còn khoảng trống 337s do phần đầu chiếm hết ngân sách; vẫn tuân thủ trần ảnh/call. |
| 3, sau 1 | Agent prompt/cache | Prompt ghi rõ loại evidence, biên batch, mốc ảnh; uncertainty output giới hạn batch, uncertainty toàn nguồn giữ ở tầng tổng hợp. Repair nhận danh sách lỗi chi tiết và chỉ sửa phần sai. Đổi version cache riêng cho vision/repair khi hợp đồng thay đổi; giữ cache transcript đã đạt. | Bản lỗi cũ không gây vòng thử lại vô hạn; không phải đọc lại toàn bộ transcript; không nới validator để chấp nhận timestamp tùy ý. |
| 4, sau 1–3 | Agent kiểm thử | Tạo fixture nhỏ từ các mốc lỗi thật, dùng phản hồi giả lập; kiểm tra điểm, đoạn, biên, NaN/Infinity, uncertainty ngoài batch, retry trả nguyên JSON, cache lỗi, coverage đầu/giữa/cuối, hủy và thử lại. | Lỗi hiện tại tái hiện trước bản sửa và qua sau sửa; không gọi API trong unit test. |
| 5 | PM | Review hợp nhất; chạy regression và build; kiểm tra UI lỗi có giai đoạn/batch/field/mốc nhận được/mốc cho phép. Sau đó mới chạy xác minh Gemini trên phần chưa đạt khi tiếp tục triển khai. | Không thay câu chuyện/kết thúc hoặc tạo mốc giả để qua test; không ảnh hưởng chế độ Chi tiết; báo riêng kết quả mocked test và kết quả API thật. |

## Các thay đổi không chấp nhận

- Không tắt validator hoặc cho mọi timestamp hợp lệ chỉ vì nằm trong tổng thời lượng.
- Không ép mọi `start=end` thành đoạn 1–3 giây vì ảnh đơn không chứng minh nội dung liên tục.
- Không xóa toàn bộ cache và gọi lại cả nguồn chỉ để tránh bản lỗi.
- Không coi API key/billing là nguyên nhân: Gemini đã trả đủ JSON trong lần chạy này.

## Trạng thái

PM đã kiểm tra job, cache và phạm vi batch. Hai agent đang đối chiếu độc lập validator và prompt/cache. Bảng trên là phân công cho giai đoạn sửa; các thay đổi runtime chưa được thực hiện trong yêu cầu lập plan này.
