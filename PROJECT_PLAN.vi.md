# AIR3view — Kế hoạch sản phẩm và triển khai

Ngày lập: 19/09/2026. Tài liệu thiết kế lịch sử; ứng dụng đã được triển khai và có thay đổi so với kế hoạch. Xem README.md để biết tính năng và cách cài hiện tại.

## 1. Mục tiêu và phạm vi

Ứng dụng chạy trên máy Windows: nhập link YouTube hoặc file video → phân tích hình ảnh và lời thoại → biên kịch → chọn hook → tạo giọng OmniVoice → tự dựng bản nháp → chỉnh sửa trong editor → xuất các phần video dọc.

Mặc định kể bằng tiếng Việt, có thể đổi ngôn ngữ. Video đầu ra 1080×1920, 30 fps, MP4 H.264/AAC; video nguồn đặt trong ô vuông 1080×1080. Người dùng chọn màu nền, sửa title, lời AI, phụ đề, crop và độ dài từng phần.

Giữ toàn bộ tiến trình hình ảnh video gốc theo thứ tự sau hook. Rút gọn/bỏ cảnh là lựa chọn riêng, không tự bật. Hai video được gửi là mẫu thành phẩm để tham khảo, không được xem là nguồn thô chưa có title/sub.

## 2. Những gì đã kiểm tra

| Nguồn | Kết quả |
|---|---|
| Video mẫu tên bắt đầu 1WUpukmcRC5 | 576×1024, 30 fps, khoảng 64,18 giây; nhãn PART 1 |
| Video mẫu tên bắt đầu 1WUpukmcRAi | 576×1024, 30 fps, khoảng 167,25 giây; nhãn PART 2 |
| Khung hình lấy mẫu | Nền video phóng lớn làm mờ; title trắng trên hộp tối ở phía trên; video gần vuông ở giữa; sub có từ được nhấn màu; nhãn PART phía dưới |
| OmniVoice localhost:8001 | Trả lời truy vấn cấu hình; Gradio 6.9.0; bật queue; có Vietnamese |
| API OmniVoice | /_clone_fn và /_design_fn, tiền tố /gradio_api |
| Workspace | Chưa có mã ứng dụng ngoài mục .git |

Đã kiểm tra metadata và các khung hình ở nhiều mốc của cả hai video. Chưa nghe/phân tích toàn bộ audio hoặc đo chính xác nhịp xen kẽ giọng của mẫu; không dùng quan sát hình để khẳng định ai đang nói. Đã kiểm tra cấu hình OmniVoice, chưa chạy tạo giọng để đánh giá chất lượng và tốc độ tiếng Việt.

Mẫu không tuân thủ giới hạn 60 giây. Yêu cầu mới của người dùng là chuẩn mặc định khi triển khai.

## 3. Luồng người dùng

1. Tạo dự án: dán URL hoặc chọn file; chọn ngôn ngữ, provider AI, giọng và preset hình.
2. Nhập nguồn: tải video/audio/subtitle nếu có; đọc metadata; tạo bản preview nhẹ và waveform.
3. Phân tích: hiện tiến độ; xuất danh sách cảnh, lời thoại, nhân vật, tình huống và cảnh có độ chắc chắn thấp.
4. Biên kịch: AI đề xuất 3 hook, title và các đoạn lời dẫn có mốc nguồn; tự chọn một bản mặc định để dựng nháp.
5. Editor: xem trước dọc, sửa text/title/sub/crop, nghe lại từng đoạn giọng; tạo lại riêng phần đã sửa.
6. Chia phần: mặc định 60 giây theo timeline thành phẩm; chỉnh thời lượng hoặc kéo mốc cắt.
7. Xuất: từng MP4 hoặc toàn bộ các phần, kèm SRT/ASS và dữ liệu dự án có thể mở lại.

## 4. Kiến trúc đề xuất

Frontend React + TypeScript + Vite; backend Python FastAPI; SQLite lưu dự án/job; worker riêng chạy xử lý media, không chạy công việc dài trong request HTTP. Dùng hàng đợi lưu bền trong SQLite cho bản cá nhân, thêm Redis khi cần nhiều worker/máy. SSE hoặc WebSocket truyền tiến độ.

