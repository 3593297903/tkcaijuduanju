"""Full-digit CSV, merged JSON, compatibility config and offline HTML exports."""

import base64
import csv
import json
import mimetypes
from pathlib import Path

from .analysis import build_report
from .model import MonitorError

CSV_FIELDS = [
    "account", "account_name", "order", "id", "title", "title_zh", "metric", "metric_label",
    "initial_views", "yesterday_views", "today_views", "day_delta", "day_rate",
    "yesterday_from_initial_delta", "yesterday_from_initial_rate", "today_from_initial_delta", "today_from_initial_rate",
    "last_views", "last_delta", "last_rate", "last_elapsed_hours", "last_delta_per_24h", "last_rate_per_24h",
    "today_display_views", "today_frontend_integer", "first_precise_views", "first_precise_recorded_at",
    "initial_recorded_at", "yesterday_recorded_at", "today_recorded_at", "last_recorded_at",
    "initial_recorded_at_beijing", "yesterday_recorded_at_beijing", "recorded_at_beijing", "last_recorded_at_beijing",
    "episodes", "genre", "description_zh", "description_en", "metadata_status", "video_id", "video_url",
    "create_time", "time_type", "is_preview", "thumbnail", "source", "snapshot_id", "initial_snapshot_id",
    "last_snapshot_id", "precision_note", "metadata_note", "status",
]


