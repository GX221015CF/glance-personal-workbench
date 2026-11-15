export const phases = {
  idle: '等待连接', queued: '等待前一个系统', opening: '正在打开学校页面',
  checking: '正在检查学校页面', sso_pending: '等待登录跳转', connecting: '正在同步并保存',
  waiting: '需要你操作', succeeded: '连接成功', failed: '连接未完成',
  unknown: '结果尚不确定', cancelled: '已取消后续操作',
};

export const errors = {
  COOLDOWN:'本次尚未发起新读取，冷却结束后可重试未完成项。',
  CREDIT_OVERVIEW_FAILED:'课程与分类已更新；学分总览未完成，保留上次可信学分。',
  CHECKING_SAVED:'正在验证后台保存的会话，无需打开学校页面。',
  PORTAL_LOGIN_REQUIRED:'请完成门户认证后点一次继续。',
  PLAN_CONFLICT: '学校培养要求或本人归属有变化，已保留原计划与上次进度，请核对；无需反复登录。',
  HOST_UNAVAILABLE: '本机组件暂不可用，请查看连接帮助。',
  HOST_NOT_FOUND: '未找到可用本机组件，请查看连接帮助。',
  HOST_FORBIDDEN: '扩展与本机组件绑定不匹配，请查看连接帮助。',
  HOST_START_FAILED: '本机组件未能启动，请查看连接帮助。',
  HOST_UPDATE_REQUIRED:'本机组件尚未更新，请运行项目tools/update-school-academic.cmd，再重试未完成项。',
  HOST_EXITED: '本机组件已退出，请检查后台状态。',
  HOST_PROTOCOL_ERROR: '本机组件通信异常，请检查后台状态。',
  HOST_NOT_CONFIGURED: '本机组件尚未绑定此扩展。', CALLER_FORBIDDEN: '本机组件绑定不匹配。',
  BRIDGE_OFFLINE: '数据后台离线，请启动个人工作台。',
  NATIVE_AUTH_UNAVAILABLE: '尚未取得本机通信凭证，请启动个人工作台。',
  NATIVE_AUTH_REJECTED: '本机通信凭证已更新，请再次检查后台状态。',
  LOGIN_REQUIRED: '请完成门户认证，再点一次“我已登录，继续连接”，其余系统由程序自动衔接。',
  WAIT_LOGIN: '请完成门户认证，再点一次“我已登录，继续连接”，其余系统由程序自动衔接。',
  WAIT_VERIFICATION: '请在学校页面完成验证码或手机验证，再回来继续。',
  PAGE_UNRECOGNIZED: '请从门户进入完整学生课表，再回来继续。',
  PAGE_CHECK_TIMEOUT: '学校页面检查超时，打开页面确认后可继续。',
  TARGET_NOT_READY: '请先完成当前任务，或取消后重新连接。',
  SCHOOL_SESSION_MISSING: '尚未读取到教务登录会话，请保留完整课表页并检查扩展版本。',
  PARTITION_UNSUPPORTED: '当前会话使用了暂不支持的分区方式，请查看连接帮助。',
  NO_SESSION: '尚未取得学校会话，请打开学校页面。',
  INVALID_SESSION: '学校会话格式异常，请查看连接帮助。',
  FRAME_SIZE: '学校会话超过通信大小限制。',
  UPSTREAM_ERROR: '学校读取暂时失败，已有数据已保留。',
  SYNC_FAILED: '同步或保存未完成，已有数据已保留。',
  NETWORK_ERROR: '学校页面暂时无法读取，确认页面后可继续。',
  TIMEOUT: '等待已超时，可以重新连接此系统。',
  CONNECTION_OUTCOME_UNKNOWN: '后台可能仍在保存，请先核对同步时间，再决定重试。',
  WORKER_INTERRUPTED: '扩展曾中断，请查看学校页面后继续。',
  TAB_CLOSED: '学校标签已关闭，可以重新连接此系统。',
  STALE_TASK: '任务已变化，已读取当前状态，请再次确认操作。',
  EXTENSION_RELOADED: '扩展已更新，请检查连接器并刷新此页。',
  REQUEST_OUTCOME_UNKNOWN: '操作响应中断，请先检查后台状态，再决定重试。',
  BRIDGE_RESPONSE_INVALID: '后台响应异常，请检查后台状态。',
  CANCELLED: '', INVALID_MESSAGE: '连接消息不兼容，请更新扩展并刷新此页。',
  INTERNAL_ERROR: '连接暂未完成，请检查后台状态。',
};