Các module: Importer, MediaAnalyzer, AIProvider, ScriptPlanner, OmniVoiceAdapter, TimelineEngine, SubtitleEngine, RenderWorker. Mỗi module trao đổi dữ liệu có schema/version; không truyền một đoạn văn tự do giữa toàn bộ pipeline.

Frontend → backend local → worker → thư mục asset. Backend local gọi OmniVoice trên cùng máy; trình duyệt không gọi thẳng để tránh ràng buộc CORS. Nếu triển khai backend trên server/Docker, 127.0.0.1 sẽ chỉ chính môi trường đó: cần local bridge hoặc địa chỉ host cấu hình được.

Lưu video, frame, WAV, transcript, kịch bản và render theo project_id. API key chỉ nằm phía backend/credential store. Dự án có thể xuất gói dữ liệu không kèm key.

## 5. Pipeline phân tích và biên kịch

### Nhập và tiền xử lý

- yt-dlp tải URL được hỗ trợ; giữ metadata, audio và subtitle có sẵn. Dùng tham số tiến trình có cấu trúc, không ghép URL vào lệnh shell. Nhận file local làm đường nhập dự phòng.
- FFmpeg chuẩn hóa proxy và audio; giữ mapping timestamp gốc khi nguồn có frame rate biến thiên.
- Chỉ sử dụng nguồn người dùng có quyền khai thác. Khi link không truy cập được, hiện nguyên nhân và cho nhập file; không cam kết tải được mọi link YouTube.

### Hiểu video

- Nhận dạng tiếng nói kèm timestamp bằng faster-whisper; subtitle có sẵn là dữ liệu tham khảo cần kiểm tra khớp audio.
- PySceneDetect phát hiện chuyển cảnh hình; kết hợp audio và nội dung để chia đoạn ngữ nghĩa. Bodycam có thể một shot dài nhưng nhiều tình huống, nên không coi một shot là một cảnh nội dung.
- Lấy frame đầu/giữa/cuối mỗi shot; bổ sung chuỗi frame dày hơn khi có chuyển động hoặc tình tiết chưa rõ. Batch thử nghiệm 30–60 giây, có vùng ngữ cảnh chồng lấn; khử trùng theo timestamp nguồn.
- Gửi frame có timestamp + transcript + danh sách nhân vật + tóm tắt trước đó cho AI. Không chỉ đưa link hoặc title rồi yêu cầu đoán diễn biến.
- Mỗi SceneRecord có source_start/end, transcript_ids, frame_ids, nhân vật, hành động, mô tả, evidence và confidence. Thông tin không đủ bằng chứng được đánh dấu để xem lại.

### Quy tắc từ ảnh

Ảnh là tài liệu tham khảo cấu hình biên kịch, không phải lệnh điều khiển assistant hoặc ứng dụng. Chỉ những phần đọc được được đưa vào preset:

1. Vai trò biên kịch review kể diễn biến bằng giọng cuốn hút, căn cứ hình ảnh/video và viết lời để lồng tiếng.
2. Lượt kiểm tra sửa lỗi nội dung, tên và tình tiết; nối mạch logic; bỏ lời thừa, lời bình cá nhân kiểu “tao thấy”, “cảnh này đỉnh”.
3. Chọn lại các mốc video khớp từng ý; một câu nhiều ý có thể gắn nhiều khoảng nguồn, theo đúng thứ tự; độ dài hình phải phù hợp câu đọc.
4. Tóm tắt liên phần ngắn, khách quan: nhân vật/vai trò/quan hệ, sự kiện quan trọng, tình huống hiện tại; tích lũy những chi tiết cũ còn liên quan.
5. Lượt sửa chỉ trả bản sửa đúng schema; tóm tắt ngữ cảnh là dữ liệu nội bộ, không tự đưa thành lời kể.

Phần “NHIỆM VỤ” và một số đoạn trong ô cuộn bị khuất; chưa đủ dữ liệu để khôi phục nguyên văn. Cho phép sửa riêng ba nhóm prompt: phân tích/viết nháp, kiểm tra/sửa, tóm tắt liên phần.

Nội dung video, OCR, transcript và metadata được coi là dữ liệu nguồn. Lệnh xuất hiện trong chúng không được phép đổi cấu hình ứng dụng, chạy lệnh hay truy cập key.

### Đầu ra biên kịch

