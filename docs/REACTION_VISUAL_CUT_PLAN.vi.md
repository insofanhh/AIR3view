# Kế hoạch thời lượng cảnh và dẫn chứng hình Reaction COPS

Ngày: 2026-10-08. Kế hoạch đã được triển khai cho chế độ range; các phần dưới lưu phân tích ban đầu.
Quy tắc phục hồi mới thay thế giới hạn một cut/shot được mô tả trong [REACTION_VISUAL_RECOVERY.vi.md](REACTION_VISUAL_RECOVERY.vi.md). Các mục kiểm chứng dưới đây lưu trạng thái v0.1.19.
Yêu cầu: cảnh cắt theo setting; nhiều cảnh cùng củng cố lập luận và diễn tả đủ nội dung đoạn voice AI; giữ quy trình tạo output đã chạy được ở v0.1.18.

## Triển khai và cách kiểm chứng

- `backend/reaction_visual.py` tạo manifest hình độc lập. Bộ giải theo frame chọn tối đa một cut từ mỗi shot nguồn thật, đúng thứ tự và đủ các nhóm cue dẫn chứng trong cửa sổ thoại. Hai cut thường liên tiếp không được cùng shot, chia liền, lặp hoặc đảo nguồn. Hook giữ lịch riêng.
- Chỉ dùng ranges đã chọn hoặc các cue đã trích và xác minh của chính nhóm diễn biến; loại lời dẫn/AI/unknown khỏi ứng viên. Không lấy một vụ việc/chương khác để bù ngân sách hình. Ghi window, nhóm dẫn chứng, shot, source/output time và lý do của từng cut.
- Preview, render trực tiếp và render cache cùng dùng lịch hình. Tiếng nguồn, voice, phụ đề, ducking và nhạc dùng lịch âm thanh riêng. Cache âm thanh không phụ thuộc việc đổi hình.
- Kiểm tra khả thi trước TTS và trước xuất. Lỗi `[visual:...]` lưu trạng thái blocked và số cảnh/thời lượng còn thiếu; không tự retry voice. Đổi range chỉ dựng lại hình, giữ kịch bản/voice đã đạt. Auto giữ quy trình cũ.
- Chỉ mục chuyển cảnh pixel được dùng lại; dự án cũ thiếu/sai chỉ mục được lập lại một lần. Không dùng phép chia selection để tự tạo shot ID.
- Test MP4 thật: nguồn có bốn cảnh màu khác nhau và tiếng 440 Hz ở 8 giây đầu, 880 Hz sau đó. Output chọn hình 0–2, 3–5, 6–8, 9–11 giây thành 8 giây, vẫn dùng tiếng 0–8 giây. Kiểm tra pixel, phổ âm, AI voice và nhạc trên cả render trực tiếp/cache.
- Mô phỏng riêng bước lập lịch cho 1.200 giây output, 600 shot/cut, 50 cửa sổ thoại: khoảng 3,3 giây trên máy hiện tại, không thêm request AI. Đây không phải thời gian render video 20 phút.

Giới hạn thực tế: chỉ mục chuyển cảnh và cue/event map bảo vệ thời lượng, thứ tự và nguồn dẫn chứng; chúng không thay thế việc xem video để xác nhận mọi chi tiết hành động trong từng câu AI. Bản output ở ảnh chưa có MP4/manifest tương ứng tại local để QA đầu–giữa–cuối. Với nguồn một shot/góc liên tục, không thể tạo các shot khác nhau bằng chia timeline; range có thể bất khả thi và sẽ báo rõ, thay vì xuất âm thầm sai setting.

## 1. Bằng chứng và giới hạn kết luận

Ảnh có setting khoảng 1–2,5 giây/cảnh, video output khoảng 2 phút 30 giây, video nguồn 19 phút 08 giây. Phần cấu trúc commentary trong ảnh mô tả 6 điểm. Không coi mọi block audio xanh là một commentary: trong chế độ dubbed còn có các lượt lồng tiếng nhân vật.

