/* Controlled dates + actual production card renderer in a small DOM fixture.
 * No browser or desktop automation, and no school requests.
 */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const dates = require('../assets/study-material-dates.js');
const root = path.resolve(__dirname, '..');
const current = '2026-11-10T10:00:00+08:00';
const at = Date.parse(current);
const describe = createdAt => dates.describe({createdAt}, null, at).upload;

test('today, yesterday and older use Shanghai calendar days, not elapsed 24-hour periods', () => {
  assert.equal(describe('2026-11-10T00:01:00+08:00').label, '今天创建');
  assert.equal(describe('2026-11-09T23:59:00+08:00').label, '昨天创建');
  assert.equal(describe('2026-11-07T23:59:00+08:00').label, '3 天前创建');
  assert.equal(describe('2026-11-09T16:00:00Z').date, '2026/11/10');
  assert.equal(describe('2026-11-09T15:59:59Z').label, '昨天创建');
});

test('Shanghai midnight changes the label after one second and handles year/leap boundaries', () => {
  const file = {createdAt: '2026-11-09T23:59:59+08:00'};
  assert.equal(dates.describe(file, null, Date.parse(file.createdAt)).upload.label, '今天创建');
  assert.equal(dates.describe(file, null, Date.parse('2026-11-10T00:00:00+08:00')).upload.label, '昨天创建');
  assert.equal(dates.describe({createdAt:'2025-12-31T23:00:00+08:00'}, null, Date.parse('2026-01-01T00:00:00+08:00')).upload.label,'昨天创建');
  assert.equal(dates.describe({createdAt:'2024-02-28T23:00:00+08:00'}, null, Date.parse('2024-03-01T00:00:00+08:00')).upload.label,'2 天前创建');
});

test('missing, invalid and future values never borrow update or sync dates', () => {
  for (const value of [null, undefined, '']) assert.equal(describe(value).label, '记录日期未提供');
  for (const value of ['nonsense','2026-02-30T00:00:00Z','2026-11-10','2026-11-10T01:00:00', '2026-11-10T24:00:00Z', '2026-11-10T00:00:00+25:00', 0, 1790000000000]) {
    assert.equal(describe(value).status, 'invalid', String(value));
    assert.equal(describe(value).datetime, null);
  }
  for (const value of ['2026-11-10T10:00:01+08:00','2026-11-11T00:00:00+08:00']) {
    assert.equal(describe(value).status, 'future');
    assert.equal(describe(value).label, '记录日期异常');
  }
  const result = dates.describe({createdAt:null,updatedAt:current}, current, at);
  assert.equal(result.upload.date,null);
  assert.equal(result.update.date,'2026/11/10');
});

test('upload and update remain distinct, duplicate same-day timestamps are suppressed', () => {
  const file = {createdAt:'2026-11-09T08:00:00+08:00'};
  assert.equal(dates.describe(file, '2026-11-09T22:00:00+08:00', at).update,null);
  assert.equal(dates.describe(file, '2026-11-08T22:00:00+08:00', at).update,null);
  assert.equal(dates.describe(file, '2026-11-11T22:00:00+08:00', at).update,null);
  const result=dates.describe(file,current,at);
  assert.equal(result.upload.label,'昨天创建');
  assert.equal(result.update.date,'2026/11/10');
  assert.equal(describe('2026-11-07T00:00:00+08:00').recent,false);
});

