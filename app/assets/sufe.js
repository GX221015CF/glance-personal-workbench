(() => {
  const bridge = 'http://127.0.0.1:8182';
  let busy = false, timer, state;
  const el = id => document.getElementById(id);
  const node = (tag, value, cls) => {
    const n = document.createElement(tag);
    n.textContent = value;
    if (cls) n.className = cls;
    return n;
  };
  async function request(path, method = 'GET') {
    const auth = await fetch(bridge + '/api/desktop', {cache:'no-store', signal:AbortSignal.timeout(5000)});
    if (!auth.ok) throw new Error('本机学校服务尚未连接，请重新启动工作台');
    const {token} = await auth.json();
    const response = await fetch(bridge + path, {method, cache:'no-store', headers:{'X-Desktop-Token':token}, signal:AbortSignal.timeout(8000)});
    if (!response.ok) throw new Error('学校同步服务暂不可用，请稍后再试');
    return response.json();
  }
  function render(data) {
    state = data;
    el('sufe-notice').textContent = (data.stale ? '上次数据 · ' : '') + data.message;
    el('sufe-updated').textContent = (data.week ? `第 ${data.week} 教学周 · ` : '') + (data.updatedAt ? '同步于 ' + new Date(data.updatedAt).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai',hour12:false}) : '尚未同步');
    el('sufe-refresh').disabled = data.refreshing;
    el('sufe-refresh').textContent = data.refreshing ? '同步中…' : '同步学校数据';
    const today = el('sufe-today'); today.replaceChildren();
    el('sufe-today-label').textContent = '今日课程 · ' + data.date;
    if (!data.available) today.append(node('p', '连接学校后，课表和考试会显示在这里。', 'sufe-muted'));
    else if (!data.week) today.append(node('p', '当前日期不在学校提供的教学周内。', 'sufe-muted'));
    else if (!data.today.length) today.append(node('p', '今日课表无课程安排。', 'sufe-muted'));
    for (const row of data.today || []) {
      const item = node('div', '', 'sufe-row');
      item.append(node('strong', `${row.start}–${row.end}`, 'sufe-time'), node('span', row.name), node('span', row.room, 'sufe-muted'));
      today.append(item);
    }
    const exams = el('sufe-exams'); exams.replaceChildren();
    const published = (data.exams || []).filter(e => e.published && e.date >= data.date).sort((a,b)=>(a.date+a.start).localeCompare(b.date+b.start));
    const pending = (data.exams || []).filter(e => !e.published);
    el('sufe-exams-label').textContent = `考试安排 · ${published.length} 场待考 · ${pending.length} 门待发布`;
    for (const row of published) {
      const item = node('div', '', 'sufe-exam');
      item.append(node('strong', row.name), node('span', `${row.date} ${row.start}–${row.end}`, 'sufe-time'), node('span', row.room, 'sufe-muted'));
      exams.append(item);
    }
    if (data.available && !published.length) exams.append(node('p', '当前没有已公布时间的待考安排。', 'sufe-muted'));
    if (pending.length) exams.append(node('p', '尚未发布：' + pending.map(e=>`${e.name}（${e.group}）`).join('、'), 'sufe-muted'));
    const all = el('sufe-courses'); all.replaceChildren();
    el('sufe-course-count').textContent = `本学期全部课程（${(data.courses || []).length} 门）`;
    for (const row of data.courses || []) {
      const item = node('div', '', 'sufe-course');
      item.append(node('strong', `${row.name} · ${row.number}`), node('span', `${row.teacher} · ${row.credits} 学分`, 'sufe-muted'));
      for (const meeting of row.meetings) item.append(node('p', meeting.raw, 'sufe-muted'));
      all.append(item);
    }
  }
  async function load() {
    if (busy || !el('sufe-dashboard')) return;
    busy = true;
    try { render(await request('/api/sufe')); }
    catch (error) { el('sufe-notice').textContent = error.message; }
    finally { busy = false; }
  }
  function start() {
    if (!el('sufe-dashboard') || el('sufe-dashboard').dataset.ready) return;
    el('sufe-dashboard').dataset.ready = '1';
    el('sufe-refresh').addEventListener('click', async () => {
      el('sufe-refresh').disabled = true;
      try {
        await request('/api/sufe/refresh', 'POST');
        await load();
        setTimeout(load, 3000); setTimeout(load, 12000);
      } catch (error) { el('sufe-notice').textContent = error.message; el('sufe-refresh').disabled = false; }
    });
    load(); clearInterval(timer);
    timer = setInterval(() => {if (document.visibilityState === 'visible') load();}, 60000);
  }
  new MutationObserver(start).observe(document.documentElement, {childList:true,subtree:true});
  start();
})();
