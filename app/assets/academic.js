import {viewData, filterCourses, statusLabel, credits, overviewMessage, restoreView} from './academic-model.mjs?v=20261109-scope';

const bridge=`${location.protocol}//${location.hostname}:8182`, key='academic-view-v1';
let saved;try{saved=JSON.parse(sessionStorage.getItem(key));}catch{}
const view=restoreView(saved), open=new Set(view.open);
let root, snapshot, teaching, busy=false, poll, interval, restored=false, error='';
let lastData='', categoryNodes=new Map(), courseNodes=new Map();
const $=id=>document.getElementById(id);
const time=value=>value?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'尚未成功同步';
function node(tag,text,cls){const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;}
function save(){view.open=[...open];view.scrollY=window.scrollY;try{sessionStorage.setItem(key,JSON.stringify(view));}catch{}}
async function request(path,method='GET'){
  const auth=await fetch(bridge+'/api/desktop',{cache:'no-store',signal:AbortSignal.timeout(5000)});
  if(!auth.ok)throw new Error('本机后台暂不可用');
  const {token}=await auth.json();
  const response=await fetch(bridge+path,{method,cache:'no-store',headers:{'X-Desktop-Token':token},signal:AbortSignal.timeout(10000)});
  const value=await response.json();
  if(!response.ok&&response.status!==429)throw new Error(value.error?.message||'读取失败');
  return value;
}
function cell(row,label,content){const td=node('td');td.dataset.label=label;if(typeof content==='string')td.textContent=content;else td.append(content);row.append(td);}
function courseTable(list,data,supplement=false){
  if(!list.length)return node('p',supplement?'当前没有匹配的补充记录':'没有匹配课程，请调整搜索词或筛选条件。','academic-note');
  const table=node('table',null,'academic-table'),head=node('thead'),hr=node('tr');
  for(const text of ['课程','分类／归属','学校状态','学分'])hr.append(node('th',text));
  head.append(hr);const body=node('tbody');
  for(const row of list){
    const identity=(supplement?'supplement:':'course:')+row.rowKey;
    let tr=courseNodes.get(identity);
    const signature=JSON.stringify(row);
    if(!tr||tr.dataset.signature!==signature){
      tr=node('tr');tr.dataset.signature=signature;
      const name=node('div',row.name);name.append(node('small',row.code));
      if(data.codes.get(row.code)>1&&!supplement)name.append(node('small','代码重复：保留各条记录，待核对'));
      if(row.outsideFixedCatalog)name.append(node('small','首次目录外的新记录'));
      cell(tr,'课程',name);
      cell(tr,'分类／归属',supplement?(row.inMainPlan?'与主计划代码重叠，独立保留':'补充记录，未自动并入主计划'):(data.groups.get(row.groupId)?.name||'未关联分类（待核对）'));
      cell(tr,'学校状态',statusLabel(row.rawStatus));
      cell(tr,'学分',supplement?`学校完成 ${credits(row.completedCredits)}`:`课程 ${credits(row.requiredCredits)} · 学校完成 ${credits(row.completedCredits)}`);
      courseNodes.set(identity,tr);
    }
    body.append(tr);
  }
  table.append(head,body);return table;
}
function paintData(force=false){
  const data=viewData(snapshot),signature=JSON.stringify(snapshot?.data);
  const y=window.scrollY;
  if(signature!==lastData){
    lastData=signature;
    const category=$('academic-category'),status=$('academic-state');
    category.replaceChildren(new Option('全部分类',''));
    for(const group of data.categories)category.append(new Option(group.name,group.id));
    for(const id of new Set(data.courses.filter(row=>!data.groups.has(row.groupId)).map(row=>row.groupId)))category.append(new Option(`未关联分类 ${id}`,id));
    status.replaceChildren(new Option('全部状态',''));
    for(const raw of data.statuses)status.append(new Option(statusLabel(raw),raw));
    // Preserve even a now-empty filter so an update never silently changes scope.
    if(view.group&&![...category.options].some(o=>o.value===view.group))category.append(new Option('原筛选分类（本次无记录）',view.group));
    if(view.status&&![...status.options].some(o=>o.value===view.status))status.append(new Option(`原状态 ${view.status}`,view.status));
    category.value=view.group;status.value=view.status;
    const summary=data.progress?.summary||{};
    $('academic-required').textContent=credits(summary.requiredCredits);
    $('academic-earned').textContent=credits(summary.earnedCredits);
    $('academic-credit-breakdown').textContent=`第一课堂 ${credits(summary.firstClassCredits)} · 非第一课堂 ${credits(summary.otherCredits)} · 艺术类 ${credits(summary.artCredits)} / ${credits(summary.artRequiredCredits)}（已获／要求）`;
    $('academic-instudy').textContent=String(data.courses.filter(r=>r.rawStatus==='在读').length);
    $('academic-course-total').textContent=String(data.courses.length);
    const choice=summary.moduleChoice;
    $('academic-choice').textContent=choice?`通识限选：${choice.candidates} 个候选模块，学校要求完成 ${choice.required} 个，已完成 ${choice.completed} 个。`:'';
    $('academic-assessment').textContent=data.progress?.assessmentRaw?`学校原评语：${data.progress.assessmentRaw}（不是正式毕业审核结论）`:'';
    $('academic-applicability').textContent=Object.values(Object.fromEntries(Object.entries(data.fixed?.definition?.applicability||{}).filter(([k])=>['GRADE','DEPT_NAME','MAJOR_NAME','LEVEL_NAME','TRAIN_NAME'].includes(k)))).filter(Boolean).join(' · ');
    $('academic-first-import').textContent=`计划首次保存 ${time(data.fixed?.fetchedAt)}。学校未提供独立计划版本、课程级建议学期及完整历史关系。`;
    const groupList=$('academic-categories'),next=new Map();
    for(const group of data.categories){
      let item=categoryNodes.get(group.id);
      if(!item){
        item=node('details',null,'academic-group');item.dataset.category=group.id;
        item.append(node('summary',group.name),node('p'),node('p',null,'academic-muted'));
        item.addEventListener('toggle',()=>{if(item.open)open.add('category:'+group.id);else open.delete('category:'+group.id);save();});
      }
      item.querySelector('summary').textContent=group.name;
      item.querySelector('p').textContent=`要求 ${credits(group.requiredCredits)} · 学校已获 ${credits(group.displayEarnedCredits)} · 在修 ${credits(group.inStudyCredits)}`;
      const counts={};for(const row of data.courses.filter(r=>r.groupId===group.id))counts[row.rawStatus]=(counts[row.rawStatus]||0)+1;
      item.querySelector('.academic-muted').textContent=Object.entries(counts).map(([k,v])=>`${statusLabel(k)} ${v}`).join(' · ')||'学校未提供该分类的课程明细';
      item.open=open.has('category:'+group.id);next.set(group.id,item);
      if(item.parentNode!==groupList)groupList.append(item);
    }
    for(const [id,item]of categoryNodes)if(!next.has(id))item.remove();categoryNodes=next;
  }else if(!force)return;
  const courses=filterCourses(data,view);
  $('academic-results').textContent=`主计划课程：显示 ${courses.length} / ${data.courses.length} 条`;
  $('academic-courses').replaceChildren(courseTable(courses,data));
  const q=view.query.trim().toLocaleLowerCase();
  const supplements=data.supplement.filter(row=>!q||`${row.name} ${row.code}`.toLocaleLowerCase().includes(q));
  $('academic-supplement-title').textContent=`补充记录 ${data.supplement.length} 条（独立保存）`;
  $('academic-supplement-list').replaceChildren(courseTable(supplements,data,true));
  $('academic-supplement-note').textContent='仅随搜索词查找，不受主计划分类／状态筛选影响；代码重叠也不重复累计学分。';
  if(restored)window.scrollTo(0,y);
}
function status(){
  const meta=snapshot?.meta;
  $('academic-status').textContent=error?'学业数据读取未完成，保留当前内容。'+error:'';
  $('academic-status').hidden=!error;
  $('academic-time').textContent=`学业数据最近保存 ${time(meta?.updatedAt)}${meta?.stale?' · 当前展示上次数据':''}`;
  $('academic-overview-note').textContent=overviewMessage(meta)+(meta?.creditOverview?.updatedAt?` · 总览最近成功 ${time(meta.creditOverview.updatedAt)}`:'');
  for(const [target,m] of [['academic',meta],['sufe',teaching?.meta]])if(m)window.dispatchEvent(new CustomEvent('study-source-state',{detail:{target,meta:m}}));
}

