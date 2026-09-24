import pytest
from backend.models import Settings
from backend.story import duration_budget_stats


@pytest.mark.parametrize('target,ratio,source,count,mode,workflow,expected',[
    (300,.9,1440,1,'single','plan_first',270),
    (300,.8,1440,1,'single','plan_first',240),
    (600,.9,1440,1,'single','plan_first',540),
    (0,.9,600,1,'single','plan_first',540),
    (300,.9,120,1,'single','plan_first',108),
    (300,.9,600,3,'parts','plan_first',180),
    (300,.75,600,3,'parts','plan_first',150),
    (300,.9,1000,1,'single','legacy',225),
])
def test_displayed_budget_matches_backend(target,ratio,source,count,mode,workflow,expected):
    p={'metadata':{'duration':source},'settings':Settings(output_mode=mode,production_workflow=workflow,
       summary_seconds=target,part_seconds=target or 60,part_count=count,duration_min_ratio=ratio).model_dump()}
    assert duration_budget_stats({},p)['minimum']==pytest.approx(expected)