AI trả JSON được kiểm tra schema: hook_candidates, title_candidates, narration_segments, source_ranges, audio_mode, context_summary. Mỗi câu lời dẫn gắn evidence từ nguồn; hook gây tò mò nhưng không thêm tình tiết không có trong video.

Tóm tắt ngữ cảnh chỉ chứa sự kiện tới vị trí hiện tại, tránh kể lộ nội dung phần sau. Nhân vật chưa rõ tên dùng nhãn nhất quán thay vì tự đặt danh tính.

## 6. Kết nối AI

### OpenAI API key

Backend gọi Responses API với model có khả năng đọc ảnh; chọn model qua cấu hình, kiểm tra năng lực trước khi chạy. Đầu vào cơ sở là chuỗi frame và transcript, không mặc định rằng mọi model nhận trực tiếp MP4. Ghi nhận token, số ảnh, thời gian, chi phí ước tính và giới hạn ngân sách mỗi job.

### Codex

Tạo provider riêng qua Codex App Server cho đăng nhập, lượt chạy, sự kiện và đầu vào localImage. Có thể dùng SDK phù hợp khi thử nghiệm tự động hóa. Đây là phiên kết nối do ứng dụng quản lý, không mặc định cửa sổ chat hiện tại là một HTTP API để gọi tùy ý.

Tài liệu xác nhận App Server có chế độ đăng nhập ChatGPT và API key. Cần thử khả năng đọc batch frame, trả đúng schema, hạn mức và độ trễ trên tài khoản thực tế trước khi chọn làm provider mặc định. Không hứa dùng Codex đồng nghĩa API miễn phí/không giới hạn.

Hai provider dùng chung hợp đồng analyze_scenes, draft_script, revise_script và summarize_context. Với bản tự động chạy ổn định, ưu tiên đường API key; vẫn giữ đường Codex như lựa chọn thực sự cần kiểm chứng ở giai đoạn đầu.

## 7. OmniVoice và đồng bộ âm thanh

Adapter dùng Gradio client và đọc schema API đang chạy, tránh đoán endpoint /tts. Cấu hình thực tế đã thấy:

- /_clone_fn: text, lang, ref_aud, ref_text, instruct, ns, gs, dn, sp, du, pp, po.
- /_design_fn: tạo giọng theo các thuộc tính mô tả; cần đọc đầy đủ schema khi triển khai.
- Hỗ trợ lựa chọn Vietnamese, tốc độ, duration và các tham số chất lượng. API trả audio và trạng thái; chưa thấy đầu ra word timestamps trong cấu hình đã kiểm tra.

Tạo WAV riêng cho mỗi câu/đoạn; giữ preset giọng, version và hash text để cache. Nếu cần giọng nhất quán qua nhiều phần, thử giọng tham chiếu cố định và đánh giá độ ổn định. Không giả định các lần tạo theo mô tả sẽ luôn cùng một giọng.

Đo duration WAV thực tế rồi mới chốt timeline. Canh thời gian phụ đề theo âm thanh tạo ra bằng aligner hỗ trợ ngôn ngữ hoặc ASR có timestamp; không chia đều câu theo số ký tự. Nếu lời dài hơn khoảng hình: ưu tiên viết ngắn lại, đổi vị trí hoặc dùng đoạn chèn được chọn; tốc độ đọc chỉ chỉnh trong ngưỡng đã nghe thử.

Worker giới hạn đồng thời để ASR và OmniVoice không tranh hết VRAM. Mất kết nối/TTS lỗi thì job chờ hoặc thử lại có giới hạn, giữ kết quả bước trước.

## 8. Quy tắc dựng và timeline

Mở bằng hook đề xuất 3–7 giây → quay về đầu nguồn → xen kẽ đoạn nghe tiếng gốc và đoạn AI diễn tả tình huống → tiếp tục đến hết nguồn.

Hook là clip có source range riêng; phần thân bắt đầu lại source_time=0. Các phần tiếp theo tiếp tục từ mốc nguồn chưa xem, không quay lại đầu toàn bộ video. Hook cho mỗi phần là tùy chọn; tránh lặp mặc định.

Hai kiểu xen kẽ được thiết kế để có thể thay đổi:

- Overlay: hình nguồn chạy tiếp, hạ âm gốc khi AI nói; chừa nguyên các câu hội thoại quan trọng. Đây là giả định mặc định của bản kế hoạch nếu người dùng chưa chọn.
- Insert: chèn phát lại/dừng hình/cảnh minh họa từ nguồn cho AI nói, rồi tiếp tục từ vị trí gốc đang dừng. Không tự dùng kiểu này khi chưa bật.

