/** Optional real-engine integration test, not part of stdlib Python tests. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';

const folder=await fs.mkdtemp(path.join(os.tmpdir(),'tkmonitor-workbook-'));
const base={order:1,id:'0000000000000000001',title:'Synthetic',title_zh:'测试',image:null,
 initial_recorded_at:'2020-01-01T00:00:00Z',yesterday_recorded_at:'2020-01-02T00:00:00Z',today_recorded_at:'2020-01-03T00:00:00Z',last_recorded_at:'2020-01-02T00:00:00Z',recorded_at_beijing:'2020-01-03 08:00:00',today_frontend_integer:5,episodes:1,create_time:null,is_preview:null,description_zh:'脱敏测试',genre:'测试',video_id:null,video_url:null,thumbnail:null};
const rows=[{...base,initial_views:2,yesterday_views:4,today_views:5,day_delta:1,day_rate:.25,yesterday_from_initial_delta:2,yesterday_from_initial_rate:1,today_from_initial_delta:3,today_from_initial_rate:1.5,last_views:4,last_delta:1,last_rate:.25},
 {...base,order:2,id:'0000000000000000002',initial_views:1,yesterday_views:7,today_views:null,today_frontend_integer:null,day_delta:null,day_rate:null,yesterday_from_initial_delta:6,yesterday_from_initial_rate:6,today_from_initial_delta:null,today_from_initial_rate:null,last_views:7,last_delta:null,last_rate:null}];
const summary={initial_total:3,yesterday_total:11,today_total:5,day_delta:1,day_rate:.25,yesterday_from_initial_delta:8,yesterday_from_initial_rate:8/3,today_from_initial_delta:3,today_from_initial_rate:1.5,last_total:11,last_delta:1,last_rate:.25};
const cfg={name:'Synthetic',prefix:'Synthetic',source:'https://www.tiktok.com/@synthetic',report_day:'2020-01-03',previous_day:'2020-01-02',metric:'frontend',metric_label:'接口整数',rows,summary};
await fs.writeFile(path.join(folder,'workbook_configs.json'),JSON.stringify([cfg]));
const script=fileURLToPath(new URL('../integrations/export_workbooks.mjs',import.meta.url));
const result=spawnSync(process.execPath,['--expose-gc','--v8-pool-size=1','--max-old-space-size=1024','--no-wasm-tier-up','--wasm-num-compilation-tasks=1',script,folder,'--check-only'],{env:process.env,encoding:'utf8',timeout:120000});
assert.equal(result.status,0,result.stderr+'\n'+result.stdout);
assert(result.stdout.includes('"last_rate":0.25'),result.stdout);
console.log('Real workbook engine passed: partial current metric uses the paired last-value denominator.');
// Only remove files created in this freshly returned temporary directory.
for(const file of await fs.readdir(folder))await fs.unlink(path.join(folder,file));
await fs.rmdir(folder);
