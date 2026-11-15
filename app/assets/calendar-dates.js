((scope) => {
  'use strict';
  function validDate(value) {
    return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value)
      && Number.isFinite(Date.parse(value + 'T00:00:00Z'))
      && new Date(value + 'T00:00:00Z').toISOString().slice(0, 10) === value;
  }
  function today(at = Date.now()) { return new Date(at + 8 * 3600000).toISOString().slice(0, 10); }
  function addDays(date, amount) { return new Date(Date.parse(date + 'T00:00:00Z') + amount * 86400000).toISOString().slice(0, 10); }
  function shiftMonth(date, amount) {
    const at = new Date(date + 'T00:00:00Z');
    const target = new Date(Date.UTC(at.getUTCFullYear(), at.getUTCMonth() + amount, 1));
    const last = new Date(Date.UTC(target.getUTCFullYear(), target.getUTCMonth() + 1, 0)).getUTCDate();
    target.setUTCDate(Math.min(at.getUTCDate(), last));
    return target.toISOString().slice(0, 10);
  }
  function occurrence(event, date) {
    if (!event.annual) return event.startDate <= date && date <= event.endDate ? {...event} : null;
    const year = date.slice(0, 4);
    const anniversary = year + event.startDate.slice(4);
    return year >= event.startDate.slice(0, 4) && validDate(anniversary) && anniversary === date
      ? {...event, startDate: anniversary, endDate: anniversary, originalStartDate: event.startDate} : null;
  }
  function onDate(events, date) {
    const ranges = new Map(rangeLayout(events).map(({event, lane, tone}) =>
      [event.source + ':' + event.id, {lane, tone}]));
    const matched = events.map(event => occurrence(event, date)).filter(Boolean).map(event =>
      ({...event, lane: -1, ...ranges.get(event.source + ':' + event.id)})).sort((a, b) =>
      a.lane - b.lane || a.source.localeCompare(b.source) || a.title.localeCompare(b.title, 'zh-CN') || a.id.localeCompare(b.id));
    const usedTones = new Map();
    for (const event of matched.filter(item => item.lane >= 0)) {
      if (!usedTones.has(event.source)) usedTones.set(event.source, new Set());
      usedTones.get(event.source).add(event.tone);
    }
    for (const event of matched.filter(item => item.lane === -1)) {
      let preferredTone = 0;
      for (const character of event.id) preferredTone = (preferredTone * 31 + character.charCodeAt(0)) % 4;
      const used = usedTones.get(event.source) || new Set();
      const choices = Array.from({length: 4}, (_, offset) => (preferredTone + offset) % 4);
      event.tone = choices.find(tone => !used.has(tone)) ?? preferredTone;
      used.add(event.tone);
      usedTones.set(event.source, used);
    }
    return matched;
  }
  function monthDates(month) {
    const first = month + '-01';
    const weekday = new Date(first + 'T00:00:00Z').getUTCDay() || 7;
    const start = addDays(first, 1 - weekday);
    return Array.from({length: 42}, (_, index) => addDays(start, index));
  }
  function rangeLayout(events) {
    const laneEnds = [];
    const toneEnds = new Map();
    // Assign lanes before clipping to the month so a continuing event keeps its position and color.
    return events.filter(event => !event.annual && event.startDate < event.endDate)
      .sort((a, b) => a.startDate.localeCompare(b.startDate) || b.endDate.localeCompare(a.endDate)
        || a.source.localeCompare(b.source) || a.id.localeCompare(b.id))
      .map((event, index) => {
        let lane = laneEnds.findIndex(end => end < event.startDate);
        if (lane === -1) lane = laneEnds.length;
        laneEnds[lane] = event.endDate;
        const sourceTones = toneEnds.get(event.source) || [];
        const choices = Array.from({length: 4}, (_, offset) => (index + offset) % 4);
        const tone = choices.find(value => !sourceTones[value] || sourceTones[value] < addDays(event.startDate, -1)) ?? index % 4;
        sourceTones[tone] = sourceTones[tone] > event.endDate ? sourceTones[tone] : event.endDate;
        toneEnds.set(event.source, sourceTones);
        return {event, lane, tone};
      });
  }
  function rangeWeeks(events, month) {
    const days = monthDates(month);
    const ranges = rangeLayout(events);
    return Array.from({length: 6}, (_, index) => {
      const weekDays = days.slice(index * 7, index * 7 + 7);
      const segments = ranges.filter(({event}) => event.startDate <= weekDays[6] && event.endDate >= weekDays[0])
        .map(({event, lane, tone}) => ({event, lane, tone,
          start: Math.max(0, weekDays.indexOf(event.startDate)),
          end: event.endDate > weekDays[6] ? 6 : weekDays.indexOf(event.endDate),
          continuesBefore: event.startDate < weekDays[0], continuesAfter: event.endDate > weekDays[6]}));
      return {days: weekDays, segments, lanes: Math.max(0, ...segments.map(segment => segment.lane + 1))};
    }).filter(week => week.days.some(date => date.startsWith(month)));
  }
  const api = {validDate, today, addDays, shiftMonth, occurrence, onDate, monthDates, rangeWeeks};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else scope.WorkbenchDates = api;
})(typeof window !== 'undefined' ? window : globalThis);
