const {test} = require('node:test');
const assert = require('node:assert/strict');
const {today, occurrence, onDate, monthDates, shiftMonth, rangeWeeks} = require('../assets/calendar-dates.js');
const school = {id:'school',source:'official',title:'考试',startDate:'2026-12-21',endDate:'2027-01-01',annual:false};
const personal = {id:'personal',source:'personal',title:'纪念日',startDate:'2026-11-14',endDate:'2026-11-14',annual:true};
test('Shanghai dates do not follow the browser timezone', () => {
  assert.equal(today(Date.parse('2026-11-05T16:00:00Z')), '2026-11-06');
  assert.equal(today(Date.parse('2026-11-05T15:59:59Z')), '2026-11-05');
});
test('inclusive ranges continue across months and years without extending boundaries', () => {
  assert.equal(occurrence(school, '2026-12-20'), null);
  assert.ok(occurrence(school, '2026-12-21'));
  assert.ok(occurrence(school, '2027-01-01'));
  assert.equal(occurrence(school, '2027-01-02'), null);
});
test('annual anniversaries retain actual occurrence range and original date', () => {
  assert.equal(occurrence(personal,'2025-11-14'),null);
  const result=occurrence(personal,'2027-11-14');
  assert.equal(result.startDate,'2027-11-14');
  assert.equal(result.originalStartDate,'2026-11-14');
});
test('February 29 repeats on leap years and skips common years', () => {
  const leap={...personal,startDate:'2028-02-29',endDate:'2028-02-29'};
  assert.equal(occurrence(leap,'2029-02-28'),null);
  assert.equal(occurrence(leap,'2029-03-01'),null);
  assert.ok(occurrence(leap,'2032-02-29'));
});
test('both sources and multiple events use the same day matching', () => {
  const calendar={...school,startDate:'2026-11-14',endDate:'2026-11-14'};
  const matched=onDate([personal,calendar], '2026-11-14');
  assert.equal(matched.length,2);
  assert.deepEqual(matched.map(e=>e.source),['official','personal']);
});
test('month grid starts on Monday and has 42 date cells', () => {
  const cells=monthDates('2026-11');
  assert.equal(cells.length,42);
  assert.equal(cells[0],'2026-10-26');
  assert.equal(cells.at(-1),'2026-12-06');
});
test('month navigation clips month-end dates and crosses years', () => {
  assert.equal(shiftMonth('2026-01-31',1),'2026-02-28');
  assert.equal(shiftMonth('2028-01-31',1),'2028-02-29');
  assert.equal(shiftMonth('2026-12-31',1),'2027-01-31');
});

test('continuous bars split at week boundaries and preserve inclusive endpoints', () => {
  const segments = rangeWeeks([school], '2026-12').flatMap(week => week.segments);
  assert.equal(segments.length, 2);
  assert.deepEqual(segments.map(({start, end, continuesBefore, continuesAfter}) =>
    [start, end, continuesBefore, continuesAfter]), [[0, 6, false, true], [0, 4, true, false]]);
});

test('visible calendar weeks cover the month without an extra outside-only row', () => {
  const december = rangeWeeks([school], '2026-12');
  assert.equal(december.length, 5);
  assert.equal(december.at(-1).days.at(-1), '2027-01-03');
  assert.equal(rangeWeeks([], '2026-11').length, 6);
  assert.equal(rangeWeeks([], '2021-02').length, 4);
});

test('overlapping ranges use separate stable lanes across months and input order', () => {
  const long = {...school, id: 'long', startDate: '2026-12-12', endDate: '2027-01-12'};
  const december = rangeWeeks([school, long], '2026-12').flatMap(week => week.segments);
  const january = rangeWeeks([long, school], '2027-01').flatMap(week => week.segments);
  for (const event of [school, long]) {
    const lanes = [...december, ...january].filter(segment => segment.event.id === event.id).map(segment => segment.lane);
    assert.equal(new Set(lanes).size, 1);
    const tones = [...december, ...january].filter(segment => segment.event.id === event.id).map(segment => segment.tone);
    assert.equal(new Set(tones).size, 1);
  }
  assert.notEqual(december.find(segment => segment.event.id === school.id).lane,
    december.find(segment => segment.event.id === long.id).lane);
  assert.notEqual(december.find(segment => segment.event.id === school.id).tone,
    december.find(segment => segment.event.id === long.id).tone);
});

test('adjacent separate events have different shades and can reuse the same lane', () => {
  const first = {...school, id: 'first', startDate: '2026-12-21', endDate: '2026-12-23'};
  const second = {...school, id: 'second', startDate: '2026-12-24', endDate: '2026-12-26'};
  const segments = rangeWeeks([second, first], '2026-12').flatMap(week => week.segments);
  assert.equal(segments[0].lane, segments[1].lane);
  assert.notEqual(segments[0].tone, segments[1].tone);
});

test('single-day and annual events keep date markers without continuous bars', () => {
  const weeks = rangeWeeks([personal, {...school, startDate: '2026-11-14', endDate: '2026-11-14'}], '2026-11');
  assert.equal(weeks.flatMap(week => week.segments).length, 0);
  assert.ok(weeks.every(week => week.lanes === 0));
});

test('bar coverage exactly matches date matching for official ranges and cross-year personal ranges', () => {
  const official = require('../config/school-calendar.json').events;
  const crossYear = {...personal, annual: false, startDate: '2026-12-30', endDate: '2027-01-03'};
  const events = [...official, crossYear];
  for (const month of ['2026-09', '2026-11', '2026-12', '2027-01', '2027-02', '2027-07']) {
    for (const week of rangeWeeks(events, month)) {
      week.days.forEach((date, column) => {
        const expected = onDate(events, date).filter(event => event.startDate < event.endDate).map(event => event.id).sort();
        const bars = week.segments.filter(segment => segment.start <= column && column <= segment.end);
        assert.deepEqual(bars.map(segment => segment.event.id).sort(), expected);
        assert.equal(new Set(bars.map(segment => segment.lane)).size, bars.length);
      });
    }
  }
});

test('January 1 details put the single-day holiday before ranges in their calendar lane order', () => {
  const official = require('../config/school-calendar.json').events;
  const events = onDate(official, '2027-01-01');
  assert.deepEqual(events.map(event => event.title), ['元旦', '本科在读生期末考试阶段', '毕业研究生离校手续']);
  assert.equal(new Set(events.map(event => event.tone)).size, 3);
  const week = rangeWeeks(official, '2027-01').find(week => week.days.includes('2027-01-01'));
  for (const event of events.filter(event => event.lane >= 0)) {
    const segment = week.segments.find(segment => segment.event.id === event.id);
    assert.equal(event.tone, segment.tone);
    assert.equal(event.lane, segment.lane);
  }
});

test('single-day events, including annual occurrences, avoid same-source range and single-day colors', () => {
  const range = {...school, startDate: '2027-01-01', endDate: '2027-01-05'};
  const singles = [0, 1, 2].map(index => ({...personal, source: 'official', id: `single-${index}`,
    startDate: '2026-01-01', endDate: '2026-01-01'}));
  const events = onDate([range, ...singles], '2027-01-01');
  assert.equal(events.length, 4);
  assert.equal(new Set(events.map(event => event.tone)).size, 4);
  assert.deepEqual(events.map(event => event.lane), [-1, -1, -1, 0]);
  assert.deepEqual(onDate([...singles].reverse().concat(range), '2027-01-01'), events);
});
