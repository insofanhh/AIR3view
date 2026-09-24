"""Compare render settings on an isolated excerpt without changing a project."""
import argparse
import copy
import json
import os
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault('FFMPEG_PATH', str(ROOT/'.venv/Scripts/ffmpeg.exe'))
from backend import store, render, media
from backend.timeline import build, slice_clips


def continuous_clips(timeline, start, end):
    result=[]
    for item in slice_clips(timeline,start,end):
        item=copy.deepcopy(item)
        if (result and item['kind'] in ('original','highlight') and result[-1]['kind']==item['kind']
                and abs(result[-1]['source_end']-item['source_start'])<.00001
                and abs(result[-1]['end']-item['start'])<.00001):
            result[-1].update(source_end=item['source_end'],end=item['end'])
        else:
            result.append(item)
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--project',required=True)
    parser.add_argument('--seconds',type=float,default=60)
    parser.add_argument('--cases',default='',help='Comma-separated subset of cases')
    options=parser.parse_args()
    project=store.read(options.project)
    # Keep the CPU baseline explicit even if production now defaults to auto.
    project['settings']['render_encoder']='cpu'
    if store.busy(options.project):
        raise RuntimeError('Project is currently running; benchmark after it completes.')
    timeline=build(project,strict=True)
    duration=min(options.seconds,timeline['duration'])
    part={'index':1,'start':0,'end':duration,'duration':duration}
    folder=store.project_dir(project['id'])/'diagnostics'/('render-benchmark-'+time.strftime('%Y%m%d-%H%M%S'))
    folder.mkdir(parents=True,exist_ok=False)
    original_run=render.run
    original_slice=render.slice_clips
    results=[]
    cases=[('baseline_x264',2,4,False,False,20,'p4'),('cpu_8_threads',4,8,False,False,20,'p4'),
           ('nvenc_p4',2,4,True,False,20,'p4'),('nvenc_p4_filter4',4,4,True,False,20,'p4'),
           ('nvenc_p4_contiguous',4,4,True,True,20,'p4'),
           ('nvenc_p4_cq26',4,4,True,True,26,'p4'),('nvenc_p5_cq24',4,4,True,True,24,'p5'),
           ('nvenc_p4_cq26_unmerged',4,4,True,False,26,'p4')]
    if options.cases:
        names=set(options.cases.split(','))
        if names-{x[0] for x in cases}:raise ValueError('Unknown benchmark case')
        cases=[x for x in cases if x[0] in names]
    print(json.dumps({'seconds':duration,'full_clips':len(timeline['clips']),
                      'full_contiguous':len(continuous_clips(timeline,0,timeline['duration'])),
                      'sample_clips':len(slice_clips(timeline,0,duration)),
                      'sample_contiguous':len(continuous_clips(timeline,0,duration)),
                      'folder':str(folder)},ensure_ascii=False),flush=True)
    for label,filter_threads,encoder_threads,gpu,merge,cq,preset in cases:
        target=folder/label;target.mkdir()
        def run(args,**kwargs):
            args=[str(x) for x in args]
            args[args.index('-filter_complex_threads')+1]=str(filter_threads)
            indices=[i for i,x in enumerate(args) if x=='-threads']
            args[indices[-1]+1]=str(encoder_threads)
            if gpu:
                args[args.index('-c:v')+1]='h264_nvenc'
                args[args.index('-preset')+1]=preset
                args[args.index('-crf')]='-cq'
                args[args.index('-cq')+1]=str(cq)
                args[-1:-1]=['-b:v','0']
            (target/'command.json').write_text(json.dumps(args,indent=2),encoding='utf-8')
            output=original_run(args,**kwargs)
            (target/'ffmpeg.log').write_text(output,encoding='utf-8')
            return output
        render.run=run
        render.slice_clips=continuous_clips if merge else original_slice
        started=time.perf_counter()
        try:
            artifact=render.render_part(project,timeline,part,target,lambda:None,width=1080)
            elapsed=time.perf_counter()-started
            result={'case':label,'elapsed_seconds':round(elapsed,3),'speed':round(duration/elapsed,3),
                    'bytes':store.asset(project['id'],artifact['file']).stat().st_size,'artifact':artifact}
        except Exception as exc:
            result={'case':label,'error':str(exc)[-1200:]}
        finally:
            render.run=original_run;render.slice_clips=original_slice
        results.append(result)
        (folder/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