class Text {
  constructor(value) { this.textContent = String(value); }
}
class Element {
  constructor(tag) { this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.style={};this._text='';this.hidden=false;this.listeners={};this.isConnected=true; }
  set textContent(value) { this._text=String(value);this.children=[]; }
  get textContent() { return this._text+this.children.map(child=>child.textContent).join(''); }
  append(...children) { for(let child of children){if(typeof child==='string')child=new Text(child);child.remove?.();child.parentNode=this;this.children.push(child);} }
  replaceChildren(...children) { for(const child of this.children)child.parentNode=null;this.children=[];this._text='';this.append(...children); }
  remove() { if(this.parentNode){const p=this.parentNode;p.children=p.children.filter(child=>child!==this);this.parentNode=null;} }
  insertBefore(child, reference) { child.remove?.();const index=reference?this.children.indexOf(reference):this.children.length;this.children.splice(index,0,child);child.parentNode=this; }
  replaceWith(child) { const p=this.parentNode;const index=p.children.indexOf(this);p.children[index]=child;child.parentNode=p;this.parentNode=null; }
  setAttribute(key,value) { this[key]=value; }
  getAttribute(key) { return this[key]; }
  addEventListener(name, callback) { (this.listeners[name] ??= []).push(callback); }
  fire(name) { for (const callback of this.listeners[name] || []) callback({target:this}); }
  matches(selector) { return selector.startsWith('.')?(this.className||'').split(' ').includes(selector.slice(1)):this.tagName===selector.toUpperCase(); }
  querySelectorAll(selector) { return this.children.flatMap(child=>[...(child.matches?.(selector)?[child]:[]),...(child.querySelectorAll?.(selector)||[])]); }
  querySelector(selector) { return this.querySelectorAll(selector)[0]||null; }
  get firstElementChild() { return this.children[0]||null; }
  get nextElementSibling() { const p=this.parentNode;return p?p.children[p.children.indexOf(this)+1]||null:null; }
}
function fixture(stored = null) {
  let now=at;
  let storage = stored === null ? null : JSON.stringify(stored);
  class Clock extends Date { static now(){return now;} }
  const ids={};
  for(const id of ['study-workspace','study-files','study-files-count','study-files-more','study-file-result','study-search-clear'])ids[id]=new Element('div');
  const listeners={};
  const document={visibilityState:'visible',documentElement:{},createElement:tag=>new Element(tag),createTextNode:text=>new Text(text),getElementById:id=>['study-material-info','study-materials-status','study-material-warning','study-material-warning-label','study-material-warning-body'].includes(id)?null:ids[id]??(ids[id]=new Element('div')),addEventListener:(name,callback)=>listeners[name]=callback};
  const scroll=[];const fetchCalls=[];
  const windowListeners={};
  const window={dispatchEvent(){},scrollY:420,addEventListener:(name,callback)=>windowListeners[name]=callback,scrollBy:(...args)=>scroll.push(args),scrollTo:(...args)=>scroll.push(args)};
  let source=fs.readFileSync(path.join(root,'assets/study.js'),'utf8');
  source=source.replace(/  start\(\);\s*\}\)\(\);\s*$/, `  globalThis.testView={materialCard,fileDate,renderMaterials,refreshMaterialDates,allAssignments,sourceStatus,
    setData(value){canvas=value;root=document.getElementById('study-workspace');},setFilter(value){Object.assign(view,value);},getView(){return view;},
    boot(){renderCanvas=()=>{};renderSchool=()=>{};load=async()=>{};loadAcademic=async()=>{};loadSharedMarks=async()=>{};start();}};\n})();`);
  const context=vm.createContext({document,window,Date:Clock,URL,CustomEvent:class{constructor(type,options){this.type=type;this.detail=options?.detail;}},StudyMaterialDates:dates,StudyFileTypes:require('../assets/study-file-types.js'),StudyOffline:require('../assets/study-offline.js'),
    sessionStorage:{getItem:()=>storage,setItem:(_key,value)=>storage=value},MutationObserver:class{observe(){}},
    fetch:async(...args)=>{fetchCalls.push(args);throw new Error('no date refresh request allowed');},setInterval:()=>1,clearInterval(){}});
  vm.runInContext(source,context);
  return {api:context.testView,ids,document,window,listeners,windowListeners,scroll,fetchCalls,getStored:()=>JSON.parse(storage),setNow:value=>now=Date.parse(value)};
}
test('type filtering combines course, search and save-status results without clearing reading state',()=>{
  const f=fixture({fileType:'word',sort:'name',course:'1',query:'作业',fileLimit:18,scrollY:420});
  const rows=[
    {id:'1',courseId:'1',name:'作业.pdf',url:'https://canvas.shufe.edu.cn/courses/1/files/1',size:100},
    {id:'2',courseId:'1',name:'作业.docx',url:'https://canvas.shufe.edu.cn/courses/1/files/2',size:100},
    {id:'3',courseId:'2',name:'作业.docx',url:'https://canvas.shufe.edu.cn/courses/2/files/3',size:100},
    {id:'4',courseId:'1',name:'课件.docx',url:'https://canvas.shufe.edu.cn/courses/1/files/4',size:100},
    {id:'5',courseId:'1',name:'作业.csv',url:'https://canvas.shufe.edu.cn/courses/1/files/5',size:100},
  ];
  const ima=require('../assets/study-ima-model.js');
  f.window.StudyIma={mount(){},setContext(){},setRows(){},decorate(){},filter:rows=>rows.filter(file=>ima.matches(file,'unsynced',{status:file.id==='1'?'exists':'unsynced'},dates))};
  f.api.setData({meta:{available:true},data:{files:rows,courses:[{id:'1',name:'课程'}]}});
  f.api.renderMaterials();
  assert.equal(f.ids['study-files'].children.length,1);
  assert.match(f.ids['study-files'].textContent,/作业.docx/);
  assert.match(f.ids['study-files'].querySelector('.study-file-type').className,/is-word/);
  f.api.boot();f.ids['study-file-type'].value='all';f.ids['study-file-type'].fire('change');
  assert.equal(f.ids['study-files'].children.length,2);
  assert.equal(f.getStored().course,'1');assert.equal(f.getStored().query,'作业');
  assert.equal(f.getStored().fileLimit,18);assert.equal(f.getStored().fileType,'all');
  assert.equal(f.getStored().sort,'name');assert.equal(f.getStored().scrollY,420);
});
test('official assignment attachments remain materials without creating duplicate offline tasks',()=>{
  const f=fixture();
  const assignment={id:'7',courseId:'1',name:'正式线上作业',description:'查看附件'};
  const attachment={id:'22',courseId:'1',name:'第四次作业.pdf',sources:[{kind:'assignment',assignmentId:'7'}]};
  f.api.setData({meta:{available:true},data:{courses:[{id:'1',name:'课程'}],assignments:[assignment],files:[attachment]}});
  const assignments=f.api.allAssignments();
  assert.equal(assignments.length,1);assert.equal(assignments[0],assignment);
});
function file(overrides={}) {
  return {id:'1',courseId:'11',name:'课程资料.pdf',courseName:'课程甲',url:'https://canvas.shufe.edu.cn/courses/11/files/1?wrap=1',
    size:2097152,createdAt:'2026-11-10T01:00:00+08:00',updatedAt:current,folder:'第一章',...overrides};
}

