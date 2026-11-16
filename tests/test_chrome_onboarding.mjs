import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {captureRequestSpec,captureOnboarding,refreshRequestSpec} from '../integrations/chrome_capture.mjs';
import {identityFromHtml,validateDramaResponse} from '../integrations/chrome_onboarding.mjs';

const ONE='0000000000000000001';
const PAGE={status_code:0,hasMore:false,cursor:1,dramaList:[{dramaID:ONE,dramaName:'Synthetic One',numWatched:'9007199254740993'}]};
const HTML='<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'+JSON.stringify({userInfo:{user:{uniqueId:'demo',secUid:'SYNTHETIC_UID'}}})+'</script>';

async function folder(t){
  const result=await fs.mkdtemp(path.join(os.tmpdir(),'tkmonitor-request-'));
  t.after(async()=>{
    assert.equal(path.dirname(path.resolve(result)),path.resolve(os.tmpdir()));
    assert(path.basename(result).startsWith('tkmonitor-request-'));
    await fs.rm(result,{recursive:true,force:true});
  });
  return result;
}

function fixture(options={}){
  let time=0,sent=false,selected=options.selected!==false,closed=0;
  const trace=[];
  const request={method:'GET',url:options.url??'https://www.tiktok.com/api/drama/user/drama_list/?cursor=0&secUid=SYNTHETIC_UID&X-Bogus=SYNTHETIC_SIGNATURE',headers:{Accept:'application/json',Cookie:'SECRET_COOKIE',Authorization:'Bearer SECRET_AUTH',Referer:'https://www.tiktok.com/@demo','User-Agent':'synthetic'}};
  const makeEvents=()=>[
    {method:'Network.requestWillBeSent',params:{requestId:'doc',request:{method:'GET',url:'https://www.tiktok.com/@demo'}}},
    {method:'Network.responseReceived',params:{requestId:'doc',response:{status:200,mimeType:'text/html'}}},
    {method:'Network.loadingFinished',params:{requestId:'doc'}},
    {method:'Network.requestWillBeSent',params:{requestId:'api',request}},
    {method:'Network.responseReceived',params:{requestId:'api',response:{status:options.status??200,mimeType:options.mime??'application/json'}}},
    ...(options.finished===false?[]:[{method:'Network.loadingFinished',params:{requestId:'api'}}])
  ];
  const cdp={
    send:async(method,args)=>{
      trace.push(method);
      if(method==='Network.getResponseBody')return {body:args.requestId==='doc'?(options.html??HTML):(options.body??JSON.stringify(PAGE)),base64Encoded:false};
      if(method==='Input.dispatchMouseEvent')sent=false;
      return {};
    },
    readEvents:async(args)=>{
      trace.push('readEvents');time+=100;options.advance?.(100);
      if(args.afterSequence===undefined)return {cursor:1,events:[],hasMore:false,truncated:false};
      if(options.truncated||options.truncatedAfterScroll&&trace.includes('Input.dispatchMouseEvent'))return {cursor:2,events:[],hasMore:false,truncated:true};
      const canSend=!options.noEvents&&(!options.onClick||selected)&&(!options.onScroll||trace.includes('Input.dispatchMouseEvent'));
      if(!sent&&canSend){sent=true;return {cursor:2,events:makeEvents(),hasMore:false,truncated:false};}
      return {cursor:3,events:[],hasMore:false,truncated:false};
    }
  };
  const tab={
    url:async()=>'https://www.tiktok.com/@demo',goto:async()=>{trace.push('goto');},
    reload:async()=>{throw new Error('User page reload is forbidden');},
    close:async()=>{closed++;trace.push('close');},capabilities:{get:async()=>cdp},
    playwright:{evaluate:async()=>null,getByRole:(role)=>({isVisible:async()=>role==='heading'?options.visible!==false:true,
      getAttribute:async()=>selected?'true':'false',click:async()=>{selected=true;trace.push('click');}})}
  };
  return {tab,trace,clock:()=>time,closed:()=>closed,request};
}

test('early first-page request: listen before goto, exact URL, no scroll, no cookie headers',async t=>{
  const root=await folder(t),f=fixture(),output=path.join(root,'request.local.json');
  const result=await captureRequestSpec({tab:f.tab,account:'demo',output,navigate:true,clock:f.clock,timeoutMs:2000});
  assert(f.trace.indexOf('Network.enable')<f.trace.indexOf('goto'));
  assert(f.trace.indexOf('readEvents')<f.trace.indexOf('goto'));
  assert.equal(result.scroll_count,0);assert(!f.trace.includes('Input.dispatchMouseEvent'));
  const saved=JSON.parse(await fs.readFile(output,'utf8'));
  assert.equal(saved.url,f.request.url);assert.equal(saved.initial_cursor,'0');
  assert.equal(saved.headers.Cookie,undefined);assert.equal(saved.headers.Authorization,undefined);
  assert(!JSON.stringify(saved).includes('SECRET_'));assert.equal(result.records,1);
});

