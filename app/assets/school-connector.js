import {errors, updateAction, updateProgress, updateSummary, updateSource, systemNames} from './school-connector-model.js?v=20261109-scope';

const channel = 'personal-workbench-school-v1';
const deliveryKey = 'workbench-school-delivery-v1';
const clientId = crypto.randomUUID();
const requests = new Map();
const fallback = {};
let root;
let state = null;
let transport = 'checking';
let feedback = '';
let busy = false;
let uncertain = false;
let nativeChecked = false;
let lastSuccess = {};
let lastHelloAt = 0;
let detectedVersion = '';
const expectedVersion = '0.11.0';
const pageTargets=location.pathname?.startsWith('/academic')?['sufe','academic']:['sufe','canvas','academic'];
try { uncertain = JSON.parse(sessionStorage.getItem(deliveryKey))?.uncertain === true; } catch {}

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function button(text, command, target) {
  const node = element('button', text, 'study-refresh school-action');
  node.type = 'button';
  node.dataset.schoolCommand = command;
  if (target) node.dataset.schoolTarget = target;
  return node;
}

function rememberDelivery(value) {
  uncertain = value;
  if (value) {
    nativeChecked = false;
    const acknowledgment = root?.querySelector('#school-connection-ack');
    if (acknowledgment) acknowledgment.checked = false;
  }
  try { sessionStorage.setItem(deliveryKey, JSON.stringify({uncertain: value})); } catch {}
}

function applyState(next) {
  if (!next || !next.systems || !Number.isInteger(next.revision)) return;
  if (state && next.revision < state.revision) return;
  if (next.outcome === 'unknown' && (state?.outcome !== 'unknown' || next.taskId !== state?.taskId)) {
    nativeChecked = false;
    const acknowledgment = root?.querySelector('#school-connection-ack');
    if (acknowledgment) acknowledgment.checked = false;
  }
  state = next;
  if (state.running || state.canCancel) {
    rememberDelivery(false);
    if (feedback===errors.REQUEST_OUTCOME_UNKNOWN) feedback='';
  }
  const changed = [];
  for (const target of next.targets || Object.keys(next.systems)) {
    const time = state.systems[target]?.result?.state.updatedAt;
    if (time && time !== (lastSuccess[target] || fallback[target]?.state.updatedAt)) changed.push(target);
    if (time) lastSuccess[target] = time;
  }
  if (changed.length) window.dispatchEvent(new CustomEvent('school-connection-data-updated', {detail: {targets: changed}}));
}

function request(command, target = null, confirm = false) {
  const mutation = !['hello', 'view'].includes(command);
  if (busy || !root?.isConnected) return;
  busy = true;
  if (mutation) feedback = '';
  if (command === 'hello') lastHelloAt = Date.now();
  const id = crypto.randomUUID();
  const timer = setTimeout(() => {
    requests.delete(id);
    busy = false;
    if (mutation && ['start', 'continue', 'cancel', 'connectSchool', 'continueSchool', 'retrySchool', 'verifySchoolFlow'].includes(command)) {
      rememberDelivery(true);
      feedback = errors.REQUEST_OUTCOME_UNKNOWN;
    } else if (command === 'hello' || command === 'view') {
      transport = 'unavailable';
    } else feedback = '连接器暂未响应，请检查连接器后再操作。';
    render();
  }, mutation ? 14000 : 3500);
  requests.set(id, {timer, command});
  window.postMessage({channel, direction: 'request', version: 1, clientId, id,
    sentAt: Date.now(), command, target, taskId: state?.taskId || null, confirm}, location.origin);
  render();
}

window.addEventListener('message', event => {
  const message = event.data;
  if (event.source !== window || event.origin !== location.origin || message?.channel !== channel
      || message.direction !== 'response' || message.version !== 1 || message.clientId !== clientId) return;
  const pending = requests.get(message.id);
  if (!pending) return;
  clearTimeout(pending.timer);
  requests.delete(message.id);
  busy = false;
  const response = message.response;
  if (response?.protocol !== 1 || typeof response.extensionVersion !== 'string') {
    transport = response?.code === 'EXTENSION_RELOADED' ? 'unavailable' : 'incompatible';
    render();
    return;
  }
  detectedVersion = response.extensionVersion;
  transport = detectedVersion === expectedVersion ? 'ready' : 'incompatible';
  applyState(response.state);
  if (!response.ok) feedback = response.code==='STALE_TASK'?'':errors[response.code] || '更新暂未完成，请核对状态。';
  else if (pending.command === 'status') {
    nativeChecked = true;
    rememberDelivery(false); feedback = '';
  } else if (pending.command === 'dismiss') {
    rememberDelivery(false);
    nativeChecked = false;
    feedback = '';
  }
  render();
});