test('reported statistics record creation date is labeled explicitly without claiming an original upload date', () => {
  const f=fixture();f.setNow('2026-11-11T14:09:48+08:00');
  const card=f.api.materialCard(file({name:'第三次作业.pdf',createdAt:'2026-11-11T11:06:46+08:00',updatedAt:'2026-11-11T11:06:48+08:00'}));
  assert.equal(card.querySelector('.study-file-relative'),null);
  assert.match(card.querySelector('.study-file-upload-date').textContent,/11\/11 创建/);
  assert.match(card.querySelector('.study-file-info').textContent,/2026\/11\/11 11:06:46/);
  assert.doesNotMatch(card.textContent,/今天上传|上传于/);
});

test('actual card DOM shows semantic dates, long title, course, size, folder and unchanged open link', () => {
  const f=fixture(),input=file({name:'很长的课程文件名'.repeat(20)+'.pdf',locked:true});
  const card=f.api.materialCard(input);
  const dates=card.querySelectorAll('time');
  assert.equal(dates.length,2);
  assert.ok(dates.every(time=>Number.isFinite(Date.parse(time.dateTime))));
  assert.equal(card.querySelector('.study-file-relative'),null);
  assert.match(card.querySelector('.study-file-upload-date').textContent,/11\/10 创建/);
  assert.match(card.textContent,/2.0 MB/);assert.match(card.textContent,/课程甲/);assert.match(card.textContent,/第一章/);
  assert.match(card.textContent,/受限资料/);
  assert.equal(card.querySelector('.study-file-link').textContent,input.name);
  assert.equal(card.querySelector('.study-file-open').href,input.url);
  assert.equal(card.querySelector('.study-file-open')['aria-label'],`打开文件 ${input.name}`);
  assert.equal(card.querySelector('.study-file-updated').hidden,true);
});

