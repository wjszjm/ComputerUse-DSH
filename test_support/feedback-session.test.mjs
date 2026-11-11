import assert from 'node:assert/strict';
import {test} from 'node:test';
import {FeedbackSession,summarizeFeedbackResult} from '../dsh-wdc-computer-use/lib/feedback-session.js';

const viewport={x:-100,y:0,width:1000,height:700};
const create=options=>new FeedbackSession({enabled:true,viewport,clock:()=>0,...options});
const result=payload=>({content:[{type:'text',text:JSON.stringify(payload)}]});

test('disabled and unknown geometry stay invisible, even if nested settings request enabled',()=>{
  const disabled=create({enabled:false,settings:{enabled:true}});
  const id=disabled.begin('click');disabled.complete(id,{ok:true,point:[200,100]});
  assert.equal(disabled.snapshot().visible,false);assert.equal(disabled.snapshot().rings.length,0);
  const unknown=create({viewport:undefined});
  assert.equal(unknown.snapshot().geometryKnown,false);assert.equal(unknown.snapshot().visible,false);
});

test('concurrent completion never marks a still-running operation idle',()=>{
  const session=create();const observation=session.begin('screenshot');const input=session.begin('type_text');
  assert.equal(session.state,'typing');session.complete(observation,{ok:true});
  assert.equal(session.state,'typing');
  const next=session.begin('screen_info');session.complete(input,{ok:true});
  assert.equal(session.state,'observing');session.complete(next,{ok:true});assert.equal(session.state,'idle');
});

test('window operations and explicit waits have accurate generic labels',()=>{
  const session=create();
  for(const [tool,state] of [['click','clicking'],['press_key','typing'],['scroll','scrolling'],['drag','operating'],
    ['activate_window','operating'],['launch_app','operating'],['wait','waiting'],['__proto__','operating']]){
    const id=session.begin(tool);assert.equal(session.state,state);session.complete(id,{ok:true});
  }
});

test('stop remains stopping while input is unresolved and observations cannot clear it',()=>{
  const session=create();const input=session.begin('type_text');
  session.setControl({stopped:true,busy:true});assert.equal(session.state,'stopping');
  const observation=session.begin('screenshot');session.complete(observation,{ok:true});assert.equal(session.state,'stopping');
  session.complete(input,{ok:false,cancelled:true});session.setControl({stopped:true,busy:false});
  assert.equal(session.state,'stopped');session.complete(input,{ok:true});assert.equal(session.state,'stopped');
  const resume=session.begin('resume');session.setControl({stopped:false,busy:false});
  assert.equal(session.state,'stopped');session.complete(resume,{ok:true});assert.equal(session.state,'idle');
});

test('unknown outcomes remain errors through background observation until explicit resume',()=>{
  const session=create();const id=session.begin('click');session.setControl({stopped:true,busy:true});
  session.fail(id,'outcome_unknown');assert.equal(session.state,'error');
  const observation=session.begin('list_windows');session.complete(observation,{ok:true});
  session.setControl({stopped:true,busy:false});assert.equal(session.state,'error');
  session.setControl({stopped:false,busy:false});const resume=session.begin('resume');
  session.complete(resume,{ok:true});assert.equal(session.state,'idle');
});

test('busy attempt does not overwrite ongoing input; failed business result does show error',()=>{
  const session=create();const active=session.begin('type_text');const rejected=session.begin('click');
  session.fail(rejected,'action_busy');assert.equal(session.state,'typing');session.complete(active,{ok:true});assert.equal(session.state,'idle');
  const bad=session.begin('click');session.complete(bad,{ok:false});assert.equal(session.state,'error');
});

test('summary uses only final screen points, rejects malformed/failed points and strips sensitive data',()=>{
  const secret='SECRET-INPUT-TEXT';
  const summary=summarizeFeedbackResult('click',result({ok:true,action:'click',screen_point:[500,350],requested_point:[1,2],text:secret}));
  assert.deepEqual(summary,{ok:true,point:[500,350]});assert.ok(!JSON.stringify(summary).includes(secret));
  assert.equal(summarizeFeedbackResult('click',result({ok:true,action:'click',requested_point:[1,2]})).point,undefined);
  assert.equal(summarizeFeedbackResult('click',result({ok:false,action:'click',screen_point:[1,2]})).point,undefined);
  assert.equal(summarizeFeedbackResult('click',result({ok:true,action:'click',screen_point:['1',2]})).point,undefined);
  assert.equal(summarizeFeedbackResult('click',{isError:true,...result({ok:true,screen_point:[1,2]})}).point,undefined);
  assert.deepEqual(summarizeFeedbackResult('type_text',result({ok:false,error_code:'emergency_stop',error:secret})),{ok:false,cancelled:true});
});

test('only full screen_info establishes geometry; explicit work area is preserved',()=>{
  const session=create({viewport:undefined});
  session.complete(session.begin('screenshot'),summarizeFeedbackResult('screenshot',result({ok:true,screen_region:{x:0,y:0,width:100,height:100}})));
  assert.equal(session.snapshot().visible,false);
  session.complete(session.begin('screen_info'),summarizeFeedbackResult('screen_info',result({ok:true,screen:{left:-100,top:0,width:1000,height:700}})));
  assert.equal(session.snapshot().visible,true);assert.equal(session.snapshot().viewport.x,-100);
  session.setGeometry(viewport,{...viewport,height:640});
  session.complete(session.begin('screen_info'),{ok:true,viewport:{x:0,y:0,width:500,height:500}});
  session.complete(session.begin('click'),{ok:true,point:[500,300]});
  assert.equal(session.snapshot().viewport.width,1000);assert.equal(session.snapshot().rings[0].x,500);
  assert.ok(session.snapshot().badge.y+session.snapshot().badge.height<640);
});

test('throwing/rejected observers are disabled with a fixed warning, healthy observers continue',async()=>{
  const warnings=[],scenes=[];const session=create({onObserverError:code=>warnings.push(code)});
  session.subscribe(()=>{throw Error('PRIVATE-ERROR');});
  session.subscribe(()=>Promise.reject(Error('PRIVATE-REJECTION')));
  session.subscribe(scene=>scenes.push(scene));
  await Promise.resolve();await Promise.resolve();
  session.complete(session.begin('wait'),{ok:true});
  assert.deepEqual(warnings,['feedback_observer_disabled','feedback_observer_disabled']);
  assert.ok(scenes.length>=3);assert.ok(!JSON.stringify({warnings,scenes}).includes('PRIVATE'));
});

test('dispose clears pending state and late callbacks cannot resurrect the view',()=>{
  const session=create();const states=[];session.subscribe(scene=>states.push(scene));const id=session.begin('click');
  session.dispose();const count=states.length;session.complete(id,{ok:true,point:[200,100]});session.fail(id,'outcome_unknown');
  session.configure({enabled:true});assert.equal(states.length,count);assert.equal(session.snapshot().visible,false);
  assert.equal(session.snapshot().activeOperations,0);assert.equal(session.begin('click'),null);
});


test('agent pointer expires without new action positions and cannot reappear in another call',()=>{
  let now=0;const session=new FeedbackSession({enabled:true,activeOnly:true,viewport:{x:-100,y:0,width:500,height:500},clock:()=>now});
  const token=session.begin('drag');session.pointer(token,[-20,40]);
  assert.equal(session.snapshot().pointer.x,-20);
  now=751;assert.equal(session.snapshot().pointer,null);
  session.complete(token,{ok:true});session.begin('screenshot');
  session.pointer(token,[30,40]);assert.equal(session.snapshot().pointer,null);
});
