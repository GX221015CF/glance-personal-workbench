(() => {
  'use strict';
  const bridge = 'http://127.0.0.1:8182';
  const storageKey = 'workbench-study-view-v1';
  const offlineDatePrefix = 'workbench-study-offline-date-v1:';
  const offlineSubmissionPrefix = 'workbench-study-offline-submission-v1:';
  const dateDrafts = new Map();
  let sharedMarks = null;
  function marksMessage(text) {
    const line = document.getElementById('study-local-marks-status');
    if (line) { line.textContent = text; line.hidden = !text; }
  }
  async function shareMarks(records) {
    try {
      const auth = await fetch(bridge + '/api/desktop', {cache:'no-store',signal:AbortSignal.timeout(4000)});
      if (!auth.ok) throw new Error();
      const {token} = await auth.json();
      const response = await fetch(bridge + '/api/desktop/study-marks', {method:'POST',
        headers:{'Content-Type':'application/json','X-Desktop-Token':token},body:JSON.stringify({records}),signal:AbortSignal.timeout(8000)});
      if (!response.ok) throw new Error();
      const result = (await response.json()).data;
      sharedMarks = result.records;
      marksMessage(result.conflictCount ? `有 ${result.conflictCount} 项已确认日期存在不同记录，双方记录已保留，需核对。` : '');
      return true;
    } catch {
      if (sharedMarks) sharedMarks = {...sharedMarks, ...records};
      marksMessage('作业标记暂未共享到桌面；本浏览器原有记录仍保留，请确认后台已重启。'); return false;
    }
  }
  async function loadSharedMarks() {
    const records = {};
    try {
      for (let i = 0; i < localStorage.length; i++) {
        const key = localStorage.key(i);
        if (!/^workbench-study-offline-(date|submission)-v1:\d{1,15}:\d{1,15}$/.test(key)) continue;
        try {
          const value = JSON.parse(localStorage.getItem(key));
          if (key.startsWith(offlineDatePrefix) && (value?.dueDate === null || StudyOffline.validDate(value?.dueDate))) records[key] = {dueDate:value.dueDate};
          if (key.startsWith(offlineSubmissionPrefix) && value?.submitted === true && typeof value.submittedAt === 'string') records[key] = {submitted:true,submittedAt:value.submittedAt};
        } catch {}
      }
    } catch {}
    if (await shareMarks(records)) preserveReading(renderCanvas);
  }
  const labels = {
    unsubmitted: '待提交', overdue: '已逾期', submitted: '已提交', graded: '已评分',
    pending_review: '待评阅', offline: '线下提交', not_required: '无需线上提交',
    excused: '已免交', unknown: '提交状态待核对',
  };
  const completed = new Set(['submitted', 'graded', 'pending_review']);
  const pending = new Set(['unsubmitted', 'overdue']);
  let root, canvas, school, academic, interval, restored = false;
  const sources = {
    canvas: {path: '/api/canvas/v1', busy: false, error: '', poll: null},
    school: {path: '/api/sufe/v1', busy: false, error: '', poll: null},
  };
  let stored = {};
  try { stored = JSON.parse(sessionStorage.getItem(storageKey)) || {}; } catch {}
  const view = {
    course: typeof stored.course === 'string' ? stored.course : '',
    tab: ['pending', 'completed', 'other', 'all'].includes(stored.tab) ? stored.tab : 'pending',
    offlineStatus: ['overdue', 'submitted', 'unsubmitted'].includes(stored.offlineStatus) ? stored.offlineStatus : 'all',
    query: typeof stored.query === 'string' ? stored.query.slice(0, 200) : '',
    sort: StudyMaterialDates.restoreSort(stored.sort),
    fileType: StudyFileTypes.restore(stored.fileType),
    fileLimit: Math.max(6, Math.min(Number(stored.fileLimit) || 6, 200)),
    assignmentLimit: Math.max(6, Math.min(Number(stored.assignmentLimit) || 6, 200)),
    open: new Set(Array.isArray(stored.open) ? stored.open.filter(x => typeof x === 'string') : []),
  };
  const el = id => document.getElementById(id);
  function node(tag, text = '', className = '') {
    const element = document.createElement(tag);
    element.textContent = text;
    if (className) element.className = className;
    return element;
  }
  function save() {
    try {
      sessionStorage.setItem(storageKey, JSON.stringify({...view, open: [...view.open].slice(-100), scrollY: window.scrollY}));
    } catch {}
  }
  function readOfflineDate(courseId, fileId) {
    try {
      const key = `${offlineDatePrefix}${courseId}:${fileId}`;
      const value = sharedMarks?.[key] || JSON.parse(localStorage.getItem(key));
      return value && (value.dueDate === null || StudyOffline.validDate(value.dueDate)) ? value.dueDate : undefined;
    } catch { return undefined; }
  }
  function allAssignments() {
    if (!canvas?.meta.available) return [];
    const data=canvas.data;
    const files=(data.sourceFiles||data.files).filter(file=>!(file.sources||[]).some(source=>source.kind==='assignment'
      && data.assignments.some(assignment=>assignment.courseId===file.courseId&&assignment.id===source.assignmentId)));
    return [...data.assignments, ...StudyOffline.buildAssignments({...data,files}, school, readOfflineDate, readOfflineSubmission)];
  }
  function readOfflineSubmission(courseId, fileId) {
    try { const key = `${offlineSubmissionPrefix}${courseId}:${fileId}`; return sharedMarks?.[key] || JSON.parse(localStorage.getItem(key)); }
    catch { return null; }
  }
  function external(text, url, className = '') {
    const parsed = new URL(url);
    if (parsed.origin !== 'https://canvas.shufe.edu.cn') throw new Error('Canvas 链接地址异常');
    const anchor = node('a', text, className);
    anchor.href = parsed.href;
    anchor.target = '_blank';
    anchor.rel = 'noopener noreferrer';
    return anchor;
  }
  function dateText(value, includeYear = false) {
    if (!value) return '未设置截止时间';
    return new Date(value).toLocaleString('zh-CN', {
      timeZone: 'Asia/Shanghai', year: includeYear ? 'numeric' : undefined,
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
    });
  }
  function remaining(value) {
    if (!value) return '';
    const difference = new Date(value).getTime() - Date.now();
    const absolute = Math.ceil(Math.abs(difference) / 60000);
    const duration = absolute >= 1440 ? `${Math.floor(absolute / 1440)} 天` : absolute >= 60 ? `${Math.floor(absolute / 60)} 小时` : `${Math.max(1, absolute)} 分钟`;
    return difference < 0 ? `已过截止 ${duration}` : `剩余 ${duration}`;
  }
  function sizeText(value) {
    if (!Number.isFinite(value) || value < 0) return '大小未提供';
    if (value >= 1048576) return `${(value / 1048576).toFixed(1)} MB`;
    return value ? `${Math.ceil(value / 1024)} KB` : '0 B';
  }
  function fileKind(file) {
    return StudyFileTypes.describe(file).kind;
  }
  function fileDate(file) {
    return [file.updatedAt, file.modifiedAt].filter(Boolean).sort().at(-1) || '';
  }
  function badge(text, kind = '') { return node('span', text, `study-badge ${kind}`); }
  function empty(container, title, hint) {
    const box = node('div', '', 'study-empty');
    box.append(node('strong', title), node('p', hint));
    container.replaceChildren(box);
  }
  function availableHint(meta, noun, name = 'canvas') {
    if (!meta && sources[name].error) return [`${noun}暂不可用`, sources[name].error];
    if (!meta) return [`正在读取${noun}`, '请稍候，正在连接本机服务。'];
    if (meta.needsLogin) return [`尚未连接${noun}`, '请使用本页的连接入口，按提示完成学校登录。'];
    return [`${noun}暂不可用`, meta.message || '请稍后同步。'];
  }
  function preserveReading(callback) {
    const candidates = [...root.querySelectorAll('[data-reading-key]')].filter(item => {
      const rect = item.getBoundingClientRect();
      return rect.bottom > 80 && rect.top < innerHeight;
    });
    candidates.sort((a, b) => Math.abs(a.getBoundingClientRect().top - 80) - Math.abs(b.getBoundingClientRect().top - 80));
    const anchor = candidates[0];
    const key = anchor?.dataset.readingKey;
    const before = anchor?.getBoundingClientRect().top;
    callback();
    const current = anchor?.isConnected ? anchor : [...root.querySelectorAll('[data-reading-key]')].find(item => item.dataset.readingKey === key);
    if (current) {
      const delta = current.getBoundingClientRect().top - before;
      if (Math.abs(delta) > 1) window.scrollBy(0, delta);
    }
  }
  function reconcile(container, rows, key, build) {
    const existing = new Map([...container.children].map(child => [child.dataset.key, child]));
    let cursor = container.firstElementChild;
    for (const row of rows) {
      const id = key(row);
      const signature = JSON.stringify(row);
      let item = existing.get(id);
      if (!item || item._studySignature !== signature) {
        const replacement = build(row);
        replacement.dataset.key = id;
        replacement.dataset.readingKey = id;
        replacement._studySignature = signature;
        if (item) {
          const description = item.querySelector('.study-description');
          const offset = description?.scrollTop || 0;
          item.replaceWith(replacement);
          const nextDescription = replacement.querySelector('.study-description');
          if (nextDescription) nextDescription.scrollTop = offset;
          if (cursor === item) cursor = replacement;
        }
        item = replacement;
      }
      existing.delete(id);
      if (item !== cursor) container.insertBefore(item, cursor);
      cursor = item.nextElementSibling;
    }
    for (const item of existing.values()) item.remove();
  }
  function disclosure(id, title) {
    const details = node('details', '', 'study-details');
    details.id = id;
    details.open = view.open.has(id);
    details.append(node('summary', title));
    return details;
  }
  function sourceStatus(name, meta) {
    const source = sources[name];
    const message=source.error?'数据读取失败，保留当前内容':!meta?.available?'尚无保存数据':meta?.stale||meta?.needsLogin?'上次保存的数据':meta?.materialWarningCount?`已更新 · ${meta.materialWarningCount} 份附件暂不可读取`:'已保存数据';
    const ids = name === 'canvas' ? ['study-canvas-status'] : ['study-school-status', 'study-exam-status'];
    for (const id of ids) {
      const target = el(id);
      target.className = `study-source ${source.error||meta?.materialWarningCount?'is-warning':''}`;
      target.replaceChildren(node('span', message), node('time', meta?.updatedAt ? dateText(meta.updatedAt) : '尚无保存时间'));
      target.title = source.error || (meta?.updatedAt?`数据最近保存 ${dateText(meta.updatedAt, true)}`:'尚无保存数据');
    }
    publishSourceState(name, meta);
  }
  function publishSourceState(name, meta) {
    if (meta) window.dispatchEvent(new CustomEvent('study-source-state', {detail: {target: name === 'school' ? 'sufe' : name === 'academic' ? 'academic' : 'canvas', meta}}));
  }
  window.addEventListener('study-source-state-request', () => {
    publishSourceState('school', school?.meta);
    publishSourceState('canvas', canvas?.meta);
    publishSourceState('academic', academic?.meta);
  });
  function assignmentCard(assignment) {
    if (assignment.source === 'file') return offlineCard(assignment);
    const urgent = pending.has(assignment.status) && assignment.dueAt && new Date(assignment.dueAt) - Date.now() <= 48 * 3600000;
    const cardState = completed.has(assignment.status) ? 'submitted' : assignment.status === 'overdue' ? 'overdue' : 'unsubmitted';
    const card = node('article', '', `study-assignment is-${cardState} ${urgent ? 'is-urgent' : ''}`);
    const top = node('div', '', 'study-card-top');
    top.append(node('h3', assignment.courseName, 'study-course-label'), badge(labels[assignment.status] || labels.unknown, `study-assignment-state is-${cardState}`));
    const heading = node('p', '', 'study-assignment-name');
    heading.append(external(assignment.name, assignment.url, 'study-assignment-title'));
    const due = node('div', '', 'study-due');
    due.append(node('span', assignment.dueAt ? `截止 ${dateText(assignment.dueAt)}` : '未设置截止时间', 'study-deadline'));
    if (pending.has(assignment.status) && assignment.dueAt) due.append(badge(assignment.remainingText, urgent ? 'is-overdue' : 'is-time'));
    card.append(top, heading, due);
    if (assignment.submission.submittedAt) card.append(node('p', `最近提交 ${dateText(assignment.submission.submittedAt)}`, 'study-submitted'));
    if (assignment.locked) card.append(node('p', '当前已锁定 · 开放情况请查看 Canvas', 'study-restriction'));
    if (assignment.unlockAt && new Date(assignment.unlockAt) > Date.now()) card.append(node('p', `开放时间 ${dateText(assignment.unlockAt)}`, 'study-restriction'));
    const details = disclosure(`study-requirements-${assignment.courseId}-${assignment.id}`, '查看作业要求');
    const description = node('div', assignment.description || '此作业没有同步到文字要求，请打开 Canvas 原页查看。', 'study-description');
    description.tabIndex = 0;
    description.setAttribute('role', 'region');
    description.setAttribute('aria-label', `${assignment.name}的作业要求`);
    details.append(description);
    const footer = node('div', '', 'study-assignment-footer');
    footer.append(external('进入 Canvas ↗', assignment.url, 'study-open-assignment'));
    card.append(details, footer);
    return card;
  }
  function offlineCard(assignment) {
    const stateLabels = {overdue: '已过期', submitted: '已提交', unsubmitted: '未提交'};
    const state = assignment.offlineStatus;
    const card = node('article', '', `study-assignment study-offline-assignment is-${state} is-offline-${state}`);
    const top = node('div', '', 'study-card-top');
    const statusText = state === 'submitted' && assignment.submittedAt
      ? `已提交 · ${dateText(assignment.submittedAt).slice(0, 5)}`
      : stateLabels[state];
    top.append(node('h3', assignment.courseName, 'study-course-label'), badge(statusText, `study-assignment-state is-${state}`));
    const heading = node('p', '', 'study-assignment-name');
    heading.append(external(assignment.name, assignment.url, 'study-assignment-title'));
    const dateConfirmed = assignment.dateSource === 'manual' && StudyOffline.validDate(assignment.dueDate);
    const key = `${assignment.courseId}:${assignment.fileId}`;
    const feedback = node('p', '', 'study-offline-feedback');
    feedback.setAttribute('role', 'status');
    const dates = node('div', '', 'study-offline-dates');
    const due = node(dateConfirmed ? 'div' : 'form', '', 'study-offline-due');
    due.append(node('span', '交作业日期', 'study-date-label'));
    if (dateConfirmed) {
      due.append(node('strong', assignment.dueDate, 'study-offline-due-value'), badge('已确认', 'is-confirmed'));
    } else {
      const input = document.createElement('input');
      input.type = 'date';
      input.required = true;
      input.className = 'study-offline-due-value study-offline-date-input';
      input.setAttribute('aria-label', `${assignment.name}的交作业日期`);
      input.title = '直接修改日期，再点击旁边的标签确认';
      input.value = dateDrafts.has(key) ? dateDrafts.get(key) : assignment.dueDate || '';
      const confirm = node('button', dateDrafts.has(key) ? '确认日期' : assignment.dateSource === 'estimated' ? '推算值' : '确认日期', 'study-badge study-date-confirm is-time');
      confirm.type = 'submit';
      confirm.title = '确认当前日期，确认后不可修改';
      confirm.setAttribute('aria-label', `确认${assignment.name}的交作业日期`);
      input.addEventListener('input', () => {
        dateDrafts.set(key, input.value);
        confirm.textContent = '确认日期';
        feedback.textContent = '';
      });
      due.addEventListener('submit', async event => {
        event.preventDefault();
        try {
          const storageId = offlineDatePrefix + key;
          // An already confirmed date must also remain locked across open tabs.
          if (StudyOffline.validDate(readOfflineDate(assignment.courseId, assignment.fileId))) {
            dateDrafts.delete(key);
            preserveReading(renderCanvas);
            return;
          }
          if (!StudyOffline.validDate(input.value)) {
            feedback.textContent = '请填写有效的交作业日期。';
            return;
          }
          localStorage.setItem(storageId, JSON.stringify({dueDate: input.value}));
          await shareMarks({[storageId]:{dueDate:input.value}});
          dateDrafts.delete(key);
          preserveReading(renderCanvas);
        } catch { feedback.textContent = '浏览器未能保存，请检查是否允许本机网站存储。'; }
      });
      due.append(input, confirm);
    }
    const uploaded = node('div', '', 'study-offline-upload');
    if (assignment.uploadFallback) uploaded.title = '原始上传时间未提供，此处使用文件时间';
    const uploadTime = assignment.uploadedAt ? new Date(Date.parse(assignment.uploadedAt) + 8 * 3600000).toISOString() : null;
    uploaded.append(node('span', '上传时间', 'study-date-label'), node('strong', uploadTime ? uploadTime.slice(0, 10) : '未提供', 'study-offline-upload-value'));
    dates.append(due, uploaded);
    card.append(top, heading, dates);
    const footer = node('div', '', 'study-assignment-footer');
    if (state !== 'submitted') {
      const submit = node('button', '已提交', 'study-offline-toggle');
      submit.type = 'button';
      submit.setAttribute('aria-label', `将${assignment.name}标记为已提交`);
      submit.addEventListener('click', async () => {
        try {
          const previous = readOfflineSubmission(assignment.courseId, assignment.fileId);
          if (!previous?.submitted) {
            const value = {submitted: true, submittedAt: new Date().toISOString()};
            localStorage.setItem(offlineSubmissionPrefix + key, JSON.stringify(value));
            await shareMarks({[offlineSubmissionPrefix + key]:value});
          }
          preserveReading(renderCanvas);
        } catch { feedback.textContent = '提交状态未能保存，请检查浏览器存储。'; }
      });
      footer.append(submit);
    }
    footer.append(external('打开作业 ↗', assignment.url, 'study-open-assignment'));
    card.append(footer, feedback);
    return card;
  }
  function assignmentGroup(assignment) {
    if (pending.has(assignment.status)) return 'pending';
    return completed.has(assignment.status) ? 'completed' : 'other';
  }
  function scoped(items) { return items.filter(item => !view.course || item.courseId === view.course); }
  function renderAssignments() {
    const meta = canvas?.meta;
    const list = el('study-assignments');
    if (!meta?.available) {
      empty(list, ...availableHint(meta, 'Canvas 作业'));
      el('study-assignment-count').textContent = '—';
      el('study-assignment-more').hidden = true;
      return;
    }
    const assignments = scoped(allAssignments());
    for (const tab of root.querySelectorAll('[data-assignment-tab]')) {
      const group = tab.dataset.assignmentTab;
      const count = group === 'all' ? assignments.length : assignments.filter(item => assignmentGroup(item) === group).length;
      tab.querySelector('span').textContent = count;
      tab.setAttribute('aria-pressed', String(group === view.tab));
    }
    const offlineFiles = assignments.filter(item => item.source === 'file');
    el('study-offline-status-filter').hidden = view.tab !== 'other';
    for (const button of root.querySelectorAll('[data-offline-status]')) {
      const status = button.dataset.offlineStatus;
      button.querySelector('span').textContent = status === 'all' ? offlineFiles.length : offlineFiles.filter(item => item.offlineStatus === status).length;
      button.setAttribute('aria-pressed', String(view.offlineStatus === status));
    }
    const rows = assignments.filter(item => (view.tab === 'all' || assignmentGroup(item) === view.tab)
      && (view.tab !== 'other' || view.offlineStatus === 'all' || item.source === 'file' && item.offlineStatus === view.offlineStatus));
    rows.sort((a, b) => {
      if (view.tab === 'completed') return (b.submission.submittedAt || b.updatedAt || '').localeCompare(a.submission.submittedAt || a.updatedAt || '');
      const rank = item => item.source === 'file' ? {overdue: 0, unsubmitted: 1, submitted: 3}[item.offlineStatus] : item.status === 'overdue' ? 0 : pending.has(item.status) ? 1 : 2;
      return rank(a) - rank(b) || (a.dueAt || a.dueDate || '9999').localeCompare(b.dueAt || b.dueDate || '9999') || a.name.localeCompare(b.name, 'zh-CN');
    });
    el('study-assignment-heading').textContent = '课程作业';
    const assignmentDescription = el('study-assignment-description');
    assignmentDescription.hidden = view.tab === 'other';
    assignmentDescription.textContent = '优先查看逾期与近期截止作业，进入 Canvas 完成提交。';
    el('study-assignment-count').textContent = `${rows.length} 项`;
    const visible = rows.slice(0, view.assignmentLimit).map(item => ({...item, remainingText: pending.has(item.status) ? remaining(item.dueAt) : ''}));
    if (rows.length) reconcile(list, visible, item => `assignment-${item.courseId}-${item.id}`, assignmentCard);
    else empty(list, view.tab === 'pending' ? '当前没有待处理的线上作业' : '此分类暂无记录', view.tab === 'other' ? '可以切换全部、已过期、未提交和已提交，查看其他线下文件作业。' : view.course ? '仅查看所选课程，可切换其他课程或作业分类。' : '可切换分类，查看已提交、已评分和线下作业。');
    el('study-assignment-more').hidden = rows.length <= view.assignmentLimit;
    el('study-assignment-more').textContent = `继续查看作业 · 已显示 ${Math.min(rows.length, view.assignmentLimit)} / ${rows.length}`;
  }
  function materialCard(file) {
    const card = node('article', '', 'study-file');
    const extension = StudyFileTypes.describe(file).label;
    const icon = node('span', extension, `study-file-type is-${fileKind(file)}`);
    icon.setAttribute('aria-label', `文件类型 ${extension}`);
    const content = node('div', '', 'study-file-content');
    const heading = node('h3');
    const anchor = external(file.name, file.url, 'study-file-link');
    anchor.title = file.name;
    heading.append(anchor);
    const titleRow = node('div', '', 'study-file-title-row');
    titleRow.append(heading);
    const metadata = node('details', '', 'study-file-meta study-file-info');
    metadata.id=`study-file-info-${file.courseId}-${file.groupId||file.id}`;
    if((file.memberIds||[]).some(id=>view.open.has(`study-file-info-${file.courseId}-${id}`)))view.open.add(metadata.id);
    metadata.open=view.open.has(metadata.id);
    const uploadDate = node('summary', '', 'study-file-upload-date');
    uploadDate.setAttribute('aria-label',`查看 ${file.name} 的日期、大小和来源详情`);
    const createdDetail=node('p'),updateDate=node('p','','study-file-updated');
    metadata.append(uploadDate,createdDetail,updateDate);
    const courseLine=node('p',file.courseName,'study-file-course');courseLine.hidden=!!view.course;
    content.append(titleRow,courseLine,metadata);
    card._studyMaterialDates = {file, uploadDate, createdDetail, updateDate};
    updateMaterialDates(card);
    if (file.folder) {
      const folder = node('p', file.folder, 'study-file-folder');
      folder.title = file.folder;
      metadata.append(folder);
    }
    const assignmentSources=(file.sources||[]).filter(source=>source.kind==='assignment');
    if(assignmentSources.length){
      const origin=node('p','','study-file-folder');
      origin.append(document.createTextNode('作业附件 · '));
      for(const [index,source] of assignmentSources.entries()){
        if(index)origin.append(document.createTextNode(' · '));
        origin.append(external(source.name||'查看作业',source.url));
      }
      metadata.append(origin);
    }
    if(file.members?.length>1){
      const originals=node('details'),label=node('summary',`来源记录 ${file.members.length} 条`);
      originals.append(label);
      for(const member of file.members){
        const detail=node('p');detail.append(external(`${member.name} · ID ${member.id}`,member.url),
          document.createTextNode(` · ${member.size??'未知'} 字节 · ${member.createdAt||'创建时间未提供'}${member.hidden?' · 文件列表隐藏':''}${member.locked?' · 已锁定':''}`));
        originals.append(detail);
      }
      metadata.append(originals);
    }
    if(file.metadataUnavailable)content.append(node('p','附件信息暂不可读取，更新学校数据可重试','study-restriction'));
    else if(file.locked)content.append(node('p','受限资料 · 开放情况请查看 Canvas','study-restriction'));
    const open = external('↗', file.url, 'study-file-open');
    open.setAttribute('aria-label', `打开文件 ${file.name}`);
    card.append(icon, content, open);
    return card;
  }
  function materialTime(value, text, title) {
    const time = node('time', text);
    time.dateTime = value.datetime;
    time.title = title;
    return time;
  }
  function updateMaterialDates(card, now = Date.now()) {
    const record = card._studyMaterialDates;
    if (!record) return;
    const {file, uploadDate, createdDetail, updateDate} = record;
    const dates = StudyMaterialDates.describe(file, fileDate(file), now);
    const signature = JSON.stringify(dates);
    if (record.signature === signature) return;
    record.signature = signature;
    const fullTime=value=>new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
    if (dates.upload.status === 'valid') {
      uploadDate.replaceChildren(materialTime(dates.upload,dates.upload.date.slice(5)+' 创建',`Canvas 文件记录创建时间 ${file.createdAt}`));
      createdDetail.replaceChildren(document.createTextNode('Canvas 文件记录创建于 '),materialTime(dates.upload,fullTime(dates.upload.datetime),'Asia/Shanghai'));
    } else {
      uploadDate.replaceChildren(node('span',dates.upload.status==='missing'?'日期未提供':'日期异常'));
      createdDetail.replaceChildren(node('span',dates.upload.status==='future'?'Canvas 记录创建时间晚于当前时间，需核对来源日期和本机时钟。':'Canvas 未提供有效的文件记录创建时间。'));
    }
    uploadDate.append(node('span',` · ${sizeText(file.size)}`,'study-file-size'));
    updateDate.hidden = !dates.update;
    updateDate.replaceChildren();
    if (dates.update) updateDate.append(document.createTextNode('文件记录更新于 '), materialTime(dates.update,fullTime(dates.update.datetime),'内容变更及操作者未经核实'));
  }
  function refreshMaterialDates() {
    if (!root?.isConnected) return;
    const now = Date.now();
    for (const card of el('study-files').children) updateMaterialDates(card, now);
  }
  function renderMaterials() {
    const list = el('study-files');
    if (!canvas?.meta.available) {
      empty(list, ...availableHint(canvas?.meta, '课程资料'));
      el('study-files-count').textContent = '—';
      el('study-files-more').hidden = true;
      return;
    }
    const query = view.query.trim().toLocaleLowerCase();
    const all = scoped(canvas.data.files);
    const matched = all.filter(file => StudyFileTypes.matches(file,view.fileType)
      && (!query || `${file.name} ${file.folder || ''}`.toLocaleLowerCase().includes(query)));
    window.StudyIma?.setContext({files:canvas.data.files,courses:canvas.data.courses,courseId:view.course,matchedFiles:matched});
    const rows = window.StudyIma ? window.StudyIma.filter(matched) : matched;
    const now = Date.now();
    rows.sort((a, b) => StudyMaterialDates.compareFiles(a, b, view.sort, now));
    window.StudyIma?.setRows(rows);
    el('study-files-count').textContent = `${rows.length} 份`;
    el('study-search-clear').hidden = !view.query;
    if (rows.length) {
      reconcile(list, rows.slice(0, view.fileLimit), file => `file-${file.courseId}-${file.groupId||file.id}`, materialCard);
      refreshMaterialDates();
      for (const card of list.children) {
        const file = rows.find(file => card.dataset.key === `file-${file.courseId}-${file.groupId||file.id}`);
        const courseLine=card.querySelector('.study-file-course');if(courseLine)courseLine.hidden=!!view.course;
        if (file) window.StudyIma?.decorate(card,file);
      }
    }
    else empty(list, all.length ? '没有找到匹配资料' : '此课程暂无文件资料', all.length ? '试试其他文件名、课程，或清除搜索条件。' : '课程页面或外部工具中的内容可进入 Canvas 查看。');
    el('study-files-more').hidden = rows.length <= view.fileLimit;
    el('study-files-more').textContent = `继续查看资料 · ${Math.min(rows.length, view.fileLimit)} / ${rows.length}`;
  }
  function selectCourse(id) {
    view.course = id;
    view.fileLimit = 6;
    view.assignmentLimit = 6;
    renderCanvas();
    save();
  }
  function courseAttention(assignments, courseId = '', at = Date.now()) {
    let pendingCount = 0, overdueCount = 0, dueSoonCount = 0;
    for (const item of assignments) {
      if (courseId && item.courseId !== courseId) continue;
      const state = item.source === 'file' ? item.offlineStatus : item.status;
      if (!pending.has(state)) continue;
      pendingCount++;
      const due = Date.parse(item.source === 'file' && item.dueDate ? `${item.dueDate}T23:59:59.999+08:00` : item.dueAt);
      if (state === 'overdue' || Number.isFinite(due) && due < at) overdueCount++;
      else if (Number.isFinite(due) && due - at <= 48 * 3600000) dueSoonCount++;
    }
    return {pendingCount, overdueCount, dueSoonCount};
  }
  function renderCourses() {
    const courses = canvas?.data.courses || [];
    if (canvas?.meta.available && view.course && !courses.some(course => course.id === view.course)) view.course = '';
    const selected = courses.find(course => course.id === view.course);
    for (const options of [el('study-course-filter'), el('study-file-course')]) {
      if (options.dataset.signature !== JSON.stringify(courses.map(course => [course.id, course.name]))) {
        options.replaceChildren(new Option('全部课程', ''), ...courses.map(course => new Option(course.name, course.id)));
        options.dataset.signature = JSON.stringify(courses.map(course => [course.id, course.name]));
      }
      options.value = view.course;
    }
    const assignments = allAssignments();
    const navigation = [{id: '', name: '全部课程'}, ...courses].map(course => ({
      ...course, ...courseAttention(assignments, course.id),
    }));
    reconcile(el('study-course-navigation'), navigation, course => `course-${course.id}`, course => {
      const button = node('button', '', 'study-course-button');
      button.type = 'button';
      button.dataset.course = course.id;
      const count = course.pendingCount;
      const urgent = course.overdueCount || course.dueSoonCount;
      const status = count
        ? [`${count} 项待处理`, course.overdueCount ? `${course.overdueCount} 项已逾期` : '', course.dueSoonCount ? `${course.dueSoonCount} 项在 48 小时内截止` : ''].filter(Boolean).join(' · ')
        : canvas?.meta.available ? '暂无待处理作业' : '等待连接 Canvas';
      const indicator = node('span', count ? String(count) : '—', `study-course-pending ${count ? 'has-items' : ''} ${urgent ? 'is-urgent' : ''}`);
      indicator.setAttribute('aria-hidden', 'true');
      button.title = status;
      button.setAttribute('aria-label', `${course.name}，${status}`);
      button.append(node('span', course.name), indicator);
      button.addEventListener('click', () => selectCourse(course.id));
      return button;
    });
    for (const button of root.querySelectorAll('[data-course]')) button.setAttribute('aria-pressed', String(button.dataset.course === view.course));
    el('study-course-count').textContent = courses.length ? `${courses.length} 门` : '—';
    el('study-scope').textContent = selected ? selected.name : '全部 Canvas 课程';
    el('study-scope-reset').hidden = !view.course;
    const inferred = scoped(allAssignments()).filter(item => item.source === 'file').length;
    el('study-scope-count').textContent = canvas?.meta.available ? `${scoped(canvas.data.assignments).length} 项 Canvas 作业 · ${inferred} 项文件识别 · ${scoped(canvas.data.files).length} 份资料` : '等待 Canvas 数据';
    const courseLink = el('study-course-open');
    courseLink.hidden = !selected;
    if (selected) courseLink.href = new URL(selected.url).origin === 'https://canvas.shufe.edu.cn' ? selected.url : 'https://canvas.shufe.edu.cn/';
  }
  function renderCanvas() {
    if (!root?.isConnected) return;
    sourceStatus('canvas', canvas?.meta);
    renderCourses();
    renderAssignments();
    renderMaterials();
    el('study-pending-total').textContent = canvas?.meta.available ? courseAttention(allAssignments()).pendingCount : '—';
    el('study-material-total').textContent = canvas?.meta.available ? canvas.data.files.length : '—';
  }
  function examCard(exam, next = false) {
    const card = node('article', '', `study-exam ${next ? 'is-next' : ''}`);
    const days = Math.round((Date.parse(`${exam.date}T00:00:00+08:00`) - Date.parse(`${school.data.date}T00:00:00+08:00`)) / 86400000);
    const top = node('div', '', 'study-card-top');
    top.append(node('strong', exam.name), badge(days < 0 ? '已结束' : days === 0 ? '今天' : `${days} 天后`, days >= 0 && days <= 14 ? 'is-time' : ''));
    card.append(top, node('p', `${exam.date.slice(5).replace('-', '/')}  ${exam.start}–${exam.end}`, 'study-exam-time'), node('p', exam.room || '地点待公布', 'study-exam-room'));
    return card;
  }
  function renderSchool() {
    if (!root?.isConnected) return;
    sourceStatus('school', school?.meta);
    const data = school?.data;
    const today = el('study-today');
    el('study-date').textContent = new Date().toLocaleDateString('zh-CN', {timeZone: 'Asia/Shanghai', month: 'long', day: 'numeric', weekday: 'long'});
    el('study-teaching-week').textContent = data?.week ? `第 ${data.week} 教学周` : '教学周待核对';
    if (!school?.meta.available) {
      empty(today, ...availableHint(school?.meta, '教务课表', 'school'));
      empty(el('study-next-exam'), ...availableHint(school?.meta, '考试安排', 'school'));
      el('study-today-total').textContent = '—';
      return;
    }
    el('study-today-total').textContent = data.today.length;
    el('study-today-date').textContent = data.date.slice(5).replace('-', '/');
    if (data.today.length) reconcile(today, data.today, item => `today-${item.number}-${item.start}`, item => {
      const row = node('article', '', 'study-meeting');
      row.append(node('time', `${item.start}–${item.end}`), node('strong', item.name), node('p', item.room || '教室未提供'));
      return row;
    });
    else empty(today, data.week ? '今天没有课程安排' : '当前日期不在教学周内', school.meta.stale ? '当前依据上次同步课表，请核对学校通知。' : '以教务课表和学校调课通知为准。');
    const published = data.exams.filter(exam => exam.published).sort((a, b) => (a.date + a.start).localeCompare(b.date + b.start));
    const upcoming = published.filter(exam => new Date(`${exam.date}T${exam.end}:00+08:00`) >= Date.now());
    const unpublished = data.exams.filter(exam => !exam.published);
    el('study-exam-count').textContent = `${upcoming.length} 场待考`;
    el('study-next-exam').replaceChildren();
    if (upcoming.length) el('study-next-exam').append(examCard(upcoming[0], true));
    else empty(el('study-next-exam'), '当前没有已公布的待考安排', `另有 ${unpublished.length} 门待公布。`);
    const remainingExams = el('study-other-exams');
    const others = published.filter(exam => exam !== upcoming[0]);
    el('study-other-exams-label').textContent = `其他已公布考试 · ${others.length} 场`;
    remainingExams.replaceChildren(...others.map(exam => examCard(exam)));
    if (!others.length) remainingExams.append(node('p', '暂无其他已公布考试。', 'study-small-note'));
    el('study-unpublished-label').textContent = `待公布安排 · ${unpublished.length} 门`;
    el('study-unpublished-exams').replaceChildren(...unpublished.map(exam => node('p', `${exam.name} · ${exam.group || '排考组待提供'}`, 'study-unpublished-row')));
    if (!unpublished.length) el('study-unpublished-exams').append(node('p', '当前排考均已公布。', 'study-small-note'));
    el('study-school-courses-label').textContent = `本学期教务课程 · ${data.courses.length} 门`;
    reconcile(el('study-school-courses'), data.courses, course => `school-${course.number}`, course => {
      const item = node('article', '', 'study-school-course');
      item.append(node('strong', `${course.name} · ${course.number}`), node('p', `${course.teacher} · ${course.credits} 学分`, 'study-small-note'));
      for (const meeting of course.meetings) item.append(node('p', meeting.raw, 'study-small-note'));
      return item;
    });
    const nearest = upcoming[0];
    const urgent = nearest && new Date(`${nearest.date}T${nearest.start}:00+08:00`) - Date.now() <= 14 * 86400000;
    el('study-exam-panel').classList.toggle('has-near-exam', Boolean(urgent));
    el('study-exam-alert').hidden = !urgent;
    if (urgent) el('study-exam-alert').textContent = `近期考试 · ${nearest.date.slice(5).replace('-', '/')} ${nearest.name} ↗`;
  }
  async function request(path, method = 'GET') {
    const auth = await fetch(bridge + '/api/desktop', {cache: 'no-store', signal: AbortSignal.timeout(5000)});
    if (!auth.ok) throw new Error('本机工作台服务尚未连接，请重新启动工作台。');
    const {token} = await auth.json();
    const response = await fetch(bridge + path, {method, cache: 'no-store', headers: {'X-Desktop-Token': token}, signal: AbortSignal.timeout(10000)});
    const result = await response.json();
    if (response.status === 429) return result;
    if (!response.ok) throw new Error(result.error?.message || '本机数据读取失败，请稍后重试。');
    return result;
  }
  async function load(name) {
    const source = sources[name];
    if (source.busy || !root?.isConnected) return;
    source.busy = true;
    try {
      const result = await request(source.path);
      if (name === 'canvas') canvas = result;
      else school = result;
      source.error = '';

      preserveReading(() => {
        if (name === 'school') renderSchool();
        renderCanvas();
      });
      clearTimeout(source.poll);
      if (result.meta.refreshing) source.poll = setTimeout(() => load(name), 3000);
    } catch (error) {
      source.error = error.message;
      if (name === 'canvas' && !canvas) renderCanvas();
      else if(name==='canvas')renderMaterials();
      if (name === 'school' && !school) renderSchool();
    } finally {
      source.busy = false;
      sourceStatus(name, name === 'canvas' ? canvas?.meta : school?.meta);
      if (!restored && canvas && school) {
        restored = true;
        requestAnimationFrame(() => { if (Number.isFinite(stored.scrollY) && stored.scrollY > 0) window.scrollTo(0, stored.scrollY); });
      }
    }
  }
  async function loadAcademic() {
    if (!root?.isConnected) return;
    try {
      academic = await request('/api/academic/v1/status');
      const m=academic.meta;
      el('study-academic-status').textContent=`学业数据${m.stale?'上次保存':'保存'} ${dateText(m.updatedAt)}`;
      publishSourceState('academic',m);
      if(m.refreshing)setTimeout(loadAcademic,1500);
    } catch { el('study-academic-status').textContent='学业状态读取失败，保留已有数据，请检查后台'; }
  }

  function start() {
    const dashboard = el('study-workspace');
    if (!dashboard || dashboard.dataset.ready) return;
    root = dashboard;
    root.dataset.ready = '1';
    window.StudyIma?.mount(() => preserveReading(renderMaterials));
    if (stored.sort !== view.sort) {
      try { sessionStorage.setItem(storageKey, JSON.stringify({...stored, sort: view.sort})); } catch {}
    }
    const marksStatus = node('p', '', 'study-source-note');
    marksStatus.id = 'study-local-marks-status'; marksStatus.hidden = true;
    marksStatus.setAttribute('role', 'status');
    root.querySelector('.study-topbar')?.after(marksStatus);
    el('study-file-search').value = view.query;
    el('study-file-sort').value = view.sort;
    el('study-file-type').value = view.fileType;
    for (const details of root.querySelectorAll('details[id]')) details.open = view.open.has(details.id);
    root.addEventListener('toggle', event => {
      if (!(event.target instanceof HTMLDetailsElement) || !event.target.id) return;
      if (event.target.open) view.open.add(event.target.id);
      else view.open.delete(event.target.id);
      save();
    }, true);
    el('study-course-filter').addEventListener('change', event => selectCourse(event.target.value));
    el('study-scope-reset').addEventListener('click', () => selectCourse(''));
    for (const tab of root.querySelectorAll('[data-assignment-tab]')) tab.addEventListener('click', () => {
      view.tab = tab.dataset.assignmentTab;
      view.assignmentLimit = 6;
      renderAssignments();
      save();
    });
    for (const button of root.querySelectorAll('[data-offline-status]')) button.addEventListener('click', () => {
      view.offlineStatus = button.dataset.offlineStatus;
      view.assignmentLimit = 6;
      renderAssignments();
      save();
    });
    el('study-file-search').addEventListener('input', event => {
      view.query = event.target.value;
      view.fileLimit = 6;
      renderMaterials();
      save();
    });
    el('study-file-course').addEventListener('change', event => selectCourse(event.target.value));
    el('study-file-type').addEventListener('change', event => {
      view.fileType=StudyFileTypes.restore(event.target.value);
      preserveReading(renderMaterials);save();
    });
    el('study-file-sort').addEventListener('change', event => {
      view.sort = StudyMaterialDates.restoreSort(event.target.value);
      renderMaterials();
      save();
    });
    el('study-search-clear').addEventListener('click', () => {
      view.query = ''; view.fileLimit = 6;
      el('study-file-search').value = '';
      renderMaterials(); save();
    });
    el('study-files-more').addEventListener('click', () => { view.fileLimit += 10; renderMaterials(); save(); });
    el('study-assignment-more').addEventListener('click', () => { view.assignmentLimit += 6; renderAssignments(); save(); });
    renderCanvas(); renderSchool();
    load('canvas'); load('school'); loadAcademic(); loadSharedMarks();
    clearInterval(interval);
    interval = setInterval(() => {
      if (document.visibilityState === 'visible' && root.isConnected) { refreshMaterialDates(); load('canvas'); load('school'); loadAcademic(); loadSharedMarks(); }
    }, 60000);
  }
  window.addEventListener('pagehide', save);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') refreshMaterialDates();
  });
  window.addEventListener('school-connection-data-updated', event => {
    for (const target of event.detail?.targets || []) {
      if (target === 'sufe') load('school');
      if (target === 'canvas') load('canvas');
      if (target === 'academic') loadAcademic();
    }
  });
  window.addEventListener('storage', event => {
    if (root?.isConnected && [offlineDatePrefix, offlineSubmissionPrefix].some(prefix => event.key?.startsWith(prefix))) preserveReading(renderCanvas);
  });
  new MutationObserver(start).observe(document.documentElement, {childList: true, subtree: true});
  start();
})();
