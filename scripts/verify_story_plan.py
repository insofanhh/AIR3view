"""Check final planning against a saved project, without TTS/render/project edits.

This may call the configured AI provider (up to the planner's bounded attempts).
Valid responses use the normal analysis cache so a later UI retry reuses them.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend import providers, store
from backend.story import plan_story, validate_plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project_id')
    args = parser.parse_args()
    if store.busy(args.project_id):
        raise ValueError('Project has a queued/running job; wait before verification.')
    project = store.read(args.project_id)
    result = plan_story(project, lambda progress, message: print(f'{progress}% {message}', flush=True), lambda: None)
    validated = validate_plan(result['story_plan'], result)
    totals = {}
    for item in validated['selections']:
        totals[item['part']] = totals.get(item['part'], 0) + item['end'] - item['start']
    totals[1] += validated['hook']['end'] - validated['hook']['start']
    print(json.dumps({'valid': True, 'selection_count': len(validated['selections']),
                      'durations': totals, 'project_saved': False}))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(providers.redact(str(exc)), file=sys.stderr)
        raise SystemExit(1)