test('actual DOM update label is separate and invalid upload dates never produce invalid time elements', () => {
  const f=fixture();
  const card=f.api.materialCard(file({createdAt:'2026-11-08T01:00:00+08:00'}));
  assert.match(card.querySelector('.study-file-updated').textContent,/文件记录更新于 2026\/11\/10/);
  assert.match(card.querySelector('.study-file-updated').querySelector('time').title,/内容变更及操作者未经核实/);
  for(const createdAt of [null,'invalid','2027-01-01T00:00:00Z']) {
    const item=f.api.materialCard(file({createdAt,updatedAt:null}));
    assert.equal(item.querySelectorAll('time').length,0);
    assert.match(item.querySelector('.study-file-upload-date').textContent,/日期(未提供|异常)/);
  }
});

test('three official statistics file identities keep their own compact and full creation dates',()=>{
  const f=fixture();f.setNow('2026-11-11T22:00:00+08:00');
  for(const [id,name,createdAt,compact,full] of [
    ['1848977','第三次作业.pdf','2026-11-11T03:06:46Z','11/11 创建','2026/11/11 11:06:46'],
    ['1848978','第三次作业答案.pdf','2026-11-11T03:06:47Z','11/11 创建','2026/11/11 11:06:47'],
    ['1758887','第三次作业.pdf','2026-10-13T12:44:14Z','10/13 创建','2026/10/13 20:44:14'],
  ]){
    const card=f.api.materialCard(file({id,name,createdAt,updatedAt:null}));
    const details=card.querySelector('.study-file-info'),summary=details.querySelector('summary');
    assert.equal(details.tagName,'DETAILS');assert.equal(details.open,false);
    assert.match(summary.getAttribute('aria-label'),/日期、大小和来源详情/);
    assert.ok(summary.textContent.startsWith(compact+' · '));
    assert.match(details.textContent,new RegExp(full));
    assert.doesNotMatch(summary.textContent,/上传|今天|昨天/);
    assert.ok(details.querySelectorAll('time').every(row=>Number.isFinite(Date.parse(row.dateTime))));
  }
});

test('course line is present in all-course browsing and hidden on reused single-course cards',()=>{
  const f=fixture(),one=file(),two=file({id:'2',courseId:'22',courseName:'课程乙'});
  f.api.setData({meta:{available:true},data:{files:[one,two],courses:[]}});f.api.renderMaterials();
  const card=f.ids['study-files'].children.find(row=>row.dataset.key==='file-11-1');
  assert.equal(card.querySelector('.study-file-course').hidden,false);
  f.api.setFilter({course:'11'});f.api.renderMaterials();
  assert.equal(f.ids['study-files'].children[0],card);
  assert.equal(card.querySelector('.study-file-course').hidden,true);
  assert.equal(f.ids['study-files-count'].textContent,'1 份');
});

test('date details restore from existing view state and information replaces redundant top hints',()=>{
  const f=fixture({open:['study-file-info-11-1'],query:'课程',course:'11',fileType:'pdf',sort:'name',scrollY:420});
  const card=f.api.materialCard(file());assert.equal(card.querySelector('.study-file-info').open,true);
  const config=fs.readFileSync(path.join(root,'config/glance.yml'),'utf8');
  const material=config.slice(config.indexOf('<section id="study-material-panel"'),config.indexOf('</aside>',config.indexOf('<section id="study-material-panel"')));
  assert.doesNotMatch(material,/id="study-file-result"|study-material-date-note|>查找文件</);
  assert.doesNotMatch(material,/study-material-info|study-materials-status|study-material-warning|study-material-utility|资料信息/);
  assert.match(material,/id="study-file-search"[^>]*aria-label="搜索课程文件"/);
  assert.equal((material.match(/id="study-files-count"/g)||[]).length,1);
  const css=fs.readFileSync(path.join(root,'assets/study.css'),'utf8');
  assert.match(css,/\.study-file-options\s*\{[^}]*repeat\(4,minmax\(0,1fr\)\)/);
  assert.match(css,/\.study-file-info>summary:focus-visible/);
});