test('compatibility export does not navigate a caller user page',async t=>{
  const root=await folder(t),f=fixture();
  const result=await refreshRequestSpec({tab:f.tab,account:'demo',output:path.join(root,'request.local.json'),clock:f.clock,timeoutMs:2000});
  assert(result.request_spec_saved);assert(!f.trace.includes('goto'));assert(!f.trace.includes('Network.disable'));
});

test('normal Short dramas tab selection is observed before its request',async t=>{
  const root=await folder(t),f=fixture({selected:false,onClick:true});
  await captureRequestSpec({tab:f.tab,account:'demo',output:path.join(root,'request.local.json'),navigate:true,clock:f.clock,timeoutMs:2000});
  assert(f.trace.includes('click'));assert(!f.trace.includes('Input.dispatchMouseEvent'));
});

test('no first-page request returns bounded unfinished result without scroll or overwrite',async t=>{
  const root=await folder(t),f=fixture({noEvents:true}),output=path.join(root,'request.local.json');
  await fs.writeFile(output,'PRESERVED');
  await assert.rejects(captureRequestSpec({tab:f.tab,account:'demo',output,clock:f.clock,timeoutMs:1000}),e=>e.code==='NEEDS_BROWSER_ACTION');
  assert.equal(await fs.readFile(output,'utf8'),'PRESERVED');assert(!f.trace.includes('Input.dispatchMouseEvent'));
});

test('explicit fallback scroll happens only once',async t=>{
  const root=await folder(t),f=fixture({onScroll:true});
  const result=await captureRequestSpec({tab:f.tab,account:'demo',output:path.join(root,'request.local.json'),allowScrollFallback:true,clock:f.clock,timeoutMs:2000});
  assert.equal(result.scroll_count,1);assert.equal(f.trace.filter(x=>x==='Input.dispatchMouseEvent').length,1);
});

test('wrong account, missing identity, unrelated origin and unfinished response cannot activate',async t=>{
  for(const options of [{url:'https://www.tiktok.com/api/drama/user/drama_list/?cursor=0&secUid=OTHER'},
    {html:'<html>No public identity mapping</html>'},{url:'https://example.com/api/drama/user/drama_list/?cursor=0&secUid=SYNTHETIC_UID'},{finished:false},{visible:false}]){
    const root=await folder(t),f=fixture(options),output=path.join(root,'request.local.json');
    await assert.rejects(captureRequestSpec({tab:f.tab,account:'demo',output,clock:f.clock,timeoutMs:1000}),e=>e.code==='NEEDS_BROWSER_ACTION');
    await assert.rejects(fs.access(output));
  }
});

test('HTTP rejection, HTML, business errors and truncated events are not accepted',async t=>{
  for(const [options,code] of [[{status:403},'HTTP_REJECTED'],[{mime:'text/html'},'NON_JSON'],[{body:JSON.stringify({status_code:99})},'API_ERROR'],[{truncated:true},'EVENTS_INCOMPLETE']]){
    const root=await folder(t),f=fixture(options);
    await assert.rejects(captureRequestSpec({tab:f.tab,account:'demo',output:path.join(root,'request.local.json'),clock:f.clock,timeoutMs:1000}),e=>e.code===code);
  }
});

test('precise counts remain strings; unsafe numeric values and duplicate IDs fail',()=>{
  assert.equal(validateDramaResponse(PAGE,'0').records,1);
  assert.throws(()=>validateDramaResponse({...PAGE,dramaList:[{...PAGE.dramaList[0],numWatched:9007199254740992}]},'0'));
  assert.throws(()=>validateDramaResponse({...PAGE,cursor:2,dramaList:[PAGE.dramaList[0],PAGE.dramaList[0]]},'0'));
  assert.equal(identityFromHtml(HTML,'other'),null);
});

test('private output and user-tab navigation restrictions apply before browser use',async t=>{
  const root=await folder(t),f=fixture();
  await assert.rejects(captureRequestSpec({tab:f.tab,account:'demo',output:path.join(root,'README.md')}),/local.json/);
  await assert.rejects(captureRequestSpec({tab:f.tab,account:'other',output:path.join(root,'request.local.json')}),e=>e.code==='TARGET_MISMATCH');
  assert.equal(f.trace.length,0);
});

