"""Prepare ID-matched daily and last-observation reports from Chrome checkpoints."""
import base64
import csv
import json
import re
import shutil
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT=Path(__file__).resolve().parent
PREV=ROOT.parent/'monitoring_2026-10-27'
OUT=ROOT/'full_numbers'
OUT.mkdir(exist_ok=True)
TZ=timezone(timedelta(hours=8))
DAY='2026-10-28'
YESTERDAY='2026-10-27'

def read(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def save(p,obj):Path(p).write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')
def moment(t):return datetime.fromisoformat(t.replace('Z','+00:00'))
def bj(t):return moment(t).astimezone(TZ).strftime('%Y-%m-%d %H:%M:%S') if t else None
def day(t):return moment(t).astimezone(TZ).date().isoformat()
def difference(a,b):return None if a is None or b is None else b-a
def rate(a,b):return None if a in (None,0) or b is None else (b-a)/a
def sum_known(rows,key):
    v=[r[key] for r in rows if r[key] is not None]
    return sum(v) if v else None

def main():
    oldcfgs=read(PREV/'workbook_configs.json')
    history=read(PREV/'history_snapshots.json')
    old_history=json.loads(json.dumps(history))
    prior_initial=read(PREV/'full_numbers'/'initial_baselines.json')
    initial={(r['account'],r['id']):r for r in prior_initial['records']}
    details={r['id']:r for r in read(ROOT/'new_drama_details.json')}
    zh=read(ROOT/'new_localizations.json')
    configs=[]
    manifests=[]
    for cfg in oldcfgs:
        account=cfg['records'][0]['account']
        checkpoint=read(ROOT/(account+'_checkpoint.json'))
        run,cards=checkpoint['run'],checkpoint['cards']
        assert run['pages'][-1]['response']['hasMore'] is False
        assert len(cards)==int(run['pages'][-1]['response']['cursor'])
        assert len({r['id'] for r in cards})==len(cards)
        old={r['id']:r for r in cfg['records']}
        assert not (set(old)-{r['id'] for r in cards}),('Prior drama missing',account)
        api={}
        for p in run['pages']:
            assert p['response'].get('status_code',0)==0
            api.update({d['dramaID']:d for d in p['response'].get('dramaList',[])})
        captured=run['last_read_at']
        assert day(captured)==DAY
        existing=[s for s in history['snapshots'] if s['account']==account and day(s['capturedAt'])<DAY]
        last=max(existing,key=lambda s:moment(s['capturedAt']))
        last_rows={r['id']:r for r in last['records']}
        yesterday_snapshots=[s for s in existing if day(s['capturedAt'])==YESTERDAY]
        yesterday=max(yesterday_snapshots,key=lambda s:moment(s['capturedAt'])) if yesterday_snapshots else None
        yesterday_rows={r['id']:r for r in yesterday['records']} if yesterday else {}
        rows=[]
        for c in cards:
            identity=c['id']
            assert re.fullmatch(r'\d{19}',identity)
            raw,episodes_text=[s.strip() for s in c['text'].splitlines() if s.strip()][-2:]
            m=re.fullmatch(r'([\d,.]+)([KMB]?)',raw)
            assert m
            value=int(Decimal(m[1].replace(',',''))*{'':1,'K':1000,'M':1000000,'B':1000000000}[m[2]])
            em=re.fullmatch(r'(\d+) Episodes',episodes_text)
            assert em
            oldrow=old.get(identity)
            r=dict(oldrow) if oldrow else {}
            image=ROOT/'thumbnails'/account/(identity+'.png')
            image.parent.mkdir(parents=True,exist_ok=True)
            if oldrow:shutil.copyfile(PREV/oldrow['thumbnail'],image)
            else:
                assert identity in zh and identity in details
                detail=details[identity]
                assert detail['title']==c['title']
                r.update(title_zh=zh[identity][0],genre=zh[identity][1],description_zh=zh[identity][2],
                    description_en=detail['description'],platform_themes=detail['tags'],
                    video_url=detail['url'],video_id=re.search(r'/video/(\d+)',detail['url'])[1],
                    create_time=None,time_beijing=None,time_type='未采集',is_preview=None,
                    metadata_note='中文名为阅读译名；题材和简介按本次详情页内容归纳；发布时间未推算。')
            assert image.is_file()
            target=OUT/image.relative_to(ROOT)
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(image,target)
            first=initial.setdefault((account,identity),dict(account=account,id=identity,title=c['title'],views=value,
                viewsRaw=raw,capturedAt=captured,snapshotId=account+':'+captured,metric='page_display_equivalent_integer'))
            yesterday_value=yesterday_rows.get(identity,{}).get('views')
            last_value=last_rows.get(identity,{}).get('views')
            frontend=int(api[identity]['numWatched']) if identity in api else None
            r.update(account=account,account_name=cfg['name'],order=c['order'],id=identity,title=c['title'],
                views_display=raw,views_approx=value,display=raw,approx=value,episodes=int(em[1]),
                recorded_at=captured,recorded_at_beijing=bj(captured),frontend=frontend,views_frontend=frontend,
                today_frontend_integer=frontend,initial_views=first['views'],initial_recorded_at=first['capturedAt'],
                initial_recorded_at_beijing=bj(first['capturedAt']),yesterday_views=yesterday_value,today_views=value,
                yesterday_recorded_at=yesterday['capturedAt'] if identity in yesterday_rows else None,
                today_recorded_at=captured,day_delta=difference(yesterday_value,value),day_rate=rate(yesterday_value,value),
                yesterday_from_initial_delta=difference(first['views'],yesterday_value),
                yesterday_from_initial_rate=rate(first['views'],yesterday_value),
                today_from_initial_delta=difference(first['views'],value),today_from_initial_rate=rate(first['views'],value),
                last_views=last_value,last_recorded_at=last['capturedAt'] if last_value is not None else None,
                last_delta=difference(last_value,value),last_rate=rate(last_value,value),
                previous_views_approx=last_value,previous_views_display=last_rows.get(identity,{}).get('viewsRaw'),
                previous_recorded_at=last['capturedAt'] if last_value is not None else None,
                before=last_value,delta=difference(last_value,value),rate=rate(last_value,value),
                status='新增' if last_value is None else '上升' if value>last_value else '下降' if value<last_value else '持平',
                thumbnail=image.relative_to(ROOT).as_posix(),image=str(target),thumbnail_url=c.get('thumbnail_url'),
                precision_note='页面显示折算整数；本次已采前端整数另列',description=r['description_zh'],tags=r['genre'])
            rows.append(r)
        common=[r for r in rows if r['last_views'] is not None]
        s=dict(account=account,name=cfg['name'],count=len(rows),initial_count=len(rows),
            yesterday_count=len(yesterday_rows),today_count=len(rows),previous_count=len(last_rows),
            initial_total=sum(r['initial_views'] for r in rows),yesterday_total=sum_known(rows,'yesterday_views'),
            today_total=sum(r['today_views'] for r in rows),day_delta=sum_known(rows,'day_delta'),day_rate=None,
            yesterday_from_initial_delta=sum_known(rows,'yesterday_from_initial_delta'),yesterday_from_initial_rate=None,
            today_from_initial_delta=sum(r['today_from_initial_delta'] for r in rows),
            new_since_last=sum(r['last_views'] is None for r in rows),new_today=sum(day(r['initial_recorded_at'])==DAY for r in rows),
            frontend_integer_count=sum(r['frontend'] is not None for r in rows),
            last_total=sum(r['last_views'] for r in common),last_delta=sum(r['last_delta'] for r in common),
            last_recorded_at=last['capturedAt'],captured_at=captured,captured_at_beijing=bj(captured),
            collection_started_at=run['start_at'],negative_changes=sum(r['last_delta']<0 for r in common),
            new_drama_views_total=sum(r['today_views'] for r in rows if r['last_views'] is None))
        daily_common=[r for r in rows if r['yesterday_views'] is not None]
        daily_base=sum(r['yesterday_views'] for r in daily_common)
        initial_yesterday_base=sum(r['initial_views'] for r in daily_common)
        s['day_rate']=s['day_delta']/daily_base if daily_base and s['day_delta'] is not None else None
        s['yesterday_from_initial_rate']=s['yesterday_from_initial_delta']/initial_yesterday_base if initial_yesterday_base and s['yesterday_from_initial_delta'] is not None else None
        s['last_rate']=s['last_delta']/s['last_total'] if s['last_total'] else None
        s['today_from_initial_rate']=s['today_from_initial_delta']/s['initial_total'] if s['initial_total'] else None
        assert s['today_total']==s['last_total']+s['last_delta']+s['new_drama_views_total']
        history['snapshots'].append(dict(id=account+':'+captured,account=account,capturedAt=captured,
            collectionStartedAt=run['start_at'],refreshed=True,
            source='用户 Chrome 内新开同账号标签页刷新后自然加载 Short dramas 到末页；时间为采集完成时间。',
            records=[dict(id=r['id'],title=r['title'],views=r['today_views'],viewsRaw=r['views_display'],
                          frontendInteger=r['frontend']) for r in rows]))
        prepared=dict(cfg,report_day=DAY,previous_day=YESTERDAY,afterTime=captured,beforeTime=last['capturedAt'],
            last_day=day(last['capturedAt']),summary=s,rows=rows,records=rows)
        configs.append(prepared)
        manifests.append(dict(account=account,count=len(rows),complete=True,refreshed=True,
            final_cursor=run['pages'][-1]['response']['cursor'],hasMore=False,captured_at=captured,started_at=run['start_at']))
        save(OUT/(cfg['prefix']+'_初始昨天今天_完整数据.json'),dict(account=account,name=cfg['name'],report_day=DAY,
            previous_day=YESTERDAY,last_day=day(last['capturedAt']),summary=s,records=rows))
        with (OUT/(cfg['prefix']+'_初始昨天今天_完整数字.csv')).open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.writer(f)
            w.writerow(['序号','英文名','中文名','初始播放量',YESTERDAY+'播放量',DAY+'播放量','日增量','日涨幅',
                '昨天较初始增量','昨天较初始涨幅','今天较初始增量','今天较初始涨幅','首次时间','昨天记录时间','今天记录时间',
                '本次已采前端整数','口径','剧目ID','上次实际播放量','较上次增加','较上次涨幅','上次记录时间'])
            for r in rows:
                pct=lambda v:'' if v is None else f'{v:.8%}'
                w.writerow([r['order'],r['title'],r['title_zh'],r['initial_views'],r['yesterday_views'],r['today_views'],
                    r['day_delta'],pct(r['day_rate']),r['yesterday_from_initial_delta'],pct(r['yesterday_from_initial_rate']),
                    r['today_from_initial_delta'],pct(r['today_from_initial_rate']),bj(r['initial_recorded_at']),
                    bj(r['yesterday_recorded_at']),bj(r['today_recorded_at']),r['frontend'],r['precision_note'],r['id'],
                    r['last_views'],r['last_delta'],pct(r['last_rate']),bj(r['last_recorded_at'])])
    for snap in old_history['snapshots']:assert snap==next(s for s in history['snapshots'] if s['id']==snap['id'])
    for r in prior_initial['records']:assert initial[(r['account'],r['id'])]==r
    assert len({s['id'] for s in history['snapshots']})==len(history['snapshots'])
    assert len([s for s in history['snapshots'] if day(s['capturedAt'])==YESTERDAY])==3
    rows=[r for c in configs for r in c['rows']]
    assert len(rows)==384 and sum(c['summary']['new_since_last'] for c in configs)==14
    assert all(r['day_delta']==r['last_delta'] and r['day_rate']==r['last_rate'] for r in rows)
    initial_doc=dict(prior_initial,records=list(initial.values()))
    combined=dict(schema='short-drama-monitor/v3',report_day=DAY,previous_day=YESTERDAY,timezone='Asia/Shanghai',
        initial_rule='按每部剧首次实际保存值固定',daily_rule='北京时间当日最后实际快照；缺失不补值',
        last_rule='同账号所选日前最近一次实际快照；跨日增量单独展示',
        summary=[c['summary'] for c in configs],records=rows)
    for folder in [ROOT,OUT]:
        save(folder/'history_snapshots.json',history)
        save(folder/'workbook_configs.json',configs)
        save(folder/'三个账号_初始昨天今天_完整数据.json',combined)
        save(folder/'initial_baselines.json',initial_doc)
    save(ROOT/'collection_manifest.json',dict(date=DAY,sources=manifests))
    payload=dict(rows=rows,history=history,initial=list(initial.values()),
        images={r['account']+':'+r['id']:'data:image/png;base64,'+base64.b64encode(Path(r['image']).read_bytes()).decode() for r in rows},reportDay=DAY)
    template=(PREV/'initial_daily_template.html').read_text(encoding='utf-8')
    if '<th>上次实际播放量</th>' not in template:
        # Retain calendar-day fields and add a clearly separate last-observation comparison.
        template=template.replace('<option value="cumulative">','<option value="lastDelta">较上次增加量降序</option><option value="lastRate">较上次涨幅降序</option><option value="cumulative">')
        template=template.replace('<div>今天较初始增加', '<div>较上次实际采集增加<b id="lastDeltaTotal">—</b><small id="lastRateTotal"></small></div><div>今天较初始增加')
        template=template.replace('<th>本次已采前端整数</th>', '<th>本次已采前端整数</th><th>上次实际播放量</th><th>较上次增加</th><th>较上次涨幅</th><th>上次实际采集时间</th>')
        template=template.replace('return{...meta,initial:base,', "const last=snapshots.filter(s=>s.account===meta.account&&day(s.capturedAt)<selected).at(-1),lastRecord=last?.records.find(r=>r.id===meta.id),lastValue=lastRecord?.views??null;return{...meta,lastViews:lastValue,lastDelta:difference(lastValue,after),lastRate:rate(lastValue,after),lastAt:lastRecord?last.capturedAt:null,initial:base,")
        template=template.replace("cell(tr,number(r.frontend),'num');body.append(tr);", "cell(tr,number(r.frontend),'num');cell(tr,number(r.lastViews),'num');cell(tr,signed(r.lastDelta),'num');cell(tr,percent(r.lastRate),'num');cell(tr,time(r.lastAt),'time');body.append(tr);")
        template=template.replace('td.colSpan=17','td.colSpan=21')
        template=template.replace("$('status').textContent=", "const lastCommon=visible.filter(r=>r.lastDelta!==null),lastBase=sumKnown(lastCommon,'lastViews'),lastChange=sumKnown(lastCommon,'lastDelta');$('lastDeltaTotal').textContent=signed(lastChange);$('lastRateTotal').textContent=lastCommon.length+'部可比，涨跌幅 '+percent(lastBase?lastChange/lastBase:null);$('status').textContent=")
        template=template.replace("'前一日记录时间','所选日记录时间','本次已采前端整数'", "'前一日记录时间','所选日记录时间','本次已采前端整数','上次实际播放量','较上次增加','较上次涨幅','上次实际采集时间'")
        template=template.replace("time(r.todayAt),r.frontend]);", "time(r.todayAt),r.frontend,r.lastViews,r.lastDelta,r.lastRate===null?'':(r.lastRate*100).toFixed(8)+'%',time(r.lastAt)]);")
        template=template.replace('所有增量和涨跌幅使用相应同批剧目计算。','所有增量和涨跌幅使用相应同批剧目计算。上次实际采集单独比较，跨日增量不冒充单日增长。')
    (ROOT/'initial_daily_template.html').write_text(template,encoding='utf-8')
    rendered=template.replace('__MONITOR_DATA__',json.dumps(payload,ensure_ascii=False).replace('<','\\u003c'))
    (OUT/'三个账号_初始昨天今天_完整数字.html').write_text(rendered,encoding='utf-8')
    notes=['# 2026-10-28 三账号监测','',
      '使用用户 Chrome 新开同账号页面刷新后逐批自然加载，均读到 hasMore=false。',
      '本次384部，10月27日370部全部保留，新增14部。10月25日历史缺口保持原样。',
      '初始值固定为各剧首次保存值；昨天取10月27日实际快照。新增14部昨天留空，不按0计算。',
      '今天较昨天的增量与涨幅，使用10月27日、28日两次实际快照的同批剧目。上次实际采集对比本次与昨日对比一致。',
      '主统计为页面显示值折算整数，低位取整信息无法恢复；本次自然分页中的前端整数另列，不与旧折算值混算。',
      '原始历史快照、370条旧初始值均保持不变。14部新增封面为页面小封面，中文名为阅读译名，简介按本次详情页内容归纳。',
      '历史关联视频时间沿用资料，新增作品未推算发布时间，关联视频日期不保证等于整剧上线日期。','',
      '|账号|剧目数|新增|本次播放量合计|上次至本次同剧增加|涨幅|完成时间（北京时间）|',
      '|---|---:|---:|---:|---:|---:|---|']
    for c in configs:
        s=c['summary']
        notes.append(f"|{c['name']}|{s['count']}|{s['new_since_last']}|{s['today_total']:,}|{s['last_delta']:+,}|{s['last_rate']:+.2%}|{s['captured_at_beijing']}|")
    notes+=['','## 页面显示下降的记录','']
    for r in rows:
        if r['last_delta'] is not None and r['last_delta']<0:
            notes.append(f"- {r['account_name']}：{r['title']}，{r['last_views']:,} → {r['today_views']:,}（{r['last_delta']:+,}）。按实际显示保留。")
    for folder in [ROOT,OUT]:(folder/'统计口径.md').write_text('\n'.join(notes)+'\n',encoding='utf-8')
    save(ROOT/'validation.json',dict(status='ok',records=len(rows),old_ids_retained=370,new_ids=14,
        immutable_initial_baselines=370,history_snapshots=len(history['snapshots']),yesterday_missing=False,
        exact_frontend_count=sum(r['frontend'] is not None for r in rows)))
    print(json.dumps(dict(status='ok',summary=combined['summary']),ensure_ascii=False))

if __name__=='__main__':main()
