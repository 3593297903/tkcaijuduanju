import csv
import hashlib
import json
import math
import re
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED
from PIL import Image, ImageDraw, ImageFont

OUT=Path(__file__).resolve().parent
ROOT=OUT.parent
configs=json.loads((OUT/'workbook_configs.json').read_text(encoding='utf-8'))
NS={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
checks=[]
for cfg in configs:
    rows=cfg['rows']
    filename=f"{cfg['prefix']}_{len(rows)}部剧_初始昨天今天_完整数字.xlsx"
    with ZipFile(OUT/filename) as z:
        sheet=ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
        cells={c.get('r'):c for c in sheet.findall('.//s:c',NS)}
        def value(address):
            cell=cells.get(address)
            return cell.findtext('s:v',namespaces=NS) if cell is not None else None
        def compare(address,v):
            actual=value(address)
            if v is None:assert actual in ('',None),(filename,address,actual)
            else:assert actual not in ('',None) and math.isclose(float(actual),v,abs_tol=1e-10),(filename,address,actual,v)
        for i,r in enumerate(rows,8):
            expected=[r['initial_views'],r['yesterday_views'],r['today_views'],r['day_delta'],r['day_rate'],
                r['yesterday_from_initial_delta'],r['yesterday_from_initial_rate'],r['today_from_initial_delta'],r['today_from_initial_rate']]
            for col,v in zip('EFGHIJKLM',expected):compare(f'{col}{i}',v)
            for col,key in [('Q','today_frontend_integer'),('AB','last_views'),('AC','last_delta'),('AD','last_rate')]:compare(f'{col}{i}',r[key])
            assert value(f'X{i}')==r['id'] and cells[f'X{i}'].get('t')=='str'
            assert value(f'Y{i}')==(str(r['video_id']) if r['video_id'] is not None else None) or (r['video_id'] is None and value(f'Y{i}') in ('',None))
            assert cells[f'E{i}'].get('t') not in ('str','s','inlineStr')
            assert (value(f'O{i}') in ('',None)) == (r['yesterday_views'] is None)
            assert value(f'N{i}') not in ('',None)
            for col in ['I','K','M','AC','AD']:assert cells[f'{col}{i}'].find('s:f',NS) is not None
        total=len(rows)+8
        for col,key in zip('EFGHIJKLM',['initial_total','yesterday_total','today_total','day_delta','day_rate',
                'yesterday_from_initial_delta','yesterday_from_initial_rate','today_from_initial_delta','today_from_initial_rate']):compare(f'{col}{total}',cfg['summary'][key])
        for col,key in [('AB','last_total'),('AC','last_delta'),('AD','last_rate')]:compare(f'{col}{total}',cfg['summary'][key])
        pane=sheet.find('.//s:pane',NS)
        assert pane.get('xSplit')=='3' and pane.get('ySplit')=='7'
        table_files=[n for n in z.namelist() if re.fullmatch(r'xl/tables/table\d+\.xml',n)]
        assert len(table_files)==1
        table=ET.fromstring(z.read(table_files[0]));assert table.get('ref')==f'A7:AE{len(rows)+7}'
        media=[n for n in z.namelist() if n.startswith('xl/media/')]
        assert len(media)==len(rows)
        assert sorted(hashlib.sha256(z.read(n)).hexdigest() for n in media)==sorted(hashlib.sha256(Path(r['image']).read_bytes()).hexdigest() for r in rows)
        assert not any(c.get('t')=='e' for c in cells.values()),'Saved workbook contains formula errors'
    with (OUT/(cfg['prefix']+'_初始昨天今天_完整数字.csv')).open(encoding='utf-8-sig',newline='') as f:exported=list(csv.reader(f))
    assert len(exported)-1==len(rows)
    for record,r in zip(exported[1:],rows):
        assert record[17]==r['id']
        if r['yesterday_views'] is None:assert all(record[c]=='' for c in [4,6,7,8,9,13])
        else:assert int(record[4])==r['yesterday_views'] and int(record[6])==r['day_delta']
        for col in [3,4,5,6,8,10,15,18,19]:assert record[col]=='' or re.fullmatch(r'-?\d+',record[col]),record[col]
    checks.append(dict(account=cfg['name'],records=len(rows),file=filename,full_numbers_verified=True,
        missing_day_preserved=True,period_comparison_verified=True,identifiers_preserved=True,
        images_verified=True,bytes=(OUT/filename).stat().st_size))

records=[r for c in configs for r in c['rows']]
added=[r for r in records if r['last_views'] is None]
assert len(records)==384 and len(added)==14
font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',14)
canvas=Image.new('RGB',(7*160,2*145),'white')
draw=ImageDraw.Draw(canvas)
for i,r in enumerate(added):
    x,y=(i%7)*160,(i//7)*145
    image=Image.open(OUT/r['thumbnail']).convert('RGB')
    assert image.size==(94,94)
    assert len(image.getcolors(maxcolors=10000) or [])>50,r['id']
    canvas.paste(image,(x+32,y+8))
    draw.text((x+8,y+108),r['account_name'][:16],fill='#203047',font=font)
    draw.text((x+8,y+125),r['id'][-9:],fill='#607086',font=font)
canvas.save(ROOT/'新增14部剧封面核对.png')

page=(OUT/'三个账号_初始昨天今天_完整数字.html').read_text(encoding='utf-8')
payload=json.loads(re.search(r'<script id="monitorData" type="application/json">(.*?)</script>',page,re.S)[1])
assert len(payload['rows'])==len(payload['images'])==len(payload['initial'])==384
assert len(payload['history']['snapshots'])==21
assert not any(s['capturedAt'].startswith('2026-10-25') for s in payload['history']['snapshots'])
assert sum(r['yesterday_views'] is not None for r in records)==370
assert all(r['yesterday_views']==r['last_views'] and r['day_delta']==r['last_delta'] and r['day_rate']==r['last_rate'] for r in records)
old=json.loads((ROOT.parent/'monitoring_2026-10-27'/'full_numbers'/'initial_baselines.json').read_text(encoding='utf-8'))
initial={(r['account'],r['id']):r for r in payload['initial']}
assert all(initial[(r['account'],r['id'])]==r for r in old['records'])

files=[OUT/c['file'] for c in checks]+sorted(OUT.glob('*_完整数字.csv'))+sorted(OUT.glob('*_完整数据.json'))
files += [OUT/n for n in ['history_snapshots.json','initial_baselines.json','统计口径.md','三个账号_初始昨天今天_完整数字.html']]
files += sorted((OUT/'thumbnails').glob('*/*.png'))
archive=OUT/'三个账号_初始昨天今天_完整数字.zip'
with ZipFile(archive,'w',ZIP_DEFLATED) as z:
    for f in files:z.write(f,f.relative_to(OUT).as_posix())
    for f in sorted(ROOT.glob('*_checkpoint.json'))+[ROOT/n for n in ['collection_manifest.json','new_drama_details.json','new_localizations.json','validation.json']]:z.write(f,'evidence/'+f.name)
with ZipFile(archive) as z:assert z.testzip() is None
result=dict(status='ok',date='2026-10-28',records=384,new_records=14,history_snapshots=21,unchanged_baselines=370,
    yesterday_missing=False,period_delta=sum(c['summary']['last_delta'] for c in configs),
    workbooks=checks,archive=archive.name,archive_bytes=archive.stat().st_size)
(OUT/'saved_file_verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