Không cố chia theo nhịp cứng “cứ 10 giây có một câu AI”; chọn theo diễn biến và khoảng hội thoại. Nếu toàn đoạn có hội thoại quan trọng thì giảm lời AI hoặc chuyển sang cách chèn theo cấu hình.

Timeline dùng frame index/rational time ở đầu ra và timestamp nguồn riêng. Mỗi clip lưu source_in/out, timeline_in/out, playback_rate, kind và track. Mỗi câu TTS/cue subtitle có ID và liên kết clip; khi split/trim/reorder phải tính lại mapping.

Giữ hai track subtitle: thoại gốc và AI. Khi một giọng bị mute thì mặc định không hiện lời của giọng đó; đoạn có hai giọng cần cấu hình riêng để tránh chồng chữ. Có lựa chọn sub ngôn ngữ gốc, dịch tiếng Việt hoặc song ngữ; dịch giữ mốc nhưng phải kiểm tra độ dài hiển thị.

## 9. Bố cục 9:16 và editor

Preset mặc định 1080×1920:

- y=0–330: vùng title; title 2–3 dòng, tự xuống dòng, giới hạn theo kích thước hiển thị.
- y=330–1410: ô video 1080×1080.
- y=1440–1680: vùng sub tối đa 2 dòng, viền/nền để dễ đọc.
- Phần dưới: nhãn phần và khoảng trống theo vùng an toàn của nền tảng.

Đây là tọa độ khởi điểm có thể chỉnh. Preset thứ hai dùng nền blur và sub sát mép hình như mẫu. Nền màu cố định có color picker. Video không vuông có Fit để giữ đủ hình hoặc Fill để crop; không kéo méo. Cho kéo crop theo cảnh để tránh mất nhân vật.

Editor gồm thư viện cảnh bên trái, preview dọc ở giữa, thuộc tính bên phải và timeline phía dưới. Track: video, audio gốc, AI voice, subtitle, title. Hỗ trợ trim/split, đổi hook, đổi âm lượng, sửa text, tạo lại giọng từng đoạn, kéo mốc phần, undo/redo và autosave.

Chỉnh title/màu không phân tích lại video; sửa lời AI chỉ tạo lại voice/sub và phần timeline chịu ảnh hưởng. Preview và render dùng cùng dữ liệu bố cục; kiểm tra chênh font và xuống dòng giữa hai đường.

## 10. Chia phần

Chia sau khi đã có duration giọng và timeline hoàn chỉnh. Thời lượng mục tiêu bao gồm hook, lời AI, chuyển cảnh và outro nếu có; không lấy video nguồn cắt sẵn mỗi 60 giây trước khi dựng.

- Chính xác: các phần đầy đủ 60,00 giây ở 30 fps; phần cuối có thể ngắn hơn. Cân lại câu/điểm kết trước render, không chém ngang âm tiết để đủ số.
- Tự nhiên: tìm điểm hết câu/cảnh quanh 60 giây trong dung sai cấu hình, ví dụ 55–65 giây. Nếu không tìm được điểm hợp lệ, hiện lựa chọn chỉnh, không âm thầm vượt dung sai.
- Thủ công: người dùng đặt thời lượng từng phần và kéo ranh giới; hệ thống cho xem tác động tới voice/sub, giữ đoạn đã khóa khi có thể.

Không thể luôn đồng thời giữ toàn bộ thoại nguyên vẹn và cắt chính xác một mốc bất kỳ. Khi xung đột, editor đánh dấu để chọn đổi thời lượng, chuyển câu sang phần sau hoặc chèn hình; không tự bỏ nội dung. Kiểm tra coverage nguồn để phát hiện mất cảnh hoặc lặp ngoài hook/replay chủ động.

## 11. Dữ liệu tối thiểu và khả năng chạy lại

Project, SourceAsset, Scene, TranscriptCue, Character, ContextSummary, ScriptVersion, NarrationSegment, VoiceAsset, TimelineClip, SubtitleCue, Part và Job.

