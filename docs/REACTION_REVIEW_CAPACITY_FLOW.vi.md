# Reaction COPS: cân cửa sổ, sửa theo chứng cứ và giảm xử lý lặp

Ngày 2026-10-08. Bổ sung cho v0.1.20, dựa trên `air3view-ae41d6a0.log`.

## Luồng xử lý

Nguồn → evidence/phân loại có cache → dàn ý → lịch cửa sổ → kiểm tra toàn bộ
lịch hình → viết/kiểm chứng → WAV ở tốc độ đã chọn → cân hình theo WAV nếu
giọng ngắn và đủ ngân sách → cache hình/PCM → ghép MP4 → kiểm tra output.

Master RULE về nội dung/văn phong giữ nguyên. `reaction_commentary_count=0`
vẫn là full review; chế độ commentary hữu hạn và thoại nhân vật giữ quy tắc riêng.

## Hình và cửa sổ đọc

- Khi một cửa sổ thiếu hình, thử dịch ranh giới giữa nó và cửa sổ sau/trước.
  Chỉ đổi nhóm row; giữ mọi range, cue và tổng thời lượng. Cửa sổ mới cùng
  phần, 4–25 giây và phải giải được các cảnh đúng setting, đủ nhóm chứng cứ,
  theo thứ tự nguồn và quy tắc không chia A/B cùng cảnh.
- Tối đa 12 ranh giới ứng viên mỗi phía. Nếu không đạt, dùng gộp cue chung rồi
  rút đuôi có giới hạn như trước. Giới hạn 65% được tính bằng frame nguyên,
  cho phép sai số dưới một frame: 578 → 375 frame không bị loại bởi 64,879%.
- Chỉ công bố sau khi toàn lịch đạt. Vẫn tối đa 2 lần sửa/cửa sổ, 64/toàn lịch;
  tổng giảm không quá max(5 giây, 10% lịch ban đầu). Không lấy tình huống khác
  để bù, không kéo giọng, không thay setting người dùng.
- Bộ giải giữ cache các cửa sổ đã giải trong cùng một transaction. Thay đổi
  row, khoảng cảnh hoặc điểm nguồn trước cửa sổ làm cache đó không còn khớp.

## Sửa lời và tiếp tục sau lỗi

- Mỗi entry có ledger câu nói gốc: câu hỏi không chứng minh tiền đề; câu điều
  kiện/khả năng giữ bất định; câu khẳng định vẫn chỉ là lời của người nói.
  Không biến dự định thành yêu cầu hoặc cảnh báo khả năng thành kết quả đã xảy ra.
- Reviewer trả `supported_facts` kèm cue IDs, `forbidden_claims` và
  `required_points`. Các ID phải thuộc evidence của entry. Hướng sửa tập trung
  vào các sự kiện này, không chỉ yêu cầu "viết lại" hoặc đủ số từ.
- Checkpoint độc lập theo entry/evidence/thời lượng/focus/ngôn ngữ/style/model;
  giữ số lượt, lời bị loại, lỗi và contract sửa qua lần chạy lại. Đoạn đã đạt
  không phải viết/kiểm chứng lại khi chỉ đổi metadata dàn ý không liên quan.
- Lời lặp đúng bản đã bị loại không gọi reviewer lần nữa. Mỗi lượt chạy vẫn
  tối đa 5 lần viết cho nhóm lỗi; provider/cancel không biến thành kết quả đạt.
  Không có fallback xuất lời chưa qua kiểm chứng.
- Cache v0.1.20 chỉ được chuyển khi hash toàn bộ input cũ khớp chính xác;
  không dò theo tên `selN` để nhận cache khác nội dung. Giữ VERSION của plan
  để không làm mọi kịch bản/audio đã đạt trở thành lỗi thời; writer có version riêng.
  Phản hồi lỗi cũ được nạp lại khi khớp entry; không tự dựng lại lời bị loại
  vì v0.1.20 chưa lưu phần text đó.

## Giọng ngắn và hiệu suất render

- Khi WAV full review ngắn, thử tối đa 13 độ dài theo frame, để đuôi tự nhiên
  dưới 0,4 giây. Giữ text, mọi row/cue, voice preset/tốc độ và audio đoạn khác.
  Chỉ nhận khi geometry, budget, toàn lịch hình và contract vẫn đạt; tối đa
  3 lần cân mỗi voice và vẫn chung giới hạn giảm 10% lịch ban đầu.
- Dùng lại raw WAV không chứa target trong key. Đổi độ dài hình không phải gọi
  TTS lần nữa. Nếu không thể cân an toàn thì sửa riêng lời theo chứng cứ như trước.
- Khi `source_mutes` phủ toàn phần, không mở/giải mã hàng loạt audio nguồn để
  rồi mute về 0. Vẫn trộn voice/nhạc và giữ PCM đúng số mẫu. Có khoảng tiếng
  gốc chưa mute thì dùng đường giải mã cũ.
- Giữ render cache từng chunk, kiểm tra NVENC và fallback CPU hiện tại; không
  tăng số encoder tùy tiện. Lưu `analysis_stats.review_writer` và
  `analysis_stats.render` với thời gian, số request/cache và encoder thực dùng.

## Kiểm chứng và giới hạn

- Toàn bộ backend cuối bản v0.1.21: 777 passed, 1 skipped; frontend 7 test
  passed và production build đạt. `git diff --check` đạt.

- Fixture nhiều cửa sổ: sel0 672 → 598 frame, sel5 578 → 375 frame trong cùng
  lịch dài; giữ mọi dẫn chứng và các cut đúng 1–2,5 giây.
- Fixture đổi ranh giới 7s/5s → 6s/6s giữ nguyên 12 giây footage, các cut đúng
  2 giây; có test cửa sổ cuối phải cân với cửa sổ trước.
- Test sửa lỗi bộ đàm giữ contract qua retry; bỏ review trùng; giữ đoạn đã đạt;
  chặn fact IDs không tồn tại và migration cache không khớp.
- Test luồng synthesize dùng SDK giả lập trả WAV thật: chỉ tạo một lần, giữ
  text/tốc độ 1,25 và audio đoạn khác, cân hình và qua strict timeline.
- WAV thật xác nhận voice còn trong mix và tiếng nguồn bị mute không bị decode.
- Bộ lập lịch mô phỏng 1.200 giây/600 shot/50 cửa sổ: lần đầu 4,748 giây,
  dùng lại cùng cửa sổ 0,013 giây; không request AI. Đây không phải thời gian
  render hay sản xuất toàn bộ video 20 phút.

Không có source/manifest của máy báo lỗi để QA nội dung video đó. Ledger câu
nói không thay thế xem video/kiểm chứng actor và hành động. AI có thể tiếp tục
trả nội dung không đạt; tool lưu checkpoint và báo giới hạn thay vì xuất sai
sự kiện hoặc chạy vô hạn. Nguồn/setting bất khả thi không được bỏ qua kiểm tra.