def _json_file(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def export_report(store, config, report, output=None, embed_covers=True):
    output = Path(output) if output else config.report_dir / report["report_day"] / report["metric"]
    output.mkdir(parents=True, exist_ok=True)
    for account in config.accounts:
        rows = next((item["rows"] for item in report["accounts"] if item["account"] == account["handle"]), [])
        with (output / (account["prefix"] + ".csv")).open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, CSV_FIELDS, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({key: _csv_value(row.get(key)) for key in CSV_FIELDS})
    _json_file(output / "merged.json", report)
    _json_file(output / "workbook_configs.json", report["accounts"])
    # Date and metric controls operate entirely offline; absent calendar days remain absent.
    days = {}
    for day in report["available_days"]:
        days[day] = {}
        for metric in ("display", "frontend"):
            current = report if day == report["report_day"] and metric == report["metric"] else build_report(store, config, day, metric)
            days[day][metric] = {key: current[key] for key in ("report_day", "previous_day", "metric", "metric_label", "complete", "account_coverage_complete", "metric_coverage", "missing_accounts", "summary", "rows")}
    covers = {}
    if embed_covers:
        for day in days.values():
            for current in day.values():
                for row in current["rows"]:
                    path = row.get("image")
                    key = row["account"] + ":" + row["id"] + ":" + str(row.get("thumbnail"))
                    if path and key not in covers and Path(path).is_file():
                        mime = mimetypes.guess_type(path)[0] or "image/png"
                        covers[key] = "data:" + mime + ";base64," + base64.b64encode(Path(path).read_bytes()).decode("ascii")
    payload = {"selected_day": report["report_day"], "selected_metric": report["metric"],
               "accounts": [{"handle": item["handle"], "name": item["name"]} for item in config.accounts],
               "days": days, "covers": covers}
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    (output / "monitor.html").write_text(HTML.replace("__TKMONITOR_DATA__", serialized), encoding="utf-8")
    return output.resolve()


def _csv_value(value):
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    # Keep integer values untouched: neither abbreviation nor scientific notation is generated.
    return value


HTML = r'''<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Short Drama Monitor · 短剧监测</title>
<style>
:root{font:14px/1.5 system-ui,Segoe UI,sans-serif;color:#22344a;background:#eef2f6}body{margin:0}header{padding:24px 28px;background:#172b43;color:white}h1{margin:0;font-size:25px}header p{color:#c7d8eb;margin-bottom:0}.controls{display:flex;flex-wrap:wrap;gap:16px;padding:18px 28px;position:sticky;top:0;background:#fff;z-index:2;border-bottom:1px solid #cbd5df}label{display:flex;gap:6px;align-items:center}input,select{font:inherit;padding:7px;border:1px solid #aab8c8;border-radius:5px}input[type=search]{min-width:210px}#summary,#notice{padding:12px 28px}#summary{display:flex;flex-wrap:wrap;gap:25px;background:#e1e9f2}.stat b{display:block;font-size:22px}#notice:empty{display:none}#notice{background:#fff1d0}.scroll{overflow:auto;margin:20px;border:1px solid #ccd6e1;border-radius:8px;background:white}table{border-collapse:collapse;width:100%;min-width:2000px}th,td{white-space:pre-line;text-align:left;padding:10px 12px;border-bottom:1px solid #e0e7ef;vertical-align:top}th{background:#dfe8f2;white-space:nowrap}td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}td.title{min-width:260px;max-width:350px}.english{font-weight:650}.chinese{color:#41698e;margin-top:5px}.cover{width:94px;height:94px;object-fit:cover;border-radius:5px}.placeholder{width:94px;height:94px;background:#eef2f6;display:grid;place-items:center;color:#7e8fa2;font-size:12px}td.synopsis{min-width:230px;max-width:420px}details p{white-space:pre-wrap;min-width:230px}small{color:#61758d}footer{padding:0 28px 25px;color:#61758d}a{color:#286399}#empty{padding:30px}.negative{color:#b3473d}
</style>
<header><h1>Short Drama Monitor · 短剧监测</h1><p>独立指标序列 · 北京时间日历日 · 离线历史浏览</p></header>
<div class="controls"><label>统计日 <input id="day" type="date"></label><label>账号 <select id="account"><option value="">全部账号</option></select></label><label>统计口径 <select id="metric"><option value="display">页面显示折算整数</option><option value="frontend">前端整数（仅已知值）</option></select></label><label>搜索 <input id="search" type="search" placeholder="英文名、中文名、剧目ID"></label><label>排序 <select id="sort"><option value="order">账号／页面顺序</option><option value="today_views">今天播放量 ↓</option><option value="day_delta">今日增量 ↓</option><option value="day_rate">今日涨幅 ↓</option><option value="last_delta">较上次增量 ↓</option><option value="title">英文标题 A–Z</option></select></label></div>
<div id="notice" role="status"></div><div id="summary"></div><div class="scroll"><table><thead><tr><th>小封面</th><th>English title<br>中文剧名</th><th>账号／集数／题材</th><th>初始</th><th>昨天</th><th>今天</th><th>今天较昨天<br>增量／涨幅</th><th>今天较初始<br>增量／涨幅</th><th>上次实际<br>值／增量／涨幅</th><th>实际间隔<br>折合24小时增量</th><th>记录时间（北京）<br>初始／昨天／今天／上次</th><th>中文简介／来源</th><th>本次前端整数<br>首次精确基线</th><th>剧目ID／来源</th></tr></thead><tbody id="rows"></tbody></table><div id="empty" hidden>该日期或筛选条件没有有效快照；缺失值没有补0。</div></div>
<footer>— 表示未采集或不可比；0为真实零值。昨天固定为日历前一天，上次实际可以是同日更早快照。页面缩写折算值不可恢复被截断的低位，前端整数独立比较。对比合计仅含两端均有值的同批剧目。发布时间只展示原始来源字段，不根据ID或初见时间推算。CSV和JSON保留完整数字与UTC时间。</footer>
<script id="data" type="application/json">__TKMONITOR_DATA__</script>
<script>
'use strict';const data=JSON.parse(document.getElementById('data').textContent),$=id=>document.getElementById(id);$('day').value=data.selected_day;$('metric').value=data.selected_metric;
for(const a of data.accounts){const o=document.createElement('option');o.value=a.handle;o.textContent=a.name;$('account').append(o)}
const num=v=>v==null?'—':Number.isInteger(v)?v.toLocaleString('en-US',{maximumFractionDigits:0}):v.toLocaleString('en-US',{maximumFractionDigits:2}),pct=v=>v==null?'—':(v*100).toFixed(2)+'%',text=(tag,value,cls)=>{const e=document.createElement(tag);e.textContent=value??'—';if(cls)e.className=cls;return e},cell=(tr,value,cls)=>{const td=text('td',value,cls);tr.append(td);return td};
function totals(rows){const comparable=rows.filter(r=>r.today_views!=null&&r.yesterday_views!=null),known=rows.filter(r=>r.today_views!=null),sum=(a,k)=>a.length?a.reduce((s,r)=>s+r[k],0):null,delta=comparable.length?comparable.reduce((s,r)=>s+r.today_views-r.yesterday_views,0):null,base=sum(comparable,'yesterday_views');return{today:sum(known,'today_views'),known:known.length,delta,rate:base?delta/base:null,comp:comparable.length}}
function notice(current){if(!current)return'该日期没有有效采集，未使用前后日期代替。';const messages=[];if(current.missing_accounts.length)messages.push('缺少账号快照：'+current.missing_accounts.join('、'));const coverage=current.metric_coverage;if(coverage.status==='missing')messages.push('所选指标没有已保存的播放量；未以其他指标代替。');else if(coverage.status==='partial')messages.push('所选指标已知 '+coverage.known+' / '+coverage.total+' 条；缺失保持空白。');return messages.join(' ')}
function draw(){const current=data.days[$('day').value]?.[$('metric').value];$('notice').textContent=notice(current);const query=$('search').value.trim().toLocaleLowerCase();let rows=(current?.rows||[]).filter(r=>(!$('account').value||r.account===$('account').value)&&(!query||[r.title,r.title_zh,r.id].join(' ').toLocaleLowerCase().includes(query)));const sort=$('sort').value;rows.sort((a,b)=>sort==='title'?a.title.localeCompare(b.title):sort==='order'?a.account.localeCompare(b.account)||a.order-b.order:a[sort]==null?b[sort]==null?0:1:b[sort]==null?-1:b[sort]-a[sort]);const s=totals(rows);$('summary').replaceChildren();for(const [label,value]of[['剧目',rows.length],['今天（已知 '+s.known+' 条）',num(s.today)],['较昨天（可比 '+s.comp+' 条）',num(s.delta)],['较昨天涨幅',pct(s.rate)]]){const div=text('div',label,'stat');div.append(text('b',value));$('summary').append(div)}$('rows').replaceChildren();$('empty').hidden=rows.length>0;
for(const r of rows){const tr=document.createElement('tr');let td=cell(tr,'');const cover=data.covers[r.account+':'+r.id+':'+r.thumbnail];if(cover){const img=document.createElement('img');img.src=cover;img.className='cover';img.alt=r.title+' 封面';img.loading='lazy';td.append(img)}else td.append(text('div','封面待采集','placeholder'));td=cell(tr,'','title');td.append(text('div',r.title,'english'),text('div',r.title_zh,'chinese'));cell(tr,r.account_name+'\n'+(r.episodes??'—')+' 集\n'+r.genre);cell(tr,num(r.initial_views),'num');cell(tr,num(r.yesterday_views),'num');cell(tr,num(r.today_views),'num');cell(tr,num(r.day_delta)+'\n'+pct(r.day_rate),'num'+(r.day_delta<0?' negative':''));cell(tr,num(r.today_from_initial_delta)+'\n'+pct(r.today_from_initial_rate),'num');cell(tr,num(r.last_views)+'\n'+num(r.last_delta)+' / '+pct(r.last_rate),'num');cell(tr,num(r.last_elapsed_hours)+' 小时\n'+num(r.last_delta_per_24h),'num');cell(tr,[r.initial_recorded_at_beijing,r.yesterday_recorded_at_beijing,r.recorded_at_beijing,r.last_recorded_at_beijing].map(v=>v??'—').join('\n'));td=cell(tr,'','synopsis');const details=document.createElement('details');details.append(text('summary',r.metadata_status==='pending'?'资料待翻译／待补充':'展开中文简介'),text('p',r.description_zh),text('small',r.metadata_note||'资料来自保存的原始来源；未补写剧情。'));td.append(details);if(r.description_en){const english=document.createElement('details');english.append(text('summary','Original synopsis'),text('p',r.description_en));td.append(english)}cell(tr,num(r.today_frontend_integer)+'\n'+num(r.first_precise_views)+'\n'+(r.first_precise_recorded_at||'—'),'num');td=cell(tr,r.id+'\n'+r.source+'\n'+r.snapshot_id);if(r.video_url&&/^https:\/\/(www\.)?tiktok\.com\//.test(r.video_url)){const link=text('a','原始视频');link.href=r.video_url;link.target='_blank';link.rel='noreferrer';td.append(document.createElement('br'),link)}$('rows').append(tr)}}
for(const id of['day','account','metric','sort'])$(id).addEventListener('change',draw);$('search').addEventListener('input',draw);draw();
</script></html>'''
