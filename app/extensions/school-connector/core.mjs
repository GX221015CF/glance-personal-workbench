export const HOST = 'com.personalworkbench.showcase_connector';
export const TARGETS = ['sufe', 'canvas', 'academic'];
export const CONFIG = Object.freeze({
  sufe: {name: '教务课表与考试', host: 'eams.sufe.edu.cn', entry: 'https://eams.sufe.edu.cn/eams/courseTableForStd.action'},
  academic: {name: '学业规划', host: 'eams.sufe.edu.cn', entry: 'https://eams.sufe.edu.cn/tch/studyCheck/courseFinishInfo/planCompletion.jsp'},
  canvas: {name: 'Canvas 作业与资料', host: 'canvas.shufe.edu.cn', entry: 'https://canvas.shufe.edu.cn/'}
});
export const ACTIVE = new Set(['queued','opening','checking','sso_pending','connecting','waiting']);
export function targetsForPage(path='/study') {
  if(['/academic','/academic/'].includes(path))return ['sufe','academic'];
  if(['/study','/study/'].includes(path))return [...TARGETS];
  throw new Fault('INVALID_MESSAGE');
}
export function taskTargets(task) {
  const targets=task?.targets || Object.keys(task?.systems || {});
  return [...new Set(targets)].filter(target=>TARGETS.includes(target));
}
export function unfinishedTask(task) {
  return !!task && (task.cooldownScheduled || Object.values(task.systems || {}).some(row=>
    ACTIVE.has(row.phase)||row.phase==='unknown'||!task.cancelled&&['failed','partial','cooldown'].includes(row.phase)));
}
export class Fault extends Error { constructor(code) {super(code);this.code=code;} }
export const WORDS = Object.freeze({
  CHECKING_SAVED:'正在验证后台已保存的会话，不打开学校页面。',
  PORTAL_LOGIN_REQUIRED:'请完成上财门户认证，再点一次“我已登录，继续连接”。',
  PLAN_CONFLICT:'学校培养要求或本人归属有变化，原计划和进度已保留，请核对后处理。无需反复登录。',
  COOLDOWN:'本次尚未发起新读取，冷却结束后只重试未完成项。',
  CREDIT_OVERVIEW_FAILED:'课程与分类已更新，学分总览未完成，保留上次可信学分。',
  IDLE:'尚未连接', queued:'等待前一个系统检查完成', opening:'正在打开学校页面', checking:'正在检查目标系统',
  sso_pending:'已点击一次登录，等待学校跳转', connecting:'后台正在验证并保存',
  waiting:'请查看学校页面，完成必要操作后点继续', succeeded:'连接成功', failed:'连接未完成',
  unknown:'结果尚不确定，请先检查后台状态', cancelled:'本次连接已取消',
  HOST_UNAVAILABLE:'本机组件未安装、绑定不匹配或未能启动。',
  HOST_NOT_FOUND:'Edge 未找到可用的本机组件，请核对注册及清单文件。诊断码：HOST_NOT_FOUND。',
  HOST_FORBIDDEN:'Edge 拒绝此扩展调用本机组件，请核对扩展 ID 绑定。诊断码：HOST_FORBIDDEN。',
  HOST_START_FAILED:'Edge 找到了组件，但未能启动它。诊断码：HOST_START_FAILED。',
  HOST_EXITED:'本机组件被启动后退出，尚未完成状态查询。诊断码：HOST_EXITED。',
  HOST_PROTOCOL_ERROR:'Edge 与本机组件的消息通信异常。诊断码：HOST_PROTOCOL_ERROR。',
  HOST_NOT_CONFIGURED:'本机组件尚未绑定这个真实扩展 ID。', CALLER_FORBIDDEN:'本机组件的扩展 ID 绑定不匹配。',
  HOST_UPDATE_REQUIRED:'本机组件尚未更新，请运行项目的update-school-academic.cmd后再更新学校数据。',
  BRIDGE_OFFLINE:'工作台数据服务未启动。', NATIVE_AUTH_UNAVAILABLE:'无法取得本机凭证，请启动工作台并核对运行用户。',
  NATIVE_AUTH_REJECTED:'本机凭证不可用，请重启工作台后检查状态。',
  LOGIN_REQUIRED:'需要在学校页面完成登录或验证。',
  WAIT_VERIFICATION:'学校正在要求验证，请在原页面完成图形验证码或手机验证，再点继续。',
  WAIT_LOGIN:'请在学校页面手动登录；扩展不会读取或填写账号密码。',
  CONNECTION_OUTCOME_UNKNOWN:'结果尚不确定，后台可能仍在保存。请先检查状态，不会自动重发。',
  WORKER_INTERRUPTED:'扩展后台曾中断。请检查状态；不会自动重发连接。',
  TAB_CLOSED:'学校连接标签已关闭，请重新开始这个系统的连接。',
  TARGET_NOT_READY:'尚未确认学校认证成功，请核对目标页面。',
  PAGE_CHECK_TIMEOUT:'页面检查未及时响应，请打开学校页面确认后继续。其他系统会继续处理。',
  PAGE_UNRECOGNIZED:'页面已打开，但还未识别到教务课表。请打开学生课表后继续。',
  PARTITION_UNSUPPORTED:'检测到分区会话，目前不支持，未导出会话。',
  NO_SESSION:'未取得目标系统会话，请在学校页面登录后继续。',
  SCHOOL_SESSION_MISSING:'未读取到教务登录会话，已暂停。请保留完整课表页，无需反复登录或点击继续。',
  STALE_TASK:'连接任务已变化，请查看当前状态后再操作。',
  EXTENSION_RELOADED:'扩展连接已更新，请重新检查连接器。',
  REQUEST_OUTCOME_UNKNOWN:'操作响应中断，请先检查状态，确认后再手动重试。',
  INVALID_SESSION:'目标系统会话格式不符合限制。', FRAME_SIZE:'会话超过本机消息限制。',
  UPSTREAM_ERROR:'学校暂时无法返回有效数据，原有数据已保留。', SYNC_FAILED:'后台连接或保存失败，原有数据已保留。',
  NETWORK_ERROR:'学校页面暂时无法读取，请检查网络后继续。',
  BRIDGE_RESPONSE_INVALID:'本机服务响应不符合协议，请检查状态后重试。',
  TIMEOUT:'自动连接已超时，可在学校页面完成后再继续。',
  CANCELLED:'任务已取消。', INVALID_MESSAGE:'连接消息无效。', INTERNAL_ERROR:'连接未完成，请检查状态后重试。'
});
export function safeCode(error) {return Object.hasOwn(WORDS,error?.code)?error.code:'INTERNAL_ERROR';}
export function ownSender(sender, runtime) {
  const url=sender?.url;
  return sender?.id===runtime.id && url===runtime.getURL('panel.html');
}
export function selectCookies(target, rows, storeId) {
  const host=CONFIG[target]?.host;
  if(!host) throw new Fault('INVALID_MESSAGE');
  const parentSession=c=>['sufe','academic'].includes(target)&&c.name==='JSESSIONID'&&['sufe.edu.cn','.sufe.edu.cn'].includes(c.domain)&&c.hostOnly===false
    &&typeof c.path==='string'&&(new URL(CONFIG[target].entry).pathname===c.path
      ||new URL(CONFIG[target].entry).pathname.startsWith(c.path.endsWith('/')?c.path:c.path+'/'));
  const kept=rows.filter(c=>c.storeId===storeId && (c.domain===host || c.domain==='.'+host||parentSession(c))
    && (target!=='canvas' || ['_normandy_session','_legacy_normandy_session'].includes(c.name)));
  if(kept.some(c=>c.partitionKey)) throw new Fault('PARTITION_UNSUPPORTED');
  if(!kept.length) throw new Fault('NO_SESSION');
  if(kept.length>(target==='canvas'?2:30)) throw new Fault('INVALID_SESSION');
  const keys=new Set();
  const cookies=kept.map(c=>{
    const domain=parentSession(c)?host:c.domain;
    const key=[c.name,domain.replace(/^\./,''),c.path].join('|');
    if(keys.has(key) || typeof c.value!=='string' || !c.value || c.value.length>8192
       || /[\u0000-\u001f\u007f]/.test(c.value) || !/^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}$/.test(c.name)
       || !c.path?.startsWith('/') || c.path.length>512 || (target==='canvas' && c.path!=='/')) throw new Fault('INVALID_SESSION');
    keys.add(key);
    // Only narrow an applicable parent JSESSIONID to the fixed EAMS host.
    // Do not modify the browser cookie or export other parent/portal cookies.
    return {name:c.name,value:c.value,domain,path:c.path,secure:c.secure,httpOnly:c.httpOnly};
  });
  return {cookies};
}
export function recover(state) {
  if(!state || state.version!==1) return {version:1,task:null,lastStatus:null};
  if(state.task) for(const row of Object.values(state.task.systems)) {
    if(['queued','connecting','opening','checking','sso_pending'].includes(row.phase)) {
      row.phase=row.phase==='connecting'?'unknown':state.task.kind==='update'&&row.phase==='queued'?'failed':'waiting';
      row.code='WORKER_INTERRUPTED';
    }
  }
  return state;
}
export function boundedMessage(message) {
  if(new TextEncoder().encode(JSON.stringify(message)).length>65536) throw new Fault('FRAME_SIZE');
  return message;
}
// This function executes only in a verified, exact school host's isolated world.
// Never reads input values, cookies, authentication tokens, or coursework contents.
export async function inspectSchool(target, clickLogin=false) {
  const visible=e=>!!e && !!(e.getClientRects().length) && getComputedStyle(e).visibility!=='hidden';
  const hostname=location.hostname;
  if(hostname==='login.sufe.edu.cn') {
    const verification=Array.from(document.querySelectorAll('img,canvas,input')).some(e=>visible(e)&&/captcha|verify|验证码|短信/.test((e.id||'')+' '+(e.getAttribute('placeholder')||'')+' '+(e.getAttribute('alt')||'')));
    return {kind:'human',verification};
  }
  if(target==='canvas' && hostname==='jiaoxue.shufe.edu.cn') {
    const candidates=Array.from(document.querySelectorAll('#login a.wl[onclick="d1()"],#login_m a.wl[onclick="d1_m()"]')).filter(e=>visible(e)&&e.textContent.trim()==='登录');
    if(candidates.length) {
      if(clickLogin) {candidates[0].click();return {kind:'clicked'};}
      return {kind:'sso_entry'};
    }
    const canvasLink=Array.from(document.querySelectorAll('a[href]')).find(a=>a.textContent.trim()==='Canvas'&&a.href==='https://canvas.sufe.edu.cn/');
    return canvasLink?{kind:'canvas_entry'}:{kind:'human'};
  }
  if(hostname!==(target==='canvas'?'canvas.shufe.edu.cn':'eams.sufe.edu.cn')) return {kind:'transition'};
  if(Array.from(document.querySelectorAll('input[type=password]')).some(visible)) return {kind:'human',verification:false};
  if(target==='academic'){
    if(location.pathname!=='/tch/studyCheck/courseFinishInfo/planCompletion.jsp')return {kind:'unrecognized'};
    const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),8000);
    try {
      const response=await fetch('/tch/studyCheck/courseFinishInfo/com.lg.studyCheck.planFinishInfo.queryStdInfo.biz.ext',{method:'POST',credentials:'same-origin',redirect:'error',signal:controller.signal,headers:{'X-Requested-With':'XMLHttpRequest'}});
      if(!response.ok)return {kind:'unauthenticated'};
      const text=await response.text();if(text.length>5000000)return {kind:'network'};
      const data=JSON.parse(text);
      return Array.isArray(data.stdInfo)&&data.stdInfo.length===1&&typeof data.stdInfo[0].GRADE==='string'?{kind:'ready'}:{kind:'unauthenticated'};
    }catch{return {kind:'unauthenticated'};}finally{clearTimeout(timer);}
  }
  if(target==='sufe') {
    // A rendered course table is different from EAMS's bootstrap form. Requiring
    // #startWeek/semester script markers in a second GET misclassifies this view.
    // This is only a candidate: Native connect must query courses AND exams and
    // save successfully before the worker may report a successful connection.
    if(!/^\/eams\/courseTableForStd(?:!courseTable)?\.action$/.test(location.pathname))return {kind:'unrecognized'};
    const headers=['课程代码','课程名称','课程序号','上课安排'];
    const table=Array.from(document.querySelectorAll('table')).some(t=>
      Array.from(t.querySelectorAll('tr')).slice(0,2).some(row=>{
        const labels=Array.from(row.querySelectorAll('th,td')).map(c=>c.textContent.replace(/\s+/g,''));
        return headers.every(label=>labels.includes(label));
      }));
    return {kind:table?'ready':'unrecognized'};
  }
  const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),8000);
  try {
    const path='/api/v1/courses?enrollment_state=active&enrollment_type=student&per_page=1';
    const response=await fetch(path,{credentials:'same-origin',cache:'no-store',redirect:'error',signal:controller.signal});
    if(response.status===401||response.status===403) return {kind:'unauthenticated'};
    if(!response.ok) return {kind:'network'};
    const text=await response.text();if(text.length>5000000) return {kind:'network'};
    let value;try{value=JSON.parse(text.replace(/^while\(1\);/,''));}catch{return {kind:'unauthenticated'};}
    return Array.isArray(value)&&value.every(x=>x&&Number.isInteger(x.id))?{kind:'ready'}:{kind:'unauthenticated'};
  } catch {return {kind:'network'};} finally {clearTimeout(timer);}
}
