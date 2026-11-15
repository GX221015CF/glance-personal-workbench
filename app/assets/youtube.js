(() => {
  const base = 'http://127.0.0.1:8182';
  let state, active = '', busy = false, started = false, limit = 60;
  const el = id => document.getElementById(id);
  const text = (tag, value, className) => {
    const node = document.createElement(tag);
    node.textContent = value;
    if (className) node.className = className;
    return node;
  };
  async function request(path, method = 'GET') {
    const session = await fetch(base + '/api/desktop', {cache:'no-store',signal:AbortSignal.timeout(5000)});
    if (!session.ok) throw new Error('本机数据服务暂未连接，请重新启动工作台。');
    const {token} = await session.json();
    const response = await fetch(base + path, {method,cache:'no-store',headers:{'X-Desktop-Token':token},signal:AbortSignal.timeout(8000)});
    if (!response.ok) {
      let message = 'YouTube 数据服务暂不可用。';
      try { message = (await response.json()).message || message; } catch (_) {}
      throw new Error(message);
    }
    return response.json();
  }
  function publicationTime(video, now = Date.now()) {
    const timestamp = Number.isFinite(video.timestamp) ? video.timestamp * 1000 : Date.parse(video.publishedAt);
    if (!Number.isFinite(timestamp)) {
      return {relative:'时间待确认', absolute:video.timeLabel || '', iso:'', recent:false};
    }
    const seconds = Math.floor((now - timestamp) / 1000);
    let relative = '刚刚';
    if (seconds < 0) relative = '即将发布';
    else if (seconds >= 365 * 86400) relative = Math.floor(seconds / (365 * 86400)) + ' 年前';
    else if (seconds >= 30 * 86400) relative = Math.floor(seconds / (30 * 86400)) + ' 个月前';
    else if (seconds >= 86400) relative = Math.floor(seconds / 86400) + ' 天前';
    else if (seconds >= 3600) relative = Math.floor(seconds / 3600) + ' 小时前';
    else if (seconds >= 60) relative = Math.floor(seconds / 60) + ' 分钟前';
    const published = new Date(timestamp);
    const absolute = new Intl.DateTimeFormat('zh-CN', {
      timeZone:'Asia/Shanghai', year:'numeric', month:'2-digit', day:'2-digit',
      hour:'2-digit', minute:'2-digit', hourCycle:'h23'
    }).format(published);
    return {relative, absolute:absolute + ' · 北京时间', iso:published.toISOString(), recent:seconds >= 0 && seconds < 86400};
  }
  function accountButton(label, id, detail) {
    const button = text('button', '', 'gmail-account' + (active === id ? ' is-active' : ''));
    button.type = 'button';
    button.append(text('strong', label), text('span', detail));
    button.addEventListener('click', () => { active = id; limit = 60; el('youtube-channel').value = ''; render(); });
    return button;
  }
  function render() {
    if (!state || !el('youtube-dashboard')) return;
    const accounts = state.accounts || [];
    if (active && !accounts.some(row => row.id === active)) active = '';
    el('youtube-account-count').textContent = accounts.length;
    const sidebar = el('youtube-accounts');
    sidebar.replaceChildren();
    if (accounts.length) {
      sidebar.append(accountButton('全部账号', '', `${accounts.length} 个 YouTube 账号`));
      for (const account of accounts) {
        const updated = account.checkedAt ? ` · ${new Date(account.checkedAt).toLocaleTimeString('zh-CN', {hour:'2-digit',minute:'2-digit',hour12:false})}` : '';
        sidebar.append(accountButton(account.title, account.id, `${account.subscriptionCount} 个订阅 · ${account.status}${updated}${account.stale ? ' · 数据不完整或过期' : ''}`));
      }
    } else sidebar.append(text('p', '尚未连接 YouTube', 'gmail-empty-small'));
    el('youtube-add').disabled = !state.configured;
    el('youtube-refresh').disabled = !accounts.length || state.refreshing;
    el('youtube-notice').textContent = state.error || (!state.configured ? '请先导入 Google 桌面客户端配置。' : !accounts.length ? 'Google 客户端已就绪。启用 YouTube Data API v3 后，点击“连接 YouTube”授权。' : state.refreshing ? '正在读取订阅频道和最新视频；首次同步需要一些时间…' : '每小时自动同步 · 每频道取最近 5 个视频，每账号保留最新 200 个 · 手动刷新间隔至少 5 分钟');
    const selectedChannel = el('youtube-channel').value;
    const channels = (state.channels || []).filter(row => !active || row.accountIds.includes(active));
    const options = el('youtube-channel');
    options.replaceChildren(new Option(`全部订阅频道（${channels.length}）`, ''));
    for (const channel of channels) options.append(new Option(channel.title, channel.id));
    if (channels.some(row => row.id === selectedChannel)) options.value = selectedChannel;
    const query = el('youtube-search').value.trim().toLocaleLowerCase();
    const days = Number(el('youtube-days').value);
    const cutoff = days ? Date.now() / 1000 - days * 86400 : 0;
    const videos = (state.videos || []).filter(row => (!active || row.accountIds.includes(active)) && (!options.value || row.channelId === options.value) && row.timestamp >= cutoff && (!query || (row.title + ' ' + row.channel).toLocaleLowerCase().includes(query)));
    el('youtube-count').textContent = `${videos.length} 个视频`;
    const list = el('youtube-videos');
    list.replaceChildren();
    for (const account of accounts.filter(row => row.error && (!active || active === row.id))) list.append(text('p', account.title + '：' + account.error, 'gmail-notice'));
    if (!videos.length) {
      const unavailable = accounts.some(row => (!active || row.id === active) && !row.available);
      list.append(text('p', !accounts.length ? '连接后，订阅频道的新视频会汇总到这里。' : state.refreshing ? '正在同步，请稍候…' : unavailable ? '尚未成功取得视频，请查看账号状态或重新连接。' : '当前筛选下没有视频。', 'gmail-empty'));
    }
    for (const video of videos.slice(0, limit)) {
      if (!/^[A-Za-z0-9_-]{11}$/.test(video.id)) continue;
      const link = document.createElement('a');
      link.className = 'youtube-video';
      link.href = 'https://www.youtube.com/watch?v=' + video.id;
      link.target = '_blank'; link.rel = 'noopener noreferrer';
      const thumbnail = document.createElement('img');
      thumbnail.src = 'https://i.ytimg.com/vi/' + video.id + '/mqdefault.jpg';
      thumbnail.alt = ''; thumbnail.loading = 'lazy'; thumbnail.referrerPolicy = 'no-referrer';
      const info = text('div', '', 'youtube-video-info');
      const published = publicationTime(video);
      const timing = text('div', '', 'youtube-publication');
      const relative = text('time', published.relative, 'youtube-time-badge' + (published.recent ? ' is-recent' : ''));
      if (published.iso) relative.dateTime = published.iso;
      relative.title = published.absolute;
      relative.setAttribute('aria-label', '发布于' + published.relative + '，' + published.absolute);
      timing.append(relative, text('span', published.absolute, 'youtube-time-exact'));
      info.append(text('h3', video.title), text('p', video.channel), timing);
      link.append(thumbnail, info, text('span', '↗', 'youtube-open'));
      list.append(link);
    }
    el('youtube-more').hidden = videos.length <= limit;
    const community = el('youtube-community');
    community.href = options.value ? 'https://www.youtube.com/channel/' + options.value + '/posts' : 'https://www.youtube.com/feed/subscriptions';
    community.textContent = options.value ? '查看此频道社区动态 ↗' : '打开 YouTube 订阅页 ↗';
  }
  async function load() {
    if (busy || !el('youtube-dashboard')) return;
    busy = true;
    try { state = await request('/api/youtube'); render(); }
    catch (error) { el('youtube-notice').textContent = error.name === 'TypeError' || error.name === 'TimeoutError' ? '本机服务暂未连接，请刷新或重新启动工作台。' : error.message; }
    finally { busy = false; }
  }
  function init() {
    if (started || !el('youtube-dashboard')) return;
    started = true;
    for (const id of ['youtube-search', 'youtube-channel', 'youtube-days']) el(id).addEventListener(id === 'youtube-search' ? 'input' : 'change', () => { limit = 60; render(); });
    el('youtube-more').addEventListener('click', () => { limit += 60; render(); });
    el('youtube-refresh').addEventListener('click', async () => {
      el('youtube-refresh').disabled = true;
      try { await request('/api/youtube/refresh', 'POST'); await load(); }
      catch (error) { el('youtube-notice').textContent = error.message; el('youtube-refresh').disabled = false; }
    });
    el('youtube-add').addEventListener('click', async () => {
      const popup = window.open('about:blank', '_blank');
      if (!popup) { el('youtube-notice').textContent = '请允许工作台打开 Google 登录窗口。'; return; }
      popup.opener = null;
      try {
        const result = await request('/api/youtube/connect', 'POST');
        const url = new URL(result.authorizationUrl);
        if (url.origin !== 'https://accounts.google.com') throw new Error('Google 登录地址无效');
        popup.location.href = url.href;
      } catch (error) { popup.close(); el('youtube-notice').textContent = error.message; }
    });
    load();
    setInterval(() => { if (document.visibilityState === 'visible') load(); }, 15000);
    document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') load(); });
  }
  init();
  if (!started) {
    const observer = new MutationObserver(() => { init(); if (started) observer.disconnect(); });
    observer.observe(document.body, {childList:true,subtree:true});
  }
})();
