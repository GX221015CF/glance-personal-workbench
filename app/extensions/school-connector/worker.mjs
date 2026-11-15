import {CONFIG,TARGETS,ACTIVE,Fault,WORDS,safeCode,ownSender,selectCookies,recover,inspectSchool,targetsForPage,taskTargets,unfinishedTask} from './core.mjs';
import {NativeLink} from './native.mjs';
import {UnifiedConnection} from './unified.mjs';
import {browserStep} from './limits.mjs';
import {PAGE_PROTOCOL, pageRequest, workbenchSender, publicState} from './workbench.mjs';
const api=chrome;
const native=new NativeLink(api);
let state={version:1,task:null,lastStatus:null};
let running=false;let mutation=Promise.resolve();
const ready=(async()=>{
  await api.storage.session.setAccessLevel({accessLevel:'TRUSTED_CONTEXTS'});
  state=recover((await api.storage.session.get('schoolConnector')).schoolConnector);
  await save();
})();
const unified=new UnifiedConnection({api,native,state:()=>state,save:()=>save(),inspect:(...args)=>inspect(...args),getSession:(...args)=>getSession(...args)});
async function save(){state.revision=(state.revision||0)+1;await api.storage.session.set({schoolConnector:state});}
function rowFor(target){return state.task?.systems[target];}
function serial(fn){const p=mutation.then(fn);mutation=p.catch(()=>{});return p;}
function current(taskId){if(state.task?.id!==taskId||state.task.cancelled)throw new Fault('CANCELLED');}
async function change(target,fields){Object.assign(rowFor(target),fields);await save();}
async function lookupTab(target,step,existing=null) {
  const tabs=await step(()=>api.tabs.query({url:'https://'+CONFIG[target].host+'/*'}));
  const candidates=tabs.filter(t=>{
    if(t.incognito)return false;
    const url=new URL(t.url);
    if(target==='academic')return url.pathname==='/tch/studyCheck/courseFinishInfo/planCompletion.jsp';
    return target==='canvas'?url.pathname==='/':/^\/eams\/courseTableForStd(?:!courseTable)?\.action$/.test(url.pathname);
  });
  candidates.sort((a,b)=>Number(!!b.active)-Number(!!a.active)||(b.lastAccessed||0)-(a.lastAccessed||0)||b.id-a.id);
  if(target==='sufe'){
    // EAMS can leave multiple result tabs at the same URL, with different POST
    // contexts. Prefer the actual rendered timetable, not the first URL match.
    // Also re-evaluate on Continue after the user opened a fresh portal tab.
    for(const candidate of candidates){
      try{
        if((await inspect(target,candidate.id,false,step)).kind==='ready')return candidate;
      }catch(error){
        if(!['TAB_CLOSED','TARGET_NOT_READY','PAGE_CHECK_TIMEOUT'].includes(safeCode(error)))throw error;
      }
    }
  }
  if(existing)return existing;
  if(candidates.length)return candidates[0];
  return step(()=>api.tabs.create({url:CONFIG[target].entry,active:true}));
}
async function inspect(target,tabId,click,step){
  const tab=await step(()=>api.tabs.get(tabId).catch(()=>{throw new Fault('TAB_CLOSED');}));
  const url=new URL(tab.url||'https://invalid/');
  const host=url.hostname;
  const allowed=[CONFIG[target].host,'login.sufe.edu.cn',...(target==='canvas'?['jiaoxue.shufe.edu.cn','canvas.sufe.edu.cn']:[])];
  if(!allowed.includes(host)) return {kind:'human'};
  if(tab.status!=='complete') return {kind:host==='login.sufe.edu.cn'?'human':'transition'};
  if(target==='academic'&&host===CONFIG.academic.host){
    // This is a handoff candidate, NOT evidence of authentication or sync.
    // The school's synchronous scripts can stall executeScript and a redundant
    // identity fetch. Only Python's five fixed queries + atomic save may report
    // success. An expired session comes back as LOGIN_REQUIRED, without replay.
    const entry=new URL(CONFIG.academic.entry);
    return {kind:url.origin===entry.origin&&url.pathname===entry.pathname?'ready':'unrecognized'};
  }
  const result=await step(()=>api.scripting.executeScript({target:{tabId},func:inspectSchool,args:[target,click]}).catch(()=>{throw new Fault('TARGET_NOT_READY');}));
  return result[0]?.result||{kind:'transition'};
}
function nextPageEvent(tabId,signal){return new Promise((resolve,reject)=>{
  const finish=(error)=>{api.tabs.onUpdated.removeListener(updated);api.tabs.onRemoved.removeListener(removed);signal.removeEventListener('abort',aborted);clearTimeout(timer);error?reject(error):resolve();};
  const updated=(id,info)=>{if(id===tabId&&(info.status==='complete'||info.url))finish();};
  const removed=id=>{if(id===tabId)finish(new Fault('TAB_CLOSED'));};
  const aborted=()=>finish(new Fault('CANCELLED'));
  const timer=setTimeout(()=>finish(),5000); // Finite fallback for a page changing without navigation.
  api.tabs.onUpdated.addListener(updated);api.tabs.onRemoved.addListener(removed);signal.addEventListener('abort',aborted,{once:true});
  if(signal.aborted)aborted();
});}
let stageAbort=null,stageTarget=null;
async function getSession(target,tabId,step){
  const stores=await step(()=>api.cookies.getAllCookieStores());
  const store=stores.find(s=>s.tabIds.includes(tabId));
  if(!store)throw new Fault('NO_SESSION');
  let cookies=await step(()=>api.cookies.getAll(target!=='canvas'
    ?{url:CONFIG[target].entry,storeId:store.id}:{domain:CONFIG[target].host,storeId:store.id}));
  try{
    if(target!=='canvas'){
      // A domain filter omits parents. The fixed URL lookup includes only
      // applicable cookies; get(name,url) selects the browser's preferred path.
      let preferred=typeof api.cookies.get==='function'
        ?await step(()=>api.cookies.get({url:CONFIG[target].entry,name:'JSESSIONID',storeId:store.id})):null;
      if(preferred)cookies=[...cookies.filter(c=>c.name!=='JSESSIONID'),preferred];
      let partitioned=!!preferred?.partitionKey;
      if(!cookies.some(c=>c.name==='JSESSIONID')&&typeof api.cookies.getPartitionKey==='function'&&typeof api.cookies.get==='function'){
        const key=await step(()=>api.cookies.getPartitionKey({tabId}));
        if(key&&['https://sufe.edu.cn','https://eams.sufe.edu.cn'].includes(key.topLevelSite)){
          const partitionCookie=await step(()=>api.cookies.get({url:CONFIG[target].entry,name:'JSESSIONID',storeId:store.id,partitionKey:key}));
          partitioned=!!partitionCookie?.partitionKey;
        }
      }
      let normalized=null;
      if(!partitioned){
        try{normalized=selectCookies(target,cookies,store.id);}
        catch(error){if(safeCode(error)!=='NO_SESSION')throw error;}
      }
      const sessionCheck={readCount:cookies.length,hasLoginSession:!!normalized?.cookies.some(c=>c.name==='JSESSIONID'),
        parentSessionNarrowed:cookies.some(c=>c.name==='JSESSIONID'&&['sufe.edu.cn','.sufe.edu.cn'].includes(c.domain)),
        partitionedSessionDetected:partitioned};
      await change(target,{sessionCheck});
      if(partitioned)throw new Fault('PARTITION_UNSUPPORTED');
      if(!sessionCheck.hasLoginSession)throw new Fault('SCHOOL_SESSION_MISSING');
      preferred=null;
      return normalized;
    }
    return selectCookies(target,cookies,store.id);
  }finally{cookies=null;}
}
async function waitUser(target,code='LOGIN_REQUIRED'){
  await change(target,{phase:'waiting',code});
  await api.alarms.create('school-connector-expiry',{when:state.task.expiresAt});
}
async function processTarget(target,taskId){
  const row=rowFor(target);stageAbort=new AbortController();stageTarget=target;
  const deadline=Date.now()+90000;
  const signal=stageAbort.signal;
  const step=action=>{
    const remaining=deadline-Date.now();
    if(remaining<=0)return Promise.reject(new Fault('PAGE_CHECK_TIMEOUT'));
    return browserStep(action,{signal,timeoutMs:Math.min(10000,remaining)});
  };
  let tab;
  try{
    current(taskId);await change(target,{phase:'opening',code:null});
    tab=row.tabId?await step(()=>api.tabs.get(row.tabId).catch(()=>null)):null;
    if(target==='sufe'||!tab)tab=await lookupTab(target,step,tab);
    if(row.tabId!==tab.id)await change(target,{tabId:tab.id});
    while(Date.now()<deadline){
      current(taskId);await change(target,{phase:'checking'});
      const outcome=await inspect(target,tab.id,false,step);
      current(taskId);
      if(outcome.kind==='ready'){
        let session=await getSession(target,tab.id,step);
        current(taskId);await change(target,{phase:'connecting',code:null,attemptAt:new Date().toISOString()});
        let result;
        try{result=await native.call(row.verify?'verify':'connect',target,session);}finally{session=null;}
        if(state.task?.id!==taskId)return;
        state.lastStatus={...state.lastStatus,[target]:result[target]};
        await change(target,{phase:'succeeded',code:null,result:result[target]});return;
      }
      if(outcome.kind==='sso_entry'){
        if(row.ssoClicked){await waitUser(target);return;}
        // Persist the once-per-task guard BEFORE dispatching any click.
        await change(target,{ssoClicked:true,phase:'sso_pending'});
        await inspect(target,tab.id,true,step);
      }else if(outcome.kind==='canvas_entry'){
        if(row.canvasOpened){await waitUser(target);return;}
        await change(target,{canvasOpened:true});
        await step(()=>api.tabs.update(tab.id,{url:'https://canvas.sufe.edu.cn/'}));
      }else if(outcome.kind==='unauthenticated'&&target==='canvas'&&!row.teachingOpened){
        await change(target,{teachingOpened:true});
        await step(()=>api.tabs.update(tab.id,{url:'https://jiaoxue.shufe.edu.cn/'}));
      }else if(['human','unauthenticated'].includes(outcome.kind)){
        await waitUser(target,outcome.verification?'WAIT_VERIFICATION':'WAIT_LOGIN');return;
      }else if(outcome.kind==='network'){
        await waitUser(target,'NETWORK_ERROR');return;
      }else if(outcome.kind==='unrecognized'){
        await waitUser(target,'PAGE_UNRECOGNIZED');return;
      }
      await nextPageEvent(tab.id,stageAbort.signal);
    }
    await waitUser(target,'TIMEOUT');
  }catch(error){
    if(state.task?.id!==taskId)return;
    const code=safeCode(error);
    if(row.code==='TAB_CLOSED')return;
    if(state.task.cancelled){if(row.phase!=='succeeded')await change(target,{phase:code==='CONNECTION_OUTCOME_UNKNOWN'?'unknown':'cancelled',code});}
    else if(['LOGIN_REQUIRED','PAGE_CHECK_TIMEOUT','SCHOOL_SESSION_MISSING'].includes(code)){await waitUser(target,code);}
    else await change(target,{phase:code==='CONNECTION_OUTCOME_UNKNOWN'?'unknown':'failed',code});
  }finally{stageAbort=null;stageTarget=null;}
}
async function execute(targets,taskId){
  running=true;
  try{
    for(const target of targets){
      if(state.task?.id!==taskId||state.task.cancelled)break;
      await processTarget(target,taskId);
    }
  }finally{running=false;native.close();await save();}
}
async function start(target,verify=false){
  if(unified.running)throw new Fault('TARGET_NOT_READY');
  if(running||Object.values(state.task?.systems||{}).some(r=>['waiting','unknown'].includes(r.phase)))throw new Fault('TARGET_NOT_READY');
  // Preflight the Host before touching school pages or reading any cookies.
  state.lastStatus=await native.call('status','all');
  const targets=target==='all'?TARGETS:[target];
  const task={id:crypto.randomUUID(),createdAt:Date.now(),expiresAt:Date.now()+600000,cancelled:false,systems:{}};
  for(const t of targets)task.systems[t]={phase:'queued',code:null,tabId:null,ssoClicked:false,teachingOpened:false,canvasOpened:false,result:null,verify:verify&&t==='academic'};
  state.task=task;await save();void execute(targets,task.id);
}
async function resume(selected='all'){
  if(running||!state.task||state.task.cancelled)throw new Fault('TARGET_NOT_READY');
  const targets=Object.entries(state.task.systems).filter(([target,r])=>r.phase==='waiting'&&(selected==='all'||selected===target)).map(([t])=>t);
  if(!targets.length)throw new Fault('TARGET_NOT_READY');
  state.task.expiresAt=Date.now()+600000;await save();void execute(targets,state.task.id);
}
async function cancel(){
  if(state.task?.unified)return unified.cancel();
  if(!state.task)return;
  state.task.cancelled=true;stageAbort?.abort();
  for(const row of Object.values(state.task.systems))if(ACTIVE.has(row.phase)){
    row.phase=row.phase==='connecting'?'unknown':'cancelled';row.code=row.phase==='unknown'?'CONNECTION_OUTCOME_UNKNOWN':'CANCELLED';
  }
  await save();
}
api.runtime.onMessage.addListener((message,sender,sendResponse)=>{
  if(message?.desktopRelay){
    if(!workbenchSender(sender,api.runtime))return false;
    const relay=message.desktopRelay;
    if(!/^[A-Za-z0-9_-]{43}$/.test(relay.ticket)||new URL(sender.url).hash!=='#desktop-connect='+relay.ticket
      ||!['attach','view','continueSchool','retrySchool','cancel','status','openPortal','openSchool'].includes(relay.command))return false;
    serial(async()=>{
      await ready;
      const pagePath=new URL(sender.url).pathname;
      if(relay.command==='attach'&&state.task?.desktopRelayTicket!==relay.ticket){
        const confirmed=relay.confirmed;
        if(!confirmed||typeof confirmed!=='object'||Object.keys(confirmed).some(t=>!targetsForPage(pagePath).includes(t)))throw new Fault('INVALID_MESSAGE');
        for(const result of Object.values(confirmed))if(result?.clientVersion!==2||!result.state?.available||result.state.stale||result.state.needsLogin||result.state.errorCode)throw new Fault('INVALID_MESSAGE');
        if(running)throw new Fault('TARGET_NOT_READY');
        await unified.start(false,pagePath,confirmed);
        state.task.desktopRelayTicket=relay.ticket;await save();
      }
      if(state.task?.desktopRelayTicket!==relay.ticket)throw new Fault('STALE_TASK');
      if(relay.command==='continueSchool')await unified.resume();
      else if(relay.command==='retrySchool')await unified.retry();
      else if(relay.command==='cancel')await unified.cancel();
      else if(relay.command==='openPortal')await unified.openPortal();
      else if(relay.command==='openSchool'){
        const row=state.task.systems[relay.target];
        if(!row?.tabId||!taskTargets(state.task).includes(relay.target))throw new Fault('TARGET_NOT_READY');
        const tab=await browserStep(()=>api.tabs.get(row.tabId));
        const url=new URL(tab.url||'https://invalid/');
        const hosts=[CONFIG[relay.target].host,'login.sufe.edu.cn',...(relay.target==='canvas'?['canvas.sufe.edu.cn','jiaoxue.shufe.edu.cn']:[])];
        if(url.protocol!=='https:'||!hosts.includes(url.hostname))throw new Fault('TARGET_NOT_READY');
        await api.tabs.update(row.tabId,{active:true});
        if(Number.isInteger(tab.windowId))await api.windows.update(tab.windowId,{focused:true});
      }
      else if(relay.command==='status'){
        if(unified.running)throw new Fault('TARGET_NOT_READY');
        state.lastStatus={...state.lastStatus,...await unified.readStatus(taskTargets(state.task))};await unified.reconcile(state.lastStatus);native.close();
      }
      return {ok:true,state:publicState(state,running||unified.running,pagePath)};
    }).then(sendResponse).catch(error=>sendResponse({ok:false,code:safeCode(error)}));
    return true;
  }
  if(message?.workbench){
    if(!workbenchSender(sender,api.runtime))return false;
    let request;
    try{request=pageRequest(message.workbench);}catch{return false;}
    serial(()=>handleWorkbench(request,sender)).then(sendResponse).catch(error=>sendResponse({
      ok:false,code:safeCode(error),protocol:PAGE_PROTOCOL,extensionVersion:api.runtime.getManifest().version,
      state:publicState(state,running||unified.running,new URL(sender.url).pathname)
    }));
    return true;
  }
  if(!ownSender(sender,api.runtime))return false;
  const command=message?.command;
  if(!['view','start','verifyAcademic','continue','cancel','status','openSchool','openPanel','dismiss','connectSchool','continueSchool','retrySchool','openPortal','verifySchoolFlow'].includes(command))return false;
  serial(async()=>{
    await ready;
    if(command==='connectSchool'||command==='verifySchoolFlow'){if(running)throw new Fault('TARGET_NOT_READY');await unified.start(command==='verifySchoolFlow');}
    else if(command==='continueSchool')await unified.resume();
    else if(command==='retrySchool')await unified.retry();
    else if(command==='openPortal')await unified.openPortal();
    else if(command==='start'){
      if(!['all',...TARGETS].includes(message.target))throw new Fault('INVALID_MESSAGE');
      await start(message.target);
    }else if(command==='verifyAcademic')await start('academic',true);
    else if(command==='continue'){
      if(message.target!==undefined&&!TARGETS.includes(message.target))throw new Fault('INVALID_MESSAGE');
      await resume(message.target||'all');
    }
    else if(command==='cancel')await cancel();
    else if(command==='dismiss'){
      if(running||Object.values(state.task?.systems||{}).some(r=>r.phase==='unknown')&&!message.confirm)throw new Fault('TARGET_NOT_READY');
      state.task=null;await save();
    }else if(command==='status'){
      if(running||unified.running)throw new Fault('TARGET_NOT_READY');
      const targets=unfinishedTask(state.task)?taskTargets(state.task):targetsForPage('/study');
      state.lastStatus={...state.lastStatus,...await unified.readStatus(targets)};await unified.reconcile(state.lastStatus);await save();native.close();
    }else if(command==='openSchool'){
      const row=rowFor(message.target);if(row?.tabId)await browserStep(()=>api.tabs.update(row.tabId,{active:true}));
    }else if(command==='openPanel')await browserStep(()=>api.tabs.create({url:api.runtime.getURL('panel.html')}));
    return {ok:true,state};
  }).then(sendResponse).catch(error=>sendResponse({ok:false,code:safeCode(error),state}));
  return true;
});

