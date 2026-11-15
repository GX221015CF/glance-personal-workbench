(() => {
  'use strict';
  const base = 'http://127.0.0.1:8182';
  const storage = 'workbench-study-ima-v1';
  const model = window.StudyImaModel;
  const labels = {exists:'已保存',unsynced:'待保存',review:'状态需检查',failed:'保存失败',new_version:'有新版本',anomaly:'原 ima 文件未找到',unlinked:'尚未选择保存位置',unchecked:'尚未检查 ima'};
  const errors = {NAME_CONFLICT:'同名结果已变化，请重新准备清单',CONTENT_CHANGED:'内容指纹发生变化，请使用新版本处理',
    OUTCOME_UNKNOWN:'结果待确认，请先核对状态',SYNC_BUSY:'已有任务正在执行',TARGET_MISSING:'目标目录已失效，请重新关联',
    REMOTE_MISSING:'原媒体未找到，已保留关联供核对',INCOMPLETE_LIST:'目录读取不完整，当前展示上次核对结果',
    RULE_CHANGED:'保存规则已更新，正在重新核对',SOURCE_METADATA_UNAVAILABLE:'附件元数据暂不可读取，刷新 ima 状态可重试',
    UNSUPPORTED_FILE:'文件格式暂不支持或超过20 MiB',LOGIN_REQUIRED:'请使用更新学校数据恢复学校连接'};
  Object.assign(errors,{INVALID_PDF:'下载内容未通过文件特征或大小校验',SOURCE_CONFLICT:'来源资料发生变化，请重新核对或预览',
    NETWORK_ERROR:'网络读取失败，可稍后重试',RATE_LIMITED:'请求频率受限，请稍后重试',FORBIDDEN:'当前请求被拒绝，请核对访问权限',
    SOURCE_LOCKED:'资料尚未开放或已锁定，请查看 Canvas',DOWNLOAD_FORBIDDEN:'Canvas 拒绝读取，请检查这份资料的访问权限',
    COS_CREDENTIAL_EXPIRED:'上传临时凭据已过期',COS_CREDENTIAL_NOT_YET_VALID:'上传临时凭据尚未生效',
    COS_UPLOAD_REJECTED:'上传请求被COS拒绝',COS_UPLOAD_UNKNOWN:'上传结果待核对',SAVE_FAILED:'本机保存未完成，请先核对状态'});
  let stored = {};
  try { stored = JSON.parse(sessionStorage.getItem(storage)) || {}; } catch {}
  const state = {filter:model.restoreFilter(stored.filter),
    selected:new Set(Array.isArray(stored.selected) ? stored.selected : []), anchor:null,
    expanded:new Set(Array.isArray(stored.expanded) ? stored.expanded : []),
    links:{},comparisons:{},job:null,actionError:'',completedNotice:null,
    unresolvedJobs:[],pending:stored.pending || null,formats:[],maxFileBytes:20*1024*1024};
  let context = {files:[],courses:[],courseId:''}, visibleRows = [], refs, repaint = () => {}, generation = 0, timer, busy = false;
  const cloudRefreshers = new Set();
  const automaticChecks = new Map();
  const checks = new Map();
  const checkFeedback = new Map();
  let more=null, actionDialogs=null;
  const node = (tag, text='', className='') => { const value=document.createElement(tag);value.textContent=text;if(className)value.className=className;return value; };
  function button(text, action, className='study-text-button', {local=false}={}) {
    const value=node('button',text,className);value.type='button';
    value.addEventListener('click',()=>{if(local)action();else void act(action);});return value;
  }
  function officialLink(){
    const link=node('a','打开 ima','ima-official');link.href='https://ima.qq.com/';link.target='_blank';link.rel='noopener noreferrer';return link;
  }
  function locationText(target){
    const path=String(target?.path||'').split('/').filter(Boolean).join(' › ');
    const root=target?.libraryId&&(target.folderId??target.targetFolderId)===target.libraryId;
    return [path,root?'根目录':''].filter(Boolean).join(' › ');
  }
  function save() { try { sessionStorage.setItem(storage,JSON.stringify({filter:state.filter,selected:[...state.selected],expanded:[...state.expanded],pending:state.pending})); } catch {} }
  const active = () => ['queued','running','checking'].includes(state.job?.phase);
  const supported = file => (file.members||[file]).some(member=>!member.locked&&!member.metadataUnavailable
    && Number.isFinite(member.size)&&member.size>0&&member.size<=state.maxFileBytes
    && state.formats.includes((member.name||file.name).split('.').at(-1).toLowerCase()));
  const info = file => model.record(file,state.comparisons,state.links);
  const eligible = file => supported(file);
  const selectionReason=file=>file.metadataUnavailable?'文件信息暂不可读取':file.locked?'资料尚未开放或已锁定，请查看 Canvas':
    file.size>state.maxFileBytes?'文件超过20 MiB':!Number.isFinite(file.size)||file.size<=0?'文件大小未提供':'暂不支持此格式';
  function message(error) { return error.message || errors[error.code] || '操作未完成，请核对本机连接及保存状态'; }
  async function api(route, body) {
    const auth=await fetch(base+'/api/desktop',{cache:'no-store',signal:AbortSignal.timeout(5000)});
    if(!auth.ok)throw new Error('本机服务暂不可用，请确认工作台已启动');
    const {token}=await auth.json();
    const response=await fetch(base+'/api/ima/v1/workspace/'+route,{method:'POST',cache:'no-store',
      headers:{'Content-Type':'application/json','X-Desktop-Token':token},body:JSON.stringify(body),signal:AbortSignal.timeout(120000)});
    const result=await response.json();
    if(!response.ok||result.error)throw Object.assign(new Error(result.error?.message||errors[result.error?.code]||'ima操作未完成'),{code:result.error?.code});
    return result.data;
  }
  function checkCourse(courseId){
    const libraryId=state.links[courseId]?.libraryId;
    const scope=courseId+':'+libraryId;
    if(checks.has(scope))return checks.get(scope);
    const request=performCheck(courseId,libraryId,scope);
    checks.set(scope,request);render();
    return request;
  }
  async function performCheck(courseId,libraryId,scope){
    checkFeedback.set(scope,'');
    try{
      const result=await api('check',{courseId});
      if(state.links[courseId]?.libraryId===libraryId&&result.courseId===courseId&&result.libraryId===libraryId){state.comparisons[courseId]=result;checkFeedback.set(scope,'已刷新');}
    }catch(error){
      if(state.links[courseId]?.libraryId===libraryId){
        const previous=state.comparisons[courseId];
        state.comparisons[courseId]={...(previous?.libraryId===libraryId?previous:{courseId,libraryId,rows:[],checkedAt:null,complete:false}),
          stale:true,error:{code:error.code||'NETWORK_ERROR',message:message(error)},lastAttemptAt:new Date().toISOString()};
      }
      checkFeedback.set(scope,message(error));throw error;
    }finally{
      checks.delete(scope);repaint();render();
    }
  }
  function refreshCourse(courseId){void checkCourse(courseId).catch(()=>{});}
  async function act(action) {
    if(busy)return;
    busy=true;state.actionError='';render();
    const initialDialog=[...document.body.querySelectorAll('dialog')].filter(dialog=>dialog.open).at(-1);
    const createdDialogs=new Set();actionDialogs=createdDialogs;
    const initialFeedback=initialDialog?.querySelector('.ima-dialog-feedback');
    if(initialFeedback)initialFeedback.textContent='';
    let problem=null;
    try { await action(); }
    catch(error) { problem=error; }
    finally {
      busy=false;
      actionDialogs=null;
      const currentDialog=[...document.body.querySelectorAll('dialog')].filter(dialog=>dialog.open).at(-1);
      if(problem&&!currentDialog&&!initialDialog&&!createdDialogs.size)state.actionError=message(problem);
      render();
      if(problem&&currentDialog&&(currentDialog===initialDialog||createdDialogs.has(currentDialog)))currentDialog.querySelector('.ima-dialog-feedback').textContent=message(problem);
    }
  }
  function showError(error){state.actionError=message(error);render();}
  function absorb(value) {
    state.links=value.links||state.links;
    state.comparisons=value.comparisons||state.comparisons;
    state.job=value.job??null;
    state.unresolvedJobs=value.unresolvedJobs||[];
    state.formats=value.formats||state.formats;
    state.maxFileBytes=value.maxFileBytes||state.maxFileBytes;
    if(state.job?.requestId===state.pending?.requestId){state.pending=null;save();}
  }
  async function refresh() {
    const id=++generation, courseId=context.courseId;
    const value=await api('view',{courseId});
    if(id!==generation)return;
    const wasActive=active();
    absorb(value);
    if(wasActive&&!active()){
      state.completedNotice=state.job?.id;
      for(const row of state.job?.rows||[])if(['success','skipped'].includes(row.status)&&row.source.courseId){
        state.selected.delete(model.key(row.source));
        for(const file of context.files)if(file.courseId===row.source.courseId&&(file.memberIds||[]).includes(row.source.fileId))state.selected.delete(model.key(file));
      }
      save();
    }
    repaint();render();schedule();
    checkCourseWhenChanged();
    if(wasActive&&!active())for(const callback of cloudRefreshers)void callback();
  }
  function schedule() {
    clearTimeout(timer);
    if(active()&&refs?.panel.isConnected)timer=setTimeout(()=>{if(document.visibilityState==='visible')void refresh().catch(error=>{showError(error);schedule();});else schedule();},1500);
  }
  function renderControls() {
    if(!refs)return;
    refs.setup.disabled=busy||active();
    refs.upload.disabled=busy||active()||!state.selected.size;refs.clear.disabled=busy||!state.selected.size;
  }
  const currentMode=()=>state.filter;
  function render() {
    if(!refs)return;
    renderControls();
    // Async completion updates the existing file controls without another list repaint.
    for(const checkbox of document.body.querySelectorAll('.ima-file-select')){
      const file=checkbox._imaFile;
      if(!file)continue;
      checkbox.checked=state.selected.has(model.key(file));
      checkbox.disabled=busy||active()||!eligible(file);
    }
    const link=state.links[context.courseId],comparison=state.comparisons[context.courseId];
    refs.setup.hidden=!context.courseId||!!link;
    refs.linkLabel.textContent=context.courseId?'ima · '+(link?locationText(link)||'已设置位置':'未设置保存位置'):'ima · 按课程设置保存位置';
    const problem=state.actionError||(comparison?.stale&&context.courseId?(comparison.error?.message||errors[comparison.error?.code]||'本次读取未完成，保留上次结果'):'');
    refs.warning.hidden=!problem;refs.notice.textContent=problem||'';
    refs.warningLabel.textContent=state.actionError?'ima 操作未完成':'ima 状态暂未更新';
    refs.warningRetry.disabled=busy||active();
    refs.status.value=state.filter;
    const keys=visibleRows.filter(eligible).map(model.key),selected=keys.filter(key=>state.selected.has(key)).length;
    refs.all.disabled=busy||active()||!keys.length;refs.all.checked=!!keys.length&&selected===keys.length;
    refs.all.indeterminate=selected>0&&selected<keys.length;
    refs.all.setAttribute('aria-label',`全选当前筛选结果，共 ${keys.length} 份可选`);
    const hidden=[...state.selected].filter(key=>!visibleRows.some(file=>model.key(file)===key)).length;
    refs.selection.textContent=state.selected.size?`已选 ${state.selected.size} 份${hidden?' · 筛选外 '+hidden+' 份':''}`:'';
    refs.selection.hidden=!state.selected.size;
    renderJob();
    renderMore();
  }
  function timeText(value) { return new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}); }
  function renderJob() {
    const job=state.job,p=model.progress(job);
    const attention=!!state.pending||!!state.unresolvedJobs.length||!!job&&(job.phase!=='complete'||p.failed>0||p.unknown>0);
    const recent=state.completedNotice&&state.completedNotice===job?.id;
    refs.job.hidden=!active()&&!attention&&!recent;
    refs.progress.setAttribute('aria-valuemax',String(Math.max(1,p.total)));
    refs.progress.setAttribute('aria-valuenow',String(p.processed));
    refs.progress.setAttribute('aria-valuetext',`已处理 ${p.processed}/${p.total}，成功 ${p.success}，失败 ${p.failed}，待确认 ${p.unknown}`);
    refs.fill.style.width=`${p.percent}%`;refs.progress.hidden=!active()&&!p.unknown;
    const skipped=job?.rows.filter(row=>row.status==='skipped').length||0;
    const omitted=job?.rows.filter(row=>row.status==='omitted').length||0;
    const result=(skipped===p.total&&p.total?`已存在 ${skipped} 份，已跳过`:`成功 ${p.success}${p.failed?' · 失败 '+p.failed:''}${p.unknown?' · 待确认 '+p.unknown:''}`)+(omitted?' · 本次跳过 '+omitted:'');
    const target=job?.target?.path;
    refs.jobLabel.textContent=active()?`已处理 ${p.processed}/${p.total}${target?' · '+target:''}`
      :state.pending?'保存结果待确认':job?.phase==='interrupted'?`任务中断 · ${result}`:`保存结果 · ${result}`;
    const courseIds=[...new Set((job?.rows||[]).map(row=>row.source.courseId).filter(Boolean))];
    const scope=courseIds.map(id=>context.courses.find(row=>row.id===id)?.name||id).join('、');
    refs.historyLabel.textContent=job?`${job.action==='move'?'上次归档':'上次保存'}${scope?' · '+scope:''} · ${result}${job.finishedAt?' · '+timeText(job.finishedAt):''}`:'暂无保存记录';
    const signature=JSON.stringify([job,state.unresolvedJobs]);
    for(const list of [refs.results,refs.historyResults])if(list._signature!==signature){
      list._signature=signature;list.replaceChildren();
      const names={queued:'等待处理',running:'正在处理',success:'已完成',skipped:'已存在，跳过',omitted:'本次跳过',failed:'失败',unknown:'待确认',not_run:'尚未执行'};
      for(const row of job?.rows||[])list.append(node('li',`${row.source.fileName} · ${names[row.status]}${row.error?' · '+(errors[row.error.code]||row.error.code):''}`));
      for(const previous of state.unresolvedJobs)for(const row of previous.rows.filter(row=>row.status==='unknown'))list.append(node('li',`${row.source.fileName} · 先前任务结果待确认`));
    }
    refs.jobDetails.hidden=!active()&&!attention;
    refs.retry.hidden=active()||!(job?.rows.some(row=>row.status==='not_run'||row.status==='failed'&&row.retryable!==false));
    refs.verify.hidden=active()||!state.pending&&!p.unknown&&!state.unresolvedJobs.length;
  }
  async function retryAction(){
    state.completedNotice=null;state.job=await api('retry',{jobId:state.job.id});render();schedule();
  }
  async function verifyAction(){
    const ids=new Set(state.unresolvedJobs.map(job=>job.id));
    if(state.job?.rows.some(row=>row.status==='unknown'))ids.add(state.job.id);
    for(const jobId of ids)await api('reconcile',{jobId});
    await refresh();
  }
  function leaveMore(openChild){
    const options={courseId:more.course.value,returnToMore:true,opener:refs.moreButton};
    more.modal.close();return openChild(options);
  }
  function renderMore(){
    if(!more?.modal.isConnected||!more.modal.open)return;
    const courseId=more.course.value,link=state.links[courseId],comparison=state.comparisons[courseId];
    const available=context.courses.some(course=>course.id===courseId);
    const scope=courseId+':'+link?.libraryId,checking=checks.has(scope);
    more.status.textContent=!available?'请选择要操作的课程':(link?locationText(link):'尚未设置保存位置');
    more.detail.textContent=(comparison?.checkedAt?'最近核对 '+timeText(comparison.checkedAt):'尚未完成核对')+(comparison?.stale?' · 上次结果，待重新核对':'');
    more.feedback.textContent=checking?'刷新中…':checkFeedback.get(scope)||'';
    more.feedback.setAttribute('aria-busy',String(checking));
    refs.check.replaceChildren();
    if(checking){const spinner=node('span','','ima-spinner');spinner.setAttribute('aria-hidden','true');refs.check.append(spinner);}
    refs.check.append(node('span',checking?'刷新中…':'刷新 ima 状态'));
    refs.check.setAttribute('aria-busy',String(checking));
    refs.link.textContent=link?'更改保存位置':'设置保存位置';
    refs.link.disabled=busy||active()||!available;
    refs.check.disabled=busy||active()||!available||!link||checking;
    refs.browse.disabled=busy||active()||!available;
    more.retry.hidden=refs.retry.hidden;more.verify.hidden=refs.verify.hidden;
    more.retry.disabled=busy||active();more.verify.disabled=busy||active();
    more.course.disabled=busy;
  }
  function openMore(courseId=context.courseId){
    if(more?.modal.isConnected&&more.modal.open){more.modal.focus();return;}
    const modal=dialog('更多操作',{opener:refs.moreButton,trackAction:false}),course=node('select'),status=node('p','','ima-notice');
    course.setAttribute('aria-label','更多操作的课程');option(course,'选择课程','');
    for(const row of context.courses)option(course,row.name,row.id);
    course.value=courseId;course.addEventListener('change',renderMore);
    const actions=node('div','','ima-more-actions'),retry=button('重试失败文件',retryAction),verify=button('检查保存结果',verifyAction);
    actions.append(refs.link,refs.check,refs.browse,retry,verify);
    const details=node('details'),detail=node('p'),feedback=node('p','','ima-refresh-feedback');
    details.append(node('summary','核对详情'),detail);feedback.setAttribute('role','status');feedback.setAttribute('aria-live','polite');
    refs.historyDetails.open=false;
    modal.append(field('课程',course),status,actions,feedback,details,refs.history);
    more={modal,course,status,retry,verify,detail,feedback};refs.moreButton.setAttribute('aria-expanded','true');
    modal.addEventListener('close',()=>{if(more?.modal===modal){more=null;refs.moreButton.setAttribute('aria-expanded','false');}},{once:true});
    renderMore();
  }
  function mount(callback) {
    repaint=callback;
    if(refs?.panel.isConnected)return;
    const parent=document.getElementById('study-file-tools')||document.getElementById('study-file-result')?.parentNode;if(!parent)return;
    const panel=node('section','','study-ima');panel.setAttribute('aria-label','ima资料保存');
    const controls=node('div','','ima-controls'),linkLabel=node('span','','ima-link-label');
    const setup=button('设置保存位置',()=>openTarget('link',null,{courseId:context.courseId}));
    const moreButton=node('button','更多','ima-more-button');moreButton.type='button';
    moreButton.addEventListener('click',()=>openMore());moreButton.setAttribute('aria-haspopup','dialog');moreButton.setAttribute('aria-expanded','false');
    controls.append(linkLabel,setup,moreButton);
    const link=button('更改保存位置',()=>leaveMore(options=>openTarget('link',null,options)));
    const check=button('刷新 ima 状态',()=>refreshCourse(more.course.value),'study-text-button ima-refresh-button',{local:true});
    const browse=button('目录管理',()=>void leaveMore(options=>browseLibrary(options)),'study-text-button',{local:true});
    const warning=node('details','','ima-warning'),warningLabel=node('summary'),notice=node('p','','ima-notice'),warningRetry=button('重试检查',()=>{
      const courseId=context.courseId;
      if(courseId&&state.links[courseId])refreshCourse(courseId);else void refresh().catch(showError);
    },'study-text-button',{local:true});
    notice.setAttribute('role','status');warning.append(warningLabel,notice,warningRetry);warning.hidden=true;
    let status=document.getElementById('study-file-state');
    if(!status){
      status=node('select');status.id='study-file-state';
      for(const [value,label] of [['all','全部'],['unsynced','待保存'],['exists','已保存'],['other','其他']])option(status,label,value);
      const label=node('label','状态','study-search-label');label.append(status);parent.append(label);
    }
    status.setAttribute('aria-label','筛选保存状态');status.value=state.filter;
    status.addEventListener('change',()=>{state.filter=model.restoreFilter(status.value);save();repaint();render();});
    const toolbar=node('div','','ima-toolbar'),selectBar=node('label','','ima-selection'),all=node('input');all.type='checkbox';
    const allLabel=node('span','全选筛选结果');selectBar.append(all,allLabel);
    all.addEventListener('change',()=>{for(const file of visibleRows.filter(eligible)){if(all.checked)state.selected.add(model.key(file));else state.selected.delete(model.key(file));}save();repaint();render();});
    const selection=node('span','','ima-selected-count'),clear=button('取消',()=>{state.selected.clear();state.anchor=null;save();repaint();render();},'ima-action ima-cancel',{local:true});
    clear.setAttribute('aria-label','取消所有资料选择');
    const upload=button('保存到 ima',()=>openTarget('upload'),'ima-action ima-save');
    toolbar.append(selectBar,upload,clear,officialLink(),selection);
    const history=node('section','','ima-history'),historyLabel=node('p'),historyResults=node('ul'),historyDetails=node('details');
    historyDetails.append(node('summary','文件明细'),historyResults);history.append(historyLabel,historyDetails);
    const job=node('section','','ima-job');job.setAttribute('aria-label','当前保存任务');
    const jobLabel=node('p'),progress=node('div','','ima-progress'),fill=node('span');
    progress.setAttribute('role','progressbar');progress.setAttribute('aria-label','本次资料处理进度');progress.setAttribute('aria-valuemin','0');progress.append(fill);
    const jobDetails=node('details'),results=node('ul');jobDetails.append(node('summary','逐项结果'),results);
    jobDetails.open=state.expanded.has('job');
    jobDetails.addEventListener('toggle',()=>{if(jobDetails.open)state.expanded.add('job');else state.expanded.delete('job');save();});
    const retry=button('重试失败文件',retryAction),verify=button('检查保存结果',verifyAction);
    job.append(jobLabel,progress,retry,verify,jobDetails);panel.append(controls,toolbar,warning,job);parent.after(panel);
    refs={panel,controls,linkLabel,setup,moreButton,link,check,browse,warning,warningLabel,warningRetry,notice,status,all,allLabel,selection,clear,upload,job,jobLabel,progress,fill,jobDetails,results,retry,verify,history,historyLabel,historyResults,historyDetails};
    render();
    void refresh().catch(showError);
  }
  function setContext(value) {
    const changed=context.courseId!==value.courseId;
    if(changed){state.actionError='';state.completedNotice=null;}
    context=value;
    for(const file of context.files){
      const aliases=(file.memberIds||[]).map(id=>file.courseId+':'+id);
      if(aliases.some(key=>state.selected.has(key))){
        for(const alias of aliases)state.selected.delete(alias);
        state.selected.add(model.key(file));
      }
    }
    save();
    if(changed&&refs)void refresh().catch(showError);
    checkCourseWhenChanged();
  }
  function checkCourseWhenChanged(){
    const courseId=context.courseId,link=state.links[courseId];
    if(!refs||busy||active()||!link||!context.courses.some(course=>course.id===courseId))return;
    const signature=model.sourceSignature(context.files.filter(file=>file.courseId===courseId));
    const scope=courseId+':'+link.libraryId;
    if(automaticChecks.get(scope)===signature)return;
    automaticChecks.set(scope,signature);
    refreshCourse(courseId);
  }
  function filter(rows) { return rows.filter(file=>model.matches(file,currentMode(),info(file),StudyMaterialDates)); }
  function setRows(rows) { visibleRows=rows;render(); }
  function decorate(card,file) {
    if(!refs)return;
    card.querySelector('.study-restriction')?.remove();
    const key=model.key(file), current=info(file);
    let checkbox=card.querySelector('.ima-file-select');
    if(!checkbox){
      checkbox=node('input','','ima-file-select');checkbox.type='checkbox';checkbox.setAttribute('aria-label',`选择 ${file.name}`);
      checkbox.addEventListener('click',event=>{state.selected=model.toggleSelection(state.selected,visibleRows.filter(eligible).map(model.key),state.anchor,key,checkbox.checked,event.shiftKey);state.anchor=key;save();repaint();render();});
      card.prepend(checkbox);card.classList.add('has-ima');
    }
    checkbox._imaFile=file;
    checkbox.checked=state.selected.has(key);checkbox.disabled=busy||active()||!eligible(file);
    const reason=selectionReason(file);
    checkbox.title=supported(file)?'':reason;
    let status=card.querySelector('.ima-file-state');
    if(!status){status=node('div','','ima-file-state');card.querySelector('.study-file-content').append(status);}
    const signature=JSON.stringify([current,supported(file),active(),context.courseId,state.filter]);
    if(status._signature===signature)return;
    status._signature=signature;status.replaceChildren();
    const metadata=card.querySelector('.study-file-info');
    if(metadata&&current.savedAssociations?.length>1){
      let associations=metadata.querySelector('.ima-saved-associations');
      if(!associations){associations=node('details','','ima-saved-associations');metadata.append(associations);}
      const opened=associations.open;
      associations.replaceChildren(node('summary',`已有保存关联 ${current.savedAssociations.length} 条`));
      for(const entry of current.savedAssociations)associations.append(node('p',`Canvas ID ${entry.sourceFileId} · ${entry.path||'云端文件待核对'}`));
      associations.open=opened;
    }
    status.hidden=['unlinked','unchecked'].includes(current.status)&&supported(file)&&state.filter!=='other';
    const label=current.stale?'待复核 · 上次'+(labels[current.status]||'状态未知'):labels[current.status]||'待核对';
    if((state.filter==='other'||!['unlinked','unchecked'].includes(current.status))&&(supported(file)||current.status==='exists'))status.append(node('span',label,`ima-state is-${current.stale?'review':current.status}`));
    if(current.path){const folder=current.path.split('/').filter(Boolean).slice(0,-1).join(' › ');status.append(node('span',' · '+(folder||'根目录')));}
    else if(current.paths?.length>1)status.append(node('span',` · ${current.paths.length} 个位置`));
    if(current.stale)status.append(node('span',' · 上次核对'));
    if(current.error&&supported(file))status.append(node('span',' · '+(errors[current.error.code]||current.error.code)));
    if(!supported(file)){
      const detail=node('details','','ima-unselectable'),summary=node('summary','不可选');
      detail.append(summary,node('p',reason));status.append(detail);
    }
    if(current.status==='new_version')status.append(button('处理新版本',()=>{state.selected=new Set([key]);save();return openTarget('version');}));
  }
  function dialog(title,options={}) {
    const opener=options.opener||document.activeElement;
    const value=node('dialog','','ima-dialog'),heading=node('h2',title),close=node('button','关闭','study-text-button');
    heading.id='ima-dialog-title-'+crypto.randomUUID();value.setAttribute('aria-labelledby',heading.id);
    close.type='button';close.addEventListener('click',()=>value.close());
    const header=node('div','','ima-dialog-heading');header.append(heading,close);
    const feedback=node('p','','ima-dialog-feedback');feedback.setAttribute('role','alert');
    if(options.returnToMore){
      const back=node('button','返回更多','study-text-button');back.type='button';
      back.addEventListener('click',()=>{value.close();openMore(options.courseId);});header.prepend(back);
    }
    value.append(header,feedback);document.body.append(value);if(options.trackAction!==false)actionDialogs?.add(value);
    value.addEventListener('close',()=>{value.remove();if(opener?.isConnected&&![...document.body.querySelectorAll('dialog')].some(dialog=>dialog.open))opener.focus();},{once:true});value.showModal();return value;
  }
  function field(label,element) {const row=node('label',label,'ima-field');row.append(element);return row;}
  function option(select,text,value) {const row=node('option',text);row.value=value;select.append(row);}
  async function openTarget(mode,move=null,options={}) {
    const modal=dialog(mode==='link'?'选择这门课的保存位置':mode==='move'?'选择归档位置':mode==='version'?'保存新版本到 ima':'保存资料到 ima',options);
    const course=node('select');
    for(const row of context.courses)option(course,row.name,row.id);
    const sources=move?.files||context.files.filter(file=>state.selected.has(model.key(file)));
    const selectedCourses=[...new Set(sources.map(file=>file.courseId).filter(Boolean))];
    const courseNames=selectedCourses.map(id=>context.courses.find(row=>row.id===id)?.name||sources.find(file=>file.courseId===id)?.courseName||id);
    course.value=options.courseId||(mode!=='link'&&selectedCourses.length===1?selectedCourses[0]:context.courseId||context.courses[0]?.id||'');
    if(mode==='link')modal.append(field('课程',course));
    const link=state.links[course.value],initialLibrary=move?.libraryId||link?.libraryId||'';
    let targetUnavailable=false;
    let target={libraryId:initialLibrary,folderId:link?.libraryId===initialLibrary?link.folderId:initialLibrary,
      path:link?.libraryId===initialLibrary?link.path||'':''};
    const pairs=sources.map(file=>({courseId:file.courseId,fileId:String(file.id??file.fileId)}));
    const count=move?.mediaIds.length??pairs.length;
    const targetText=node('strong','','ima-current-target'),change=button('更改',openPicker);
    const targetRow=node('div','','ima-target-row');targetRow.append(targetText,change);modal.append(targetRow);
    const scope=courseNames.length===1?' · '+courseNames[0]:courseNames.length>1?` · ${courseNames.length} 门课程`:'';
    const countText=node('p',mode==='link'?'':`本次 ${count} 份资料${scope}`,'ima-file-count');countText.hidden=mode==='link';
    const notice=node('p','','ima-notice');notice.setAttribute('role','status');
    const listing=node('ul','','ima-preview-files');
    const conflictBox=node('div','','ima-name-conflicts');
    let conflictChoices=[];
    function showFiles(rows){
      listing.replaceChildren();
      for(const file of rows){
        const courseName=file.courseName||context.courses.find(row=>row.id===file.courseId)?.name;
        listing.append(node('li',(courseName?courseName+' · ':'')+(file.fileName||file.name||'')+
        (file.size!=null?` · ${file.size.toLocaleString()} 字节`:'')));
      }
    }
    showFiles(sources);
    modal.append(countText,notice,listing,conflictBox);
    const picker=node('section','','ima-directory-picker');picker.hidden=true;
    const libraries=node('select'),crumbs=node('nav','','ima-breadcrumbs'),folders=node('div','','ima-folder-list');
    crumbs.setAttribute('aria-label','文件夹路径');
    const pickerStatus=node('p','','ima-notice');pickerStatus.setAttribute('role','status');
    let entries=[],libraryRows=[],browseFolder='',loading=false,directoryRequest=0,directoryReady=false,verifiedLibrary='';
    const retryDirectory=button('重试读取目录',()=>void (libraryRows.length?loadDirectory(browseFolder):openPicker()),'study-text-button',{local:true});retryDirectory.hidden=true;
    const useFolder=button('使用此位置',chooseFolder,'ima-primary');
    const cancel=button('取消',()=>{if(mode==='link')modal.close();else{picker.hidden=true;renderTarget();}},'study-text-button',{local:true});
    const newName=node('input');newName.type='text';newName.maxLength=160;newName.placeholder='文件夹名称';newName.setAttribute('aria-label','新文件夹名称');
    const createError=node('p','','ima-notice');createError.setAttribute('role','status');
    const createRow=node('div','','ima-create');createRow.hidden=true;
    const create=button('新建文件夹',()=>{createRow.hidden=!createRow.hidden;if(!createRow.hidden)newName.focus();},'study-text-button',{local:true});
    const submitCreate=button('创建',async()=>{
      if(!newName.value.trim()){createError.textContent='请输入文件夹名称';newName.focus();return;}
      try{
        const result=await api('folder',{action:'create',libraryId:libraries.value,folderId:browseFolder,name:newName.value.trim(),requestId:crypto.randomUUID()});
        if(!modal.isConnected)return;
        newName.value='';createError.textContent='';createRow.hidden=true;await loadDirectory(result.objectId);
      }catch(error){createError.textContent=message(error);}
    });
    createRow.append(newName,submitCreate,createError);
    picker.append(field('知识库',libraries),crumbs,pickerStatus,retryDirectory,folders,create,createRow,useFolder,cancel);
    modal.append(picker);
    const retryPreview=button('重新准备清单',preparePreview);retryPreview.hidden=true;
    const start=button(mode==='move'?`确认移动 ${count} 份资料`:`确认保存 ${count} 份资料`,async()=>{
      let preview=start._preview;if(!preview)return;
      if(preview.conflicts?.length&&conflictChoices.some(row=>row.action.value!=='exists')){
        start.disabled=true;
        try{preview=await api('resolve-preview',{token:preview.token,decisions:conflictChoices.map(row=>({courseId:row.conflict.courseId,fileId:row.conflict.fileId,action:row.action.value,mediaId:null}))});}
        catch(error){start._preview=null;notice.textContent=message(error);retryPreview.hidden=false;return;}
      }
      state.pending={token:preview.token,requestId:crypto.randomUUID()};save();start.disabled=true;
      try{state.job=await api('start',state.pending);state.pending=null;save();modal.close();render();schedule();}
      catch(error){notice.textContent=message(error);await refresh();}
    },'ima-primary');
    start.disabled=true;start.hidden=mode==='link';modal.append(retryPreview,start);
    function renderTarget(){
      targetText.textContent=targetUnavailable?'原保存位置不可用，请更改位置':(selectedCourses.length>1?'全部保存到 · ':'保存位置 · ')+(locationText(target)||'尚未选择');
      start.disabled=!start._preview||!picker.hidden||loading;
      change.disabled=loading;course.disabled=loading;
    }
    function renderDirectory(){
      crumbs.replaceChildren();folders.replaceChildren();
      const library=libraryRows.find(row=>row.libraryId===libraries.value);
      const trail=model.directoryTrail(entries,libraries.value,browseFolder);
      const root=button((library?.name||'知识库')+' › 根目录',()=>navigate(libraries.value),'study-text-button',{local:true});root.disabled=loading;crumbs.append(root);
      for(const row of trail||[]){
        crumbs.append(node('span','›'));
        const step=button(row.name,()=>navigate(row.folderId),'study-text-button',{local:true});step.disabled=loading;crumbs.append(step);
      }
      const children=model.directoryChildren(entries,browseFolder);
      for(const row of children){
        const enter=button('▸ '+row.name,()=>navigate(row.folderId),'ima-folder-button',{local:true});
        enter.setAttribute('aria-label','进入文件夹 '+row.name);enter.disabled=loading;
        folders.append(enter);
      }
      if(!children.length&&directoryReady&&!loading)folders.append(node('p','空文件夹','ima-help'));
      picker.setAttribute('aria-busy',String(loading));
      useFolder.disabled=loading||!directoryReady||trail===null;create.disabled=useFolder.disabled;libraries.disabled=loading;
    }
    async function navigate(folderId){
      if(loading)return;
      if(model.directoryTrail(entries,libraries.value,folderId)===null)return;
      browseFolder=folderId;pickerStatus.textContent='';createRow.hidden=true;renderDirectory();
    }
    async function loadDirectory(preferred=''){
      const request=++directoryRequest,libraryId=libraries.value;
      loading=true;directoryReady=false;retryDirectory.hidden=true;pickerStatus.textContent='正在读取目录…';renderTarget();renderDirectory();
      try{
        const result=await api('tree',{libraryId});
        if(!modal.isConnected||request!==directoryRequest||libraries.value!==libraryId)return;
        if(result.complete===false)throw new Error('目录读取未完成，请重试');
        entries=model.dailyEntries(result.entries);directoryReady=true;verifiedLibrary=libraryId;
        const selected=preferred||libraryId;
        if(model.directoryTrail(entries,libraryId,selected)===null){
          browseFolder=libraryId;pickerStatus.textContent='原位置不可用，请重新选择。';
        }else{
          browseFolder=selected;pickerStatus.textContent='';
        }
      }catch(error){if(modal.isConnected&&request===directoryRequest){entries=[];browseFolder='';pickerStatus.textContent=message(error);retryDirectory.hidden=false;}}
      finally{if(modal.isConnected&&request===directoryRequest){loading=false;renderTarget();renderDirectory();}}
    }
    async function openPicker(){
      picker.hidden=false;renderTarget();
      if(!libraryRows.length){
        loading=true;renderTarget();useFolder.disabled=true;create.disabled=true;libraries.disabled=true;
        pickerStatus.textContent='正在读取知识库…';
        try{
          const result=await api('libraries',{});if(!modal.isConnected)return;libraryRows=result.libraries;
          libraries.replaceChildren();for(const row of libraryRows)option(libraries,row.name,row.libraryId);
        }catch(error){pickerStatus.textContent=message(error);retryDirectory.hidden=false;return;}
        finally{loading=false;renderTarget();}
      }
      if(!libraryRows.length){pickerStatus.textContent='当前没有可写知识库。';return;}
      libraries.value=libraryRows.some(row=>row.libraryId===target.libraryId)?target.libraryId:libraryRows[0].libraryId;
      await loadDirectory(target.libraryId===libraries.value?target.folderId:libraries.value);
    }
    async function chooseFolder(){
      const library=libraryRows.find(row=>row.libraryId===libraries.value);
      const trail=model.directoryTrail(entries,libraries.value,browseFolder);
      if(!library||trail===null||loading||!directoryReady)return;
      target={libraryId:library.libraryId,folderId:browseFolder,path:[library.name,...trail.map(row=>row.name)].join('/')};
      targetUnavailable=false;
      if(mode==='link'){
        const courseId=course.value;loading=true;renderDirectory();
        try{
          absorb(await api('link',{courseId,libraryId:target.libraryId,folderId:target.folderId}));
          pickerStatus.textContent='正在检查已有资料…';
          await checkCourse(courseId);
          modal.close();repaint();await refresh();return;
        }finally{loading=false;renderDirectory();}
      }
      picker.hidden=true;renderTarget();await preparePreview();
    }
    async function preparePreview(){
      start._preview=null;conflictChoices=[];conflictBox.replaceChildren();retryPreview.hidden=true;renderTarget();
      if(!target.libraryId||!target.folderId){await openPicker();return;}
      loading=true;renderTarget();notice.textContent='正在核验文件清单和保存位置…';
      try{
        if(verifiedLibrary!==target.libraryId){
          const result=await api('tree',{libraryId:target.libraryId});
          if(!modal.isConnected)return;
          if(result.complete===false)throw new Error('目录读取未完成，请重试');
          entries=model.dailyEntries(result.entries);verifiedLibrary=target.libraryId;
        }
        if(model.directoryTrail(entries,target.libraryId,target.folderId)===null){
          targetUnavailable=true;
          throw new Error('原保存位置已不存在或不可用，请更改位置');
        }
        const preview=mode==='move'?await api('move-preview',{libraryId:move.libraryId,mediaIds:move.mediaIds,targetLibraryId:target.libraryId,folderId:target.folderId})
          :await api('preview',{files:pairs,libraryId:target.libraryId,folderId:target.folderId,newVersion:mode==='version'});
        if(!modal.isConnected)return;
        target.path=preview.target.path;showFiles(preview.sources);start._preview=preview;
        for(const conflict of preview.conflicts||[]){
          const row=node('section','','ima-conflict-choice'),action=node('select'),detail=node('p','','ima-help');
          action.setAttribute('aria-label',conflict.fileName+' 同名文件处理');
          for(const [value,label] of [['exists','已保存，自动跳过'],['skip','本次跳过'],['copy','另存一份']])option(action,label,value);
          const choice={conflict,action};conflictChoices.push(choice);
          const update=()=>{detail.textContent=action.value==='copy'?'将新增 '+conflict.fileName+' → '+target.path:action.value==='exists'?'按完整同名规则已保存，保留现有位置。':'本次不处理这份资料。';renderTarget();};
          action.addEventListener('change',update);
          row.append(node('strong',conflict.fileName),field('同名处理',action),detail);conflictBox.append(row);update();
        }
        notice.textContent=mode==='move'?'确认后移动文件。':'确认后开始保存，内容相同且已经保存的文件会跳过。';
        if(preview.conflicts?.length)notice.textContent='有 '+preview.conflicts.length+' 份已保存资料，将自动跳过。可按需选择另存一份。';
        if(mode==='version')notice.textContent+='已有版本继续保留。';
      }catch(error){notice.textContent=message(error);retryPreview.hidden=false;}
      finally{loading=false;renderTarget();}
    }
    libraries.addEventListener('change',()=>void loadDirectory(libraries.value));
    course.addEventListener('change',()=>void act(async()=>{
      const current=state.links[course.value];target=current?{libraryId:current.libraryId,folderId:current.folderId,path:current.path||''}:{libraryId:'',folderId:'',path:''};
      renderTarget();await openPicker();
    }));
    renderTarget();
    if(mode==='link'||!target.libraryId)await openPicker();else await preparePreview();
  }
  async function browseLibrary(options={}) {
    const courseId=options.courseId||context.courseId;
    const modal=dialog('ima 目录与归档',options),libraries=node('select'),notice=node('p','','ima-notice');
    const crumbs=node('nav','','ima-breadcrumbs'),items=node('div','','ima-cloud-list'),selected=new Set();
    crumbs.setAttribute('aria-label','文件夹路径');notice.setAttribute('role','status');notice.setAttribute('aria-live','polite');
    let currentTree=[],folderId='',anchor=null,loading=false,ready=false,treeRequest=0,checkedAt='';
    const all=node('input');all.type='checkbox';const allLabel=node('label','全选当前目录文件 ');allLabel.prepend(all);
    const move=button('移动所选文件',()=>openTarget('move',{libraryId:libraries.value,mediaIds:[...selected],files:currentTree.filter(row=>selected.has(row.mediaId))}),'ima-primary');
    const count=node('span');
    const refreshButton=button('刷新目录',()=>void (libraries._rows?.length?load(folderId):loadLibraries()),'study-text-button ima-refresh-button',{local:true});
    const up=button('上一级',()=>{
      const row=currentTree.find(row=>row.kind==='folder'&&row.folderId===folderId);
      navigate(row?.parentFolderId||libraries.value);
    },'study-text-button',{local:true});
    const rename=button('重命名当前文件夹',()=>{
      const row=currentTree.find(row=>row.kind==='folder'&&row.folderId===folderId);
      if(!row||!ready)return;
      const form=dialog('重命名文件夹'),input=node('input');input.type='text';input.value=row.name;input.maxLength=160;
      form.append(field('新名称',input),button('保存名称',async()=>{
        if(!input.value.trim())throw new Error('请输入文件夹名称');
        await api('folder',{action:'rename',libraryId:libraries.value,folderId:row.folderId,name:input.value.trim(),requestId:crypto.randomUUID()});
        form.close();await load(row.folderId);if(courseId&&state.links[courseId])await checkCourse(courseId);
      },'ima-primary'));
    });
    const create=button('新建文件夹',()=>{
      const form=dialog('新建文件夹'),input=node('input');input.type='text';input.maxLength=160;
      form.append(field('文件夹名称',input),button('创建',async()=>{
        if(!input.value.trim())throw new Error('请输入文件夹名称');
        const result=await api('folder',{action:'create',libraryId:libraries.value,folderId,name:input.value.trim(),requestId:crypto.randomUUID()});
        form.close();await load(result.objectId);
      },'ima-primary'));
    });
    const actions=node('div','','ima-directory-actions');actions.append(up,refreshButton,create,rename);
    modal.append(field('知识库',libraries),crumbs,actions,notice,allLabel,count,move,items);
    function shown(){return currentTree.filter(row=>row.kind==='file'&&row.parentFolderId===folderId);}
    function navigate(id){
      if(!ready||loading||model.directoryTrail(currentTree,libraries.value,id)===null)return;
      folderId=id;anchor=null;notice.textContent='';renderTree();
    }
    function renderTree(){
      const files=shown(),libraryId=libraries.value;
      crumbs.replaceChildren();items.replaceChildren();
      const root=button((libraries._rows?.find(row=>row.libraryId===libraryId)?.name||'知识库')+' › 根目录',()=>navigate(libraryId),'study-text-button',{local:true});
      root.disabled=loading||!ready;crumbs.append(root);
      for(const row of model.directoryTrail(currentTree,libraryId,folderId)||[]){
        crumbs.append(node('span','›'));const step=button(row.name,()=>navigate(row.folderId),'study-text-button',{local:true});
        step.disabled=loading||!ready;crumbs.append(step);
      }
      for(const row of model.directoryChildren(currentTree,folderId)){
        const enter=button('▸ '+row.name,()=>navigate(row.folderId),'ima-folder-button',{local:true});
        enter.setAttribute('aria-label','进入文件夹 '+row.name);enter.disabled=loading||!ready;items.append(enter);
      }
      for(const row of files){
        const box=node('label','','ima-cloud-file'),input=node('input');input.type='checkbox';input.checked=selected.has(row.mediaId);input.disabled=loading||!ready||active();
        input.setAttribute('aria-label','选择 '+row.name);
        input.addEventListener('click',event=>{const next=model.toggleSelection(selected,files.map(row=>row.mediaId),anchor,row.mediaId,input.checked,event.shiftKey);selected.clear();for(const id of next)selected.add(id);anchor=row.mediaId;renderTree();});
        const type=StudyFileTypes.describe(row),icon=node('span',type.label,'study-file-type is-'+type.kind);
        box.append(input,icon,node('span',row.name));items.append(box);
      }
      if(ready&&!loading&&!files.length&&!model.directoryChildren(currentTree,folderId).length)items.append(node('p','空文件夹','ima-help'));
      const outside=[...selected].filter(id=>!files.some(row=>row.mediaId===id)).length;
      count.textContent=ready?`当前目录 ${files.length} 份 · 已选 ${selected.size} 份${outside?' · 目录外 '+outside+' 份':''}`:'';
      move.disabled=loading||!ready||!selected.size||active();all.disabled=loading||!ready||!files.length||active();
      all.checked=!!files.length&&files.every(row=>selected.has(row.mediaId));all.indeterminate=files.some(row=>selected.has(row.mediaId))&&!all.checked;
      up.disabled=loading||!ready||folderId===libraryId;rename.disabled=loading||!ready||folderId===libraryId;create.disabled=loading||!ready;
      items.setAttribute('aria-busy',String(loading));refreshButton.disabled=loading;
      refreshButton.setAttribute('aria-busy',String(loading));refreshButton.replaceChildren();
      if(loading){const spinner=node('span','','ima-spinner');spinner.setAttribute('aria-hidden','true');refreshButton.append(spinner);}
      refreshButton.append(node('span',loading?'读取中…':'刷新目录'));
    }
    async function load(preferred=''){
      const request=++treeRequest,libraryId=libraries.value;
      if(!libraryId)return;
      loading=true;ready=false;notice.textContent='正在读取目录…';renderTree();
      try{
        const result=await api('tree',{libraryId});
        if(!modal.isConnected||request!==treeRequest||libraries.value!==libraryId)return;
        if(result.complete===false)throw new Error('目录读取未完成，请重试');
        currentTree=model.dailyEntries(result.entries);
        for(const id of selected)if(!currentTree.some(row=>row.kind==='file'&&row.mediaId===id))selected.delete(id);
        const wanted=preferred||libraryId;
        folderId=model.directoryTrail(currentTree,libraryId,wanted)===null?libraryId:wanted;
        checkedAt=result.checkedAt;
        notice.textContent=folderId!==wanted?'原位置不可用，已返回根目录':'已刷新';ready=true;
      }catch(error){if(modal.isConnected&&request===treeRequest)notice.textContent=message(error)+(checkedAt?' · 上次读取 '+timeText(checkedAt):'')+' · 可重试刷新目录';}
      finally{if(modal.isConnected&&request===treeRequest){loading=false;renderTree();}}
    }
    const reload=()=>load(folderId);
    cloudRefreshers.add(reload);modal.addEventListener('close',()=>{treeRequest++;cloudRefreshers.delete(reload);},{once:true});
    all.addEventListener('change',()=>{for(const row of shown()){if(all.checked)selected.add(row.mediaId);else selected.delete(row.mediaId);}renderTree();});
    libraries.addEventListener('change',()=>{selected.clear();currentTree=[];checkedAt='';folderId=libraries.value;anchor=null;void load();});
    async function loadLibraries(){
      loading=true;renderTree();notice.textContent='正在读取知识库…';
      try{
        const result=await api('libraries',{});if(!modal.isConnected)return;
        libraries._rows=result.libraries;libraries.replaceChildren();for(const row of result.libraries)option(libraries,row.name,row.libraryId);
        const linked=state.links[courseId]?.libraryId;
        if(result.libraries.some(row=>row.libraryId===linked))libraries.value=linked;
        if(libraries.value){folderId=libraries.value;await load();}else notice.textContent='当前没有可写知识库';
      }catch(error){if(modal.isConnected)notice.textContent=message(error)+' · 可重试刷新目录';}
      finally{if(modal.isConnected&&treeRequest===0){loading=false;renderTree();}}
    }
    await loadLibraries();
  }
  document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible'&&refs?.panel.isConnected)void refresh().catch(showError);});
  window.StudyIma={mount,setContext,filter,setRows,decorate,mode:currentMode};
})();
