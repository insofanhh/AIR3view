# Kế hoạch phục hồi Reaction COPS khi dùng Codex

Ngày phân tích: 2026-10-07. Nguồn: `C:/Users/Admin/Downloads/air3view-ab6d83df.log` và code hiện tại.
Trạng thái: đã triển khai sửa schema, preflight, phân loại lỗi, checkpoint pending_review và giảm gọi AI chỉnh phụ đề nguồn nhiều cue. Phần tải YouTube 403 được loại khỏi phạm vi triển khai theo yêu cầu người dùng.

## Kết quả triển khai

- `backend/ai_schema.py`: chuẩn hóa và kiểm tra 18 response models của Codex/OpenAI, giữ nullable/default của dữ liệu lưu cũ và adapter Gemini riêng.
- `backend/ai_errors.py`: phân biệt schema, xác thực, hạn mức, timeout và kết nối; UI không nhận lại prompt từ log CLI.
- Job kiểm tra schema trước bước chuẩn bị/phân tích; lỗi schema không dùng vòng retry của lô hoặc giao diện.
- Checkpoint hội thoại tách `pending_review` khỏi `accepted`, giữ candidates khi reviewer/cancellation lỗi; chỉ dịch lại ID bị bác bỏ. Cache/checkpoint hợp lệ trước đây tiếp tục đọc được.
- Chế độ efficient bảo toàn văn bản/mốc/ID nguồn từ 128 cue trở lên, bỏ lượt chỉnh hình thức bằng AI và giữ bước phân loại vai trò. Chế độ detailed giữ AI cleanup.
- Mỗi yêu cầu có `.call.json` ghi thời gian/cache hit/số request; lỗi có `.failure.json` ghi provider/model/stage/schema hash/code/retryable. Không ghi prompt hoặc credentials vào các metadata này.
- Đợt kiểm chứng chính: 268 tests backend, 3 tests frontend auto-retry và frontend production build đạt. Có kiểm thử điều phối Reaction COPS, phục hồi hội thoại, TTS giả lập và FFmpeg render với nguồn tổng hợp.
- Kiểm chứng bổ sung: 39 tests schema/launcher đạt; sau bổ sung xử lý metadata bị khóa, 80 tests schema/dubbing/OpenAI đạt. Đây là các đợt có test trùng nhau, không cộng thành tổng số test độc lập. Metadata dùng file tạm riêng và không khiến cache hợp lệ bị gửi lại khi ghi log thất bại.
- Lần smoke Codex thật bằng hội thoại giả lập bị lỗi xác thực trong môi trường thực thi; chưa chứng minh dịch vụ chấp nhận yêu cầu hoặc chạy hết video thực tế. Không thay thông tin đăng nhập của người dùng để vượt qua lỗi này.
- Chưa đóng gói/phát hành một bản EXE mới trong lần triển khai này. Installer đã bổ sung preflight schema cho smoke test runtime đóng gói.

## 1. Nguyên nhân đã xác nhận

Ba tác vụ `034ba021`, `978dca38`, `11afdc42` dừng ở cùng lỗi HTTP 400:

```text
invalid_json_schema
Invalid schema for response_format 'codex_output_schema'
Missing 'issue'.
param: text.format.schema
```

Luồng lỗi: `reaction_cops.plan_reaction` → `reaction_dubbing.write` → `reaction_dubbing.review` → `providers.ask_ai` → Codex CLI.

`backend/reaction_dubbing.py`, model `DubReviewItem`, khai báo `issue: str = ''`.
Pydantic tạo property `issue` nhưng không đưa vào `required` vì trường có mặc định.
`backend/providers.py` gửi thẳng `response_model.model_json_schema()` qua `--output-schema`.
OpenAI strict Structured Outputs yêu cầu tất cả property của object thuộc `required`.

Đây là lỗi định nghĩa yêu cầu của ứng dụng, xảy ra trước khi Codex tạo kết quả kiểm tra nghĩa.
Thử lại cùng schema hoặc thay prompt lời thoại không sửa được lỗi này.
OpenAI API cũng nhận schema này với `strict: true`, nên cần sửa chung cho hai provider.

`codex_error()` hiện ghép thông báo kiểm tra kết nối/đăng nhập cho phần lớn lỗi, khiến lỗi schema bị mô tả sai.
Cảnh báo Code Mode bị tắt xuất hiện kèm theo; lỗi chặn thực tế trong log là schema HTTP 400.
Không cần bật quyền chạy shell/Code Mode để giải quyết schema của tác vụ trả JSON.

