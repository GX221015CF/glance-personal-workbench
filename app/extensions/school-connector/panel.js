import {CONFIG,WORDS} from './core.mjs';
import {publicState} from './workbench.mjs';
const $=id=>document.getElementById(id);
let busy=false,view=null,checked=false,action='connectSchool',target=null;
const labels={queued:'等待更新',opening:'正在恢复学校会话',checking:'正在检查学校页面',sso_pending:'正在完成学校跳转',
  connecting:'正在读取并保存',waiting:'需要学校验证',succeeded:'本次已更新',partial:'部分数据已更新',
  failed:'本次未完成',cooldown:'本次未发起，等待冷却',unknown:'结果待核对',cancelled:'已停止后续更新'};
async function send(command,extra={}){
  if(busy&&command!=='view')return;
  if(command!=='view')busy=true;
  if(['connectSchool','retrySchool','continueSchool'].includes(command)){
    $('unified-connect').disabled=true;$('notice').textContent='正在准备本次更新…';
  }
  try{
    const response=await chrome.runtime.sendMessage({command,...extra});
    if(command==='status'&&response?.ok)checked=true;
    if(response?.state)render(response.state);
    if(!response?.ok)$('notice').textContent=WORDS[response?.code]||'更新未完成，请检查工作台。';
  }catch{$('notice').textContent='扩展后台暂不可用，请重新打开面板。';}
  finally{busy=false;}
}
function element(tag,text){const node=document.createElement(tag);if(text)node.textContent=text;return node;}
function render(state){
  const running=Object.values(state.task?.systems||{}).some(row=>['queued','opening','checking','sso_pending','connecting'].includes(row.phase));
  view=publicState(state,running);
  const rows=Object.values(view.systems);
  action='connectSchool';target=null;
  let text='更新学校数据';
  if(running)text='正在更新…';
  else if(view.outcome==='unknown'){text='核对状态';action='status';}
  else if(view.canContinue){
    text='前往学校验证';
    const waiting=Object.entries(view.systems).find(([,row])=>row.phase==='waiting'&&row.canOpen);
    action=view.portalWaiting?'openPortal':waiting?'openSchool':'openPortal';target=view.portalWaiting?null:waiting?.[0];
  }else if(view.scheduled){text='正在更新…';}
  else if(view.updating&&rows.some(row=>['failed','partial','cooldown','cancelled'].includes(row.phase))){text='重试未完成项';action='retrySchool';}
  $('unified-connect').textContent=text;
  $('unified-connect').disabled=running||view.scheduled&&!view.canContinue||action==='retrySchool'&&!view.canRetry;
  $('unified-continue').hidden=!view.canContinue;$('unified-continue').disabled=running;
  $('cancel').hidden=!view.canCancel;$('status').disabled=running;
  $('unknown').hidden=view.outcome!=='unknown';
  $('dismiss').disabled=running||view.canCancel||!checked||!$('ack').checked;
  const successes=rows.filter(row=>row.phase==='succeeded').length;
  $('notice').textContent=!view.updating?'尚未发起本次更新；详情显示已保存数据':view.outcome==='succeeded'?`${successes}/${view.targets.length} · 更新完成`:`已完成 ${successes}/${view.targets.length}，其余结果见详情`;
  $('systems').replaceChildren();
  for(const [name,row]of Object.entries(view.systems)){
    const card=element('article');card.append(element('h2',CONFIG[name].name));
    card.append(element('p',view.updating?labels[row.phase]||'等待更新':'已保存数据'));
    const stamp=row.result?.state.updatedAt;
    card.append(element('p',stamp?'最近成功保存 '+new Date(stamp).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'尚无成功保存时间'));
    if(row.code)card.append(element('p',WORDS[row.code]||'请核对状态'));
    $('systems').append(card);
  }
}
$('identity').textContent='扩展 ID '+chrome.runtime.id;$('runtime-origin').textContent=chrome.runtime.getURL('');
$('unified-connect').addEventListener('click',()=>send(action,target?{target}:{}));
$('unified-continue').addEventListener('click',()=>send('continueSchool'));
for(const command of ['cancel','status'])$(command).addEventListener('click',()=>send(command));
$('ack').addEventListener('change',()=>void send('view'));
$('dismiss').addEventListener('click',()=>{if(checked&&$('ack').checked)void send('dismiss',{confirm:true});});
chrome.storage.onChanged.addListener((changes,area)=>{if(area==='session'&&changes.schoolConnector?.newValue)render(changes.schoolConnector.newValue);});
void send('view');