function build() {
  root = element('section', '', 'school-connector');
  root.id = 'study-school-connector';
  root.setAttribute('aria-label', '学校数据更新');
  const heading = element('div', '', 'school-connector-heading');
  const primary = button('更新学校数据', 'connectSchool');
  primary.id = 'school-update-primary';
  const summary = element('span', '', 'school-summary');
  summary.id = 'school-connection-summary';
  summary.setAttribute('role', 'status');
  summary.setAttribute('aria-live', 'polite');
  const progress=element('div','','school-update-progress');
  progress.id='school-update-progress';
  progress.setAttribute('role','progressbar');
  progress.setAttribute('aria-label','学校数据更新进度');
  progress.setAttribute('aria-describedby','school-connection-summary');
  progress.setAttribute('aria-valuemin','0');
  const fill=element('span','','school-update-progress-fill');
  fill.id='school-update-progress-fill';
  progress.append(fill);
  progress.hidden=true;
  const resume = button('我已登录，继续更新', 'continueSchool');
  resume.id = 'school-update-continue';
  resume.hidden = true;
  const cancel = button('取消本次更新', 'cancel');
  cancel.id = 'school-update-cancel';
  cancel.hidden = true;
  heading.append(primary, progress, summary, resume, cancel);
  root.append(heading);
  const scope=element('p','','school-update-scope');
  scope.id='school-update-scope';scope.hidden=true;root.append(scope);
  const manager = element('details', '', 'school-login-manager');
  manager.id = 'school-login-manager';
  manager.append(element('summary', '查看详情'));
  const rows = element('div', '', 'school-connection-systems');
  manager.append(rows);
  const diagnostic = element('details', '', 'school-connection-help');
  diagnostic.id = 'school-update-diagnostics';
  diagnostic.append(element('summary', '故障诊断'), button('核对后台状态', 'status'), button('重新检测扩展', 'hello'));
  diagnostic.append(element('p', '需要上财连接器0.11.0和已更新的本机组件。后台离线时正常启动工作台。学校要求的密码、验证码和二次认证在学校页面完成。'));
  const unknown = element('div', '', 'school-uncertain');
  unknown.id = 'school-connection-uncertain';
  const acknowledge = element('label');
  const check = element('input');check.type = 'checkbox';check.id = 'school-connection-ack';
  acknowledge.append(check, document.createTextNode('已核对后台时间，确认结束本次未确认任务'));
  check.addEventListener('change', render);
  unknown.append(element('p', '已发送的请求仍可能完成，先核对状态，确认前不会重复发送。'), acknowledge, button('结束未确认任务', 'dismiss'));
  diagnostic.append(unknown);
  manager.append(diagnostic);
  root.append(manager);
  root.addEventListener('click', event => {
    const action = event.target.closest('button[data-school-command]');
    if (!event.isTrusted || !action || action.disabled) return;
    const command = action.dataset.schoolCommand;
    if (command === 'dismiss' && (!nativeChecked || !check.checked)) return;
    request(command, action.dataset.schoolTarget || null, command === 'dismiss');
  });
  return root;
}

function renderRows(targets){
  const rows=root.querySelector('.school-connection-systems');
  const signature=targets.join('|');
  if(rows.dataset.targets===signature)return;
  rows.dataset.targets=signature;rows.replaceChildren();
  for (const target of targets) {
    const row = element('div', '', 'school-connection-row');
    row.dataset.system = target;
    const info = element('div', '', 'school-system-info');
    info.append(element('strong', systemNames[target]), element('span', '', 'school-system-phase'),
      element('time', '', 'school-system-time'), element('p', '', 'school-system-hint'));
    row.append(info);
    rows.append(row);
  }
}