Đã đọc code v0.1.18 và chạy mô phỏng bằng fixture giả, không gọi AI, không sửa dự án.
Bản local có dự án cùng tên Hospital nhưng chưa có `story_plan`/exports, setting local là 1,5–2,5 giây và commentary=0. Không dùng cấu hình local này thay cho cấu hình của ảnh. Chưa có file output/manifest tương ứng để đo các cảnh cụ thể trong ảnh.

### Nguyên nhân A — Khoảng thời lượng chưa phải ràng buộc của cảnh output

- `backend/reaction_scene_duration.py:instructions()` ghi rõ “prefer”, “not a hard validity requirement” và cho phép whole-cue exceptions.
- `_partition()` gộp cue chồng nhau/câu hỏi/câu chưa hoàn tất thành đơn vị; đơn vị này có thể vượt tối đa.
- `clean_blocks()` cho phụ đề tự động YouTube dùng đường fallback giữ cue nguồn; không có bước cắt visual độc lập ở cuối cho mọi audio mode.
- `reaction_cops.validate_plan()` kiểm tra cảnh ít nhất 1 giây và không chồng; không bắt buộc mọi cảnh nằm trong min/max setting.
- `audit()` thống kê lệch và thêm warning sau khi lập kế hoạch, không sửa lệch hoặc chặn kế hoạch lỗi trước render.

Mô phỏng: hai cue hỏi–đáp 4+4 giây, đặt 1–2,5 giây, `_clean_footage()` trả một cảnh 8 giây. Đây là hành vi hiện được code/tests cho phép, không phải setting bị mất.

### Nguyên nhân B — Ranh giới clip không đồng nghĩa đổi hình thực sự

- `reaction_review.prepare()` chỉ chia đều từng source selection theo maximum. Các phần thường là [a,b], [b,c], [c,d] liên tục của cùng nguồn.
- Timeline và FFmpeg concat nối lại các phần đó liên tục. Nếu nguồn không đổi góc/hành động, người xem vẫn thấy một shot dài dù timeline có nhiều clip ngắn.
- Mô phỏng full review: 30 giây nguồn liên tục thành 12 clip 2,5 giây, nhưng toàn bộ 30 giây vẫn chạy liên tục.
- Không quy lỗi này cho NVENC, tốc độ giọng hay việc renderer tự gộp sai; chính dữ liệu clip đã liên tục từ bước chọn hình.

### Nguyên nhân C — Min và tính khả thi chưa được xử lý đầy đủ

- `prepare()` đọc min nhưng chưa dùng khi quyết định số đoạn chia.
- Mô phỏng range 2–2,5 giây, từng selection 3 giây: chia thành 1,5+1,5 giây. Giữ nguyên riêng một selection 3 giây không thể chia thành các đoạn đều thuộc 2–2,5; code chưa đưa ra xử lý khả thi phù hợp.
- Cần dùng số frame nguyên và xác định khả thi trước khi chia, không cố ép mọi residual bằng cắt nhỏ hoặc pad.

### Nguyên nhân D — Dẫn chứng được kiểm tra theo cửa sổ rộng

- Commentary đã có `commentary_span`: một voice có thể chạy xuyên nhiều clip. Không cần tăng commentary count để có thêm cảnh.
- Việc chọn/group clip hiện chủ yếu dựa trên priority, mốc chương, cue và tổng thời gian, chưa có hợp đồng từng ý trong lời kể phải được minh họa bởi cảnh nào ở thời điểm nào.
- Full-review clip giữ cue gần khoảng cắt (±1,5 giây). Kiểm tra đó bảo vệ nguồn trích dẫn nhưng không chứng minh frame đang hiển thị chính hành động/nhân vật của câu AI.
- Writer review kiểm tra nghĩa với cue, chưa trả lại bản đồ từng mệnh đề → dẫn chứng hình → khoảng output. Một lời kể về hành động có thể chạy trên cảnh nói chuyện thuộc cùng cửa sổ mà thiếu hình hành động đó.