test('normal stale and unreadable data render without the removed region through refresh and course switches',()=>{
 const f=fixture();
 const unavailable=file({id:'2',name:'附件.txt',metadataUnavailable:true,metadataHttpStatus:404,sources:[{kind:'assignment',url:'https://canvas.shufe.edu.cn/courses/11/assignments/7'}]});
 for(const meta of [{available:true},{available:true,materialWarningCount:1},{available:true,stale:true,message:'读取失败'}]){
   f.api.setData({meta,data:{files:[file(),unavailable],courses:[]}});
   f.api.sourceStatus('canvas',meta);
   for(const course of ['', '11', '']){
     f.api.setFilter({course,fileType:'all'});f.api.renderMaterials();
     assert.equal(f.ids['study-files'].children.length,2);
     assert.equal(f.ids['study-files-count'].textContent,'2 份');
     for(const id of ['study-material-info','study-materials-status','study-material-warning','study-material-warning-body'])assert.equal(f.document.getElementById(id),null);
   }
 }
 assert.equal(unavailable.metadataUnavailable,true);assert.equal(unavailable.metadataHttpStatus,404);
 f.api.setFilter({fileType:'pdf'});f.api.renderMaterials();assert.equal(f.ids['study-files-count'].textContent,'1 份');
 f.api.setFilter({fileType:'all',query:'附件'});f.api.renderMaterials();assert.equal(f.ids['study-files-count'].textContent,'1 份');
 assert.equal(f.ids['study-search-clear'].hidden,false);
});

test('visibility refresh and normal redraw update relative text without replacing cards, filters or reading position', () => {
  const f=fixture();
  f.api.setData({meta:{available:true},data:{files:[file()],courses:[]}});
  f.api.setFilter({query:'课程',course:'11',fileLimit:16,open:new Set(['study-courses'])});f.api.renderMaterials();
  const card=f.ids['study-files'].children[0],link=card.querySelector('.study-file-open');
  assert.equal(card.querySelector('.study-file-relative'),null);
  f.setNow('2026-11-11T00:00:00+08:00');f.document.visibilityState='hidden';f.listeners.visibilitychange();
  assert.equal(card.querySelector('.study-file-relative'),null);
  f.document.visibilityState='visible';f.listeners.visibilitychange();
  assert.match(card.querySelector('.study-file-upload-date').textContent,/11\/10 创建/);
  f.setNow('2026-11-12T00:00:00+08:00');f.api.renderMaterials();
  assert.match(card.querySelector('.study-file-upload-date').textContent,/11\/10 创建/);
  assert.equal(f.ids['study-files'].children[0],card);assert.equal(card.querySelector('.study-file-open'),link);
  assert.equal(f.api.getView().query,'课程');assert.equal(f.api.getView().course,'11');assert.equal(f.api.getView().fileLimit,16);
  assert.deepEqual([...f.api.getView().open],['study-courses']);
  assert.equal(f.scroll.length,0);assert.equal(f.fetchCalls.length,0);
});

test('upload sorting ignores record updates; filename mode, result hints and empty search agree', () => {
  const f=fixture();
  const newerUpload=file({id:'new',name:'B',updatedAt:'2026-11-08T12:00:00+08:00'});
  const newerUpdate=file({id:'old',name:'A',createdAt:'2026-10-01T00:00:00+08:00',modifiedAt:current,updatedAt:'2026-11-01T00:00:00+08:00'});
  assert.equal(f.api.fileDate(newerUpdate),current);
  f.api.setData({meta:{available:true},data:{files:[newerUpload,newerUpdate],courses:[]}});f.api.renderMaterials();
  assert.equal(f.ids['study-files'].children[0].dataset.key,'file-11-new');
  assert.equal(f.ids['study-files-count'].textContent,'2 份');
  assert.equal(f.ids['study-file-result'].textContent,'');
  f.api.setFilter({sort:'name'});f.api.renderMaterials();assert.equal(f.ids['study-files'].children[0].dataset.key,'file-11-old');
  assert.equal(f.ids['study-files-count'].textContent,'2 份');
  f.api.setFilter({query:'absent'});f.api.renderMaterials();assert.match(f.ids['study-files'].textContent,/没有找到/);
});

test('upload order compares timestamp instants across offsets, not date strings', () => {
  const files=[
    file({id:'a',name:'A',createdAt:'2026-11-10T08:30:00+08:00'}),
    file({id:'b',name:'B',createdAt:'2026-11-09T20:00:00-05:00'}),
    file({id:'c',name:'C',createdAt:'2026-11-10T01:30:00Z'}),
  ];
  assert.deepEqual(files.sort((a,b)=>dates.compareFiles(a,b,'upload',at)).map(f=>f.id),['c','b','a']);
});

