(() => {
  let root, timer, busy = false, pending = null, applied = '';
  const el = id => document.getElementById(id);
  const reduceMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const text = (tag, value, className) => {
    const node = document.createElement(tag);
    node.textContent = value || '';
    if (className) node.className = className;
    return node;
  };
  const safeLink = value => {
    try { const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) ? url.href : ''; }
    catch (_) { return ''; }
  };
  function publicationTime(label, timeline) {
    const daily = /^日报\s+(\d{4}-\d{2}-\d{2})$/.exec(label || '');
    if (daily) {
      const timestamp = Date.parse(daily[1] + 'T00:00:00+08:00') / 1000;
      return Number.isFinite(timestamp) ? {timestamp, basis:'日报收录日期', dayOnly:true} : null;
    }
    const reference = Number(timeline) * 1000;
    const match = /^(原文|AIHOT收录)\s+(\d{2})-(\d{2})\s+(\d{2}):(\d{2})$/.exec(label || '');
    if (!match || !Number.isFinite(reference) || reference <= 0) return null;
    // timeLabel has no year. Use the nearest year to its own timeline, so
    // December articles still display correctly after the New Year.
    const year = new Date(reference + 8 * 3600000).getUTCFullYear();
    const dates = [year - 1, year, year + 1].map(y => Date.parse(`${y}-${match[2]}-${match[3]}T${match[4]}:${match[5]}:00+08:00`)).filter(Number.isFinite);
    dates.sort((a, b) => Math.abs(a - reference) - Math.abs(b - reference));
    return dates.length ? {timestamp:dates[0] / 1000, basis:match[1]} : null;
  }
  function relativeTime(timestamp, dayOnly = false) {
    if (dayOnly) {
      const days = Math.floor((Date.now() + 8 * 3600000) / 86400000) - Math.floor((timestamp * 1000 + 8 * 3600000) / 86400000);
      return days < 0 ? '待收录日报' : days === 0 ? '今日日报' : days + ' 天前日报';
    }
    const seconds = Math.floor(Date.now() / 1000 - timestamp);
    if (seconds < 0) return '即将发布';
    if (seconds < 60) return '刚刚';
    if (seconds < 3600) return Math.floor(seconds / 60) + ' 分钟前';
    if (seconds < 86400) return Math.floor(seconds / 3600) + ' 小时前';
    return Math.floor(seconds / 86400) + ' 天前';
  }
  function updateTimes() {
    root?.querySelectorAll('.aihot-time-badge[data-published]').forEach(node => {
      const timestamp = Number(node.dataset.published);
      node.textContent = relativeTime(timestamp, node.dataset.dayOnly === 'true');
      const age = Date.now() / 1000 - timestamp;
      node.classList.toggle('is-recent', age >= 0 && age < 86400);
    });
  }
  function publication(row) {
    const meta = text('div', '', 'aihot-publication');
    const time = publicationTime(row.timeLabel, row.timeline);
    if (time) {
      const badge = text('time', relativeTime(time.timestamp, time.dayOnly), 'aihot-time-badge');
      badge.dataset.published = time.timestamp;
      badge.dataset.dayOnly = String(Boolean(time.dayOnly));
      badge.dateTime = new Date(time.timestamp * 1000).toISOString();
      badge.title = `${row.timeLabel}（北京时间）；相对时间依据：${time.basis}`;
      meta.append(badge);
    }
    meta.append(text('span', `${row.source || 'AIHOT'} · ${row.timeLabel || '时间未提供'}（北京时间）`, 'aihot-source'));
    return meta;
  }
  function card(row) {
    const article = text('article', '', 'aihot-article');
    article.dataset.newsKey = row.key;
    const title = text('a', row.title, 'aihot-title');
    title.href = safeLink(row.url) || 'https://aihot.news/';
    title.target = '_blank'; title.rel = 'noopener noreferrer';
    article.append(title, text('p', `${row.matchedLabel || ''} · ${row.inDaily ? '日报收录' : row.selected ? '精选' : '公开动态（未进入精选）'}`, 'aihot-interest-label'));
    if (row.summary) article.append(text('p', row.summary, 'aihot-summary'));
    article.append(publication(row));
    if (row.reason) {
      const detail = text('details', '', 'aihot-reason');
      detail.append(text('summary', '精选理由'), text('p', row.reason));
      article.append(detail);
    }
    return article;
  }
  function reveal(article, index) {
    if (reduceMotion()) return;
    article.style.setProperty('--news-delay', Math.min(index, 7) * 55 + 'ms');
    article.classList.add('aihot-card-enter');
    article.addEventListener('animationend', () => article.classList.remove('aihot-card-enter'), {once:true});
  }
  function apply(data) {
    const list = el('aihot-feed-list');
    const rows = data.items || [];
    const existing = new Map([...list.querySelectorAll('[data-news-key]')].map(node => [node.dataset.newsKey, node]));
    let added = 0;
    const used = new Set();
    for (const [index, row] of rows.entries()) {
      let node = existing.get(row.key);
      const signature = JSON.stringify(row);
      if (!node) {
        node = card(row);
        reveal(node, added++);
      } else if (node.dataset.signature !== signature) {
        const expanded = node.querySelector('details')?.open;
        node.replaceChildren(...card(row).childNodes);
        if (expanded && node.querySelector('details')) node.querySelector('details').open = true;
      }
      node.dataset.signature = signature;
      used.add(node);
      if (list.children[index] !== node) list.insertBefore(node, list.children[index] || null);
      existing.delete(row.key);
    }
    existing.forEach(node => node.remove());
    list.querySelectorAll('[data-news-key]').forEach(node => { if (!used.has(node)) node.remove(); });
    list.querySelectorAll('.aihot-feed-empty').forEach(node => node.remove());
    if (!rows.length) {
      const empty = text('div', '', 'aihot-feed-empty');
      if (data.running) {
        empty.append(text('span', '', 'aihot-feed-skeleton'), text('span', '', 'aihot-feed-skeleton'));
      }
      empty.append(text('p', data.running ? '正在寻找你关注的资讯，匹配后会自动出现。' : data.partial ? '部分来源尚未返回，获取后会自动显示。' : '本轮暂未找到符合关注词的非论文资讯。'));
      list.append(empty);
    }
    el('aihot-feed-count').textContent = rows.length + ' 条可读';
    applied = JSON.stringify(rows);
    pending = null;
    el('aihot-feed-updates').hidden = true;
    updateTimes();
  }
  function isReading() {
    return Boolean(root.querySelector('details[open]')) || root.getBoundingClientRect().top < -40;
  }
  function update(data) {
    const rows = data.items || [];
    updateTimes();
    const progress = el('aihot-feed-progress');
    const ratio = data.queryCount ? Math.min(100, Math.max(0, 100 * data.completedQueries / data.queryCount)) : 0;
    progress.hidden = !data.running;
    progress.setAttribute('aria-valuenow', String(Math.round(ratio)));
    progress.firstElementChild.style.width = Math.max(3, ratio) + '%';
    el('aihot-feed-dot').classList.toggle('is-updating', Boolean(data.running));
    el('aihot-feed-status').textContent = data.running ? (rows.length ? '已有内容可以先看，正在继续补充…' : '正在寻找你关注的资讯…') : data.partial ? '部分来源暂未更新，先看已获取内容' : '本轮更新完成 · 关注资讯已就绪';
    el('aihot-feed-footnote').textContent = data.stale ? '部分卡片来自上次成功获取的数据，恢复后会继续更新。' : '';
    const daily = el('aihot-feed-daily');
    const dailyUrl = safeLink(data.dailyUrl);
    daily.hidden = !dailyUrl;
    if (dailyUrl) { daily.href = dailyUrl; daily.textContent = `${data.dailyDate || ''} 完整日报 ↗`; }
    const signature = JSON.stringify(rows);
    if (signature === applied) {
      pending = null;
      el('aihot-feed-updates').hidden = true;
    } else if (isReading() && el('aihot-feed-list').querySelector('[data-news-key]')) {
      pending = data;
      const keys = new Set([...root.querySelectorAll('[data-news-key]')].map(node => node.dataset.newsKey));
      const count = rows.filter(row => !keys.has(row.key)).length;
      const button = el('aihot-feed-updates');
      button.textContent = count ? `${count} 条新内容 · 点击更新` : '资讯已有更新 · 点击查看';
      button.hidden = false;
    } else {
      apply(data);
    }
  }
  async function poll() {
    if (busy || !root?.isConnected) return;
    clearTimeout(timer);
    if (document.visibilityState === 'hidden') { timer = setTimeout(poll, 10000); return; }
    busy = true;
    let delay = 10000;
    try {
      const response = await fetch('http://127.0.0.1:8182/api/news', {cache:'no-store',signal:AbortSignal.timeout(5000)});
      if (!response.ok) throw new Error('Local feed unavailable');
      const data = await response.json();
      update(data);
      delay = data.running ? 2500 : 60000;
    } catch (_) {
      el('aihot-feed-status').textContent = '连接暂时中断，保留已显示资讯并自动重试';
      el('aihot-feed-dot').classList.remove('is-updating');
      el('aihot-feed-progress').hidden = true;
    } finally {
      busy = false;
      timer = setTimeout(poll, delay);
    }
  }
  function init() {
    if (root?.isConnected) return true;
    root = el('aihot-feed');
    if (!root) return false;
    [...root.querySelectorAll('[data-news-key]')].forEach((node, index) => {
      const source = node.querySelector('.aihot-source');
      if (source && !node.querySelector('.aihot-publication')) {
        source.replaceWith(publication({source:node.dataset.newsSource, timeLabel:node.dataset.newsTimeLabel, timeline:node.dataset.newsTimeline}));
      }
      reveal(node, index);
    });
    updateTimes();
    setInterval(() => { if (root?.isConnected && document.visibilityState === 'visible') updateTimes(); }, 30000);
    el('aihot-feed-updates').addEventListener('click', () => {
      if (!pending) return;
      apply(pending);
      root.scrollIntoView({behavior:reduceMotion() ? 'auto' : 'smooth',block:'start'});
    });
    poll();
    return true;
  }
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible' && root?.isConnected) { updateTimes(); poll(); } });
  if (!init()) {
    const observer = new MutationObserver(() => { if (init()) observer.disconnect(); });
    observer.observe(document.body, {childList:true,subtree:true});
  }
})();