async function handleWorkbench(request,sender){
  await ready;
  const pagePath=new URL(sender.url).pathname;
  // Stored IDs survive worker suspension. Never retain raw requests or sessions.
  // The document binding distinguishes a refresh from a duplicate delivery.
  const stored=await api.storage.session.get('schoolWorkbenchRequests');
  const ledger=Array.isArray(stored.schoolWorkbenchRequests)?stored.schoolWorkbenchRequests:[];
  const key=sender.documentId+'|'+request.clientId+'|'+request.id;
  const signature=JSON.stringify([request.command,request.target,request.taskId,request.confirm]);
  const previous=ledger.find(item=>item.key===key);
  const reply=()=>({ok:true,protocol:PAGE_PROTOCOL,extensionVersion:api.runtime.getManifest().version,state:publicState(state,running||unified.running,pagePath)});
  if(previous){
    if(previous.signature!==signature)throw new Fault('INVALID_MESSAGE');
    return previous.finished?previous.reply:{...reply(),ok:false,code:'REQUEST_OUTCOME_UNKNOWN'};
  }
  if(['hello','view'].includes(request.command))return reply();
  if(request.taskId!==(state.task?.id||null))throw new Fault('STALE_TASK');
  const record={key,signature,finished:false,reply:null};
  ledger.push(record);
  await api.storage.session.set({schoolWorkbenchRequests:ledger.slice(-256)});
  try{
    if(request.command==='connectSchool'||request.command==='verifySchoolFlow'){if(running)throw new Fault('TARGET_NOT_READY');await unified.start(request.command==='verifySchoolFlow',pagePath);}
    else if(request.command==='continueSchool')await unified.resume();
    else if(request.command==='retrySchool')await unified.retry();
    else if(request.command==='openPortal')await unified.openPortal();
    else if(request.command==='start')await start(request.target);
    else if(request.command==='continue')await resume(request.target);
    else if(request.command==='cancel')await cancel();
    else if(request.command==='status'){
      if(running||unified.running)throw new Fault('TARGET_NOT_READY');
      const targets=unfinishedTask(state.task)?taskTargets(state.task):targetsForPage(pagePath);
      state.lastStatus={...state.lastStatus,...await unified.readStatus(targets)};await unified.reconcile(state.lastStatus);await save();native.close();
    }else if(request.command==='openSchool'){
      const row=rowFor(request.target);
      if(!row?.tabId)throw new Fault('TAB_CLOSED');
      const tab=await browserStep(()=>api.tabs.get(row.tabId));
      const allowedHosts=[CONFIG[request.target].host,'login.sufe.edu.cn',
        ...(request.target==='canvas'?['canvas.sufe.edu.cn','jiaoxue.shufe.edu.cn']:[])];
      const url=new URL(tab.url||'https://invalid/');
      if(url.protocol!=='https:'||!allowedHosts.includes(url.hostname))throw new Fault('TARGET_NOT_READY');
      await browserStep(()=>api.tabs.update(row.tabId,{active:true}));
      if(Number.isInteger(tab.windowId))await api.windows.update(tab.windowId,{focused:true});
    }else if(request.command==='dismiss'){
      if(running||!request.confirm||Object.values(state.task?.systems||{}).some(row=>ACTIVE.has(row.phase)))throw new Fault('TARGET_NOT_READY');
      state.task=null;await save();
    }
    record.reply=reply();
  }catch(error){record.reply={...reply(),ok:false,code:safeCode(error)};}
  record.finished=true;
  await api.storage.session.set({schoolWorkbenchRequests:ledger.slice(-256)});
  return record.reply;
}
api.tabs.onRemoved.addListener(tabId=>void serial(async()=>{
  await ready;
  if(!state.task)return;
  for(const [target,row] of Object.entries(state.task.systems))if(row.tabId===tabId&&!row.cleaning&&ACTIVE.has(row.phase)){
    row.phase=row.phase==='connecting'?'unknown':'failed';row.code=row.phase==='unknown'?'CONNECTION_OUTCOME_UNKNOWN':'TAB_CLOSED';
    if(stageTarget===target)stageAbort?.abort();
  }
  await save();
}));
api.alarms.onAlarm.addListener(alarm=>{
  if(alarm.name==='school-update-cooldown')void serial(async()=>{
    await ready;
    if(!state.task?.cooldownScheduled||state.task.cancelled)return;
    if(Date.now()>=state.task.expiresAt){
      state.task.cooldownScheduled=false;
      for(const row of Object.values(state.task.systems))if(row.phase==='cooldown'){row.phase='failed';row.code='TIMEOUT';}
      await save();return;
    }
    if(unified.running){await api.alarms.create('school-update-cooldown',{when:Date.now()+1000});return;}
    await unified.retry(true);
  });
  if(alarm.name==='school-connector-expiry')void serial(async()=>{
  await ready;if(state.task&&Date.now()>=state.task.expiresAt)for(const row of Object.values(state.task.systems))if(row.phase==='waiting'){row.phase='failed';row.code='TIMEOUT';}
  await save();
});});
