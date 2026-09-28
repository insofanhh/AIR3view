# Kế hoạch nâng chất lượng Reaction COPS cho các dự án sau

## Mục tiêu và thứ tự ưu tiên

1. Chỉ dùng cue IN_SCENE đã xác minh để lập sự kiện, chọn cảnh và viết lời AI. SOURCE_NARRATION, cue lẫn vai trò và cue chưa rõ không được dùng làm chứng cứ hay giữ tiếng nguyên bản.
2. Giữ xương sống tình huống theo thứ tự: bối cảnh hiện trường, bước ngoặt, hành động/hệ quả, lời kể hoặc xác minh tiếp theo và diễn biến cuối đã xác nhận. Không tạo một đoạn thoại gốc kéo dài ở cuối nếu phần đó chứa chương mới cần host giải thích.
3. Thời lượng nhập là mục tiêu ưu tiên; nếu nguồn sạch không đủ thì tự giảm mục tiêu và ghi rõ chênh lệch. Không lặp cảnh, kéo dài hình, đệm im lặng hoặc dùng lời dẫn nguồn để bù.
4. Tỷ lệ thoại gốc là mục tiêu mềm. Ưu tiên trong khoảng ±7 điểm phần trăm khi có thể; chỉ cảnh báo nếu lệch quá 10 điểm và ghi nguyên nhân. Không làm hỏng cấu trúc hoặc kéo dài lời AI để ép tỷ lệ chính xác. Ngay cả khi đặt 100% thoại gốc, vẫn giữ số COMMENTARY point cần thiết theo master prompt.

## Thay đổi luồng lập kế hoạch

### 1. Kiểm kê nguồn đầy đủ và lập bản đồ diễn biến

- Giữ bước tối ưu SRT và phân loại vai trò. Từ **toàn bộ** cue IN_SCENE, tạo các event ngắn với `cue_ids`, khoảng thời gian, nội dung xác nhận, mức chắc chắn và quan hệ trước/sau. Chia nguồn dài thành batch nhỏ; hợp nhất event theo chương, không gửi toàn bộ transcript trong một request OpenAI.
- Tách sự kiện được nói ở hiện trường khỏi khẳng định của một bên. Không suy ra sự thật từ lời hô, tiếng hét, tiêu đề, metadata hoặc lời dẫn hậu kỳ.
- Ghi các chương có bằng chứng; nếu nguồn chỉ có một pha hoặc rất ít thoại, không ép đủ số chương hoặc đủ 3 điểm yếu.

### 2. Khóa các điểm COMMENTARY trước khi bù thời lượng

- AI chọn các bước ngoặt từ event map và cue thật: thường 3–6 điểm, tối đa 10. Mỗi điểm có sự kiện mới, cue dẫn chứng và vai trò biên tập riêng. Điểm đầu vào một diễn biến cụ thể; điểm cuối giải thích diễn biến IN_SCENE cuối được giữ, không dùng cập nhật hậu kỳ làm kết quả.
- Dùng bộ kiểm tra độc lập để phát hiện chương quyết định bị bỏ, các điểm dồn vào nửa đầu, điểm cuối đến quá sớm, lặp ý, dùng cue tương lai, hoặc synopsis hứa hẹn chương không có trong lịch. Nếu thiếu, chỉ lập lại các điểm và cảnh liên quan, không viết voice/TTS trước.
- Các trường `synopsis`, `last_confirmed_event` được tái tạo từ lịch **đã khóa**, không giữ nguyên bản nháp nếu nhánh sửa đã thay danh sách cảnh.

### 3. Tối ưu cảnh và ngân sách mềm

