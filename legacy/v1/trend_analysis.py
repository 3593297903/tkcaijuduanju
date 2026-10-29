"""Compare observed time series by stable account/drama IDs; no imputed days."""
import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'trend_analysis'
OUT.mkdir(exist_ok=True)
DATA=json.loads((ROOT/'full_numbers'/'三个账号_初始昨天今天_完整数据.json').read_text(encoding='utf-8'))
H=json.loads((ROOT/'full_numbers'/'history_snapshots.json').read_text(encoding='utf-8'))
TZ=timezone(timedelta(hours=8))

def dt(t): return datetime.fromisoformat(t.replace('Z','+00:00'))
def bj(t): return dt(t).astimezone(TZ).strftime('%Y-%m-%d %H:%M:%S')
def day(t): return bj(t)[:10]
def hours(a,b): return (dt(b)-dt(a)).total_seconds()/3600
def change(a,b):
    h=hours(a['at'],b['at']); delta=b['views']-a['views']
    return dict(start=a['day'],end=b['day'],hours=h,before=a['views'],after=b['views'],
                delta=delta,rate=delta/a['views'] if a['views'] else None,v24=delta*24/h)

daily={}
for s in sorted(H['snapshots'],key=lambda s:dt(s['capturedAt'])):
    daily[s['account'],day(s['capturedAt'])]=s
days=sorted({d for _,d in daily})
assert days==['2026-10-22','2026-10-23','2026-10-24','2026-10-26','2026-10-27','2026-10-28']
meta={(r['account'],r['id']):r for r in DATA['records']}
series={k:[] for k in meta}
for (account,d),s in sorted(daily.items()):
    for r in s['records']:
        key=account,r['id']
        if key in series:series[key].append(dict(day=d,at=s['capturedAt'],views=r['views']))

records=[]
for key,r in meta.items():
    points=sorted(series[key],key=lambda p:p['day'])
    byday={p['day']:p for p in points}
    intervals=[change(byday[a],byday[b]) for a,b in zip(days,days[1:]) if a in byday and b in byday]
    latest=next((x for x in intervals if x['end']=='2026-10-28'),None)
    previous=next((x for x in intervals if x['end']=='2026-10-27'),None)
    recent=change(byday['2026-10-26'],byday['2026-10-28']) if '2026-10-26' in byday else None
    older=change(byday['2026-10-22'],byday['2026-10-26']) if all(d in byday for d in ['2026-10-22','2026-10-26']) else None
    earlier=[x for x in intervals if x['end']<'2026-10-28']
    rec=dict(account=r['account'],account_name=r['account_name'],id=r['id'],title=r['title'],
             title_zh=r['title_zh'],genre=r['genre'],description_zh=r['description_zh'],
             source=r['video_url'],thumbnail=r['thumbnail'],first_seen=points[0]['day'],
             latest_views=r['today_views'],points=points,intervals=intervals,latest=latest,previous=previous,
             recent=recent,older=older,window_change=change(points[0],points[-1]) if len(points)>1 else None,
             acceleration=latest['v24']/previous['v24'] if latest and previous and previous['v24']>0 else None,
             recent_vs_older=recent['v24']/older['v24'] if recent and older and older['v24']>0 else None,
             previous_peak_v24=max([x['v24'] for x in earlier],default=None))
    records.append(rec)

account_intervals=[]
three_day_cohort=[]
for account in sorted({r['account'] for r in records}):
    cohort=set.intersection(*[{r['id'] for r in daily[account,d]['records']} for d in days])
    for a,b in zip(days,days[1:]):
        s,t=daily[account,a],daily[account,b]
        before={r['id']:r['views'] for r in s['records']};after={r['id']:r['views'] for r in t['records']}
        common=set(before)&set(after)
        for population,ids in [('pair_common',common),('fixed_cohort',cohort)]:
            x=change(dict(day=a,at=s['capturedAt'],views=sum(before[i] for i in ids)),
                     dict(day=b,at=t['capturedAt'],views=sum(after[i] for i in ids)))
            account_intervals.append(dict(account=account,account_name=next(r['account_name'] for r in records if r['account']==account),
                population=population,count=len(ids),**x))
    recent_days=days[-3:]
    ids=set.intersection(*[{r['id'] for r in daily[account,d]['records']} for d in recent_days])
    changes=[]
    for a,b in zip(recent_days,recent_days[1:]):
        s,t=daily[account,a],daily[account,b]
        before={r['id']:r['views'] for r in s['records']};after={r['id']:r['views'] for r in t['records']}
        changes.append(change(dict(day=a,at=s['capturedAt'],views=sum(before[i] for i in ids)),
                              dict(day=b,at=t['capturedAt'],views=sum(after[i] for i in ids))))
    three_day_cohort.append(dict(account=account,count=len(ids),intervals=changes,
                                latest_speed_change=changes[1]['v24']/changes[0]['v24']-1))

ranked=sorted([r for r in records if r['latest']],key=lambda r:r['latest']['delta'],reverse=True)
out=dict(as_of='2026-10-28',days=days,records=records,account_intervals=account_intervals,three_day_cohort=three_day_cohort,
         rules=['同账号同剧目ID匹配','同日取最后实际快照','10月25日缺失，不补值',
                '所有主统计使用页面显示折算整数','24小时速度为区间新增按实际间隔折算，非实际完整24小时播放量',
                '新收录日期不等于发布日期','实际观测仅覆盖10月22日至28日，不构造半月增长'])
(OUT/'trend_analysis.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
headers=['账号','剧目ID','英文剧名','中文剧名','题材','首次收录日']+days
for a,b in zip(days,days[1:]):headers += [a+'→'+b+'增量',a+'→'+b+'涨幅',a+'→'+b+'折算24小时增量']
headers+=['最近速度相对上一轮倍数','近两轮速度相对更早倍数']
with (OUT/'384部剧_历史播放量与增长对比.csv').open('w',encoding='utf-8-sig',newline='') as f:
    w=csv.writer(f);w.writerow(headers)
    for r in records:
        pts={p['day']:p for p in r['points']};ints={(x['start'],x['end']):x for x in r['intervals']}
        row=[r['account_name'],r['id'],r['title'],r['title_zh'],r['genre'],r['first_seen']]+[pts.get(d,{}).get('views') for d in days]
        for a,b in zip(days,days[1:]):
            x=ints.get((a,b),{});row += [x.get('delta'),f"{x['rate']:.4%}" if x.get('rate') is not None else None,round(x['v24']) if x.get('v24') is not None else None]
        w.writerow(row+[r['acceleration'],r['recent_vs_older']])

def brief(r):
    return {k:r[k] for k in ['account_name','title','title_zh','first_seen','latest_views','latest','previous','acceleration','recent','older','recent_vs_older']}
print('ACCOUNTS',json.dumps([x for x in account_intervals if x['population']=='pair_common'],ensure_ascii=False))
print('TOP_LATEST',json.dumps([brief(r) for r in ranked[:22]],ensure_ascii=False))
print('TOP_RELATIVE',json.dumps([brief(r) for r in sorted([r for r in ranked if r['latest']['before']>=500000 and r['latest']['delta']>=200000],key=lambda r:r['latest']['rate'],reverse=True)[:15]],ensure_ascii=False))
print('TOP_WINDOW',json.dumps([brief(r) for r in sorted([r for r in records if r['first_seen']=='2026-10-22' and r['window_change']],key=lambda r:r['window_change']['delta'],reverse=True)[:12]],ensure_ascii=False))
assert len(records)==384 and sum(r['latest'] is not None for r in records)==370
assert sum(r['latest']['delta'] for r in records if r['latest'])==23655800