function render() {
  if (!root?.isConnected) return;
  const ready = transport === 'ready';
  const unknown = uncertain || state?.outcome === 'unknown';
  const pendingUpdate=[...requests.values()].some(item=>['connectSchool','continueSchool','retrySchool'].includes(item.command));
  const message = transport === 'checking' ? '正在检测更新组件'
    : transport === 'unavailable' ? '扩展暂未响应，请展开故障诊断检查'
      : transport === 'incompatible' ? '请重新加载上财连接器0.11.0并刷新本页'
        : feedback || (pendingUpdate&&!state?.running?'正在准备本次更新…':updateSummary(state, fallback, uncertain));
  root.querySelector('#school-connection-summary').textContent = message;
  const targets=state?.targets||pageTargets;
  renderRows(targets);
  const progress=updateProgress(state);
  const track=root.querySelector('#school-update-progress');
  track.hidden=!progress.visible;
  track.setAttribute('aria-valuemax',String(progress.total||targets.length));
  track.setAttribute('aria-valuenow',String(progress.completed));
  track.setAttribute('aria-valuetext',message);
  root.querySelector('#school-update-progress-fill').style.width=`${progress.ratio*100}%`;
  const scope=root.querySelector('#school-update-scope');
  scope.hidden=!state?.foreignTask;
  scope.textContent=state?.foreignTask?`另一页面发起的更新 · ${targets.map(target=>systemNames[target]).join('、')}（${targets.length} 项），完成后可按本页范围更新。`:'';
  const policy = updateAction(state, unknown);
  const primary = root.querySelector('#school-update-primary');
  primary.textContent = pendingUpdate?'正在更新…':policy.text;
  primary.dataset.schoolCommand = policy.command || 'connectSchool';
  if (policy.target) primary.dataset.schoolTarget = policy.target;
  else delete primary.dataset.schoolTarget;
  primary.disabled = !ready || busy || policy.disabled;
  const resume = root.querySelector('#school-update-continue');
  resume.hidden = !state?.unified || !state?.canContinue;
  resume.disabled = !ready || busy || !!state?.running;
  const cancel = root.querySelector('#school-update-cancel');
  cancel.hidden = !state?.canCancel;
  cancel.disabled = busy;
  for (const target of targets) {
    const row = root.querySelector(`[data-system="${target}"]`);
    const data = state?.systems[target];
    const line = updateSource(data, fallback[target], state?.updating);
    row.querySelector('.school-system-phase').textContent = line.label;
    row.querySelector('.school-system-time').textContent = `最近成功保存 ${line.time}`;
    row.querySelector('.school-system-hint').textContent = line.hint;
    row.dataset.phase = data?.phase || 'idle';
  }
  const status = root.querySelector('[data-school-command="status"]');
  status.disabled = !ready || busy || !state?.canCheck;
  root.querySelector('[data-school-command="hello"]').disabled = busy;
  root.querySelector('#school-connection-uncertain').hidden = !unknown;
  root.querySelector('[data-school-command="dismiss"]').disabled = busy || !nativeChecked
    || !root.querySelector('#school-connection-ack').checked || state?.running || state?.canCancel;
}

function mount() {
  const workspace = document.getElementById('study-workspace') || document.getElementById('academic-workspace');
  if (!workspace || root?.isConnected) return;
  const heading=workspace.querySelector('.study-topbar, .academic-header');
  if (!heading) return;
  heading.after(build());
  render();
  request('hello');
}

window.addEventListener('study-source-state', event => {
  const {target, meta} = event.detail || {};
  if (!['sufe', 'academic', 'canvas'].includes(target) || !meta) return;
  fallback[target] = {state: {available: meta.available, stale: meta.stale, needsLogin: meta.needsLogin,
    updatedAt: meta.updatedAt, checkedAt: meta.checkedAt, errorCode: meta.errorCode, creditOverview: meta.creditOverview}};
  render();
});
// The deferred study script may have finished its first API read before this
// module's import resolves. Ask for its current metadata without fetching again.
window.dispatchEvent(new CustomEvent('study-source-state-request'));

document.addEventListener('click', event => {
  const shortcut = event.target.closest('[data-school-help]');
  if (!event.isTrusted || !shortcut || !root?.isConnected) return;
  event.preventDefault();
  root.querySelector('#school-login-manager').open = true;
  root.scrollIntoView({block:'start',behavior:'smooth'});
  root.querySelector('#school-update-primary').focus();
});

new MutationObserver(mount).observe(document.documentElement, {childList: true, subtree: true});
mount();
setInterval(() => {
  if (!root?.isConnected || busy || document.visibilityState !== 'visible') return;
  if (transport === 'ready') request('view');
  else if (Date.now() - lastHelloAt > 10000) request('hello');
}, 2000);
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && !busy && root?.isConnected) request(transport === 'ready' ? 'view' : 'hello');
});
