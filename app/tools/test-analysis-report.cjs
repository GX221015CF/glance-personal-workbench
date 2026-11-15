const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('trusted slider updates numbers and bar for positive, negative and zero coefficients', () => {
  const script = fs.readFileSync(path.join(__dirname, '../assets/analysis-report.js'), 'utf8');
  for (const factor of [3, -2, 0]) {
    const listeners = {};
    const input = {min:'0', max:'100', value:'20', addEventListener(name, fn) { listeners[name] = fn; }};
    const output = {}, current = {}, progress = {};
    const nodes = {'input[type="range"]':input, output, '.simulator-current':current, progress};
    const panel = {dataset:{factor:String(factor), offset:'2', unit:'分钟'}, querySelector(selector) { return nodes[selector]; }};
    vm.runInNewContext(script, {Intl, document:{querySelectorAll() { return [panel]; }}});
    assert.equal(output.textContent, (factor * 20 + 2) + '分钟');
    assert.equal(progress.value, 20);
    input.value = '80';
    listeners.input();
    assert.equal(output.textContent, (factor * 80 + 2) + '分钟');
    assert.equal(current.textContent, '80');
    assert.equal(progress.value, 80);
  }
});

test('visual template matches reference body width with responsive components', () => {
  const css = fs.readFileSync(path.join(__dirname, '../assets/analysis-report.css'), 'utf8');
  assert.match(css, /--measure:64em/);
  assert.match(css, /--visual-width:992px/);
  assert.match(css, /main\s*\{[^}]*max-width:var\(--measure\)/);
  assert.match(css, /@media\(max-width:999px\)\s*\{\s*\.wide\s*\{[^}]*width:100%/);
  const narrow = css.split('@media(max-width:560px)')[1].split('@media(prefers-reduced-motion:reduce)')[0];
  for (const selector of ['.deck', '.matrix', '.bar-row', '.funnel > li', '.timeline > li']) {
    assert.ok(narrow.includes(selector), selector);
  }
  assert.match(narrow, /\.deck,\.matrix\s*\{\s*grid-template-columns:1fr/);
  assert.match(narrow, /\.funnel > li\s*\{\s*width:100%/);
  assert.match(css, /minmax\(0,3fr\)/);
  assert.equal(64 * 15.5, 992);
  assert.equal(64 * 15.5 + 48, 1040);
  assert.match(css, /\.report\s*\{[^}]*max-width:calc\(var\(--visual-width\) \+ 48px\)/);
});

test('visual CSS has print fallbacks, reduced motion and no external resources', () => {
  const css = fs.readFileSync(path.join(__dirname, '../assets/analysis-report.css'), 'utf8');
  assert.doesNotMatch(css, /@import|url\s*\(|https?:|@font-face/i);
  const print = css.split('@media print')[1];
  for (const selector of ['.wide', '.dcard', '.bar-track', '.bar-fill', '.funnel > li', '.mx.q1', '.timeline > li::after']) {
    assert.ok(print.includes(selector), selector);
  }
  assert.doesNotMatch(print, /\.deck[^}]*display:none/);
  assert.match(print, /body\s*\{\s*background:#fff/);
  assert.match(css, /prefers-reduced-motion:reduce/);
  assert.match(css, /\.dcard\.flash::before\s*\{\s*animation:none/);
  const heading = Number(css.match(/h1\s*\{\s*font-size:(\d+)px/)[1]);
  const conclusion = Number(css.match(/\.core-conclusion\s*\{\s*font-size:(\d+)px/)[1]);
  assert.ok(heading > conclusion);
});
