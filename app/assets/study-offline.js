/* File-derived homework and date estimates, independent of browser storage and Canvas status. */
((scope) => {
  'use strict';
  const day = 86400000;
  function normalize(value) {
    return String(value || '').normalize('NFKC').toLowerCase().replace(/[\s\p{P}\p{S}]+/gu, '');
  }
  function isAssignmentFile(file) {
    return /作业|课后习题|课后练习|(?:^|[^a-z])(?:home[\s_-]*work|assignments?|hw)(?=$|[^a-z])/i.test(file.name)
      && !/答案|解答|题解|解析|(?:^|[^a-z])(?:solutions?|answers?)(?:[^a-z]|$)/i.test(file.name);
  }
  function validDate(value) {
    return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value)
      && Number.isFinite(Date.parse(value + 'T00:00:00Z'))
      && new Date(value + 'T00:00:00Z').toISOString().slice(0, 10) === value;
  }
  function estimateDueDate(file, course, school) {
    const upload = file.createdAt || file.modifiedAt || file.updatedAt;
    if (!upload || !Number.isFinite(Date.parse(upload))) return {date: null, reason: '文件未提供上传日期，请手动设定。'};
    const uploadedDate = new Date(Date.parse(upload) + 8 * 3600000).toISOString().slice(0, 10);
    if (!school?.meta?.available) return {date: null, reason: '课表尚不可用，请手动设定。'};
    const data = school.data;
    const number = (course.name || '').normalize('NFKC').match(/\((\d+)\)\s*$/)?.[1];
    const name = normalize((course.name || '').replace(/[（(]\d+[）)]\s*$/, ''));
    const matches = data.courses.filter(item => normalize(item.name) === name && (!number || item.number === number));
    if (matches.length !== 1) return {date: null, reason: '无法唯一匹配教务课程，请手动设定。'};
    if (!data.teachingWeeks?.length) return {date: null, reason: '课表未提供教学周日期，请手动设定。'};
    const uploaded = new Date(uploadedDate + 'T00:00:00Z');
    const weekday = uploaded.getUTCDay() || 7;
    const nextMonday = uploaded.getTime() + (8 - weekday) * day;
    for (let offset = 0; offset < 7; offset++) {
      const date = new Date(nextMonday + offset * day).toISOString().slice(0, 10);
      const week = data.teachingWeeks.find(item => item.start <= date && date <= item.end);
      const meeting = matches[0].meetings.some(item => item.weekday === offset + 1 && week && item.weeks.includes(week.week));
      if (meeting) return {date, reason: '按上传日期所在自然周的下一周，首个实际课次推算。'};
    }
    return {date: null, reason: '下一自然周没有该课程的有效课次，请手动设定。'};
  }
  function offlineStatus(dueDate, submitted, at = Date.now()) {
    if (submitted) return 'submitted';
    const today = new Date(at + 8 * 3600000).toISOString().slice(0, 10);
    return dueDate && dueDate < today ? 'overdue' : 'unsubmitted';
  }
  function buildAssignments(data, school, readDate = () => undefined, readSubmission = () => null, at = Date.now()) {
    const courses = new Map(data.courses.map(course => [course.id, course]));
    return data.files.filter(file => {
      if (!isAssignmentFile(file)) return false;
      const filename = normalize(file.name);
      const stem = normalize(file.name.replace(/\.[^.]+$/, ''));
      return !data.assignments.some(assignment => assignment.courseId === file.courseId
        && (normalize(assignment.name) === stem || normalize(assignment.description).includes(filename)));
    }).map(file => {
      const estimate = estimateDueDate(file, courses.get(file.courseId) || {name: file.courseName}, school);
      const saved = readDate(file.courseId, file.id);
      const manual = saved === null || validDate(saved);
      const dueDate = manual ? saved : estimate.date;
      const submission = readSubmission(file.courseId, file.id);
      const submitted = submission?.submitted === true;
      return {
        id: 'file-' + file.id, fileId: file.id, source: 'file', status: 'file_offline',
        courseId: file.courseId, courseName: file.courseName, name: file.name, url: file.url,
        uploadedAt: file.createdAt || file.modifiedAt || file.updatedAt || null,
        uploadFallback: !file.createdAt, dueDate,
        offlineStatus: offlineStatus(dueDate, submitted, at),
        submittedAt: submitted && typeof submission.submittedAt === 'string' ? submission.submittedAt : null,
        dateSource: manual ? 'manual' : estimate.date ? 'estimated' : 'unset',
        estimatedDate: estimate.date, dateReason: estimate.reason,
        schoolStale: Boolean(school?.meta.stale || school?.meta.needsLogin),
      };
    });
  }
  const api = {isAssignmentFile, validDate, estimateDueDate, offlineStatus, buildAssignments};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else scope.StudyOffline = api;
})(typeof window !== 'undefined' ? window : globalThis);
