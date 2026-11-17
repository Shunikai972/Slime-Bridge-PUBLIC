// Execute the real updater action with explicit deferred API/timer boundaries.
const fs = require('fs'), assert = require('assert');
const source = fs.readFileSync(process.argv[2], 'utf8');
const start = source.indexOf("    'update-apply': async () => {");
const end = source.indexOf("    'mods-realm':", start);
assert(start >= 0 && end > start);
const L = { update: { frozen: true } }, timers = new Map(), messages = [];
const progressNode = { textContent: '' }, $ = selector => { assert.equal(selector, '#update-progress'); return progressNode; };
let page = 'options', rendered = 0, applies = 0, statusCalls = 0, restartAcks = 0, finish;
const api = { update_apply: () => { applies++; return new Promise(resolve => { finish = resolve; }); },
    update_status: async () => { statusCalls++; return { ok: true, phase: 'download', done: 1048576, total: 2097152 }; },
    update_restart_ack: async () => { restartAcks++; return { ok: true }; } };
const renderView = () => { rendered++; }, toast = (...args) => messages.push(args), t = key => key;
const safe = async fn => { try { return await fn(); } catch (_) { return undefined; } };
const setInterval = fn => { timers.set(1, fn); return 1; }, clearInterval = id => timers.delete(id);
const action = eval('({' + source.slice(start, end) + '})')["update-apply"];
(async () => {
    const first = action();
    assert(L.updateBusy); assert.equal(applies, 1); assert.equal(timers.size, 1);
    await action(); assert.equal(applies, 1, 'double click must not start a second download');
    const pendingTimer = timers.get(1);
    const rendersBeforeProgress = rendered;
    await pendingTimer(); assert.equal(L.updateProgress.done, 1048576); assert.equal(statusCalls, 1);
    assert.equal(rendered, rendersBeforeProgress, 'progress must not replace the page and restart its500ms entry animation');
    assert(progressNode.textContent.includes('1.0 MiB / 2.0 MiB'));
    finish({ ok: false, error: 'DOWNLOAD_FAILED' }); await first;
    assert(!L.updateBusy); assert.equal(timers.size, 0); assert.equal(L.updateProgress, null);
    await pendingTimer(); assert.equal(statusCalls, 1, 'late timer must not poll or resurrect completed progress');
    const second = action(); assert.equal(applies, 2); finish({ ok: true, version: '9.9.9' }); await second;
    assert(!L.updateBusy); assert.equal(timers.size, 0);
    assert.equal(restartAcks, 1, 'successful API response must be received before acknowledging restart');
    assert(messages.some(m => m[0] === 'upd.restarting')); assert(rendered >= 4);
    console.log('UPDATE_ACTION_PASS duplicate-click/progress/failure/retry/late-timer-cleanup');
})().catch(error => { console.error(error); process.exitCode = 1; });
