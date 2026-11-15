((scope) => {
  'use strict';
  const storageKey = 'workbench-calendar-official-filters-v2';
  const legacyKey = 'workbench-calendar-hidden-official-types-v1';
  const typeLabels = {campus:'校园活动', teaching:'教学', registration:'报到 / 手续', exam:'考试',
    holiday:'假期 / 节日', competition:'比赛', anniversary:'纪念日', personal:'个人安排', other:'其他'};
  // Display taxonomy only. Never change the official event or its source type.
  // First matching rule wins; a combined term-start/practice node stays in term-start.
  const rules = {
    campus:[['ceremony','重要典礼',/开学典礼|毕业典礼/], ['innovation','创新创业',/双创月/],
      ['culture','文化与生活',/校园美食节|校园(?:国际)?文化节|毕业晚会/],
      ['commemoration','主题纪念活动',/教师节|国际劳动妇女节|世界读书日|青年节/]],
    teaching:[['term','学期开始与上课',/学期开始|上课|教学活动/], ['orientation','军训与入学教育',/军训|入学教育/],
      ['practice','社会实践',/社会实践/], ['graduation-work','毕业实习与论文',/毕业实习|毕业论文|撰写.*论文/],
      ['appraisal','毕业鉴定',/毕业鉴定/]],
    registration:[['returning','在读生报到',/在读生.*报到/], ['international','留学生报到',/留学生.*报到/],
      ['freshman','本科新生报到',/^(?:本科)?新生报到$/], ['departure','毕业离校手续',/离校手续/]],
    exam:[['freshman','本科新生期末考试',/新生.*期末考试/], ['final','在读生期末考试',/期末考试/],
      ['thesis','论文考核与答辩',/论文.*(?:考核|答辩)/]],
    holiday:[['vacation','寒暑假开始',/寒假开始|暑假开始/], ['public','法定节日',/^(?:元旦|春节|清明节|劳动节|端午节|中秋节|国庆节)$/]],
    competition:[['sports','校运会',/校运会/], ['sports-culture','体育文化活动',/体育文化/]],
    anniversary:[['alumni','校庆与校友返校',/校庆|校友返校/]],
  };
  function classify(event) {
    const match = (rules[event.type] || []).find(rule => rule[2].test(event.title || ''));
    return {id:match?.[0] || 'other', label:match?.[1] || '其他'};
  }
  function groupKey(type, subcategory) { return type + ':' + subcategory; }
  function cleanMap(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
    return Object.fromEntries(Object.entries(value).filter(([key, val]) => key.length <= 300 && typeof val === 'boolean').slice(0,20000));
  }
  function normalize(value) {
    if (!value || value.version !== 2 || typeof value.defaultVisible !== 'boolean') return null;
    return {version:2, defaultVisible:value.defaultVisible, types:cleanMap(value.types),
      groups:cleanMap(value.groups), events:cleanMap(value.events), initialPreferencesApplied:true};
  }
  function migrate(saved, legacyHidden, events) {
    const existing = normalize(saved);
    if (existing) return {state:existing, migrated:false};
    const state = {version:2, defaultVisible:true, types:{}, groups:{}, events:{}, initialPreferencesApplied:true};
    for (const type of Array.isArray(legacyHidden) ? legacyHidden : []) {
      if (typeof type === 'string' && type.length <= 100) Object.defineProperty(state.types,type,{value:false,enumerable:true,writable:true,configurable:true});
    }
    for (const event of events) {
      if (event.source !== 'official') continue;
      if (event.title === '开学典礼') state.events[event.id] = true;
      if (event.title === '上财双创月') state.events[event.id] = false;
    }
    return {state, migrated:true};
  }
  function load(storage, events) {
    let saved, legacy;
    try { saved = JSON.parse(storage.getItem(storageKey)); } catch {}
    try { legacy = JSON.parse(storage.getItem(legacyKey)); } catch {}
    const result = migrate(saved,legacy,events);
    // Called only after a successful event snapshot. Never migrate an empty startup placeholder.
    if (result.migrated) storage.setItem(storageKey,JSON.stringify(result.state));
    return result.state;
  }
  function visible(state, event) {
    if (event.source !== 'official') return true;
    if (!state) return true;
    if (Object.hasOwn(state.events,event.id)) return state.events[event.id];
    const key = groupKey(event.type,classify(event).id);
    if (Object.hasOwn(state.groups,key)) return state.groups[key];
    if (Object.hasOwn(state.types,event.type)) return state.types[event.type];
    return state.defaultVisible;
  }
  function filter(state, events) { return events.filter(event => visible(state,event)); }
  function selection(state, events) {
    const total = events.length, selected = events.filter(event => visible(state,event)).length;
    return {total,selected,checked:total > 0 && selected === total,indeterminate:selected > 0 && selected < total};
  }
  function setAll(state, value) {
    return {version:2, defaultVisible:!!value, types:{}, groups:{}, events:{}, initialPreferencesApplied:true};
  }
  function setType(state, type, value, events) {
    const next = normalize(state);
    next.types[type] = !!value;
    for (const key of Object.keys(next.groups)) if (key.startsWith(type + ':')) delete next.groups[key];
    for (const event of events) if (event.source === 'official' && event.type === type) delete next.events[event.id];
    return next;
  }
  function setGroup(state, type, subcategory, value, events) {
    const next = normalize(state);
    next.groups[groupKey(type,subcategory)] = !!value;
    for (const event of events) if (event.source === 'official' && event.type === type && classify(event).id === subcategory) delete next.events[event.id];
    return next;
  }
  function setEvent(state, id, value) {
    const next = normalize(state); next.events[id] = !!value; return next;
  }
  function tree(events) {
    const official = events.filter(event => event.source === 'official');
    const orderedTypes = [...Object.keys(typeLabels),...new Set(official.map(event => event.type))];
    return [...new Set(orderedTypes)].map(type => {
      const items = official.filter(event => event.type === type);
      const groups = [...(rules[type] || []).map(rule => ({id:rule[0],label:rule[1]})),{id:'other',label:'其他'}]
        .map(group => ({...group, events:items.filter(event => classify(event).id === group.id)
          .sort((a,b) => a.startDate.localeCompare(b.startDate) || a.id.localeCompare(b.id))})).filter(group => group.events.length);
      return {id:type,label:typeLabels[type] || type,events:items,groups};
    }).filter(type => type.events.length);
  }
  function shortDate(event) {
    const start = event.startDate.replaceAll('-','/');
    const end = event.endDate.slice(0,4) === event.startDate.slice(0,4) ? event.endDate.slice(5).replace('-','/') : event.endDate.replaceAll('-','/');
    const semester = {autumn:'秋季',spring:'春季',summer:'第三学期'}[event.semester];
    return (event.startDate === event.endDate ? start : start + '–' + end) + (semester ? ' · ' + semester : '');
  }
  const api = {storageKey,legacyKey,typeLabels,classify,groupKey,migrate,load,normalize,visible,filter,selection,setAll,setType,setGroup,setEvent,tree,shortDate};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else scope.WorkbenchCalendarFilters = api;
})(typeof window !== 'undefined' ? window : globalThis);
