const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const types=require('../assets/study-file-types.js');

test('exactly five classes share icon labels and filter identity',()=>{
  for(const [extension,kind] of [['pdf','pdf'],['PPT','ppt'],['pptx','ppt'],['DOC','word'],['docx','word'],['xls','excel'],['XLSX','excel']]){
    const file={name:'资料.'+extension};
    assert.deepEqual(types.describe(file),{kind,label:extension.toUpperCase()});
    assert.equal(types.matches(file,kind),true);
    assert.equal(types.matches(file,'all'),true);
  }
});
test('CSV, statistics, code, text, archives and images stay neutral even with legacy MIME hints',()=>{
  for(const extension of ['csv','txt','dta','do','sav','r','ipynb','py','zip','png','md','odp','odt']){
    const file={name:'附件.'+extension,contentType:'application/vnd.ms-excel'};
    assert.equal(types.describe(file).kind,'other');
    assert.equal(types.describe(file).label,extension.toUpperCase());
    assert.equal(types.matches(file,'excel'),false);
  }
});
test('missing extensions use reliable MIME and old or missing filters are harmless',()=>{
  assert.deepEqual(types.describe({name:'资料',contentType:'APPLICATION/PDF; charset=binary'}),{kind:'pdf',label:'PDF'});
  assert.deepEqual(types.describe({fileName:'资料',contentType:'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}),{kind:'word',label:'DOCX'});
  assert.deepEqual(types.describe({}),{kind:'other',label:'FILE'});
  for(const value of [undefined,null,'data','archive','unknown'])assert.equal(types.restore(value),'all');
});
test('classification loads before study and all six filter labels are configured',()=>{
  const root=path.resolve(__dirname,'..');
  const config=fs.readFileSync(path.join(root,'config/glance.yml'),'utf8');
  assert.ok(config.indexOf('/assets/study-file-types.js')<config.indexOf('/assets/study.js?'));
  for(const label of ['全部','PDF','PPT','Word','Excel','其他'])assert.match(config,new RegExp('>'+label+'</option>'));
  const css=fs.readFileSync(path.join(root,'assets/study.css'),'utf8');
  for(const kind of ['pdf','ppt','word','excel','other'])assert.ok(css.includes('.study-file-type.is-'+kind));
  assert.doesNotMatch(css,/\.study-file-type\.is-(data|document|slides)/);
});
