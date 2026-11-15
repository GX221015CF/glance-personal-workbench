/* Production UI in a deterministic DOM fixture. No browser automation. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const model=require('../assets/study-ima-model.js');
const dates=require('../assets/study-material-dates.js');
const root=path.resolve(__dirname,'..');

test('recommendations require a current complete match and a valid upload time',()=>{
  const file={id:'2',courseId:'1',createdAt:'2026-11-10T10:00:00Z'};
  const at=Date.parse('2026-11-11T10:00:00Z');
  assert.equal(model.matches(file,'recommended',{status:'unsynced',supported:true},dates,at),true);
  for(const row of [{status:'exists'},{status:'review'},{status:'unsynced',stale:true},{status:'unsynced',supported:false}])assert.equal(model.matches(file,'recommended',row,dates,at),false);
  for(const createdAt of [null,'invalid','2027-01-01T00:00:00Z'])assert.equal(model.matches({...file,createdAt},'recommended',{status:'unsynced'},dates,at),false);
});
test('comparison identity uses course and linked library, including stale-cache indication',()=>{
  const file={id:'2',courseId:'1'};
  const comparisons={'1':{libraryId:'lib',rows:[{fileId:'2',status:'exists'}],stale:true,checkId:'check'}};
  assert.equal(model.record(file,comparisons,{'1':{libraryId:'lib'}}).status,'exists');
  assert.equal(model.record(file,comparisons,{'1':{libraryId:'other'}}).unchecked,true);
  assert.equal(model.record({...file,courseId:'2'},comparisons,{}).unchecked,true);
  assert.equal(model.record(file,comparisons,{'1':{libraryId:'lib'}}).stale,true);
});
test('unlinked and unchecked courses belong to the shared other state',()=>{
  const file={id:'2',courseId:'1'};
  const unlinked=model.record(file,{},{});
  const unchecked=model.record(file,{}, {'1':{libraryId:'lib'}});
  assert.equal(unlinked.status,'unlinked');assert.equal(unchecked.status,'unchecked');
  assert.equal(model.matches(file,'other',unlinked,dates),true);
  assert.equal(model.matches(file,'other',unchecked,dates),true);
});
test('shift selection follows filtered order and preserves hidden selections',()=>{
  const first=model.toggleSelection(new Set(['outside']),['a','b','c','d'],'b','d',true,true);
  assert.deepEqual([...first],['outside','b','c','d']);
  const second=model.toggleSelection(first,['a','b','c','d'],'d','b',false,true);
  assert.deepEqual([...second],['outside']);
});
test('real processed progress keeps successes, failures and uncertain outcomes distinct',()=>{
  const p=model.progress({rows:[{status:'success'},{status:'skipped'},{status:'failed'},{status:'unknown'},{status:'queued'}]});
  assert.deepEqual(p,{total:5,processed:4,success:2,failed:1,unknown:1,percent:80});
  assert.equal(model.progress({rows:[{status:'running'}]}).percent,0);
});

class Element {
  constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.parentNode=null;this.dataset={};this.style={};this.listeners={};this._text='';this.hidden=false;this.disabled=false;this._value=undefined;this.className='';this.classList={add:value=>{this.className+=' '+value;}};}
  set textContent(value){this._text=String(value);this.children=[];}
  get textContent(){return this._text+this.children.map(row=>row.textContent).join('');}
  set value(value){this._value=String(value);}
  get value(){return this._value??(this.tagName==='SELECT'?this.children[0]?.value||'':'');}
  set selectedIndex(value){this._value=this.children[value]?.value||'';}
  append(...children){for(const child of children){child.remove();child.parentNode=this;this.children.push(child);}}
  prepend(...children){for(const child of children.reverse()){child.remove();child.parentNode=this;this.children.unshift(child);}}
  after(child){const parent=this.parentNode;child.remove();child.parentNode=parent;parent.children.splice(parent.children.indexOf(this)+1,0,child);}
  remove(){if(this.parentNode){this.parentNode.children=this.parentNode.children.filter(row=>row!==this);this.parentNode=null;}}
  replaceChildren(...children){for(const child of this.children)child.parentNode=null;this.children=[];this._text='';if(this.tagName==='SELECT')this._value=undefined;this.append(...children);}
  setAttribute(key,value){this[key]=String(value);}
  getAttribute(key){return this[key];}
  addEventListener(name,callback){(this.listeners[name]??=[]).push(callback);}
  fire(name,extra={}){for(const callback of this.listeners[name]||[])callback({target:this,type:name,...extra});}
  dispatchEvent(event){this.fire(event.type,event);}
  click(extra={}){if(this.disabled)return false;if(this.tagName==='INPUT'&&this.type==='checkbox')this.checked=!this.checked;this.fire('click',extra);if(this.type==='checkbox')this.fire('change',extra);return true;}
  matches(selector){return selector.startsWith('.')?this.className.split(' ').includes(selector.slice(1)):this.tagName===selector.toUpperCase();}
  querySelectorAll(selector){return this.children.flatMap(child=>[...(child.matches(selector)?[child]:[]),...child.querySelectorAll(selector)]);}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  focus(){this.ownerDocument.activeElement=this;}
  showModal(){this.open=true;this.focus();}
  close(){this.open=false;this.fire('close');}
  escape(){let prevented=false;this.fire('cancel',{preventDefault(){prevented=true;}});if(!prevented)this.close();}
  get isConnected(){return this.tagName==='BODY'||!!this.parentNode?.isConnected;}
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));

test('directory refresh regression immediately shows scoped busy feedback without blocking selection',async()=>{
 const f=fixture({files:2});await flush();const {boxes}=attachSelectableCards(f);
 let finish;f.setCheckGate(new Promise(resolve=>finish=resolve));button(f,'更多').click();
 const refresh=button(f,'刷新 ima 状态');refresh.click();
 assert.equal(refresh.getAttribute('aria-busy'),'true');assert.match(refresh.textContent,/刷新中/);
 boxes[0].click();button(f,'取消').click();boxes[1].click();assert.equal(boxes[1].checked,true);
 refresh.click();await flush();assert.equal(f.calls.filter(row=>row.route==='check').length,2);
 finish();await flush();await flush();assert.equal(refresh.getAttribute('aria-busy'),'false');
});

test('directory visibility regression hides owned descendants and files while preserving similar names',()=>{
 const entries=[{kind:'folder',folderId:'test',parentFolderId:'lib',isTestDirectory:true},
 {kind:'folder',folderId:'child',parentFolderId:'test'}, {kind:'file',mediaId:'secret',parentFolderId:'child'},
 {kind:'folder',folderId:'normal',parentFolderId:'lib',name:'Workbench-IMA-Test-normal'},
 {kind:'file',mediaId:'visible',parentFolderId:'normal'}];
 assert.deepEqual(model.dailyEntries(entries).map(row=>row.folderId||row.mediaId),['normal','visible']);
});

test('directory management regression opens root and navigates only direct children',async()=>{
 const f=fixture({treeEntries:[{kind:'folder',folderId:'a',parentFolderId:'lib',name:'A'},
 {kind:'folder',folderId:'aa',parentFolderId:'a',name:'a'},
 {kind:'file',mediaId:'root-file',parentFolderId:'lib',name:'root.pdf'},
 {kind:'file',mediaId:'nested-file',parentFolderId:'aa',name:'nested.pdf'},
 {kind:'folder',folderId:'test',parentFolderId:'lib',name:'hidden',isTestDirectory:true}]});await flush();
 button(f,'更多').click();button(f,'目录管理').click();await flush();await flush();
 const modal=f.body.querySelector('dialog');assert.ok(modal.querySelector('.ima-breadcrumbs'));
 assert.match(modal.querySelector('.ima-cloud-list').textContent,/root.pdf/);
 assert.doesNotMatch(modal.querySelector('.ima-cloud-list').textContent,/nested.pdf|hidden/);
 button(f,'▸ A').click();assert.ok(button(f,'▸ a'));button(f,'▸ a').click();
 assert.match(modal.querySelector('.ima-cloud-list').textContent,/nested.pdf/);
 button(f,'上一级').click();assert.ok(button(f,'▸ a'));
});
function fixture({files=12,candidates=false,candidateCount=1,job=null,stored={},failRoute=null,checkGate=null,previewGate=null,treeEntries=null,treeGate=null,treeGatesByLibrary=null,previewConflicts=[],librariesList=null,treesByLibrary=null}={}){
  const body=new Element('body'),ids={},storage=new Map([['workbench-study-ima-v1',JSON.stringify(stored)]]),calls=[];
  for(const id of ['study-file-sort','study-file-course']){ids[id]=new Element('select');body.append(ids[id]);}
  ids['study-file-tools']=new Element('div');body.append(ids['study-file-tools']);
  const rows=Array.from({length:files},(_,index)=>({id:String(index+2),courseId:'1',name:`课件${index+1}.pdf`,size:100,createdAt:'2026-11-10T10:00:00Z'}));
  const comparison={libraryId:'lib',courseId:'1',complete:true,checkId:'check',checkedAt:'2026-11-11T00:00:00Z',
    rows:rows.map((file,index)=>({fileId:file.id,courseId:'1',status:candidates&&index===0?'exists':'unsynced',supported:true,
      candidates:candidates&&index===0?Array.from({length:candidateCount},(_,i)=>({mediaId:i?'manual-'+i:'manual',name:'课件1.pdf',path:'/历史'+i+'/课件1.pdf',size:null})):[]}))};
  const view={links:{'1':{libraryId:'lib',folderId:'folder',path:'财务管理/课件PPT'}},comparisons:{'1':comparison},job,formats:['pdf'],maxFileBytes:20*1024*1024};
  const cloudEntries=treeEntries||[{kind:'folder',folderId:'folder',parentFolderId:'lib',path:'/课件PPT',name:'课件PPT'}];
  const listeners={};
  const document={body,visibilityState:'visible',getElementById:id=>ids[id],createElement:tag=>Object.assign(new Element(tag),{ownerDocument:document}),addEventListener:(name,action)=>{listeners[name]=action;}};
  let uuid=0;
  let treeComplete=true;
  const window={StudyImaModel:model};
  const context=vm.createContext({window,document,StudyMaterialDates:dates,Date,Set,Map,Event:class{constructor(type){this.type=type;}},
    StudyFileTypes:require('../assets/study-file-types.js'),AbortSignal,crypto:{randomUUID:()=>`request-${++uuid}`},setTimeout:()=>1,clearTimeout(){},
    sessionStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value)},
    fetch:async(url,options={})=>{
      if(url.endsWith('/api/desktop'))return {ok:true,json:async()=>({token:'SYNTHETIC'})};
      const route=url.split('/').at(-1),data=JSON.parse(options.body);calls.push({route,data});
      if(route===failRoute)return {ok:false,json:async()=>({error:{code:'UPSTREAM_ERROR',message:'合成目录操作失败'}})};
      let result=view;
      if(route==='libraries')result={libraries:librariesList||[{libraryId:'lib',name:'财务管理'}]};
      if(route==='tree'){if(treeGatesByLibrary?.[data.libraryId])await treeGatesByLibrary[data.libraryId];else if(treeGate)await treeGate;result={entries:treesByLibrary?.[data.libraryId]||cloudEntries.slice(),complete:treeComplete,checkedAt:'2026-11-11T00:00:00Z'};}
      if(route==='folder'){
        cloudEntries.push({kind:'folder',folderId:'created',parentFolderId:data.folderId,name:data.name,path:'/新目录'});
        result={objectId:'created'};
      }
      if(route==='preview'){
        if(!(librariesList||[{libraryId:'lib'}]).some(row=>row.libraryId===data.libraryId)||data.folderId!==data.libraryId&&!['folder','lib','nested','created'].includes(data.folderId))return {ok:false,json:async()=>({error:{code:'TARGET_MISSING',message:'原保存位置已不存在，请更改位置'}})};
        if(previewGate)await previewGate;
        result={token:'preview-'+data.folderId,sources:rows.filter(row=>data.files.some(pair=>pair.courseId===row.courseId&&pair.fileId===row.id)).map(row=>({fileName:row.name,size:row.size,courseId:row.courseId,courseName:row.courseName||'财务管理'})),target:{path:((librariesList||[{libraryId:'lib',name:'财务管理'}]).find(row=>row.libraryId===data.libraryId)?.name||'')+(cloudEntries.find(row=>row.folderId===data.folderId)?.path||'')},conflicts:previewConflicts};
      }
      if(route==='resolve-preview')result={token:'resolved-preview'};
      if(route==='confirm-batch'){
        const confirmed=data.selections.map(selection=>{
          const row=comparison.rows.find(row=>row.fileId===selection.fileId);
          if(row?.candidates?.length>1&&!selection.mediaId)return {fileId:selection.fileId,status:'needs_selection'};
          if(row){row.status='exists';row.mediaId=selection.mediaId||row.candidates?.[0]?.mediaId;row.candidates=[];}
          return {fileId:selection.fileId,status:'confirmed'};
        });
        result={rows:confirmed,comparison};
      }
      if(route==='start')result={id:'job',requestId:data.requestId,phase:'queued',rows:[]};
      if(route==='confirm')result=comparison;
      if(route==='check'){if(checkGate)await checkGate;result=view.comparisons[data.courseId]||comparison;}
      return {ok:true,json:async()=>({data:result})};
    }});
  vm.runInContext(fs.readFileSync(path.join(root,'assets/study-ima.js'),'utf8'),context);
  const api=window.StudyIma;
  api.mount(()=>{});api.setContext({files:rows,courses:[{id:'1',name:'财务管理'}],courseId:'1'});api.setRows(rows);
  return {api,body,rows,calls,view,comparison,ids,storage,listeners,setFailure:value=>{failRoute=value;},setCheckGate:value=>{checkGate=value;},setTreeComplete:value=>{treeComplete=value;}};
}
function button(f,text){
  const matches=row=>row.textContent===text||(text==='确认保存'&&row.textContent.startsWith('确认保存 '))||(text==='确认所选已保存'&&row.textContent.startsWith('确认所选已保存 '));
  const modal=f.body.querySelectorAll('dialog').at(-1);
  return modal?.querySelectorAll('button').find(matches)||f.body.querySelectorAll('button').find(matches);
}

test('actual UI select-all includes the entire filtered result beyond six rendered cards',async()=>{
  const f=fixture();await flush();
  const all=f.body.querySelectorAll('input').find(row=>row.type==='checkbox');all.checked=true;all.fire('change');await flush();
  const saved=JSON.parse(f.storage.get('workbench-study-ima-v1'));
  assert.equal(saved.selected.length,12);
  assert.match(f.body.textContent,/已选 12 份/);
  f.api.setRows(f.rows.slice(0,2));
  assert.match(f.body.textContent,/筛选外 10 份/);
  button(f,'取消').fire('click');await flush();
  assert.equal(JSON.parse(f.storage.get('workbench-study-ima-v1')).selected.length,0);
});


test('save dialog automatically previews chosen files and requires just one explicit save',async()=>{
  const f=fixture({stored:{selected:['1:2','1:3']}});await flush();
  button(f,'保存到 ima').fire('click');await flush();await flush();
  assert.equal(button(f,'预览所选文件'),undefined);
  assert.match(f.body.querySelector('.ima-current-target').textContent,/财务管理 › 课件PPT/);
  assert.match(f.body.querySelector('.ima-file-count').textContent,/2 份资料/);
  assert.match(f.body.textContent,/财务管理 · 课件1.pdf/);
  assert.deepEqual(f.calls.find(row=>row.route==='preview').data.files,[{courseId:'1',fileId:'2'},{courseId:'1',fileId:'3'}]);
  assert.equal(f.calls.some(row=>row.route==='start'),false);
  button(f,'确认保存').fire('click');await flush();await flush();
  const start=f.calls.find(row=>row.route==='start');assert.equal(start.data.token,'preview-folder');assert.ok(start.data.requestId);
});
test('folder errors are visible inside the current dialog and dialog has an accessible title',async()=>{
  const f=fixture({stored:{selected:['1:2']},failRoute:'folder'});await flush();
  button(f,'保存到 ima').fire('click');await flush();await flush();
  const dialog=f.body.querySelector('dialog');
  assert.equal(dialog.getAttribute('aria-labelledby'),dialog.querySelector('h2').id);
  assert.ok(dialog.querySelector('h2').id);
  button(f,'更改').fire('click');await flush();await flush();
  button(f,'新建文件夹').click();const name=dialog.querySelectorAll('input').find(row=>row.placeholder==='文件夹名称');name.value='合成测试目录';
  button(f,'创建').fire('click');await flush();await flush();
  assert.match(dialog.querySelector('.ima-create').textContent,/合成目录操作失败/);
});
test('a completed check stays attached to its original course after switching courses',async()=>{
  let finish;const gate=new Promise(resolve=>{finish=resolve;});
  const f=fixture({checkGate:gate});await flush();
  assert.equal(f.calls.find(row=>row.route==='check').data.courseId,'1');
  const other={id:'99',courseId:'2',name:'另一课程.pdf',size:100,createdAt:'2026-11-10T10:00:00Z'};
  f.view.links['2']={libraryId:'lib',folderId:'folder'};
  f.view.comparisons['2']={courseId:'2',libraryId:'lib',checkId:'other-check',rows:[{fileId:'99',status:'exists',path:'/另一课程.pdf',candidates:[]}]};
  f.api.setContext({files:[...f.rows,other],courses:[{id:'1',name:'财务管理'},{id:'2',name:'另一课程'}],courseId:'2'});
  await flush();finish();await flush();await flush();
  const card=new Element('article'),content=new Element('div');content.className='study-file-content';card.append(content);f.body.append(card);
  f.api.decorate(card,other);
  assert.match(card.querySelector('.ima-file-state').textContent,/已保存/);
  assert.doesNotMatch(card.querySelector('.ima-file-state').textContent,/尚未核对/);
});
test('production progress DOM exposes actual count, failure text and retry action',async()=>{
  const job={id:'job',phase:'complete',rows:[{source:{fileName:'A.pdf'},status:'success'},{source:{fileName:'B.pdf'},status:'failed',error:{code:'NETWORK_ERROR'}}]};
  const f=fixture({job});await flush();
  const progress=f.body.querySelector('.ima-progress');
  assert.equal(progress.getAttribute('role'),'progressbar');assert.equal(progress.getAttribute('aria-valuenow'),'2');
  assert.equal(progress.getAttribute('aria-valuemax'),'2');assert.match(f.body.textContent,/成功 1 · 失败 1/);
  assert.equal(button(f,'重试失败文件').hidden,false);
});
test('idle completed history is available only inside the more dialog',async()=>{
 const job={id:'job',phase:'complete',finishedAt:'2026-11-11T06:10:00Z',rows:[{source:{fileName:'A.pdf'},status:'skipped'}]};
 const f=fixture({job});await flush();
 assert.equal(f.body.querySelector('.ima-job').hidden,true);assert.equal(f.body.querySelector('.ima-history'),null);
 button(f,'更多').fire('click');await flush();
 assert.match(f.body.querySelector('dialog').textContent,/上次保存.*已存在 1 份/);
 assert.ok(f.body.querySelector('dialog').querySelector('.ima-history'));
 button(f,'关闭').fire('click');assert.equal(f.body.querySelector('dialog'),null);
});

test('all-course saved-status filtering remains active and keeps the permanent toolbar',async()=>{
 const f=fixture({files:149,stored:{filter:'unsynced'}});await flush();
 f.comparison.rows.slice(0,3).forEach(row=>row.status='exists');
 f.api.setContext({files:f.rows,courses:[{id:'1',name:'财务管理'}],courseId:''});await flush();
 assert.equal(f.api.mode(),'unsynced');assert.equal(f.api.filter(f.rows).length,146);
 assert.equal(f.body.querySelector('.ima-controls').hidden,false);
 assert.ok(f.body.querySelector('.ima-toolbar'));assert.equal(f.body.querySelector('.ima-filters'),null);
 assert.equal(button(f,'保存到 ima').disabled,true);assert.equal(button(f,'取消').disabled,true);
 assert.equal(f.body.querySelector('.ima-selection').querySelector('input').getAttribute('aria-label'),'全选当前筛选结果，共 149 份可选');
});

test('four dropdown configuration and current-course position expose more as an independent dialog',async()=>{
 const f=fixture();await flush();
 const select=f.body.querySelectorAll('select').find(row=>row.id==='study-file-state');
 assert.deepEqual(select.children.map(row=>row.textContent),['全部','待保存','已保存','其他']);
 assert.equal(f.body.querySelector('.ima-controls').querySelectorAll('button').filter(row=>!row.hidden).map(row=>row.textContent).join(''),'更多');
 assert.equal(f.body.querySelector('dialog'),null);button(f,'更多').fire('click');await flush();
 const modal=f.body.querySelector('dialog');
 for(const label of ['更改保存位置','刷新 ima 状态','目录管理'])assert.ok(button(f,label));
 assert.ok(modal.getAttribute('aria-labelledby'));assert.match(modal.textContent,/暂无保存记录/);
 assert.equal(f.body.querySelector('.ima-normal-info'),null);
 assert.match(f.body.querySelector('.ima-link-label').textContent,/财务管理 › 课件PPT/);
});

test('permanent save and cancel controls track selection while unknown jobs keep recovery',async()=>{
 const f=fixture();await flush();const toolbar=f.body.querySelector('.ima-toolbar');
 const initial=toolbar.children.slice();assert.equal(button(f,'保存到 ima').disabled,true);assert.equal(button(f,'取消').disabled,true);
 const all=f.body.querySelector('.ima-selection').querySelector('input');all.checked=true;all.fire('change');await flush();
 assert.equal(button(f,'保存到 ima').disabled,false);assert.equal(button(f,'取消').disabled,false);assert.match(toolbar.textContent,/已选 12 份/);
 f.api.setRows(f.rows.slice(0,2));assert.match(toolbar.textContent,/筛选外 10 份/);
 button(f,'取消').fire('click');await flush();assert.equal(button(f,'取消').disabled,true);
 assert.equal(f.body.querySelector('.ima-toolbar'),toolbar);assert.deepEqual(toolbar.children,initial);
 assert.equal(f.body.querySelector('.ima-selected-count').hidden,true);
 for(const [phase,status,recovery] of [['complete','unknown','检查保存结果'],['interrupted','not_run','重试失败文件']]){
   const other=fixture({job:{id:'job',phase,rows:[{source:{courseId:'1',fileName:'A.pdf'},status}]}});await flush();
   assert.equal(other.body.querySelector('.ima-job').hidden,false);assert.equal(button(other,recovery).hidden,false);
   assert.equal(button(other,recovery).parentNode,other.body.querySelector('.ima-job'));
 }
});

test('running progress keeps actual count and target, then shows a short completion and collapses history',async()=>{
  const job={id:'current',phase:'running',target:{path:'财务管理/课件PPT'},rows:[
    {source:{courseId:'1',fileName:'A.pdf'},status:'success'},
    {source:{courseId:'1',fileName:'B.pdf'},status:'running'},
  ]};
  const f=fixture({job});await flush();
  const progress=f.body.querySelector('.ima-progress');
  assert.equal(progress.hidden,false);assert.equal(progress.getAttribute('aria-valuenow'),'1');
  assert.equal(progress.getAttribute('aria-valuemax'),'2');
  assert.match(f.body.querySelector('.ima-job').textContent,/已处理 1\/2 · 财务管理\/课件PPT/);
  f.view.job={...job,phase:'complete',rows:job.rows.map(row=>({...row,status:'success'})),finishedAt:'2026-11-11T14:00:00Z'};
  f.listeners.visibilitychange();await flush();await flush();
  assert.equal(progress.hidden,true);
  assert.equal(f.body.querySelector('.ima-job').hidden,false);
  assert.match(f.body.querySelector('.ima-job').textContent,/保存结果 · 成功 2/);
  assert.equal(f.body.querySelector('.ima-job').querySelector('details').hidden,true);
  button(f,'更多').fire('click');await flush();assert.match(f.body.querySelector('.ima-history').textContent,/财务管理 · 成功 2/);
});

test('automatic read errors persist as one expandable warning outside normal information',async()=>{
  const f=fixture({failRoute:'check'});await flush();await flush();
  const warning=f.body.querySelector('.ima-warning');assert.equal(warning.hidden,false);
  assert.equal(warning.querySelector('summary').textContent,'ima 状态暂未更新');
  assert.match(warning.textContent,/合成目录操作失败/);
  assert.ok(warning.querySelectorAll('button').some(row=>row.textContent==='重试检查'));
});
test('old recommendation filter migrates to pending and preserves the selection',async()=>{
 const f=fixture({stored:{filter:'recommended',selected:['1:2']}});await flush();
 const select=f.body.querySelectorAll('select').find(row=>row.id==='study-file-state');
 assert.equal(select.value,'unsynced');assert.equal(f.body.querySelector('.ima-filters'),null);
 select.value='exists';select.fire('change');await flush();
 const saved=JSON.parse(f.storage.get('workbench-study-ima-v1'));assert.deepEqual(saved.selected,['1:2']);assert.equal(saved.filter,'exists');
});

test('management actions live in the more dialog and close restores the trigger',async()=>{
 const f=fixture();await flush();const trigger=button(f,'更多');trigger.focus();trigger.fire('click');await flush();
 assert.ok(f.body.querySelector('dialog'));assert.equal(trigger.getAttribute('aria-expanded'),'true');
 assert.equal(f.body.querySelector('.ima-controls').querySelectorAll('button').some(row=>row.textContent==='更改'),false);
 button(f,'关闭').fire('click');await flush();
 assert.equal(trigger.getAttribute('aria-expanded'),'false');assert.equal(trigger.ownerDocument.activeElement,trigger);
});

test('single, Shift and filtered select-all include supported same-name materials',async()=>{
  const f=fixture({files:4,candidates:true});await flush();
  const cards=f.rows.map(file=>{const card=new Element('article'),content=new Element('div');content.className='study-file-content';card.append(content);f.body.append(card);f.api.decorate(card,file);return card;});
  const first=cards[0].querySelector('.ima-file-select');assert.equal(first.disabled,false);first.checked=true;first.fire('click');await flush();
  const third=cards[2].querySelector('.ima-file-select');third.checked=true;third.fire('click',{shiftKey:true});await flush();
  assert.deepEqual(JSON.parse(f.storage.get('workbench-study-ima-v1')).selected,['1:2','1:3','1:4']);
  const all=f.body.querySelector('.ima-selection').querySelector('input');all.checked=true;all.fire('change');await flush();
  assert.equal(JSON.parse(f.storage.get('workbench-study-ima-v1')).selected.length,4);
  assert.ok(button(f,'保存到 ima'));
  assert.equal(f.calls.filter(row=>row.route==='confirm-batch').length,0);
});











test('save preview choices resolve against the preview before start and display copy destination',async()=>{
  const conflict={courseId:'1',fileId:'2',fileName:'课件1.pdf',candidates:[{mediaId:'manual',path:'/历史/课件1.pdf'}]};
  const f=fixture({stored:{selected:['1:2']},previewConflicts:[conflict]});await flush();
  button(f,'保存到 ima').fire('click');await flush();await flush();
  const action=f.body.querySelector('.ima-conflict-choice').querySelector('select');
  assert.equal(action.value,'exists');action.value='copy';action.fire('change');
  assert.match(f.body.querySelector('.ima-conflict-choice').textContent,/将新增 课件1.pdf → 财务管理\/课件PPT/);
  button(f,'确认保存').fire('click');await flush();await flush();
  const resolve=f.calls.find(row=>row.route==='resolve-preview');
  assert.equal(resolve.data.token,'preview-folder');assert.equal(resolve.data.decisions[0].action,'copy');
  assert.equal(f.calls.find(row=>row.route==='start').data.token,'resolved-preview');
});







test('unselectable formats retain a keyboard-accessible reason instead of candidate-based disabling',async()=>{
  const f=fixture();await flush();const file={...f.rows[0],name:'程序.exe'};
  const card=new Element('article'),content=new Element('div');content.className='study-file-content';card.append(content);f.body.append(card);f.api.decorate(card,file);
  assert.equal(card.querySelector('.ima-file-select').disabled,true);
  const reason=card.querySelector('.ima-unselectable');assert.equal(reason.tagName,'DETAILS');
  assert.equal(reason.querySelector('summary').textContent,'不可选');assert.match(reason.textContent,/暂不支持此格式/);
});

test('changing a destination refreshes the preview before any upload can start',async()=>{
  const f=fixture({stored:{selected:['1:2']}});await flush();
  button(f,'保存到 ima').fire('click');await flush();await flush();
  button(f,'更改').fire('click');await flush();await flush();
  f.body.querySelector('.ima-breadcrumbs').querySelector('button').fire('click');await flush();
  button(f,'使用此位置').fire('click');
  assert.equal(button(f,'确认保存').disabled,true);
  await flush();await flush();
  assert.equal(f.calls.filter(row=>row.route==='preview').length,2);
  assert.equal(f.calls.some(row=>row.route==='start'),false);
  button(f,'确认保存').fire('click');await flush();await flush();
  assert.equal(f.calls.find(row=>row.route==='start').data.token,'preview-lib');
});

test('preview locks target controls and keeps the cloud untouched until confirmation',async()=>{
  let finish;const previewGate=new Promise(resolve=>{finish=resolve;});
  const f=fixture({stored:{selected:['1:2']},previewGate});await flush();
  button(f,'保存到 ima').fire('click');await flush();await flush();
  const modal=f.body.querySelector('dialog');
  assert.equal(button(f,'更改').disabled,true);
  assert.match(modal.querySelector('.ima-current-target').textContent,/财务管理 › 课件PPT/);
  assert.match(modal.querySelector('.ima-file-count').textContent,/1 份资料/);
  assert.equal(button(f,'确认保存').disabled,true);
  assert.equal(f.calls.some(row=>row.route==='start'),false);
  finish();await flush();await flush();
  assert.equal(button(f,'确认保存').disabled,false);
});

test('missing saved folder or library requires reselection instead of falling back to a root',async()=>{
  for(const property of ['folderId','libraryId']){
    const f=fixture({stored:{selected:['1:2']}});await flush();
    f.view.links['1'][property]='removed';
    button(f,'保存到 ima').fire('click');await flush();await flush();
    assert.equal(f.calls.some(row=>row.route==='start'),false);
    assert.equal(button(f,'确认保存').disabled,true);
    assert.match(f.body.textContent,/保存位置已不存在/);
    assert.equal(button(f,'重新准备清单').hidden,false);
  }
});

test('setting a course destination also checks existing files without a second action',async()=>{
  const f=fixture();await flush();
  f.calls.length=0;
  button(f,'更多').fire('click');await flush();button(f,'更改保存位置').fire('click');await flush();await flush();
  button(f,'使用此位置').fire('click');await flush();await flush();
  const routes=f.calls.map(row=>row.route);
  assert.ok(routes.indexOf('link')<routes.indexOf('check'));
  assert.equal(f.calls.some(row=>row.route==='start'),false);
  assert.equal(f.body.querySelector('dialog'),null);
});

test('course selection and changed Canvas files automatically check once without sending an upload',async()=>{
  const f=fixture();await flush();await flush();
  assert.equal(f.calls.filter(row=>row.route==='check').length,1);
  const context={files:f.rows,courses:[{id:'1',name:'财务管理'}],courseId:'1'};
  f.api.setContext(context);f.api.setContext(context);await flush();
  assert.equal(f.calls.filter(row=>row.route==='check').length,1);
  const added={id:'99',courseId:'1',name:'新课件.pdf',size:100,createdAt:'2027-02-01T00:00:00Z'};
  f.api.setContext({...context,files:[...f.rows,added]});await flush();await flush();
  assert.equal(f.calls.filter(row=>row.route==='check').length,2);
  assert.equal(f.calls.some(row=>row.route==='start'||row.route==='confirm'),false);
});

test('preview failure keeps confirmation disabled and reports the error in the save window',async()=>{
  const f=fixture({stored:{selected:['1:2']},failRoute:'preview'});await flush();
  button(f,'保存到 ima').fire('click');await flush();await flush();
  assert.equal(button(f,'确认保存').disabled,true);
  assert.match(f.body.querySelector('dialog').textContent,/合成目录操作失败/);
  assert.equal(f.calls.some(row=>row.route==='start'),false);
});

test('directory helpers follow stable parents and reject missing or cyclic paths',()=>{
  const entries=[{kind:'folder',folderId:'a',parentFolderId:'lib'},{kind:'folder',folderId:'b',parentFolderId:'a'}];
  assert.deepEqual(model.directoryTrail(entries,'lib','b').map(row=>row.folderId),['a','b']);
  assert.deepEqual(model.directoryChildren(entries,'a').map(row=>row.folderId),['b']);
  assert.equal(model.directoryTrail(entries,'lib','missing'),null);
  assert.equal(model.directoryTrail([{kind:'folder',folderId:'cycle',parentFolderId:'cycle'}],'lib','cycle'),null);
});

test('hierarchical picker lists one level, hides owned tests and commits a nested target',async()=>{
  const f=fixture({stored:{selected:['1:2']},treeEntries:[
    {kind:'folder',folderId:'folder',parentFolderId:'lib',name:'课件PPT',path:'/课件PPT'},
    {kind:'folder',folderId:'nested',parentFolderId:'folder',name:'章节',path:'/课件PPT/章节'},
    {kind:'folder',folderId:'owned',parentFolderId:'lib',name:'Workbench-IMA-Test-id',path:'/Workbench-IMA-Test-id',isTestDirectory:true},
  ]});await flush();
  button(f,'保存到 ima').fire('click');await flush();await flush();
  assert.equal(f.calls.filter(row=>row.route==='tree').length,1);
  button(f,'更改').fire('click');await flush();await flush();
  assert.match(f.body.querySelector('.ima-folder-list').textContent,/章节/);
  f.body.querySelector('.ima-breadcrumbs').querySelector('button').fire('click');await flush();
  assert.equal(f.body.querySelector('.ima-test-directories'),null);
  assert.doesNotMatch(f.body.querySelector('.ima-folder-list').textContent,/Workbench-IMA-Test-id/);
  button(f,'▸ 课件PPT').fire('click');await flush();button(f,'▸ 章节').fire('click');await flush();
  assert.equal(f.calls.filter(row=>row.route==='tree').length,2);
  assert.match(f.body.querySelector('.ima-breadcrumbs').textContent,/章节/);
  button(f,'使用此位置').fire('click');await flush();await flush();
  assert.match(f.body.querySelector('.ima-current-target').textContent,/财务管理 › 课件PPT › 章节/);
  button(f,'确认保存').fire('click');await flush();await flush();
  assert.equal(f.calls.find(row=>row.route==='start').data.token,'preview-nested');
});

test('directory read errors keep the known target and count and allow a read-only retry',async()=>{
  const f=fixture({stored:{selected:['1:2']},failRoute:'tree'});await flush();
  button(f,'保存到 ima').fire('click');await flush();await flush();
  button(f,'更改').fire('click');await flush();await flush();
  assert.match(f.body.querySelector('.ima-current-target').textContent,/财务管理 › 课件PPT/);
  assert.match(f.body.querySelector('.ima-file-count').textContent,/1 份/);
  assert.equal(button(f,'确认保存').disabled,true);
  f.setFailure(null);button(f,'重试读取目录').fire('click');await flush();await flush();
  assert.equal(button(f,'使用此位置').disabled,false);
  button(f,'取消').fire('click');await flush();
  button(f,'重新准备清单').click();await flush();await flush();
  assert.equal(button(f,'确认保存').disabled,false);
  assert.equal(f.calls.some(row=>row.route==='start'||row.route==='folder'),false);
});

test('new directory is created under the browsing parent and selected only by explicit use',async()=>{
  const f=fixture({stored:{selected:['1:2']}});await flush();
  button(f,'保存到 ima').fire('click');await flush();await flush();
  button(f,'更改').fire('click');await flush();await flush();
  button(f,'新建文件夹').click();const input=f.body.querySelector('dialog').querySelector('input');input.value='新目录';
  button(f,'创建').fire('click');await flush();await flush();
  assert.equal(f.calls.find(row=>row.route==='folder').data.folderId,'folder');
  assert.match(f.body.querySelector('.ima-current-target').textContent,/课件PPT/);
  button(f,'使用此位置').fire('click');await flush();await flush();
  assert.equal(f.calls.filter(row=>row.route==='preview').at(-1).data.folderId,'created');
  assert.equal(f.calls.some(row=>row.route==='start'),false);
});

test('failed preview can be retried without selecting files again or sending an upload',async()=>{
  const f=fixture({stored:{selected:['1:2']},failRoute:'preview'});await flush();
  button(f,'保存到 ima').fire('click');await flush();await flush();
  f.setFailure(null);button(f,'重新准备清单').fire('click');await flush();await flush();
  assert.equal(button(f,'确认保存').disabled,false);
  assert.equal(f.calls.some(row=>row.route==='start'),false);
  assert.deepEqual(f.calls.filter(row=>row.route==='preview').at(-1).data.files,[{courseId:'1',fileId:'2'}]);
});
test('responsive CSS, keyboard controls and reduced motion use compact green rectangular progress',()=>{
  const css=fs.readFileSync(path.join(root,'assets/study-ima.css'),'utf8');
  assert.match(css,/@media\(max-width:600px\)/);assert.match(css,/prefers-reduced-motion:reduce/);
  assert.match(css,/\.ima-progress[^}]*border-radius:2px/);assert.match(css,/\.ima-progress>span[^}]*background:#94c7a0/);
  assert.match(css,/:focus-visible/);assert.match(css,/overflow-wrap:anywhere/);
  assert.match(css,/\.study-file\.has-ima>\.study-file-type\s*\{display:grid/);
  const config=fs.readFileSync(path.join(root,'config/glance.yml'),'utf8');
  assert.ok(config.indexOf('/assets/study-ima.js')<config.indexOf('/assets/study.js'));
});

test('official daily entry uses a fixed external link with native keyboard semantics',async()=>{
  const f=fixture({candidates:true});await flush();
  const link=f.body.querySelector('.ima-official');
  assert.equal(link.tagName,'A');assert.equal(link.textContent,'打开 ima');
  assert.equal(link.href,'https://ima.qq.com/');assert.equal(link.target,'_blank');
  assert.equal(link.rel,'noopener noreferrer');
  assert.equal(f.body.querySelector('.ima-confirm-bar'),null);
  assert.ok(!f.body.querySelectorAll('button').some(row=>/浏览 ima|确认所选已保存/.test(row.textContent)));
  const css=fs.readFileSync(path.join(root,'assets/study-ima.css'),'utf8');
  assert.match(css,/\.ima-official[^}]*border-radius:7px/);
  for(const state of ['hover','focus-visible','active'])assert.ok(css.includes('.ima-official:'+state));
});

test('name-rule results are saved, selectable, and absent from pending without a confirmation panel',async()=>{
  const f=fixture({candidates:true});await flush();
  f.comparison.rows[0].association='user_name_rule';f.comparison.rows[0].path='/课件PPT/课件1.pdf';
  const card=new Element('article'),content=new Element('div');content.className='study-file-content';card.append(content);f.body.append(card);
  f.api.decorate(card,f.rows[0]);
  assert.equal(card.querySelector('.ima-file-select').disabled,false);
  assert.match(card.querySelector('.ima-file-state').textContent,/已保存.*课件PPT/);
  assert.doesNotMatch(card.textContent,/需确认|有同名文件/);
  assert.equal(model.matches(f.rows[0],'unsynced',f.comparison.rows[0],dates),false);
  assert.equal(model.matches(f.rows[0],'exists',f.comparison.rows[0],dates),true);
});

test('multiple existing locations default to skipping without choosing a unique media',async()=>{
  const conflict={courseId:'1',fileId:'2',fileName:'课件1.pdf',candidates:[{mediaId:'a',path:'/甲/课件1.pdf'},{mediaId:'b',path:'/乙/课件1.pdf'}]};
  const f=fixture({stored:{selected:['1:2']},previewConflicts:[conflict]});await flush();
  button(f,'保存到 ima').fire('click');await flush();await flush();
  const action=f.body.querySelector('.ima-conflict-choice').querySelector('select');
  assert.equal(action.value,'exists');assert.equal(button(f,'确认保存').disabled,false);
  button(f,'确认保存').fire('click');await flush();await flush();
  assert.equal(f.calls.filter(row=>row.route==='resolve-preview').length,0);
  assert.equal(f.calls.filter(row=>row.route==='start').length,1);
});

test('hidden but unlocked readable assignment attachment remains selectable in the real decorate path',async()=>{
 const f=fixture();await flush();
 Object.assign(f.rows[0],{hidden:true,locked:false,name:'第一次作业.docx',size:15616});f.view.formats.push('docx');
 f.api.setContext({files:f.rows,courses:[{id:'1',name:'财务管理'}],courseId:'1'});await flush();
 const card=new Element('article'),content=new Element('div');content.className='study-file-content';card.append(content);f.body.append(card);
 f.api.decorate(card,f.rows[0]);assert.equal(card.querySelector('.ima-file-select').disabled,false);
});

test('merged groups restore raw-id selections and preview one real id after representative changes',async()=>{
 const f=fixture({files:2,stored:{selected:['1:2','1:3']}});await flush();
 const original=f.rows.map(row=>({...row}));Object.assign(f.rows[0],{groupId:'group-test',memberIds:['2','3'],members:original});f.rows.splice(1);
 f.api.setContext({files:f.rows,courses:[{id:'1',name:'财务管理'}],courseId:'1'});f.api.setRows(f.rows);await flush();
 assert.deepEqual(JSON.parse(f.storage.get('workbench-study-ima-v1')).selected,['1:group-test']);
 assert.match(f.body.querySelector('.ima-toolbar').textContent,/已选 1 份/);
 f.rows[0].id='3';f.api.setContext({files:f.rows,courses:[{id:'1',name:'财务管理'}],courseId:'1'});await flush();
 button(f,'保存到 ima').fire('click');await flush();await flush();
 assert.deepEqual(f.calls.filter(row=>row.route==='preview').at(-1).data.files,[{courseId:'1',fileId:'3'}]);
 assert.equal(model.key(f.rows[0]),'1:group-test');
});

test('group saved state recognizes any original member and same-name formats remain separate',()=>{
 const comparison={libraryId:'lib',rows:[{fileId:'3',status:'exists',mediaId:'saved'}]};
 const pdf={id:'2',courseId:'1',groupId:'pdf-group',memberIds:['2','3']};
 assert.equal(model.record(pdf,{'1':comparison},{'1':{libraryId:'lib'}}).status,'exists');
 assert.equal(model.record({...pdf,groupId:'word-group',memberIds:['4']},{'1':comparison},{'1':{libraryId:'lib'}}).status,'unchecked');
});

test('official ima default styling has a green fill, border and priority over generic workspace links',()=>{
 const css=fs.readFileSync(path.join(root,'assets/study-ima.css'),'utf8');
 assert.match(css,/#study-workspace a\.ima-official[^}]*background:color-mix/);
 assert.match(css,/#study-workspace a\.ima-official[^}]*border:1px solid color-mix/);
 assert.match(css,/#study-workspace a\.ima-official:hover/);
 assert.match(css,/#study-workspace a\.ima-official:focus-visible/);
});

test('root-linked courses explicitly show the root in both course row and save preview',async()=>{
 const f=fixture({stored:{selected:['1:2']}});await flush();
 Object.assign(f.view.links['1'],{folderId:'lib',path:'财务管理'});
 f.api.setRows(f.rows);await flush();
 assert.match(f.body.querySelector('.ima-link-label').textContent,/财务管理.*根目录/);
 button(f,'保存到 ima').fire('click');await flush();await flush();
 assert.match(f.body.querySelector('.ima-current-target').textContent,/根目录/);
 assert.match(f.body.querySelector('.ima-file-count').textContent,/本次 1 份资料.*财务管理/);
 assert.equal(f.calls.find(row=>row.route==='preview').data.folderId,'lib');
});

test('cross-course selection names both courses and one common destination without starting a job',async()=>{
 const f=fixture({files:2,stored:{selected:['1:2','2:3']}});await flush();
 Object.assign(f.rows[0],{courseName:'财务管理'});Object.assign(f.rows[1],{courseId:'2',courseName:'统计学'});
 f.view.links['2']={libraryId:'stats',folderId:'stats',path:'统计学'};
 f.api.setContext({files:f.rows,courses:[{id:'1',name:'财务管理'},{id:'2',name:'统计学'}],courseId:'1'});f.api.setRows(f.rows);await flush();
 button(f,'保存到 ima').fire('click');await flush();await flush();
 assert.match(f.body.querySelector('.ima-file-count').textContent,/本次 2 份资料.*2 门课程/);
 assert.match(f.body.querySelector('.ima-current-target').textContent,/全部保存到.*财务管理/);
 assert.match(f.body.querySelector('.ima-preview-files').textContent,/财务管理/);assert.match(f.body.querySelector('.ima-preview-files').textContent,/统计学/);
 const preview=f.calls.find(row=>row.route==='preview');assert.equal(preview.data.files.length,2);
 assert.equal(preview.data.libraryId,'lib');assert.equal(f.calls.some(row=>row.route==='start'),false);
});

test('a single selected course uses its own linked root even while browsing a different course',async()=>{
 const f=fixture({files:2,stored:{selected:['2:3']},librariesList:[{libraryId:'lib',name:'财务管理'},{libraryId:'stats',name:'统计学'}]});await flush();
 Object.assign(f.rows[1],{courseId:'2',courseName:'统计学'});f.view.links['2']={libraryId:'stats',folderId:'stats',path:'统计学'};
 f.api.setContext({files:f.rows,courses:[{id:'1',name:'财务管理'},{id:'2',name:'统计学'}],courseId:'1'});f.api.setRows([f.rows[0]]);await flush();
 button(f,'保存到 ima').fire('click');await flush();await flush();
 assert.match(f.body.querySelector('.ima-current-target').textContent,/统计学.*根目录/);
 assert.doesNotMatch(f.body.querySelector('.ima-current-target').textContent,/财务管理/);
 assert.match(f.body.querySelector('.ima-file-count').textContent,/本次 1 份资料.*统计学/);
 const preview=f.calls.find(row=>row.route==='preview');assert.equal(preview.data.libraryId,'stats');
 assert.equal(preview.data.folderId,'stats');assert.deepEqual(preview.data.files,[{courseId:'2',fileId:'3'}]);
});

test('other partitions all exceptional and stale states while preserving upload limitations as properties',()=>{
 const file={courseId:'1',id:'2'};
 const states=['review','failed','new_version','anomaly','unlinked','unchecked','unknown'];
 for(const status of states){
   assert.equal(model.statusGroup({status}),'other');assert.equal(model.matches(file,'other',{status},dates),true);
   assert.equal(model.matches(file,'exists',{status},dates),false);assert.equal(model.matches(file,'unsynced',{status},dates),false);
   assert.equal(model.matches(file,'all',{status},dates),true);
 }
 for(const status of ['exists','unsynced']){
   assert.equal(model.matches(file,'other',{status,stale:true},dates),true);
   assert.equal(model.matches(file,status,{status,stale:true},dates),false);
   assert.equal(model.matches(file,status,{status,supported:false},dates),true);
 }
 for(const mode of states)assert.equal(model.restoreFilter(mode),'other');
 assert.equal(model.restoreFilter('garbage'),'all');assert.equal(model.restoreFilter(undefined),'all');
 const comparisons={'1':{libraryId:'lib',complete:false,rows:[{fileId:'2',status:'exists'}]}};
 assert.equal(model.record(file,comparisons,{'1':{libraryId:'lib'}}).stale,true);
});

test('legacy exception modes migrate to other and leave unrelated view storage intact',async()=>{
 for(const filter of ['review','failed','new_version','anomaly','unlinked','unchecked']){
   const f=fixture({stored:{filter,selected:['1:2'],expanded:['job']}});await flush();
   assert.equal(f.api.mode(),'other');const saved=JSON.parse(f.storage.get('workbench-study-ima-v1'));
   assert.equal(saved.filter,'other');assert.deepEqual(saved.selected,['1:2']);assert.deepEqual(saved.expanded,['job']);
 }
 const code=fs.readFileSync(path.join(root,'assets/study-ima.js'),'utf8');
 assert.ok(!code.includes('workbench-study-view-v1'));
});

test('other in all courses uses each linked library and switching to a course preserves the dropdown',async()=>{
 const f=fixture({files:4});await flush();
 f.rows[1].courseId='2';f.rows[2].courseId='3';f.rows[3].courseId='4';
 f.comparison.rows=f.comparison.rows.slice(0,1);f.comparison.rows[0].status='exists';
 f.view.links['2']={libraryId:'second',folderId:'second',path:'第二门'};
 f.view.links['3']={libraryId:'third',folderId:'third',path:'第三门'};
 f.view.comparisons['2']={libraryId:'second',complete:true,rows:[{fileId:'3',status:'unsynced'}]};
 f.view.comparisons['3']={libraryId:'wrong',complete:true,rows:[{fileId:'4',status:'exists'}]};
 const courses=[{id:'1',name:'第一门'},{id:'2',name:'第二门'},{id:'3',name:'第三门'},{id:'4',name:'第四门'}];
 f.api.setContext({files:f.rows,courses,courseId:''});await flush();
 const select=f.body.querySelectorAll('select').find(row=>row.id==='study-file-state');select.value='other';select.fire('change');await flush();
 assert.deepEqual(f.api.filter(f.rows).map(row=>row.id),['4','5']);
 f.api.setContext({files:f.rows,courses,courseId:'1'});await flush();
 assert.equal(f.api.mode(),'other');assert.equal(select.value,'other');assert.equal(f.api.filter([f.rows[0]]).length,0);
});

test('an unmatched row can show the reason in other without duplicating normal saved copy',async()=>{
 const f=fixture();await flush();delete f.view.links['1'];
 f.listeners.visibilitychange();await flush();await flush();
 const card=new Element('article'),content=new Element('div');content.className='study-file-content';card.append(content);f.body.append(card);
 f.api.decorate(card,f.rows[0]);assert.equal(card.querySelector('.ima-file-state').hidden,true);
 const select=f.body.querySelectorAll('select').find(row=>row.id==='study-file-state');select.value='other';select.fire('change');await flush();
 f.api.decorate(card,f.rows[0]);assert.equal(card.querySelector('.ima-file-state').hidden,false);assert.match(card.textContent,/尚未选择保存位置/);
});

test('more in all courses requires an explicit scope and returning from a child preserves selection and filters',async()=>{
 const f=fixture({stored:{filter:'other',selected:['1:2']}});await flush();
 f.api.setContext({files:f.rows,courses:[{id:'1',name:'财务管理'}],courseId:''});await flush();
 button(f,'更多').fire('click');await flush();const original=f.body.querySelector('dialog'),course=original.querySelector('select');
 assert.equal(course.value,'');assert.equal(button(f,'设置保存位置').disabled,true);assert.equal(button(f,'刷新 ima 状态').disabled,true);
 course.value='1';course.fire('change');assert.equal(button(f,'更改保存位置').disabled,false);
 button(f,'更改保存位置').fire('click');await flush();await flush();
 assert.equal(f.body.querySelectorAll('dialog').length,1);assert.match(f.body.querySelector('dialog').textContent,/选择这门课的保存位置/);
 button(f,'返回更多').fire('click');await flush();assert.match(f.body.querySelector('dialog').textContent,/更多操作/);
 assert.equal(f.body.querySelector('dialog').querySelector('select').value,'1');
 assert.equal(f.api.mode(),'other');assert.deepEqual(JSON.parse(f.storage.get('workbench-study-ima-v1')).selected,['1:2']);
 button(f,'关闭').fire('click');assert.equal(f.body.querySelectorAll('dialog').length,0);
 assert.equal(button(f,'更多').ownerDocument.activeElement,button(f,'更多'));
});

test('closing more during a scoped refresh keeps its result in the correct course and never reopens the dialog',async()=>{
 const f=fixture();await flush();let finish;const gate=new Promise(resolve=>finish=resolve);f.setCheckGate(gate);
 button(f,'更多').fire('click');await flush();const modal=f.body.querySelector('dialog');
 button(f,'刷新 ima 状态').fire('click');modal.close();await flush();
 assert.equal(f.body.querySelector('dialog'),null);finish();await flush();await flush();
 assert.equal(f.body.querySelector('dialog'),null);assert.ok(f.calls.some(row=>row.route==='check'&&row.data.courseId==='1'));
 button(f,'更多').fire('click');await flush();assert.match(f.body.querySelector('dialog').textContent,/最近核对/);
 f.view.job={id:'later',phase:'complete',rows:[{source:{courseId:'1',fileName:'新结果.pdf'},status:'skipped'}]};
 f.listeners.visibilitychange();await flush();await flush();assert.match(f.body.querySelector('.ima-history').textContent,/新结果.pdf/);
});

test('cancel only clears selections while running progress and pending recovery remain present',async()=>{
 const job={id:'active',phase:'running',target:{path:'财务管理/课件PPT'},rows:[{source:{courseId:'1',fileName:'A.pdf'},status:'running'}]};
 const f=fixture({job,stored:{selected:['1:2']}});await flush();
 assert.equal(button(f,'保存到 ima').disabled,true);assert.equal(button(f,'取消').disabled,false);
 assert.equal(f.body.querySelector('.ima-selection').querySelector('input').disabled,true);
 button(f,'取消').fire('click');await flush();assert.equal(f.view.job.phase,'running');assert.match(f.body.querySelector('.ima-job').textContent,/已处理 0\/1/);
 assert.equal(f.calls.some(row=>row.route==='start'||row.route==='retry'),false);
});

test('four real dropdowns share one grid and permanent action styles provide green and red accessible controls',()=>{
 const config=fs.readFileSync(path.join(root,'config/glance.yml'),'utf8');
 const options=config.slice(config.indexOf('<div class="study-file-options">'),config.indexOf('</div>',config.indexOf('<div class="study-file-options">')));
 for(const id of ['study-file-course','study-file-type','study-file-sort','study-file-state'])assert.ok(options.includes('id="'+id+'"'));
 const css=fs.readFileSync(path.join(root,'assets/study-ima.css'),'utf8');
 assert.match(css,/\.ima-link-label[^}]*font-size:1.25rem/);
 assert.match(css,/\.ima-cancel[^}]*--study-danger/);assert.match(css,/\.ima-save[^}]*--study-success/);
 assert.match(css,/\.ima-action[^}]*border-radius:7px/);assert.match(css,/\.ima-action[^}]*width:112px/);assert.match(css,/\.ima-action:disabled/);assert.match(css,/:focus-visible/);
 const study=fs.readFileSync(path.join(root,'assets/study.css'),'utf8');assert.match(study,/\.study-file-options[^}]*repeat\(4/);assert.match(study,/@media \(max-width: 600px\)[^}]*repeat\(2/);
});

test('returning from directory management during a failed read does not put the old error into more',async()=>{
 let rejectRead;const gate=new Promise((_resolve,reject)=>rejectRead=reject);
 const f=fixture({treeGate:gate});await flush();
 button(f,'更多').fire('click');await flush();button(f,'目录管理').fire('click');await flush();await flush();
 assert.match(f.body.querySelector('dialog').textContent,/ima 目录与归档/);
 button(f,'返回更多').fire('click');await flush();
 assert.match(f.body.querySelector('dialog').textContent,/更多操作/);
 rejectRead(new Error('旧目录读取失败'));await flush();await flush();
 assert.equal(f.body.querySelector('.ima-dialog-feedback').textContent,'');
 assert.equal(f.body.querySelector('.ima-warning').hidden,true);
 assert.equal(f.body.querySelectorAll('dialog').length,1);
 assert.equal(button(f,'更多').getAttribute('aria-expanded'),'true');
 button(f,'关闭').fire('click');await flush();assert.equal(button(f,'更多').getAttribute('aria-expanded'),'false');
});

test('Esc follows the native cancel lifecycle and preserves filters, selections and return focus',async()=>{
 const f=fixture({stored:{filter:'other',selected:['1:2']}});await flush();
 const trigger=button(f,'更多');trigger.focus();trigger.fire('click');await flush();
 const modal=f.body.querySelector('dialog');modal.escape();await flush();
 assert.equal(f.body.querySelector('dialog'),null);assert.equal(trigger.ownerDocument.activeElement,trigger);
 assert.equal(f.api.mode(),'other');assert.deepEqual(JSON.parse(f.storage.get('workbench-study-ima-v1')).selected,['1:2']);
});

test('more scoped course operations never replace the visible course when source context changes',async()=>{
 const f=fixture();await flush();
 const courses=[{id:'1',name:'财务管理'},{id:'2',name:'另一课程'}];
 f.api.setContext({files:f.rows,courses,courseId:'1'});await flush();button(f,'更多').fire('click');await flush();
 const modal=f.body.querySelector('dialog');assert.equal(modal.querySelector('select').value,'1');
 f.view.links['2']={libraryId:'other',folderId:'other',path:'另一课程'};
 f.api.setContext({files:f.rows,courses,courseId:'2'});await flush();
 assert.equal(modal.querySelector('select').value,'1');
 button(f,'刷新 ima 状态').fire('click');await flush();await flush();
 assert.equal(f.calls.filter(row=>row.route==='check').at(-1).data.courseId,'1');
 assert.match(f.body.querySelector('.ima-link-label').textContent,/另一课程/);
});

test('more can close and reopen during a late failed check without losing focus or inheriting its error',async()=>{
 const f=fixture();await flush();let rejectRead;const gate=new Promise((_resolve,reject)=>rejectRead=reject);f.setCheckGate(gate);
 const trigger=button(f,'更多');trigger.focus();trigger.fire('click');await flush();
 button(f,'刷新 ima 状态').fire('click');await flush();f.body.querySelector('dialog').close();await flush();
 assert.equal(trigger.disabled,false);assert.equal(trigger.ownerDocument.activeElement,trigger);
 trigger.fire('click');await flush();assert.ok(f.body.querySelector('dialog'));
 rejectRead(new Error('已关闭窗口的核对失败'));await flush();await flush();
 assert.equal(f.body.querySelector('.ima-dialog-feedback').textContent,'');assert.equal(f.body.querySelector('.ima-warning').hidden,false);assert.match(f.body.querySelector('dialog').textContent,/上次结果，待重新核对/);
 assert.equal(f.api.filter(f.rows).length,f.rows.length);
 assert.equal(button(f,'刷新 ima 状态').disabled,false);
});

function attachSelectableCards(f){
 const cards=f.rows.map(file=>{
   const card=new Element('article'),content=new Element('div');content.className='study-file-content';card.append(content);f.body.append(card);
   return {card,file};
 });
 const repaint=()=>{f.api.setContext({files:f.rows,courses:[{id:'1',name:'财务管理'}],courseId:'1'});f.api.setRows(f.api.filter(f.rows));for(const row of cards)f.api.decorate(row.card,row.file);};
 f.api.mount(repaint);repaint();
 return {repaint,boxes:cards.map(row=>row.card.querySelector('.ima-file-select'))};
}

test('cancel regression after completion restores a real decorated checkbox without a later repaint',async()=>{
 const f=fixture({files:2});await flush();const {boxes,repaint}=attachSelectableCards(f);
 assert.equal(boxes[0].click(),true);assert.equal(boxes[0].checked,true);
 button(f,'取消').click();await flush();
 const observed={selectedCount:JSON.parse(f.storage.get('workbench-study-ima-v1')).selected.length,
   checkboxDisabledAfterCancel:boxes[1].disabled,saveDisabled:button(f,'保存到 ima').disabled};
 repaint();observed.checkboxDisabledAfterNextRepaint=boxes[1].disabled;
 assert.equal(observed.selectedCount,0);assert.equal(observed.checkboxDisabledAfterNextRepaint,false);
 assert.equal(observed.checkboxDisabledAfterCancel,false,JSON.stringify(observed));
});

test('cancel regression clears selection synchronously and permits another checkbox in the same turn',async()=>{
 const f=fixture({files:2});await flush();const {boxes}=attachSelectableCards(f);
 for(let index=0;index<5;index++){
   assert.equal(boxes[0].click(),true);assert.equal(button(f,'取消').click(),true);
   assert.equal(boxes[1].disabled,false);assert.equal(boxes[1].click(),true);
   assert.deepEqual(JSON.parse(f.storage.get('workbench-study-ima-v1')).selected,['1:3']);
   assert.equal(button(f,'取消').click(),true);
 }
});

test('cancel recovery supports select-all, partial selection and Shift repeatedly without repaint workarounds',async()=>{
 const f=fixture({files:4});await flush();const {boxes}=attachSelectableCards(f),all=f.body.querySelector('.ima-selection').querySelector('input');
 for(let run=0;run<4;run++){
   assert.equal(all.click(),true);assert.equal(all.checked,true);assert.equal(all.indeterminate,false);
   assert.equal(button(f,'取消').click(),true);assert.equal(all.disabled,false);assert.equal(all.checked,false);
   assert.ok(boxes.every(box=>!box.disabled&&!box.checked));
   assert.equal(boxes[0].click(),true);assert.equal(all.indeterminate,true);
   assert.equal(boxes[2].click({shiftKey:true}),true);
   assert.deepEqual(JSON.parse(f.storage.get('workbench-study-ima-v1')).selected,['1:2','1:3','1:4']);
   button(f,'取消').click();assert.equal(boxes[3].click({shiftKey:true}),true);
   assert.deepEqual(JSON.parse(f.storage.get('workbench-study-ima-v1')).selected,['1:5']);
   button(f,'取消').click();
 }
 assert.equal(button(f,'保存到 ima').disabled,true);assert.equal(button(f,'取消').disabled,true);
});

test('cancel after changing the filtered range clears outside selections and immediately enables the visible choice',async()=>{
 const f=fixture({files:3});await flush();const {boxes}=attachSelectableCards(f);
 boxes[0].click();f.api.setRows(f.rows.slice(1));
 assert.match(f.body.querySelector('.ima-selected-count').textContent,/已选 1 份.*筛选外 1 份/);
 button(f,'取消').click();assert.equal(button(f,'保存到 ima').disabled,true);
 assert.equal(f.body.querySelector('.ima-selected-count').hidden,true);assert.equal(boxes[1].click(),true);
 assert.equal(button(f,'保存到 ima').disabled,false);assert.deepEqual(JSON.parse(f.storage.get('workbench-study-ima-v1')).selected,['1:3']);
});

test('both successful and failed async checks restore file checkboxes at action completion while preserving real restrictions',async()=>{
 for(const failed of [false,true]){
   const f=fixture({files:3});await flush();f.rows[2].locked=true;
   const {boxes}=attachSelectableCards(f);boxes[0].click();
   let finish,reject;f.setCheckGate(new Promise((resolve,fail)=>{finish=resolve;reject=fail;}));
   button(f,'更多').click();await flush();button(f,'刷新 ima 状态').click();await flush();
   assert.equal(boxes[0].disabled,false);assert.equal(boxes[1].disabled,false);
   assert.equal(f.body.querySelector('.ima-selection').querySelector('input').disabled,false);
   if(failed)reject(new Error('合成读取失败'));else finish();await flush();await flush();
   assert.equal(boxes[0].disabled,false);assert.equal(boxes[1].disabled,false);assert.equal(boxes[2].disabled,true);
   assert.equal(f.body.querySelector('.ima-selection').querySelector('input').disabled,false);
   assert.equal(button(f,'保存到 ima').disabled,false);
   button(f,'关闭').click();assert.equal(boxes[1].click(),true);
   assert.deepEqual(JSON.parse(f.storage.get('workbench-study-ima-v1')).selected,['1:2','1:3']);
 }
});

test('cancel clears choices during a real running task and task completion restores supported files only',async()=>{
 const job={id:'real-job',phase:'running',target:{path:'财务管理/课件PPT'},rows:[{source:{courseId:'1',fileName:'A.pdf'},status:'running'}]};
 const f=fixture({files:3,job,stored:{selected:['1:2']}});await flush();f.rows[2].size=21*1024*1024;
 const {boxes}=attachSelectableCards(f);assert.ok(boxes.every(box=>box.disabled));
 button(f,'取消').click();assert.equal(f.view.job.phase,'running');assert.equal(boxes[1].disabled,true);
 f.view.job={...job,phase:'complete',rows:[{source:{courseId:'1',fileName:'A.pdf'},status:'success'}]};
 f.listeners.visibilitychange();await flush();await flush();
 assert.equal(boxes[0].disabled,false);assert.equal(boxes[1].disabled,false);assert.equal(boxes[2].disabled,true);
 assert.equal(boxes[1].click(),true);assert.equal(button(f,'保存到 ima').disabled,false);
});

test('plain checkbox selection and local cancel do not enter the async lock or start automatic checks',async()=>{
 const f=fixture({files:2});await flush();const {boxes}=attachSelectableCards(f),before=f.calls.length;
 boxes[0].click();button(f,'取消').click();boxes[1].click();button(f,'取消').click();
 assert.equal(f.calls.length,before);assert.ok(boxes.every(box=>!box.disabled));
});

test('more operations use visible neutral borders with stronger selectors than the generic dialog text-button rule',()=>{
 const css=fs.readFileSync(path.join(root,'assets/study-ima.css'),'utf8');
 const base='.ima-dialog .ima-more-actions .study-text-button';
 const rule=css.slice(css.indexOf(base+' {'),css.indexOf('}',css.indexOf(base+' {')));
 assert.match(rule,/border:1px solid #929298/);assert.match(rule,/background:#303034/);assert.match(rule,/color:#e4e4e7/);
 assert.match(rule,/min-height:36px/);assert.match(rule,/padding:8px 12px/);assert.match(rule,/border-radius:6px/);
 for(const suffix of [':hover:not(:disabled)',':active:not(:disabled)',':focus-visible',':disabled'])assert.ok(css.includes(base+suffix));
 assert.ok(css.indexOf(base+' {')>css.indexOf('.ima-dialog .study-text-button {'));
 assert.doesNotMatch(css.slice(css.indexOf(base+' {'),css.indexOf('.ima-more-button:focus-visible')),/--study-success|--study-danger|#dcc48a/);
});

test('picker keeps new folder input on demand and history details collapsed',async()=>{
 const f=fixture({stored:{selected:['1:2']}});await flush();button(f,'更多').click();
 assert.equal(Boolean(f.body.querySelector('.ima-history').querySelector('details').open),false);
 button(f,'更改保存位置').click();await flush();await flush();
 const modal=f.body.querySelector('dialog'),form=modal.querySelector('.ima-create');
 assert.equal(form.hidden,true);assert.doesNotMatch(modal.textContent,/逐层进入|没有子文件夹|进入文件夹后|设置后会记住/);
 button(f,'新建文件夹').click();assert.equal(form.hidden,false);
 button(f,'创建').click();await flush();assert.match(form.textContent,/请输入文件夹名称/);
 assert.equal(f.calls.some(row=>row.route==='folder'),false);
});

test('a hidden cached target needs a visible explicit selection before preview or save',async()=>{
 const f=fixture({stored:{selected:['1:2']},treeEntries:[{kind:'folder',folderId:'folder',parentFolderId:'lib',name:'owned',isTestDirectory:true}]});await flush();
 button(f,'保存到 ima').click();await flush();await flush();
 assert.equal(button(f,'确认保存').disabled,true);assert.equal(f.calls.some(row=>row.route==='preview'),false);
 assert.doesNotMatch(f.body.querySelector('.ima-current-target').textContent,/课件PPT/);
 button(f,'更改').click();await flush();await flush();assert.match(f.body.querySelector('.ima-breadcrumbs').textContent,/根目录/);
 assert.doesNotMatch(f.body.querySelector('.ima-folder-list').textContent,/owned/);
 button(f,'使用此位置').click();await flush();await flush();
 assert.equal(f.calls.filter(row=>row.route==='preview').at(-1).data.folderId,'lib');
 assert.equal(f.calls.some(row=>row.route==='start'),false);
});

test('directory refresh retains a valid path and selection but prunes newly hidden objects',async()=>{
 const entries=[{kind:'folder',folderId:'a',parentFolderId:'lib',name:'A'},
 {kind:'file',mediaId:'root-file',parentFolderId:'lib',name:'root.pdf'},
 {kind:'file',mediaId:'a-file',parentFolderId:'a',name:'a.pdf'}];
 const f=fixture({treeEntries:entries});await flush();button(f,'更多').click();button(f,'目录管理').click();await flush();await flush();
 const modal=f.body.querySelector('dialog');modal.querySelector('.ima-cloud-file').querySelector('input').click();
 button(f,'▸ A').click();assert.match(modal.textContent,/目录外 1 份/);modal.querySelector('.ima-cloud-file').querySelector('input').click();
 button(f,'刷新目录').click();await flush();await flush();assert.match(modal.querySelector('.ima-breadcrumbs').textContent,/A/);
 entries[0].isTestDirectory=true;button(f,'刷新目录').click();await flush();await flush();
 assert.match(modal.textContent,/原位置不可用，已返回根目录/);assert.doesNotMatch(modal.querySelector('.ima-cloud-list').textContent,/a.pdf/);
 assert.match(modal.textContent,/当前目录 1 份 · 已选 1 份/);
});

test('directory partial and failed reads block stale targets and support a local retry',async()=>{
 const f=fixture();await flush();button(f,'更多').click();button(f,'目录管理').click();await flush();await flush();
 f.setTreeComplete(false);button(f,'刷新目录').click();await flush();await flush();
 const modal=f.body.querySelector('dialog');assert.match(modal.textContent,/目录读取未完成.*上次读取/);
 assert.equal(button(f,'新建文件夹').disabled,true);assert.equal(button(f,'上一级').disabled,true);
 f.setTreeComplete(true);f.setFailure('tree');button(f,'刷新目录').click();await flush();await flush();assert.match(modal.textContent,/合成目录操作失败/);
 f.setFailure(null);button(f,'刷新目录').click();await flush();await flush();assert.equal(button(f,'新建文件夹').disabled,false);
});

test('directory library read failures retain a working retry and returning focus',async()=>{
 const f=fixture({failRoute:'libraries'});await flush();button(f,'更多').click();button(f,'目录管理').click();await flush();await flush();
 assert.match(f.body.querySelector('dialog').textContent,/合成目录操作失败/);assert.equal(button(f,'刷新目录').disabled,false);
 f.setFailure(null);button(f,'刷新目录').click();await flush();await flush();assert.ok(button(f,'▸ 课件PPT'));
 button(f,'返回更多').click();assert.match(f.body.querySelector('dialog').textContent,/更多操作/);
 button(f,'关闭').click();assert.equal(button(f,'更多').ownerDocument.activeElement,button(f,'更多'));
});

test('refresh feedback follows the selected course across close reopen and late failure',async()=>{
 const f=fixture();await flush();let reject;
 f.setCheckGate(new Promise((_resolve,fail)=>reject=fail));button(f,'更多').click();button(f,'刷新 ima 状态').click();
 const original=f.body.querySelector('dialog');original.close();button(f,'更多').click();
 assert.equal(f.body.querySelector('.ima-refresh-button').getAttribute('aria-busy'),'true');
 reject(new Error('本课程核对失败'));await flush();await flush();
 assert.match(f.body.querySelector('.ima-refresh-feedback').textContent,/本课程核对失败/);
 assert.equal(button(f,'刷新 ima 状态').disabled,false);assert.equal(f.body.querySelector('.ima-dialog-feedback').textContent,'');
});

test('refresh success stays in its course when more switches course during the request',async()=>{
 const f=fixture();await flush();const courses=[{id:'1',name:'财务管理'},{id:'2',name:'统计学'}];
 f.view.links['2']={libraryId:'second',folderId:'second',path:'统计学'};
 f.api.setContext({files:f.rows,courses,courseId:'1'});let finish;f.setCheckGate(new Promise(resolve=>finish=resolve));
 button(f,'更多').click();button(f,'刷新 ima 状态').click();const course=f.body.querySelector('dialog').querySelector('select');
 course.value='2';course.fire('change');assert.equal(f.body.querySelector('.ima-refresh-button').getAttribute('aria-busy'),'false');
 finish();await flush();await flush();assert.equal(f.body.querySelector('.ima-refresh-feedback').textContent,'');
 course.value='1';course.fire('change');assert.match(f.body.querySelector('.ima-refresh-feedback').textContent,/已刷新/);
});

test('directory loading is isolated across libraries and a late old tree cannot replace the new one',async()=>{
 let finish;const gate=new Promise(resolve=>finish=resolve);
 const f=fixture({treeGate:gate,librariesList:[{libraryId:'lib',name:'财务管理'},{libraryId:'second',name:'统计学'}],treesByLibrary:{
 lib:[{kind:'folder',folderId:'first',parentFolderId:'lib',name:'旧目录'}],second:[{kind:'folder',folderId:'second-a',parentFolderId:'second',name:'新目录'}]}});
 await flush();button(f,'更多').click();button(f,'目录管理').click();await flush();await flush();
 const modal=f.body.querySelector('dialog'),library=modal.querySelector('select');assert.equal(modal.querySelector('.ima-cloud-list').getAttribute('aria-busy'),'true');
 library.value='second';library.fire('change');await flush();finish();await flush();await flush();
 assert.match(modal.querySelector('.ima-cloud-list').textContent,/新目录/);assert.doesNotMatch(modal.querySelector('.ima-cloud-list').textContent,/旧目录/);
 assert.match(modal.querySelector('.ima-breadcrumbs').textContent,/统计学.*根目录/);
});

test('move destination shares hidden filtering and confirms root by its actual id',async()=>{
 const f=fixture({treeEntries:[{kind:'file',mediaId:'real',parentFolderId:'lib',name:'real.pdf'},
 {kind:'folder',folderId:'hidden',parentFolderId:'lib',name:'owned',isTestDirectory:true}]});await flush();
 button(f,'更多').click();button(f,'目录管理').click();await flush();await flush();
 f.body.querySelector('.ima-cloud-file').querySelector('input').click();button(f,'移动所选文件').click();await flush();await flush();
 button(f,'更改').click();await flush();await flush();
 assert.doesNotMatch(f.body.querySelectorAll('.ima-folder-list').at(-1).textContent,/owned/);
 button(f,'使用此位置').click();await flush();await flush();
 const preview=f.calls.filter(row=>row.route==='move-preview').at(-1);assert.equal(preview.data.folderId,'lib');assert.deepEqual(preview.data.mediaIds,['real']);
 assert.equal(f.calls.some(row=>row.route==='start'),false);
});

test('refresh CSS reserves stable feedback and supports reduced motion',()=>{
 const css=fs.readFileSync(path.join(root,'assets/study-ima.css'),'utf8');
 assert.match(css,/\.ima-refresh-button[^}]*min-width:132px/);assert.match(css,/\.ima-refresh-feedback[^}]*min-height/);
 assert.match(css,/@media\(prefers-reduced-motion:reduce\)\s*\{\.ima-spinner\s*\{animation:none/);
 assert.match(css,/\.ima-folder-list,\.ima-cloud-list[^}]*min-height:100px/);
});

test('removed material information is never mounted again during refresh or course switches',async()=>{
 const f=fixture();await flush();const {boxes}=attachSelectableCards(f);
 for(const courseId of ['', '1', '', '1']){
   f.api.setContext({files:f.rows,courses:[{id:'1',name:'财务管理'}],courseId});f.api.setRows(f.rows);await flush();
   f.listeners.visibilitychange();await flush();await flush();
   assert.equal(f.body.querySelector('.ima-normal-info'),null);
   assert.doesNotMatch(f.body.textContent,/资料信息|ima最近核对|ima保存状态按每份资料对应课程的关联库核对/);
 }
 boxes[0].click();assert.equal(button(f,'保存到 ima').disabled,false);button(f,'取消').click();
 boxes[1].click();assert.equal(boxes[1].checked,true);button(f,'更多').click();assert.ok(button(f,'刷新 ima 状态'));
});

test('compact material information preserves stale check errors and the retry entry',async()=>{
 const f=fixture({failRoute:'check'});await flush();await flush();
 const warning=f.body.querySelector('.ima-warning');assert.equal(warning.hidden,false);
 assert.match(warning.textContent,/合成目录操作失败/);assert.ok(button(f,'重试检查'));
 assert.doesNotMatch(f.body.textContent,/ima保存状态按每份资料对应课程的关联库核对/);
});


test('late initial library read cannot clear the newer directory loading indicator',async()=>{
 let finishFirst,finishSecond;
 const f=fixture({librariesList:[{libraryId:'lib',name:'财务管理'},{libraryId:'second',name:'统计学'}],
 treeGatesByLibrary:{lib:new Promise(resolve=>finishFirst=resolve),second:new Promise(resolve=>finishSecond=resolve)},
 treesByLibrary:{lib:[],second:[{kind:'folder',folderId:'new',parentFolderId:'second',name:'新目录'}]}});
 await flush();button(f,'更多').click();button(f,'目录管理').click();await flush();await flush();
 const modal=f.body.querySelector('dialog'),library=modal.querySelector('select');library.value='second';library.fire('change');await flush();
 finishFirst();await flush();await flush();assert.equal(modal.querySelector('.ima-cloud-list').getAttribute('aria-busy'),'true');
 assert.equal(button(f,'新建文件夹').disabled,true);finishSecond();await flush();await flush();
 assert.equal(modal.querySelector('.ima-cloud-list').getAttribute('aria-busy'),'false');assert.ok(button(f,'▸ 新目录'));
});
