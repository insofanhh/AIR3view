# Hook và văn phong: phân tích 4 video mẫu

Ngày kiểm tra: 24/09/2026. Đã tải đúng bốn URL người dùng cung cấp, xem khung hình tại 0/2/5/10/20/40 giây và nhận dạng lời kể tiếng Hàn bằng Whisper chạy cục bộ. Mốc dưới đây là xấp xỉ từ ASR, không phải căn từng từ thủ công. Tên riêng và các khẳng định pháp lý trong video chưa được kiểm chứng; báo cáo phân tích cách kể, không xác nhận tính đúng đắn của câu chuyện.

| Mẫu | Cách mở và triển khai | Điều có thể áp dụng |
|---|---|---|
| [7688677555997527309](https://www.tiktok.com/@tt325010nin/video/7688677555997527309) | Lời kể xuất hiện gần ngay đầu. Trong khoảng 0–7s, video đặt một nghịch lý: hành động cứu trẻ trong đám cháy lại dẫn tới cáo buộc. Sau đó trình bày hai lập luận, bằng chứng, rồi kết quả theo lời người kể. | Hook AI nêu nhân vật + tình huống + nghịch lý trung tâm, không mất thời gian chào hỏi. |
| [7688675488415370509](https://www.tiktok.com/@tt325010nin/video/7688675488415370509) | Hình hiện trường đối đầu ở đầu; lời kể tiếng Hàn bắt đầu khoảng 2,3s, nêu xung đột và hậu quả. Sau đó quay về nguyên nhân tranh chấp chỗ đỗ xe, diễn tiến, rồi các lập luận liên quan. | Đoạn hiện trường ngắn làm điểm vào, sau đó lời AI giải thích nguyên nhân. Không lấy việc “có người đối đầu trong hình” làm bằng chứng chắc chắn rằng có tiếng hét. |
| [7688672889607900430](https://www.tiktok.com/@tt325010nin/video/7688672889607900430) | Lời kể bắt đầu ngay cùng hình camera giám sát. Câu đầu nêu người, địa điểm và hành động chính; câu sau bổ sung nguyên nhân/hệ quả. Phần giữa giải thích chuỗi hành động rồi tới phản ứng và kết quả. | Khi không có tiếng hiện trường thích hợp, hình vẫn chạy và AI mở tình huống ngay, thay vì để hook im lặng. |
| [7688306140488207629](https://www.tiktok.com/@tt325010nin/video/7688306140488207629) | Đoạn hiện trường mở đầu ngắn; lời kể tiếng Hàn xuất hiện khoảng 2,6s. Người kể báo trước hệ quả chính, rồi quay về diễn biến dẫn tới hệ quả đó. | Có thể cho biết mức độ nghiêm trọng sớm, rồi kể trình tự nguyên nhân–diễn biến–kết quả; chỉ dùng hệ quả có bằng chứng. |

## Văn phong chung

- Vào tình huống ngay: chủ thể, hành động và điều đang bị đe dọa hoặc mâu thuẫn chính.
- Lời kể chủ yếu là câu trần thuật, động từ cụ thể, ít mở bài và bình luận lan man.
- Nêu điểm căng thẳng sớm, sau đó kể lại theo trình tự dễ hiểu; mỗi câu đưa thêm một diễn biến.
- Tiêu đề ngắn đặt phía trên, phụ đề chia thành cụm ngắn theo lời kể. Hình đổi giữa hiện trường và tư liệu minh họa.
- Các mẫu kết thúc bằng câu hỏi cho người xem. AIR3view chỉ nên dùng khi có vấn đề thực sự còn mở; không ép câu hỏi kích động, kết tội hoặc lặp lại ở mọi video.

Không sao chép cách gán tội danh, số liệu, lời dẫn hay hình minh họa thành bằng chứng. Không thể kết luận giọng là AI chỉ từ âm sắc. Bài học được áp dụng là cấu trúc kể chuyện, không phải sao chép nội dung mẫu.

## Quy tắc triển khai trong AIR3view

1. Phân loại lời nguồn; chấm riêng mức phù hợp làm hook. Hội thoại thông thường không tự động được xem là drama.
2. Ưu tiên một đoạn 3–7s có xung đột, phản ứng hoặc tiếng hét thật được ghi nhận; giữ tiếng gốc, không chồng lời AI. Không lấy bình luận nguồn làm hook.
3. Nếu không có ứng viên đủ bằng chứng, chọn hình phù hợp và viết một câu AIR3view nêu hoàn cảnh chung, nhân vật và mâu thuẫn của toàn câu chuyện. Không cố nhét toàn bộ sự kiện vào một câu.
4. Hook AI có slot thời lượng, cùng giọng/tốc độ, vòng sửa lời khi TTS chưa vừa, và phụ đề theo tiếng AI. Hook không làm tăng tổng thời lượng đã khóa.
5. Hook có tiếng thật được ưu tiên trước ngân sách thoại gốc. Nếu riêng hook đã vượt tỷ lệ trên video rất ngắn, giữ hook và không lấy thêm thoại gốc; không tự tắt tiếng hook.
6. Sau hook, bổ sung bối cảnh cần thiết, tránh đọc lại câu hook; kể các bước leo thang, bằng chứng quan trọng và kết quả thực tế.

Giới hạn: việc chọn tiếng hét không có lời phụ thuộc bằng chứng âm thanh được nhận dạng trong nguồn. Hình ảnh đơn lẻ hoặc âm lượng lớn không đủ để xác nhận tiếng người thật. Khi chưa chắc, dùng hook AI thay vì phát lời bình nguồn hoặc tự dựng tiếng hét.
