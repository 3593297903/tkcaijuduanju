/** Observe whole-account Short dramas responses in the user's authorized Chrome. */
import {createHash, randomUUID} from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import {CaptureError, NETWORK_METHODS, captureRequestSpec, requestInfo, responseBody, validateDramaResponse} from './chrome_onboarding.mjs';

const ORIGIN = 'https://www.tiktok.com';
const HANDLE = /^[A-Za-z0-9_.-]+$/;
const DRAMA_ID = /^[0-9]{19}$/;
const MAX_COVER_BYTES = 8_000_000;
const SCROLL_INTERVAL = 1000;
const CONTINUATION_OPTIONS = {allowReferencesOnly: true, allowEmptyTerminal: true};
const digest = bytes => createHash('sha256').update(bytes).digest('hex');

async function writePrivate(file, value) {
  const temporary = `${file}.${randomUUID()}.tmp`;
  try {
    await fs.writeFile(temporary, value, {mode: 0o600, flag: 'wx'});
    await fs.rename(temporary, file);
  } finally {
    await fs.unlink(temporary).catch(() => {});
  }
}

async function privateDirectory(folder) {
  await fs.mkdir(folder, {recursive: true});
  if (path.relative(folder, await fs.realpath(folder)) !== '') {
    throw new CaptureError('PRIVATE_OUTPUT', 'The browser output path must stay in the private task directory.');
  }
}

async function readTask(manifestPath) {
  const file = path.resolve(manifestPath);
  const task = JSON.parse(await fs.readFile(file, 'utf8'));
  const id = task.task_id;
  const directory = path.join(path.dirname(file), `${id}.browser`);
  const accounts = task.selected_accounts;
  const maxPages = task.max_pages ?? 100;
  if (task.schema !== 'tkmonitor.browser-fallback/v1' || typeof id !== 'string' || !/^[0-9a-f]{32}$/.test(id)
      || path.basename(file) !== `${id}.browser.local.json` || typeof task.output_dir !== 'string'
      || path.resolve(task.output_dir) !== directory || !['collect', 'covers'].includes(task.phase)
      || !Array.isArray(accounts) || !accounts.length || new Set(accounts).size !== accounts.length
      || accounts.some(account => typeof account !== 'string' || !HANDLE.test(account) || ['.', '..'].includes(account))
      || !Number.isFinite(Date.parse(task.started_at)) || !Number.isFinite(task.browser_timeout_seconds)
      || task.browser_timeout_seconds < 5 || task.browser_timeout_seconds > 300
      || !Number.isInteger(maxPages) || maxPages < 1 || maxPages > 1000) {
    throw new CaptureError('MANIFEST_INVALID', 'The private browser fallback task is invalid.');
  }
  for (const account of accounts) {
    const entries = task.missing_covers?.[account] ?? [];
    if (!Array.isArray(entries) || new Set(entries.map(item => item?.id)).size !== entries.length
        || entries.some(item => typeof item?.id !== 'string' || !DRAMA_ID.test(item.id)
            || typeof item.title !== 'string' || !item.title.trim())
        || task.phase === 'covers' && !entries.length) {
      throw new CaptureError('MANIFEST_INVALID', 'The requested cover list is invalid.');
    }
  }
  return {...task, directory, maxPages};
}

function readPage(body, requestedCursor, continuation) {
  let value;
  try { value = JSON.parse(body); }
  catch { throw new CaptureError('INVALID_JSON', 'The browser did not return valid drama JSON.'); }
  validateDramaResponse(value, requestedCursor, continuation ? CONTINUATION_OPTIONS : undefined);
  const following = Number(value.cursor);
  if (!Number.isSafeInteger(following)) {
    throw new CaptureError('RESPONSE_INVALID', 'The browser response has a cursor outside the supported range.');
  }
  return {following, hasMore: value.hasMore, items: value.dramaList ?? []};
}

function validateResponse(entry) {
  if (entry.response.status !== 200 || entry.response.url && entry.response.url !== entry.url) {
    throw new CaptureError('HTTP_REJECTED', 'The normal browser drama response was not successful.');
  }
  const mime = entry.response.mimeType
    ?? Object.entries(entry.response.headers ?? {}).find(([key]) => key.toLowerCase() === 'content-type')?.[1];
  if (!/application\/json|\+json/i.test(mime ?? '')) {
    throw new CaptureError('NON_JSON', 'The normal browser drama response was not JSON.');
  }
}

