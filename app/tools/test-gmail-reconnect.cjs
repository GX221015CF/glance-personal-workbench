const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');

const script = fs.readFileSync(path.join(__dirname, '../assets/gmail.js'), 'utf8');
const expired = {id: 'a'.repeat(20), email: 'one@example.test', status: '需要重新授权',
  needsReconnect: true, stale: true, available: true, avatarStatus: 'ready', unread: 2};
const settle = async () => { for (let i = 0; i < 6; i++) await new Promise(resolve => setImmediate(resolve)); };

function setup(accounts = [expired], options = {}) {
  let savedAccounts = accounts;
  const dom = new JSDOM(`<div id="gmail-dashboard">
    <span id="gmail-account-count"></span><div id="gmail-accounts"></div>
    <button id="gmail-add"></button><button id="gmail-refresh"></button>
    <p id="gmail-notice"></p><details id="gmail-setup"></details>
    <h3 id="gmail-list-title"></h3><input id="gmail-unread" type="checkbox">
    <div id="gmail-messages"></div></div>`, {url: 'http://127.0.0.1:8180/gmail', runScripts: 'outside-only'});
  const calls = [], opened = [], confirmations = [];
  dom.window.fetch = async (url, init = {}) => {
    calls.push({url, method: init.method || 'GET'});
    if (url.endsWith('/api/desktop')) return {ok: true, json: async () => ({token: 'test-session'})};
    if (url.endsWith('/api/gmail')) return {ok: true, json: async () => ({configured: options.configured !== false, accounts: savedAccounts, messages: [], refreshing: false})};
    if (options.failure) return {ok: false, json: async () => ({message: '连接失败，请重试'})};
    if (options.wait) await options.wait;
    if (url.includes('/api/gmail/remove/')) {
      savedAccounts = savedAccounts.filter(account => account.id !== url.split('/').at(-1));
      return {ok: true, json: async () => ({removed: true, snapshot: {configured: true, accounts: savedAccounts, messages: [], refreshing: false}})};
    }
    return {ok: true, json: async () => ({authorizationUrl: options.url || 'https://accounts.google.com/o/oauth2/v2/auth?state=test'})};
  };
  dom.window.open = (url) => { opened.push(url); return null; };
  dom.window.confirm = message => { confirmations.push(message); return options.confirm !== false; };
  dom.window.eval(script);
  return {dom, calls, opened, confirmations, doc: dom.window.document};
}

test('expired account with an existing avatar has its own reconnect button', async () => {
  const ui = setup();
  try {
    await settle();
    const button = ui.doc.querySelector('[data-gmail-reconnect]');
    assert.ok(button, 'expired account needs a visible reconnect action');
    assert.match(button.textContent, /重新连接/);
    assert.equal(button.dataset.gmailReconnect, expired.id);
  } finally { ui.dom.window.close(); }
});

test('reconnect requests the selected ID and opens the actual Google URL with a fallback link', async () => {
  const ui = setup();
  try {
    await settle();
    ui.doc.querySelector('[data-gmail-reconnect]').click();
    await settle();
    assert.ok(ui.calls.some(call => call.method === 'POST' && call.url.endsWith('/api/gmail/reconnect/' + expired.id)));
    assert.equal(ui.opened.length, 1);
    assert.ok(ui.opened[0].startsWith('https://accounts.google.com/'));
    assert.ok(!ui.opened.includes('about:blank'));
    const link = ui.doc.querySelector('#gmail-auth a');
    assert.equal(link.href, ui.opened[0]);
    assert.equal(link.target, '_blank');
    assert.equal(link.rel, 'noopener noreferrer');
    ui.doc.querySelector('[data-gmail-reconnect]').click();
    await settle();
    assert.equal(ui.calls.filter(call => call.method === 'POST').length, 2);
    assert.equal(ui.opened.length, 2);
  } finally { ui.dom.window.close(); }
});

test('preparation disables duplicate clicks and preserves the account and unread filters', async () => {
  let release;
  const ui = setup([expired], {wait: new Promise(resolve => { release = resolve; })});
  try {
    await settle();
    ui.doc.querySelectorAll('.gmail-account')[1].click();
    ui.doc.querySelector('#gmail-unread').checked = true;
    ui.doc.querySelector('[data-gmail-reconnect]').click();
    await settle();
    assert.equal(ui.doc.querySelector('[data-gmail-reconnect]').disabled, true);
    assert.equal(ui.doc.querySelector('#gmail-add').disabled, true);
    release();
    await settle();
    assert.equal(ui.doc.querySelector('.gmail-account.is-active strong').textContent, expired.email);
    assert.equal(ui.doc.querySelector('#gmail-unread').checked, true);
    assert.equal(ui.doc.querySelector('[data-gmail-reconnect]').disabled, false);
  } finally { release(); ui.dom.window.close(); }
});

