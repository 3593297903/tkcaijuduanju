import fs from "node:fs/promises";
import path from "node:path";
const API="/api/drama/user/drama_list/";
const CARD="[data-e2e=\"creator-drama-card\"]";

/** Refresh a private normal request using one real browser pagination action.
 * The standalone HTTP collector then starts at cursor0 and verifies all pages.
 * This captures no Cookie/Authorization headers and never invents signatures.
 */
export async function refreshRequestSpec({tab,account,output,maxPages=100}){
  if(!/^[a-zA-Z0-9_.]+$/.test(account))throw new Error('Invalid account');
  if(!output.toLowerCase().endsWith(".local.json"))throw new Error("Private request output must end in .local.json");
  const target='https://www.tiktok.com/@'+account;
  if(await tab.url()!==target)await tab.goto(target);
  const cdp=await tab.capabilities.get('cdp');
  await cdp.send('Network.enable');
  const methods=['Network.requestWillBeSent','Network.responseReceived'];
  const cursor=(await cdp.readEvents({methods,limit:1000})).cursor;
  await tab.playwright.locator(CARD).first().waitFor({state:'attached',timeoutMs:10000});
  const count=await tab.playwright.locator(CARD).count();
  await cdp.send('Input.dispatchMouseEvent',{type:'mouseWheel',x:Math.max(100,await tab.playwright.evaluate(()=>innerWidth-80)),y:500,deltaX:0,deltaY:6000});
  try{await tab.playwright.locator(CARD).nth(count).waitFor({state:'attached',timeoutMs:6000});}catch{}
  const events=await cdp.readEvents({methods,afterSequence:cursor,limit:1000,timeoutMs:1000});
  if(events.truncated||events.hasMore)throw new Error('Request capture buffer incomplete; retry in a fresh tab');
  const event=events.events.find(e=>e.method==='Network.requestWillBeSent'&&typeof e.params?.request?.url==='string'&&new URL(e.params.request.url).origin==="https://www.tiktok.com"&&new URL(e.params.request.url).pathname===API);
  if(!event)throw new Error('No fresh drama request observed; retry in a new account tab');
  const request=event.params.request;
  const headers=Object.fromEntries(Object.entries(request.headers||{}).filter(([k])=>['accept','accept-language','referer','user-agent'].includes(k.toLowerCase())));
  const spec={url:request.url,headers,initial_cursor:'0',cursor_param:'cursor',max_pages:maxPages,timeout_seconds:20,retries:1};
  await fs.mkdir(path.dirname(output),{recursive:true});
  await fs.writeFile(output,JSON.stringify(spec,null,2),{mode:0o600});
  return {account,request_spec_saved:true,captured_at:new Date().toISOString()};
}