## 2. Hợp đồng mới, giữ nguyên master prompt và voice

Tách ba đối tượng:

1. **Evidence/event span**: tình huống, cuộc trao đổi hoặc hành động có thể dài; giữ toàn bộ cue để hiểu nội dung.
2. **Voice window**: đoạn commentary, review hoặc lượt nhân vật; giữ âm thanh, tốc độ, số commentary và logic nghĩa như hiện tại.
3. **Visual cut**: khoảng hình được hiển thị; phải tuân thủ setting cảnh riêng, không lấy thời lượng của voice/event làm thời lượng một cut.

Setting “Theo khoảng thời lượng” trở thành ràng buộc của visual cuts. Auto vẫn giữ hành vi dựng hiện tại. Hook dùng thời lượng riêng và được ghi rõ là ngoại lệ hook, không phải lệch cảnh thông thường.

Không làm giọng nhanh/chậm để ép cảnh; không tăng commentary count, đọc timestamp, đổi sự kiện, thêm kết quả chưa xác minh hoặc bỏ đoạn thoại quan trọng để đạt số giây.

## 3. Luồng đề xuất

Giữ pipeline đang chạy: nguồn → đọc hiểu/phân loại → dàn ý/chọn evidence → viết/review lời kể → voice.
Thêm bước **lập và kiểm tra visual edit** độc lập theo nội dung và thời lượng voice, trước render:

```text
Evidence + lời kể đã đạt + voice window
       ↓
Bản đồ từng ý/nhân vật/hành động → cue, event và nguồn hình
       ↓
Chọn chuỗi cảnh theo diễn biến, chia theo min/max frame
       ↓
Kiểm tra từng cut + độ khớp ý/hình + liên tục thoại + tổng thời lượng
       ↓
Chỉ sửa cửa sổ/cảnh bị lỗi → xác nhận lại
       ↓
Khóa visual edit → preview → render/export
```

Có thể tạo ứng viên và bản đồ trước TTS, rồi kiểm tra/cân visual edit sau khi đo voice. Không thay toàn bộ pipeline sang viết lại/TTS trước chọn evidence trong bản sửa này.

## 4. Thứ tự triển khai

### P0.1 — Tách visual timeline khỏi lịch âm thanh

- Thêm manifest `visual_edit` gồm cut ID, source start/end, output start/end, voice-window ID, evidence IDs, event/shot ID và lý do chọn.
- Một cut không đổi `segment_id`, câu AI, lịch turn hoặc audio đã render. Timeline âm thanh/phụ đề map theo voice windows; timeline video map theo visual cuts.
- Chế độ tiếng gốc/dubbed: continuity của audio hội thoại phải giữ nguyên, dù có visual cuts. Nếu đổi hình sang evidence khác thì source audio cần track map độc lập, không lấy audio của hình mới làm thoại.
- Với full review/commentary: giữ AI đã đo cùng nhạc/mức nền. Những khoảng còn giữ tiếng nguồn vẫn phải dùng audio đã khóa; tránh accidental jump/cắt từ do thay hình.
- Cùng manifest phục vụ preview, render nhanh, render CPU/NVENC và xuất phần để không lệch logic.

### P0.2 — Bộ chia cảnh xác định bằng code

