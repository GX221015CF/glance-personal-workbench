const {test} = require('node:test');
const assert = require('node:assert/strict');
const {isAssignmentFile, validDate, estimateDueDate, offlineStatus, buildAssignments} = require('../assets/study-offline.js');

const course = {id: '10', name: '测试课程（0116）'};
function school(meetings = [{weekday: 3, weeks: [1, 2]}]) {
  return {meta: {available: true, stale: false}, data: {
    courses: [{name: '测试课程', number: '0116', meetings}],
    teachingWeeks: [{week: 1, start: '2026-06-01', end: '2026-06-07'}, {week: 2, start: '2026-06-08', end: '2026-06-14'}],
  }};
}
const file = {id: '20', courseId: '10', courseName: course.name, name: '第一章作业.docx',
  createdAt: '2026-06-01T10:00:00+08:00', modifiedAt: '2026-06-05T10:00:00+08:00', url: 'https://canvas.shufe.edu.cn/courses/10/files/20?wrap=1'};
const data = {courses: [course], files: [file], assignments: []};

test('recognizes homework filenames and excludes answer documents', () => {
  for (const name of ['第三章作业.doc', '第二章课下作业.docx', '2026_HW3.pdf', 'Homework_2.pdf', '课后练习.pdf']) assert.ok(isAssignmentFile({name}), name);
  for (const name of ['第三章作业答案.doc', '第二章课下作业（及答案）.docx', '2026_HW1-solutions.pdf', 'assignment_answers.pdf', '国际税收教学大纲.doc', 'hwengineering.pdf']) assert.ok(!isAssignmentFile({name}), name);
});
test('Monday upload and Wednesday course give Wednesday of next calendar week', () => {
  assert.equal(estimateDueDate(file, course, school()).date, '2026-06-10');
});
test('uploads after the weekly class still target the next calendar week', () => {
  assert.equal(estimateDueDate({...file, createdAt: '2026-06-07T20:00:00+08:00'}, course, school()).date, '2026-06-10');
});
test('uses Shanghai upload date across a UTC midnight boundary', () => {
  assert.equal(estimateDueDate({...file, createdAt: '2026-06-07T18:00:00Z'}, course, school()).date, null);
});
test('valid teaching weeks and parity are required', () => {
  assert.equal(estimateDueDate(file, course, school([{weekday: 3, weeks: [1]}])).date, null);
});
test('multiple classes use the first actual meeting in the next week', () => {
  assert.equal(estimateDueDate(file, course, school([{weekday: 5, weeks: [2]}, {weekday: 2, weeks: [2]}])).date, '2026-06-09');
});
test('section mismatch and missing school data retain an unset date', () => {
  assert.equal(estimateDueDate(file, {...course, name: '测试课程（9999）'}, school()).date, null);
  assert.equal(estimateDueDate(file, course, null).date, null);
});
test('semester calendars starting on Sunday are matched by actual date', () => {
  const result = school();
  result.data.teachingWeeks = [{week: 1, start: '2026-05-31', end: '2026-06-06'}, {week: 2, start: '2026-06-07', end: '2026-06-13'}];
  assert.equal(estimateDueDate(file, course, result).date, '2026-06-10');
});
test('date estimates cross month and year boundaries', () => {
  const result = school();
  result.data.teachingWeeks = [{week: 2, start: '2026-12-28', end: '2027-01-03'}];
  result.data.courses[0].meetings = [{weekday: 5, weeks: [2]}];
  assert.equal(estimateDueDate({...file, createdAt: '2026-12-21T10:00:00+08:00'}, course, result).date, '2027-01-01');
});
test('existing online assignments and their named attachments are not duplicated', () => {
  assert.equal(buildAssignments({...data, assignments: [{courseId: '10', name: '第一章作业'}]}, school()).length, 0);
  assert.equal(buildAssignments({...data, assignments: [{courseId: '10', name: '在线提交', description: '请完成第一章作业.docx'}]}, school()).length, 0);
  assert.equal(buildAssignments({...data, assignments: [{courseId: '99', name: '第一章作业'}]}, school()).length, 1);
});
test('manual dates and explicitly cleared dates survive new estimates', () => {
  assert.equal(buildAssignments(data, school(), () => '2026-06-12')[0].dueDate, '2026-06-12');
  const cleared = buildAssignments(data, school(), () => null)[0];
  assert.equal(cleared.dueDate, null);
  assert.equal(cleared.dateSource, 'manual');
});
test('invalid stored dates do not override estimates', () => {
  assert.equal(validDate('2026-02-30'), false);
  assert.equal(validDate('2028-02-29'), true);
  assert.equal(buildAssignments(data, school(), () => 'invalid')[0].dueDate, '2026-06-10');
});
test('stale calendar estimates retain provenance', () => {
  const result = school(); result.meta.stale = true;
  const [assignment] = buildAssignments(data, result);
  assert.equal(assignment.schoolStale, true);
  assert.equal(assignment.dateSource, 'estimated');
  assert.equal(assignment.uploadedAt, file.createdAt);
});
test('due dates remain unsubmitted until the end of the Shanghai calendar day', () => {
  assert.equal(offlineStatus('2026-06-10', false, Date.parse('2026-06-10T15:59:59Z')), 'unsubmitted');
  assert.equal(offlineStatus('2026-06-10', false, Date.parse('2026-06-10T16:00:00Z')), 'overdue');
});
test('user-submitted records take priority over passed or missing dates', () => {
  const at = Date.parse('2026-06-11T00:00:00+08:00');
  assert.equal(offlineStatus('2026-06-10', true, at), 'submitted');
  assert.equal(offlineStatus(null, true, at), 'submitted');
  assert.equal(offlineStatus(null, false, at), 'unsubmitted');
  assert.equal(offlineStatus('2026-06-10', false, at), 'overdue');
});
test('confirmed dates control status instead of the estimated deadline', () => {
  const at = Date.parse('2026-06-11T10:00:00+08:00');
  const [assignment] = buildAssignments(data, school(), () => '2026-06-12', () => null, at);
  assert.equal(assignment.estimatedDate, '2026-06-10');
  assert.equal(assignment.dueDate, '2026-06-12');
  assert.equal(assignment.offlineStatus, 'unsubmitted');
  assert.equal(assignment.dateSource, 'manual');
});
test('submission records survive changed estimates and manual deadline edits', () => {
  const at = Date.parse('2026-06-11T10:00:00+08:00');
  const saved = {submitted: true, submittedAt: '2026-06-09T10:00:00+08:00'};
  const [assignment] = buildAssignments(data, school(), () => '2026-06-01', () => saved, at);
  assert.equal(assignment.offlineStatus, 'submitted');
  assert.equal(assignment.submittedAt, saved.submittedAt);
  const [cleared] = buildAssignments(data, null, () => null, () => saved, at);
  assert.equal(cleared.dueDate, null);
  assert.equal(cleared.offlineStatus, 'submitted');
});