function collectEvents(events, pending, secUid, expected) {
  for (const event of events) {
    const params = event.params ?? {};
    const id = params.requestId;
    if (typeof id !== 'string') continue;
    if (event.method === 'Network.requestWillBeSent') {
      const info = requestInfo(params.request ?? {});
      const requested = Number(info?.cursor);
      if (!params.redirectResponse && info?.secUid === secUid && Number.isSafeInteger(requested) && requested >= expected) {
        pending.set(id, {requested, url: params.request.url});
      } else {
        pending.delete(id);
      }
      continue;
    }
    const entry = pending.get(id);
    if (!entry) continue;
    if (event.method === 'Network.responseReceived') entry.response = params.response;
    if (event.method === 'Network.loadingFinished') entry.finished = true;
    if (event.method === 'Network.loadingFailed') pending.delete(id);
  }
}

function budget(deadline, clock, cap) {
  const remaining = Math.floor(deadline - clock());
  if (remaining <= 0) throw new CaptureError('CAPTURE_TIMEOUT', 'The browser fallback reached its time limit.');
  return Math.min(cap, remaining);
}

async function wheel(tab) {
  const dimensions = await tab.playwright.evaluate(() => ({width: innerWidth, height: innerHeight}));
  if (!Number.isFinite(dimensions?.width) || !Number.isFinite(dimensions?.height)
      || dimensions.width < 2 || dimensions.height < 2) {
    throw new CaptureError('BROWSER_WINDOW', 'Could not locate a visible browser viewport to scroll.');
  }
  return {type: 'mouseWheel', x: Math.floor(dimensions.width * 0.68), y: Math.floor(dimensions.height * 0.69), deltaX: 0,
    deltaY: Math.max(600, Math.min(2500, Math.floor(dimensions.height * 1.8)))};
}

function mediaKey(value) {
  try {
    const url = new URL(value);
    const host = url.hostname;
    if (url.protocol !== 'https:' || url.username || url.password || url.port || url.hash
        || !(host.endsWith('.tiktokcdn.com') || /^p(?:16|19)-common-sign\.tiktokcdn-us\.com$/.test(host))) return null;
    const key = url.pathname.split('/').at(-1).split('~')[0];
    return /^[a-f0-9]{32,64}$/i.test(key) ? key.toLowerCase() : `${host}${url.pathname}`;
  } catch {
    return null;
  }
}

function thumbnailUrl(item) {
  return [item.zoomCover?.['240'], item.cover?.['240'], item.thumbnail_url,
    ...(Array.isArray(item.cover?.urlList) ? item.cover.urlList : []), item.cover].find(url => typeof url === 'string' && mediaKey(url));
}

async function saveCover(folder, target, file, url, binding) {
  if (typeof file !== 'string' || !path.isAbsolute(file)) return null;
  const stat = await fs.stat(file).catch(() => null);
  if (!stat?.isFile() || stat.size < 1 || stat.size > MAX_COVER_BYTES) return null;
  // The handoff requires the original bounded bytes, not a remote URL or a screenshot.
  const bytes = await fs.readFile(file);
  if (bytes.length !== stat.size) return null;
  await writePrivate(path.join(folder, `cover-${target.id}.bin`), bytes);
  return {id: target.id, title: target.title, url, ...binding, sha256: digest(bytes)};
}

async function recoverSavedCovers(folder, targets) {
  const file = path.join(folder, 'covers-progress.local.json');
  let results;
  try { results = JSON.parse(await fs.readFile(file, 'utf8')); }
  catch (error) { if (error.code === 'ENOENT') return []; throw error; }
  if (!Array.isArray(results)) return [];
  const saved = [];
  for (const result of results) {
    const target = targets.get(result?.id);
    if (!target || target.title !== result.title || !mediaKey(result.url)) continue;
    if (!(result.identity_source === 'card_id' && result.card_id === target.id
        || result.identity_source === 'response_url' && mediaKey(result.matched_url) === mediaKey(result.url))) continue;
    const image = path.join(folder, `cover-${target.id}.bin`);
    const stat = await fs.stat(image).catch(() => null);
    if (stat?.isFile() && stat.size > 0 && stat.size <= MAX_COVER_BYTES && digest(await fs.readFile(image)) === result.sha256) {
      saved.push([target.id, result]);
    }
  }
  return saved;
}