const updatePhases={queued:'等待更新',opening:'正在恢复学校会话',checking:'正在检查学校页面',
  sso_pending:'正在完成学校跳转',connecting:'正在读取并保存',waiting:'需要学校验证',
  succeeded:'本次已更新',partial:'部分数据已更新',failed:'本次更新未完成',
  cooldown:'本次未发起，等待冷却',unknown:'本次结果待核对',cancelled:'已停止后续更新'};

export function updateAction(state,uncertain=false,now=Date.now()) {
  const rows=Object.values(state?.systems||{});
  if(state?.running)return {text:'正在更新…',command:null,disabled:true};
  if(uncertain||state?.outcome==='unknown')return {text:'核对状态',command:'status',disabled:!state?.canCheck};
  if(state?.canContinue){
    const waiting=Object.entries(state.systems).find(([,row])=>row.phase==='waiting'&&row.canOpen);
    return {text:'前往学校验证',command:state.portalWaiting?'openPortal':waiting?'openSchool':'openPortal',
      target:state.portalWaiting?null:waiting?.[0],disabled:false};
  }
  if(state?.scheduled)return {text:'正在更新…',command:null,disabled:true};
  if(state?.updating&&rows.some(row=>['failed','partial','cooldown','cancelled'].includes(row.phase))){
    const retryable=rows.some(row=>['failed','partial','cooldown','cancelled'].includes(row.phase)&&(!row.retryAt||row.retryAt<=now));
    return {text:'重试未完成项',command:'retrySchool',disabled:!state.canRetry||!retryable};
  }
  return {text:'更新学校数据',command:'connectSchool',disabled:!state?.canStart};
}

export function updateProgress(state) {
  const targets=state?.targets || Object.keys(state?.systems || {});
  const rows=targets.map(target=>[target,state?.systems?.[target]||{phase:'idle'}]);
  const completed=rows.filter(([,row])=>row.phase==='succeeded').length,total=targets.length;
  const complete=!!state?.updating&&total>0&&completed===total;
  const prefix=`已完成 ${completed}/${total}`;
  const describe=(selected,single,multiple)=>selected.length===1
    ?single(systemNames[selected[0][0]]):`${multiple} ${selected.length} 项`;
  let text=prefix;
  const unknown=rows.filter(([,row])=>row.phase==='unknown');
  const waiting=rows.filter(([,row])=>row.phase==='waiting');
  const failed=rows.filter(([,row])=>row.phase==='failed');
  const partial=rows.filter(([,row])=>row.phase==='partial');
  const processing=rows.filter(([,row])=>['opening','checking','sso_pending','connecting'].includes(row.phase));
  if(complete)text=`${completed}/${total} · 更新完成`;
  else if(state?.cancelled)text+=` · 已取消后续更新${unknown.length?'，已发请求待核对':''}`;
  else if(processing.length)text+=' · '+describe(processing,name=>`正在更新${name}`,'正在更新');
  else if(state?.running)text+=' · 正在检查更新范围';
  else if(unknown.length)text+=' · '+describe(unknown,name=>`${name}结果待核对`,'结果待核对');
  else if(waiting.length)text+=' · '+describe(waiting,name=>`${name}需要学校验证`,'需要学校验证');
  else if(failed.length)text+=' · '+describe(failed,name=>`${name}更新失败`,'更新失败');
  else if(partial.length)text+=' · '+describe(partial,name=>`${name}部分更新`,'部分更新');
  else if(rows.some(([,row])=>row.phase==='cooldown'))text+=' · 等待冷却'+(state?.scheduled?'，已安排自动更新':'');
  else text+=' · 等待更新';
  return {total,completed,complete,text,ratio:total?completed/total:0,
    visible:!!state?.updating&&total>0&&!complete};
}

export function updateSummary(state,fallback={},uncertain=false) {
  const progress=updateProgress(state),pageCount=state?.pageTargets?.length||progress.total;
  if(!state?.updating){
    if(state?.outcome==='unknown')return '后台仍可能处理上次请求，请先核对状态';
    if(state?.canContinue)return '上次学校验证尚未完成，可继续或取消';
    if(state?.previousTask)return `另一页面的 ${state.previousTask.total} 项更新已结束；本页可更新 ${pageCount} 项`;
    return `点击更新本页 ${pageCount} 个来源；已保存数据可在详情查看`;
  }
  if(uncertain)return `已完成 ${progress.completed}/${progress.total} · 本次结果待核对，确认前不会重复发送`;
  if(progress.complete){
    const historical=Object.entries(state.systems).some(([target,row])=>{
      const result=latestStatus(row,fallback[target]);
      return result?.state.stale||result?.state.needsLogin;
    });
    const stamp=state.completedAt||Object.values(state.systems).map(row=>row.result?.state.updatedAt).filter(Boolean).sort((a,b)=>Date.parse(a)-Date.parse(b)).at(-1);
    const clock=stamp?new Date(stamp).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hour12:false}):'时间待核对';
    return `${historical?'上次 ':''}${progress.text} · ${clock}`;
  }
  return progress.text;
}