- Chuyển setting sang frames: minFrames=ceil(minSeconds×30), maxFrames=floor(maxSeconds×30).
- Với vùng có F frames: số cut khả thi n thuộc [ceil(F/maxFrames), floor(F/minFrames)]. Nếu có, chia cân bằng sao cho tổng frame giữ nguyên và mỗi cut đạt bounds; ưu tiên các mốc chuyển cảnh/hành động hợp lệ.
- Nếu vùng không khả thi: thử kết hợp vùng tương thích, chọn lại vài frame thật trong nguồn hoặc phân bố lại thời lượng các cut cùng voice window. Không đẩy phần dư vào cut quá ngắn hay kéo freeze/silence.
- Bảo vệ ranh giới sự kiện, giai đoạn, identity và các vùng loại trừ. Không ghép vùng khác tình huống chỉ để đủ min.
- Sai lệch do setting không chia hết cho frame được mô tả rõ; không dùng tolerance rộng để hợp thức hóa cảnh vượt maximum nhiều frame.
- Không thể bảo đảm min=max tuyệt đối đồng thời giữ mọi tổng thời lượng tùy ý. Trường hợp bất khả thi phải có reason code, mốc cảnh, số frame thiếu/thừa và hướng điều chỉnh cụ thể. Không âm thầm render lệch hoặc retry AI vô hạn.

### P0.3 — Cắt hình có ý nghĩa, không chỉ thêm biên clip

- Dùng chuyển cảnh/keyframe đã chuẩn bị và phần mô tả hình sẵn có để xếp hạng cảnh cùng event, người/vật/hành động đang được nói đến.
- Khi có nguồn phù hợp: dùng establishing context → hành động/phản ứng → chứng cứ/chi tiết → hậu quả đã xác minh; có thể thay số cảnh theo thời lượng lời kể.
- Kiểm tra chuỗi clip liên tiếp cùng source, cùng shot, cùng framing. Ghi `continuous_run_seconds` và phân biệt boundary trên timeline với chuyển hình thực sự; không tính chia liền [a,b],[b,c] là thay đổi góc.
- Không bỏ giữa hành động quan trọng, đảo thứ tự gây sai quan hệ nhân quả hoặc dùng cảnh khác người làm phản ứng của người đang nói.
- Nguồn chỉ có một góc: không thể tạo góc quay mới bằng phép chia timeline. Giữ hình đúng bằng chứng; ghi thiếu visual diversity. Nếu sản phẩm muốn crop/reframe để hỗ trợ chi tiết, đó là quy tắc riêng, dùng hình thật và không coi là chứng cứ mới. Không mặc định thêm zoom giật hoặc cảnh minh họa giả.

### P0.4 — Gắn từng ý AI với chứng cứ hình

- Cấu trúc riêng `visual_beats`: câu/mệnh đề, claim IDs, evidence IDs, loại claim (quan sát/thoại/phân tích có căn cứ), nhân vật/hành động trọng tâm, visual candidates và output interval.
- Bản đồ không thay master prompt văn phong; không thêm nội dung phải đọc vào narration/subtitle.
- Claim hành động/chi tiết nhìn thấy phải có frame/range thực sự xác nhận. Claim lời nói được hỗ trợ bằng cue đúng người/ngữ cảnh, không bắt mọi câu phân tích phải có một hình “chứng minh” riêng.
- Đầu cửa sổ giải thích tình huống, tiếp đến các cut phục vụ lập luận, cuối cửa sổ thể hiện trạng thái đã xác minh; không chỉ chọn clip có priority cao nhất hoặc chia đều theo source time.
- Dùng caption/word timing AI có sẵn để xác định ranh giới câu gần đúng; không thêm một lượt ASR canh từng từ thoại gốc cho tất cả nguồn.
- Một cảnh có thể hỗ trợ nhiều ý, một ý có thể cần nhiều cảnh; không ép một câu= một cut.
- Chỉ gọi AI bổ sung theo batch voice window khi dữ liệu hiện có không đủ rõ; mặc định lập/cân cut bằng code. Không gọi AI riêng cho từng 1–2 giây cảnh.

### P0.5 — Kiểm tra trước render và sửa có giới hạn

