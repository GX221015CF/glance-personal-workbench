(function(scope) {
  'use strict';
  const extensions = {pdf:'pdf',ppt:'ppt',pptx:'ppt',doc:'word',docx:'word',xls:'excel',xlsx:'excel'};
  const mimes = {
    'application/pdf':['pdf','PDF'],
    'application/vnd.ms-powerpoint':['ppt','PPT'],
    'application/vnd.openxmlformats-officedocument.presentationml.presentation':['ppt','PPTX'],
    'application/msword':['word','DOC'],
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document':['word','DOCX'],
    'application/vnd.ms-excel':['excel','XLS'],
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':['excel','XLSX'],
  };
  function describe(file) {
    const name=String(file.name||file.fileName||'').trim();
    const extension=/\.([a-z0-9]+)$/i.exec(name)?.[1].toLowerCase();
    if(extension)return {kind:extensions[extension]||'other',label:extension.toUpperCase()};
    const mime=String(file.contentType||'').split(';')[0].trim().toLowerCase();
    const [kind,label]=mimes[mime]||['other','FILE'];
    return {kind,label};
  }
  const restore=value=>['pdf','ppt','word','excel','other'].includes(value)?value:'all';
  const matches=(file,kind)=>kind==='all'||describe(file).kind===kind;
  const api={describe,restore,matches};
  if(typeof module==='object'&&module.exports)module.exports=api;
  else scope.StudyFileTypes=api;
})(globalThis);