test('equal upload timestamps and all missing/invalid/future dates have deterministic tie breaks', () => {
  const files=[
    file({id:'2',name:'资料10',createdAt:current}),file({id:'3',name:'资料2',createdAt:current}),
    file({id:'1',name:'资料2',createdAt:'2026-11-10T02:00:00Z'}),
    file({id:'4',name:'A',createdAt:null,updatedAt:current}),
    file({id:'5',name:'B',createdAt:'bad',modifiedAt:current}),
    file({id:'6',name:'C',createdAt:'2026-11-11T00:00:00+08:00'}),
  ];
  const compare=(a,b)=>dates.compareFiles(a,b,'upload',at);
  const expected=['1','3','2','4','5','6'];
  assert.deepEqual([...files].sort(compare).map(f=>f.id),expected);
  assert.deepEqual([...files].reverse().sort(compare).map(f=>f.id),expected);
  assert.equal(compare(files[0],files[0]),0);
  const names=[file({name:'资料10'}),file({name:'中文'}),file({name:'资料2'}),file({name:'A'}),file({name:'B'})];
  assert.deepEqual([...names].sort((a,b)=>dates.compareFiles(a,b,'name',at)).map(f=>f.name),
    [...names].sort((a,b)=>a.name.localeCompare(b.name,'zh-CN',{numeric:true})).map(f=>f.name));
});

test('stored recent/name/upload/unknown choices restore without erasing other view fields', () => {
  for(const value of ['recent','name','upload','unknown',null,undefined]){
    const stored={sort:value,course:'11',query:'课程',fileLimit:16,assignmentLimit:26,tab:'completed',
      offlineStatus:'submitted',open:['study-courses'],scrollY:730};
    const f=fixture(stored);
    const expected=value==='name'?'name':'upload';
    assert.equal(dates.restoreSort(value),expected);
    assert.equal(f.api.getView().sort,expected);
    f.api.boot();
    assert.equal(f.ids['study-file-sort'].value,expected);
    const migrated=f.getStored();
    assert.equal(migrated.sort,expected);
    for(const key of ['course','query','fileLimit','assignmentLimit','tab','offlineStatus','scrollY'])assert.equal(migrated[key],stored[key]);
    assert.deepEqual(migrated.open,stored.open);
    assert.equal(f.fetchCalls.length,0);
  }
});

test('real sort-change handler persists the new choice; reload and continue viewing keep context', () => {
  const f=fixture({sort:'recent',course:'11',query:'课程',fileLimit:16,open:['study-courses'],scrollY:420});
  const files=Array.from({length:20},(_,i)=>file({id:String(i),name:`课程资料${i}.pdf`,createdAt:`2026-11-${String(i%9+1).padStart(2,'0')}T00:00:00+08:00`}));
  f.api.setData({meta:{available:true},data:{files,courses:[]}});f.api.boot();
  f.ids['study-file-sort'].value='name';f.ids['study-file-sort'].fire('change');
  assert.equal(f.ids['study-files'].children.length,16);
  f.ids['study-files-more'].fire('click');
  assert.equal(f.ids['study-files'].children.length,20);
  const saved=f.getStored();
  assert.equal(saved.sort,'name');assert.equal(saved.fileLimit,26);assert.equal(saved.course,'11');assert.equal(saved.query,'课程');assert.equal(saved.scrollY,420);
  assert.deepEqual(saved.open,['study-courses']);
  const next=fixture(saved);next.api.setData({meta:{available:true},data:{files,courses:[]}});next.api.boot();next.api.renderMaterials();
  assert.equal(next.ids['study-file-sort'].value,'name');assert.equal(next.ids['study-files'].children.length,20);
  next.ids['study-file-sort'].value='upload';next.ids['study-file-sort'].fire('change');
  assert.equal(fixture(next.getStored()).api.getView().sort,'upload');
  assert.equal(next.scroll.length,0);assert.equal(next.fetchCalls.length,0);
});

