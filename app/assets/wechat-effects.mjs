import { attachButton } from './components/button-player.mjs';
import { attachAccordion } from './components/accordion-motion.mjs';
import selection from './components/wechat-selection.mjs';

const storageKey = 'wechat-component-effects-enabled-v1';
export function createWechatEffects(root) {
  const buttons = new Map(), panels = new Map();
  let enabled = true, destroyed = false, queued = false;
  try { enabled = localStorage.getItem(storageKey) !== 'false'; } catch {}
  const ordinary = selection.records.filter(record => record.component === 'button' && record.scene === 'general');
  const waiting = selection.records.find(record => record.trigger === 'loading');
  const accordion = selection.records.find(record => record.component === 'accordion');
  const control = document.createElement('button');
  control.type = 'button'; control.id = 'wechat-effects-toggle';
  control.title = `组件选择修订 ${selection.revision}，可随时切换比较`;
  root.querySelector('.wechat-refresh-actions').append(control);
  function clear() {
    buttons.forEach(player => player.destroy()); buttons.clear();
    panels.forEach(player => player.destroy()); panels.clear();
  }
  function summary(button, region, open) {
    if (!enabled || destroyed) { region.hidden = !open; return; }
    let player = panels.get(region);
    if (!player) {
      player = attachAccordion({ button, region, content: region.querySelector('.wechat-summary-content'), duration: accordion.params.duration });
      panels.set(region, player);
    }
    player.setOpen(open, { animate: region.isConnected });
  }
  function sync() {
    if (destroyed) return;
    const label = enabled ? '已选交互 · 开' : '已选交互 · 关';
    if (control.textContent !== label) control.textContent = label;
    control.setAttribute('aria-pressed', String(enabled));
    root.dataset.componentRevision = String(selection.revision);
    root.dataset.componentEffects = enabled ? 'on' : 'off';
    if (!enabled) return;
    for (const [node, player] of buttons) if (!root.contains(node)) { player.destroy(); buttons.delete(node); }
    for (const [node, player] of panels) if (!root.contains(node)) { player.destroy(); panels.delete(node); }
    root.querySelectorAll('#wechat-refresh,#wechat-more,#wechat-updates,.wechat-summary-button,.wechat-analysis-button,.wechat-summary-retry,.wechat-reader-close').forEach(button => {
      let player = buttons.get(button);
      if (!player) {
        player = attachButton(button, { records: button.id === 'wechat-refresh' ? [...ordinary, waiting] : ordinary });
        buttons.set(button, player);
      }
      player.ensureDecoration();
      if (button.id === 'wechat-refresh') player.setBusy(button.getAttribute('aria-busy') === 'true');
    });
    root.querySelectorAll('.wechat-article').forEach(card => {
      const button = card.querySelector('.wechat-summary-toggle'), region = card.querySelector('.wechat-summary-panel');
      if (button && region) summary(button, region, button.getAttribute('aria-expanded') === 'true');
    });
  }
  const observer = new MutationObserver(() => {
    if (queued || destroyed) return;
    queued = true;
    queueMicrotask(() => { queued = false; sync(); });
  });
  observer.observe(root, { childList: true, subtree: true, attributes: true, attributeFilter: ['aria-busy'] });
  function toggle() {
    enabled = !enabled;
    try { localStorage.setItem(storageKey, String(enabled)); } catch {}
    clear(); sync();
  }
  control.addEventListener('click', toggle);
  sync();
  return {
    summary, sync,
    destroy() { if (destroyed) return; destroyed = true; observer.disconnect(); clear(); control.removeEventListener('click', toggle); control.remove(); delete root.dataset.componentRevision; delete root.dataset.componentEffects; },
  };
}