- Thay `_fill_from_full_source()` chọn block theo ưu tiên/số vùng bằng bộ chọn theo chương: trước hết bảo đảm các event quan trọng và khoảng hội thoại thật, sau đó thêm cảnh để tiến gần thời lượng. Chọn đủ thời gian cho từng điểm COMMENTARY và giữ chuyển cảnh đúng thứ tự; không lặp hoặc cắt giữa lời.
- Dùng `original_dialogue_ratio` để xếp hạng các lịch **đều đạt cấu trúc**, không dùng làm điều kiện cứng. Ví dụ setting 70% có thể ra 63–77%; 75,3% không tự động bị coi là lỗi nếu cấu trúc tốt. Ngoài khoảng mềm, thử đổi cảnh/slot; nếu vẫn không thể, xuất video khả thi kèm lý do.
- Bỏ công thức giới hạn tổng thời lượng `250 / commentary_share` cho Reaction COPS vì nó biến tỷ lệ mềm thành trần thời lượng cứng. Khả năng thật phụ thuộc nguồn sạch, số điểm có chứng cứ và thời gian cần để đọc tự nhiên.

### 4. Viết lời sau khi đã khóa lịch

- Mỗi điểm tiếng Anh ưu tiên 2 câu liền mạch, khoảng 42–55 từ và 11–16 giây; điều chỉnh theo nội dung/cảnh thật thay vì nhồi từ cho đủ slot dài. Nếu TTS đo được quá dài, sửa riêng câu hoặc chọn lại độ dài cảnh quanh điểm đó, giữ cùng voice profile và tốc độ đọc tự nhiên.
- Cho người viết thấy cue hiện tại **và tóm tắt các sự thật IN_SCENE đã xác nhận ở những điểm trước**, để tránh nhận định sai như “chưa nghe được lý do nào” khi đoạn mở đã nhắc tới một cuộc gọi. Không đưa lời dẫn nguồn hoặc diễn biến tương lai vào context.
- Kiểm tra lời nói: không intro/outro, bài học, CTA, timestamp, cue ID, kết luận vượt chứng cứ hoặc câu trích từ SOURCE_NARRATION. Điểm cuối không được biến thành lời chốt vụ án.

### 5. Chuẩn hóa phụ đề trước khi render

- Với rolling subtitles của YouTube, hợp nhất bản cập nhật trùng câu và giải quyết khoảng chồng trước khi tạo SRT/ASS. Không để hai cue độc lập cùng xuất ở một vị trí trong một thời điểm; nếu không có mốc từ đáng tin cậy, dùng phụ đề thường thay vì highlight sai.
- Phụ đề AI ưu tiên khi voice AI đang nói. Kiểm tra thứ tự đọc, mốc cắt và khả năng hiển thị bằng ảnh trích tại các điểm giao cue.

## Kiểm thử và phát hành

- Test hồi quy cho nguồn dài nhiều chương, nguồn ít thoại, lời dẫn nguồn chen giữa/cuối, cue lẫn vai trò, target dài hơn nguồn, ratio 10–100%, giới hạn token OpenAI và rolling subtitle chồng nhau. Bất biến: không cue cấm, không cảnh lặp, thời lượng khả thi, điểm cuối gắn với chương cuối, không lời bình dồn hết về đầu.
- Chạy dự án `3baeafc9adb546268bd9c6351e2bb708` làm ca chuẩn: khoảng 240 giây, hook tắt, nguồn 70% là ưu tiên mềm; điểm commentary phải bao phủ cả giai đoạn sau còng tay và lời kể hiện trường, không dùng lời dẫn cáo buộc hậu kỳ. SRT/ASS xuất không còn cue chồng sai thứ tự.
- Lưu trong `duration_plan` các số `requested/effective/actual`, `requested/actual_original_ratio`, vị trí điểm commentary, event coverage và lý do lệch. Tăng version/fingerprint kế hoạch để chỉ những dự án được phân tích lại mới dùng luật mới; bản xuất cũ vẫn mở được.

## Tiêu chí hoàn thành

- Video đúng thời lượng nếu đủ cảnh sạch; nếu không đủ, tự điều chỉnh và thông báo rõ.
- Mỗi point có chứng cứ IN_SCENE, không nói trước và không mượn narrator. Các chương quan trọng có trong bản xuất, đặc biệt diễn biến cuối; không có đuôi dài chỉ vì cần đủ phút.
- Nhịp voice đúng master prompt trong phần lớn điểm, cùng giọng và tốc độ tự nhiên; sai lệch tỷ lệ thoại nhỏ được chấp nhận và có số đo.
- Phụ đề đọc theo đúng thứ tự, không hiển thị đồng thời hai bản rolling của cùng câu.
