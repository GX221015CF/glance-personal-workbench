(() => {
  'use strict';
  const channel = 'personal-workbench-school-v1';
  const origins = new Set(['http://127.0.0.1:8180', 'http://localhost:8180']);
  if (window !== window.top || !origins.has(location.origin)
      || !['/study', '/study/', '/academic', '/academic/'].includes(location.pathname) || location.search) return;
  const mutations = new Set(['start', 'continue', 'cancel', 'status', 'openSchool', 'dismiss', 'connectSchool', 'continueSchool', 'retrySchool', 'openPortal', 'verifySchoolFlow']);
  const seen = new Map();
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  const validUuid = value => typeof value === 'string' && uuid.test(value);
  let grant = null;
  let lastRead = 0;
  let busy = false;
  // The real DOM event is observed in the isolated content world. A page-supplied
  // isTrusted property is never accepted as evidence of a user action.
  document.addEventListener('click', event => {
    const button = event.target.closest?.('#study-school-connector button[data-school-command], #study-workspace button[data-school-reconnect], #academic-workspace button[data-school-reconnect]');
    if (!event.isTrusted || !button || button.disabled) return;
    const command = button.dataset.schoolCommand;
    if (!mutations.has(command)) return;
    grant = {command, target: button.dataset.schoolTarget || null, expires: Date.now() + 1500};
  }, true);

  window.addEventListener('message', async event => {
    if (event.source !== window || event.origin !== location.origin || busy) return;
    const message = event.data;
    if (!message || message.channel !== channel || message.direction !== 'request') return;
    const keys = ['channel', 'direction', 'version', 'clientId', 'id', 'sentAt', 'command', 'target', 'taskId', 'confirm'];
    if (Object.keys(message).length !== keys.length || keys.some(key => !Object.hasOwn(message, key))
        || message.version !== 1 || !validUuid(message.clientId) || !validUuid(message.id)
        || !Number.isFinite(message.sentAt) || Math.abs(Date.now() - message.sentAt) > 15000
        || !['hello', 'view', ...mutations].includes(message.command)
        || (message.taskId !== null && !validUuid(message.taskId)) || typeof message.confirm !== 'boolean') return;
    const request = {...message};
    delete request.direction;
    // Requests expire after 15 seconds. Retain IDs for twice that interval so
    // long-lived workbench tabs keep polling without unbounded storage growth.
    for (const [id, timestamp] of seen) if (Date.now() - timestamp > 30000) seen.delete(id);
    if (seen.has(request.id) || seen.size >= 256) return;
    if (mutations.has(request.command)) {
      if (!grant || grant.expires < Date.now() || grant.command !== request.command || grant.target !== request.target) return;
      grant = null;
    } else {
      if (Date.now() - lastRead < 200) return;
      lastRead = Date.now();
    }
    seen.set(request.id, Date.now());
    busy = true;
    try {
      const response = await chrome.runtime.sendMessage({workbench: request});
      window.postMessage({channel, direction: 'response', version: 1, clientId: request.clientId, id: request.id, response}, location.origin);
    } catch {
      window.postMessage({channel, direction: 'response', version: 1, clientId: request.clientId, id: request.id,
        response: {ok: false, code: 'EXTENSION_RELOADED'}}, location.origin);
    } finally { busy = false; }
  });
})();
