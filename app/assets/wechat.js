(() => {
  const bridge = 'http://127.0.0.1:8182';
  const element = id => document.getElementById(id);
  const dateFormat = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23'
  });
  let root, state, active = '', busy = false, pending = null, timer;
  let shownSignature = '', displayLimit = 40;
  let requesting = false, manualRefresh = null, refreshMessage = '', cooldownUntil = 0;
  let reader, readerContent, readerScroll = 0, readerRequest = 0;
  const submitting = new Set(), requestErrors = new Map();
  const expandedSummaries = new Set(), summaryCache = new Map();
  let effects = null, intervals = [], lifecycleObserver = null, lifecycleVersion = 0;
  const text = (tag, value, className) => {
    const node = document.createElement(tag);
    node.textContent = value || '';
    if (className) node.className = className;
    return node;
  };
  function relativeTime(timestamp) {
    const seconds = Math.floor(Date.now() / 1000 - timestamp);
    if (seconds < 0) return '即将发布';
    if (seconds < 60) return '刚刚';
    if (seconds < 3600) return Math.floor(seconds / 60) + ' 分钟前';
    if (seconds < 86400) return Math.floor(seconds / 3600) + ' 小时前';
    if (seconds < 30 * 86400) return Math.floor(seconds / 86400) + ' 天前';
    if (seconds < 365 * 86400) return Math.floor(seconds / (30 * 86400)) + ' 个月前';
    return Math.floor(seconds / (365 * 86400)) + ' 年前';
  }
  function updateTimes() {
    root.querySelectorAll('time[data-published]').forEach(node => {
      const timestamp = Number(node.dataset.published);
      node.textContent = relativeTime(timestamp);
      const age = Date.now() / 1000 - timestamp;
      node.classList.toggle('is-recent', age >= 0 && age < 86400);
    });
  }
  function analysisLabel(article, kind = 'analysis') {
    const status = article[kind + 'Status'], label = kind === 'summary' ? '总结' : '解读';
    return status === 'completed' ? label + '完成' : status === 'submitting' ? '提交中…' :
      ['queued', 'running'].includes(status) ? `${label}中 ${analysisPercent(article, kind)}%` :
      status === 'failed' ? '重试' + label : label;
  }
  function analysisPercent(article, kind = 'analysis') {
    if (article[kind + 'Status'] === 'completed') return 100;
    return Math.max(0, Math.min(99, Number(article[kind + 'Percent']) || 0));
  }
  function paintAnalysis(card, article, kind = 'analysis') {
    const button = card.querySelector(`.wechat-${kind}-button`);
    const progress = card.querySelector(`.wechat-${kind}-progress`);
    const meter = card.querySelector(`.wechat-${kind}-meter`);
    if (!button || !progress || !meter) return;
    const status = article[kind + 'Status'];
    const running = ['submitting', 'queued', 'running'].includes(status);
    const label = analysisLabel(article, kind);
    if (button.textContent !== label) button.textContent = label;
    button.disabled = running;
    button.setAttribute('aria-busy', String(running));
    button.title = kind === 'summary' ? '简要总结新闻，完成后在卡片下方展开' : '在后台解读，完成后点击查看报告';
    progress.textContent = article[kind + 'Error'] || (status === 'completed' ? '100%' :
      running ? `${article[kind + 'Progress'] || '正在提交'} · 阶段进度` : '');
    progress.dataset.state = status || 'none';
    progress.hidden = !progress.textContent;
    meter.hidden = !running;
    meter.value = analysisPercent(article, kind);
  }
  function setAnalysisCard(articleId, status, kind = 'analysis') {
    const article = state.articles.find(item => item.id === articleId);
    if (!article) return;
    Object.assign(article, {[kind+'Status']:status.status, [kind+'RunId']:status.runId || '', [kind+'Error']:status.error || '',
      [kind+'Progress']:status.progress || '', [kind+'Percent']:status.progressPercent || 0});
    const card = root.querySelector(`[data-article-id="${articleId}"]`);
    if (card) { paintAnalysis(card, article, kind); if (kind === 'summary') syncSummary(card, article); }
  }
  async function desktopRequest(path, method = 'GET') {
    const session = await fetch(bridge + '/api/desktop', {cache:'no-store', signal:AbortSignal.timeout(5000)});
    if (!session.ok) throw new Error('本机工作台服务未连接');
    const {token} = await session.json();
    const response = await fetch(bridge + path, {method, cache:'no-store', headers:{'X-Desktop-Token':token}, signal:AbortSignal.timeout(10000)});
    const result = await response.json();
    if (!response.ok) throw new Error(result.message || '解读请求失败，请稍后重试');
    return result;
  }
  async function analyzeArticle(articleId, kind = 'analysis') {
    const article = state?.articles.find(item => item.id === articleId);
    const key = kind + ':' + articleId;
    if (!article || submitting.has(key)) return;
    if (article[kind+'Status'] === 'completed' && article[kind+'RunId']) {
      if (kind === 'summary') toggleSummary(articleId);
      else window.open(bridge + `/api/wechat/analysis/${article.id}/${article.analysisRunId}/document`, '_blank', 'noopener');
      return;
    }
    if (['submitting', 'queued', 'running'].includes(article[kind+'Status'])) return;
    submitting.add(key);
    requestErrors.delete(key);
    setAnalysisCard(articleId, {status:'submitting', progress:'正在提交给本机 WorkBuddy', progressPercent:0}, kind);
    const path = `/api/wechat/${kind}/${article.id}`;
    try {
      const started = await desktopRequest(path, 'POST');
      setAnalysisCard(articleId, started[kind], kind);
    } catch (error) {
      const message = error.message || '请求状态暂未确认，正在重新连接本机服务。';
      requestErrors.set(key, message);
      setAnalysisCard(articleId, {status:'failed', error:message}, kind);
    } finally {
      submitting.delete(key);
      clearTimeout(timer);
      timer = setTimeout(load, 100);
    }
  }
  function syncSummary(card, article) {
    const toggle = card.querySelector('.wechat-summary-toggle');
    const panel = card.querySelector('.wechat-summary-panel');
    if (!toggle || !panel) return;
    const complete = article.summaryStatus === 'completed';
    toggle.hidden = !complete;
    const open = complete && expandedSummaries.has(article.id);
    toggle.setAttribute('aria-label', open ? '收起总结' : '展开总结');
    if (effects) effects.summary(toggle, panel, open);
    else panel.hidden = !open;
    toggle.setAttribute('aria-expanded', String(open));
    if (open) loadSummary(article, card);
  }
  function toggleSummary(articleId) {
    const article = state?.articles.find(item => item.id === articleId);
    const card = root.querySelector(`[data-article-id="${articleId}"]`);
    if (!article || !card || article.summaryStatus !== 'completed') return;
    if (expandedSummaries.has(articleId)) expandedSummaries.delete(articleId);
    else expandedSummaries.add(articleId);
    syncSummary(card, article);
  }
  function showSummaryMarkdown(card, markdown) {
    const body = card.querySelector('.wechat-summary-markdown');
    if (body.dataset.loaded === markdown) return;
    if (window.WeChatMarkdown) window.WeChatMarkdown.render(markdown, body);
    else { body.replaceChildren(text('pre', markdown)); }
    body.dataset.loaded = markdown;
  }
  async function loadSummary(article, card) {
    const cached = summaryCache.get(article.id);
    if (cached?.runId === article.summaryRunId) {
      if (cached.markdown !== undefined) showSummaryMarkdown(card, cached.markdown);
      return;
    }
    const entry = {runId:article.summaryRunId};
    summaryCache.set(article.id, entry);
    const body = card.querySelector('.wechat-summary-markdown');
    body.replaceChildren(text('p', '正在读取本地总结…'));
    try {
      const response = await fetch(bridge + `/api/wechat/summary/${article.id}/${entry.runId}/content`, {cache:'no-store',signal:AbortSignal.timeout(8000)});
      if (!response.ok) throw new Error('本地总结暂时无法读取');
      const result = await response.json();
      if (typeof result.markdown !== 'string' || result.markdown.length > 8000 || result.runId !== entry.runId) throw new Error('总结格式无效');
      if (summaryCache.get(article.id) !== entry || state?.articles.find(item => item.id === article.id)?.summaryRunId !== entry.runId) return;
      entry.markdown = result.markdown;
      const currentCard = root.querySelector(`[data-article-id="${article.id}"]`);
      if (currentCard) showSummaryMarkdown(currentCard, entry.markdown);
    } catch (error) {
      if (summaryCache.get(article.id) !== entry) return;
      summaryCache.delete(article.id);
      const currentArticle = state?.articles.find(item => item.id === article.id);
      const currentCard = root?.querySelector(`[data-article-id="${article.id}"]`);
      if (!currentCard || currentArticle?.summaryRunId !== entry.runId) return;
      const currentBody = currentCard.querySelector('.wechat-summary-markdown');
      currentBody.replaceChildren(text('p', error.message || '读取失败，请稍后重试'));
      const retry = text('button', '重试读取', 'wechat-summary-retry'); retry.type='button';
      retry.addEventListener('click', () => loadSummary(currentArticle, currentCard)); currentBody.append(retry);
    }
  }
  function articleCard(article) {
    const card = text('article', '', 'wechat-article');
    card.dataset.articleId = article.id;
    card.dataset.sourceId = article.sourceId || '';
    const link = text('div', '', 'wechat-article-link');
    const cover = text('button', '', 'wechat-cover');
    cover.type = 'button';
    cover.setAttribute('aria-label', '阅读：' + article.title);
    cover.addEventListener('click', () => openReader(article.id));
    cover.append(text('span', article.source, 'wechat-cover-placeholder'));
    if (article.hasCover && /^[a-f0-9]{64}$/.test(article.id)) {
      const image = document.createElement('img');
      image.alt = ''; image.loading = 'lazy'; image.referrerPolicy = 'no-referrer';
      image.addEventListener('load', () => cover.classList.add('has-image'));
      image.addEventListener('error', () => { image.remove(); cover.classList.remove('has-image'); });
      image.src = bridge + '/api/wechat/cover/' + article.id;
      cover.append(image);
    }
    const body = text('div', '', 'wechat-article-body');
    const readerButton = text('button', '', 'wechat-reader-trigger');
    readerButton.type = 'button';
    readerButton.addEventListener('click', () => openReader(article.id));
    readerButton.append(text('p', article.source, 'wechat-article-source'), text('h3', article.title));
    if (article.summary) readerButton.append(text('p', article.summary, 'wechat-article-summary'));
    body.append(readerButton);
    const publication = text('div', '', 'youtube-publication');
    const date = new Date(article.timestamp * 1000);
    const exact = dateFormat.format(date) + ' · 北京时间';
    const relative = text('time', relativeTime(article.timestamp), 'youtube-time-badge');
    relative.dataset.published = article.timestamp;
    relative.dateTime = article.publishedAt;
    relative.title = exact;
    publication.append(relative, text('span', exact, 'youtube-time-exact'));
    body.append(publication);
    link.append(cover, body, text('span', '›', 'wechat-open'));
    const actions = text('div', '', 'wechat-analysis-actions');
    const analysis = text('button', analysisLabel(article), 'wechat-analysis-button');
    analysis.type = 'button';
    analysis.addEventListener('click', () => analyzeArticle(article.id));
    const meter = text('progress', '', 'wechat-analysis-meter');
    meter.max = 100;
    meter.setAttribute('aria-label', '解读阶段进度');
    meter.title = '按实际流程阶段更新，不代表正文生成比例或预计剩余时间';
    const progress = text('span', '', 'wechat-analysis-progress');
    progress.setAttribute('role', 'status');
    progress.setAttribute('aria-live', 'polite');
    actions.append(analysis, meter, progress);
    publication.append(actions);
    const summaryActions = text('div', '', 'wechat-analysis-actions');
    const summarize = text('button', '总结', 'wechat-summary-button'); summarize.type='button';
    summarize.addEventListener('click', () => analyzeArticle(article.id, 'summary'));
    const summaryMeter = text('progress', '', 'wechat-summary-meter'); summaryMeter.max=100;
    summaryMeter.setAttribute('aria-label', '总结阶段进度');
    summaryMeter.title='按实际流程阶段更新，不代表正文生成比例或剩余时间';
    const summaryProgress = text('span', '', 'wechat-summary-progress');
    summaryProgress.setAttribute('role','status'); summaryProgress.setAttribute('aria-live','polite');
    const toggle = text('button', '', 'wechat-summary-toggle'); toggle.type='button';
    const arrow = text('span', '▾', 'wechat-summary-arrow'); arrow.setAttribute('aria-hidden','true'); toggle.append(arrow);
    toggle.setAttribute('aria-label','展开总结'); toggle.setAttribute('aria-expanded','false');
    toggle.setAttribute('aria-controls','wechat-summary-'+article.id);
    toggle.addEventListener('click', () => toggleSummary(article.id));
    summaryActions.append(summarize, summaryMeter, summaryProgress, toggle);
    publication.append(summaryActions);
    const panel = text('section', '', 'wechat-summary-panel'); panel.hidden=true;
    panel.id='wechat-summary-'+article.id; panel.setAttribute('aria-label','文章简要总结');
    const content = text('div', '', 'wechat-summary-content');
    content.append(text('div', '', 'wechat-summary-markdown'),text('p','AI 总结 · 基于已收录正文，未进行外部核验','wechat-summary-note'));
    panel.append(content);
    card.append(link, panel);
    paintAnalysis(card, article);
    paintAnalysis(card, article, 'summary');
    syncSummary(card, article);
    return card;
  }
  function contentSignature(article) {
    const {id, sourceId, source, title, summary, timestamp, publishedAt, hasCover} = article;
    return JSON.stringify({id, sourceId, source, title, summary, timestamp, publishedAt, hasCover});
  }
  function visibleArticles() {
    return state.articles.filter(article => !active || article.sourceId === active);
  }
  function renderArticles(force = false) {
    const articles = visibleArticles();
    const signature = articles.map(contentSignature).join('\n');
    const list = element('wechat-articles');
    // Progress updates must not reorder/replace cards or wait behind the new-article banner.
    list.querySelectorAll('[data-article-id]').forEach(card => {
      const article = state.articles.find(item => item.id === card.dataset.articleId);
      if (article) { paintAnalysis(card, article); paintAnalysis(card, article, 'summary'); syncSummary(card, article); }
    });
    if (signature === shownSignature && !force) {
      pending = null;
      element('wechat-updates').hidden = true;
      updateTimes();
      return;
    }
    const reading = reader?.open || list.getBoundingClientRect().top < 0 || list.contains(document.activeElement) || Boolean(window.getSelection()?.toString());
    if (!force && reading && list.querySelector('[data-article-id]')) {
      pending = true;
      const keys = new Set([...list.querySelectorAll('[data-article-id]')].map(node => node.dataset.articleId));
      const added = articles.filter(article => !keys.has(article.id)).length;
      const button = element('wechat-updates');
      button.textContent = added ? `${added} 篇新文章 · 点击更新` : '文章有更新 · 点击查看';
      button.hidden = false;
      updateTimes();
      return;
    }
    const existing = new Map([...list.querySelectorAll('[data-article-id]')].map(node => [node.dataset.articleId, node]));
    const used = new Set();
    articles.slice(0, displayLimit).forEach((article, index) => {
      const signature = contentSignature(article);
      let node = existing.get(article.id);
      if (!node) node = articleCard(article);
      else if (node.dataset.signature !== signature) node.replaceChildren(...articleCard(article).childNodes);
      node.dataset.signature = signature;
      node.dataset.sourceId = article.sourceId || '';
      used.add(node);
      if (list.children[index] !== node) list.insertBefore(node, list.children[index] || null);
    });
    [...list.children].forEach(node => { if (!used.has(node)) node.remove(); });
    if (!articles.length) list.append(text('p', state.refreshing ? '正在获取公众号文章，已完成的来源会先显示。' : '这个来源暂未收录文章，请查看左侧同步状态。', 'gmail-empty'));
    element('wechat-more').hidden = articles.length <= displayLimit;
    element('wechat-count').textContent = articles.length + ' 篇文章';
    element('wechat-list-title').textContent = active ? state.sources.find(source => source.id === active).name : '全部公众号';
    element('wechat-updates').hidden = true;
    pending = null;
    shownSignature = signature;
    updateTimes();
  }
  function sourceButton(name, id) {
    const button = text('button', '', 'gmail-account wechat-source' + (active === id ? ' is-active' : ''));
    button.type = 'button'; button.dataset.sourceId = id;
    button.append(text('strong', name), text('span', '', 'wechat-source-status'), text('span', '', 'wechat-source-updated'), text('span', '', 'wechat-source-error'));
    button.addEventListener('click', () => {
      active = id; displayLimit = 40; shownSignature = '';
      renderSources(); renderArticles(true);
    });
    return button;
  }
  function renderSources() {
    const sidebar = element('wechat-sources');
    const sources = [{id:'', name:'全部', count:state.articles.length, status:'合并时间流'}, ...state.sources];
    const current = new Map([...sidebar.children].map(node => [node.dataset.sourceId, node]));
    sources.forEach((source, index) => {
      const node = current.get(source.id) || sourceButton(source.name, source.id);
      node.classList.toggle('is-active', active === source.id);
      node.querySelector('strong').textContent = source.name;
      node.querySelector('.wechat-source-status').textContent = `${source.count} 篇 · ${source.status}`;
      node.querySelector('.wechat-source-updated').textContent = source.id ? source.lastSuccess ? '上次成功 ' + dateFormat.format(new Date(source.lastSuccess)) : '尚无成功同步记录' : '';
      const error = node.querySelector('.wechat-source-error');
      error.textContent = source.error || '';
      error.hidden = !source.error;
      if (sidebar.children[index] !== node) sidebar.insertBefore(node, sidebar.children[index] || null);
    });
  }
  function renderRefresh() {
    const button = element('wechat-refresh');
    const hint = element('wechat-refresh-hint');
    const running = requesting || Boolean(state?.refreshing) || Boolean(manualRefresh);
    const remaining = Math.max(0, Math.ceil((cooldownUntil - Date.now()) / 1000));
    button.disabled = running || remaining > 0;
    button.classList.toggle('is-refreshing', running);
    const label = running ? '正在刷新' : '刷新文章';
    if (button.textContent !== label) button.textContent = label;
    if (button.getAttribute('aria-busy') !== String(running)) button.setAttribute('aria-busy', String(running));
    const countdown = remaining ? `下次可刷新：${Math.floor(remaining / 60)} 分 ${String(remaining % 60).padStart(2, '0')} 秒` : '现在可以手动刷新';
    hint.textContent = running ? '正在检查各公众号，文章可继续阅读…' : [refreshMessage, countdown].filter(Boolean).join(' · ');
  }
  async function load() {
    clearTimeout(timer);
    if (busy || !root?.isConnected) return;
    if (document.visibilityState === 'hidden') { timer = setTimeout(load, 15000); return; }
    busy = true;
    const version = lifecycleVersion;
    try {
      const response = await fetch(bridge + '/api/wechat', {cache:'no-store', signal:AbortSignal.timeout(8000)});
      if (!response.ok) throw new Error('本机公众号服务暂不可用');
      const snapshot = await response.json();
      if (version !== lifecycleVersion || !root?.isConnected) return;
      state = snapshot;
      state.articles.forEach(article => {
        for (const kind of ['analysis','summary']) {
          const key = kind + ':' + article.id;
          if (submitting.has(key)) {
            Object.assign(article, {[kind+'Status']:'submitting', [kind+'Progress']:'正在提交给本机 WorkBuddy', [kind+'Percent']:0});
          } else if ((article[kind+'Status'] || 'none') === 'none' && requestErrors.has(key)) {
            Object.assign(article, {[kind+'Status']:'failed', [kind+'Error']:requestErrors.get(key), [kind+'Percent']:0});
          } else if (article[kind+'Status'] !== 'none') requestErrors.delete(key);
        }
      });
      cooldownUntil = Date.now() + (state.retryAfterSeconds || 0) * 1000;
      if (active && !state.sources.some(source => source.id === active)) active = '';
      renderSources(); renderArticles();
      const failures = state.sources.filter(source => source.error).length;
      element('wechat-notice').textContent = state.refreshing ? '正在检查新文章，已收录内容可以先看…' : failures ? `${failures} 个来源暂未更新，已保留上次成功收录的文章。详情见来源列表。` : `已收录 ${state.articles.length} 篇 · 每 30 分钟自动检查新文章 · 本地持续保存`;
      if (manualRefresh && !state.refreshing && !requesting) {
        const added = state.articles.filter(article => !manualRefresh.ids.has(article.id)).length;
        refreshMessage = failures ? `刷新结束，${failures} 个来源更新失败，新增 ${added} 篇` : added ? `刷新完成，新增 ${added} 篇` : '刷新完成，暂无新文章';
        element('wechat-notice').textContent = refreshMessage + (failures ? '，已保留此前文章，详情见来源列表。' : '。');
        manualRefresh = null;
      }
      renderRefresh();
    } catch (error) {
      if (version !== lifecycleVersion || !root?.isConnected) return;
      element('wechat-notice').textContent = manualRefresh ? '暂时无法确认刷新结果，正在重新连接本机服务。' : '本机连接暂时中断，保留当前文章并自动重试。';
    } finally {
      if (version !== lifecycleVersion) return;
      busy = false;
      timer = setTimeout(load, state?.refreshing || manualRefresh || state?.articles?.some(article => ['queued', 'running'].includes(article.analysisStatus) || ['queued','running'].includes(article.summaryStatus)) ? 2000 : 15000);
    }
  }
  async function refresh() {
    if (requesting || state?.refreshing || manualRefresh || Date.now() < cooldownUntil) return;
    requesting = true;
    const version = lifecycleVersion;
    refreshMessage = '';
    renderRefresh();
    try {
      const session = await fetch(bridge + '/api/desktop', {cache:'no-store', signal:AbortSignal.timeout(5000)});
      if (!session.ok) throw new Error('本机服务未连接');
      const {token} = await session.json();
      if (version !== lifecycleVersion) return;
      const response = await fetch(bridge + '/api/wechat/refresh', {method:'POST',headers:{'X-Desktop-Token':token},signal:AbortSignal.timeout(8000)});
      const result = await response.json();
      if (version !== lifecycleVersion) return;
      if (!response.ok) {
        if (result.retryAfterSeconds) cooldownUntil = Date.now() + result.retryAfterSeconds * 1000;
        throw new Error(result.message || '刷新失败，请稍后重试');
      }
      manualRefresh = {ids: new Set((state?.articles || []).map(article => article.id))};
      requesting = false;
      await load();
    } catch (error) {
      if (version !== lifecycleVersion) return;
      refreshMessage = error.message || '刷新请求失败，请稍后重试';
    } finally {
      if (version !== lifecycleVersion) return;
      requesting = false;
      renderRefresh();
    }
  }
  function renderReaderNodes(nodes, parent, articleId) {
    const tags = new Set(['p','h1','h2','h3','h4','h5','h6','ul','ol','li','blockquote','pre','code','strong','b','em','i','br']);
    for (const item of nodes) {
      if (item.type === 'text' && typeof item.text === 'string') {
        parent.append(document.createTextNode(item.text));
      } else if (item.type === 'image' && Number.isSafeInteger(item.index) && item.index >= 0) {
        const holder = text('figure', '', 'wechat-reader-image');
        const placeholder = text('span', '图片加载中…');
        const image = document.createElement('img');
        image.alt = '文章配图'; image.loading = 'lazy';
        image.addEventListener('load', () => placeholder.remove());
        image.addEventListener('error', () => { image.remove(); placeholder.textContent = '图片暂时无法加载'; });
        image.src = bridge + '/api/wechat/image/' + articleId + '/' + item.index;
        holder.append(placeholder, image); parent.append(holder);
      } else if (tags.has(item.type) && Array.isArray(item.children)) {
        const node = document.createElement(item.type);
        renderReaderNodes(item.children, node, articleId);
        parent.append(node);
      }
    }
  }
  async function openReader(id) {
    if (!/^[a-f0-9]{64}$/.test(id)) return;
    readerScroll = window.scrollY;
    const request = ++readerRequest;
    readerContent.replaceChildren(text('p', '正在读取本地正文…', 'wechat-reader-message'));
    reader.showModal();
    try {
      const response = await fetch(bridge + '/api/wechat/article/' + id, {cache:'no-store', signal:AbortSignal.timeout(8000)});
      if (!response.ok) throw new Error('本地文章暂时无法读取');
      const article = await response.json();
      if (request !== readerRequest || !reader.open) return;
      const source = text('p', article.source, 'wechat-article-source');
      readerContent.dataset.sourceId = state?.articles.find(item => item.id === id)?.sourceId || '';
      const title = text('h2', article.title, 'wechat-reader-title');
      const publication = text('div', '', 'youtube-publication');
      const exact = dateFormat.format(new Date(article.timestamp * 1000)) + ' · 北京时间';
      const relative = text('time', relativeTime(article.timestamp), 'youtube-time-badge');
      relative.dataset.published = article.timestamp; relative.dateTime = article.publishedAt; relative.title = exact;
      publication.append(relative, text('span', exact, 'youtube-time-exact'));
      const original = text('a', '打开微信原文 ↗', 'wechat-original');
      const url = new URL(article.url);
      if (url.protocol === 'https:' && url.hostname === 'mp.weixin.qq.com' && !url.port && !url.username && !url.password && /^\/s(?:\/[A-Za-z0-9_-]+)?$/.test(url.pathname)) {
        original.href = url.href; original.target = '_blank'; original.rel = 'noopener noreferrer';
      }
      const body = text('div', '', 'wechat-reader-body');
      if (Array.isArray(article.nodes) && article.nodes.length) renderReaderNodes(article.nodes, body, id);
      else body.append(text('p', '正文尚未收录。该文章可能已不在来源的最新订阅中，可使用上方按钮打开微信原文。', 'wechat-reader-message'));
      readerContent.replaceChildren(source, title, publication, original, body);
      updateTimes();
    } catch (error) {
      if (request === readerRequest && reader.open) readerContent.replaceChildren(text('p', '本地文章暂时无法读取，请稍后重试。', 'wechat-reader-message'));
    }
  }
  function mountEffects(mountedRoot) {
    const version = lifecycleVersion;
    import('/assets/wechat-effects.mjs?v=20261109-integration-1').then(module => {
      if (version !== lifecycleVersion || root !== mountedRoot || !mountedRoot.isConnected) return;
      effects = module.createWechatEffects(root);
    }).catch(() => { /* Business controls remain usable if enhancement resources cannot load. */ });
  }
  function startIntervals() {
    intervals.push(setInterval(() => { if (root?.isConnected && document.visibilityState === 'visible') updateTimes(); }, 30000));
    intervals.push(setInterval(() => { if (root?.isConnected && document.visibilityState === 'visible') renderRefresh(); }, 1000));
  }
  function init() {
    if (root?.isConnected) return true;
    root = element('wechat-dashboard');
    if (!root) return false;
    mountEffects(root);
    reader = document.createElement('dialog'); reader.className = 'wechat-reader';
    reader.setAttribute('aria-label', '公众号文章阅读');
    const toolbar = text('div', '', 'wechat-reader-toolbar');
    const close = text('button', '← 返回文章列表', 'wechat-reader-close'); close.type = 'button';
    close.addEventListener('click', () => reader.close());
    toolbar.append(close); readerContent = text('article', '', 'wechat-reader-content');
    reader.append(toolbar, readerContent); root.append(reader);
    reader.addEventListener('close', () => { readerRequest++; window.scrollTo(0, readerScroll); });
    element('wechat-refresh-hint').setAttribute('role', 'status');
    element('wechat-refresh-hint').setAttribute('aria-live', 'off');
    element('wechat-refresh').addEventListener('click', refresh);
    element('wechat-more').addEventListener('click', () => { displayLimit += 40; renderArticles(true); });
    element('wechat-updates').addEventListener('click', () => {
      if (!pending) return;
      renderArticles(true);
      element('wechat-list-title').scrollIntoView({block:'start', behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});
    });
    load();
    startIntervals();
    return true;
  }
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible' && root?.isConnected) load(); });
  function cleanup() {
    clearTimeout(timer); intervals.forEach(clearInterval); intervals = [];
    effects?.destroy(); effects = null; readerRequest++; lifecycleVersion++; busy = false; requesting = false;
  }
  lifecycleObserver = new MutationObserver(() => {
    if (root && !root.isConnected) { cleanup(); root = null; shownSignature = ''; }
    if (!root) init();
  });
  lifecycleObserver.observe(document.body, {childList:true,subtree:true});
  window.addEventListener('pagehide', () => { cleanup(); lifecycleObserver.disconnect(); });
  window.addEventListener('pageshow', event => { if (event.persisted) {
    if (root?.isConnected) { mountEffects(root); startIntervals(); load(); }
    else { root = null; shownSignature = ''; init(); }
    lifecycleObserver.observe(document.body, {childList:true,subtree:true});
  } });
  init();
})();
