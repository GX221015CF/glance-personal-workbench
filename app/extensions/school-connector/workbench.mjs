import {WORDS, TARGETS, ACTIVE, Fault, targetsForPage, taskTargets, unfinishedTask} from './core.mjs';

export const PAGE_PROTOCOL = 1;
export const PAGE_CHANNEL = 'personal-workbench-school-v1';
const pageOrigins = new Set(['http://127.0.0.1:8180', 'http://localhost:8180']);
const pagePaths = new Set(['/study', '/study/', '/academic', '/academic/']);
const commands = new Set(['hello', 'view', 'start', 'continue', 'cancel', 'status', 'openSchool', 'dismiss', 'connectSchool', 'continueSchool', 'retrySchool', 'openPortal', 'verifySchoolFlow']);
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const validUuid = value => typeof value === 'string' && uuid.test(value);
const phases = new Set([...ACTIVE, 'succeeded', 'partial', 'cooldown', 'failed', 'unknown', 'cancelled']);

export function workbenchUrl(value) {
  try {
    const url = new URL(value);
    return pageOrigins.has(url.origin) && pagePaths.has(url.pathname) && !url.username && !url.password && !url.search;
  } catch { return false; }
}

export function workbenchSender(sender, runtime) {
  return sender?.id === runtime.id && sender.frameId === 0 && Number.isInteger(sender.tab?.id)
    && typeof sender.documentId === 'string' && sender.documentId.length > 0 && sender.documentId.length <= 100
    && workbenchUrl(sender.url) && workbenchUrl(sender.tab.url)
    && new URL(sender.url).origin === sender.origin;
}

export function pageRequest(value, now = Date.now()) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Fault('INVALID_MESSAGE');
  const required = ['channel', 'version', 'clientId', 'id', 'sentAt', 'command', 'target', 'taskId', 'confirm'];
  if (Object.keys(value).length !== required.length || required.some(key => !Object.hasOwn(value, key))
      || value.channel !== PAGE_CHANNEL || value.version !== PAGE_PROTOCOL
      || !validUuid(value.clientId) || !validUuid(value.id)
      || !Number.isFinite(value.sentAt) || Math.abs(now - value.sentAt) > 15000
      || !commands.has(value.command) || typeof value.confirm !== 'boolean'
      || (value.taskId !== null && !validUuid(value.taskId))) throw new Fault('INVALID_MESSAGE');
  const targeted = ['start', 'continue', 'openSchool'].includes(value.command);
  if (targeted ? ![...TARGETS, ...(value.command === 'openSchool' ? [] : ['all'])].includes(value.target) : value.target !== null)
    throw new Fault('INVALID_MESSAGE');
  if (value.command !== 'dismiss' && value.confirm) throw new Fault('INVALID_MESSAGE');
  return value;
}

function safeStatus(status) {
  if (!status || typeof status !== 'object') return null;
  const state = {};
  for (const key of ['available', 'stale', 'needsLogin', 'refreshing']) state[key] = status.state?.[key] === true;
  for (const key of ['updatedAt', 'checkedAt']) {
    const value = status.state?.[key];
    state[key] = typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T[0-9:+.-]{8,30}$/.test(value) ? value : null;
  }
  state.errorCode = ['LOGIN_REQUIRED','PLAN_CONFLICT','SYNC_FAILED','UPSTREAM_ERROR','INVALID_SESSION'].includes(status.state?.errorCode) ? status.state.errorCode : null;
  const overview=status.state?.creditOverview;
  if(overview&&typeof overview==='object')state.creditOverview={available:overview.available===true,stale:overview.stale===true,
    errorCode:['NOT_ACQUIRED','PAGE_UNAVAILABLE','SOURCE_CHANGED','INVALID_FIELDS'].includes(overview.errorCode)?overview.errorCode:null,
    updatedAt:typeof overview.updatedAt==='string'&&/^\d{4}-\d{2}-\d{2}T[0-9:+.-]{8,30}$/.test(overview.updatedAt)?overview.updatedAt:null};
  const counts = {};
  for (const key of ['categories', 'courses', 'courseCount', 'exams', 'examCount', 'publishedExams', 'publishedExamCount', 'assignments', 'files', 'pending']) {
    const value = status.counts?.[key];
    if (Number.isInteger(value) && value >= 0 && value <= 1000000) counts[key] = value;
  }
  return {state, counts};
}

