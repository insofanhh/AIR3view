# Kế hoạch PM: tự sửa cảnh và lời thoại không khớp thời lượng

## Nguyên nhân xác minh trên dự án Filming YouTube

- Nguồn dài 1542,29 giây, mục tiêu một video 600 giây.
- Hai bản kế hoạch đầu chỉ đạt khoảng 373,4 giây, thiếu mức tối thiểu 450 giây.
- Bản tiếp theo đủ tổng thời lượng nhưng cảnh cuối bắt đầu 1482s khi cảnh trước kết thúc khoảng 1492,07s: chồng hơn 10 giây.
- Hook trong bản cache dài 7,2 giây, vượt hợp đồng 3–7 giây.
- Các câu trả lời đúng cấu trúc JSON vẫn được cache dù chưa đạt điều kiện lịch dựng. Chạy lại với cùng prompt có thể lặp lại bản lỗi.
- Số từ chỉ là ước lượng thời lượng; VieNeu không nhận thời lượng đích như OmniVoice. Cần đo audio thật để quyết định sửa lời.

## Vòng xử lý

1. **Kiểm tra lịch trước TTS:** đo mốc theo khung hình 30fps; báo index cảnh, đầu/cuối, cuối cảnh trước và loại lỗi. Không dùng thông báo gộp chung.
2. **Sửa hình học:** chuẩn hóa hook; bỏ phần đầu hình trùng đã được cảnh trước bao phủ khi vẫn giữ đủ thời lượng. Cảnh hợp lệ, bằng chứng và lời kể không đổi. Các lỗi không sửa được bằng phép tính sẽ yêu cầu AI trả mốc mới chỉ cho index lỗi, tối đa 3 lượt.
3. **Lưu checkpoint:** gắn kết quả với nguồn, draft và cấu hình. Mỗi lần sửa tăng generation để không lặp mãi cache sai. Không ghi kế hoạch vào dự án cho tới khi kiểm tra cuối đạt.
4. **Viết lời theo cảnh:** giữ câu đã đạt; chỉ sửa ID lỗi. Sau giới hạn sửa ước lượng số từ, có thể chuyển câu không rỗng, đúng ID/ngôn ngữ sang kiểm tra bằng audio thật. Không chấp nhận ID thiếu/trùng hoặc câu rỗng.
5. **Đo và sửa TTS:** tạo audio, đo độ dài, căn tốc độ trong giới hạn của dịch vụ. Nếu vẫn lệch, tính lại ngân sách lời từ tỷ lệ thời lượng đích/thực tế và yêu cầu AI sửa riêng đoạn đó, giữ ý nghĩa và bằng chứng.
6. **Lặp có giới hạn:** tối đa 8 lượt sửa lời mỗi đoạn và 96 lượt cho một job. Mạng, hết hạn mức, model chưa sẵn sàng và thao tác hủy được báo riêng; không retry như lỗi thời lượng.
7. **Giữ tiến độ:** lưu câu sửa và đồng bộ slot trong story_plan; chỉ audio/caption đoạn thay đổi bị tạo lại. Đo cả audio từ cache, không nhận dạng lại caption hợp lệ.
8. **Kiểm tra cuối:** audio nằm trong cảnh, không vượt ranh giới phần, thoại gốc không bị che, tổng thời lượng hợp lệ. Chỉ sau đó mới render.

## Tiêu chí kiểm thử

- Bản lỗi thực tế qua bước lập lịch, không còn overlap và hook vượt thời lượng.
- Sửa một đoạn không làm thay đổi mốc/nội dung/audio các đoạn hợp lệ khác.
- Audio còn lệch sau xử lý không được công bố thành công hoặc đưa vào render.
- Chạy lại không gọi ASR/TTS cho các đoạn có cache hợp lệ.
- Hủy tác vụ và lỗi API được truyền ra ngay; các vòng tự sửa đều có giới hạn.
- Test hồi quy bao gồm mốc ngoài nguồn, cảnh ngắn/chồng, số từ không đạt, cache và TTS lệch thời lượng.

## Giới hạn

Không có vòng AI nào bảo đảm hội tụ cho mọi câu, mọi model và mọi tình trạng máy. Khi hết lượt, ứng dụng phải báo đúng đoạn cùng thời lượng đo được, giữ tiến độ, và cho phép người dùng đổi model hoặc sửa lời. Không tăng tốc vô hạn, bịa thêm nội dung hay lặp hình để che lỗi.
