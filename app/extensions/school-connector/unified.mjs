import {CONFIG,targetsForPage,Fault,safeCode} from './core.mjs';
import {browserStep} from './limits.mjs';

export const PORTAL='https://portal.sufe.edu.cn/main.html#/Index';
export const AUTH_ENTRIES={sufe:'https://eams.sufe.edu.cn/eams/sso/login',academic:'https://eams.sufe.edu.cn/tch/sso/login',canvas:'https://jiaoxue.shufe.edu.cn/'};
// Paths above are school-observed redirects, not constructed tickets/services.
export class UnifiedConnection {
  constructor({api,native,state,save,inspect,getSession}){Object.assign(this,{api,native,state,save,inspect,getSession});this.running=false;}
  get task(){return this.state().task;}
  async change(target,fields){Object.assign(this.task.systems[target],fields);await this.save();}
  assert(id){if(this.task?.id!==id||this.task.cancelled)throw new Fault('CANCELLED');if(Date.now()>=this.task.expiresAt)throw new Fault('TIMEOUT');}
  step(fn){return browserStep(fn,{signal:this.controller?.signal,timeoutMs:10000});}
  async start(forceOfficial=false,sourcePage='/study',confirmed=null){
    const s=this.state();
    if(this.running||s.task?.cooldownScheduled||Object.values(s.task?.systems||{}).some(r=>['waiting','unknown','opening','checking','connecting','queued','sso_pending'].includes(r.phase)))throw new Fault('TARGET_NOT_READY');
    const targets=targetsForPage(sourcePage);
    s.task={id:crypto.randomUUID(),kind:forceOfficial?'connection_verify':'update',unified:true,sourcePage:sourcePage.replace(/\/$/,''),targets,
      createdAt:Date.now(),expiresAt:Date.now()+600000,cancelled:false,round:0,portalWaiting:false,portalTabId:null,systems:{}};
    for(const target of targets)s.task.systems[target]={phase:confirmed?.[target]?'succeeded':'queued',code:null,result:confirmed?.[target]||null,tabId:null,createdByTask:false};
    this.task.forceOfficial=forceOfficial===true;
    const remaining=targets.filter(target=>!confirmed?.[target]);
    await this.save();void this.run(remaining,!forceOfficial&&!confirmed);
  }
  async readStatus(targets){
    const status={};
    for(const target of targets){
      const result=await this.native.call('status',target);
      if(result[target]?.clientVersion!==2)throw new Fault('HOST_UPDATE_REQUIRED');
      status[target]=result[target];
    }
    return status;
  }
  async completed(target,result,path){
    if(result.state?.needsLogin)throw new Fault('LOGIN_REQUIRED');
    if(result.state?.errorCode)throw new Fault(result.state.errorCode);
    if(!result.state?.available||result.state.stale||result.state.refreshing)throw new Fault('BRIDGE_RESPONSE_INVALID');
    const overview=result.state.creditOverview;
    const partial=target==='academic'&&overview&&(!overview.available||overview.stale||!!overview.errorCode);
    await this.change(target,{phase:partial?'partial':'succeeded',code:partial?'CREDIT_OVERVIEW_FAILED':null,
      result,needsPortal:false,connectionPath:path,retryAt:null});
  }
  async run(targets,checkSaved=false){
    this.running=true;this.controller=new AbortController();const id=this.task.id;
    try{
      try{
        const status=await this.readStatus(targets);
        this.state().lastStatus={...this.state().lastStatus,...status};
      }
      catch(error){
        if(!this.task.cancelled)for(const target of targets)await this.change(target,{phase:'failed',code:safeCode(error)});
        return;
      }
      const restore=[];
      for(const target of targets){
        this.assert(id);
        if(!checkSaved){restore.push(target);continue;}
        await this.change(target,{phase:'connecting',code:'CHECKING_SAVED',attemptAt:new Date().toISOString(),
          beforeUpdatedAt:this.state().lastStatus?.[target]?.state.updatedAt||null,attemptKind:'check'});
        try{
          const checked=(await this.native.call('check',target))[target];
          this.state().lastStatus[target]=checked;
          if(checked.checkVersion!==2||!checked.refresh)throw new Fault('BRIDGE_RESPONSE_INVALID');
          if(this.task.cancelled){
            if(checked.validated)await this.completed(target,checked,'saved_session');
            else await this.change(target,{phase:'cancelled',code:'CANCELLED',result:checked});
            break;
          }
          if(checked.validated){await this.completed(target,checked,'saved_session');}
          else if(checked.state?.refreshing)await this.change(target,{phase:'unknown',code:'CONNECTION_OUTCOME_UNKNOWN',result:checked});
          else if(checked.state?.errorCode==='PLAN_CONFLICT')await this.change(target,{phase:'failed',code:'PLAN_CONFLICT',result:checked});
          else if(checked.state?.needsLogin||checked.state?.errorCode==='LOGIN_REQUIRED'||!checked.state?.available&&!checked.state?.errorCode){await this.change(target,{phase:'queued',code:null,result:checked});restore.push(target);}
          else if(checked.refresh.reason==='cooldown')await this.change(target,{phase:'cooldown',code:'COOLDOWN',result:checked,retryAt:Date.now()+checked.refresh.retryAfterSeconds*1000+1000});
          else await this.change(target,{phase:'failed',code:checked.state?.errorCode||'UPSTREAM_ERROR',result:checked});
        }catch(error){
          const raw=safeCode(error),code=raw==='BRIDGE_RESPONSE_INVALID'?'CONNECTION_OUTCOME_UNKNOWN':raw;
          await this.change(target,{phase:code==='CONNECTION_OUTCOME_UNKNOWN'?'unknown':'failed',code});
        }
      }
      for(const target of restore){this.assert(id);await this.connectTarget(target,id);}
      this.assert(id);
      const needsPortal=Object.values(this.task.systems).some(r=>r.phase==='waiting'&&r.needsPortal);
      if(needsPortal){this.task.portalWaiting=true;await this.openPortal();}
      else if(Object.values(this.task.systems).every(r=>r.phase==='succeeded')){
        this.task.completedAt=Object.values(this.task.systems).map(r=>r.result.state.updatedAt).sort((a,b)=>Date.parse(a)-Date.parse(b)).at(-1);
      }
      await this.save();
    }catch(error){
      if(safeCode(error)!=='CANCELLED')for(const row of Object.values(this.task?.systems||{}))if(row.phase==='queued'){row.phase='failed';row.code=safeCode(error);}
    }finally{this.running=false;this.native.close();await this.scheduleCooldown();await this.save();}
  }
  async scheduleCooldown(){
    this.task.cooldownScheduled=false;
    await this.api.alarms.clear?.('school-update-cooldown');
    if(this.task.cancelled||Date.now()>=this.task.expiresAt)return;
    const eligible=Object.values(this.task.systems).filter(row=>row.phase==='cooldown'&&(row.autoRetryCount||0)<1
      &&row.retryAt<this.task.expiresAt&&row.retryAt-Date.now()<=120000);
    if(!eligible.length)return;
    this.task.cooldownScheduled=true;
    await this.api.alarms.create('school-update-cooldown',{when:Math.max(Date.now()+200,Math.min(...eligible.map(row=>row.retryAt)))});
  }
  async openPortal(){
    let tab=this.task.portalTabId?await this.api.tabs.get(this.task.portalTabId).catch(()=>null):null;
    if(!tab){tab=await this.api.tabs.create({url:PORTAL,active:true});this.task.portalTabId=tab.id;}
    else await this.api.tabs.update(tab.id,{active:true});
    if(Number.isInteger(tab.windowId)&&this.api.windows?.update)await this.api.windows.update(tab.windowId,{focused:true});
    await this.save();
  }
  async pause(target,code,round){
    await this.change(target,{phase:'waiting',code,needsPortal:round===0});
    await this.api.alarms.create('school-connector-expiry',{when:this.task.expiresAt});
    if(round>0){const tab=await this.api.tabs.update(this.task.systems[target].tabId,{active:true}).catch(()=>null);if(tab&&Number.isInteger(tab.windowId)&&this.api.windows?.update)await this.api.windows.update(tab.windowId,{focused:true});}
  }
  async waitPage(tabId){
    await browserStep(()=>new Promise(resolve=>{
      const done=()=>{clearTimeout(timer);this.api.tabs.onUpdated.removeListener(updated);this.controller?.signal.removeEventListener('abort',done);resolve();};
      const updated=(id,info)=>{if(id===tabId&&(info.status==='complete'||info.url))done();};
      const timer=setTimeout(done,1000);this.api.tabs.onUpdated.addListener(updated);this.controller.signal.addEventListener('abort',done,{once:true});
    }),{signal:this.controller.signal,timeoutMs:2000});
  }
  async connectTarget(target,id){
    const row=this.task.systems[target],round=this.task.round||0;
    try{
      await this.change(target,{phase:'opening',code:null,needsPortal:false});
      let tab=row.tabId?await this.step(()=>this.api.tabs.get(row.tabId).catch(()=>null)):null;
      if(!tab){
        const tabs=await this.step(()=>this.api.tabs.query({url:CONFIG[target].entry}));
        tab=tabs.find(t=>!t.incognito&&t.url===CONFIG[target].entry);
        if(!tab){tab=await this.step(()=>this.api.tabs.create({url:AUTH_ENTRIES[target],active:false}));row.createdByTask=true;}
        row.tabId=tab.id;await this.save();
      }
      // At most one official entry navigation per authentication round.
      if(row.authRound!==round){row.authRound=round;await this.save();await this.step(()=>this.api.tabs.update(tab.id,{url:AUTH_ENTRIES[target]}));}
      const deadline=Date.now()+60000;
      while(Date.now()<deadline){
        this.assert(id);await this.change(target,{phase:'sso_pending'});
        tab=await this.step(()=>this.api.tabs.get(row.tabId));
        if(tab.status!=='complete'){await this.waitPage(tab.id);continue;}
        const url=new URL(tab.url||'https://invalid/');
        let outcome;
        if(target!=='canvas'&&url.origin==='https://eams.sufe.edu.cn'){
          if(url.pathname===new URL(AUTH_ENTRIES[target]).pathname){await this.waitPage(tab.id);continue;}
          const exact=url.pathname===new URL(CONFIG[target].entry).pathname||(target==='sufe'&&url.pathname==='/eams/courseTableForStd!courseTable.action');
          if(exact)outcome={kind:'ready'};
          else if(row.targetRound!==round){row.targetRound=round;await this.save();await this.step(()=>this.api.tabs.update(tab.id,{url:CONFIG[target].entry}));continue;}
          else outcome={kind:'unrecognized'};
        }else if(url.hostname==='login.sufe.edu.cn'){
          outcome=await this.inspect(target,tab.id,false,fn=>this.step(fn));
        }else outcome=await this.inspect(target,tab.id,false,fn=>this.step(fn));
        this.assert(id);
        if(outcome.kind==='ready'){
          let session=await this.getSession(target,tab.id,fn=>this.step(fn));
          await this.change(target,{phase:'connecting',code:null,attemptAt:new Date().toISOString(),
            beforeUpdatedAt:this.state().lastStatus?.[target]?.state.updatedAt||null,attemptKind:'connect'});
          let result;try{result=(await this.native.call('connect',target,session))[target];}finally{session=null;}
          this.state().lastStatus[target]=result;
          await this.completed(target,result,'official_sso');
          await this.cleanup(target);return;
        }
        if(outcome.kind==='sso_entry'&&row.clickRound!==round){row.clickRound=round;await this.save();await this.inspect(target,tab.id,true,fn=>this.step(fn));}
        else if(outcome.kind==='canvas_entry'&&row.canvasRound!==round){row.canvasRound=round;await this.save();await this.step(()=>this.api.tabs.update(tab.id,{url:'https://canvas.sufe.edu.cn/'}));}
        else if(['human','unauthenticated','sso_entry'].includes(outcome.kind)){await this.pause(target,outcome.verification?'WAIT_VERIFICATION':'WAIT_LOGIN',round);return;}
        else if(outcome.kind==='network'){await this.change(target,{phase:'failed',code:'NETWORK_ERROR'});return;}
        else if(outcome.kind==='unrecognized'){await this.pause(target,'PAGE_UNRECOGNIZED',round);return;}
        await this.waitPage(tab.id);
      }
      await this.change(target,{phase:'failed',code:'TIMEOUT'});
    }catch(error){
      const raw=safeCode(error),code=raw==='BRIDGE_RESPONSE_INVALID'?'CONNECTION_OUTCOME_UNKNOWN':raw;
      if(this.task?.id!==id)return;
      if(this.task.cancelled){const uncertain=['connecting','unknown'].includes(row.phase)||code==='CONNECTION_OUTCOME_UNKNOWN';await this.change(target,{phase:uncertain?'unknown':'cancelled',code:uncertain?'CONNECTION_OUTCOME_UNKNOWN':'CANCELLED'});return;}
      if(['LOGIN_REQUIRED','SCHOOL_SESSION_MISSING'].includes(code))await this.pause(target,code,round);
      else await this.change(target,{phase:code==='CONNECTION_OUTCOME_UNKNOWN'?'unknown':'failed',code});
    }
  }
  async cleanup(target){
    const row=this.task.systems[target];
    if(!row.createdByTask||this.task.cancelled||!['succeeded','partial'].includes(row.phase)||!this.api.tabs.remove)return;
    const tab=await this.api.tabs.get(row.tabId).catch(()=>null);
    if(!tab)return;
    const url=new URL(tab.url||'https://invalid/');
    if(url.origin!==new URL(CONFIG[target].entry).origin)return;
    row.cleaning=true;await this.save();
    await this.api.tabs.remove(row.tabId).catch(()=>{});row.tabId=null;row.cleaning=false;await this.save();
  }
  async resume(){
    if(this.running||!this.task?.unified||this.task.cancelled)throw new Fault('TARGET_NOT_READY');
    const targets=Object.entries(this.task.systems).filter(([,r])=>r.phase==='waiting').map(([t])=>t);
    if(!targets.length)throw new Fault(Object.values(this.task.systems).some(r=>r.phase==='unknown')?'CONNECTION_OUTCOME_UNKNOWN':'TARGET_NOT_READY');
    if(this.task.portalWaiting){this.task.round=1;this.task.portalWaiting=false;}
    // Once portal authentication was signalled, later continuation resumes
    // the existing system page, without restarting official login loops.
    this.task.cooldownScheduled=false;await this.api.alarms.clear?.('school-update-cooldown');
    this.task.expiresAt=Date.now()+600000;await this.save();void this.run(targets);
  }
  async retry(scheduled=false){
    if(this.running||!this.task?.unified||scheduled&&this.task.cancelled
      ||!scheduled&&Object.values(this.task.systems).some(r=>r.phase==='unknown'))throw new Fault('TARGET_NOT_READY');
    const targets=Object.entries(this.task.systems).filter(([,r])=>(scheduled?r.phase==='cooldown'&&(r.autoRetryCount||0)<1:['failed','partial','cancelled','cooldown'].includes(r.phase))
      &&(!r.retryAt||r.retryAt<=Date.now())).map(([target])=>target);
    if(!targets.length){if(scheduled){await this.scheduleCooldown();await this.save();return;}throw new Fault('TARGET_NOT_READY');}
    this.task.cooldownScheduled=false;await this.api.alarms.clear?.('school-update-cooldown');
    this.task.cancelled=false;this.task.portalWaiting=false;this.task.round=0;delete this.task.completedAt;
    for(const target of targets){
      const row=this.task.systems[target];
      for(const key of ['authRound','clickRound','canvasRound','targetRound'])delete row[key];
      row.autoRetryCount=scheduled?(row.autoRetryCount||0)+1:0;
      row.phase='queued';row.code=null;
    }
    this.task.expiresAt=Date.now()+600000;await this.save();void this.run(targets,true);
  }
  async cancel(){
    if(!this.task?.unified)return;
    this.task.cancelled=true;this.task.portalWaiting=false;this.controller?.abort();
    this.task.cooldownScheduled=false;await this.api.alarms.clear?.('school-update-cooldown');
    for(const row of Object.values(this.task.systems))if(['queued','opening','checking','connecting','sso_pending','waiting','cooldown'].includes(row.phase)){
      const uncertain=row.phase==='connecting';row.phase=uncertain?'unknown':'cancelled';row.code=uncertain?'CONNECTION_OUTCOME_UNKNOWN':'CANCELLED';
    }
    await this.save();
  }
  async reconcile(status){
    if(!this.task?.unified)return;
    for(const [target,row]of Object.entries(this.task.systems)){
      const result=status[target];
      if(row.phase!=='unknown'||!result||result.state?.refreshing)continue;
      if(this.task.kind==='update'&&result.clientVersion!==2)throw new Fault('HOST_UPDATE_REQUIRED');
      const afterAttempt=Date.parse(result.state.checkedAt||result.state.updatedAt)>=Math.floor(Date.parse(row.attemptAt||'invalid')/1000)*1000;
      if(afterAttempt&&result.state.available&&!result.state.stale&&!result.state.needsLogin&&!result.state.errorCode
        &&result.state.updatedAt!==row.beforeUpdatedAt)await this.completed(target,result,row.attemptKind==='check'?'saved_session':'official_sso');
      else if(afterAttempt&&result.state.errorCode)await this.change(target,{phase:'failed',code:result.state.errorCode,result});
    }
    await this.save();
  }
}
