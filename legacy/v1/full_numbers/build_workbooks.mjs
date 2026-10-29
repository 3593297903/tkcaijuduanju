import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {Workbook, SpreadsheetFile} from '@oai/artifact-tool';

const out='E:/jianc/outputs/monitoring_2026-10-28/full_numbers';
const configs=JSON.parse(await fs.readFile(path.join(out,'workbook_configs.json'),'utf8'));
const dateValue=s=>s==null?null:Date.parse(s)/86400000+25569+8/24;
const headers=['序号','页面小封面','中文剧名','英文剧名','初始播放量（次）','昨天播放量（次）','今天播放量（次）',
  '今天较昨天增加（次）','今天较昨天涨跌幅','昨天较初始增加（次）','昨天较初始涨跌幅','今天较初始增加（次）','今天较初始涨跌幅',
  '首次记录时间（北京时间）','昨天记录时间（北京时间）','今天记录时间（北京时间）','本次已采前端整数','主统计口径','集数',
  '关联视频时间（北京时间）','预告标记','中文简介','题材归纳','剧目ID','关联视频ID','来源视频链接','缩略图文件','上次实际播放量（10月27日）','较上次增加（次）','较上次涨跌幅','上次实际采集时间'];
const widths=[52,110,245,380,170,170,170,175,155,175,155,175,155,200,200,200,180,185,70,200,90,550,245,220,220,420,310,185,185,155,200];
const column=n=>{let s='';for(n++;n>0;n=Math.floor((n-1)/26))s=String.fromCharCode(65+(n-1)%26)+s;return s;};
const close=(a,b)=>assert(Math.abs(a-b)<1e-10,`${a} != ${b}`);
const blank=v=>v==null||v==='';
const manifests=[];
for(const cfg of configs){
  console.log('Starting '+cfg.name);
  const rows=cfg.rows,start=8,end=start+rows.length-1,total=end+1;
  const wb=Workbook.create(),sheet=wb.worksheets.add('剧目统计');
  sheet.showGridLines=false;sheet.tabColor='#243449';
  sheet.getRange(`A1:AE${total}`).format={font:{name:'Arial',size:10,color:'#203047'},verticalAlignment:'center',rowHeight:28};
  sheet.getRange('A1:AE1').format.rowHeight=10;
  sheet.getRange('A2').values=[[`${cfg.name} ${rows.length}部剧：初始、昨天、今天播放量监测`]];
  sheet.getRange('A2').format.font={name:'Arial',size:14,bold:true,color:'#203047'};
  sheet.getRange('A2:AE2').format.borders={bottom:{style:'thin',color:'#B8C5D2'}};
  sheet.getRange('A3').values=[[`来源：${cfg.source}（Short dramas）。统计日：${cfg.report_day}；昨天：${cfg.previous_day}。今天记录时间：${rows[0].recorded_at_beijing}。`]];
  sheet.getRange('A4').values=[['播放量与增量显示完整数字，单位：次。E至M统一采用页面显示折算值；历史缩写未保存的低位无法恢复，非精确计数。本次已取得的前端整数单列Q。']];
  sheet.getRange('A5').values=[['初始按每部剧首次记录固定，每日取最后快照；新增剧昨天留空。比较使用相应同批分母。昨天取10月27日实际快照。新增剧昨天留空，未按0计算。AB至AE保留上次采集对比。新增剧简介来自本次详情页，未推算发布日期。']];
  sheet.getRange('A3:AE5').format.font={name:'Arial',size:10,color:'#607086'};
  sheet.getRange('A6:AE6').format.rowHeight=10;
  sheet.getRange('A7:AE7').values=[headers];
  sheet.getRange(`X${start}:Y${end}`).setNumberFormat('@');
  const values=rows.map(r=>[r.order,null,r.title_zh,r.title,r.initial_views,r.yesterday_views,r.today_views,
    null,null,null,null,null,null,dateValue(r.initial_recorded_at),dateValue(r.yesterday_recorded_at),dateValue(r.today_recorded_at),
    r.today_frontend_integer,'页面显示折算整数',r.episodes,r.create_time==null?null:r.create_time/86400+25569+8/24,
    r.is_preview==null?'未采集':r.is_preview?'是':'否',r.description_zh,r.genre,String(r.id),r.video_id==null?'':String(r.video_id),r.video_url,r.thumbnail,r.last_views,null,null,dateValue(r.last_recorded_at)]);
  sheet.getRange(`A${start}:AE${end}`).values=values;
  sheet.getRange(`H${start}:M${start}`).formulas=[[
    `=IF(AND(ISNUMBER(F${start}),ISNUMBER(G${start})),G${start}-F${start},"")`,
    `=IF(AND(ISNUMBER(F${start}),ISNUMBER(G${start})),IF(F${start}=0,"",H${start}/F${start}),"")`,
    `=IF(AND(ISNUMBER(E${start}),ISNUMBER(F${start})),F${start}-E${start},"")`,
    `=IF(AND(ISNUMBER(E${start}),ISNUMBER(F${start})),IF(E${start}=0,"",J${start}/E${start}),"")`,
    `=IF(AND(ISNUMBER(E${start}),ISNUMBER(G${start})),G${start}-E${start},"")`,
    `=IF(AND(ISNUMBER(E${start}),ISNUMBER(G${start})),IF(E${start}=0,"",L${start}/E${start}),"")`]];
  sheet.getRange(`H${start}:M${end}`).fillDown();
  sheet.getRange(`AC${start}:AD${start}`).formulas=[[
    `=IF(AND(ISNUMBER(AB${start}),ISNUMBER(G${start})),G${start}-AB${start},"")`,
    `=IF(AND(ISNUMBER(AB${start}),ISNUMBER(G${start})),IF(AB${start}=0,"",AC${start}/AB${start}),"")`]];
  sheet.getRange(`AC${start}:AD${end}`).fillDown();
  sheet.getRange(`AB${start}:AC${total}`).setNumberFormat('#,##0');
  sheet.getRange(`AD${start}:AD${total}`).setNumberFormat('0.00%');
  sheet.getRange(`AE${start}:AE${end}`).setNumberFormat('yyyy-mm-dd hh:mm:ss');
  sheet.getRange(`AB${start}:AE${total}`).format.horizontalAlignment='right';
  sheet.getRange(`E${start}:M${total}`).setNumberFormat('#,##0');
  for(const col of ['I','K','M'])sheet.getRange(`${col}${start}:${col}${total}`).setNumberFormat('0.00%');
  sheet.getRange(`N${start}:P${end}`).setNumberFormat('yyyy-mm-dd hh:mm:ss');
  sheet.getRange(`Q${start}:Q${end}`).setNumberFormat('#,##0');
  sheet.getRange(`S${start}:S${end}`).setNumberFormat('#,##0');
  sheet.getRange(`T${start}:T${end}`).setNumberFormat('yyyy-mm-dd hh:mm:ss');
  sheet.getRange(`C${start}:D${end}`).format.wrapText=true;
  sheet.getRange(`V${start}:W${end}`).format.wrapText=true;
  sheet.getRange(`A${start}:AE${end}`).format.rowHeightPx=106;
  sheet.getRange(`E${start}:Q${total}`).format.horizontalAlignment='right';
  sheet.getRange(`C${start}:C${end}`).format.font={name:'Arial',size:10,bold:true,color:'#355D89'};
  widths.forEach((width,i)=>sheet.getRange(`${column(i)}1:${column(i)}${total}`).format.columnWidthPx=width);
  const table=sheet.tables.add(`A7:AE${end}`,true,`${cfg.prefix}Monitoring`);table.style='TableStyleMedium2';table.showFilterButton=true;
  sheet.getRange('A7:AE7').format={fill:'#243449',font:{name:'Arial',size:10,bold:true,color:'#FFFFFF'},horizontalAlignment:'center',verticalAlignment:'center',wrapText:true,rowHeight:42};
  sheet.freezePanes.freezeRows(7);sheet.freezePanes.freezeColumns(3);
  for(let i=0;i<rows.length;i++)sheet.images.add({dataUrl:'data:image/png;base64,'+(await fs.readFile(rows[i].image)).toString('base64'),anchor:{from:{row:start-1+i,col:1,rowOffsetPx:6,colOffsetPx:8},extent:{widthPx:94,heightPx:94}}});
  sheet.getRange(`C${total}`).values=[['合计（初始含各剧首次；各对比只含可比剧）']];
  for(const col of ['E','F','G','H','J','L','AB','AC'])sheet.getRange(`${col}${total}`).formulas=[[`=IF(COUNT(${col}${start}:${col}${end})=0,"",SUM(${col}${start}:${col}${end}))`]];
  sheet.getRange(`AD${total}`).formulas=[[`=IF(AB${total}=0,"",AC${total}/AB${total})`]];
  for(const [col,numerator,base,required] of [['I','H','F','G'],['K','J','E','F'],['M','L','E','G']]){
    const denominator=`SUMIFS(${base}${start}:${base}${end},${required}${start}:${required}${end},"<>")`;
    sheet.getRange(`${col}${total}`).formulas=[[`=IF(${denominator}=0,"",${numerator}${total}/${denominator})`]];
  }
  sheet.getRange(`A${total}:AE${total}`).format.borders={top:{style:'thin',color:'#B8C5D2'}};
  const knownIndex=rows.findIndex(r=>r.last_views!=null),knownRow=start+knownIndex,known=rows[knownIndex];
  // Exercise formula updates and distinguish a missing day from a genuine zero.
  sheet.getRange(`F${knownRow}`).values=[[1000]];
  sheet.getRange(`G${knownRow}`).values=[[known.today_views+100]];
  assert.equal(sheet.getRange(`H${knownRow}`).values[0][0],known.today_views+100-1000);
  assert.equal(sheet.getRange(`L${knownRow}`).values[0][0],known.today_from_initial_delta+100);
  assert.equal(sheet.getRange(`AC${knownRow}`).values[0][0],known.last_delta+100);
  sheet.getRange(`G${knownRow}`).values=[[known.today_views]];
  sheet.getRange(`F${knownRow}`).values=[[0]];
  assert.equal(sheet.getRange(`H${knownRow}`).values[0][0],known.today_views);
  assert(blank(sheet.getRange(`I${knownRow}`).values[0][0]));
  sheet.getRange(`F${knownRow}`).values=[[known.yesterday_views]];
  assert.equal(sheet.getRange(`H${knownRow}`).values[0][0],known.day_delta);
  sheet.getRange(`AB${knownRow}`).values=[[0]];
  assert.equal(sheet.getRange(`AC${knownRow}`).values[0][0],known.today_views);
  assert(blank(sheet.getRange(`AD${knownRow}`).values[0][0]));
  sheet.getRange(`AB${knownRow}`).values=[[known.last_views]];
  wb.recalculate();
  assert.deepEqual(sheet.getRange(`E${start}:G${end}`).values,rows.map(r=>[r.initial_views,r.yesterday_views,r.today_views]));
  assert.deepEqual(sheet.getRange(`X${start}:Y${end}`).values,rows.map(r=>[String(r.id),r.video_id==null?'':String(r.video_id)]));
  const calculated=sheet.getRange(`H${start}:M${end}`).values;
  rows.forEach((r,i)=>{
    const expected=[r.day_delta,r.day_rate,r.yesterday_from_initial_delta,r.yesterday_from_initial_rate,r.today_from_initial_delta,r.today_from_initial_rate];
    expected.forEach((v,j)=>v==null?assert(blank(calculated[i][j])):close(calculated[i][j],v));
  });
  const totals=sheet.getRange(`E${total}:M${total}`).values[0],s=cfg.summary;
  const expectedTotals=[s.initial_total,s.yesterday_total,s.today_total,s.day_delta,s.day_rate,s.yesterday_from_initial_delta,s.yesterday_from_initial_rate,s.today_from_initial_delta,s.today_from_initial_rate];
  expectedTotals.forEach((v,i)=>v==null?assert(blank(totals[i])):close(totals[i],v));
  const lastValues=sheet.getRange(`AB${start}:AD${end}`).values;
  rows.forEach((r,i)=>[r.last_views,r.last_delta,r.last_rate].forEach((v,j)=>v==null?assert(blank(lastValues[i][j])):close(lastValues[i][j],v)));
  const lastTotals=sheet.getRange(`AB${total}:AD${total}`).values[0];
  [s.last_total,s.last_delta,s.last_rate].forEach((v,i)=>close(lastTotals[i],v));
  const errors=await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:20},maxChars:1500,summary:cfg.name+' final formula scan'});
  await fs.writeFile(path.join(out,cfg.prefix+'_formula_scan.ndjson'),errors.ndjson);
  const check=await wb.inspect({kind:'table',range:`剧目统计!E${start}:M${Math.min(end,start+3)}`,include:'values,formulas',tableMaxRows:4,tableMaxCols:9,maxChars:3500});
  await fs.writeFile(path.join(out,cfg.prefix+'_inspection.ndjson'),check.ndjson);
  for(const [label,range] of [['preview','A1:M11'],['detail_preview','N7:AA10'],['last_comparison_preview','AB7:AE12']]){
    console.log('Rendering '+cfg.name+' '+label);
    const preview=await wb.render({sheetName:'剧目统计',range,scale:1,format:'png'});
    await fs.writeFile(path.join(out,cfg.prefix+'_'+label+'.png'),new Uint8Array(await preview.arrayBuffer()));
  }
  global.gc?.();
  console.log('Exporting '+cfg.name);
  const filename=`${cfg.prefix}_${rows.length}部剧_初始昨天今天_完整数字.xlsx`,file=await SpreadsheetFile.exportXlsx(wb);
  await file.save(path.join(out,filename));
  manifests.push({account:cfg.name,file:filename,rows:rows.length,initial:totals[0],yesterday:totals[1],today:totals[2],day_delta:s.day_delta,day_rate:s.day_rate,last_delta:s.last_delta,last_rate:s.last_rate,cumulative_delta:totals[7],formulaChecks:true,recalculationVerified:true});
  console.log(JSON.stringify(manifests.at(-1)));
}
await fs.writeFile(path.join(out,'export_manifest.json'),JSON.stringify(manifests,null,2));
