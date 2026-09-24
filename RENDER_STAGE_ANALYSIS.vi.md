# Đánh giá bước “Đang dựng phần 1/1” — 24/09/2026

## Kết luận

Giữ bước xuất MP4 cuối. Đây là bước hiện thực hóa lịch dựng thành file xem/chia sẻ được, không phải một lượt AI phân tích hoặc một bước chia nhỏ thừa. “1/1” có nghĩa đầu ra hiện tại có một video. Có thể hoãn xuất khi đang duyệt kịch bản hoặc xem trước; không thể bỏ hoàn toàn nếu cần file thành phẩm có cắt cảnh, hook, giọng AI, nền, tiêu đề và phụ đề như cấu hình.

Luồng: timeline đã khóa → đọc các đoạn nguồn → xử lý hình + trộn tiếng + vẽ phụ đề → mã hóa → đóng gói MP4 → kiểm tra thời lượng/kích thước → công bố file.

Chép nguyên luồng video (`streamcopy`) nhanh nhưng không áp dụng được các thay đổi hình như crop, blur, tiêu đề và phụ đề cháy vào hình; các phép lọc cần khung hình đã giải mã. [FFmpeg — Streamcopy và filtering](https://ffmpeg.org/ffmpeg.html#Streamcopy).

## Dữ liệu dự án gần nhất

“Bus Driver Caught Stabbing Passenger in 4K”, ID `bf7529d74de64c8d9f1c03a4b45a8b85`:

- Nguồn VP9/Opus, 938,366 giây, khoảng 430,12 MB.
- Thành phẩm 299,567 giây, 1080×1920, 30fps: gần 9.000 khung hình.
- 20 cảnh, 15 voice, 129 cue; phụ đề ASS tạo 933 sự kiện do tô từng từ và các lớp chữ.
- Nền mờ, che phụ đề nguồn 15%, phụ đề tô từ đều đang bật.
- File thành phẩm 187,93 MB, encoder ghi nhận là **NVENC**.
- Khoảng cách thời gian từ ghi filtergraph đến kết quả xuất là **95,65 giây**. Đây là ước tính lượt dựng từ dấu thời gian file, không phải hồ sơ CPU từng công đoạn.
- Khi kiểm tra, các job đã hoàn tất, không còn FFmpeg dựng video. GPU hiện tại là RTX 3050 6GB, khoảng 5GB VRAM đang được sử dụng; đây là ảnh chụp trạng thái sau lượt dựng, không chứng minh mức sử dụng trong lượt cũ.

## Đo tách từng yếu tố

Đã dựng riêng 60 giây đầu, giữ nguyên nguồn, mốc cảnh, giọng và 1080×1920. Mẫu có 6 input cảnh và 4 voice. File thử nằm tại `data/bf7529d74de64c8d9f1c03a4b45a8b85/diagnostics/render-ablation-20260924-125847/`; không thay đổi bản xuất hay cấu hình dự án.

| Thay đổi so với cấu hình hiện tại | Thời gian | File MB | Đánh đổi |
|---|---:|---:|---|
| Giữ nguyên | 9,454s | 26,08 | Mốc đối chiếu |
| Nền màu thay nền mờ | 8,653s | 23,98 | Đổi hình thức nền |
| Tắt làm mờ phụ đề nguồn | 7,853s | 27,74 | Lộ phụ đề gốc |
| Phụ đề thường, không tô từng từ | 8,649s | 25,69 | Mất hiệu ứng tô từ |
| Không vẽ phụ đề vào hình | 7,641s | 25,10 | MP4 không còn phụ đề cháy; vẫn có SRT |
| NVENC p1 thay p4 | 8,643s | 29,86 | File lớn hơn khoảng 14,5%; chưa đánh giá chất lượng tương đương |

Mỗi biến thể đo một lượt, chịu ảnh hưởng cache và tải máy. Không cộng các phần trăm hoặc nhân tuyến tính để dự đoán thời gian video đầy đủ. Các phép bỏ hiệu ứng nhằm xác định chi phí, không phải đề nghị tự bỏ chức năng người dùng cần.

## Vì sao bước này mất thời gian

1. **Mỗi khung hình đều phải được dựng lại.** Cắt/ghép, resize/crop, nền mờ, che phụ đề nguồn, tiêu đề và phụ đề được xử lý rồi mới mã hóa.
2. **Đã dùng GPU encode nhưng còn phần CPU.** Renderer hiện không dùng giải mã GPU; trim/fps/scale/blur/overlay/ASS chạy qua filtergraph phần mềm. Bật NVENC không tự chuyển tất cả filter sang GPU. [NVIDIA — FFmpeg hardware acceleration](https://docs.nvidia.com/video-technologies/video-codec-sdk/13.0/ffmpeg-with-nvidia-gpu/index.html).
3. **Mỗi cảnh mở một input VP9 riêng.** Cách này tránh lỗi giữ quá nhiều khung hình trong RAM khi hook ở cuối nguồn rồi quay về đầu. Nó có thêm chi phí khởi tạo/seek/giải mã ở video nhiều cảnh. Chưa có hồ sơ đủ để gọi đây là nút thắt lớn nhất của lượt cũ.
4. **Phụ đề tô từ tăng số sự kiện chữ.** Mẫu đo cho thấy có chi phí nhưng không giải thích toàn bộ thời gian. Bỏ căn từng từ trước đó cũng không bỏ bước dựng hình và mã hóa này.
5. **Thông báo thiếu tiến độ thật.** Code chỉ báo trước mỗi phần; với 1 phần, nội dung không đổi đến khi FFmpeg kết thúc. Chưa thu `out_time`/fps/speed để hiển thị tiến độ và ETA. [FFmpeg — progress](https://ffmpeg.org/ffmpeg.html#Advanced-options).
6. **Cache hiện có nhưng còn thô.** Một phần hoàn chỉnh đã được lưu và dùng lại nếu toàn bộ fingerprint khớp. Fingerprint hiện chứa cả settings, narration và transcript: đổi một yếu tố có thể làm dựng lại cả phần. Không nên mô tả tình trạng này là “chưa có cache”.

## Kế hoạch ưu tiên

### P0 — Đo rõ và tránh chạy thừa

- Thu tiến độ `out_time`, fps, speed; hiển thị “đã dựng 02:10/05:00”, ETA và giai đoạn đóng gói/kiểm tra. Không coi đây là cải thiện tốc độ thực.
- Tách cache hình, tiếng và phụ đề theo đúng dữ liệu ảnh hưởng. Đổi rule/provider mà media không đổi không làm mất cache render.
- Khi chỉ đổi âm lượng: dùng lại video đã mã hóa, trộn lại audio và đóng gói MP4. Nếu sửa text/subtitle thì phải xác định lớp hình có đổi hay không; không dùng đường tắt gây lệch phụ đề.
- Xem thử đoạn 10–20s và dựng trực tiếp ở độ phân giải preview. Hiện tại preview vẫn xử lý bố cục 1080×1920 trước khi thu xuống 360×640.

### P1 — Dựng lại theo đoạn, giữ nguyên chất lượng đầu ra

- Chia tác vụ nội bộ thành các đoạn 30–60s hoặc tại biên cảnh phù hợp, cache từng đoạn theo hình/tiếng/phụ đề liên quan.
- Chỉ dựng lại đoạn bị tác động; ghép các đoạn đã mã hóa tương thích ở bước cuối.
- Kiểm tra biên frame, voice, subtitle, AAC padding và PTS; không nối MP4 tùy tiện hoặc giả định mọi đoạn độc lập.
- Chỉ thử 1–2 worker sau khi đo RAM/VRAM. Không mở hàng chục decoder GPU khi VRAM đã gần đầy. Cache đoạn chủ yếu giúp lần sửa/retry; tốc độ lần xuất đầu phải benchmark riêng.

### P2 — Tăng tốc lần xuất đầu

- Thử gộp các cảnh thực sự liên tiếp hoặc chuẩn hóa nguồn dễ giải mã khi có nhiều lần sử dụng; chỉ bật khi lợi ích vượt chi phí chuyển mã ban đầu.
- Bản benchmark cũ từng gộp input nhanh hơn nhưng sai khác tại một số frame nối; phải sửa mapping frame và kiểm tra mọi biên trước khi đưa vào renderer chính.
- Thử đường giải mã/scale GPU theo lô nhỏ, đo cả chi phí chuyển frame về CPU để vẽ ASS; không mặc định “toàn GPU” sẽ nhanh hơn trong cấu hình hiện tại.
- Thêm chế độ nhanh tùy chọn: nền màu, phụ đề thường, preset p1 hoặc 720p. Mặc định giữ chất lượng/hiệu ứng người dùng đã chọn; không tự đổi giọng, tốc độ phát hay ngân sách thời lượng.

## Tiêu chí chấp nhận

- So sánh cùng timeline ở cả mẫu ngắn và toàn video; đo cold/warm cache riêng, ít nhất ba lượt cho phương án sẽ triển khai.
- Không mất tiếng hook, không chồng giọng, không sai phụ đề và không thêm/mất cảnh.
- Kiểm tra frame tại biên, tổng thời lượng, kích thước và đồng bộ tiếng; kiểm tra dung lượng/chất lượng khi đổi encoder/preset.
- Lỗi/hủy chỉ làm mất đoạn đang dựng, giữ các đoạn đã đạt kiểm tra.

Đề xuất chốt: giữ xuất cuối; ưu tiên cache theo lớp và theo đoạn để tránh dựng lại, preview ngắn để duyệt nhanh, rồi mới tối ưu sâu decode/filter. Không cam kết mức tăng tốc toàn video từ một mẫu 60 giây.