test('a failed preparation reports a retry and re-enables reconnect', async () => {
  const ui = setup([expired], {failure: true});
  try {
    await settle();
    ui.doc.querySelector('[data-gmail-reconnect]').click();
    await settle();
    assert.match(ui.doc.querySelector('#gmail-auth').textContent, /连接失败.*重试/);
    assert.equal(ui.doc.querySelector('[data-gmail-reconnect]').disabled, false);
    assert.equal(ui.opened.length, 0);
    ui.doc.querySelector('[data-gmail-reconnect]').click();
    await settle();
    assert.equal(ui.calls.filter(call => call.method === 'POST').length, 2);
  } finally { ui.dom.window.close(); }
});

test('add account also works when Electron returns null for external window.open', async () => {
  const ui = setup([]);
  try {
    await settle();
    ui.doc.querySelector('#gmail-add').click();
    await settle();
    assert.ok(ui.calls.some(call => call.url.endsWith('/api/gmail/connect') && call.method === 'POST'));
    assert.ok(ui.doc.querySelector('#gmail-auth a'));
    assert.doesNotMatch(ui.doc.querySelector('#gmail-auth').textContent, /请允许此页面/);
  } finally { ui.dom.window.close(); }
});

test('legacy expired state is explained and missing client disables authorization', async () => {
  const ui = setup([{...expired, needsReconnect: undefined}], {configured: false});
  try {
    await settle();
    assert.ok(ui.doc.querySelector('[data-gmail-reconnect]'));
    assert.equal(ui.doc.querySelector('[data-gmail-reconnect]').disabled, true);
    assert.equal(ui.doc.querySelector('#gmail-setup').open, true);
  } finally { ui.dom.window.close(); }
});

test('invalid Google destinations are rejected without navigation', async () => {
  for (const url of ['https://accounts.google.com.attacker.test/', 'https://user:secret@accounts.google.com/', 'javascript:alert(1)']) {
    const ui = setup([expired], {url});
    try {
      await settle();
      ui.doc.querySelector('[data-gmail-reconnect]').click();
      await settle();
      assert.equal(ui.opened.length, 0);
      assert.equal(ui.doc.querySelector('#gmail-auth a'), null);
      assert.match(ui.doc.querySelector('#gmail-auth').textContent, /登录地址无效/);
    } finally { ui.dom.window.close(); }
  }
});

test('connected accounts can manually reconnect without hiding the continuation link', async () => {
  const ui = setup([{...expired, status: '已连接', needsReconnect: false, stale: false, checkedAt: 'before'}]);
  try {
    await settle();
    ui.doc.querySelector('[data-gmail-reconnect]').click();
    await settle();
    assert.ok(ui.doc.querySelector('#gmail-auth a'));
  } finally { ui.dom.window.close(); }
});

test('returning after successful authorization updates status and keeps selected filters', async () => {
  const accounts = [{...expired, checkedAt: 'before'}];
  const ui = setup(accounts);
  try {
    await settle();
    ui.doc.querySelectorAll('.gmail-account')[1].click();
    ui.doc.querySelector('[data-gmail-reconnect]').click();
    await settle();
    Object.assign(accounts[0], {status: '已连接', needsReconnect: false, stale: false, checkedAt: 'after'});
    ui.dom.window.dispatchEvent(new ui.dom.window.Event('focus'));
    await settle();
    assert.match(ui.doc.querySelector('#gmail-auth').textContent, /已重新连接/);
    assert.equal(ui.doc.querySelector('#gmail-auth a'), null);
    assert.equal(ui.doc.querySelector('.gmail-account.is-active strong').textContent, expired.email);
    assert.doesNotMatch(ui.doc.querySelector('#gmail-notice').textContent, /需要重新授权/);
  } finally { ui.dom.window.close(); }
});

test('account identity and both actions share a selected rectangular card', async () => {
  const ui = setup();
  try {
    await settle();
    ui.doc.querySelectorAll('.gmail-account')[1].click();
    const card = ui.doc.querySelector('.gmail-account-card.is-active');
    assert.equal(card.dataset.gmailAccount, expired.id);
    assert.ok(card.querySelector('.gmail-account-with-avatar'));
    const actions = card.querySelector('.gmail-account-actions');
    assert.deepEqual([...actions.children].map(node => node.textContent), ['重新连接', '删除账号']);
    assert.equal(actions.querySelector('[data-gmail-remove]').closest('.gmail-account-card'), card);
    assert.equal(card.querySelector('button button'), null);
    const css = fs.readFileSync(path.join(__dirname, '../assets/custom.css'), 'utf8');
    assert.match(css, /\.gmail-account-actions\s*\{[^}]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/);
    assert.match(css, /\.gmail-account-card\.is-active\s*\{[^}]*border-color:/);
  } finally { ui.dom.window.close(); }
});

