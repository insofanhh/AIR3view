# AIR3view Studio

Ứng dụng chạy trên máy để biến video YouTube hoặc file local thành video review dọc **1080 × 1920, 30 fps**. Backend Python/FastAPI, giao diện React/TypeScript, dựng bằng FFmpeg; AI phân tích qua Codex CLI hoặc OpenAI API, giọng đọc qua OmniVoice riêng.

## Tính năng

- **Một video tóm tắt:** chọn highlight từ toàn bộ nguồn, ưu tiên diễn biến nhanh, căng thẳng và đối thoại nổi bật có bằng chứng, giữ bối cảnh và kết quả.
- **Nhiều phần:** đặt số phần và thời lượng mong muốn mỗi phần trước khi chạy.
- Lời dẫn có **Mở đầu → Diễn biến → Kết thúc**. Mở đầu phát sau hook và một đoạn hình gốc; kết thúc nêu kết quả và bài học.
- Hình tiếp tục chạy khi AI nói; tiếng gốc mặc định tắt trong đoạn AI, bật lại sau đó.
- Ngôn ngữ đầu ra cho title, lời AI và phụ đề; có tiếng Việt và English.
- Phụ đề highlight từ đang đọc, chỉnh màu/bật tắt; xuất MP4, ASS và SRT.
- Sửa lời dẫn, phụ đề, crop, title, nền; lưu dự án trên máy, cache kết quả, hủy/thử lại tác vụ.

## 1. Yêu cầu trước khi cài

Hướng dẫn bên dưới dành cho **máy Windows 10/11 64-bit**. Windows là môi trường đã kiểm thử; chưa xác nhận toàn bộ quy trình trên macOS/Linux.

| Thành phần | Yêu cầu / ghi chú |
| --- | --- |
| Git | Dùng để clone và cập nhật mã nguồn |
| Python | Khuyến nghị 3.11 hoặc 3.12, bản 64-bit. Bản phát triển đã chạy với 3.10, nhưng yt-dlp cảnh báo ngừng hỗ trợ phiên bản này |
| Node.js | 22.x; dùng cùng npm để cài và build frontend |
| FFmpeg | Có encoder libx264, AAC và filter `ass`/libass; thêm thư mục `bin` vào PATH |
| Bộ nhớ | Nên có ít nhất 16 GB RAM, ưu tiên 32 GB khi chạy TTS và ASR local cùng lúc. Đây là khuyến nghị vận hành, không phải bảo đảm đủ cho mọi model/video |
| GPU | Không bắt buộc cho AIR3view. OmniVoice chạy CPU được nhưng chậm; cấu hình CUDA cần PyTorch phù hợp GPU/driver |
| Ổ đĩa | Chừa nhiều GB cho môi trường Python, model và video. Nguồn, proxy, WAV và bản xuất cùng tồn tại; nhu cầu tăng theo số dự án |
| Internet | Cần khi cài gói/tải model, tải YouTube và gọi nhà cung cấp AI |
| AI | Chọn **một**: tài khoản dùng Codex CLI đã đăng nhập, hoặc OpenAI API key với model hỗ trợ ảnh và Structured Outputs |

