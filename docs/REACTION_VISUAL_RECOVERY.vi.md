# Khôi phục ngân sách hình Reaction COPS

Ngày 2026-10-08. Tài liệu này cập nhật quy tắc cứng của bản v0.1.19 trong
`REACTION_VISUAL_CUT_PLAN.vi.md`: một cảnh máy quay có thể chứa nhiều diễn biến,
nhưng chia đôi liền nhau một cảnh không tạo ra chuyển hình thật.

Cập nhật tiếp sau log v0.1.20: xem [luồng cân cửa sổ và sửa theo chứng cứ](REACTION_REVIEW_CAPACITY_FLOW.vi.md).

## Luồng mới

Nguồn → đọc hiểu/phân loại → dàn ý/chọn cue → tạo lịch cửa sổ đọc → **kiểm tra
sức chứa hình và cân lịch** → viết/kiểm chứng lời → tạo giọng → render/export.

- `reaction_visual` chọn cả đầu vùng nguồn hợp lệ và các mốc cue đã dẫn chứng,
  tránh mất hình do chỉ giữ một anchor. Vẫn dùng chỉ mục shot từ pixel.
- Hai đoạn cùng shot chỉ hợp lệ khi nguồn không chồng, có khoảng bỏ qua ít nhất
  0,5 giây và cue dẫn chứng không trùng. Không chia `[a,b],[b,c]`, không lặp cue
  dài thành nhiều cảnh, không coi mỗi cue là một shot/góc máy mới.
- Chỉ dùng ranges đã chọn và **cue đã trích, xác minh**. Gần nhau về thời gian
  không tự chứng minh hai người/tình huống là cùng vụ; không mở sang cue chưa
  trích chỉ để bù thời lượng. Loại narrator, mixed/unknown như trước.
- Bộ giải trả `feasible_frames` từ đường đi thực sự đạt đủ nhóm dẫn chứng.
  Không dùng tổng capacity các ứng viên có thể chồng nhau làm ngân sách sửa.

## Cân có giới hạn

`reaction_visual_recovery` kiểm tra toàn lịch trước bước viết. Nếu lỗi, thử gộp
hai cửa sổ **full review có cue chung**, cùng phần và tổng không quá 25 giây.
Nếu vẫn thiếu, chỉ giảm đuôi hình của các row thuộc cửa sổ AI lỗi, giữ mọi row,
ID và cue dẫn chứng. Full review không phát thoại nguồn nên hình có thể cắt
trong cue khi vẫn thỏa mapping dẫn chứng gần; mode tiếng gốc giữ trọn cue.
Không sửa cửa sổ thoại nhân vật hoặc lồng tiếng để loại nội dung người thật.

- Tối đa 2 lần cân/cửa sổ, 64 lần/toàn lịch, không lặp vô hạn.
- Một cửa sổ phải còn ít nhất 65% độ dài. Tổng giảm ≤ max(5 giây, 10% lịch
  ban đầu); lưu lịch ban đầu để lần chạy tiếp không giảm dần vượt giới hạn.
- Setting của người dùng không bị sửa. Nếu cần, chỉ giảm ngân sách hiệu dụng
  được tính từ nguồn; video vẫn phải qua kiểm tra tối thiểu 10 giây/phần,
  nguồn sạch, thứ tự, cue, commentary và cấu trúc như trước.
- Mọi đề xuất là bản sao; chỉ công bố sau khi toàn bộ lịch hình đạt. Ghi
  `visual_budget`, `visual_repair_history`, mã lỗi và context từng cửa sổ.

## Kịch bản cũ và âm thanh

Chạy toàn bộ/tạo giọng của dự án cũ thử phục hồi trước TTS. Chỉ các cửa sổ đã
đổi thời lượng được đưa vào writer; full review vẫn qua factual review độc lập.
Voice, phụ đề, lịch thoại và voice-repair state được làm mới cho đúng cửa sổ;
text/audio/hash của cửa sổ còn lại được giữ. Không đổi voice preset, mẫu tham
chiếu, tốc độ hay master prompt về văn phong/nội dung.

Render/export giữ preflight nghiêm ngặt; nếu đổi scene setting làm dự án cũ
thiếu hình, dùng Chạy toàn bộ/Tạo giọng để phục hồi lời và âm thanh trước xuất.
Nếu người dùng hủy hoặc provider lỗi, giữ kế hoạch trước đó; không nuốt lỗi
network/cancellation thành lỗi thiếu cảnh. Auto giữ cách dựng cũ.

## Kiểm chứng

- Fixture tái hiện số liệu log: 22,40 giây yêu cầu, 9 shot, sức chứa 18,60 giây.
  Cân full review nhiều row còn 18,60 giây, giữ mọi cue; 9 cut nằm đúng 1–2,5s.
- Test writer/reviewer chỉ nhận `sel0` lỗi; voice đã đạt của cửa sổ khác giữ
  nguyên. Test hủy, cached failure context, range cố định, same-shot bodycam
  có cue riêng, chặn A/B sát nhau, không lấy cue khác vụ để bù.
- Toàn bộ backend bản v0.1.20: 759 passed, 1 skipped, gồm bảo vệ lưu chỉ mục
  cảnh và test không mở cửa sổ WAV sang hình bất khả thi. Build frontend và
  7 test frontend passed; `git diff --check` đạt.
- Mô phỏng bộ lập lịch 1.200 giây output / 600 cảnh / 50 cửa sổ: khoảng 4,9
  giây trên máy hiện tại, không request AI. Đây **không** phải thời gian
  sản xuất/render video 20 phút.
- Chưa có source/manifest từ máy báo lỗi trong log để kiểm tra video thật đó.
  Fixture tái hiện các ràng buộc/số liệu, không thay thế QA nội dung MP4 thật.

Giới hạn: cue thật không tự chứng minh thay đổi góc/hành động; bodycam excerpts
là các dẫn chứng khác nhau theo thoại/thời gian. Nguồn ít hình phù hợp hoặc
setting bất khả thi vẫn cần báo giới hạn sau khi hết các phương án hữu hạn.
