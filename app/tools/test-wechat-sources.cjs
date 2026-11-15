const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.join(__dirname, '..');
const sources = JSON.parse(fs.readFileSync(path.join(root, 'config/wechat-sources.json'), 'utf8')).sources;
const css = fs.readFileSync(path.join(root, 'assets/wechat.css'), 'utf8');

class Node {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this.classList = {toggle() {}};
  }
  append(...children) { this.children.push(...children); }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener() {}
  querySelector(selector) {
    for (const child of this.children) {
      if ((child.className || '').split(' ').includes(selector.slice(1))) return child;
      const match = child.querySelector(selector);
      if (match) return match;
    }
    return null;
  }
}

function loadCards() {
  const document = {getElementById() { return null; }, createElement(tag) { return new Node(tag); }, addEventListener() {}, body: {}};
  const context = vm.createContext({document, window: {addEventListener() {}}, Intl, MutationObserver: class {observe() {}}});
  const script = fs.readFileSync(path.join(root, 'assets/wechat.js'), 'utf8');
  // Expose the real card builders only in this isolated DOM test, with no network or browser.
  vm.runInContext(script.replace(/\}\)\(\);\s*$/, 'globalThis.builders = {articleCard, sourceButton}; })();'), context);
  return context.builders;
}

test('each configured source has a distinct stable colour and a shared fallback', () => {
  const palette = new Map([...css.matchAll(/\[data-source-id="([^"]+)"\] \{ --wechat-source-color: (#[a-f0-9]+); \}/g)]
    .map(match => [match[1], match[2]]));
  assert.equal(new Set(sources.map(source => palette.get(source.id))).size, sources.length);
  for (const source of sources) assert.ok(palette.has(source.id), source.id);
  assert.match(css, /#wechat-dashboard \{ --wechat-source-color: var\(--color-primary\); \}/);
  assert.match(css, /\.wechat-article-source::before/);
  assert.match(css, /\.wechat-article \{ border-left: 4px solid var\(--wechat-source-color\)/);
});

test('publication badges stay neutral in both cards and the reader, including recent posts', () => {
  const badgeRules = [...css.matchAll(/([^{}]*\.youtube-time-badge[^{}]*)\{([^{}]*)\}/g)];
  assert.equal(badgeRules.length, 1);
  assert.equal(badgeRules[0][1].trim(), '#wechat-dashboard .youtube-time-badge');
  assert.match(badgeRules[0][2], /color: var\(--color-text-base\)/);
  assert.match(badgeRules[0][2], /filter: grayscale\(1\)/);
  assert.match(badgeRules[0][2], /background: transparent/);
  assert.doesNotMatch(badgeRules[0][2], /--color-primary|--wechat-source-color/);
});

test('source sidebar is a bounded independent scroll region, including narrow screens', () => {
  const sidebar = css.match(/\.wechat-sidebar \{([^}]+)\}/)[1];
  assert.match(sidebar, /position: sticky/);
  assert.match(sidebar, /max-height: calc\(100dvh - 36px\)/);
  assert.match(sidebar, /overflow-y: auto/);
  assert.match(sidebar, /overscroll-behavior-y: contain/);
  assert.match(sidebar, /scrollbar-gutter: stable/);
  assert.match(css, /@media \(max-width: 800px\) \{\s*\.wechat-sidebar \{[^}]*max-height: 50dvh/);
  const config = fs.readFileSync(path.join(root, 'config/glance.yml'), 'utf8');
  assert.match(config, /<aside class="gmail-sidebar wechat-sidebar" tabindex="0" aria-label="公众号订阅来源，可独立滚动">/);
});

test('article cards and sidebar retain the same source ID and readable source name', () => {
  const {articleCard, sourceButton} = loadCards();
  for (const source of [...sources, {id: 'future-source', name: '新增公众号'}]) {
    const article = {id: 'a'.repeat(64), sourceId: source.id, source: source.name,
      title: '测试文章', timestamp: 1794139200, publishedAt: '2026-11-08T12:00:00+08:00'};
    const card = articleCard(article);
    const sidebar = sourceButton(source.name, source.id);
    assert.equal(card.dataset.sourceId, source.id);
    assert.equal(sidebar.dataset.sourceId, source.id);
    assert.equal(card.querySelector('.wechat-article-source').textContent, source.name);
    assert.ok(card.querySelector('.wechat-analysis-button'));
    assert.ok(card.querySelector('.wechat-summary-button'));
    assert.ok(card.querySelector('.wechat-reader-trigger'));
  }
});

test('unknown or hostile labels remain plain text without changing presentation classes', () => {
  const {articleCard} = loadCards();
  const label = '<img src=x onerror=alert(1)>';
  const card = articleCard({id: 'b'.repeat(64), sourceId: '"] body { color:red }', source: label,
    title: '标题', timestamp: 1794139200, publishedAt: '2026-11-08T12:00:00+08:00'});
  assert.equal(card.className, 'wechat-article');
  assert.equal(card.querySelector('.wechat-article-source').textContent, label);
  assert.equal(card.querySelector('.wechat-article-source').children.length, 0);
});
