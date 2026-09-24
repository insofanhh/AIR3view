# Đánh giá render AIR3view — 23/09/2026

Đối tượng: “Police Know He Was Hiding Something In This U-Haul”. Nguồn VP9/Opus 1920×1080, dài 805,033s. Bản dựng 1080×1920/30fps dài 373,367s, gồm 43 cảnh, 22 voice và 227 cue phụ đề. Hiệu ứng đang bật: nền mờ và che phụ đề nguồn 16%.

Máy có 28 luồng CPU, NVIDIA RTX 3050 6GB. Khi khảo sát, khoảng 5GB VRAM đã được dùng. NVENC được thử bằng encode thật và hoạt động.

## Số đo

Benchmark tạo file riêng trong `data/8a7f64bcec56445c96bd971db30c540b/diagnostics/`; không thay đổi các bản xuất hoặc nội dung dự án. Không tính AI/ASR/TTS vào thời gian này. Số đo một lượt chịu ảnh hưởng tải máy, cache ổ đĩa và nhiệt độ; không phải cam kết cho mọi video.

| Bản dựng đầy đủ 6:13 | Giây thực | MP4 (MB thập phân) |
|---|---:|---:|
| Hiện tại: libx264 veryfast CRF20, filter 2 / encoder 4 | 98,124 | 165,89 |
| NVENC p4 CQ26, filter 4, giữ nguyên 43 input cảnh | 57,054 | 178,28 |
| NVENC p4 CQ26, gộp input liên tục còn 9 | 49,626 | 178,29 |
| NVENC p4 CQ20, gộp input còn 9 | 48,046 | 369,94 |

Phương án giữ nguyên cắt cảnh giảm khoảng 42% thời gian ở lần đo này, file lớn hơn khoảng 7,5%. CQ của NVENC không tương đương trực tiếp với CRF của libx264.

Mẫu 60s: CPU hiện tại 15,110s; CPU filter4/encoder8 12,056s; NVENC p4 CQ20 filter4 9,455s; thêm gộp input 7,656s; NVENC p4 CQ26 và gộp 7,451s.

## Kiểm tra chất lượng và quyết định

- Tất cả bản benchmark đạt thời lượng và kích thước đầu ra.
- SRT giữa CPU và NVENC giữ nguyên cắt cảnh giống nhau.
- 16 frame kiểm tra quanh điểm nối và trong cảnh ở phút đầu: sai khác tuyệt đối trung bình dưới 1,2 mức trên thang 0–255; PSNR so với bản CPU khoảng 41,5–49,3dB. Đây là so với bản nén CPU, không phải đo chất lượng tuyệt đối từ bản lossless.
- Bản gộp input có sai khác lớn hơn ở một số frame quanh ranh giới do cách seek/fps của nguồn VP9. Chưa dùng gộp input trong renderer chính.

## Đã áp dụng

1. `Bố cục → Bộ mã hóa xuất`: Tự động / CPU / NVIDIA NVENC.
2. Auto kiểm tra encode NVENC thật. Nếu khả dụng: p4, VBR, CQ26. Nếu không: libx264 veryfast CRF20.
3. Tối đa 4 luồng filter, 8 luồng CPU encode, giới hạn theo số CPU của máy.
4. Auto có fallback CPU cho lỗi khởi tạo thiết bị NVENC. Hủy/lỗi dữ liệu/lỗi filter không bị coi là lỗi GPU để retry.
5. Giữ nguyên input từng cảnh, mốc cắt, mix âm thanh, subtitle và kiểm tra file cuối.
6. Đổi version renderer để không tái sử dụng nhầm cache bản cũ. Kết quả xuất ghi lại encoder thực tế.

## Ưu tiên tiếp theo

1. Preview 10–20s quanh vị trí đang xem, xử lý trực tiếp ở độ phân giải preview; hiện preview vẫn qua bố cục 1080×1920 rồi mới thu nhỏ.
2. Tách fingerprint render khỏi các thiết lập AI không ảnh hưởng hình/tiếng. Thay đổi rule/provider không nên tự làm mất cache một bản media vẫn giống nhau.
3. Căn phụ đề chỉ cho các đoạn thoại gốc được dùng; dùng lại mốc từ sẵn có. NVENC không làm Whisper/VieNeu nhanh hơn.
4. Chuẩn hóa mapping frame trước khi gộp input liên tục; chạy kiểm tra tại mọi điểm nối rồi mới bật.
5. Với video rất dài: cân nhắc cache render theo đoạn 30–60s và ghép cuối; phải xử lý keyframe, audio padding và subtitle qua biên, không ghép MP4 tùy tiện.
6. Hiển thị tiến độ/ETA theo `out_time` từ FFmpeg thay vì phần trăm cố định ở đầu bước.

Không nên bật giải mã GPU cho hàng chục cảnh cùng lúc khi VRAM gần đầy, hoặc tăng số worker không giới hạn. Cần đo thêm trước khi chọn NVDEC/CUDA cho toàn filtergraph.

## Tài liệu tham chiếu

- [FFmpeg — filter_complex_threads](https://ffmpeg.org/ffmpeg.html#Advanced-options): số luồng xử lý filtergraph.
- [NVIDIA — FFmpeg hardware acceleration](https://docs.nvidia.com/video-technologies/video-codec-sdk/13.0/ffmpeg-with-nvidia-gpu/index.html): NVENC/NVDEC và truyền frame GPU.

Mã đo lặp: `scripts/benchmark_render.py --project <id> --seconds 60 --cases baseline_x264,nvenc_p4_cq26_unmerged`.
