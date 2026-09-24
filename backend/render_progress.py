"""Cancelable FFmpeg execution with periodic media-time progress."""
import math
import subprocess
import tempfile
import time
from pathlib import Path
from .media import NO_WINDOW


def parse_progress(text):
    """Ignore incomplete writes and unknown/N/A values from FFmpeg."""
    rows={}
    for line in text.splitlines():
        key,separator,value=line.partition('=')
        if separator:rows[key]=value.strip()
    try:done=max(0,float(rows.get('out_time_us','0'))/1_000_000)
    except ValueError:done=0
    try:speed=float(rows.get('speed','0').removesuffix('x'))
    except ValueError:speed=0
    return (done if math.isfinite(done) else 0, speed if math.isfinite(speed) and speed>0 else 0)


def run_progress(args,folder,check,duration,progress,timeout=7200):
    folder=Path(folder)
    with tempfile.TemporaryDirectory(prefix='progress-',dir=folder) as temporary, tempfile.TemporaryFile() as log:
        path=Path(temporary)/'ffmpeg.txt'
        command=[str(args[0]),'-progress',str(path.resolve()),'-stats_period','0.5','-nostats',*[str(x) for x in args[1:]]]
        check()
        process=subprocess.Popen(command,cwd=folder,stdout=log,stderr=log,creationflags=NO_WINDOW)
        started=last=time.monotonic()
        try:
            while process.poll() is None:
                check()
                now=time.monotonic()
                if now-started>timeout:raise TimeoutError('Quá thời gian dựng; các đoạn đã xong được giữ để thử lại.')
                if now-last>=.5:
                    last=now
                    try:done,speed=parse_progress(path.read_text('utf-8',errors='replace'))
                    except OSError:done,speed=0,0
                    progress(min(duration,done),duration,speed)
                time.sleep(.15)
        except BaseException:
            process.kill();process.wait()
            raise
        log.seek(0)
        output=log.read().decode('utf-8',errors='replace')
        if process.returncode:raise RuntimeError(output[-5000:])
        check()
        progress(duration,duration,0)
        return output