async function saveVisibleCovers(tab, folder, targets, covers, deadline, clock) {
  const open = [...targets.values()].filter(target => !covers.has(target.id));
  if (!open.length) return;
  const available = await tab.playwright.evaluate(titles => {
    const wanted = new Set(titles);
    const found = [];
    for (const image of document.querySelectorAll('img[alt]')) {
      if (!wanted.has(image.alt) || !image.complete || image.naturalWidth <= 0 || !image.getClientRects().length) continue;
      const ids = new Set();
      const remember = value => { if (/^[0-9]{19}$/.test(value ?? '')) ids.add(value); };
      for (let element = image, level = 0; element && level < 7; element = element.parentElement, level++) {
        // Stop before a common ancestor shared by unrelated cards.
        if (element !== image && element.querySelectorAll('img[alt]').length > 1) break;
        for (const name of ['data-drama-id', 'data-dramaid', 'data-series-id']) remember(element.getAttribute(name));
        const e2e = element.getAttribute('data-e2e') ?? '';
        remember(/^(?:drama|short-drama)-(?:card|item)[_-]([0-9]{19})$/.exec(e2e)?.[1]);
        if (element.matches('a[href]')) {
          const link = new URL(element.href, document.baseURI);
          if (link.origin === location.origin && /(?:^|\/)(?:drama|short-drama|short-dramas|series)(?:\/|$)/i.test(link.pathname)) {
            for (const name of ['dramaID', 'dramaId', 'drama_id', 'series_id']) remember(link.searchParams.get(name));
            remember(/(?:^|[/-])([0-9]{19})(?:\/|$)/.exec(link.pathname)?.[1]);
          }
        }
      }
      found.push({title: image.alt, url: image.currentSrc || image.src, ids: [...ids]});
    }
    return found;
  }, open.map(target => target.title));
  const eligible = [];
  for (const target of open) {
    const responseUrl = mediaKey(target.thumbnail_url) ? target.thumbnail_url : target.observedThumbnail;
    const expected = mediaKey(responseUrl);
    const possible = available.filter(image => image.title === target.title && mediaKey(image.url)
      && (expected && mediaKey(image.url) === expected || image.ids.length === 1 && image.ids[0] === target.id));
    const keys = new Set(possible.map(image => mediaKey(image.url)));
    if (keys.size !== 1) continue;
    const key = keys.values().next().value;
    const identity_source = key === expected ? 'response_url' : 'card_id';
    const binding = identity_source === 'response_url' ? {identity_source, matched_url: responseUrl}
      : {identity_source, card_id: target.id};
    eligible.push({target, key, binding});
  }
  if (!eligible.length || clock() >= deadline) return;
  const savedCount = covers.size;
  let inventory, assets;
  try {
    assets = await tab.capabilities.get('pageAssets');
    if (typeof assets?.list === 'function' && typeof assets?.bundle === 'function') inventory = await assets.list();
  } catch { /* The exact rendered-image locator remains available on other runtimes. */ }
  if (inventory?.assets?.length && clock() < deadline) {
    const batch = [];
    for (const candidate of eligible) {
      const matches = inventory.assets.filter(asset => asset.kind === 'image' && mediaKey(asset.url) === candidate.key);
      if (matches.length === 1) batch.push({...candidate, asset: matches[0]});
    }
    for (let index = 0; index < batch.length && clock() < deadline; index += 20) {
      const group = batch.slice(index, index + 20);
      let bundle;
      try { bundle = await assets.bundle({inventoryId: inventory.id, assetIds: group.map(item => item.asset.id)}); }
      catch { break; }
      for (const {target, asset, binding} of group) {
        const file = bundle?.assets?.find(item => item.id === asset.id && mediaKey(item.url) === mediaKey(asset.url));
        if (!file) continue;
        const saved = await saveCover(folder, target, file.path, asset.url, binding);
        if (saved) covers.set(target.id, saved);
      }
    }
  }
  for (const {target, key, binding} of eligible) {
    if (clock() >= deadline) break;
    if (covers.has(target.id)) continue;
    const locator = tab.playwright.getByRole('img', {name: target.title, exact: true});
    const candidates = await locator.evaluateAll(images => images.map(image => ({url: image.currentSrc || image.src,
      loaded: image.complete && image.naturalWidth > 0})));
    const matches = candidates.map((image, index) => image.loaded && mediaKey(image.url) === key ? index : -1).filter(index => index >= 0);
    if (matches.length !== 1) continue;
    const selected = locator.nth(matches[0]);
    const url = await selected.evaluate(image => image.currentSrc || image.src);
    if (mediaKey(url) !== key || typeof selected.downloadMedia !== 'function') continue;
    let file;
    try { file = await selected.downloadMedia({timeoutMs: budget(deadline, clock, 7500)}); }
    catch { continue; }
    if (mediaKey(await selected.evaluate(image => image.currentSrc || image.src).catch(() => null)) !== key) continue;
    const saved = await saveCover(folder, target, file, url, binding);
    if (saved) covers.set(target.id, saved);
  }
  if (covers.size > savedCount) {
    await writePrivate(path.join(folder, 'covers-progress.local.json'), `${JSON.stringify([...covers.values()])}\n`);
  }
}

