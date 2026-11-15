(() => {
  const bridge = 'http://127.0.0.1:8182';
  let state, active = '', busy = false, started = false;
  let connecting = false, authorization = null;
  let removing = '', snapshotVersion = 0;
  const element = id => document.getElementById(id);
  const text = (tag, value, className) => {
    const node = document.createElement(tag);
    node.textContent = value;
    if (className) node.className = className;
    return node;
  };
  async function request(path, method = 'GET') {
    const session = await fetch(bridge + '/api/desktop', {cache:'no-store',signal:AbortSignal.timeout(5000)});
    if (!session.ok) throw new Error('本机邮箱服务未连接，请重新启动工作台。');
    const { token } = await session.json();
    const response = await fetch(bridge + path, {method, cache:'no-store',headers:{'X-Desktop-Token':token},signal:AbortSignal.timeout(8000)});
    if (!response.ok) {
      let message = '邮箱服务暂不可用，请稍后重试。';
      try { message = (await response.json()).message || message; } catch (_) {}
      throw new Error(message);
    }
    return response.json();
  }
  function accountButton(label, id, detail, avatarUrl = '') {
    const button = text('button', '', 'gmail-account gmail-account-with-avatar' + (active === id ? ' is-active' : ''));
    button.type = 'button';
    const avatar = text('span', id ? label.slice(0, 2).toUpperCase() : '▦', 'gmail-account-avatar');
    avatar.setAttribute('aria-hidden', 'true');
    avatar.translate = false;
    if (id) avatar.style.setProperty('--avatar-hue', String(parseInt(id.slice(0, 4), 16) % 360));
    if (avatarUrl) {
      try {
        const url = new URL(avatarUrl);
        if (url.protocol === 'https:' && (url.hostname === 'googleusercontent.com' || url.hostname.endsWith('.googleusercontent.com')) && !url.username && !url.password) {
          const image = document.createElement('img');
          image.alt = ''; image.width = 38; image.height = 38; image.referrerPolicy = 'no-referrer';
          image.addEventListener('load', () => avatar.classList.add('has-image'));
          image.addEventListener('error', () => { image.remove(); avatar.classList.remove('has-image'); });
          image.src = url.href;
          avatar.append(image);
        }
      } catch (_) {}
    }
    const copy = text('div', '', 'gmail-account-copy');
    copy.append(text('strong', label));
    if (detail) copy.append(text('span', detail));
    button.append(avatar, copy);
    button.addEventListener('click', () => { active = id; render(); });
    return button;
  }
  function authNotice(message, url = '') {
    const notice = element('gmail-auth');
    notice.replaceChildren(text('span', message));
    if (url) {
      const link = text('a', '继续 Google 授权 ↗');
      link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer';
      notice.append(link);
    }
    notice.hidden = false;
  }
  async function connect(account = null) {
    if (connecting || removing) return;
    const id = account ? account.id : '';
    connecting = true;
    render();
    authNotice(account ? `正在准备重新连接 ${account.email}…` : '正在准备 Google 登录…');
    try {
      const result = await request(account ? '/api/gmail/reconnect/' + account.id : '/api/gmail/connect', 'POST');
      const url = new URL(result.authorizationUrl);
      if (url.origin !== 'https://accounts.google.com' || url.username || url.password) throw new Error('Google 登录地址无效');
      authorization = {id, checkedAt: account ? account.checkedAt : '', wasExpired: Boolean(account && (account.needsReconnect || account.status === '需要重新授权'))};
      authNotice(account ? `请在 Google 中重新授权 ${account.email}。若登录页未打开，请点击下方链接。` : '请在 Google 中选择邮箱并授权。若登录页未打开，请点击下方链接。', url.href);
      // Electron opens HTTPS links externally and returns null. Always keep a
      // real link available for browsers that block an asynchronous popup.
      window.open(url.href, '_blank', 'noopener,noreferrer');
    } catch (error) {
      authorization = null;
      authNotice(error.message + '，可再次点击连接按钮重试。');
    } finally {
      connecting = false;
      render();
    }
  }
  async function removeAccount(account) {
    if (connecting || removing) return;
    if (!window.confirm(`从工作台删除 ${account.email} 的连接和本机邮件快照？后续可重新添加。`)) return;
    removing = account.id;
    snapshotVersion++;
    render();
    try {
      const result = await request('/api/gmail/remove/' + account.id, 'POST');
      state = result.snapshot;
      snapshotVersion++;
      if (active === account.id) active = '';
      if (authorization && authorization.id === account.id) authorization = null;
      authNotice(`已删除 ${account.email} 的工作台连接。需要时可重新添加。`);
    } catch (error) {
      authNotice(error.message + '，可再次点击“删除账号”重试。');
    } finally {
      removing = '';
      render();
    }
  }
  function render() {
    if (!state || !element('gmail-dashboard')) return;
    const accounts = state.accounts || [];
    if (active && !accounts.some(account => account.id === active)) active = '';
    element('gmail-account-count').textContent = accounts.length;
    const sidebar = element('gmail-accounts');
    sidebar.replaceChildren();
    if (accounts.length) {
      const allKnown = accounts.every(account => typeof account.unread === 'number');
      sidebar.append(accountButton('全部账号', '', allKnown ? `${accounts.reduce((sum, account) => sum + account.unread, 0)} 封收件箱未读` : '部分账号正在同步'));
      for (const account of accounts) {
        const card = text('div', '', 'gmail-account-card' + (active === account.id ? ' is-active' : ''));
        card.dataset.gmailAccount = account.id;
        const unread = typeof account.unread === 'number' ? `${account.unread} 封未读` : '未读数待同步';
        card.append(accountButton(account.email, account.id, `${unread} · ${account.status}${account.stale ? ' · 旧数据' : ''}`, account.avatarUrl));
        const actions = text('div', '', 'gmail-account-actions');
        const reconnect = text('button', '重新连接', 'gmail-reconnect');
        reconnect.type = 'button';
        reconnect.dataset.gmailReconnect = account.id;
        reconnect.disabled = !state.configured || connecting || Boolean(removing);
        reconnect.title = `重新登录并授权 ${account.email}`;
        reconnect.addEventListener('click', () => connect(account));
        const remove = text('button', removing === account.id ? '删除中…' : '删除账号', 'gmail-remove');
        remove.type = 'button';
        remove.dataset.gmailRemove = account.id;
        remove.disabled = connecting || Boolean(removing);
        remove.title = `删除 ${account.email} 在工作台中的连接`;
        remove.addEventListener('click', () => removeAccount(account));
        actions.append(reconnect, remove);
        card.append(actions);
        if (account.avatarStatus === 'authorization' && !account.needsReconnect && account.status !== '需要重新授权') {
          const authorize = text('button', '获取 Google 头像 ↗', 'gmail-avatar-authorize');
          authorize.type = 'button';
          authorize.title = `重新登录 ${account.email}，允许读取 Google 基本资料与头像`;
          authorize.disabled = !state.configured || connecting || Boolean(removing);
          authorize.addEventListener('click', () => connect(account));
          card.append(authorize);
        }
        sidebar.append(card);
      }
    } else sidebar.append(text('p', '尚未连接账号', 'gmail-empty-small'));
    element('gmail-add').disabled = !state.configured || connecting || Boolean(removing);
    element('gmail-refresh').disabled = !accounts.length || state.refreshing || Boolean(removing);
    const expired = accounts.filter(account => account.needsReconnect || account.status === '需要重新授权');
    element('gmail-notice').textContent = state.error || (!state.configured ? '尚未配置 Google 登录。页面已就绪，完成接入设置后即可逐个添加邮箱。' : !accounts.length ? 'Google 登录已就绪。点击“添加 Gmail 账号”连接第一个邮箱。' : expired.length ? `${expired.length} 个邮箱需要重新授权，请点击对应账号下的“重新连接”。已取得的邮件暂时保留。` : state.refreshing ? '正在同步各账号的邮件列表…' : '每 5 分钟自动同步 · 每个账号最多显示最近 20 封收件箱邮件');
    if (authorization) {
      const connected = authorization.wasExpired && accounts.some(account => account.id === authorization.id && account.status === '已连接' && !account.stale && account.checkedAt !== authorization.checkedAt);
      if (connected) {
        authorization = null;
        authNotice('邮箱已重新连接，邮件列表已更新。');
      }
    }
    if (!state.configured) element('gmail-setup').open = true;
    const current = accounts.find(account => account.id === active);
    element('gmail-list-title').textContent = (current ? current.email : '全部账号') + ' · 收件箱';
    const list = element('gmail-messages');
    list.replaceChildren();
    const rows = (state.messages || []).filter(row => (!active || row.accountId === active) && (!element('gmail-unread').checked || row.unread));
    if (!rows.length) {
      let message = '当前筛选下没有邮件。';
      if (!accounts.length) message = '连接邮箱后，最新邮件会显示在这里。';
      else if (accounts.some(account => (!active || account.id === active) && !account.available)) message = '所选邮箱尚未成功同步，请查看账号状态或重新授权。';
      list.append(text('div', message, 'gmail-empty'));
    }
    for (const row of rows) {
      const link = document.createElement('a');
      link.className = 'gmail-message' + (row.unread ? ' is-unread' : '');
      // Only navigate to Gmail, never to links taken from mail content.
      const url = new URL(row.url);
      if (url.origin !== 'https://mail.google.com') continue;
      link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer';
      const main = text('div', '', 'gmail-message-main');
      main.append(text('strong', row.subject), text('span', row.sender, 'gmail-sender'));
      const meta = text('div', '', 'gmail-message-meta');
      meta.append(text('span', row.account, 'gmail-account-tag'), text('time', row.timeLabel + ' 北京时间'));
      link.append(text('span', row.unread ? '●' : '', 'gmail-unread-dot'), main, meta);
      list.append(link);
    }
    for (const account of accounts.filter(account => account.error && (!active || account.id === active))) list.prepend(text('p', account.email + '：' + account.error, 'gmail-notice'));
  }
  async function load() {
    if (busy || !element('gmail-dashboard')) return;
    busy = true;
    const version = snapshotVersion;
    try {
      const result = await request('/api/gmail');
      if (version !== snapshotVersion) return;
      state = result; render();
    }
    catch (error) {
      if (version !== snapshotVersion) return;
      element('gmail-notice').textContent = error.name === 'TypeError' || error.name === 'TimeoutError' ? '本机邮箱服务暂时未连接，请重新启动工作台后刷新。' : error.message;
      if (!state) element('gmail-messages').replaceChildren(text('div', '暂未取得邮箱连接状态，请稍后重试。', 'gmail-empty'));
    }
    finally { busy = false; }
  }
  function init() {
    if (started || !element('gmail-dashboard')) return;
    started = true;
    if (!element('gmail-auth')) {
      const notice = text('div', '', 'gmail-auth');
      notice.id = 'gmail-auth'; notice.hidden = true;
      notice.setAttribute('role', 'status'); notice.setAttribute('aria-live', 'polite');
      element('gmail-notice').after(notice);
    }
    element('gmail-unread').addEventListener('change', render);
    element('gmail-refresh').addEventListener('click', async () => {
      element('gmail-refresh').disabled = true;
      try { await request('/api/gmail/refresh', 'POST'); await load(); }
      catch (error) { element('gmail-notice').textContent = error.message; }
    });
    element('gmail-add').addEventListener('click', () => connect());
    load();
    setInterval(() => { if (document.visibilityState === 'visible') load(); }, 30000);
    document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') load(); });
    window.addEventListener('focus', load);
  }
  init();
  if (!started) {
    const observer = new MutationObserver(() => { init(); if (started) observer.disconnect(); });
    observer.observe(document.body, {childList:true,subtree:true});
  }
})();
