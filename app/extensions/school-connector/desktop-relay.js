// Runs only in the existing allowlisted localhost content-script world.
// A short-lived backend capability replaces the webpage click grant for this
// desktop-originated task. It never accepts URLs or session material.
(() => {
  const match = /^#desktop-connect=([A-Za-z0-9_-]{43})$/.exec(location.hash);
  if (!match || window !== window.top) return;
  const ticket = match[1];
  const endpoint = 'http://127.0.0.1:8182/api/desktop-relay/v1/control';
  let stopped = false;
  let attached = false;
  let polling = false;
  async function control(action, extra = {}) {
    const response = await fetch(endpoint, {method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({action,ticket,...extra}),signal:AbortSignal.timeout(6000)});
    if (!response.ok) throw new Error('RELAY_REJECTED');
    return (await response.json()).data;
  }
  async function tick() {
    if (stopped || polling) return;
    polling = true;
    try {
      let command = 'view';
      let confirmed;
      let target=null;
      if (!attached) {
        const claim = await control('claim');
        if (claim.page !== location.pathname.replace(/\/$/, '')) throw new Error('PAGE_MISMATCH');
        command = 'attach'; confirmed = claim.confirmed;
      } else {
        const next = await control('next');
        command = next.command?.command || 'view';
        target = next.command?.target || null;
      }
      const response = await chrome.runtime.sendMessage({desktopRelay:{ticket,command,confirmed,target}});
      if (!response?.ok) throw new Error('CONNECTOR_UNAVAILABLE');
      attached = true;
      await control('report',{state:response.state});
    } catch {
      // No automatic mutation replay. A page refresh can attach idempotently;
      // otherwise the desktop reports the missing relay and allows state check.
      stopped = true;
    } finally { polling = false; }
  }
  tick();
  const timer = setInterval(tick,1500);
  setTimeout(() => {stopped=true;clearInterval(timer);},600000);
})();
