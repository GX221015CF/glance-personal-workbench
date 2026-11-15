(() => {
  const bridge = 'http://127.0.0.1:8182';
  const statusLabels = {
    unsubmitted: '待提交', overdue: '已逾期', submitted: '已提交', graded: '已评分',
    pending_review: '待评阅', offline: '线下提交', not_required: '无需线上提交',
    excused: '已免交', unknown: '提交状态待核对',
  };
  let busy = false, state, timer;
  const el = id => document.getElementById(id);
  function node(tag, text, className) {
    const result = document.createElement(tag);
    result.textContent = text;
    if (className) result.className = className;
    return result;
  }
  function link(text, url) {
    const result = node('a', text);
    const parsed = new URL(url);
    if (parsed.origin !== 'https://canvas.shufe.edu.cn') throw new Error('Canvas 链接地址异常');
    result.href = parsed.href;
    result.target = '_blank';
    result.rel = 'noopener noreferrer';
    return result;
  }
  function formatDate(value) {
    return value ? new Date(value).toLocaleString('zh-CN', {timeZone: 'Asia/Shanghai', hour12: false}) : '未设置';
  }
  function formatSize(size) {
    if (size >= 1024 * 1024) return (size / 1024 / 1024).toFixed(1) + ' MB';
    return Math.ceil(size / 1024) + ' KB';
  }
  async function request(path, method = 'GET') {
    const auth = await fetch(bridge + '/api/desktop', {cache: 'no-store', signal: AbortSignal.timeout(5000)});
    if (!auth.ok) throw new Error('请先启动个人工作台服务');
    const {token} = await auth.json();
    const response = await fetch(bridge + '/api/canvas/v1' + path, {
      method, cache: 'no-store', headers: {'X-Desktop-Token': token}, signal: AbortSignal.timeout(8000),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error?.message || 'Canvas 服务暂时不可用');
    return result;
  }
  function assignmentNode(assignment) {
    const item = node('div', '', 'canvas-assignment');
    item.append(link(assignment.name + ' ↗', assignment.url));
    item.append(node('p', assignment.courseName + ' · ' + (statusLabels[assignment.status] || '状态待核对'),
      assignment.status === 'overdue' ? 'canvas-overdue' : 'sufe-muted'));
    item.append(node('p', '截止时间 · ' + formatDate(assignment.dueAt), 'sufe-time'));
    if (assignment.submission.submittedAt) {
      item.append(node('p', '最近提交 · ' + formatDate(assignment.submission.submittedAt), 'sufe-muted'));
    }
    if (assignment.locked) item.append(node('p', 'Canvas 当前标记为锁定，具体开放情况请查看原页。', 'sufe-muted'));
    const details = document.createElement('details');
    details.append(node('summary', '查看作业要求'), node('div', assignment.description || '作业要求请查看 Canvas 原页。', 'canvas-description'));
    item.append(details);
    return item;
  }
  function renderLists() {
    if (!state) return;
    const selected = el('canvas-course-filter').value;
    const matches = item => !selected || item.courseId === selected;
    const assignments = state.data.assignments.filter(matches).sort((a, b) =>
      (a.dueAt || '9999').localeCompare(b.dueAt || '9999') || a.name.localeCompare(b.name));
    const pending = assignments.filter(a => ['unsubmitted', 'overdue'].includes(a.status));
    el('canvas-pending-title').textContent = `待办作业 · ${pending.length} 项`;
    el('canvas-pending').replaceChildren(...pending.map(assignmentNode));
    if (state.meta.available && !pending.length) {
      el('canvas-pending').append(node('p', '当前同步范围内没有待提交的线上作业。', 'sufe-muted'));
    }
    el('canvas-assignment-title').textContent = `全部作业与提交状态（${assignments.length} 项）`;
    el('canvas-assignments').replaceChildren(...assignments.map(assignmentNode));
    const files = state.data.files.filter(matches);
    el('canvas-materials-title').textContent = `课程资料 · ${files.length} 份`;
    const materials = el('canvas-materials');
    materials.replaceChildren();
    for (const course of state.data.courses.filter(c => !selected || c.id === selected)) {
      const courseFiles = files.filter(f => f.courseId === course.id).sort((a, b) =>
        (b.modifiedAt || b.updatedAt || '').localeCompare(a.modifiedAt || a.updatedAt || ''));
      const group = node('details', '', 'canvas-course-materials');
      group.open = Boolean(selected);
      group.append(node('summary', `${course.name} · ${courseFiles.length} 份资料`));
      if (!courseFiles.length) group.append(node('p', 'Canvas 文件目录暂无资料。', 'sufe-muted'));
      for (const file of courseFiles) {
        const item = node('div', '', 'canvas-material');
        item.append(link(file.name + ' ↗', file.url));
        item.append(node('p', [file.folder, formatSize(file.size), '更新于 ' + formatDate(file.modifiedAt || file.updatedAt)].filter(Boolean).join(' · '), 'sufe-muted'));
        if (file.locked || file.hidden) item.append(node('p', 'Canvas 当前标记为受限，请查看原页的开放情况。', 'sufe-muted'));
        group.append(item);
      }
      materials.append(group);
    }
  }
  function render(result) {
    const dataChanged = !state || JSON.stringify(state.data) !== JSON.stringify(result.data);
    state = result;
    const {meta, data} = result;
    el('canvas-notice').textContent = (meta.stale ? '上次数据 · ' : '') + meta.message;
    el('canvas-updated').textContent = meta.updatedAt
      ? `${data.counts.courses} 门课程 · 同步于 ${formatDate(meta.updatedAt)}` : '尚未同步';
    el('canvas-refresh').disabled = meta.refreshing;
    el('canvas-refresh').textContent = meta.refreshing ? '同步中…' : '同步 Canvas';
    if (!dataChanged) return;
    const filter = el('canvas-course-filter');
    const previous = filter.value;
    filter.replaceChildren(new Option('全部课程', ''), ...data.courses.map(c => new Option(c.name, c.id)));
    if (data.courses.some(c => c.id === previous)) filter.value = previous;
    renderLists();
  }
  async function load() {
    if (busy || !el('canvas-dashboard')) return;
    busy = true;
    try { render(await request('')); }
    catch (error) { el('canvas-notice').textContent = error.message; }
    finally { busy = false; }
  }
  function start() {
    const dashboard = el('canvas-dashboard');
    if (!dashboard || dashboard.dataset.ready) return;
    dashboard.dataset.ready = '1';
    el('canvas-course-filter').addEventListener('change', renderLists);
    el('canvas-refresh').addEventListener('click', async () => {
      el('canvas-refresh').disabled = true;
      try {
        await request('/refresh', 'POST');
        await load();
        setTimeout(load, 4000);
        setTimeout(load, 15000);
      } catch (error) {
        el('canvas-notice').textContent = error.message;
        el('canvas-refresh').disabled = false;
      }
    });
    load();
    clearInterval(timer);
    timer = setInterval(() => { if (document.visibilityState === 'visible') load(); }, 60000);
  }
  new MutationObserver(start).observe(document.documentElement, {childList: true, subtree: true});
  start();
})();