export function updateSource(row,fallback,updating=false,now=Date.now()) {
  const result=latestStatus(row,fallback);
  let label=updating?updatePhases[row?.phase]:'已保存数据';
  if(!result?.state.available&&!updating)label='尚无保存数据';
  else if(!updating&&result?.state.available)label=result.state.stale?'上次保存的数据':'已保存数据，尚未发起本次更新';
  if(updating&&row?.phase==='succeeded'&&result?.state.stale)label='上次更新已保存，当前展示缓存';
  if(updating&&row?.phase==='succeeded'&&result?.state.needsLogin)label='上次更新成功，当前需要学校验证';
  const hint=row?.phase==='cooldown'?`本次尚未发起新读取，约 ${Math.max(0,Math.ceil(((row.retryAt||now)-now)/1000))} 秒后${row.scheduled?'已安排自动尝试一次':'可重试'}。`
    :errors[row?.code]||'';
  return {label:label||'等待更新',hint,time:connectionTime(result?.state.updatedAt)};
}

export function connectionTime(value) {
  if (!value || !Number.isFinite(Date.parse(value))) return '尚未同步';
  return new Date(value).toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  });
}

export const systemNames = {sufe: '教务课表与考试', canvas: 'Canvas 作业与资料', academic: '学业规划'};
export function latestStatus(row, fallback) {
  const stamp = result => Date.parse(result?.state?.checkedAt || result?.state?.updatedAt || '') || 0;
  return fallback && (!row?.result || stamp(fallback) >= stamp(row.result)) ? fallback : row?.result;
}

// The next action follows the task. A waiting task must never show Start again.
export function connectionAction(target, row, fallback, task = {}) {
  const result = latestStatus(row, fallback), phase = row?.phase || 'idle';
  const conflict = row?.code === 'PLAN_CONFLICT' || result?.state?.errorCode === 'PLAN_CONFLICT';
  const unknown = task.outcome === 'unknown' || phase === 'unknown';
  const active = ['queued', 'opening', 'checking', 'sso_pending', 'connecting'].includes(phase);
  const waiting = phase === 'waiting' && !task.cancelled;
  const healthy = result?.state?.available && !result.state.needsLogin && !result.state.errorCode;
  let start = '';
  if (!conflict && !unknown && !active && !waiting && !healthy) {
    start = result?.state?.available ? '重新连接' : '连接';
  }
  return {start, continue: waiting, open: waiting && !!row?.canOpen,
    blocked: conflict || unknown, needsPortal: !conflict && (waiting || result?.state?.needsLogin === true),
    name: systemNames[target] || target};
}

export function sourceLine(row, fallback) {
  const result = latestStatus(row, fallback);
  let label = phases[row?.phase] || '正在读取';
  if (!row || row.phase === 'idle') {
    label = !result ? '正在读取' : result.state.needsLogin ? '需要重新连接'
      : result.state.stale ? '展示上次数据' : result.state.available ? '已保存数据，连接时将重新核验' : '尚未连接';
  }
  if (row?.phase === 'succeeded' && result?.state.needsLogin) label = '登录已过期，请重新连接';
  if (result?.state.available && !result.state.needsLogin && !result.state.errorCode
      && result !== row?.result && ['failed','cancelled','waiting'].includes(row?.phase)
      && Date.parse(result.state.updatedAt || '') > Date.parse(row?.result?.state.updatedAt || '1970-01-01')) {
    // Keep a waiting task actionable until explicitly continued/cancelled;
    // only finished old task failures can be superseded by a newer sync.
    if(row.phase !== 'waiting') label = result.state.stale ? '展示上次数据' : '已有可用数据';
  }
  if (result?.state.errorCode === 'PLAN_CONFLICT') label = '培养计划有变化，请核对（旧数据已保留）';
  const expired = result?.state.errorCode !== 'PLAN_CONFLICT' && result?.state.needsLogin === true && (!row || ['idle', 'succeeded'].includes(row.phase));
  return {label, expired, time: connectionTime(result?.state.updatedAt), code: errors[result?.state.errorCode] || errors[row?.code] || ''};
}

export function connectionSummary(state) {
  return {
    idle: '连接时会自动验证三个系统', running: '正在连接', waiting: '需要你完成学校页面操作',
    partial: '部分成功', succeeded: '连接完成', failed: '连接未完成',
    unknown: '结果尚不确定', cancelled: '已取消后续操作',
  }[state?.outcome] || '查看连接状态';
}
