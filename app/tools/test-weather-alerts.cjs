const {test}=require('node:test');
const assert=require('node:assert/strict');
const {alertsFor,shanghaiDates}=require('../assets/weather-alerts.js');
const day=(high,low,code=3)=>({high,low,code});
const baseline=(highMean=20,lowMean=10)=>({kind:'station-observation',available:true,sampleCount:7,highMean,lowMean,start:'2026-10-31',end:'2026-11-06'});
test('small temperature variation is quiet, tomorrow rain still reminds',()=>{
  assert.deepEqual(alertsFor([day(18.8,14.6),day(19.7,14.4)],baseline(21,13.7)),[]);
  assert.equal(alertsFor([day(18.8,14.6),day(19.7,14.4,61)])[0].text,'有雨带伞');
});
test('compares against separate weekly means, strictly more than four degrees',()=>{
  assert.equal(alertsFor([day(20,10),day(15,9)],baseline())[0].text,'降温 5°C');
  assert.equal(alertsFor([day(20,10),day(24,15)],baseline())[0].text,'早晚升温 5°C');
  assert.equal(alertsFor([day(20,10),day(25,11)],baseline())[0].text,'升温 5°C');
  assert.equal(alertsFor([day(20,10),day(20,5)],baseline())[0].text,'早晚降温 5°C');
  assert.equal(alertsFor([day(20,10),day(24,14)],baseline()).length,0);
  assert.equal(alertsFor([day(20,10),day(16,6)],baseline()).length,0);
  assert.equal(alertsFor([day(30,18),day(20,10)],baseline()).length,0,'Large difference from today must not trigger');
  assert.equal(alertsFor([day(25,10),day(25,10)],baseline()).length,1,'No difference from today can still differ from week');
  assert.equal(alertsFor([day(20,10),day(24.01,10)],baseline())[0].text,'升温 >4°C');
});
test('opposite high and low movements do not hide each other',()=>{
  const alerts=alertsFor([day(20,10),day(25,5)],baseline());
  assert.deepEqual(alerts.map(a=>a.text),['早晚降温 5°C','白天升温 5°C']);
});
test('WMO weather codes distinguish rain snow freezing rain thunderstorms and fog',()=>{
  const kinds={rain:[51,53,55,61,63,65,80,81,82],snow:[71,73,75,77,85,86],ice:[56,57,66,67],storm:[95,96,99],fog:[45,48]};
  for(const [kind,codes] of Object.entries(kinds))for(const code of codes){
    assert.equal(alertsFor([day(20,10),day(20,10,code)])[0].kind,kind,`code ${code}`);
  }
  assert.equal(alertsFor([day(20,10,61),day(20,10,0)]).length,0);
  assert.equal(alertsFor([day(20,10),day(14,4,75)],baseline()).length,2);
});
test('missing unverified model or incomplete history falls back to today',()=>{
  for(const b of [null,{},baseline(NaN,10),{...baseline(),sampleCount:6},{...baseline(),available:false},{...baseline(),kind:'model-history'}]){
    const alerts=alertsFor([day(20,10),day(10,0,61)],b);
    assert.equal(alerts.length,2);
    assert.equal(alerts[0].text,'降温 10°C');
    assert.equal(alerts[1].kind,'rain');
  }
  assert.equal(alertsFor([day(20,10),day(24,14)],null).length,0);
  const text=alertsFor([day(20,10),day(25,10)],null)[0].detail;
  assert.ok(!/基准|平均|过去|模型|今天/.test(text));
});
test('invalid and absent data never produce travel advice',()=>{
  for(const days of [null,[],[day(20,10)], [day(NaN,10),day(15,5)], [day(20,10),day(5,15)], [day(20,10),day(15,5,61.5)]])assert.deepEqual(alertsFor(days),[]);
});
test('date comparison follows Shanghai across UTC midnight and year rollover',()=>{
  assert.deepEqual(shanghaiDates(new Date('2026-11-07T16:01:00Z')),['2026-11-08','2026-11-09']);
  assert.deepEqual(shanghaiDates(new Date('2026-12-31T08:00:00Z')),['2026-12-31','2027-01-01']);
});