## 2. Thời gian và lỗi tải nguồn riêng biệt

| Giai đoạn lần chạy phân tích đầu | Thời gian xấp xỉ |
|---|---:|
| Đọc transcript | 2 phút 06 giây |
| Kiểm tra hình ảnh | 4 phút 14 giây |
| Tối ưu 402 cue phụ đề | 4 phút 31 giây |
| Phân loại vai trò 402 cue | 3 phút 40 giây |
| Chọn cảnh, viết commentary, dịch và kiểm tra hội thoại đến lúc lỗi | 54 giây |
| Tổng tác vụ `all` đầu | 15 phút 26 giây |

Hai lần thử lại mất khoảng 17 giây và 12 giây. Cache phân tích đang được dùng lại.
Log này chưa chạy đến tạo giọng hoặc FFmpeg render; không thể dùng nó để kết luận tốc độ render.

Trước đó tải YouTube lỗi `HTTP Error 403: Forbidden` sau khoảng 9 phút 30 giây.
Lần tải sau thành công, toàn bước chuẩn bị mất khoảng 3 phút 10 giây.
Log chưa đủ để phân biệt URL media hết hạn, phiên xác thực, giới hạn IP/client hay sự cố tạm thời.
Đây là lỗi khác với lỗi schema Codex.

## 3. Triển khai theo thứ tự

### P0.1 — Sửa hợp đồng dữ liệu kiểm tra hội thoại

- Trong `DubReviewItem`, bắt buộc `issue: str`; khi hợp lệ phải trả `issue: ""`.
- Prompt reviewer nêu rõ mọi item trả đủ `id`, `valid`, `issue`.
- Tiếp tục kiểm tra ID thiếu/lặp, số liệu, phủ định, người nói và tính trung thực như hiện tại.
- Không tự đặt `valid=true` khi reviewer lỗi; không bỏ qua kiểm tra nghĩa để qua bước.

### P0.2 — Chuẩn hóa schema tại ranh giới provider

- Tạo module chung, ví dụ `backend/ai_schema.py`, sinh schema gửi Codex/OpenAI từ model Pydantic.
- Duyệt root, `$defs`, `properties`, `items`, các nhánh union và reference; mọi object có `required` đủ property và `additionalProperties: false`.
- Loại metadata mặc định không cần cho transport. Trường nullable giữ khả năng nhận `null`; không biến mọi trường có default thành nullable.
- Không thay model lưu Settings/Project hoặc phá tương thích dữ liệu cũ chỉ để sửa schema AI.
- Kiểm tra root là object, reference hợp lệ và các cấu trúc không được provider hỗ trợ. Nếu gặp dictionary tự do hoặc cấu trúc không chuyển đổi an toàn, báo rõ model/đường dẫn và sửa response model tương ứng; không âm thầm bỏ dữ liệu.
- Giữ validator nghiệp vụ phía ứng dụng sau khi nhận kết quả. Schema đúng chưa đồng nghĩa nội dung đúng.
- Giữ adapter Gemini riêng; không áp dụng giới hạn của Codex/OpenAI cho Gemini một cách máy móc.
- Tạo registry các response model thực sự được các giai đoạn gọi. Kiểm tra schema của những giai đoạn dự kiến dùng ngay khi bắt đầu job, trước bước phân tích tốn thời gian.

### P0.3 — Phân loại lỗi và retry

- Ghi log có `stage`, provider, model, response model, schema hash, mã lỗi và retryable; che key/cookie/token và không đưa toàn prompt vào thông báo UI.
- `invalid_json_schema`: không retry tự động cùng yêu cầu; hiển thị “Schema kiểm tra hội thoại không hợp lệ: thiếu issue; cần cập nhật ứng dụng”.
- Tách lỗi đăng nhập, hết hạn mức, mạng, timeout và schema. Chỉ hướng dẫn đăng nhập khi có bằng chứng lỗi xác thực.
- Cập nhật `backend/batches.py` và retry phía UI để không lặp lỗi schema dù log chứa từ khóa khác như rate limit hoặc timeout.
- Lỗi tạm thời được thử lại có giới hạn và backoff, vẫn cho hủy; lỗi dữ liệu chỉ sửa lại đúng batch/đoạn.

### P0.4 — Tiếp tục đúng bước, bảo toàn cache

