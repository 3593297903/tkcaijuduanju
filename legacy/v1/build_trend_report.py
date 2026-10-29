import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'trend_analysis'
data=json.loads((OUT/'trend_analysis.json').read_text(encoding='utf-8'))
page=(ROOT/'trend_dashboard_template.html').read_text(encoding='utf-8').replace('__TREND_DATA__',json.dumps(data,ensure_ascii=False).replace('<','\\u003c'))
(OUT/'三账号_历史增长与近期爆款分析.html').write_text(page,encoding='utf-8')
rows=data['records']
bytitle={r['title_zh']:r for r in rows}
def num(n):return '—' if n is None else f'{n:,.0f}'
def pct(n):return '—' if n is None else f'{n:+.2%}'
top=sorted([r for r in rows if r['latest']],key=lambda r:r['latest']['delta'],reverse=True)
report=['# 三账号历史增长与近期爆款分析','',
'统计截止：2026年10月28日。依据21次原始快照，同日取最后记录，覆盖10月22、23、24、26、27、28日。',
'10月25日缺失，24→26日不可拆为单日。各轮间隔不同，判断加速时用实际小时数折算24小时新增。页面缩写折算值与精确前端整数不混用。',
'', '## 主要判断','',
'1. 当前新增最多、且持续走强：Baby Drama-ca《我哥是个超级恋爱脑》。最新新增2,800,000，前一轮2,500,000；近两轮平均速度为22→26日的3.10倍。',
'2. 近期新爆款最明确：VerveDrama《我驯服了七个反派继子女》。24日51,000→26日918,900→27日2,700,000→28日4,700,000。最近两轮都贡献大规模新增，折算24小时速度再提高约10%。',
'3. 持续热播但并非全程加速：Baby Drama-ca《听懂万物的我，选了个亿万富豪爸爸！》。22日873,200→28日12,000,000，最新新增1,800,000；速度较上一轮回升约10%，仍低于23→24日高点。',
'4. 近期突然翻热、但最新一轮放缓：Epic Creation《我的宠物石头变成了女儿》。22→26日仅新增300,000，26→28日新增3,200,000；最新新增从2,000,000降至1,200,000。',
'5. 《黑帮最小的老大》《丈夫把我当作巨蛇的猎物》仍有高新增，但最近速度分别比上一轮回落约28%、23%。',
'', '## 最新新增前15','',
'|账号|中文剧名|26→27新增|27→28新增|27→28涨幅|最新速度相对上一轮|28日播放量|',
'|---|---|---:|---:|---:|---:|---:|']
for r in top[:15]:
    report.append('|'+ '|'.join([r['account_name'],r['title_zh'],num(r['previous']['delta']) if r['previous'] else '—',num(r['latest']['delta']),pct(r['latest']['rate']),f"{r['acceleration']:.2f}倍" if r['acceleration'] is not None else '—',num(r['latest_views'])])+'|')
report+=['','## 早期起量观察','',
'这些剧的新增已有一定规模，但历史短或基数较低，暂不与每日新增超过1,000,000的成熟爆款等同。','',
'|账号|中文剧名|27日播放量|28日播放量|最新新增|涨幅|上一轮新增|','|---|---|---:|---:|---:|---:|---:|']
for title in ['庄园里的小小继承人','他不知道的三胞胎','克洛伊的完美家庭','我带着大鹅住进爸爸的豪宅']:
    r=bytitle[title];x=r['latest']
    report.append('|'+ '|'.join([r['account_name'],title,num(x['before']),num(x['after']),num(x['delta']),pct(x['rate']),num(r['previous']['delta'])])+'|')
report+=['','## 固定同批剧目的账号速度','',
'仅使用26、27、28日三次都有记录的剧目，避免新增剧造成比较对象变化。','',
'|账号|可比剧目|26→27折算24小时新增|27→28折算24小时新增|速度变化|','|---|---:|---:|---:|---:|']
for c in data['three_day_cohort']:
    name=next(r['account_name'] for r in rows if r['account']==c['account'])
    report.append(f"|{name}|{c['count']}|{num(c['intervals'][0]['v24'])}|{num(c['intervals'][1]['v24'])}|{pct(c['latest_speed_change'])}|")
verve=[r for r in rows if r['account']=='vervedramaeg' and r['latest']]
verve_net=sum(r['latest']['delta'] for r in verve)
later_net=sum(r['latest']['delta'] for r in verve if r['first_seen']>='2026-10-24')
report+=['',f'Verve在24日或以后首次收录的作品贡献最新净增{num(later_net)}，占该账号本轮净增的{later_net/verve_net:.2%}。这是监测收录时间，不能直接当作上线时间。',
'','## 历史大爆款与近期势头分开看','',
'- 《萌宝来敲门：总裁爸爸，你是我的》当前243,200,000，最新新增700,000、涨幅0.29%。存量巨大，当前增速属于较稳定的长尾。',
'- 《亿万富豪老板的宝宝能和我说话》当前61,300,000，最新仍新增1,500,000，但近两轮平均速度约为22→26日的一半。仍热，势头已较早期放慢。',
'- 《城堡里的动物知音小女孩》当前11,400,000，最新显示无新增；近两轮平均速度约为22→26日的10%，不应按总播放量判定为当前爆发。',
'- 《众神家的第五个宝宝，竟是小龙女！》最新显示回落400,000，保留原值，不能据此认定真实观看行为为负数，也不列为近期走强。',
'','## 内容侧观察','',
'正在大规模增长的剧覆盖了多个方向：假恋爱与隐藏豪门、带异能的继母保护孩子、萌娃通灵与收养、石头化人成女儿、黑帮父女反差、婚姻谋害与巨蟒求生。不能据这些数据把近期热门全部概括为同一种小女孩认亲故事。',
'以下为剧情归纳。播放数据只能说明这些作品的表现，无法单独证明某一设定造成了增长。','']
for r in top[:7]:report.append(f"- **{r['title_zh']}**（{r['title']}，{r['account_name']}）：{r['genre']}。{r['description_zh']}")
report+=['','## 完整清单','',
'配套CSV保留384部剧六次日期的播放量、五个实际区间的新增、涨幅、折算24小时速度。14部只有28日记录的剧不计算历史涨幅。',
'配套HTML可筛选账号、搜索剧名、查看各次播放量或各区间新增，并勾选最多6部对照历史曲线。','']
(OUT/'三账号_历史增长与近期爆款分析.md').write_text('\n'.join(report),encoding='utf-8')
print(json.dumps(dict(records=len(rows),comparable=sum(r['latest'] is not None for r in rows),
                     new_without_history=sum(r['latest'] is None for r in rows),verve_recently_observed_share=later_net/verve_net),ensure_ascii=False))