test('layout rules wrap dates/title while retaining a separate open column and helpers load before study', () => {
  const css=fs.readFileSync(path.join(root,'assets/study.css'),'utf8');
  assert.match(css,/\.study-file-title-row\s*\{[^}]*flex-wrap:\s*wrap/);
  assert.match(css,/\.study-file-title-row h3\s*\{[^}]*min-width:\s*0/);
  assert.match(css,/\.study-file\s*\{[^}]*grid-template-columns:\s*38px minmax\(0, 1fr\) 20px/);
  assert.match(css,/\.study-file h3\s*\{[^}]*overflow-wrap:\s*anywhere/);
  const config=fs.readFileSync(path.join(root,'config/glance.yml'),'utf8');
  assert.ok(config.indexOf('/assets/study-material-dates.js')<config.indexOf('/assets/study.js?'));
  assert.doesNotMatch(config,/日期为 Canvas 当前文件记录创建时间|老师首次发布资料的日期尚未核实/);
  assert.doesNotMatch(config,/study-material-info|study-materials-status|study-material-warning|study-material-utility/);
  assert.doesNotMatch(css,/study-material-info|study-material-warning|study-material-utility/);
  const options=config.match(/<select id="study-file-sort"[^>]*>([\s\S]*?)<\/select>/)[1];
  assert.deepEqual([...options.matchAll(/<option value="([^"]+)">([^<]+)<\/option>/g)].map(m=>[m[1],m[2]]),
    [['upload','时间排序'],['name','默认排序']]);
});

test('grouped material cards keep original source details, course/type/search counts and expanded state',()=>{
 const f=fixture({stored:{open:['study-file-info-1-2']}});
 const member=file({id:'2',name:'作业.pdf',courseId:'1',size:100});
 const grouped={...member,groupId:'pdf-group',memberIds:['2','3'],members:[member,{...member,id:'3',hidden:true}]};
 const word={...member,id:'4',name:'作业.docx',groupId:'word-group',memberIds:['4'],members:[{...member,id:'4',name:'作业.docx'}]};
 const card=f.api.materialCard(grouped);
 assert.equal(card.querySelector('.study-file-info').id,'study-file-info-1-pdf-group');
 assert.match(card.textContent,/来源记录 2 条/);assert.match(card.textContent,/ID 2/);assert.match(card.textContent,/ID 3/);
 f.api.setData({meta:{available:true,stale:false},data:{files:[grouped,word],courses:[{id:'1',name:'课程甲'}],assignments:[]}});
 f.api.setFilter({course:'1',fileType:'pdf',query:'作业'});f.api.renderMaterials();
 assert.equal(f.ids['study-files'].children.length,1);
 assert.match(f.ids['study-files-count'].textContent,/1/);
});

test('real material rendering composes state with course, type and search across all courses',()=>{
 const f=fixture();const model=require('../assets/study-ima-model.js');
 const rows=[file({id:'1',courseId:'1',name:'作业.pdf'}),file({id:'2',courseId:'1',name:'作业.docx'}),
             file({id:'3',courseId:'2',name:'作业.pdf'}),file({id:'4',courseId:'1',name:'其他资料.pdf'})];
 const comparisons={'1':{libraryId:'lib',complete:true,rows:[{fileId:'1',status:'exists'},{fileId:'2',status:'unsynced'},{fileId:'4',status:'unsynced'}]}};
 let mode='other',visible=[];
 f.window.StudyIma={setContext(){},setRows(rows){visible=rows;},decorate(){},
   filter(rows){return rows.filter(row=>model.matches(row,mode,model.record(row,comparisons,{'1':{libraryId:'lib'}}),dates));}};
 f.api.setData({meta:{available:true,stale:false},data:{files:rows,courses:[{id:'1',name:'课程甲'},{id:'2',name:'课程乙'}],assignments:[]}});
 f.api.setFilter({course:'',fileType:'pdf',query:'作业'});f.api.renderMaterials();
 assert.deepEqual(visible.map(row=>row.id),['3']);assert.match(f.ids['study-files-count'].textContent,/1/);
 mode='unsynced';f.api.setFilter({course:'1',fileType:'word',query:'作业'});f.api.renderMaterials();assert.deepEqual(visible.map(row=>row.id),['2']);
 mode='exists';f.api.setFilter({course:'',fileType:'pdf',query:'作业'});f.api.renderMaterials();assert.deepEqual(visible.map(row=>row.id),['1']);
 comparisons['1'].stale=true;mode='other';f.api.renderMaterials();assert.deepEqual(visible.map(row=>row.id).sort(),['1','3']);
});