- `validate_visual_edit` kiểm tra source/output ranges, frame count, min/max, hook, mapping voice, nguồn evidence, chronological event order, audio continuity, tổng thời lượng và semantic coverage từng beat.
- Tách chất lượng lời thoại đã đạt khỏi lỗi visual edit; lỗi cảnh không kích hoạt vòng viết lại toàn bộ script hoặc tổng hợp lại voice.
- Mỗi cửa sổ lỗi: điều chỉnh boundary → thay candidate cùng evidence/event → bổ sung frame kiểm tra mục tiêu nếu cần. Tối đa 2 lượt semantic repair cho cửa sổ; lỗi arithmetic được code xử lý, không gửi LLM.
- Nếu nguồn không đủ, trình bày giới hạn thật cùng phần nào thiếu. Không chấp nhận “đã có cue gần cảnh” như thay thế cho kiểm tra hình.
- Trước FFmpeg/export chạy audit manifest thực tế, không chỉ audit story selections ban đầu.

### P0.6 — UI, cache và dự án cũ

- Trong mode range, đổi mô tả “ưu tiên/cho phép lệch” thành cam kết thời lượng visual cut với sai số frame và hook exception được mô tả cụ thể.
- Hiển thị độ dài từng cut trên tooltip/bảng Cảnh; tổng cảnh đạt khoảng, cảnh ngoại lệ, số voice windows và số commentary riêng.
- Lưu reason từng cut: minh họa ý nào, nguồn nào, giữ liên tục hay chuyển hình thật. Preview dùng cùng visual edit như export.
- Đổi min/max chỉ lập lại visual edit khi script/voice/evidence vẫn hợp lệ; tách visual fingerprint khỏi fingerprint nội dung để không lặp phân tích nguồn/TTS.
- Visual cache phụ thuộc version, scene settings, clip content/source hash, beat map, measured voice timing và geometry. Chỉ invalidate các chunk đổi; giữ evidence/voice/outline cache.
- Dự án cũ giữ bản render đã có; khi dựng lại range thì tạo manifest mới từ dữ liệu đã đạt. Không sửa âm thầm timeline đang chạy/export.

## 5. Kiểm thử và nghiệm thu

- Arithmetic: 1–2,5; 1–1; 2–2,5; 10–20; source/total không chia hết; các cut quanh min/max và sai số frame.
- Hội thoại cue dài, câu hỏi–đáp, cue chồng phụ đề tự động, gap bị loại, cảnh hành động nhanh và nguồn nhiều vùng ngắn; không lẫn evidence hoặc cắt đứt audio.
- Cả 3 audio profiles: commentary+original, commentary+dubbed, commentary=0 full review; có/tắt hook; video đơn/lô; xuất một file/phần.
- Synthetic video có scene ID rõ trên từng frame để kiểm chứng source→output và boundary trong file MP4, không chỉ assert JSON.
- Một voice 12 giây, setting 1–2,5 giây: khoảng 5–12 visual cuts khi khả thi, cùng 1 voice window; commentary count không tăng. Nếu chọn 2 giây/cut thì 6 cut; mỗi cut phục vụ ý của cùng đoạn AI.
- Regression chuỗi nguồn liên tục 30 giây: vẫn thống kê là một continuous run nếu không có thay đổi hình. Không báo “12 chuyển cảnh thật” chỉ vì có 12 clip.
- Xác nhận cảnh hành động phù hợp được hiển thị khi AI nói về hành động đó; lời phân tích bám chứng cứ, không preview kết quả tương lai.
- Đo prepare/edit/review/render duration, requests và cache-hit trước/sau; kiểm soát số FFmpeg input bằng chunk/cache sẵn có. Không tăng RAM/tổng API theo số cut.
- Khi có MP4 và manifest đúng máy của ảnh, đo setting 1–2,5 trực tiếp trên nguồn/output và review sample đầu–giữa–cuối. Mô phỏng hiện tại chứng minh vấn đề code, chưa thay thế QA video đó.

Mục tiêu nghiệm thu: mọi visual cut thường trong range frame khả thi, ngoại lệ có lý do rõ; voice/audio continuity và số commentary giữ nguyên; các ý quan trọng có dẫn chứng hình phù hợp; không render âm thầm sai setting và không lặp AI vô hạn để che nguồn thiếu.