test('canceling deletion leaves the account connected and sends no request', async () => {
  const ui = setup([expired], {confirm: false});
  try {
    await settle();
    ui.doc.querySelector('[data-gmail-remove]').click();
    await settle();
    assert.equal(ui.confirmations.length, 1);
    assert.ok(ui.confirmations[0].includes(expired.email));
    assert.equal(ui.calls.filter(call => call.method === 'POST').length, 0);
    assert.equal(ui.doc.querySelectorAll('.gmail-account-card').length, 1);
  } finally { ui.dom.window.close(); }
});

test('deleting the selected account returns to all accounts and keeps the unread filter', async () => {
  const ui = setup([expired, {...expired, id: 'b'.repeat(20), email: 'two@example.test'}]);
  try {
    await settle();
    ui.doc.querySelectorAll('.gmail-account')[1].click();
    ui.doc.querySelector('#gmail-unread').checked = true;
    ui.doc.querySelector('[data-gmail-remove]').click();
    await settle();
    assert.ok(ui.calls.some(call => call.url.endsWith('/api/gmail/remove/' + expired.id) && call.method === 'POST'));
    assert.equal(ui.doc.querySelectorAll('.gmail-account-card').length, 1);
    assert.equal(ui.doc.querySelector('#gmail-account-count').textContent, '1');
    assert.equal(ui.doc.querySelector('.gmail-account.is-active strong').textContent, '全部账号');
    assert.equal(ui.doc.querySelector('#gmail-unread').checked, true);
    assert.match(ui.doc.querySelector('#gmail-auth').textContent, /已删除/);
  } finally { ui.dom.window.close(); }
});

test('deleting another account preserves selection and deleting the last shows the empty state', async () => {
  const ui = setup([expired, {...expired, id: 'b'.repeat(20), email: 'two@example.test'}]);
  try {
    await settle();
    ui.doc.querySelectorAll('.gmail-account')[1].click();
    ui.doc.querySelectorAll('[data-gmail-remove]')[1].click();
    await settle();
    assert.equal(ui.doc.querySelector('.gmail-account.is-active strong').textContent, expired.email);
    ui.doc.querySelector('[data-gmail-remove]').click();
    await settle();
    assert.equal(ui.doc.querySelectorAll('.gmail-account-card').length, 0);
    assert.match(ui.doc.querySelector('#gmail-accounts').textContent, /尚未连接账号/);
    assert.equal(ui.doc.querySelector('#gmail-add').disabled, false);
  } finally { ui.dom.window.close(); }
});

test('failed deletion keeps the connection and exposes retry', async () => {
  const ui = setup([expired], {failure: true});
  try {
    await settle();
    ui.doc.querySelector('[data-gmail-remove]').click();
    await settle();
    assert.equal(ui.doc.querySelectorAll('.gmail-account-card').length, 1);
    assert.equal(ui.doc.querySelector('[data-gmail-remove]').disabled, false);
    assert.match(ui.doc.querySelector('#gmail-auth').textContent, /删除账号.*重试/);
  } finally { ui.dom.window.close(); }
});

test('deletion disables both actions until completion to prevent duplicate submissions', async () => {
  let release;
  const ui = setup([expired], {wait: new Promise(resolve => { release = resolve; })});
  try {
    await settle();
    ui.doc.querySelector('[data-gmail-remove]').click();
    await settle();
    assert.equal(ui.doc.querySelector('[data-gmail-remove]').textContent, '删除中…');
    assert.equal(ui.doc.querySelector('[data-gmail-remove]').disabled, true);
    assert.equal(ui.doc.querySelector('[data-gmail-reconnect]').disabled, true);
    ui.doc.querySelector('[data-gmail-remove]').click();
    assert.equal(ui.calls.filter(call => call.method === 'POST').length, 1);
    release();
    await settle();
    assert.equal(ui.doc.querySelectorAll('.gmail-account-card').length, 0);
  } finally { release(); ui.dom.window.close(); }
});

test('an older polling response cannot restore the account after deletion', async () => {
  const ui = setup([expired]);
  try {
    await settle();
    const originalFetch = ui.dom.window.fetch;
    let finish;
    ui.dom.window.fetch = async (url, init) => {
      if (url.endsWith('/api/gmail')) return new Promise(resolve => {
        finish = () => resolve({ok: true, json: async () => ({configured: true, accounts: [expired], messages: []})});
      });
      return originalFetch(url, init);
    };
    ui.dom.window.dispatchEvent(new ui.dom.window.Event('focus'));
    await settle();
    assert.equal(typeof finish, 'function');
    ui.doc.querySelector('[data-gmail-remove]').click();
    await settle();
    assert.equal(ui.doc.querySelectorAll('.gmail-account-card').length, 0);
    finish();
    await settle();
    assert.equal(ui.doc.querySelectorAll('.gmail-account-card').length, 0);
  } finally { ui.dom.window.close(); }
});
