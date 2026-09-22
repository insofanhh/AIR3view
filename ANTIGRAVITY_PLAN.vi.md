# Kế hoạch kết nối Antigravity với AIR3view

Ngày kiểm tra: 2026-09-22. PM: main agent. Các sub-agent được giao dùng `gpt-5.6-sol`, reasoning `high`.

## Kết luận hiện tại

Máy có Antigravity IDE (User) 2.5.5. Launcher `antigravity-ide.cmd` cung cấp lệnh `chat` nhận prompt và `--add-file`, nhưng dùng để mở chat trong IDE; chưa có giao diện nhận kết quả headless/JSON để làm provider cho AIR3view.

Chưa tìm thấy Antigravity CLI `agy` trong PATH và các vị trí cài phổ biến đã kiểm tra. Google có tài liệu riêng cho CLI này: chạy headless, chọn model, JSON schema và đầu ra có cấu trúc. Không thể coi việc đã cài hoặc đăng nhập IDE là bằng chứng CLI đã sẵn sàng.

Một điểm chưa được xác minh: gửi khung hình qua headless. Tài liệu stdin stream-json chỉ hỗ trợ khối `text`, từ chối các loại khối khác; tài liệu ảnh/video của CLI mô tả dán qua clipboard trong giao diện tương tác. Cần thử đường gửi/đọc ảnh được hỗ trợ, không được lặng lẽ bỏ ảnh và gọi đó là phân tích đầy đủ video.

SDK Antigravity hỗ trợ đa phương thức, nhưng hướng dẫn xác thực sử dụng Gemini API key hoặc Vertex AI. Đây không phải bằng chứng SDK dùng được phiên đăng nhập của IDE.

## Phân công và thứ tự

| Bước | Phụ trách | Công việc | Điều kiện nghiệm thu |
| --- | --- | --- | --- |
| 1 | Agent khảo sát máy — Sol/high | Kiểm tra bản cài, PATH, version và help; phân biệt IDE với CLI | Đã hoàn tất: có IDE, chưa có `agy` |
| 2 | Agent rà soát giao thức — Sol/high | Đối chiếu tài liệu chính thức: headless, ảnh, JSON, xác thực, quyền công cụ | Đã hoàn tất: JSON/văn bản có hỗ trợ; chưa có đường đính kèm ảnh headless được tài liệu xác nhận, chưa có cờ tắt toàn bộ tool theo từng lần gọi |
| 3 | Agent backend — Sol/high, sau bước 2 | Thử CLI riêng bằng văn bản và một ảnh tổng hợp; dùng schema nhỏ; kiểm tra model, timeout, hủy và trạng thái kết thúc | Phải chứng minh AI đọc đúng chi tiết chỉ có trong ảnh và trả JSON hợp lệ; người dùng tự hoàn tất đăng nhập nếu CLI yêu cầu |
| 4 | Agent backend — Sol/high, sau thử nghiệm đạt | Thêm provider `antigravity` và adapter vào `ask_ai`; chọn model; giữ cache tách theo provider/model; kiểm tra kết quả bằng Pydantic | Phân tích ảnh/transcript, lập kịch bản và dịch trả đúng schema; không tự chuyển provider khi lỗi |
| 5 | Agent giao diện — Sol/high, chạy song song bước 4 | Thêm option Antigravity CLI trên máy; trạng thái phát hiện/cài đặt/đăng nhập; chọn model; nút kiểm tra; lưu trong cấu hình chung | Phân biệt tìm thấy binary với kết nối được; chuyển dự án/khởi động lại vẫn nhớ cấu hình |
| 6 | PM và agent kiểm thử — Sol/high | Kiểm tra tích hợp, lỗi đăng nhập/quota/model, JSON sai, timeout/hủy; chạy mẫu ngắn rồi kiểm tra bản local | Không ảnh hưởng Gemini/OpenAI/Codex; không báo hoàn tất khi thiếu ảnh hoặc output bị cắt |

## Nguyên tắc triển khai

- Dùng CLI/API công khai; không lấy token từ IDE hoặc gọi endpoint nội bộ bằng thông tin đăng nhập trích xuất.
- Giữ phạm vi dữ liệu mỗi lần phân tích trong thư mục riêng. Kiểm tra quyền đọc/ghi, shell, web và MCP của CLI trước khi đưa transcript nguồn vào. Không bật tự động chấp nhận mọi quyền.
- Prompt dài truyền qua stdin theo giao thức được CLI hỗ trợ, không ghép thành lệnh shell. Mỗi đoạn dùng phiên độc lập, không tiếp nối chat cá nhân.
- Chỉ lưu kết quả sau khi kiểm tra trạng thái kết thúc, JSON schema và các điều kiện mốc thời gian/thời lượng của AIR3view.
- Nếu chỉ xác minh được văn bản, phải công bố giới hạn và thống nhất phạm vi riêng trước khi thay thế pipeline đọc khung hình hiện tại.
- Không đưa option chưa chạy được vào giao diện dưới trạng thái “đã kết nối”.

## Tài liệu đã đối chiếu

- Headless và JSON schema: https://antigravity.google/docs/cli/headless/
- Cài CLI và xác thực: https://antigravity.google/docs/cli/install/
- Đính kèm media trong CLI tương tác: https://antigravity.google/docs/cli/prompting/
- Quyền công cụ: https://antigravity.google/docs/cli-permissions
- SDK và phương thức xác thực: https://antigravity.google/docs/sdk/overview/

## Trạng thái bàn giao

Đã hoàn tất hai nhánh khảo sát độc lập và lập kế hoạch. Kết luận nghiệm thu: chưa đủ điều kiện triển khai Antigravity làm provider thay thế toàn bộ pipeline ảnh + transcript của AIR3view bằng giao diện headless được tài liệu công khai hỗ trợ. Kế hoạch backend/giao diện chỉ bắt đầu khi bước 3 chứng minh được các điều kiện còn thiếu, hoặc người dùng chọn rõ phạm vi chỉ đọc transcript.

Chưa thay đổi provider đang chạy của AIR3view. Chưa cài thêm CLI, chưa thực hiện đăng nhập hoặc suy luận tính phí. Cài CLI riêng có thể kiểm tra được xác thực và văn bản/JSON, nhưng riêng việc cài CLI không giải quyết giới hạn ảnh headless. Không coi tài khoản đăng nhập IDE là phiên CLI đã xác minh.
