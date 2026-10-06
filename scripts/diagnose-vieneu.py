"""Reproduce one saved narration without changing the project or calling an LLM."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend import store, vieneu
from backend.media import probe


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', required=True)
    parser.add_argument('--narration', default='story-sel0')
    parser.add_argument('--device', choices=('cpu', 'cuda', 'auto'), default='cpu')
    parser.add_argument('--timeout', type=float, default=180)
    args = parser.parse_args()
    with sqlite3.connect(f'file:{store.DB.as_posix()}?mode=ro', uri=True) as conn:
        row = conn.execute('select body from projects where id=?', (args.project,)).fetchone()
    if not row:
        raise ValueError('Project not found')
    project = json.loads(row[0])
    narration = next(n for n in project['narrations'] if n['id'] == args.narration)
    reference = next((store.project_dir(args.project) / 'vieneu-references').glob('*.wav'))
    settings = dict(project['settings'], vieneu_device=args.device)
    output = store.DATA / 'diagnostics' / 'vieneu-sdk' / f'{args.narration}-{args.device}.wav'
    started = time.monotonic()
    def report(progress, message):
        print(f'{time.monotonic()-started:.2f}s {message}', flush=True)
    runtime = vieneu.LocalRuntime()
    try:
        # Capture the worker's native stack if an inference has stopped making progress.
        params = vieneu.infer_parameters(settings, narration['text'], reference)
        runtime.generate(params, output, args.device, report, lambda: None,
                         timeout=args.timeout)
        print(json.dumps({'device': runtime.info.get('device'), 'seconds': round(time.monotonic()-started, 3),
                          'duration': probe(output)['duration'], 'target': narration['target_duration'],
                          'file': str(output)}), flush=True)
    finally:
        runtime.close()


if __name__ == '__main__':
    main()
