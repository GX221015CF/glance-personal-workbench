(() => {
  const bridge = 'http://127.0.0.1:8182/api/desktop';
  let running = false;

  async function connect() {
    const response = await fetch(bridge, { cache: 'no-store', signal: AbortSignal.timeout(4000) });
    if (!response.ok) throw new Error('本机按钮服务暂不可用，请重新启动工作台后刷新');
    return response.json();
  }

  function buttons() { return [...document.querySelectorAll('[data-desktop-action]')]; }
  function message(text) {
    document.querySelectorAll('.desktop-action-status').forEach(element => { element.textContent = text; });
  }
  async function init() {
    if (!buttons().length) return;
    try {
      const session = await connect();
      buttons().forEach(button => { button.disabled = !session.actions[button.dataset.desktopAction]; });
      message('与桌面快捷方式相同，点击后打开脚本窗口。');
    } catch (_) {
      message('本机按钮服务未连接，点击此处重试。');
    }
  }
  document.addEventListener('click', async event => {
    if (event.target.closest('.desktop-action-status') && !running) { await init(); return; }
    const button = event.target.closest('[data-desktop-action]');
    if (!button || button.disabled || running) return;
    running = true;
    buttons().forEach(element => { element.disabled = true; });
    message('正在打开' + button.textContent.trim() + '…');
    let resultMessage;
    try {
      // A fresh token also handles bridge restarts without a page reload.
      const session = await connect();
      const response = await fetch(`${bridge}/${button.dataset.desktopAction}`, {
        method: 'POST', headers: { 'X-Desktop-Token': session.token },
        signal: AbortSignal.timeout(6000),
      });
      const result = await response.json();
      resultMessage = result.message || '无法打开，请重试';
    } catch (_) {
      resultMessage = '未收到启动确认，请先检查是否已弹出窗口，再尝试重启工作台。';
    } finally {
      message(resultMessage);
      setTimeout(async () => {
        running = false;
        try {
          const session = await connect();
          buttons().forEach(element => { element.disabled = !session.actions[element.dataset.desktopAction]; });
        } catch (_) { message('本机按钮服务未连接，点击此处重试。'); }
      }, 5000);
    }
  });
  init();
  // Glance loads widget HTML after the deferred scripts on some page loads.
  const observer = new MutationObserver(() => {
    if (buttons().length) { observer.disconnect(); init(); }
  });
  observer.observe(document.body, { childList: true, subtree: true });
})();
