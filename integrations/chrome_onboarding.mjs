/** Request preparation in an injected, authorized Chrome runtime. */
import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash, randomUUID} from 'node:crypto';

const API='/api/drama/user/drama_list/';
const METHODS=['Network.requestWillBeSent','Network.responseReceived','Network.loadingFinished','Network.loadingFailed'];
const HEADERS=new Set(['accept','accept-language','referer','user-agent']);
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
export class CaptureError extends Error {
  constructor(code,message){super(message);this.name='CaptureError';this.code=code;}
}

async function atomicJson(output,value){
  if(!output.endsWith('.local.json'))throw new CaptureError('PRIVATE_OUTPUT','Browser output must end in .local.json.');
  await fs.mkdir(path.dirname(output),{recursive:true});
  const temporary=output+'.'+randomUUID()+'.tmp';
  const bytes=JSON.stringify(value,null,2)+'\n';
  try{await fs.writeFile(temporary,bytes,{mode:0o600});await fs.rename(temporary,output);}
  finally{await fs.unlink(temporary).catch(()=>{});}
  return sha(bytes);
}

function profileUrl(account){
  if(!/^[A-Za-z0-9_.-]+$/.test(account)||['.','..'].includes(account))throw new CaptureError('ACCOUNT_INVALID','Invalid target account.');
  return 'https://www.tiktok.com/@'+account;
}

function requestInfo(request){
  try{
    const url=new URL(request.url);
    if(url.origin!=='https://www.tiktok.com'||url.pathname!==API||url.hash||url.username||url.password
        ||request.method!=='GET'||url.searchParams.getAll('secUid').length!==1||!url.searchParams.get('secUid')
        ||url.searchParams.getAll('cursor').length!==1||!/^\d+$/.test(url.searchParams.get('cursor')))return null;
    return {secUid:url.searchParams.get('secUid'),cursor:url.searchParams.get('cursor')};
  }catch{return null;}
}

/** Only extract the exact public handle -> secUid mapping requested by the user. */
export function profileIdentity(value,account,source='profile_response'){
  let visited=0;
  function walk(node,depth=0){
    if(!node||typeof node!=='object'||depth>16||++visited>80000)return null;
    if(node.uniqueId===account&&typeof node.secUid==='string'&&node.secUid)return {handle:account,secUid:node.secUid,source};
    for(const item of Object.values(node)){const found=walk(item,depth+1);if(found)return found;}
    return null;
  }
  return walk(value);
}

