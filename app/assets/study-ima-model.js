(function(scope) {
  'use strict';
  const key = file => `${file.courseId}:${file.groupId || (file.id ?? file.fileId)}`;
  function restoreFilter(value) {
    if (['all','unsynced','exists','other'].includes(value)) return value;
    if (value === 'recommended') return 'unsynced';
    return ['review','failed','new_version','anomaly','unlinked','unchecked','unknown'].includes(value) ? 'other' : 'all';
  }
  const statusGroup = state => !state.stale && ['exists','unsynced'].includes(state.status) ? state.status : 'other';
  function record(file, comparisons, links) {
    if (!links[file.courseId]) return {status:'unlinked', candidates:[], unchecked:true};
    const comparison = comparisons[file.courseId];
    if (!comparison || comparison.libraryId !== links[file.courseId].libraryId) return {status:'unchecked', candidates:[], unchecked:true};
    const ids=file.memberIds||[String(file.id??file.fileId)];
    const row = (comparison.rows||[]).find(item => file.groupId&&item.groupId===file.groupId || ids.includes(item.fileId));
    return {...(row || {status:'unchecked',candidates:[],unchecked:true}), stale:!!comparison.stale||comparison.complete===false, checkId:comparison.checkId};
  }
  function matches(file, mode, state, dates, at = Date.now()) {
    if (mode === 'all') return true;
    if (mode === 'recommended') return state.status === 'unsynced' && !state.stale && state.supported !== false
      && dates.parse(file.createdAt) !== null && dates.parse(file.createdAt) <= at;
    return statusGroup(state) === restoreFilter(mode);
  }
  function toggleSelection(selected, orderedKeys, anchor, clicked, checked, shift) {
    const next = new Set(selected);
    const first = orderedKeys.indexOf(anchor), last = orderedKeys.indexOf(clicked);
    const range = shift && first >= 0 && last >= 0 ? orderedKeys.slice(Math.min(first,last), Math.max(first,last)+1) : [clicked];
    for (const value of range) { if (checked) next.add(value); else next.delete(value); }
    return next;
  }
  function progress(job) {
    const rows = job?.rows || [];
    const success = rows.filter(row => ['success','skipped'].includes(row.status)).length;
    const failed = rows.filter(row => row.status === 'failed').length;
    const unknown = rows.filter(row => row.status === 'unknown').length;
    const processed = rows.filter(row => ['success','skipped','omitted','failed','unknown','not_run'].includes(row.status)).length;
    return {total:rows.length, processed, success, failed, unknown, percent:rows.length ? processed / rows.length * 100 : 0};
  }
  function directoryTrail(entries, libraryId, folderId) {
    const trail=[],seen=new Set();
    let id=folderId;
    while(id!==libraryId){
      if(seen.has(id))return null;
      seen.add(id);
      const row=entries.find(entry=>entry.kind==='folder'&&entry.folderId===id);
      if(!row)return null;
      trail.unshift(row);id=row.parentFolderId;
    }
    return trail;
  }
  const directoryChildren=(entries,parentId)=>entries.filter(row=>row.kind==='folder'&&row.parentFolderId===parentId);
  function dailyEntries(entries) {
    const hidden=new Set(entries.filter(row=>row.kind==='folder'&&row.isTestDirectory).map(row=>row.folderId));
    let changed=true;
    while(changed){
      changed=false;
      for(const row of entries)if(row.kind==='folder'&&hidden.has(row.parentFolderId)&&!hidden.has(row.folderId)){
        hidden.add(row.folderId);changed=true;
      }
    }
    return entries.filter(row=>!hidden.has(row.folderId)&&!hidden.has(row.parentFolderId));
  }
  function sourceSignature(files){
    const instant=value=>Number.isFinite(Date.parse(value))?Date.parse(value):value||null;
    return JSON.stringify(files.map(file=>[key(file),file.name||file.fileName,file.size,
      instant(file.createdAt),instant(file.updatedAt),instant(file.modifiedAt),!!file.metadataUnavailable,
      (file.members||[]).map(member=>[member.id??member.fileId,member.size,member.updatedAt,member.modifiedAt,member.locked,member.metadataUnavailable])])
      .sort((a,b)=>a[0].localeCompare(b[0])));
  }
  const api = {key, record, matches, restoreFilter, statusGroup, toggleSelection, progress,directoryTrail,directoryChildren,dailyEntries,sourceSignature};
  if (typeof module === 'object' && module.exports) module.exports = api;
  else scope.StudyImaModel = api;
})(globalThis);
