(() => {
  'use strict';
  const dates = window.WorkbenchDates;
  const filters = window.WorkbenchCalendarFilters;
  const api = 'http://127.0.0.1:8182';
  const types = {teaching: '教学', registration: '报到 / 手续', exam: '考试', holiday: '假期 / 节日',
    campus: '校园活动', anniversary: '纪念日', competition: '比赛', personal: '个人安排', other: '其他'};
  let root, snapshot, busy = false, saving = false, timer, verificationTimer;
  let selected = dates.today(), month = selected.slice(0, 7), focused = selected;
  let editId = null, editRevision = 0, createId = null, deletingId = null;
  let memoryRevision = '', tooltipTimer, tooltipSuppressedUntil = 0;
  let officialFilter = null, filterTreeSignature = '';
  const expandedFilterGroups = new Set();
  const el = id => document.getElementById('home-cal-' + id);
  function node(tag, text = '', cls = '') {
    const element = document.createElement(tag);
    element.textContent = text;
    if (cls) element.className = cls;
    return element;
  }
  function button(text, action, cls = '') {
    const element = node('button', text, cls);
    element.type = 'button';
    element.addEventListener('click', action);
    return element;
  }
  function sourceLabel(event) { return event.source === 'official' ? '上财校历' : '个人添加'; }
  function eventColors(event, tone = event.tone) { return `home-cal-tone ${event.source} tone-${tone}`; }
  function dateLabel(date, year = true) {
    return new Date(date + 'T00:00:00+08:00').toLocaleDateString('zh-CN', {timeZone: 'Asia/Shanghai',
      year: year ? 'numeric' : undefined, month: 'long', day: 'numeric', weekday: 'long'});
  }
  function link(text, url) {
    const parsed = new URL(url);
    if (parsed.protocol !== 'https:' || !['www.sufe.edu.cn', 'ieco.sufe.edu.cn'].includes(parsed.hostname)) throw new Error('校历来源地址无效');
    const anchor = node('a', text);
    anchor.href = parsed.href; anchor.target = '_blank'; anchor.rel = 'noopener noreferrer';
    return anchor;
  }
  function visibleEvents() {
    return filters.filter(officialFilter,snapshot?.events || []);
  }
  function eventsOn(date) { return dates.onDate(visibleEvents(), date); }
  function applyCalendarFilter(next) {
    officialFilter = next;
    try {
      localStorage.setItem(filters.storageKey, JSON.stringify(officialFilter));
      el('filter-storage-note').textContent = '';
    } catch { el('filter-storage-note').textContent = '浏览器未允许保存设置，本次选择仅在当前页面生效。'; }
    renderCalendarFilter(); renderGrid(); renderDetails();
  }
  function renderCalendarFilter() {
    const options = el('filter-options');
    if (!options) return;
    const official = (snapshot?.events || []).filter(event => event.source === 'official');
    const signature = JSON.stringify(official.map(({id,type,title,startDate,endDate,semester}) => ({id,type,title,startDate,endDate,semester})));
    if (signature !== filterTreeSignature) {
      const activeKey = options.contains(document.activeElement) ? document.activeElement.dataset.filterKey : null;
      options.replaceChildren();
      function group(labelText, key, events, change, level) {
        const holder = node('div','','home-cal-filter-group level-' + level);
        holder.dataset.filterGroup = key;
        const row = node('div','','home-cal-filter-row');
        const label = node('label','','home-cal-filter-option');
        const input = document.createElement('input');
        input.type = 'checkbox'; input.dataset.filterKey = key;
        input.setAttribute('aria-label', labelText + '：全选或取消');
        input.calendarFilterEvents = events;
        input.addEventListener('change', () => change(input.checked));
        const count = node('small','','home-cal-filter-number');
        const children = node('div','','home-cal-filter-children');
        children.id = 'home-cal-filter-children-' + options.querySelectorAll('.home-cal-filter-group').length + '-' + level + '-' + key.replace(/[^a-z0-9-]/gi,'-');
        const toggle = button('▸', () => {
          if (expandedFilterGroups.has(key)) expandedFilterGroups.delete(key); else expandedFilterGroups.add(key);
          children.hidden = !expandedFilterGroups.has(key);
          toggle.setAttribute('aria-expanded',String(!children.hidden));
          toggle.setAttribute('aria-label',(children.hidden ? '展开' : '收起') + labelText);
        },'home-cal-filter-toggle');
        toggle.dataset.filterKey = 'toggle:' + key;
        toggle.setAttribute('aria-controls',children.id);
        children.hidden = !expandedFilterGroups.has(key);
        toggle.setAttribute('aria-expanded',String(!children.hidden));
        toggle.setAttribute('aria-label',(children.hidden ? '展开' : '收起') + labelText);
        label.append(input,node('span',labelText));
        row.append(label,count,toggle); holder.append(row,children);
        holder.calendarFilterCount = count;
        return {holder,children};
      }
      for (const type of filters.tree(official)) {
        const main = group(type.label,type.id,type.events,
          value => applyCalendarFilter(filters.setType(officialFilter,type.id,value,snapshot.events)),0);
        options.append(main.holder);
        for (const sub of type.groups) {
          const subkey = filters.groupKey(type.id,sub.id);
          const child = group(sub.label,subkey,sub.events,
            value => applyCalendarFilter(filters.setGroup(officialFilter,type.id,sub.id,value,snapshot.events)),1);
          main.children.append(child.holder);
          for (const event of sub.events) {
            const label = node('label','','home-cal-filter-event');
            const input = document.createElement('input'); input.type='checkbox';
            input.dataset.eventId=event.id; input.dataset.filterKey='event:'+event.id;
            input.setAttribute('aria-label',`显示事件：${event.title}（${filters.shortDate(event)}）`);
            input.addEventListener('change',() => applyCalendarFilter(filters.setEvent(officialFilter,event.id,input.checked)));
            const caption = node('span');
            caption.append(node('span',event.title),node('small',filters.shortDate(event)));
            label.append(input,caption); child.children.append(label);
          }
        }
      }
      filterTreeSignature = signature;
      if (activeKey) [...options.querySelectorAll('[data-filter-key]')].find(input => input.dataset.filterKey === activeKey)?.focus({preventScroll:true});
    }
    const overall = filters.selection(officialFilter,official);
    el('filter-count').textContent = overall.total ? `${overall.selected}/${overall.total} 条` : '暂无校历';
    for (const input of options.querySelectorAll('input[data-filter-key]')) {
      if (input.calendarFilterEvents) {
        const state = filters.selection(officialFilter,input.calendarFilterEvents);
        input.checked=state.checked; input.indeterminate=state.indeterminate;
        const holder = input.closest('.home-cal-filter-group');
        holder.calendarFilterCount.textContent = `${state.selected}/${state.total}`;
        holder.calendarFilterCount.title = `已选 ${state.selected} 条，共 ${state.total} 条`;
      } else if (input.dataset.eventId) {
        const event = official.find(item => item.id === input.dataset.eventId);
        input.checked = event ? filters.visible(officialFilter,event) : false;
      }
    }
    el('filter-all').disabled = !overall.total || overall.checked;
    el('filter-none').disabled = !overall.total || overall.selected === 0;
  }
  function setOfficialTypesVisible(visible) {
    applyCalendarFilter(filters.setAll(officialFilter,visible));
  }
  function hideTooltip() {
    clearTimeout(tooltipTimer);
    el('tooltip').hidden = true;
    for (const cell of root.querySelectorAll('[aria-describedby="home-cal-tooltip"]')) cell.removeAttribute('aria-describedby');
  }
  function showTooltip(target) {
    hideTooltip();
    const events = eventsOn(target.dataset.date);
    if (!events.length) return;
    const tooltip = el('tooltip');
    tooltip.replaceChildren(node('strong', dateLabel(target.dataset.date, false)));
    for (const event of events.slice(0, 3)) {
      const item = node('div', '', 'home-cal-preview ' + eventColors(event));
      item.append(node('p', event.title), node('small', `${sourceLabel(event)} · ${event.startDate === event.endDate ? event.startDate : event.startDate + ' — ' + event.endDate}`));
      tooltip.append(item);
    }
    if (events.length > 3) tooltip.append(node('small', `另有 ${events.length - 3} 条，点击日期查看全部。`));
    tooltip.hidden = false;
    target.setAttribute('aria-describedby', tooltip.id);
    const rect = target.getBoundingClientRect();
    const calendarRect = root.getBoundingClientRect();
    const gridRect = el('grid').getBoundingClientRect();
    const width = tooltip.getBoundingClientRect().width;
    const height = tooltip.getBoundingClientRect().height;
    const rightSpace = innerWidth - calendarRect.right;
    const left = rightSpace > width + 12 ? calendarRect.right + 12 : Math.max(8, Math.min(rect.left, innerWidth - width - 8));
    const top = rightSpace > width + 12 ? Math.max(8, Math.min(rect.top, innerHeight - height - 8))
      : gridRect.bottom + height + 12 < innerHeight ? gridRect.bottom + 8 : Math.max(8, gridRect.top - height - 8);
    tooltip.style.left = left + 'px'; tooltip.style.top = top + 'px';
  }
  function selectDate(date) {
    tooltipSuppressedUntil = Date.now() + 800;
    selected = date; focused = date; month = date.slice(0, 7); deletingId = null;
    hideTooltip(); renderGrid(); renderDetails();
    el('events').scrollTop = 0;
    hideTooltip();
  }
  function renderGrid() {
    hideTooltip();
    el('month').value = month;
    const grid = el('grid');
    const activeDate = grid.contains(document.activeElement) ? document.activeElement.dataset.date : null;
    const today = snapshot?.meta.today || dates.today();
    grid.setAttribute('aria-label', `${month.replace('-', '年')}月日期`);
    const previous = new Map([...grid.querySelectorAll('.home-cal-day')].map(item => [item.dataset.date, item]));
    const weeks = dates.rangeWeeks(visibleEvents(), month).map(week => {
      const row = node('div', '', 'home-cal-week');
      // Keep logical lanes/colors stable, but reserve only occupied display rows
      // in this week. Empty weeks and gaps in global lane indices need no space.
      const displayLanes = new Map([...new Set(week.segments.map(segment => segment.lane))]
        .sort((a,b) => a-b).map((lane,index) => [lane,index]));
      row.style.gridTemplateRows = `var(--cal-day-height) ${'var(--cal-range-height) '.repeat(displayLanes.size)}4px`;
      const cells = week.days.map((date, column) => {
        let cell = previous.get(date);
        if (!cell) {
          cell = button('', () => selectDate(date), 'home-cal-day');
          cell.dataset.date = date;
          cell.addEventListener('pointerenter', event => {
            if (event.pointerType !== 'touch' && Date.now() >= tooltipSuppressedUntil) tooltipTimer = setTimeout(() => showTooltip(cell), 240);
          });
          cell.addEventListener('pointerleave', hideTooltip);
          cell.addEventListener('focus', () => { focused = date; if (cell.matches(':focus-visible')) showTooltip(cell); });
          cell.addEventListener('blur', hideTooltip);
        }
        const events = eventsOn(date);
        cell.className = 'home-cal-day' + (date.slice(0, 7) !== month ? ' is-outside' : '') + (date === today ? ' is-today' : '') + (date === selected ? ' is-selected' : '');
        cell.setAttribute('aria-pressed', String(date === selected));
        if (date === today) cell.setAttribute('aria-current', 'date');
        else cell.removeAttribute('aria-current');
        cell.setAttribute('aria-label', dateLabel(date) + (date === today ? '，今天' : '') + (events.length ? `，${events.length} 条事件，${[...new Set(events.map(sourceLabel))].join('、')}` : '，无事件'));
        cell.tabIndex = date === focused ? 0 : -1;
        cell.style.gridColumn = column + 1;
        const marks = node('span', '', 'home-cal-marks');
        for (const event of events.filter(item => item.startDate === item.endDate).slice(0, 3)) {
          const mark = node('i', '', eventColors(event));
          mark.dataset.eventId = event.id;
          mark.setAttribute('aria-hidden', 'true'); marks.append(mark);
        }
        cell.replaceChildren(node('span', String(Number(date.slice(-2))), 'home-cal-number'), marks);
        return cell;
      });
      row.append(...cells);
      for (const segment of week.segments) {
        const bar = node('div', '', `home-cal-range ${eventColors(segment.event, segment.tone)}`
          + (segment.event.startDate <= selected && selected <= segment.event.endDate ? ' is-active' : '')
          + (segment.continuesBefore ? ' continues-before' : '') + (segment.continuesAfter ? ' continues-after' : ''));
        bar.dataset.eventId = segment.event.id;
        bar.setAttribute('aria-hidden', 'true');
        bar.style.gridColumn = `${segment.start + 1} / ${segment.end + 2}`;
        bar.style.gridRow = displayLanes.get(segment.lane) + 2;
        row.append(bar);
      }
      return row;
    });
    grid.replaceChildren(...weeks);
    if (!grid.querySelector('[tabindex="0"]')) {
      focused = month + '-01';
      grid.querySelector(`[data-date="${focused}"]`).tabIndex = 0;
    }
    if (activeDate) grid.querySelector(`[data-date="${activeDate}"]`)?.focus({preventScroll: true});
  }
  function eventCard(event) {
    const card = node('article', '', 'home-cal-event ' + eventColors(event));
    card.dataset.eventId = event.id;
    const meta = node('p', `${sourceLabel(event)} · ${types[event.type]}`, 'home-cal-event-source');
    const title = node('h4');
    if (event.source === 'official') {
      const sourceLink = link(event.title, event.sourceUrl);
      sourceLink.title = '查看官方校历（新标签页）';
      title.append(sourceLink);
    } else title.textContent = event.title;
    card.append(title, meta);
    card.append(node('p', event.startDate === event.endDate ? event.startDate : `${event.startDate} — ${event.endDate}`, 'home-cal-event-range'));
    if (event.annual) card.append(node('p', `每年重复 · 始于 ${event.originalStartDate || event.startDate}`, 'home-cal-event-note'));
    if (event.needsReview) card.append(node('p', '待核对 · 请查看官方原文', 'home-cal-event-note'));
    if (event.source === 'personal') {
      if (event.description) {
        const more = node('details', '', 'home-cal-event-more');
        more.dataset.eventKey = event.source + ':' + event.id;
        more.append(node('summary', '查看说明'), node('p', event.description, 'home-cal-event-description'));
        card.append(more);
      }
      const actions = node('div', '', 'home-cal-event-actions');
      actions.append(button('编辑', () => openEditor(event.id)), button('删除', () => { deletingId = event.id; renderDetails(); }));
      card.append(actions);
      if (deletingId === event.id) {
        const confirm = node('div', '', 'home-cal-delete-confirm');
        confirm.append(node('p', event.annual ? '删除这条每年重复的个人事件？' : '删除这条个人事件？'),
          button('确认删除', () => removeEvent(event)), button('取消', () => { deletingId = null; renderDetails(); }));
        card.append(confirm);
      }
    }
    return card;
  }
  function renderDetails() {
    const expanded = new Set([...el('events').querySelectorAll('details[open][data-event-key]')].map(item => item.dataset.eventKey));
    const scrollTop = el('events').scrollTop;
    const events = eventsOn(selected);
    el('selected-date').textContent = dateLabel(selected, false);
    el('selected-count').textContent = events.length ? `${events.length} 件事` : '';
    el('events').replaceChildren(...events.map(eventCard));
    for (const item of el('events').querySelectorAll('details[data-event-key]')) item.open = expanded.has(item.dataset.eventKey);
    el('events').scrollTop = scrollTop;
    if (!events.length) {
      const message = snapshot ? '当天没有事件' : '正在读取日历…';
      el('events').append(node('p', message, 'home-cal-empty'));
    }
    if (snapshot?.meta.personalAvailable === false) el('events').append(node('p', '个人事件暂时无法读取，请检查下方提示。', 'home-cal-error'));
  }
  function renderSource() {
    const source = snapshot.sources[0];
    const meta = snapshot.meta;
    el('academic-year').textContent = source ? `${source.academicYear} 学年` : '校历暂不可用';
    const verification = meta.verification;
    const stamp = verification.checkedAt || source?.verifiedAt;
    el('verified').textContent = stamp ? '核验 ' + new Date(stamp).toLocaleString('zh-CN', {timeZone: 'Asia/Shanghai',month: '2-digit',day: '2-digit',hour: '2-digit',minute: '2-digit',hour12: false}) : '尚未核验';
    el('source-message').textContent = meta.officialError || (verification.state === 'changed' || verification.state === 'failed' ? verification.message : '');
    el('verify').disabled = meta.verifying;
    el('verify').textContent = meta.verifying ? '核验中…' : '核验原图';
    const contents = el('source-details');
    contents.replaceChildren();
    for (const item of snapshot.sources) {
      contents.append(link(item.title + ' ↗', item.url), node('p', item.publicationNote));
      contents.append(node('p', '事件人工核对更新于 ' + item.verifiedAt.slice(0, 10)));
    }
    for (const note of snapshot.notes) contents.append(node('p', note));
    contents.append(node('p', '2 月 29 日的年度纪念日仅在闰年显示。'));
    el('add').disabled = !meta.personalAvailable;
    el('status').textContent = meta.personalError || '';
  }
  async function request(path = '', body = undefined) {
    const authorization = await fetch(api + '/api/desktop', {cache: 'no-store', signal: AbortSignal.timeout(5000)});
    if (!authorization.ok) throw new Error('本机服务尚未连接，请重新启动工作台。');
    const {token} = await authorization.json();
    const headers = {'X-Desktop-Token': token};
    if (body !== undefined && body !== null) headers['Content-Type'] = 'application/json';
    const response = await fetch(api + '/api/calendar/v1' + path, {method: body === undefined ? 'GET' : 'POST', headers,
      body: body == null ? undefined : JSON.stringify(body), cache: 'no-store', signal: AbortSignal.timeout(15000)});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error?.message || '日历请求失败，请重试。');
    return payload.data;
  }
  async function load() {
    if (busy || !root?.isConnected) return;
    busy = true;
    try {
      const result = await request();
      const signature = JSON.stringify(result.events);
      snapshot = result;
      if (!officialFilter && result.events.some(event => event.source === 'official')) {
        try { officialFilter = filters.load(localStorage,result.events); }
        catch {
          // Preserve usable settings in memory even if the browser refuses persistence.
          let saved,legacy;
          try { saved=JSON.parse(localStorage.getItem(filters.storageKey)); legacy=JSON.parse(localStorage.getItem(filters.legacyKey)); } catch {}
          officialFilter=filters.migrate(saved,legacy,result.events).state;
          el('filter-storage-note').textContent='浏览器未允许保存设置，本次选择仅在当前页面生效。';
        }
      }
      renderSource();
      renderCalendarFilter();
      if (signature !== memoryRevision) { renderGrid(); renderDetails(); memoryRevision = signature; }
      clearTimeout(verificationTimer);
      if (result.meta.verifying) verificationTimer = setTimeout(load, 1500);
    } catch (error) { el('status').textContent = error.message + (snapshot ? ' 当前保留上次数据。' : ''); }
    finally { busy = false; }
  }
  function openEditor(id = null) {
    const existing = snapshot?.events.find(event => event.source === 'personal' && event.id === id);
    editId = existing?.id || null;
    editRevision = existing?.revision || 0;
    createId = crypto.randomUUID();
    el('editor-title').textContent = existing ? '编辑个人事件' : '添加个人事件';
    el('title').value = existing?.title || '';
    el('start').value = existing?.startDate || selected;
    el('end').value = existing?.endDate || selected;
    el('description').value = existing?.description || '';
    el('type').value = existing?.type || 'personal';
    el('annual').checked = existing?.annual || false;
    el('form-status').textContent = '';
    el('dialog').showModal();
    el('title').focus();
  }
  async function saveEvent(event) {
    event.preventDefault();
    if (saving) return;
    const title = el('title').value.trim();
    const start = el('start').value, end = el('end').value;
    if (!title) { el('form-status').textContent = '请填写事件标题。'; el('title').focus(); return; }
    if (!dates.validDate(start) || !dates.validDate(end)) { el('form-status').textContent = '请填写有效的起止日期。'; return; }
    if (end < start) { el('form-status').textContent = '结束日期不能早于开始日期。'; return; }
    if (el('annual').checked && (start !== end || el('type').value !== 'anniversary')) {
      el('form-status').textContent = '每年重复适用于单日纪念日，请选择纪念日类型并使用相同的起止日期。'; return;
    }
    saving = true; el('save').disabled = true; el('form-status').textContent = '正在保存…';
    try {
      const saved = await request('/events', {id: editId || createId, revision: editRevision, title,
        startDate: start, endDate: end, type: el('type').value, annual: el('annual').checked, description: el('description').value});
      const previous = snapshot.events.findIndex(item => item.id === saved.id && item.source === 'personal');
      if (previous >= 0) snapshot.events[previous] = saved;
      else snapshot.events.push(saved);
      selected = saved.startDate; focused = selected; month = selected.slice(0, 7);
      memoryRevision = JSON.stringify(snapshot.events);
      renderGrid(); renderDetails();
      el('dialog').close();
      el('status').textContent = '个人事件已保存。';
      el('add').focus({preventScroll: true});
    } catch (error) { el('form-status').textContent = error.message; }
    finally { saving = false; el('save').disabled = false; }
  }
  async function removeEvent(event) {
    if (saving) return;
    saving = true;
    try {
      await request('/delete', {id: event.id, revision: event.revision});
      snapshot.events = snapshot.events.filter(item => item.id !== event.id || item.source !== 'personal');
      memoryRevision = JSON.stringify(snapshot.events); deletingId = null;
      renderGrid(); renderDetails(); el('status').textContent = '个人事件已删除。';
    } catch (error) { el('status').textContent = error.message; }
    finally { saving = false; }
  }
  function changeMonth(value) {
    if (!/^\d{4}-\d{2}$/.test(value) || !dates.validDate(value + '-01') || value < '1900-01' || value > '2199-12') return;
    month = value; selected = value + '-01'; focused = selected; deletingId = null;
    renderGrid(); renderDetails(); el('events').scrollTop = 0;
  }
  function keyNavigation(event) {
    const cell = event.target.closest('[data-date]');
    if (!cell) return;
    const offsets = {ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7};
    let target;
    if (event.key in offsets) target = dates.addDays(cell.dataset.date, offsets[event.key]);
    else if (event.key === 'PageUp' || event.key === 'PageDown') target = dates.shiftMonth(cell.dataset.date, event.key === 'PageUp' ? -1 : 1);
    else if (event.key === 'Home') target = month + '-01';
    else if (event.key === 'End') target = dates.addDays(dates.shiftMonth(month + '-01', 1), -1);
    else if (event.key === 'Escape') { hideTooltip(); return; }
    else return;
    event.preventDefault();
    if (target < '1900-01-01' || target > '2199-12-31') return;
    focused = target; month = target.slice(0, 7); renderGrid();
    el('grid').querySelector(`[data-date="${target}"]`).focus({preventScroll: true});
  }
  function start() {
    const calendar = document.getElementById('home-calendar');
    if (!calendar || calendar.dataset.ready) return;
    root = calendar; root.dataset.ready = '1';
    for (const [value, caption, label] of [['0', '日历', '查看日历'], ['2', '天气', '查看天气与常用入口']]) {
      const mobileTab = document.querySelector(`.mobile-navigation-input[value="${value}"]`);
      if (!mobileTab) continue;
      mobileTab.setAttribute('aria-label', label);
      const mobileLabel = mobileTab.closest('label');
      mobileLabel.classList.add('home-cal-mobile-tab');
      mobileLabel.dataset.caption = caption;
      mobileLabel.tabIndex = 0;
      mobileLabel.setAttribute('role', 'button');
      mobileLabel.setAttribute('aria-label', label);
      const showPanel = event => {
        event.preventDefault(); mobileTab.checked = true; hideTooltip();
      };
      mobileLabel.addEventListener('click', showPanel);
      mobileLabel.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') showPanel(event);
      });
    }
    el('prev').addEventListener('click', () => changeMonth(dates.shiftMonth(month + '-01', -1).slice(0, 7)));
    el('next').addEventListener('click', () => changeMonth(dates.shiftMonth(month + '-01', 1).slice(0, 7)));
    el('month').addEventListener('change', event => changeMonth(event.target.value));
    el('today').addEventListener('click', () => selectDate(dates.today()));
    el('add').addEventListener('click', () => openEditor());
    el('cancel').addEventListener('click', () => { if (!saving) el('dialog').close(); });
    el('dialog').addEventListener('cancel', event => { if (saving) event.preventDefault(); });
    el('form').addEventListener('submit', saveEvent);
    el('start').addEventListener('change', () => {
      if (el('annual').checked) el('end').value = el('start').value;
    });
    el('annual').addEventListener('change', () => {
      if (el('annual').checked) { el('type').value = 'anniversary'; el('end').value = el('start').value; }
    });
    el('verify').addEventListener('click', async () => {
      el('verify').disabled = true;
      try { await request('/verify', null); await load(); }
      catch (error) { el('status').textContent = error.message; el('verify').disabled = false; }
    });
    el('grid').addEventListener('keydown', keyNavigation);
    el('filter-all').addEventListener('click', () => setOfficialTypesVisible(true));
    el('filter-none').addEventListener('click', () => setOfficialTypesVisible(false));
    renderGrid(); renderDetails(); load();
    clearInterval(timer);
    timer = setInterval(() => {
      if (!root.isConnected || document.visibilityState !== 'visible' || saving) return;
      load();
    }, 60000);
  }
  window.addEventListener('resize', () => { if (root?.isConnected) hideTooltip(); });
  window.addEventListener('scroll', () => { if (root?.isConnected) hideTooltip(); }, true);
  new MutationObserver(start).observe(document.documentElement, {childList: true, subtree: true});
  start();
})();
