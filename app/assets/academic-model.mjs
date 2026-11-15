// School statuses are retained; this module never decides graduation/recognition.
export const statusLabel = raw => ({'已过':'已修（学校：已过）','在读':'在修（学校：在读）','待修':'待修'}[raw] || `其他状态：${raw || '未提供'}`);
export const credits = value => typeof value === 'number' && Number.isFinite(value) ? String(value) : '暂未获取';
export function overviewMessage(meta) {
  const overview=meta?.creditOverview;
  if(overview?.errorCode==='SOURCE_CHANGED') return overview.available
    ? '学校总览计算来源有变化，沿用上次学分，等待核对'
    : '学校总览计算来源有变化，学分暂未取得';
  if(overview?.errorCode) return overview.available
    ? '学分总览本次读取未完成，沿用上次学分'
    : '学分总览暂未取得，课程与分类可单独查看';
  if(overview?.available) return overview.stale||meta?.stale||meta?.errorCode
    ? '学分总览正在展示上次保存的数据'
    : '按学校总览口径统计';
  return '学分总览暂未取得，课程与分类可单独查看';
}
export function viewData(snapshot) {
  const fixed = snapshot?.data?.fixedPlan, progress = snapshot?.data?.progress;
  const groups = new Map((fixed?.definition?.categories || []).map(row => [row.id, row]));
  const dynamic = new Map((progress?.categories || []).map(row => [row.id, row]));
  const courses = progress?.courses || [], codes = new Map();
  for (const row of courses) codes.set(row.code, (codes.get(row.code) || 0) + 1);
  return {fixed, progress, courses, groups, codes,
    categories: [...groups.values()].map(row => ({...row, ...(dynamic.get(row.id) || {})})),
    supplement: progress?.supplement || [],
    statuses: [...new Set(courses.map(row => row.rawStatus))]};
}
export function filterCourses(data, {query='', group='', status=''}={}) {
  const needle=query.trim().toLocaleLowerCase();
  return data.courses.filter(row => (!group || row.groupId === group)
    && (!status || row.rawStatus === status)
    && (!needle || `${row.name} ${row.code}`.toLocaleLowerCase().includes(needle)));
}
export function sourceMessage(meta, error='') {
  if(error) return meta?.available ? '读取失败，保留当前数据' : '本机后台暂不可用，请启动工作台';
  if(meta?.errorCode==='PLAN_CONFLICT') return '培养要求或本人归属有变化，已保留原计划与上次进度，请核对学校计划';
  if(meta?.needsLogin) return meta.available ? '登录已过期，展示上次数据；请使用上方“恢复学校连接”，门户认证后会自动衔接' : '尚未连接学业规划；请使用上方“连接学校”，程序会自动处理官方登录衔接';
  if(meta?.refreshing) return '正在同步，当前内容保留';
  if(meta?.errorCode) return '同步未完成，展示上次数据；稍后可重试同步';
  if(meta?.stale) return '正在展示上次保存的数据，尚未核验本次连接';
  if(meta?.available&&meta?.creditOverview?.errorCode) return `课程与分类可用；${overviewMessage(meta)}`;
  return meta?.available ? '已取得学校数据' : '尚无可用学业数据';
}
export function refreshLine(meta, requestResult, beforeTime) {
  if(requestResult?.error) return requestResult.error.message || '同步请求失败，保留已有数据';
  if(meta?.refreshing) return '同步中，当前数据保留';
  if(requestResult?.reason==='not_connected') return '尚未连接，请先连接学业规划';
  if(requestResult?.reason==='cooldown') return `请在 ${requestResult.retryAfterSeconds} 秒后再同步`;
  if(meta?.errorCode || meta?.needsLogin) return sourceMessage(meta);
  if(requestResult?.accepted || requestResult?.reason==='already_running') {
    if(meta?.updatedAt && meta.updatedAt!==beforeTime && !meta.stale) return meta.creditOverview?.errorCode
      ? `课程与分类同步成功；${overviewMessage(meta)}` : '同步成功';
    return '结果待核对，请查看最近成功时间；不会自动重发';
  }
  return sourceMessage(meta);
}
export function restoreView(value) {
  return {query:typeof value?.query==='string'?value.query.slice(0,200):'',
    group:typeof value?.group==='string'?value.group:'',status:typeof value?.status==='string'?value.status:'',
    open:Array.isArray(value?.open)?value.open.filter(v=>typeof v==='string').slice(0,200):[],
    scrollY:Number.isFinite(value?.scrollY)&&value.scrollY>=0?value.scrollY:0};
}
