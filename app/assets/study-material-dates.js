/* Canvas file-record dates and material ordering, independent of sync time and homework. */
((scope) => {
  'use strict';
  const dayMilliseconds = 86400000;
  const formatter = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  });

  function parse(value) {
    if (typeof value !== 'string') return null;
    const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(Z|[+-]\d{2}:\d{2})$/.exec(value);
    if (!match) return null;
    const [, year, month, day, hour, minute, second, zone] = match;
    const leap = Number(year) % 4 === 0 && (Number(year) % 100 !== 0 || Number(year) % 400 === 0);
    const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    if (Number(year) < 1 || Number(month) < 1 || Number(month) > 12 || Number(day) < 1
        || Number(day) > days[Number(month) - 1] || Number(hour) > 23 || Number(minute) > 59
        || Number(second) > 59 || zone !== 'Z' && (Number(zone.slice(1, 3)) > 23 || Number(zone.slice(4)) > 59)) return null;
    const timestamp = Date.parse(value);
    return Number.isFinite(timestamp) ? timestamp : null;
  }

  function shanghaiDay(timestamp) {
    const parts = Object.fromEntries(formatter.formatToParts(timestamp).map(part => [part.type, part.value]));
    const iso = `${parts.year}-${parts.month}-${parts.day}`;
    return {ordinal: Date.parse(iso + 'T00:00:00Z') / dayMilliseconds, date: iso.replaceAll('-', '/')};
  }

  function restoreSort(value) {
    return value === 'name' ? 'name' : 'upload';
  }

  function compareFiles(a, b, sort = 'upload', now = Date.now()) {
    if (restoreSort(sort) === 'upload') {
      const aTime = parse(a.createdAt);
      const bTime = parse(b.createdAt);
      const aValid = aTime !== null && aTime <= now;
      const bValid = bTime !== null && bTime <= now;
      if (aValid !== bValid) return aValid ? -1 : 1;
      if (aValid && aTime !== bTime) return bTime - aTime;
    }
    const byName = a.name.localeCompare(b.name, 'zh-CN', {numeric: true});
    if (byName) return byName;
    const aId = JSON.stringify([String(a.courseId ?? ''), String(a.id ?? '')]);
    const bId = JSON.stringify([String(b.courseId ?? ''), String(b.id ?? '')]);
    return aId < bId ? -1 : aId > bId ? 1 : 0;
  }

  function describe(file, updatedAt, now = Date.now()) {
    const created = parse(file.createdAt);
    const valid = created !== null && created <= now;
    const currentDay = shanghaiDay(now);
    let upload = {status: 'missing', label: '记录日期未提供', date: null, datetime: null, recent: false};
    if (file.createdAt !== null && file.createdAt !== undefined && file.createdAt !== '' && !valid) {
      upload = {status: created === null ? 'invalid' : 'future', label: '记录日期异常', date: null, datetime: null, recent: false};
    }
    if (valid) {
      const day = shanghaiDay(created);
      const difference = currentDay.ordinal - day.ordinal;
      upload = {status: 'valid', date: day.date, datetime: new Date(created).toISOString(), recent: difference <= 1,
        label: difference === 0 ? '今天创建' : difference === 1 ? '昨天创建' : `${difference} 天前创建`};
    }
    const updated = parse(updatedAt);
    const updatedDay = updated !== null ? shanghaiDay(updated) : null;
    const showUpdate = updated !== null && updated <= now
      && (!valid || updated > created && updatedDay.ordinal !== shanghaiDay(created).ordinal);
    return {upload, update: showUpdate ? {date: updatedDay.date, datetime: new Date(updated).toISOString()} : null};
  }

  const api = {parse, shanghaiDay, describe, restoreSort, compareFiles};
  if (typeof module === 'object' && module.exports) module.exports = api;
  else scope.StudyMaterialDates = api;
})(globalThis);
