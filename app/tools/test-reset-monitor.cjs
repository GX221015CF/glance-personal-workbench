const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');

const script = fs.readFileSync(path.join(__dirname, '../assets/reset-monitor.js'), 'utf8');
const key = 'workbench-reset-read-v1';
function row(id, timestamp = 10) {
  return `<a class="reset-event" data-reset-revision="${id}" data-reset-timestamp="${timestamp}" href="https://aihot.news/codex-reset" target="_blank"><span data-reset-title>发重置卡 · 已预告</span><span data-reset-time>预告帖 11-12 12:00</span></a>`;
}
function widget(rows) {
  return `<section class="live-resets"><header class="widget-header"><a href="https://aihot.news/codex-reset">TIBO</a></header><div class="widget-content"><a data-reset-open-all href="https://aihot.news/codex-reset">日历</a><div class="reset-events">${rows}</div></div></section>`;
}
function setup(html, saved = null, blocked = false) {
  const dom = new JSDOM(html, {url: 'http://127.0.0.1:8180/', runScripts: 'outside-only'});
  if (saved !== null) dom.window.localStorage.setItem(key, saved);
  if (blocked) Object.defineProperty(dom.window, 'localStorage', {get() { throw new Error('blocked'); }});
  dom.window.document.addEventListener('click', event => event.preventDefault());
  dom.window.document.addEventListener('auxclick', event => event.preventDefault());
  dom.window.eval(script);
  return dom;
}
const settle = () => new Promise(resolve => setImmediate(resolve));

test('unread messages have one warm dot each and sort newest first', async () => {
  const dom = setup(widget(row('old', 1) + row('new', 20)));
  const doc = dom.window.document;
  assert.deepEqual([...doc.querySelectorAll('.reset-event')].map(n => n.dataset.resetRevision), ['new', 'old']);
  assert.equal(doc.querySelectorAll('.reset-unread-dot').length, 2);
  assert.match(doc.querySelector('.reset-event').getAttribute('aria-label'), /未查看/);
  await settle();
  assert.equal(doc.querySelectorAll('.reset-unread-dot').length, 2);
  dom.window.close();
});

test('clicking a message persists only that revision and preserves its link', async () => {
  const dom = setup(widget(row('one') + row('two')));
  const first = dom.window.document.querySelector('.reset-event');
  first.dispatchEvent(new dom.window.MouseEvent('click', {bubbles: true, cancelable: true}));
  assert.equal(first.querySelector('.reset-unread-dot'), null);
  assert.equal(first.href, 'https://aihot.news/codex-reset');
  assert.equal(dom.window.document.querySelectorAll('.reset-unread-dot').length, 1);
  const saved = dom.window.localStorage.getItem(key);
  assert.deepEqual(JSON.parse(saved), ['one']);
  await settle();
  dom.window.close();
  const reopened = setup(widget(row('one') + row('two')), saved);
  assert.equal(reopened.window.document.querySelectorAll('.reset-unread-dot').length, 1);
  await settle();
  reopened.window.close();
});

test('background replacement preserves read state, new revisions notify again', async () => {
  const dom = setup(widget(row('seen')), '["seen"]');
  const content = dom.window.document.querySelector('.reset-events');
  content.innerHTML = row('seen') + row('new', 20);
  await settle();
  assert.equal(dom.window.document.querySelectorAll('.reset-unread-dot').length, 1);
  content.innerHTML = row('confirmed', 30);
  await settle();
  assert.equal(dom.window.document.querySelectorAll('.reset-unread-dot').length, 1);
  dom.window.close();
});

test('calendar and widget title mark the currently displayed messages read', async () => {
  for (const selector of ['[data-reset-open-all]', '.widget-header a']) {
    const dom = setup(widget(row('one') + row('two')));
    dom.window.document.querySelector(selector).dispatchEvent(new dom.window.MouseEvent('click', {bubbles: true, cancelable: true}));
    assert.equal(dom.window.document.querySelectorAll('.reset-unread-dot').length, 0);
    await settle();
    dom.window.close();
  }
});

test('late widget insertion, malformed storage and blocked storage remain usable', async () => {
  const dom = setup('<main></main>', '{invalid');
  dom.window.document.querySelector('main').innerHTML = widget(row('new'));
  await settle();
  assert.equal(dom.window.document.querySelectorAll('.reset-unread-dot').length, 1);
  dom.window.close();
  const blocked = setup(widget(row('new')), null, true);
  blocked.window.document.querySelector('.reset-event').dispatchEvent(new blocked.window.MouseEvent('click', {bubbles: true, cancelable: true}));
  assert.equal(blocked.window.document.querySelectorAll('.reset-unread-dot').length, 0);
  await settle();
  blocked.window.close();
});

test('middle click marks read, right click waits, and storage changes sync tabs', async () => {
  const dom = setup(widget(row('one') + row('two')));
  const first = dom.window.document.querySelector('.reset-event');
  first.dispatchEvent(new dom.window.MouseEvent('auxclick', {button: 2, bubbles: true, cancelable: true}));
  assert.equal(dom.window.document.querySelectorAll('.reset-unread-dot').length, 2);
  first.dispatchEvent(new dom.window.MouseEvent('auxclick', {button: 1, bubbles: true, cancelable: true}));
  assert.equal(dom.window.document.querySelectorAll('.reset-unread-dot').length, 1);
  dom.window.localStorage.setItem(key, '["one","two"]');
  dom.window.dispatchEvent(new dom.window.StorageEvent('storage', {key}));
  assert.equal(dom.window.document.querySelectorAll('.reset-unread-dot').length, 0);
  await settle();
  dom.window.close();
});

test('cached records without revision retain stable read identity after dot updates', async () => {
  const dom = setup(widget(row('').replace('data-reset-revision=""', '')));
  const first = dom.window.document.querySelector('.reset-event');
  first.dispatchEvent(new dom.window.MouseEvent('click', {bubbles: true, cancelable: true}));
  await settle();
  assert.equal(dom.window.document.querySelectorAll('.reset-unread-dot').length, 0);
  assert.equal(JSON.parse(dom.window.localStorage.getItem(key)).length, 1);
  dom.window.close();
});
