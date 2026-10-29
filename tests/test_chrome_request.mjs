import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {refreshRequestSpec} from '../integrations/chrome_capture.mjs';

const folder=await fs.mkdtemp(path.join(os.tmpdir(),'tkmonitor-request-'));
const output=path.join(folder,'demo.local.json');
const events=[{method:'Network.requestWillBeSent',params:{request:{url:'https://www.tiktok.com/api/drama/user/drama_list/?cursor=20&sample=synthetic',headers:{Accept:'application/json',Cookie:'SYNTHETIC_SECRET',Authorization:'Bearer SYNTHETIC',Referer:'https://www.tiktok.com/@demo','User-Agent':'synthetic'}}}}];
let reads=0;
const locator={first:()=>({waitFor:async()=>{}}),count:async()=>20,nth:()=>({waitFor:async()=>{}})};
const cdp={send:async()=>{},readEvents:async()=>++reads===1?{cursor:10,events:[],hasMore:false,truncated:false}:{cursor:20,events,hasMore:false,truncated:false}};
const tab={url:async()=>'https://www.tiktok.com/@demo',goto:async()=>{},capabilities:{get:async()=>cdp},playwright:{locator:()=>locator,evaluate:async()=>1280}};
const result=await refreshRequestSpec({tab,account:'demo',output});
assert.equal(result.request_spec_saved,true);
const saved=JSON.parse(await fs.readFile(output,'utf8'));
assert.equal(saved.initial_cursor,'0');
assert.equal(saved.url,events[0].params.request.url);
assert.equal(saved.headers.Cookie,undefined);assert.equal(saved.headers.Authorization,undefined);
assert(!JSON.stringify(saved).includes('SECRET'));
await assert.rejects(refreshRequestSpec({tab,account:'demo',output:path.join(folder,'README.md')}),/local.json/);
events[0].params.request.url='https://example.com/api/drama/user/drama_list/?cursor=20';reads=0;
await assert.rejects(refreshRequestSpec({tab,account:'demo',output}),/No fresh drama/);
console.log('Chrome request adapter: private output, normal-header allowlist, origin, and zero-start tests passed.');
// This directory was returned by mkdtemp and contains only synthetic test output.
await fs.unlink(output);await fs.rmdir(folder);
