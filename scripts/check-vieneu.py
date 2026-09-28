"""Download/warm VieNeu and synthesize short neutral samples (no API key)."""
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend import vieneu, store
from backend.media import probe
from backend.models import Settings


def main():
    folder = store.DATA / 'diagnostics' / 'vieneu-sdk'
    folder.mkdir(parents=True, exist_ok=True)
    settings = Settings(vieneu_device='cpu').model_dump()
    def report(_progress, message):
        print(message, flush=True)
    try:
        for index, text in enumerate(('Xin chào, đây là giọng đọc thử của AIR3view. Câu chuyện bắt đầu từ một cuộc gặp bất ngờ.',
                                      'Chúng ta cùng theo dõi những diễn biến tiếp theo của câu chuyện.')):
            start = time.monotonic()
            output = vieneu.generate(settings, text, folder / f'preset-{index+1}.wav', report, lambda: None)
            print(json.dumps({'file': str(output), 'elapsed': round(time.monotonic()-start, 2),
                              'duration': probe(output)['duration']}), flush=True)
        settings['voice_mode'] = 'clone'
        start = time.monotonic()
        output = vieneu.generate(settings, 'Đây là câu thử tiếp theo, sử dụng lại mẫu giọng vừa tạo.',
                                 folder / 'clone.wav', report, lambda: None, reference=folder / 'preset-1.wav')
        print(json.dumps({'file': str(output), 'elapsed': round(time.monotonic()-start, 2),
                          'duration': probe(output)['duration']}), flush=True)
    finally:
        vieneu._runtime.close()


if __name__ == '__main__':
    main()