async function load(){
  if(busy||!root?.isConnected)return;
  busy=true;
  try{
    const read=await Promise.allSettled([request('/api/academic/v1/snapshot'),request('/api/sufe/v1/status')]);
    if(read[0].status==='fulfilled'){snapshot=read[0].value;error='';paintData();}else error=read[0].reason.message;
    if(read[1].status==='fulfilled')teaching=read[1].value;
    clearTimeout(poll);
    if(snapshot?.meta.refreshing||teaching?.meta.refreshing)poll=setTimeout(load,1500);
    if(snapshot&&!restored){restored=true;requestAnimationFrame(()=>window.scrollTo(0,view.scrollY));}
  }finally{busy=false;status();}
}

function mount(){
  const target=$('academic-workspace');if(!target||target.dataset.ready)return;
  root=target;root.dataset.ready='1';
  // Constant markup only. All school/user content is inserted via textContent.
  root.innerHTML=`<header class="academic-header"><div><p class="study-eyebrow">学业工作区</p><h1>学业进度与培养计划</h1><p id="academic-applicability" class="academic-muted"></p></div><div class="academic-actions"><a href="/study">课程与资料 →</a></div></header>
    <p id="academic-status" role="status" aria-live="polite">正在读取已保存的学业数据…</p>
    <div class="academic-summary"><div class="academic-stat">总要求学分<strong id="academic-required">暂未获取</strong></div><div class="academic-stat">已获总学分<strong id="academic-earned">暂未获取</strong></div><div class="academic-stat">在修课程<strong id="academic-instudy">—</strong></div><div class="academic-stat">主计划课程记录<strong id="academic-course-total">—</strong></div></div>
    <p id="academic-credit-breakdown" class="academic-muted"></p><p id="academic-overview-note" class="academic-muted" role="status"></p>
    <section class="academic-panel" aria-labelledby="academic-groups-title"><h2 id="academic-groups-title">培养分类</h2><p id="academic-choice"></p><p id="academic-assessment" class="academic-note"></p><div id="academic-categories" class="academic-groups"></div></section>
    <section class="academic-panel" aria-labelledby="academic-courses-title"><h2 id="academic-courses-title">课程查找</h2><p>“待修”包含选修候选，不代表每门都必须完成；“已过”与计入某类培养要求分开，以学校分类结果为准。</p><div class="academic-toolbar"><label>课程名称或代码<input id="academic-query" type="search" maxlength="200" autocomplete="off" placeholder="查找课程…"></label><label>培养分类<select id="academic-category"><option value="">全部分类</option></select></label><label>修读状态<select id="academic-state"><option value="">全部状态</option></select></label><button id="academic-clear" class="study-refresh" type="button">清除筛选</button></div><p id="academic-results" role="status"></p><div id="academic-courses"></div></section>
    <details id="academic-supplement" class="academic-panel"><summary id="academic-supplement-title">补充记录</summary><p id="academic-supplement-note" class="academic-note"></p><div id="academic-supplement-list"></div></details>
    <details id="academic-source" class="academic-source"><summary>来源与适用范围</summary><p>来源：<a href="https://eams.sufe.edu.cn/tch/studyCheck/courseFinishInfo/planCompletion.jsp" target="_blank" rel="noopener noreferrer">上财门户 → 学业规划 ↗</a>。当前应用的培养要求与修读情况，不是完整培养方案原文、全部历史成绩或正式毕业审核。</p><p id="academic-time" class="academic-muted"></p><p id="academic-first-import"></p><p>固定计划首次完整保存后保留，普通同步只更新进度。课程级建议学期、重修／免修／替代历史未提供；未知或重复记录保留待核对。</p></details>`;
  $('academic-query').value=view.query;
  for(const id of ['academic-supplement','academic-source']){
    const el=$(id);el.open=open.has(id);el.addEventListener('toggle',()=>{if(el.open)open.add(id);else open.delete(id);save();});
  }
  for(const [id,field,event]of [['academic-query','query','input'],['academic-category','group','change'],['academic-state','status','change']])$(id).addEventListener(event,e=>{view[field]=e.target.value;paintData(true);save();});
  $('academic-clear').addEventListener('click',()=>{view.query='';view.group='';view.status='';for(const id of ['academic-query','academic-category','academic-state'])$(id).value='';paintData(true);save();});
  snapshot=undefined;teaching=undefined;restored=false;lastData='';categoryNodes=new Map();courseNodes=new Map();
  load();clearInterval(interval);interval=setInterval(()=>{if(document.visibilityState==='visible')load();},60000);
}
window.addEventListener('pagehide',save);
window.addEventListener('study-source-state-request',()=>{if(root?.isConnected)status();});
window.addEventListener('school-connection-data-updated',event=>{if(event.detail?.targets?.some(t=>['academic','sufe'].includes(t)))load();});
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')load();});
new MutationObserver(mount).observe(document.documentElement,{childList:true,subtree:true});mount();