function observeCoverUrls(items, targets) {
  for (const item of items) {
    const target = targets.get(item.dramaID);
    if (!target || target.title !== item.dramaName) continue;
    const url = thumbnailUrl(item);
    if (url) target.observedThumbnail = url;
  }
}

async function captureAccount(task, account, folder, createTab, clock) {
  const start = clock();
  const deadline = start + task.browser_timeout_seconds * 1000;
  const targets = new Map((task.missing_covers?.[account] ?? []).map(item => [item.id, item]));
  const pages = [];
  const covers = new Map(task.phase === 'covers' ? await recoverSavedCovers(folder, targets) : []);
  const identifiers = new Set();
  const references = new Set();
  let identity, lastRead;
  const tab = await createTab(`${ORIGIN}/`);
  try {
    await captureRequestSpec({tab, account, navigate: true, initialPageOnly: true, clock,
      timeoutMs: budget(deadline, clock, 90000), maxPages: task.maxPages,
      onVerified: async ({cdp, eventCursor, events, body, evidence}) => {
        identity = evidence.identity;
        const first = readPage(body, 0, false);
        if (task.phase === 'collect') {
          const savePage = async (requested, raw, decoded) => {
            for (const item of decoded.items) {
              if (Object.keys(item).length === 1) {
                references.add(item.dramaID);
              } else {
                if (identifiers.has(item.dramaID)) throw new CaptureError('DUPLICATE_DRAMA', 'A drama was returned twice.');
                identifiers.add(item.dramaID);
              }
            }
            lastRead = new Date(clock()).toISOString();
            await writePrivate(path.join(folder, `page-${requested}.local.json`), raw);
            pages.push({cursor: requested, observed_at: lastRead, secUid: identity.secUid, sha256: digest(raw)});
          };
          await savePage(0, body, first);
          if (!first.hasMore) return;
          const input = await wheel(tab);
          const pending = new Map();
          let cursor = eventCursor;
          let received = events;
          let lastScroll = -Infinity;
          let next = first;
          while (next.hasMore) {
            if (pages.length >= task.maxPages) throw new CaptureError('PAGE_LIMIT', 'The browser reached its page limit before the terminal page.');
            collectEvents(received, pending, identity.secUid, next.following);
            const requested = next.following;
            let advanced = false;
            for (const [id, entry] of pending) {
              if (entry.requested < requested) pending.delete(id);
              if (entry.requested !== requested || !entry.response || !entry.finished) continue;
              validateResponse(entry);
              const raw = await responseBody(cdp, id, budget(deadline, clock, 5000));
              next = readPage(raw, requested, true);
              await savePage(requested, raw, next);
              pending.delete(id);
              advanced = true;
              break;
            }
            if (advanced) {
              received = [];
              continue;
            }
            if (clock() - lastScroll >= SCROLL_INTERVAL) {
              await cdp.send('Input.dispatchMouseEvent', input, {timeoutMs: budget(deadline, clock, 4000)});
              lastScroll = clock();
            }
            const batch = await cdp.readEvents({afterSequence: cursor, methods: NETWORK_METHODS, limit: 1000,
              timeoutMs: budget(deadline, clock, 500)});
            if (batch.truncated) throw new CaptureError('EVENTS_INCOMPLETE', 'The browser event history was truncated.');
            cursor = batch.cursor;
            received = batch.events;
          }
        } else {
          observeCoverUrls(first.items, targets);
          const input = await wheel(tab);
          const pending = new Map();
          let next = first;
          let cursor = eventCursor;
          let received = events;
          let gap = 0;
          while (clock() < deadline && covers.size < targets.size && gap < 6) {
            collectEvents(received, pending, identity.secUid, next.following);
            let advanced = false;
            for (const [id, entry] of pending) {
              if (entry.requested < next.following) pending.delete(id);
              if (!next.hasMore || entry.requested !== next.following || !entry.response || !entry.finished) continue;
              validateResponse(entry);
              next = readPage(await responseBody(cdp, id, budget(deadline, clock, 5000)), entry.requested, true);
              observeCoverUrls(next.items, targets);
              pending.delete(id);
              advanced = true;
            }
            if (advanced) gap = 0;
            await saveVisibleCovers(tab, folder, targets, covers, deadline, clock);
            if (covers.size === targets.size || clock() >= deadline) break;
            const before = await tab.playwright.evaluate(() => ({y: scrollY, height: document.documentElement.scrollHeight}));
            await cdp.send('Input.dispatchMouseEvent', input, {timeoutMs: budget(deadline, clock, 4000)});
            const batch = await cdp.readEvents({afterSequence: cursor, methods: NETWORK_METHODS, limit: 1000,
              timeoutMs: budget(deadline, clock, 500)}).catch(error => {
              if (clock() >= deadline) return null;
              throw error;
            });
            if (!batch) break;
            if (batch.truncated) throw new CaptureError('EVENTS_INCOMPLETE', 'The browser event history was truncated.');
            cursor = batch.cursor;
            received = batch.events;
            const after = await tab.playwright.evaluate(() => ({y: scrollY, height: document.documentElement.scrollHeight}));
            gap = before.y === after.y && before.height === after.height ? gap + 1 : 0;
          }
          lastRead = new Date(clock()).toISOString();
        }
      }});
    if (task.phase === 'collect' && [...references].some(id => !identifiers.has(id))) {
      throw new CaptureError('UNRESOLVED_DRAMA_REFERENCE', 'A referenced drama had no complete response.');
    }
    const current = new URL(await tab.url());
    if (current.origin !== ORIGIN || current.pathname.replace(/\/$/, '') !== `/@${account}`) {
      throw new CaptureError('TARGET_MISMATCH', 'The browser left the intended account during fallback.');
    }
    const missing = [...targets.keys()].filter(id => !covers.has(id));
    const result = {schema: 'tkmonitor.browser-checkpoint/v1', task_id: task.task_id, account,
      visible_account: account, identity, start_at: new Date(start).toISOString(), last_read_at: lastRead,
      pages, covers: [...covers.values()], missing_covers: missing};
    await writePrivate(path.join(folder, 'capture.local.json'), `${JSON.stringify(result, null, 2)}\n`);
    return {account, pages: pages.length, records: identifiers.size, covers: covers.size, missing_covers: missing.length};
  } finally {
    await tab.close().catch(() => {});
  }
}

