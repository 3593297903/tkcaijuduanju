"""Advance the established reporting pipeline without modifying prior snapshots."""
from pathlib import Path

ROOT=Path(__file__).resolve().parent
PREV=ROOT.parent/'monitoring_2026-10-27'
source=(PREV/'prepare_reports.py').read_text(encoding='utf-8')

def replace(a,b):
    global source
    assert a in source,a
    source=source.replace(a,b)

replace("PREV=ROOT.parent/'monitoring_2026-10-26'", "PREV=ROOT.parent/'monitoring_2026-10-27'")
replace("DAY='2026-10-27'", "DAY='2026-10-28'")
replace("YESTERDAY='2026-10-26'", "YESTERDAY='2026-10-27'")
replace("len(rows)==370 and sum(c['summary']['new_since_last'] for c in configs)==4", "len(rows)==384 and sum(c['summary']['new_since_last'] for c in configs)==14")
replace("notes=['# 2026-10-27 三账号监测'", "notes=['# 2026-10-28 三账号监测'")
replace('本次370部，10月26日366部全部保留，新增4部。','本次384部，10月27日370部全部保留，新增14部。')
replace('昨天取10月26日实际快照。新增4部','昨天取10月27日实际快照。新增14部')
replace('使用10月26日、27日两次实际快照','使用10月27日、28日两次实际快照')
replace('366条旧初始值均保持不变。4部新增封面','370条旧初始值均保持不变。14部新增封面')
replace('old_ids_retained=366,new_ids=4','old_ids_retained=370,new_ids=14')
replace('immutable_initial_baselines=366','immutable_initial_baselines=370')
(ROOT/'prepare_reports.py').write_text(source,encoding='utf-8')

out=ROOT/'full_numbers'
out.mkdir(exist_ok=True)
builder=(PREV/'full_numbers'/'build_workbooks.mjs').read_text(encoding='utf-8')
builder=builder.replace('monitoring_2026-10-27','monitoring_2026-10-28')
builder=builder.replace('上次实际播放量（10月26日）','上次实际播放量（10月27日）')
builder=builder.replace('昨天取10月26日实际快照','昨天取10月27日实际快照')
(out/'build_workbooks.mjs').write_text(builder,encoding='utf-8')

verification=(PREV/'full_numbers'/'verify_and_package.py').read_text(encoding='utf-8')
verification=verification.replace('len(records)==370 and len(added)==4','len(records)==384 and len(added)==14')
verification=verification.replace('(4*160,145)','(7*160,2*145)').replace('(i%4)*160,(i//4)*145','(i%7)*160,(i//7)*145')
verification=verification.replace('新增4部剧封面核对.png','新增14部剧封面核对.png')
verification=verification.replace("==len(payload['initial'])==370", "==len(payload['initial'])==384")
verification=verification.replace("len(payload['history']['snapshots'])==18", "len(payload['history']['snapshots'])==21")
verification=verification.replace("sum(r['yesterday_views'] is not None for r in records)==366", "sum(r['yesterday_views'] is not None for r in records)==370")
verification=verification.replace("'monitoring_2026-10-26'", "'monitoring_2026-10-27'")
verification=verification.replace("date='2026-10-27',records=370,new_records=4,history_snapshots=18,unchanged_baselines=366", "date='2026-10-28',records=384,new_records=14,history_snapshots=21,unchanged_baselines=370")
(out/'verify_and_package.py').write_text(verification,encoding='utf-8')
print('Prepared 2026-10-28 pipeline: 384 dramas, 14 new, 370 prior baselines.')
