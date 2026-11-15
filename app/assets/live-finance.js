(() => {
  let busy = false;
  let lastFetch = Date.now();
  const finance = ['.live-focus-markets', '.world-market-snapshot', '.live-resets', '.widget-type-weather', '.home-weather'];
  function formatWeatherErrors() {
    document.querySelectorAll('.widget-type-weather .widget-error-header, .home-weather .widget-error-header').forEach(header => {
      if (header.dataset.localized) return;
      const detail = header.nextElementSibling;
      if (!detail) return;
      const original = detail.textContent;
      const title = header.querySelector('.color-negative');
      if (title) title.textContent = '天气暂时不可用';
      detail.classList.remove('break-all');
      detail.textContent = '天气源连接暂时中断，工作台会自动重试。';
      const disclosure = document.createElement('details');
      disclosure.className = 'weather-network-details';
      const summary = document.createElement('summary');
      summary.textContent = '查看连接详情';
      const message = document.createElement('p');
      message.textContent = original;
      disclosure.append(summary, message);
      detail.after(disclosure);
      header.dataset.localized = 'true';
    });
  }
  async function refresh() {
    if (busy || document.visibilityState === 'hidden' || typeof pageData === 'undefined') return;
    if (Date.now() - lastFetch < 55000) return;
    busy = true;
    lastFetch = Date.now();
    try {
      const response = await fetch(`${pageData.baseURL}/api/pages/${pageData.slug}/content/`, { cache: 'no-store' });
      if (!response.ok) return;
      const incoming = new DOMParser().parseFromString(await response.text(), 'text/html');
      const selectors = [...finance];
      for (const selector of selectors) {
        const current = document.querySelector(`${selector} > .widget-content`);
        const next = incoming.querySelector(`${selector} > .widget-content`);
        if (current && next) {
          const referenceOpen = current.querySelector('.market-reference-note')?.open;
          current.innerHTML = next.innerHTML;
          if (referenceOpen && current.querySelector('.market-reference-note')) current.querySelector('.market-reference-note').open = true;
        }
      }
      formatWeatherErrors();
    } catch (_) {
      // Retain the last rendered data; its source timestamp remains visible.
    } finally { busy = false; }
  }
  setInterval(refresh, 60000);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') refresh(); });
  formatWeatherErrors();
  const weatherObserver = new MutationObserver(() => {
    if (!document.querySelector('.widget-type-weather, .home-weather')) return;
    formatWeatherErrors();
    weatherObserver.disconnect();
  });
  weatherObserver.observe(document.body, { childList: true, subtree: true });
})();