export function publicState(state, running, pagePath='/study') {
  const pageTargets=targetsForPage(pagePath),storedTask=state.task;
  const originalTargets=taskTargets(storedTask);
  const differentScope=storedTask?.kind==='update'&&originalTargets.join('|')!==pageTargets.join('|');
  // Keep unfinished work's real scope across pages. A settled task from another
  // page is history, not a newly completed task for this page's smaller scope.
  const task=differentScope&&!running&&!unfinishedTask(storedTask)?null:storedTask;
  const targets=task?taskTargets(task):pageTargets;
  const systems = {};
  for (const target of targets) {
    const row = task?.systems?.[target];
    systems[target] = {
      connectionPath: ['saved_session','official_sso'].includes(row?.connectionPath) ? row.connectionPath : null,
      phase: phases.has(row?.phase) ? row.phase : 'idle',
      code: Object.hasOwn(WORDS, row?.code) ? row.code : null,
      retryAt: Number.isFinite(row?.retryAt) ? row.retryAt : null,
      scheduled: row?.phase==='cooldown'&&task?.cooldownScheduled===true&&(row.autoRetryCount||0)<1,
      canOpen: !!row?.tabId && ['waiting', 'checking', 'sso_pending'].includes(row.phase),
      result: safeStatus(row?.result || state.lastStatus?.[target]),
    };
  }
  const rows = Object.values(systems);
  const uncertain = rows.some(row => row.phase === 'unknown');
  const waiting = rows.some(row => row.phase === 'waiting');
  const active = rows.some(row => ACTIVE.has(row.phase));
  const successes = rows.filter(row => row.phase === 'succeeded').length;
  const scheduled=task?.cooldownScheduled===true&&!task.cancelled;
  const orphanBusy=!task&&targets.some(target=>state.lastStatus?.[target]?.state.refreshing);
  let outcome = 'idle';
  if (uncertain||orphanBusy) outcome = 'unknown';
  else if (running) outcome = 'running';
  else if (waiting) outcome = successes ? 'partial' : 'waiting';
  else if (successes) outcome = rows.some(row => ['failed', 'partial', 'cooldown', 'cancelled'].includes(row.phase)) ? 'partial' : 'succeeded';
  else if (task) outcome = task.cancelled ? 'cancelled' : 'failed';
  return {
    unified: task?.unified === true,
    targets,
    pageTargets,
    sourcePage: ['/academic','/study'].includes(task?.sourcePage)?task.sourcePage:null,
    foreignTask: !!task&&differentScope,
    previousTask: !task&&storedTask?{total:originalTargets.length,
      completed:originalTargets.filter(target=>storedTask.systems[target]?.phase==='succeeded').length,
      completedAt:typeof storedTask.completedAt==='string'?storedTask.completedAt:null}:null,
    updating: task?.kind === 'update',
    cancelled: task?.cancelled===true,
    scheduled,
    createdAt: Number.isFinite(task?.createdAt)?task.createdAt:null,
    completedAt: typeof task?.completedAt==='string'?task.completedAt:null,
    portalWaiting: task?.unified === true && task?.portalWaiting === true,
    portalCanOpen: Number.isInteger(task?.portalTabId),
    revision: Number.isInteger(state.revision) ? state.revision : 0,
    taskId: typeof storedTask?.id === 'string' && uuid.test(storedTask.id) ? storedTask.id : null,
    outcome, systems, running,
    canStart: !running && !active && !uncertain && !scheduled && !orphanBusy,
    canContinue: !running && waiting && !task?.cancelled,
    canRetry: !running && !active && !uncertain && !scheduled && rows.some(row=>['failed','partial','cooldown','cancelled'].includes(row.phase)
      &&(!row.retryAt||row.retryAt<=Date.now())),
    canCancel: (active||scheduled) && !task?.cancelled,
    canCheck: !running,
    canDismiss: !running && uncertain,
  };
}