async function manifest(t){
  const root=await folder(t),id='1234567890abcdef1234567890abcdef';
  const task={schema:'tkmonitor.onboarding/v1',task_id:id,state:'needs_browser',account:'demo',profile_url:'https://www.tiktok.com/@demo',browser_rounds:1,allow_scroll_fallback:false,browser_timeout_seconds:5};
  for(const [key,suffix] of Object.entries({candidate_path:'.request.local.json',evidence_path:'.evidence.local.json',response_path:'.response.local.json',failure_path:'.failure.local.json'}))task[key]=path.join(root,id+suffix);
  const file=path.join(root,id+'.local.json');await fs.writeFile(file,JSON.stringify(task));return {root,task,file};
}

test('injected Chrome factory closes its temporary tab and produces hashed evidence',async t=>{
  const m=await manifest(t),f=fixture();
  const result=await captureOnboarding({manifestPath:m.file,createTab:async url=>{assert.equal(url,'https://www.tiktok.com/');return f.tab;}});
  assert.equal(result.status,'READY_TO_RESUME');assert.equal(f.closed(),1);
  const evidence=JSON.parse(await fs.readFile(m.task.evidence_path,'utf8'));
  assert.equal(evidence.task_id,m.task.task_id);assert.equal(evidence.scroll_count,0);assert.equal(evidence.temporary_tabs,1);
  assert.equal(evidence.request_spec_sha256.length,64);assert.equal(evidence.response_sha256.length,64);
  assert((await fs.readFile(m.task.response_path,'utf8')).includes('9007199254740993'));
});

test('unavailable browser does not claim success or consume a capture attempt',async t=>{
  const m=await manifest(t),result=await captureOnboarding({manifestPath:m.file});
  assert.equal(result.status,'NEEDS_BROWSER');assert.equal(result.code,'BROWSER_UNAVAILABLE');
  await assert.rejects(fs.access(path.join(m.root,m.task.task_id+'.capture-attempt.local.json')));
});

test('truncation permits one replacement, closes both tabs and bounds repeat attempts',async t=>{
  const m=await manifest(t),pages=[fixture({truncated:true}),fixture({truncated:true})];let count=0;
  const result=await captureOnboarding({manifestPath:m.file,createTab:async()=>pages[count++].tab});
  assert.equal(count,2);assert.equal(result.code,'EVENTS_INCOMPLETE');assert(pages.every(f=>f.closed()===1));
  const again=await captureOnboarding({manifestPath:m.file,createTab:async()=>{throw new Error('SECRET_RUNTIME_URL');}});
  assert.equal(again.code,'CAPTURE_ALREADY_ATTEMPTED');assert(!JSON.stringify(result).includes('SECRET'));
});

test('scroll budget is shared across a truncated page and its successful replacement',async t=>{
  const m=await manifest(t);m.task.allow_scroll_fallback=true;await fs.writeFile(m.file,JSON.stringify(m.task));
  let wall=Date.now(),count=0;const originalNow=Date.now;
  const pages=[fixture({noEvents:true,truncatedAfterScroll:true,advance:ms=>{wall+=ms;}}),fixture({advance:ms=>{wall+=ms;}})];
  try{
    Date.now=()=>wall;
    const result=await captureOnboarding({manifestPath:m.file,createTab:async()=>pages[count++].tab});
    assert.equal(result.status,'READY_TO_RESUME');assert.equal(result.temporary_tabs,2);assert.equal(result.scroll_count,1);
    assert.equal(pages.flatMap(f=>f.trace).filter(x=>x==='Input.dispatchMouseEvent').length,1);
    const evidence=JSON.parse(await fs.readFile(m.task.evidence_path,'utf8'));assert.equal(evidence.scroll_count,1);assert.equal(evidence.temporary_tabs,2);
  }finally{Date.now=originalNow;}
});

test('unexpected browser error text stays out of public result and saved failure summary',async t=>{
  const m=await manifest(t);
  const result=await captureOnboarding({manifestPath:m.file,createTab:async()=>{throw new Error('https://example.test/?SECRET_RUNTIME_TOKEN=synthetic');}});
  assert.equal(result.code,'BROWSER_RUNTIME_ERROR');assert(!JSON.stringify(result).includes('SECRET'));
  assert(!(await fs.readFile(m.task.failure_path,'utf8')).includes('SECRET'));
});
