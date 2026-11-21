import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {createHash} from 'node:crypto';
import {captureCheckpoint} from '../integrations/chrome_checkpoint.mjs';

const one = '0000000000000000001';
const two = '0000000000000000002';
const secUid = 'SYNTHETIC_IDENTITY';
const start = Date.parse('2026-11-20T08:00:00Z');
const first = {status_code: 0, hasMore: true, cursor: 1,
  dramaList: [{dramaID: one, dramaName: 'First Drama', numWatched: '9007199254740993'}]};
const second = {status_code: 0, hasMore: true, cursor: 3,
  dramaList: [{dramaID: one}, {dramaID: two, dramaName: 'Second Drama', numWatched: 12}]};
const terminal = {status_code: 0, hasMore: false, cursor: 3};

async function task(t, phase = 'collect', requested = [], accounts = ['demo']) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'tkmonitor-checkpoint-'));
  t.after(async () => {
    assert.equal(path.dirname(path.resolve(root)), path.resolve(os.tmpdir()));
    assert(path.basename(root).startsWith('tkmonitor-checkpoint-'));
    await fs.rm(root, {recursive: true, force: true});
  });
  const id = '7314a87bec5d2cc70c9e145d2302a93f';
  const directory = path.join(root, `${id}.browser`);
  const manifestPath = path.join(root, `${id}.browser.local.json`);
  await fs.writeFile(manifestPath, JSON.stringify({schema: 'tkmonitor.browser-fallback/v1', task_id: id, phase,
    selected_accounts: accounts, started_at: new Date(start).toISOString(), browser_timeout_seconds: 6, output_dir: directory,
    missing_covers: {demo: requested}}));
  return {root, id, directory, manifestPath, read: async file => JSON.parse(await fs.readFile(path.join(directory, file), 'utf8'))};
}

function fixture({pages = [first, second, terminal], requestedCursors = [0, 1, 3], wrongIdentity = false,
  duplicateTitle = false, candidate, downloadFile, assets} = {}) {
  let time = start, cursor = 0, page = -1, target;
  let pendingEvents = [];
  const bodies = new Map();
  const trace = [];
  let downloads = 0;
  const event = (method, params) => ({method: `Network.${method}`, params});
  function sendPage(index) {
    if (!pages[index]) return;
    const requestId = `page-${index}`;
    const uid = wrongIdentity && index === 1 ? 'UNRELATED' : secUid;
    const url = `https://www.tiktok.com/api/drama/user/drama_list/?cursor=${requestedCursors[index]}&secUid=${uid}&X-Bogus=NO_TOKEN_OUTPUT`;
    bodies.set(requestId, JSON.stringify(pages[index]));
    pendingEvents.push(event('requestWillBeSent', {requestId, request: {url, method: 'GET'}}),
      event('responseReceived', {requestId, response: {url, status: 200, mimeType: 'application/json'}}),
      event('loadingFinished', {requestId}));
  }
  const cdp = {
    async send(method, params) {
      trace.push(method);
      if (method === 'Network.getResponseBody') return {body: bodies.get(params.requestId)};
      if (method === 'Input.dispatchMouseEvent') sendPage(++page);
      return {};
    },
    async readEvents(options) {
      time += 320;
      if (options.afterSequence === undefined) return {cursor, events: [], truncated: false};
      const events = pendingEvents;
      pendingEvents = [];
      return {cursor: ++cursor, events, truncated: false};
    }
  };
  const locator = {
    isVisible: async () => true,
    getAttribute: async () => 'true',
    evaluateAll: async () => candidate ? Array(duplicateTitle ? 2 : 1).fill({url: candidate.url, loaded: true}) : [],
    nth: () => ({evaluate: async () => candidate?.url, downloadMedia: async () => {downloads++; return downloadFile;}})
  };
  const tab = {
    url: async () => target,
    async goto(next) { target = next; trace.push('goto'); sendPage(++page); },
    async close() { trace.push('close'); },
    capabilities: {get: async name => name === 'pageAssets' ? assets : cdp},
    playwright: {getByRole: () => locator, evaluate: async (fn, arg) => {
      if (arg === 'demo') return {handle: arg, secUid, source: 'profile_dom_json'};
      if (Array.isArray(arg)) return candidate ? [candidate] : [];
      if (String(fn).includes('innerWidth')) return {width: 1280, height: 720};
      return {y: page * 800, height: 8000};
    }}
  };
  return {capture: async manifestPath => captureCheckpoint({manifestPath, createTab: async () => tab, clock: () => time}),
    trace, downloads: () => downloads};
}

