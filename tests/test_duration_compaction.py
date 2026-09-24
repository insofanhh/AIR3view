import copy
import pytest
from backend.models import Settings
from backend.story import _compact_overlong_plan, duration_budget_stats


def context(target=330, ratio=.9, count=1):
    return {'metadata':{'duration':1500},'settings':Settings(production_workflow='plan_first',
      narration_style='storytelling',output_mode='single' if count==1 else 'parts',
      summary_seconds=target,part_seconds=target,part_count=count,duration_min_ratio=ratio).model_dump()}


def draft():
    ranges=[(58.079,110),(117.84,165.92),(218.4,280.72),(334.32,396.56),
            (542.8,592.56),(841.6,897.36),(1122,1181.16),(1213.76,1271.12),(1374.08,1439.6)]
    return {'hook':{'start':0,'end':3.08},'selections':[
      dict(start=a,end=b,part=1,section='opening' if i==0 else 'ending' if i==8 else 'development',
           priority=1, evidence='Keep evidence '+str(i),narration='Keep narration '+str(i))
      for i,(a,b) in enumerate(ranges)]}


def test_real_518_second_draft_is_not_greedily_cut_below_297():
    p=context();raw=draft();before=copy.deepcopy(raw)
    result=_compact_overlong_plan(raw,p)
    total=duration_budget_stats(result,p)['totals'][1]
    assert 297<=total<=330
    assert result['selections'][0]==raw['selections'][0]
    assert result['selections'][-1]==raw['selections'][-1]
    assert all(x in raw['selections'] for x in result['selections'])
    assert raw==before


def test_no_whole_scene_solution_leaves_original_for_ai_replan():
    p=context(60,1)
    raw={'hook':{'start':0,'end':4},'selections':[
        dict(start=0,end=20,section='opening',part=1),
        dict(start=20,end=70,section='development',part=1,priority=.1),
        dict(start=70,end=90,section='ending',part=1)]}
    assert _compact_overlong_plan(raw,p)==raw


def test_valid_plan_is_unchanged_and_ratio_is_never_lowered():
    p=context(600);raw=draft()
    assert _compact_overlong_plan(raw,p)==raw
    assert p['settings']['duration_min_ratio']==.9


def test_every_part_respects_its_own_bounds():
    p=context(60,.9,2)
    raw={'hook':{'start':0,'end':4},'selections':[
        dict(start=0,end=10,section='opening',part=1),
        dict(start=10,end=54,section='development',part=1,priority=1),
        dict(start=54,end=84,section='development',part=1,priority=.1),
        dict(start=100,end=144,section='development',part=2,priority=1),
        dict(start=144,end=174,section='development',part=2,priority=.1),
        dict(start=174,end=188,section='ending',part=2)]}
    result=_compact_overlong_plan(raw,p)
    assert duration_budget_stats(result,p)['totals']=={1:58,2:58}
