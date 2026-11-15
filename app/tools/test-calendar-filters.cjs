const {test}=require('node:test');
const assert=require('node:assert/strict');
const f=require('../assets/calendar-filters.js');
const dates=require('../assets/calendar-dates.js');
const events=require('../config/school-calendar.json').events;
const find=suffix=>events.find(e=>e.id.endsWith('-'+suffix));
const initial=()=>f.migrate(null,[],events).state;

test('real 44-event taxonomy separates ceremony and innovation and contains every event once',()=>{
  const before=JSON.stringify(events);
  assert.equal(f.classify(find('opening')).id,'ceremony');
  assert.equal(f.classify(find('innovation')).id,'innovation');
  const tree=f.tree(events);
  assert.equal(tree.length,7);
  const flattened=tree.flatMap(t=>t.groups.flatMap(g=>g.events));
  assert.equal(flattened.length,events.length);
  assert.equal(new Set(flattened.map(e=>e.id)).size,events.length);
  assert.ok(tree.every(t=>t.groups.every(g=>g.events.length)));
  assert.equal(JSON.stringify(events),before);
  assert.equal(f.classify(find('spring-final')).id,'final');
  assert.equal(f.classify(find('summer-start')).id,'term');
  assert.equal(f.classify(find('winter-practice')).id,'practice');
});
test('initial preferences affect only opening and innovation; unrelated items stay visible',()=>{
  const state=initial();
  assert.equal(f.visible(state,find('opening')),true);
  assert.equal(f.visible(state,find('innovation')),false);
  assert.deepEqual(f.filter(state,events).map(e=>e.id).sort(),events.filter(e=>e.id!==find('innovation').id).map(e=>e.id).sort());
});
test('legacy migration preserves disabled categories, with explicit ceremony exception, and runs once',()=>{
  const migrated=f.migrate(null,['teaching','campus'],events);
  assert.equal(migrated.migrated,true);
  for(const event of events.filter(e=>e.type==='teaching'))assert.equal(f.visible(migrated.state,event),false);
  assert.equal(migrated.state.types.campus,false);
  assert.equal(f.visible(migrated.state,find('food')),false);
  assert.equal(f.visible(migrated.state,find('opening')),true);
  assert.equal(f.selection(migrated.state,events.filter(e=>e.type==='campus')).indeterminate,true);
  let changed=f.setEvent(migrated.state,find('opening').id,false);
  changed=f.setEvent(changed,find('innovation').id,true);
  const reloaded=f.migrate(JSON.parse(JSON.stringify(changed)),[],events);
  assert.equal(reloaded.migrated,false);
  assert.equal(f.visible(reloaded.state,find('opening')),false);
  assert.equal(f.visible(reloaded.state,find('innovation')),true);
  assert.equal(f.visible(reloaded.state,find('military')),false);
});
test('storage migration writes only v2, retains original v1, and never overwrites manual choices',()=>{
  const store=new Map([[f.legacyKey,JSON.stringify(['exam'])]]);
  let writes=0;
  const storage={getItem:k=>store.get(k)||null,setItem:(k,v)=>{writes++;store.set(k,v);}};
  let state=f.load(storage,events);
  assert.equal(writes,1);assert.equal(store.get(f.legacyKey),'["exam"]');
  state=f.setEvent(state,find('innovation').id,true);
  storage.setItem(f.storageKey,JSON.stringify(state));
  const before=writes;const reloaded=f.load(storage,events);
  assert.equal(writes,before);assert.equal(f.visible(reloaded,find('innovation')),true);
  assert.equal(f.visible(reloaded,find('returning-final')),false);
});
test('main subcategory and leaf selection propagate true partial and empty states',()=>{
  let state=initial();const campus=events.filter(e=>e.type==='campus');
  const ceremony=campus.filter(e=>f.classify(e).id==='ceremony');
  assert.equal(f.selection(state,campus).indeterminate,true);
  state=f.setType(state,'campus',false,events);
  assert.deepEqual(f.selection(state,campus),{total:campus.length,selected:0,checked:false,indeterminate:false});
  state=f.setGroup(state,'campus','ceremony',true,events);
  assert.equal(f.selection(state,ceremony).checked,true);
  assert.equal(f.selection(state,campus).indeterminate,true);
  state=f.setEvent(state,find('opening').id,false);
  assert.equal(f.selection(state,ceremony).indeterminate,true);
  state=f.setGroup(state,'campus','ceremony',false,events);
  assert.equal(f.selection(state,campus).selected,0);
  state=f.setType(state,'campus',true,events);
  assert.equal(f.selection(state,campus).checked,true);
  assert.equal(f.visible(state,find('innovation')),true);
});
test('same title distinct stable IDs and different terms are independently selectable',()=>{
  const registrations=events.filter(e=>e.title==='在读生报到');
  assert.equal(registrations.length,2);
  let state=f.setEvent(initial(),registrations[0].id,false);
  assert.equal(f.visible(state,registrations[0]),false);
  assert.equal(f.visible(state,registrations[1]),true);
  assert.notEqual(f.shortDate(registrations[0]),f.shortDate(registrations[1]));
});
test('clear all and disabled categories never hide personal events, including colliding IDs/types',()=>{
  const state=f.setAll(initial(),false);
  for(const type of Object.keys(f.typeLabels)){
    const personal={...find('innovation'),source:'personal',type,annual:false};
    assert.equal(f.visible(state,personal),true);
    assert.deepEqual(f.filter(state,[personal]),[personal]);
  }
  assert.equal(f.filter(state,events).length,0);
  assert.equal(f.filter(f.setAll(state,true),events).length,events.length);
});
test('unknown and new events fall back to Other and inherited rules, not historical ID allowlists',()=>{
  const unknown={...find('opening'),id:'sufe-new',title:'新活动'};
  assert.equal(f.classify(unknown).id,'other');assert.equal(f.visible(initial(),unknown),true);
  const tree=f.tree([...events,unknown]);
  assert.equal(tree.find(t=>t.id==='campus').groups.find(g=>g.id==='other').events[0].id,unknown.id);
  let state=f.setType(initial(),'campus',false,events);
  assert.equal(f.visible(state,unknown),false);
  const newOpening={...find('opening'),id:'sufe-next-opening',startDate:'2027-08-29',endDate:'2027-08-29'};
  assert.equal(f.visible(state,newOpening),false);
  state=f.setGroup(state,'campus','ceremony',true,events);
  assert.equal(f.visible(state,newOpening),true);
  assert.equal(f.visible(state,unknown),false);
  assert.equal(f.visible(initial(),{...unknown,type:'future-category'}),true);
  state=f.setAll(state,false);assert.equal(f.visible(state,{...unknown,type:'future-category'}),false);
});
test('filtered bars and date matching agree across months while opening date remains',()=>{
  const filtered=f.filter(initial(),events),innovation=find('innovation');
  for(const month of ['2026-11','2026-12']){
    assert.equal(dates.rangeWeeks(filtered,month).flatMap(w=>w.segments).filter(s=>s.event.id===innovation.id).length,0);
    assert.ok(dates.rangeWeeks(events,month).flatMap(w=>w.segments).some(s=>s.event.id===innovation.id));
  }
  assert.ok(dates.onDate(filtered,find('opening').startDate).some(e=>e.id===find('opening').id));
  assert.ok(!dates.onDate(filtered,'2026-11-16').some(e=>e.id===innovation.id));
});