test('captures verified identity, exact raw bytes, reference entries, and omitted empty terminal', async t => {
  const m = await task(t), f = fixture();
  const result = await f.capture(m.manifestPath);
  assert.equal(result.status, 'READY_TO_RESUME');
  assert.equal(result.results[0].records, 2);
  const evidence = await m.read('demo/capture.local.json');
  assert.deepEqual(evidence.pages.map(item => item.cursor), [0, 1, 3]);
  assert(evidence.pages.every(item => item.secUid === secUid));
  const body = await fs.readFile(path.join(m.directory, 'demo', 'page-0.local.json'));
  assert(body.includes('9007199254740993'));
  assert.equal(createHash('sha256').update(body).digest('hex'), evidence.pages[0].sha256);
  assert.deepEqual(await m.read('ready.local.json'), {task_id: m.id, accounts: ['demo']});
  assert.equal(f.trace.at(-1), 'close');
  assert(!JSON.stringify(evidence).includes('NO_TOKEN_OUTPUT'));
});

test('a continuation from another account cannot finish a checkpoint', async t => {
  const m = await task(t), f = fixture({wrongIdentity: true});
  const result = await f.capture(m.manifestPath);
  assert.equal(result.code, 'CAPTURE_TIMEOUT');
  await assert.rejects(fs.access(path.join(m.directory, 'ready.local.json')));
  await assert.rejects(fs.access(path.join(m.directory, 'demo', 'capture.local.json')));
  assert.equal(f.trace.at(-1), 'close');
});

test('cover recovery without a response URL requires the exact visible drama ID', async t => {
  const m = await task(t, 'covers', [{id: one, title: 'First Drama'}]);
  const local = path.join(m.root, 'asset.bin');
  await fs.writeFile(local, 'synthetic cover');
  const cover = {title: 'First Drama', url: 'https://p16-common-sign.tiktokcdn.com/images/1234.webp', ids: [one]};
  const f = fixture({candidate: cover, downloadFile: local});
  const result = await f.capture(m.manifestPath);
  assert.equal(result.status, 'READY_TO_RESUME');
  assert.equal(f.downloads(), 1);
  const receipt = await m.read('demo/capture.local.json');
  assert.equal(receipt.covers[0].identity_source, 'card_id');
  assert.equal(await fs.readFile(path.join(m.directory, 'demo', `cover-${one}.bin`), 'utf8'), 'synthetic cover');
});

test('cover recovery declines an ambiguous locator or a title without identity', async t => {
  const m = await task(t, 'covers', [{id: one, title: 'First Drama'}]);
  const url = 'https://p19-common-sign.tiktokcdn.com/images/1234.webp';
  for (const options of [{candidate: {title: 'First Drama', url, ids: []}},
    {candidate: {title: 'First Drama', url, ids: [one]}, duplicateTitle: true}]) {
    const f = fixture(options);
    const result = await f.capture(m.manifestPath);
    assert.equal(result.code, 'COVERS_INCOMPLETE');
    assert.equal(f.downloads(), 0);
    assert.deepEqual((await m.read('demo/capture.local.json')).missing_covers, [one]);
  }
});

test('cover recovery matches fresh response URL, batches a rendered asset, and reuses a valid hash on retry', async t => {
  const m = await task(t, 'covers', [{id: one, title: 'First Drama'}]);
  const local = path.join(m.root, 'asset.bin');
  await fs.writeFile(local, 'synthetic cover');
  const assetKey = '53e89763c0514b0298582a6f62156a18';
  const responseUrl = `https://p19-common-sign.tiktokcdn.com/path/${assetKey}~small.webp`;
  const renderedUrl = `https://p16-common-sign.tiktokcdn.com/path/${assetKey}~large.jpeg`;
  const pages = [{...first, dramaList: [{...first.dramaList[0], zoomCover: {'240': responseUrl}}]}, second, terminal];
  const asset = {id: 'rendered', url: renderedUrl, kind: 'image'};
  const bundles = [];
  const assets = {
    list: async () => ({id: 'rendered-assets', assets: [asset]}),
    bundle: async args => { bundles.push(args); return {assets: [{...asset, path: local}]}; }
  };
  const f = fixture({pages, candidate: {title: 'First Drama', url: renderedUrl, ids: []}, assets});
  assert.equal((await f.capture(m.manifestPath)).status, 'READY_TO_RESUME');
  assert.equal(f.downloads(), 0);
  assert.deepEqual(bundles, [{inventoryId: 'rendered-assets', assetIds: ['rendered']}]);
  const receipt = await m.read('demo/capture.local.json');
  assert.equal(receipt.covers[0].identity_source, 'response_url');
  assert.equal(receipt.covers[0].matched_url, responseUrl);
  const next = fixture();
  assert.equal((await next.capture(m.manifestPath)).status, 'READY_TO_RESUME');
  assert.deepEqual((await m.read('demo/capture.local.json')).covers, receipt.covers);
});
