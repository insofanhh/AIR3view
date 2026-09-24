# Sửa tỷ lệ thoại gốc không phản ánh thiết lập

## Nguyên nhân đã xác nhận

Dự án “Bus Driver Caught Stabbing Passenger in 4K” đặt 70%, đầu ra khoảng 147s nhưng chỉ có hook và một câu gốc, tổng khoảng 7,2s (4,9%). Có 408 cue được phân loại participant, 67 commentary, không có unknown. Sau khi hợp nhất thời gian chồng nhau, nguồn có khoảng 638,28s tiếng người thật; cộng hook được phép lặp là 641,88s. Không phải nguồn thiếu hội thoại.

- Phụ đề tự động YouTube là các dòng hiển thị cuộn chồng nhau. Bộ chọn yêu cầu mọi cue giao với cảnh phải nằm trọn trong cảnh; các dòng lấn qua mép đã làm nhiều đoạn thật bị loại.
- Trần 70% không có kiểm tra phía dưới. Mốc 4,9% vẫn hợp lệ dưới trần.
- Bản chọn cảnh dành hơn 54s cuối cho phần lời bình nguồn; sửa bộ chọn tiếng trong các cảnh cũ thôi không đủ để đạt 70%. Cần chọn lại cảnh trên toàn nguồn và dành phần lời AI ngắn hơn để kể kết quả.

## Thay đổi

- Với nguồn `youtube_auto_subtitles`, cho phép cắt ở mốc cue đã quan sát khi các cue giao nhau đều là participant đủ độ tin cậy. Không nới quy tắc cho SRT/ASR thông thường; không cho lọt commentary hoặc unknown.
- Gộp các khoảng thoại, trừ phần người nói chưa rõ/lời bình; đếm thời lượng hợp nhất, không cộng trùng cue.
- Tạo nhiều ứng viên ở mốc cue, kể cả các đoạn con của cuộc hội thoại dài. Các slot AI liên tiếp cùng chặng không ép chia vụn tiếng gốc.
- Trên 50%, tỷ lệ là mục tiêu: ví dụ 70% tiếng gốc và khoảng 30% lời AI. Cho phép lệch tối đa 3 điểm phần trăm phía dưới để giữ câu; vẫn không vượt trần (ngoại lệ hook thật bắt buộc trên video quá ngắn được giữ như trước).
- AI nhận ngân sách số giây thoại gốc trước khi chọn cảnh, danh sách khoảng nguồn đủ điều kiện và đề xuất từ các chặng khác nhau. Vẫn phải giữ mở tình huống, diễn biến và kết quả, không lấy bình luận nguồn làm thoại thật.
- Nếu cảnh đã chọn chưa đạt nhưng toàn nguồn có đủ tiếng thật: yêu cầu lập lại cảnh, tối đa ba lượt, trước khi viết lời/TTS. Nếu vẫn không đạt, báo số đo rõ ràng và không xuất bản kịch bản gần như toàn AI.
- Với mục tiêu trên 50%, mỗi phần mở đầu, diễn biến và kết thúc đều phải có bridge AIR3view ngắn khi cần; bridge dài 4–8 giây, một hoặc hai câu, xen giữa các đoạn người thật. Bước kiểm tra coverage đọc chính phần thoại thực tế và yêu cầu bridge kết thúc nói ra kết quả đã được chứng minh; metadata `outcome` không được tính thay cho câu thoại.
- Nếu toàn nguồn thiếu tiếng thật: hạ ngân sách thực hiện theo lượng có bằng chứng, báo rõ mục tiêu/thực tế và số giây nguồn khả dụng; không chèn im lặng, lặp cảnh hoặc lời bình để bù.
- Phiên bản kế hoạch mới ngăn “Chạy toàn bộ” dùng lại bản lệch tỷ lệ cũ. Mức 10–50% giữ hành vi trần để tương thích cách kể thiên về AI.

## Kiểm chứng

Kiểm thử cục bộ trên các khoảng nguồn có bằng chứng của chính dự án; giữ chặng tranh cãi, cao trào, cảnh cảnh sát và đoạn kết nguồn để AI kể lại. Không gọi Gemini và chưa tạo lại toàn bộ TTS/MP4 dự án.

| Thiết lập | Tiếng gốc kế hoạch | Lời AI kế hoạch |
|---|---:|---:|
| 60% | 59,77% | 40,23% |
| 70% | 69,91% | 30,09% |
| 80% | 79,99% | 20,01% |

Tổng thời lượng thử 144,433s, trong khoảng 135–150s của thiết lập. Số liệu và lịch cảnh thử lưu ở `data/bf7529d74de64c8d9f1c03a4b45a8b85/diagnostics/retention-target/local-replay.json`. Đây là xác nhận thuật toán chọn tiếng và ngân sách, không phải MP4 mới đã hoàn tất.

Kiểm thử tự động bao gồm 60/70/80%, phụ đề chồng nhau, loại lời bình, không đếm trùng, thiếu nguồn và tự lập lại cảnh trước khi gọi viết lời.

Kiểm thử cấu trúc bổ sung gồm bridge mở đầu/diễn biến/kết thúc, giới hạn 1–2 câu, không có đoạn AI liên tiếp quá dài, review kết quả nói thực tế và không sửa các slot tiếng gốc.

Để áp dụng cho dự án cũ: tải lại trang, chọn tỷ lệ rồi Chạy toàn bộ. MP4 đã xuất không tự thay đổi.