- `reaction_dubbing.write()` lưu candidates đã dịch/kiểm tra cấu trúc vào checkpoint `pending_review` trước khi gọi reviewer; tách rõ khỏi `accepted`.
- Khi resume: kiểm tra identity, nguồn, ngôn ngữ và candidates còn phù hợp; review các candidates còn chờ trước khi yêu cầu dịch mới.
- Chỉ chuyển sang `accepted` sau review nghĩa thành công và kiểm tra mốc/turns. Nếu reviewer bác bỏ, chỉ tái tạo các ID bị bác bỏ, với lý do cụ thể và số lượt hữu hạn.
- Schema mới phải tạo cache key mới cho yêu cầu chịu ảnh hưởng. Không xóa toàn bộ `analysis-cache`, video nguồn, evidence hoặc các bản dịch đã đạt.
- Vì adapter chung có thể đổi hash nhiều schema, thiết kế đọc cache legacy đã được kiểm tra Pydantic và hợp đồng nghiệp vụ; không dùng response chưa hợp lệ làm cache đạt.
- Không tăng version toàn checkpoint một cách gây mất tất cả tiến độ. Nếu đổi định dạng checkpoint, hỗ trợ đọc dạng cũ chỉ có `accepted`/`generation`.

### P0.5 — Kiểm chứng và phát hành

- Test schema `DubReview`: nested `required` gồm `id`, `valid`, `issue`; thiếu `issue` bị từ chối, `issue=""` được chấp nhận.
- Audit mọi response model trong registry cho Codex/OpenAI: default, nullable, list/object lồng nhau, `$defs`/reference và schema không được hỗ trợ.
- Mô phỏng Codex HTTP 400: thông báo đúng, không retry vô ích, lưu chẩn đoán.
- Mô phỏng reviewer lỗi sau khi dịch: resume chỉ review lại, không dịch lại; không tự chấp nhận hội thoại chưa review.
- Kiểm tra checkpoint/cache cũ, hủy job, chế độ nguyên tiếng nguồn, dubbed và commentary=0 full review; bảo đảm không đổi master prompt ngoài hợp đồng JSON.
- Chạy pipeline bằng provider giả lập đến voice/render để kiểm tra điều phối. Sau đó chạy smoke Codex thật bằng chính dự án/cấu hình lỗi trên máy có dữ liệu; xác nhận đi qua review trước khi kết luận lỗi thực tế đã hết.
- Khi checks đạt, đóng gói bản cài Windows mới để máy đang chạy dưới `AppData/Local/Programs/AIR3view` nhận được thay đổi. Sửa repo local không tự sửa ứng dụng cài ở máy khác.

## 4. P1 — Tải nguồn và giảm thời gian phân tích

- Khi tải media báo 403, tái trích xuất metadata/URL media mới trước khi thử lại, có giới hạn số lần; không tải đi tải lại URL đã bị từ chối.
- Chỉ tái sử dụng `.part` khi định dạng/nguồn tương thích; lưu chẩn đoán đã che thông tin xác thực. Lỗi cần xác minh phiên thì dừng và hướng dẫn cập nhật phiên thay vì lặp vô hạn.
- Đo thời gian/request count/cache hit từng giai đoạn. Tối ưu phụ đề hiện tốn 4 phút 31 giây cho 402 cue; đánh giá làm sạch xác định bằng code trước, chỉ gọi AI cho cue thực sự cần sửa.
- Giữ phân loại vai trò và chứng cứ: không giảm thời gian bằng cách coi toàn bộ lời bình nguồn là hội thoại thật.
- Thực hiện tối ưu hiệu năng sau sửa P0, đo cùng nguồn/cấu hình. Không hứa toàn pipeline 15–20 phút chỉ từ log chưa có TTS/render.

## 5. Tiêu chí nghiệm thu

1. Schema DubReview qua preflight và được Codex chấp nhận; không còn lỗi `Missing 'issue'`.
2. Dự án đã lỗi tiếp tục từ bản dịch/review hoặc cache gần nhất, không chạy lại toàn bộ phân tích.
3. Hội thoại chưa kiểm tra nghĩa không được render thành dữ liệu đã đạt.
4. Lỗi cấu hình/schema hiển thị đúng nguyên nhân và không tiêu hao N lượt retry.
5. Regression các chế độ Reaction COPS đạt; smoke dự án thật đi qua review, TTS và xuất video khi đủ runtime/nguồn/hạn mức.

Tài liệu đối chiếu: [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs), phần required fields và additionalProperties.