/** The factory must return only a temporary tab owned by this capture. */
export async function captureCheckpoint({manifestPath, createTab, clock = Date.now}) {
  const task = await readTask(manifestPath);
  if (typeof createTab !== 'function') return {status: 'NEEDS_BROWSER', code: 'BROWSER_UNAVAILABLE', manifest: manifestPath};
  await privateDirectory(task.directory);
  const lock = path.join(task.directory, 'capture.lock');
  try { await fs.writeFile(lock, `${new Date(clock()).toISOString()}\n`, {mode: 0o600, flag: 'wx'}); }
  catch (error) {
    if (error.code === 'EEXIST') return {status: 'NEEDS_BROWSER_ACTION', code: 'CAPTURE_ALREADY_RUNNING', manifest: manifestPath};
    throw error;
  }
  const results = [];
  const ready = path.join(task.directory, 'ready.local.json');
  try {
    await fs.unlink(ready).catch(error => { if (error.code !== 'ENOENT') throw error; });
    for (const account of task.selected_accounts) {
      const accountDirectory = path.join(task.directory, account);
      await privateDirectory(accountDirectory);
      const marker = path.join(accountDirectory, 'capture.local.json');
      await fs.unlink(marker).catch(error => { if (error.code !== 'ENOENT') throw error; });
    }
    for (const account of task.selected_accounts) {
      results.push(await captureAccount(task, account, path.join(task.directory, account), createTab, clock));
    }
    await writePrivate(ready, `${JSON.stringify({task_id: task.task_id, accounts: task.selected_accounts})}\n`);
    if (task.phase === 'covers' && results.some(result => result.missing_covers)) {
      return {status: 'NEEDS_BROWSER_ACTION', code: 'COVERS_INCOMPLETE', manifest: manifestPath, results};
    }
    return {status: 'READY_TO_RESUME', manifest: manifestPath, phase: task.phase, results};
  } catch (error) {
    return {status: 'NEEDS_BROWSER_ACTION', code: error instanceof CaptureError ? error.code : 'BROWSER_RUNTIME_ERROR',
      manifest: manifestPath, results};
  } finally {
    await fs.unlink(lock).catch(() => {});
  }
}
