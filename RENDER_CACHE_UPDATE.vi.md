# Cập nhật dựng video theo đoạn — 24/09/2026

## Đã triển khai

- Hiển thị giây đã xử lý, tốc độ và ETA từ FFmpeg cho từng giai đoạn dựng hình, trộn tiếng và ghép MP4. Tiến độ không lùi khi các đoạn hoàn thành khác thứ tự.
- Cache hình theo biên cảnh, mục tiêu khoảng 45s/đoạn. Không cắt lại giữa một cảnh dài chỉ để đạt kích thước cache cố định, tránh thay đổi cách quy đổi frame của nguồn.
- Cache tiếng tách khỏi hình. Thay đổi âm lượng không tạo lại giọng TTS và không mã hóa lại hình khi nội dung hình/phụ đề không đổi.
- Trộn tiếng liên tục thành PCM, sau đó mã hóa AAC một lần khi đóng gói MP4; không nối nhiều audio AAC có padding riêng.
- Ghép các luồng hình đã mã hóa bằng stream copy; kiểm tra cấu hình codec tương thích, thời lượng, kích thước và audio trước khi công bố.
- Sửa một cue phụ đề chỉ làm mất cache đoạn chứa cue đó. Sửa chữ bị cháy vào hình vẫn phải dựng lại hình tương ứng; không tái sử dụng nhầm.
- Dữ liệu cache không phụ thuộc tên dự án hoặc các rule/provider AI khi chúng không thay đổi media thực tế.
- Chỉ đánh dấu cache sẵn sàng sau khi file hoàn tất và được kiểm tra; file lỗi/hủy không được dùng lại. Các đoạn đã hoàn thành được giữ cho lượt thử lại.
- Tối đa hai luồng NVENC ở đầu ra 1080p khi có ít nhất 8 CPU logic và GPU đầu tiên báo còn tối thiểu 768 MiB. Nếu không đo được tài nguyên, dùng một luồng. Nếu lỗi tài nguyên GPU, giảm còn một luồng; chế độ Auto mới được tiếp tục chuyển cả phần sang CPU khi cần. Không trộn codec của các đoạn khác encoder trong cùng file.
- Nút **Dựng thử 15s** dùng vị trí đầu phát hiện tại trên timeline, giới hạn trong phần đang xem. Xử lý trực tiếp ở 360×640. API lưu vị trí này trong job để thử lại đúng vị trí.

## Đo thực tế

Dự án: `bf7529d74de64c8d9f1c03a4b45a8b85`, “Bus Driver Caught Stabbing Passenger in 4K”. Thành phẩm 299,567s, 1080×1920/30fps, nguồn VP9/Opus. Giữ nguyên hiệu ứng, phụ đề, giọng, timeline và encoder NVENC p4 CQ26.

Các phép đo không gọi AI/ASR/TTS, không thay cấu hình hoặc danh sách bản xuất trong database của dự án. File thử được lưu riêng và tiến trình benchmark chỉ đọc dự án; tự dừng nếu có job người dùng bắt đầu.

| Đường dựng | Lượt 1 | Lượt 2 | Lượt 3 | Trung bình |
|---|---:|---:|---:|---:|
| Một lượt FFmpeg như renderer trước | 42,231s | 41,779s | 42,184s | **42,065s** |
| Cache từng đoạn, tối đa hai luồng, chưa có cache thành phẩm | 36,961s | 37,064s | 36,748s | **36,924s** |

Lượt đầu giảm khoảng **12,2%** trong phép so sánh cùng máy/cùng giai đoạn này. “Chưa có cache” ở đây là cache dựng của ứng dụng; cache hệ điều hành/ổ đĩa có thể đã nóng. Không dùng số 96s của lượt cũ trước đó để quảng cáo mức tăng tốc vì điều kiện tải máy khác nhau.

| Thao tác sau khi đã có cache | Số đo một lượt |
|---|---:|
| Xuất lại không thay đổi | **0,465s** |
| Chỉ đổi âm lượng AI; dùng lại cả 6 đoạn hình | **9,082s** |
| Preview 15s từ giây 60, 360×640 | **3,645s** |

Đường cache một luồng được thử trước đó mất 51,849s ở lượt đầu, chậm hơn baseline. Vì vậy chỉ dùng một luồng khi preview, CPU hoặc tài nguyên GPU không đủ; lợi ích dùng lại cache vẫn còn trong các trường hợp đó. Hai luồng là giới hạn có chủ đích, không tăng worker vô hạn.

## Kiểm chứng

- Bộ kiểm thử đầy đủ trước lần tinh chỉnh fallback cuối: **354 đạt, 1 bỏ qua**; build TypeScript/Vite thành công. Kiểm thử tập trung tiếp theo: **32 đạt**, bao gồm đường fallback GPU giảm số luồng rồi chuyển CPU.
- Kiểm thử video thực xác nhận đổi âm lượng giữ nguyên hash từng packet video, chỉ thay tiếng.
- Kiểm thử sửa một cue: 1 đoạn dựng lại, 5 đoạn tái sử dụng; âm thanh dùng lại.
- Hủy giữa chừng rồi chạy lại giữ cache các đoạn đã xong; file cache bị cắt hỏng chỉ làm dựng lại đoạn hỏng.
- Video thử có giọng kéo qua nhiều điểm nối không xuất hiện khe tiếng; PCM đủ đúng số mẫu 48kHz.
- Video VP9 thực: đủ **8.987 packet/frame**, DTS tăng đúng thứ tự. So 17 frame ở đầu/cuối và sát các điểm nối: đều ở đúng chỉ số frame, sai khác pixel trung bình lớn nhất khoảng **2,525/255** so với bản mã hóa một lượt. Đây là kiểm tra tương đối giữa hai bản nén, không phải chứng minh lossless hoặc đánh giá chất lượng tuyệt đối.

Số liệu máy đọc được: `data/bf7529d74de64c8d9f1c03a4b45a8b85/diagnostics/incremental-render-20260924-135128/` gồm `results.json`, `parallel-first.json`, `repeat-measurements.json`, `boundary-verification.json`.

## Vận hành và giới hạn

- Cache nằm trong `data/<project-id>/render-cache/`; file thành phẩm vẫn nằm trong `renders/`. Cache hình và PCM dùng thêm dung lượng, được xóa cùng dự án bằng chức năng xóa dự án.
- Cache cũ từ renderer nguyên phần vẫn được giữ; lần đầu dùng cấu trúc cache mới cần dựng và tạo cache mới.
- Không bật giải mã GPU cho hàng chục cảnh cùng lúc. Việc giảm số input đang hoạt động nhờ chia theo biên cảnh và tối đa hai worker là cải tiến đã đo được; đường GPU decode/scale toàn bộ chưa được đưa vào vì chưa có bằng chứng lợi ích ổn định với ASS và VRAM hiện tại.
- Tốc độ thực còn phụ thuộc codec nguồn, độ phức tạp hình, số cảnh, hiệu ứng, tải máy và dung lượng GPU trống. Không cam kết mọi video đều nhanh hơn đúng một tỷ lệ cố định.