**Lỗi đã biết trên máy 8 GB:** ASR và OmniVoice cùng giữ model có thể làm cạn RAM/bộ nhớ ảo (`mkl_malloc`, `bad allocation`, `MemoryError`). Bản hiện tại **chưa tự chia audio thành các đoạn nhỏ để giảm RAM và chưa tự tháo/nạp OmniVoice**. Xem [cách xử lý thiếu bộ nhớ](#thiếu-bộ-nhớ).

Cài Git, Python và Node từ trang của nhà cung cấp. FFmpeg cho Windows có các bản build được liên kết tại [trang tải FFmpeg](https://ffmpeg.org/download.html); chọn bản có libass. Mở PowerShell mới sau khi cập nhật PATH, rồi kiểm tra:

```powershell
git --version
python --version
node --version
npm.cmd --version
ffmpeg -version
ffmpeg -hide_banner -filters | Select-String '\bass\s'
```

Lệnh cuối phải hiện filter `ass`. Nếu `python` mở Microsoft Store, chọn đúng Python đã cài hoặc dùng Python Launcher `py -3.11` ở bước tạo môi trường bên dưới.

## 2. Clone và cài AIR3view

Nên đặt mã nguồn trong thư mục local, ví dụ `C:\Projects`, để tránh tranh chấp đồng bộ SQLite/video.

```powershell
New-Item -ItemType Directory -Force C:\Projects | Out-Null
Set-Location C:\Projects
git clone https://github.com/insofanhh/AIR3view.git
Set-Location AIR3view

# Chọn Python rõ ràng nếu máy có nhiều phiên bản.
py -3.11 -m venv .venv
.\Setup-AIR3view.ps1
```

Nếu không có `py`, dùng `python -m venv .venv` với Python đúng phiên bản. Không sao chép `.venv` từ máy khác; hãy tạo lại trên máy đích.

Nếu PowerShell chặn script, có thể chạy các lệnh cài thủ công dưới đây; không cần thay Execution Policy của hệ thống:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run build
```

`Setup-AIR3view.ps1` kiểm tra công cụ cần thiết, tạo `.venv` nếu chưa có, cài thư viện và build giao diện. Nếu cần chỉ rõ đường dẫn Python lúc tạo môi trường:

```powershell
.\Setup-AIR3view.ps1 -Python 'C:\Path\To\Python311\python.exe'
```

**Script này không cài OmniVoice, FFmpeg hoặc đăng nhập AI.** Tiếp tục bước 3 và 4. Nếu dùng đường dẫn FFmpeg riêng, đặt `$env:FFMPEG_PATH` trước khi chạy setup/server.

## 3. Cài và chạy OmniVoice riêng

AIR3view không đóng gói model OmniVoice. Dùng môi trường Python riêng để tránh xung đột PyTorch/Gradio với backend. Hướng dẫn gốc: [OmniVoice](https://github.com/k2-fsa/OmniVoice).

Mở PowerShell thứ hai:

```powershell
Set-Location C:\Projects
git clone https://github.com/k2-fsa/OmniVoice.git
Set-Location OmniVoice

# Revision đã dùng để tích hợp hai endpoint Gradio của AIR3view.
git checkout 08be0b4ccbac3e13e374e86fbfead4b4cac343e2
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
```

Chọn **một** bản PyTorch. Ví dụ CPU:

```powershell
.\.venv\Scripts\python.exe -m pip install torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cpu
```

Hoặc NVIDIA với driver tương thích CUDA 12.8:

```powershell
.\.venv\Scripts\python.exe -m pip install torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
```

Các lệnh phiên bản ở trên dựa trên [hướng dẫn PyTorch](https://pytorch.org/get-started/previous-versions/#v280). Chọn build phù hợp máy; AIR3view không tự cài CUDA. Sau đó cài OmniVoice và Gradio đã dùng khi tích hợp:

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m pip install gradio==6.9.0
.\.venv\Scripts\python.exe -c "import torch; print('CUDA available:', torch.cuda.is_available())"
```

Chạy trên CPU:

```powershell
.\.venv\Scripts\omnivoice-demo.exe --ip 127.0.0.1 --port 8001 --device cpu --no-asr
```

Hoặc, khi CUDA hoạt động và GPU đủ bộ nhớ:

```powershell
.\.venv\Scripts\omnivoice-demo.exe --ip 127.0.0.1 --port 8001 --device cuda:0 --no-asr
```

Lần đầu sẽ tải model; chờ đến khi dịch vụ sẵn sàng rồi mở [OmniVoice local](http://127.0.0.1:8001). Giữ terminal này chạy. `Ctrl+C` dừng dịch vụ và giải phóng model.

`--no-asr` bỏ model nhận dạng riêng của OmniVoice để tiết kiệm bộ nhớ. Với chế độ clone, AIR3view tự nhận dạng lời nói trong audio tham chiếu theo ngôn ngữ nguồn khi tạo giọng, rồi gửi phần text đó cho OmniVoice. Kết quả được cache theo bytes của file audio; dùng lại cùng file ở dự án khác không cần nhận dạng lại. AIR3view vẫn dùng faster-whisper riêng để nhận dạng nguồn và canh phụ đề. Hướng dẫn giọng là thẻ OmniVoice hỗ trợ (ví dụ `male, low pitch`); để trống sẽ giữ giọng của audio, không nhập mô tả tự do.

Đây là bộ phiên bản tham chiếu, chưa phải lockfile toàn bộ phụ thuộc của OmniVoice. Khi cập nhật OmniVoice, kiểm tra lại hai endpoint `/_design_fn` và `/_clone_fn` trước khi chạy dự án lớn.

## 4. Kết nối nhà cung cấp AI

### Cách A — Codex CLI

Cài và đăng nhập trên **máy mới** theo [hướng dẫn Codex chính thức](https://github.com/openai/codex):

```powershell
npm.cmd install -g @openai/codex
codex --version
codex login
```

Mở lại terminal chạy AIR3view sau khi cài CLI. Trong tab **Kết nối**, chọn **Codex trên máy này**. Có thể để trống Model để dùng mặc định CLI. Bản tích hợp hiện dùng `codex exec`, ảnh và JSON Schema; CLI cần hỗ trợ `--ignore-user-config`, `--output-schema`, `--image` và các cờ feature của adapter. Nếu CLI báo cờ không được hỗ trợ, cập nhật CLI hoặc chọn OpenAI API; không cần cài Codex desktop để chạy server này.

Đăng nhập dùng tài khoản và hạn mức của chính bạn. Không sao chép file đăng nhập/token của máy cũ vào repository.

### Cách B — OpenAI API

Trong **Kết nối**, chọn **OpenAI API key**, nhập key, bấm **Lưu key trên máy** và nhập tên model có khả năng đọc ảnh/Structured Outputs. Adapter gửi request đến Responses API. Chi phí API phụ thuộc model và lượng ảnh/transcript.

Key nhập trong giao diện được lưu mã hóa trên máy bằng Windows DPAPI, dùng lại sau khi khởi động AIR3view và khi chuyển dự án. Key gắn với tài khoản Windows hiện tại, không nằm trong JSON dự án hay mã nguồn. Nút **Xóa key đã lưu** xóa bản lưu cục bộ. Có thể đặt biến môi trường `OPENAI_API_KEY` trước khi chạy server; nếu còn biến môi trường, ứng dụng vẫn có thể dùng key đó sau khi xóa bản lưu. Ứng dụng **không tự đọc file `.env`**.

## 5. Khởi động và kiểm tra

Quay lại terminal AIR3view:

```powershell
Set-Location C:\Projects\AIR3view
.\Start-AIR3view.ps1
```

Hoặc không dùng script PowerShell:

```powershell
.\.venv\Scripts\python.exe run.py
```

Mở **[http://127.0.0.1:8000](http://127.0.0.1:8000)**. Trong một terminal khác có thể kiểm tra:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
Invoke-RestMethod http://127.0.0.1:8000/api/omnivoice
```

`health.ok` và `health.ffmpeg` phải là `True`; kiểm tra OmniVoice trả `ok=True` khi dịch vụ 8001 đang chạy. Nếu dùng API key, `health.codex=False` không phải lỗi.

Server chỉ nghe localhost. Chạy **một instance AIR3view cho mỗi thư mục dữ liệu**; không thêm nhiều worker. Khi có job, ứng dụng giữ Windows không tự ngủ và khôi phục trạng thái sau khi job kết thúc/hủy/lỗi. Lần đầu nên thử nguồn ngắn trước khi xử lý video dài.

## 6. Quy trình sử dụng

1. Nhập link YouTube hoặc file video; chờ bước chuẩn bị proxy, WAV và khung hình hoàn tất.
2. Mở **Đầu ra**, chọn một trong hai chế độ:
   - **Một video tóm tắt:** đặt thời lượng mong muốn 30–1800 giây.
   - **Nhiều phần:** đặt số phần 1–100 và thời lượng mong muốn mỗi phần; luồng biên tập hiện yêu cầu tối thiểu 30 giây/phần.
3. Chọn ngôn ngữ text + voice và độ dài đoạn hình gốc trước lời mở đầu (mặc định 3 giây).
4. Bấm **Chạy toàn bộ**, hoặc chạy từng bước để duyệt: **AI phân tích & biên kịch → Tạo giọng → Dựng video**. Nguồn đã chuẩn bị được dùng lại.
5. Trong **Cảnh**, xem mốc nguồn và lý do chọn highlight. Trong **Lời AI**, duyệt các nhãn Mở đầu/Diễn biến/Kết thúc, sửa câu rồi tạo lại giọng.
6. Trong **Bố cục**, chỉnh title, nền, crop, cỡ phụ đề và màu highlight. Bật làm mờ phụ đề dính sẵn nếu chữ nguồn chồng với chữ mới.
7. **Dựng thử** tạo phần đầu ở 360×640; **Xuất video** dựng thành phẩm 1080×1920. Tải MP4/ASS/SRT ở bảng xuất hoặc mở bản dựng cạnh trình phát.

Thời lượng đặt là **mục tiêu tối đa đã bao gồm hook**. AI cắt theo câu chuyện nên kết quả có thể ngắn hơn, không thêm phần ngoài số yêu cầu, không lặp/đóng băng hình để lấp thời gian. Nguồn quá ngắn hoặc lời dài hơn cảnh sẽ báo lỗi thay vì cắt mất lời.

AI đọc các đoạn của toàn bộ nguồn trước khi lập bản biên tập tổng thể. Cấu trúc ba phần xuyên suốt từ video đầu đến video cuối, không bắt buộc lặp lại phần giới thiệu ở mỗi file. Bài học phải dựa trên nội dung; kết quả chưa rõ cần được nói rõ. Cần duyệt lại tên riêng và kết luận do AI tạo.

### Workflow phân tích tiết kiệm

Trong **Rule**, chọn cách phân tích:

- **Tiết kiệm · đọc lời thoại trước** (mặc định): lấy phụ đề nguồn, đọc toàn bộ transcript để lập diễn biến có mốc, kiểm tra một tập khung hình đại diện và các đoạn cần xác minh, rồi viết kịch bản cuối. Không tạo lời dẫn nháp cho từng phút để bỏ đi ở bước cuối. Video dài có thể cần chia transcript/ảnh thành nhiều nhóm.
- **Chi tiết · đọc từng phút**: giữ quy trình đọc ảnh và lời thoại từng phút, phù hợp khi cần xem hình kỹ hơn. Bật lượt kiểm tra sẽ gửi lại mỗi nhóm cho AI rà soát.

Chế độ tiết kiệm vẫn lấy hình ở đầu/cuối nguồn, phân bố xuyên suốt và ưu tiên đoạn không có thoại hoặc tình tiết chưa rõ. Nó không xem mọi khung hình; các hành động rất ngắn vẫn có thể bị bỏ sót. Khi bật kiểm tra lại, chỉ các kết quả cần xác minh mới được rà soát thêm. Các quy tắc mốc cảnh, thời lượng, tỷ lệ thoại và kết thúc câu chuyện vẫn được kiểm tra trước khi lưu kịch bản.

Ưu tiên phụ đề do kênh cung cấp; nếu không có thì thử phụ đề tự động YouTube. Phụ đề cần có văn bản và mốc hợp lệ; không lấy được thì nhận dạng audio trên máy. Bản lời nguồn được giữ riêng với bản dịch hiển thị.

Kết quả đọc transcript và xác minh hình có cache riêng. Đổi số phần/thời lượng/cách viết chỉ lập lại kịch bản từ bằng chứng đã có; đổi transcript, hình, model hoặc quy tắc phân tích liên quan sẽ làm mới các bước phụ thuộc. Nếu bước viết kịch bản thất bại, dữ liệu bằng chứng đã hoàn thành vẫn được giữ để thử lại.

Với video khoảng 20 phút có transcript tốt, mục tiêu là vài lượt gọi thay vì hơn 40 lượt khi quy trình từng phút có bật kiểm tra. Số lượt thực tế phụ thuộc độ dài nguồn, số đoạn chưa rõ và số lần sửa kết quả; không bảo đảm thời gian hay chi phí cố định.

Trong **Kết nối**, có thể chọn `gemini-3.1-flash-lite` để ưu tiên chi phí nếu model này được Google trả trong danh sách của key. Một số key mới vẫn liệt kê `gemini-2.5-flash-lite` nhưng Google có thể từ chối model đó; ứng dụng không tự đổi model đã chọn. Không cần thêm Groq để dùng workflow này; ASR cloud chưa được tích hợp ở bản này.

### Dùng Gemini API thay Codex trên máy

Trong tab **Kết nối**, chọn **Google Gemini API key**, nhập key rồi bấm **Lưu key trên máy**. AIR3view tải danh sách model mà key nhìn thấy; chọn model có khả năng đọc ảnh và bấm **Kiểm tra kết nối & model**. Sau khi lưu dự án, các bước phân tích khung hình, đọc transcript, viết/dịch lời dẫn và chọn highlight sẽ dùng Gemini thay cho Codex CLI. ASR và OmniVoice vẫn chạy cục bộ theo cấu hình riêng.

Gemini key được lưu mã hóa riêng với OpenAI key và dùng chung cho các dự án, không cần nhập lại mỗi lần mở ứng dụng trên cùng tài khoản Windows. Có thể đặt `GEMINI_API_KEY` hoặc `GOOGLE_API_KEY` trong môi trường trước khi chạy server. Khi chuyển sang máy hoặc tài khoản Windows khác, cần nhập lại API key; dữ liệu mã hóa không thay thế việc cấu hình key trên máy mới.

### Cấu hình dùng chung giữa các dự án

Các tùy chọn trong **Đầu ra, Bố cục, Giọng, Rule, Kết nối** được tự lưu làm cấu hình dùng chung khi bạn lưu/chỉnh dự án. Dự án mới và dự án được mở tiếp theo nhận cấu hình đã lưu gần nhất, gồm provider/model, ASR, URL OmniVoice, ngôn ngữ, thời lượng, màu/chữ, âm lượng, cách kể và quy tắc AI. File giọng tham chiếu cùng kết quả nhận dạng tự động có bản lưu chung, được sao chép vào dự án đích để sử dụng mà không phải tải lên hoặc nhận dạng lại cùng file.

Tiêu đề câu chuyện, bật/tắt và mốc hook, cùng danh sách mốc chia phần thủ công vẫn thuộc video nguồn. Dự án đang xử lý giữ cấu hình của tác vụ đang chạy; mở lại dự án sau khi tác vụ kết thúc để nhận cấu hình chung. Nếu đổi các tùy chọn ảnh hưởng đến kịch bản, cần phân tích lại trước khi tạo giọng/xuất video; lời dẫn và kết quả cũ không tự được viết lại.

Ở lần nâng cấp đầu tiên, ứng dụng lấy cấu hình từ dự án được cập nhật gần nhất. Sau đó chỉ thao tác lưu của người dùng cập nhật cấu hình chung; kết quả do AI tự tạo không trở thành mặc định cho các video khác.

Đổi chế độ, số phần, thời lượng, ngôn ngữ, hook hoặc quy tắc sẽ yêu cầu phân tích lại trước khi xuất/tạo giọng cho bản chọn cảnh mới. Dự án cũ vẫn xem được; chọn chế độ ở tab Đầu ra trước khi chạy mới. Phân tích lại thay lời dẫn hiện tại, nên sao lưu nếu cần giữ bản cũ.

### Phụ đề và âm thanh

- Tab **Giọng** dùng một giọng kể chung cho toàn bộ video và các phần. Chế độ **Tạo một giọng chung** tạo mẫu một lần rồi dùng mẫu đó cho mọi lời dẫn; mẫu được lưu trong dự án và giữ nguyên khi sửa lời hoặc tốc độ đọc. Có thể nghe mẫu trước khi xuất. Chế độ tham chiếu dùng chung audio bạn tải lên.
- **AI kể xuyên suốt** là mặc định cho dự án mới: kể bối cảnh, các chặng diễn biến và kết quả có bằng chứng, giữ 10–20% thời lượng cho thoại gốc quan trọng (mặc định 15%, tính cả hook). Đây là tỷ lệ đoạn ưu tiên thoại gốc ở âm lượng đầy đủ, không phải số từ. Các cảnh lời AI giữ tiếng nguồn nhỏ dưới nền cả khi ngắt câu, đồng thời chỉ hiện phụ đề lời kể. Thanh **Tiếng gốc dưới nền lời AI** chỉnh từ 0–100% so với âm lượng gốc, mặc định 15%; chọn 0% để tắt. Bấm **Lưu âm lượng & dựng lại** để cập nhật MP4. Chỉ đổi âm lượng sẽ dùng lại audio; nếu đổi giọng mẫu, lời đọc hoặc cấu hình tổng hợp giọng, ứng dụng tự tạo lại các đoạn cần thiết trước khi xuất, không viết lại kịch bản. Dự án cũ chọn chế độ này trong tab **Giọng**, rồi bấm **Viết lại lời kể & tạo video** để phân tích và dựng lại. Giọng kể chung vẫn được giữ.
- Lời kể được viết theo thời lượng cảnh và tốc độ giọng mẫu, chia thành đoạn tối đa 25 giây. OmniVoice tạo audio theo thời lượng đó; nếu lệch quá nhiều, ứng dụng yêu cầu sửa lời thay vì cắt câu hoặc chèn im lặng. Nguồn không có audio không thể giữ thoại gốc.
- Với dự án cũ, bấm **Áp dụng giọng chung cho toàn bộ video** để thay các đoạn từng được tạo bằng giọng riêng. Sau đó dựng/xuất lại để đưa giọng mới vào MP4. Đổi giới tính hoặc ngôn ngữ dùng mẫu tương ứng; không cần phân tích AI lại chỉ để đổi giọng.

- Highlight chỉ tô từ đang được đọc; khoảng nghỉ trả về màu thường. Câu thiếu mốc từ đủ tin cậy giữ chữ thường. MP4/ASS có màu; SRT chỉ giữ nội dung và thời gian.
- Bấm **Canh highlight theo âm thanh** sau khi sửa phụ đề hoặc để cập nhật dự án cũ; dùng lại WAV đã có.
- Tiếng gốc mặc định 0% lúc AI kể, trở lại sau đó. Ngoài lời AI, âm thanh gốc không được dịch lồng tiếng lại.
- Preview mô phỏng bố cục/lịch phát; MP4 do FFmpeg/libass dựng là bản chuẩn. Gain âm thanh lớn hơn 100% chỉ thể hiện đầy đủ khi render.
- Các video mẫu đã dính title/sub/nền cần được dùng làm tham chiếu; để dựng sạch hãy nhập nguồn gốc.

Editor tự lưu; `Ctrl+S` lưu ngay, Undo giữ tối đa 30 thay đổi trong phiên. Cache giữ các kết quả đã hoàn thành, nhưng bấm Thử lại không tự giải quyết lỗi thiếu RAM/hạn mức.

## 7. Dữ liệu, sao lưu và chuyển máy

```text
AIR3view/
├── backend/              API, AI, nhận dạng, timeline và render
├── frontend/             React/TypeScript; package-lock.json
├── scripts/              Kiểm tra và xác minh output
├── tests/                Kiểm thử, có test FFmpeg thực
├── data/                 Dữ liệu local, không đưa vào Git
│   ├── studio.sqlite3    Metadata dự án, trạng thái job và cấu hình dùng chung
│   ├── _preferences/    Bản lưu giọng tham chiếu dùng chung
│   ├── .private/        API key được mã hóa bằng Windows DPAPI
│   ├── models/           Model ASR đã tải
│   └── <project_id>/     Nguồn, proxy, WAV, frames, giọng, cache và bản xuất
├── requirements.txt      Các phụ thuộc Python trực tiếp được ghim phiên bản
├── Setup-AIR3view.ps1
└── Start-AIR3view.ps1
```

**Clone GitHub chỉ lấy mã nguồn và tài liệu**, không lấy video, giọng mẫu, dự án SQLite, model hay môi trường Python.

Để mang dự án đã làm sang máy khác:

1. Chờ/hủy job và dừng AIR3view trên máy cũ, rồi sao chép **toàn bộ `data/`**. Khi sao lưu SQLite, giữ cả các file `-wal`/`-shm` nếu còn tồn tại; không sao chép riêng DB lúc server đang ghi.
2. Cài phần mềm trên máy mới theo hướng dẫn ở trên, rồi dừng server máy mới.
3. Chép `data/` vào thư mục repository mới, hoặc chỉ định thư mục bằng `AIR3VIEW_DATA`. Không ghi đè dữ liệu có sẵn của máy đích; sao lưu trước hoặc dùng thư mục khác.
4. Khởi động lại AIR3view, cấu hình AI/OmniVoice và kiểm tra dự án. Tác vụ đang dở sẽ được đánh dấu gián đoạn để thử lại.

JSON tải từ editor chỉ là manifest để tham khảo, không chứa media và chưa có chức năng nhập lại toàn bộ dự án bằng JSON. Model OmniVoice thường nằm trong cache Hugging Face riêng; có thể tải lại trên máy mới. Không chuyển `.venv` hoặc `node_modules` giữa các máy.

## 8. Cập nhật và kiểm thử

Dừng server trước khi cập nhật; sao lưu `data/`:

```powershell
git pull --ff-only
.\Setup-AIR3view.ps1
.\Start-AIR3view.ps1
```

Kiểm thử local không gọi AI/TTS online:

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe scripts/check.py
npm.cmd --prefix frontend run build
```

Kết quả gần nhất trước khi đóng gói: **62 test đạt**, gồm dựng FFmpeg thật, chọn cảnh/ánh xạ timeline, mute audio, karaoke, số phần, cache và API; frontend build đạt. Đây không phải xác nhận đã cài thử trên mọi cấu hình máy mới. Các test biên kịch dùng phản hồi AI giả lập; nội dung thực vẫn cần duyệt.

Kiểm tra Codex thực, **có sử dụng hạn mức và gửi ảnh được chỉ định tới AI**:

```powershell
.\.venv\Scripts\python.exe scripts/check-codex.py 'C:\Path\To\test-image.jpg'
```

Phát triển giao diện: chạy backend và `npm.cmd --prefix frontend run dev` ở hai terminal. Vite ở `http://127.0.0.1:5173` proxy API/media về cổng 8000. Khi dùng server production, build frontend rồi restart backend.

## 9. Xử lý lỗi thường gặp

### Thiếu bộ nhớ

`mkl_malloc: failed to allocate memory`, `bad allocation`, `Unable to allocate ... MiB` hoặc `MemoryError` là hết bộ nhớ khả dụng; không phải lỗi link video hay API key.

- Kiểm tra Task Manager, đóng ứng dụng nặng và dừng dịch vụ OmniVoice nếu chưa cần TTS. Khởi động lại backend sau lỗi để giải phóng model ASR đang giữ.
- Chọn ASR `tiny`/`base` trong Kết nối nếu chấp nhận giảm chất lượng, ưu tiên phụ đề có sẵn hoặc nhập SRT để giảm nhận dạng nguồn ban đầu.
- Trên máy RAM thấp, chạy từng bước thay cho Chạy toàn bộ: phân tích khi OmniVoice đã dừng; lưu kết quả, restart backend khi không còn job, rồi mới bật OmniVoice và tạo giọng. Tạo giọng/canh karaoke vẫn dùng ASR và có thể tiếp tục thiếu RAM.
- Kiểm tra dung lượng trống ổ hệ thống/bộ nhớ ảo; tăng bộ nhớ ảo không bảo đảm hiệu năng và không thay thế RAM.
- Nếu máy vẫn cạn bộ nhớ, chuyển sang máy nhiều RAM hơn. Bản này chưa có cơ chế tự luân phiên model hoặc ASR theo chunk để bảo đảm hoạt động trên máy 8 GB.

### Không thấy giao diện / chỉ thấy thông báo build

Chạy `npm.cmd --prefix frontend ci`, `npm.cmd --prefix frontend run build`, rồi restart server. `frontend/dist` được tạo trên máy mới, không nằm trong Git.

### Thiếu `cublas64_12.dll` / CUDA ASR không hoạt động

Đây là lỗi thư viện GPU của faster-whisper khi nhận dạng hoặc canh phụ đề; môi trường OmniVoice riêng vẫn có thể hoạt động. AIR3view tự thử lại toàn bộ đoạn âm thanh bằng CPU int8 khi CUDA thiếu thư viện, driver không tương thích hoặc hết VRAM. Khi thành công, lựa chọn **Thiết bị ASR** của dự án được lưu thành **CPU**, tránh lặp lỗi ở từng lời dẫn. Lời kể, giọng mẫu và âm thanh đã tạo được giữ lại. Lỗi file, model hoặc thao tác hủy không bị che thành lỗi CUDA.

Có thể chọn **Kết nối → Thiết bị ASR → CPU** thủ công rồi chạy **Tạo giọng OmniVoice** để tiếp tục từ audio đã lưu, sau đó **Xuất video**. Chỉ chọn lại NVIDIA CUDA khi đã cài đúng cuBLAS CUDA 12 / cuDNN 9 và GPU còn đủ bộ nhớ; xem [yêu cầu faster-whisper](https://github.com/SYSTRAN/faster-whisper#gpu).

### OmniVoice chưa kết nối / không tạo được giọng

Mở `http://127.0.0.1:8001`, xem terminal OmniVoice đã nạp model xong chưa. Chạy đúng cổng, không bật chia sẻ public. Kiểm tra URL trong Kết nối là `http://127.0.0.1:8001`. Nếu API báo thiếu endpoint, dùng revision/Gradio tham chiếu ở bước 3. Với clone và `--no-asr`, AIR3view tự nhận dạng audio mẫu trước khi gửi text cho OmniVoice; lần đầu có thể mất thêm thời gian, còn cùng file sẽ dùng cache.

### Codex hết hạn mức hoặc không tìm thấy lệnh

Kiểm tra `codex --version`, đăng nhập trên máy đó và mở lại terminal server sau khi sửa PATH. Hết hạn mức thì chờ khôi phục hoặc chọn OpenAI API; các phân tích đã hoàn thành vẫn ở cache. CLI không nhận cờ có thể không tương thích adapter hiện tại.

### YouTube không tải được / thiếu JavaScript runtime

Thử nhập file local để tiếp tục. Nội dung yêu cầu đăng nhập/giới hạn truy cập có thể không tải được. yt-dlp có thể cần runtime JavaScript và bản extractor tương thích; xem [hướng dẫn yt-dlp](https://github.com/yt-dlp/yt-dlp#dependencies). Không gửi cookie/tài khoản qua repository. Cập nhật gói có chủ đích và kiểm tra lại pipeline sau thay đổi.

### FFmpeg hoặc font lỗi

Kiểm tra `ffmpeg -version`, filter `ass`, PATH/`FFMPEG_PATH`. Font mặc định trên Windows là Arial Bold. Nếu không có font, đặt `AIR3VIEW_FONT` đến file TTF phù hợp và kiểm tra MP4, nhất là với tiếng Việt. Cần font đã cài cho libass; biến này chủ yếu dùng đo chữ khi xuống dòng.

### Cổng đang được sử dụng

Dừng instance cũ hoặc đặt `AIR3VIEW_PORT` trước khi khởi động. Không chạy hai server cùng ghi vào một `data/`. Endpoint kiểm tra OmniVoice trên giao diện hiện dùng cổng 8001; nên giữ cổng này dù cấu hình TTS có trường URL.

## 10. Biến môi trường

Đặt trong terminal trước khi chạy server; không cần sửa code:

| Biến | Ý nghĩa |
| --- | --- |
| `AIR3VIEW_PORT` | Cổng backend, mặc định 8000 |
| `AIR3VIEW_DATA` | Đường dẫn tuyệt đối tới thư mục dữ liệu; mặc định `data/` trong repo |
| `FFMPEG_PATH` | Đường dẫn đầy đủ tới `ffmpeg.exe` nếu không dùng PATH |
| `CODEX_PATH` | Đường dẫn executable Codex nếu không tìm thấy trong PATH |
| `OPENAI_API_KEY` | API key trong môi trường server; không bắt buộc nếu dùng Codex |
| `AIR3VIEW_FONT` | Font TTF dùng đo bố cục phụ đề |

Ví dụ cho đường dẫn có dấu cách:

```powershell
$env:AIR3VIEW_DATA = 'D:\AIR3view Data'
$env:FFMPEG_PATH = 'C:\Tools\ffmpeg\bin\ffmpeg.exe'
$env:AIR3VIEW_PORT = '8000'
.\Start-AIR3view.ps1
```

AIR3view là công cụ local, chưa có xác thực tài khoản hay thiết kế để public ra Internet. Ảnh và transcript được gửi tới provider khi phân tích; video và file dựng được lưu trên máy. Chỉ xử lý nội dung/giọng mẫu bạn có quyền sử dụng. Chưa có installer `.exe`, timeline kéo-thả kiểu NLE, hay bảo đảm forced alignment hoàn hảo.

`PROJECT_PLAN.vi.md` là thiết kế lịch sử; `VALIDATION.vi.md` ghi kiểm chứng ở máy phát triển và tham chiếu một số file local không được upload. README này mô tả cách cài và luồng sử dụng hiện tại.