export function identityFromHtml(html,account){
  const pattern=/<script\b[^>]*\bid=["'](?:__UNIVERSAL_DATA_FOR_REHYDRATION__|SIGI_STATE)["'][^>]*>([\s\S]*?)<\/script>/gi;
  for(const match of html.matchAll(pattern)){
    try{const found=profileIdentity(JSON.parse(match[1]),account,'profile_document');if(found)return found;}catch{}
  }
  return null;
}

export function validateDramaResponse(value,cursor){
  if(!value||typeof value!=='object'||Array.isArray(value))throw new CaptureError('RESPONSE_INVALID','Expected a drama response object.');
  if(['status_code','statusCode'].filter(k=>k in value).some(k=>value[k]!==0&&value[k]!=='0'))throw new CaptureError('API_ERROR','The captured request returned a business error.');
  if(!Array.isArray(value.dramaList)||typeof value.hasMore!=='boolean'
      ||!(typeof value.cursor==='string'&&/^\d+$/.test(value.cursor)||typeof value.cursor==='number'&&Number.isSafeInteger(value.cursor)&&value.cursor>=0)
      ||BigInt(value.cursor)!==BigInt(cursor)+BigInt(value.dramaList.length))throw new CaptureError('RESPONSE_INVALID','The captured response has invalid pagination.');
  const seen=new Set();let complete=0;
  for(const item of value.dramaList){
    if(!item||typeof item!=='object'||typeof item.dramaID!=='string'||!/^\d{19}$/.test(item.dramaID))throw new CaptureError('RESPONSE_INVALID','The captured response has invalid drama IDs.');
    if(Object.keys(item).length===1)continue;
    const count=item.numWatched;
    if(seen.has(item.dramaID)||typeof item.dramaName!=='string'||!item.dramaName.trim()
        ||!(typeof count==='string'&&/^\d+$/.test(count)||typeof count==='number'&&Number.isSafeInteger(count)&&count>=0)
        ||BigInt(count)>9223372036854775807n)throw new CaptureError('RESPONSE_INVALID','The captured response has invalid drama titles or precise counts.');
    seen.add(item.dramaID);complete++;
  }
  if(!complete)throw new CaptureError('RESPONSE_INVALID','No complete dramas were returned by the captured request.');
  return {records:complete,raw_slots:value.dramaList.length};
}

async function responseBody(cdp,requestId){
  const result=await cdp.send('Network.getResponseBody',{requestId});
  if(typeof result?.body!=='string')throw new CaptureError('RESPONSE_UNAVAILABLE','The recorded response body is unavailable.');
  const body=result.base64Encoded?Buffer.from(result.body,'base64').toString('utf8'):result.body;
  if(Buffer.byteLength(body)>25*1024*1024)throw new CaptureError('RESPONSE_INVALID','The recorded response exceeds the size limit.');
  return body;
}

async function domIdentity(tab,account){
  if(!tab.playwright?.evaluate)return null;
  try{
    // Read public DOM JSON and return only the target identity, not the whole page.
    return await tab.playwright.evaluate(target=>{
      let visited=0;
      function find(node,depth=0){
        if(!node||typeof node!=='object'||depth>16||++visited>80000)return null;
        if(node.uniqueId===target&&typeof node.secUid==='string'&&node.secUid)return {handle:target,secUid:node.secUid,source:'profile_dom_json'};
        for(const item of Object.values(node)){const found=find(item,depth+1);if(found)return found;}
        return null;
      }
      for(const element of document.querySelectorAll('script#__UNIVERSAL_DATA_FOR_REHYDRATION__,script#SIGI_STATE')){
        try{const identity=find(JSON.parse(element.textContent));if(identity)return identity;}catch{}
      }
      return null;
    },account);
  }catch{return null;}
}

async function visibleAccount(tab,account){
  try{return await tab.playwright.getByRole('heading',{name:account,exact:true}).isVisible();}catch{return false;}
}

/** navigate=true is restricted to a caller-owned temporary page. */
export async function captureRequestSpec({tab,account,output,evidencePath,responsePath,taskId=null,navigate=false,
  allowScrollFallback=false,timeoutMs=90000,maxPages=100,initialEvents=[],clock=()=>Date.now(),onScroll=()=>{}}){
  const target=profileUrl(account);
  if(!output?.endsWith('.local.json'))throw new CaptureError('PRIVATE_OUTPUT','Private request output must end in .local.json.');
  if(!navigate&&(await tab.url())!==target)throw new CaptureError('TARGET_MISMATCH','Use a target account tab or a caller-owned temporary tab.');
  if(!Number.isFinite(timeoutMs)||timeoutMs<100||timeoutMs>300000)throw new CaptureError('BUDGET_INVALID','Invalid browser capture budget.');
  const started=clock(),deadline=started+timeoutMs;
  const cdp=await tab.capabilities.get('cdp');
  await cdp.send('Network.enable');
  const first=await cdp.readEvents({methods:METHODS,limit:1000});
  let cursor=first.cursor,identity=null,clicked=false,scrolls=0,visible=false,domChecked=false,lastDomCheck=0;
  let events=[...initialEvents];
  const pending=new Map(),readBodies=new Set();
  try{
    if(navigate)await tab.goto(target);
    while(clock()<deadline){
      for(const event of events){
        const p=event.params??{};if(typeof p.requestId!=='string')continue;
        const entry=pending.get(p.requestId)??{};
        if(event.method==='Network.requestWillBeSent')entry.request=p.request;
        if(event.method==='Network.responseReceived')entry.response=p.response;
        if(event.method==='Network.loadingFinished')entry.finished=true;
        if(event.method==='Network.loadingFailed')entry.failed=true;
        pending.set(p.requestId,entry);
      }
      for(const [id,entry] of pending){
        if(!entry.request||!entry.response||!entry.finished||entry.failed||readBodies.has(id))continue;
        let url;try{url=new URL(entry.request.url);}catch{continue;}
        if(url.origin!=='https://www.tiktok.com')continue;
        const info=requestInfo(entry.request),profileDocument=url.pathname==='/@'+account,profileResponse=url.pathname==='/api/user/detail/';
        if(!info&&!profileDocument&&!profileResponse)continue;
        let body;try{body=entry.body??await responseBody(cdp,id);}catch{readBodies.add(id);continue;}
        if(profileDocument){identity=identityFromHtml(body,account)??identity;readBodies.add(id);continue;}
        if(profileResponse){try{identity=profileIdentity(JSON.parse(body),account)??identity;}catch{}readBodies.add(id);continue;}
        entry.body=body;
        if(!identity)continue;
        if(info.secUid!==identity.secUid){readBodies.add(id);continue;}
        if(entry.response.status!==200)throw new CaptureError('HTTP_REJECTED','The target drama request was not successful.');
        const contentType=entry.response.mimeType??Object.entries(entry.response.headers??{}).find(([k])=>k.toLowerCase()==='content-type')?.[1]??'';
        if(!/application\/json|\+json/i.test(contentType))throw new CaptureError('NON_JSON','The target drama response is not JSON.');
        let value;try{value=JSON.parse(body);}catch{throw new CaptureError('INVALID_JSON','The target drama response is not valid JSON.');}
        const counts=validateDramaResponse(value,info.cursor);
        visible=await visibleAccount(tab,account);if(!visible)continue;
        const headers=Object.fromEntries(Object.entries(entry.request.headers??{}).filter(([k,v])=>HEADERS.has(k.toLowerCase())&&typeof v==='string'&&!/[\r\n]/.test(v)));
        const spec={url:entry.request.url,headers,initial_cursor:'0',cursor_param:'cursor',max_pages:maxPages,timeout_seconds:20,retries:1};
        const captured=new Date().toISOString(),specHash=await atomicJson(output,spec);
        let responseHash=null;
        if(responsePath){
          if(!responsePath.endsWith('.local.json'))throw new CaptureError('PRIVATE_OUTPUT','Response output must be private.');
          const temporary=responsePath+'.'+randomUUID()+'.tmp';
          try{await fs.writeFile(temporary,body,{mode:0o600});await fs.rename(temporary,responsePath);}finally{await fs.unlink(temporary).catch(()=>{});}
          responseHash=sha(body);
        }
        const evidence={schema:'tkmonitor.browser-capture/v1',task_id:taskId,account,visible_account:account,identity,
          captured_at:captured,request_id:id,request_method:'GET',http_status:200,loading_finished:true,
          capture_source:scrolls?'scroll_fallback':navigate?'first_page':'recorded_request',scroll_count:scrolls,
          user_page_refreshes:0,temporary_tabs:navigate?1:0,duration_ms:clock()-started,
          request_spec_sha256:specHash,response_sha256:responseHash,...counts};
        if(evidencePath)await atomicJson(evidencePath,evidence);
        return {account,request_spec_saved:true,captured_at:captured,capture_source:evidence.capture_source,
          scroll_count:scrolls,user_page_refreshes:0,records:counts.records,duration_ms:evidence.duration_ms};
      }
      if(clock()-lastDomCheck>=400||!domChecked){
        if(!identity){identity=await domIdentity(tab,account);domChecked=true;}
        visible=await visibleAccount(tab,account);
        if(visible&&!clicked){
          try{
            const dramas=tab.playwright.getByRole('tab',{name:'Short dramas',exact:true});
            if(await dramas.isVisible()&&await dramas.getAttribute('aria-selected')!=='true')await dramas.click({timeout:3000});
            clicked=true;
          }catch{}
        }
        lastDomCheck=clock();
      }
      if(allowScrollFallback&&scrolls===0&&visible&&clock()-started>=Math.min(timeoutMs/2,5000)){
        await cdp.send('Input.dispatchMouseEvent',{type:'mouseWheel',x:100,y:500,deltaX:0,deltaY:900});scrolls++;onScroll();
      }
      const page=await cdp.readEvents({methods:METHODS,afterSequence:cursor,limit:1000,timeoutMs:Math.max(1,Math.min(500,deadline-clock()))});
      cursor=page.cursor;
      if(page.truncated)throw new CaptureError('EVENTS_INCOMPLETE','Required browser event history was truncated; no request was activated.');
      events=page.events;
    }
    throw new CaptureError('NEEDS_BROWSER_ACTION','No verifiable target request was observed within the capture budget.');
  }finally{if(navigate)await cdp.send('Network.disable').catch(()=>{});}
}

async function readManifest(manifestPath){
  const file=path.resolve(manifestPath),task=JSON.parse(await fs.readFile(file,'utf8'));
  if(task.schema!=='tkmonitor.onboarding/v1'||!/^[a-f0-9]{32}$/.test(task.task_id)||path.basename(file)!==task.task_id+'.local.json'
      ||profileUrl(task.account)!==task.profile_url||task.browser_rounds!==1||typeof task.allow_scroll_fallback!=='boolean'
      ||!Number.isFinite(task.browser_timeout_seconds)||task.browser_timeout_seconds<5||task.browser_timeout_seconds>300)throw new CaptureError('MANIFEST_INVALID','Invalid private onboarding task.');
  for(const [key,suffix] of Object.entries({candidate_path:'.request.local.json',evidence_path:'.evidence.local.json',response_path:'.response.local.json',failure_path:'.failure.local.json'})){
    if(path.resolve(task[key]??'')!==path.join(path.dirname(file),task.task_id+suffix))throw new CaptureError('MANIFEST_INVALID','Task output escaped its private directory.');
  }
  if(task.state!=='needs_browser')throw new CaptureError('TASK_STATE_INVALID','This task is no longer awaiting browser preparation.');
  return task;
}

/** createTab is supplied by CUA, e.g. url => cua.createBrowserTab('chrome',url,...). */
export async function captureOnboarding({manifestPath,createTab,recordedTab=null,initialEvents=[]}){
  const task=await readManifest(manifestPath);
  try{await fs.access(task.evidence_path);await fs.access(task.candidate_path);return {status:'READY_TO_RESUME',account:task.account,manifest:manifestPath};}catch{}
  if(typeof createTab!=='function'&&!(recordedTab&&initialEvents.length))return {status:'NEEDS_BROWSER',code:'BROWSER_UNAVAILABLE',account:task.account,manifest:manifestPath};
  const attemptPath=path.join(path.dirname(path.resolve(manifestPath)),task.task_id+'.capture-attempt.local.json');
  try{await fs.writeFile(attemptPath,JSON.stringify({started_at:new Date().toISOString()}),{flag:'wx',mode:0o600});}
  catch(error){if(error.code==='EEXIST')return {status:'NEEDS_BROWSER_ACTION',code:'CAPTURE_ALREADY_ATTEMPTED',account:task.account,manifest:manifestPath};throw error;}
  const started=Date.now();let temporary=0,totalScrolls=0;
  try{
    if(recordedTab&&initialEvents.length){
      try{
        const result=await captureRequestSpec({tab:recordedTab,account:task.account,output:task.candidate_path,
          evidencePath:task.evidence_path,responsePath:task.response_path,taskId:task.task_id,
          initialEvents,timeoutMs:Math.min(5000,task.browser_timeout_seconds*1000)});
        return {status:'READY_TO_RESUME',...result,manifest:manifestPath,temporary_tabs:0};
      }catch(error){if(!(error instanceof CaptureError))throw error;}
    }
    if(typeof createTab!=='function')throw new CaptureError('BROWSER_UNAVAILABLE','An authorized Chrome tab factory is required.');
    let lastError;
    for(let attempt=0;attempt<2;attempt++){
      const remaining=task.browser_timeout_seconds*1000-(Date.now()-started);if(remaining<1000)break;
      let tab;
      try{
        tab=await createTab('https://www.tiktok.com/');temporary++;
        const captureBudget=task.browser_timeout_seconds*1000-(Date.now()-started);
        if(captureBudget<100)throw new CaptureError('NEEDS_BROWSER_ACTION','The temporary page initialization exhausted the capture budget.');
        const result=await captureRequestSpec({tab,account:task.account,output:task.candidate_path,evidencePath:task.evidence_path,
          responsePath:task.response_path,taskId:task.task_id,navigate:true,allowScrollFallback:task.allow_scroll_fallback&&totalScrolls===0,
          timeoutMs:captureBudget,onScroll:()=>{totalScrolls++;}});
        const evidence=JSON.parse(await fs.readFile(task.evidence_path,'utf8'));
        evidence.temporary_tabs=temporary;evidence.scroll_count=totalScrolls;evidence.duration_ms=Date.now()-started;
        await atomicJson(task.evidence_path,evidence);
        return {status:'READY_TO_RESUME',...result,scroll_count:totalScrolls,duration_ms:evidence.duration_ms,
          manifest:manifestPath,temporary_tabs:temporary,total_duration_ms:Date.now()-started};
      }catch(error){lastError=error;if(!(error instanceof CaptureError)||error.code!=='EVENTS_INCOMPLETE')break;}
      finally{if(tab)await tab.close().catch(()=>{});}
    }
    throw lastError??new CaptureError('NEEDS_BROWSER_ACTION','Browser preparation did not finish within its budget.');
  }catch(error){
    const failure={status:'NEEDS_BROWSER_ACTION',code:error instanceof CaptureError?error.code:'BROWSER_RUNTIME_ERROR',
      account:task.account,manifest:manifestPath,temporary_tabs:temporary,scroll_count:totalScrolls,user_page_refreshes:0,total_duration_ms:Date.now()-started};
    await atomicJson(task.failure_path,failure);return failure;
  }
}