Job có trạng thái theo bước, progress, error, retry_count và checkpoint. Cache key gồm fingerprint nguồn, model, prompt version, preset giọng và setting render. Hủy job có chủ đích; mở lại ứng dụng tiếp tục từ bước còn thiếu. Chỉ một worker được claim cùng job; không ghi đè bản người dùng đã chỉnh/khóa.

## 12. Các giai đoạn và điều kiện hoàn thành

| Giai đoạn | Việc làm | Điều kiện qua bước |
|---|---|---|
| 0 — Kiểm chứng tích hợp | Một nguồn ngắn, batch ảnh/transcript qua API và Codex; một câu tiếng Việt qua OmniVoice; xác định GPU | Có JSON cảnh đúng schema, WAV phát được, số liệu thời gian/chi phí; xác định provider khả dụng |
| 1 — Đường xử lý media | Import URL/file, proxy, audio, transcript, shot/scene, lưu job | Dữ liệu có timestamp, resume được và đọc lại dự án đúng |
| 2 — Biên kịch và tự dựng | Rule editor, hook/title, TTS, mapping, template dọc, sub, xuất một phần | Có một bản nháp xem được từ đầu đến cuối bằng pipeline thật |
| 3 — Editor và nhiều phần | Chỉnh từng track, regenerate cục bộ, chia chính xác/tự nhiên/thủ công | Sửa câu hoặc ranh giới phần vẫn khớp hình/âm/sub, không mất đoạn |
| 4 — Hoàn thiện | Queue, cache, lỗi mạng, giới hạn tài nguyên, QA, đóng gói Windows | Chạy lại job, kiểm tra nguồn dài và preset mẫu ổn định |

Ước lượng sơ bộ cho một lập trình viên có kinh nghiệm: 4–6 tuần để có MVP với editor cơ bản; thêm 2–4 tuần nếu cần editor đa track trau chuốt và đóng gói ổn định. Đây là ước lượng lập kế hoạch, không phải cam kết; điều chỉnh sau giai đoạn 0 và benchmark trên máy thực tế.

## 13. Tiêu chí nghiệm thu

- Nhập được ít nhất một URL truy cập được và một file local; lỗi tải không mất dự án.
- Nội dung AI có mốc chứng cứ; hook có thể đổi; thân bài đi từ đầu tới cuối nguồn và có báo cáo coverage.
- MP4 đúng 9:16; ô video đúng 1:1; title sửa được; màu nền và vị trí sub đổi được.
- Tiếng gốc/AI rõ, không clipping hoặc hai giọng tranh nhau ngoài cấu hình chủ động; nghe kiểm tra đầu/giữa/cuối và chỗ chuyển giọng.
- Mục tiêu sai lệch sub ≤200 ms trên tập câu kiểm tra thủ công; câu vượt ngưỡng hoặc độ tin cậy thấp được đánh dấu, không mặc định ASR chính xác tuyệt đối.
- Không phụ đề vượt ranh giới part hoặc nằm ngoài khung; không cắt mất dấu tiếng Việt.
- Kiểm tra nguồn có frame rate biến thiên, thoại liên tục, cảnh không có lời, TTS dài hơn slot, hết hạn key, mất OmniVoice, restart job.
- Sửa một câu không gọi lại phân tích toàn video; các phần đã khóa không bị ghi đè âm thầm.
- Đo thời gian phân tích/TTS/render, peak VRAM/RAM và chi phí trên 1 phút nguồn trước khi công bố tốc độ.

## 14. Nguồn kỹ thuật

- Codex App Server, ảnh local và đăng nhập: https://learn.chatgpt.com/docs/app-server
- Codex SDK: https://learn.chatgpt.com/docs/codex-sdk
- OpenAI image inputs: https://developers.openai.com/api/docs/guides/images-vision
- yt-dlp: https://github.com/yt-dlp/yt-dlp
- Scene detection: https://www.scenedetect.com/docs/latest/api/detectors.html
- ASR và word timestamps: https://github.com/SYSTRAN/faster-whisper
- Dựng hình/âm/sub: https://ffmpeg.org/ffmpeg-filters.html
- OmniVoice thực tế: http://127.0.0.1:8001/config và http://127.0.0.1:8001/gradio_api/info

Thông số bố cục, nhịp hook, kiến trúc, lộ trình và tiêu chí nghiệm thu trong bản này là đề xuất thiết kế; chưa phải kết quả benchmark/kiểm thử ứng dụng.
