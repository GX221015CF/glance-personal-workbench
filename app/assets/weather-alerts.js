(function (global) {
  'use strict';
  const TEMPERATURE_THRESHOLD = 4;
  function alertsFor(days, baseline = null, threshold = TEMPERATURE_THRESHOLD) {
    if (!Array.isArray(days) || days.length !== 2 || !Number.isFinite(threshold) || threshold <= 0) return [];
    for (const day of days) {
      if (!day || !Number.isFinite(day.high) || !Number.isFinite(day.low) || day.low > day.high ||
          day.low < -80 || day.high > 65 || !Number.isInteger(day.code) || day.code < 0 || day.code > 99) return [];
    }
    const tomorrow = days[1];
    const baselineValid = baseline?.kind === 'station-observation' && baseline.available === true && baseline.sampleCount === 7 &&
      Number.isFinite(baseline.highMean) && Number.isFinite(baseline.lowMean) &&
      baseline.lowMean >= -80 && baseline.highMean <= 65 && baseline.lowMean <= baseline.highMean;
    const highChange = tomorrow.high - (baselineValid ? baseline.highMean : days[0].high);
    const lowChange = tomorrow.low - (baselineValid ? baseline.lowMean : days[0].low);
    const alerts = [];
    for (const direction of [-1, 1]) {
      // Suppress floating-point noise at the strict >4°C boundary.
      const high = highChange * direction - threshold > 1e-9, low = lowChange * direction - threshold > 1e-9;
      if (!high && !low) continue;
      const amount = Math.abs(high ? highChange : lowChange);
      const prefix = high ? (lowChange * direction < -threshold ? '白天' : '') : '早晚';
      const word = direction < 0 ? '降温' : '升温';
      const rounded = Number(amount.toFixed(1));
      const amountLabel = rounded <= threshold ? '>' + threshold : rounded;
      alerts.push({kind:direction < 0 ? 'cool' : 'warm', text:`${prefix}${word} ${amountLabel}°C`,
        detail:direction < 0 ? '明日明显降温，注意添衣。' : '明日明显升温，留意穿衣调整。'});
    }
    const code = tomorrow.code;
    let travel;
    if ([56,57,66,67].includes(code)) travel = ['ice','冻雨防滑','明日预报有冻雨或冻毛毛雨，留意路面结冰，出行防滑。'];
    else if ([71,73,75,77,85,86].includes(code)) travel = ['snow','有雪防滑','明日预报有降雪，注意保暖及路面湿滑，出门预留时间。'];
    else if ([95,96,99].includes(code)) travel = ['storm','雷雨提醒','明日预报有雷雨'+([96,99].includes(code)?'，可能伴有冰雹':'')+'，留意天气变化并调整户外安排。'];
    else if ([65,82].includes(code)) travel = ['rain','较强降雨','明日预报有较强降雨，带伞并为通勤预留时间。'];
    else if ([51,53,55,61,63,80,81].includes(code)) travel = ['rain','有雨带伞','明日预报有降雨，出门带伞，注意路面湿滑。'];
    else if ([45,48].includes(code)) travel = ['fog','有雾慢行','明日预报有雾，留意低能见度，出行减速慢行。'];
    if (travel) alerts.push({kind:travel[0],text:travel[1],detail:travel[2]});
    return alerts;
  }
  function shanghaiDates(now = new Date()) {
    const iso = new Intl.DateTimeFormat('en-CA', {timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'});
    // Add one day to the Shanghai calendar date, independent of host timezone.
    const today = iso.format(now);
    const tomorrow = new Date(today + 'T12:00:00+08:00'); tomorrow.setUTCDate(tomorrow.getUTCDate()+1);
    return [today,iso.format(tomorrow)];
  }
  function decorate(document, now = new Date()) {
    const expected = shanghaiDates(now);
    document.querySelectorAll('.home-weather-days').forEach(container => {
      const nodes = [...container.querySelectorAll('.home-weather-day')];
      const days = nodes.map(node => ({date:node.dataset.date,high:Number(node.dataset.high),low:Number(node.dataset.low),code:Number(node.dataset.code)}));
      const valid = days.length === 2 && days.every((day,i)=>day.date===expected[i] &&
        ['high','low','code'].every(key=>nodes[i].dataset[key] !== undefined && nodes[i].dataset[key] !== ''));
      const yesterday = new Date(expected[0]+'T12:00:00+08:00'); yesterday.setUTCDate(yesterday.getUTCDate()-1);
      const start = new Date(expected[0]+'T12:00:00+08:00'); start.setUTCDate(start.getUTCDate()-7);
      const iso = date => new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(date);
      const baseline = {kind:container.dataset.baselineKind,available:container.dataset.baselineAvailable==='true' && container.dataset.baselineStart===iso(start) && container.dataset.baselineEnd===iso(yesterday),
        sampleCount:Number(container.dataset.baselineCount),highMean:Number(container.dataset.baselineHigh),lowMean:Number(container.dataset.baselineLow),
        start:container.dataset.baselineStart,end:container.dataset.baselineEnd};
      if (!container.dataset.baselineHigh || !container.dataset.baselineLow) baseline.available=false;
      const alerts = valid ? alertsFor(days, baseline) : [];
      const signature = JSON.stringify([days,alerts,baseline,container.dataset.stale]);
      const host = container.parentElement;
      host.querySelector('.home-weather-baseline')?.remove();
      if (container.dataset.alertSignature===signature && (!alerts.length || host.querySelector('.home-weather-alerts'))) return;
      container.dataset.alertSignature=signature;
      host.querySelector('.home-weather-alerts')?.remove();
      container.querySelector('.home-weather-attention')?.remove();
      if (!alerts.length) return;
      const marker = document.createElement('span'); marker.className='home-weather-attention';
      marker.textContent='!'; marker.setAttribute('aria-label','明日有出行提示'); marker.title='查看下方明日提示';
      nodes[1].querySelector('.home-weather-label')?.append(marker);
      const panel = document.createElement('div'); panel.className='home-weather-alerts';
      panel.setAttribute('role','note'); panel.setAttribute('aria-label','明日天气出行提示');
      const heading = document.createElement('p'); heading.className='home-weather-alert-heading';
      heading.textContent=container.dataset.stale==='true' ? '明日提示 · 上次预报' : '明日提示'; panel.append(heading);
      const chips = document.createElement('div'); chips.className='home-weather-alert-chips';
      for (const alert of alerts) {
        const chip = document.createElement('span'); chip.className='home-weather-alert weather-alert-'+alert.kind;
        chip.textContent=alert.text; chip.title=alert.detail; chip.setAttribute('aria-label',alert.text+'。'+alert.detail);
        chips.append(chip);
      }
      panel.append(chips); container.after(panel);
    });
  }
  const api={alertsFor,shanghaiDates,decorate,TEMPERATURE_THRESHOLD};
  if (typeof module!=='undefined' && module.exports) { module.exports=api; return; }
  global.HomeWeatherAlerts=api;
  let scheduled=false;
  const update=()=>{ scheduled=false; decorate(global.document); };
  const schedule=()=>{ if(!scheduled) {scheduled=true;setTimeout(update,50);} };
  function start() {
    update();
    new MutationObserver(records=>{
      if(records.some(record=>[...record.addedNodes,...record.removedNodes].some(node=>
        node.nodeType===1 && !node.matches('.home-weather-alerts,.home-weather-attention') &&
        (node.matches('.home-weather-days,.home-weather') || node.querySelector('.home-weather-days'))))) schedule();
    }).observe(global.document.body,{childList:true,subtree:true});
    setInterval(schedule,60000);
    global.document.addEventListener('visibilitychange',()=>{if(global.document.visibilityState==='visible')schedule();});
  }
  if(global.document.readyState==='loading')global.document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})(typeof window!=='undefined'?window:globalThis);
