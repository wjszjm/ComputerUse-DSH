import assert from 'node:assert/strict';
import {test,mock} from 'node:test';
import {EventEmitter} from 'node:events';
import {PassThrough,Writable} from 'node:stream';
import {FeedbackSession} from '../dsh-wdc-computer-use/lib/feedback-session.js';

const children=[],scenarios=[];
const geometry={viewport:{x:-1280,y:0,width:3200,height:1080},workArea:{x:0,y:0,width:1920,height:1040},uiScale:1.5,captureExcluded:true};
mock.module('node:child_process',{namedExports:{spawn(command,args,options){
  assert.equal(command,'mock-python');assert.deepEqual(args,['-B','-m','wdc.overlay_host']);
  assert.equal(options.windowsHide,true);assert.equal(options.shell,undefined);
  const scenario=scenarios.shift()||{},child=new EventEmitter();
  child.stdout=new PassThrough();child.stderr=new PassThrough();child.packets=[];child.exitCode=null;child.killed=false;
  child.send=message=>child.stdout.write(JSON.stringify(message)+'\n');
  const exit=()=>{child.exitCode=0;child.stdout.end();child.stderr.end();child.emit('exit',0,null);};
  child.stdin=new Writable({write(chunk,encoding,done){
    const packet=JSON.parse(String(chunk));child.packets.push(packet);
    if(!scenario.noAck)queueMicrotask(()=>{if(child.exitCode===null)child.send({type:'ack',sequence:packet.sequence});});done();
  },final(done){if(!scenario.ignoreEof)exit();done();}});
  child.kill=()=>{child.killed=true;exit();};
  children.push(child);
  if(!scenario.noReady)queueMicrotask(()=>child.send({type:'ready',...geometry,...scenario.geometry}));
  return child;
}}});
const {NativeFeedbackHost,nativeScene}=await import('../dsh-wdc-computer-use/lib/native-feedback-host.js');
const tick=()=>new Promise(setImmediate);
function clock(){
  let now=0,next=0;const tasks=new Map();
  const timers={setTimeout(fn,delay){const id=++next;tasks.set(id,{fn,at:now+delay,interval:0});return id;},
    clearTimeout(id){tasks.delete(id);},setInterval(fn,delay){const id=++next;tasks.set(id,{fn,at:now+delay,interval:delay});return id;},
    clearInterval(id){tasks.delete(id);}};
  return {timers,tasks,get now(){return now;},advance(delta){now+=delta;for(const [id,task] of [...tasks])if(task.at<=now){
    if(task.interval)task.at=now+task.interval;else tasks.delete(id);task.fn();}}};
}
function setup(scenario={},overrides={}){
  const time=clock(),warnings=[],statuses=[];
  const options={python:'mock-python',cwd:'mock-development',feedbackEnabled:true,feedbackNativeEnabled:true,feedbackSettings:{},...overrides};
  const feedback=new FeedbackSession({enabled:options.feedbackEnabled,settings:options.feedbackSettings,clock:()=>time.now});
  const host=new NativeFeedbackHost(options,feedback,value=>warnings.push(value),value=>statuses.push(value),time.timers);
  if(options.feedbackNativeEnabled&&options.feedbackEnabled)scenarios.push(scenario);
  return {host,feedback,time,warnings,statuses};
}

test('native display is opt-in and never starts for the default-disabled configuration',()=>{
  const s=setup({}, {feedbackNativeEnabled:false});const before=children.length;
  assert.equal(s.host.start(),false);assert.equal(children.length,before);assert.equal(s.time.tasks.size,0);s.host.close();
});

test('ready geometry initializes physical viewport, taskbar work area and DPI before display',async()=>{
  const s=setup();assert.equal(s.host.start(),true);await tick();
  try{
    assert.equal(s.host.state,'ready');assert.equal(s.host.status().captureExcluded,true);
    assert.deepEqual(s.feedback.snapshot().viewport,geometry.viewport);assert.equal(s.feedback.settings.uiScale,1.5);
    assert.equal(children.at(-1).packets[0].type,'scene');
    assert.ok(s.feedback.snapshot().badge.y+s.feedback.snapshot().badge.height<1040);
    s.time.advance(500);await tick();assert.equal(children.at(-1).packets.at(-1).type,'heartbeat');
  }finally{s.host.close();await tick();}
  assert.equal(s.time.tasks.size,0);assert.equal(children.at(-1).killed,false);
});

test('lack of verified capture exclusion disables the host before sending any scene',async()=>{
  const s=setup({geometry:{captureExcluded:false}});s.host.start();await tick();
  assert.equal(s.host.state,'failed');assert.equal(children.at(-1).packets.length,0);assert.equal(s.time.tasks.size,0);
});

test('one outstanding packet bounds backpressure and sends only the latest state after acknowledgement',async()=>{
  const s=setup({noAck:true});s.host.start();await tick();const child=children.at(-1);
  try{
    const id=s.feedback.begin('click');s.feedback.complete(id,{ok:true,point:[600,400]});
    assert.equal(child.packets.length,1);
    child.send({type:'ack',sequence:child.packets[0].sequence});await tick();
    assert.equal(child.packets.length,2);assert.equal(child.packets[1].scene.state,'idle');
    assert.deepEqual(child.packets[1].scene.rings.map(r=>[r.x,r.y]),[[600,400]]);
    const pending=s.host.pending;child.send({type:'ack',sequence:pending-1});assert.equal(s.host.pending,pending);
  }finally{s.host.close();await tick();}
});

test('ack timeout fails closed and does not mutate the operation session',async()=>{
  const s=setup({noAck:true});s.host.start();await tick();const id=s.feedback.begin('type_text');
  s.time.advance(1001);await tick();
  assert.equal(s.host.state,'failed');assert.equal(s.host.reason,'ack_timeout');
  assert.equal(s.feedback.state,'typing');s.feedback.complete(id,{ok:true});assert.equal(s.feedback.state,'idle');
  assert.equal(s.time.tasks.size,0);
});

test('startup timeout and ignored EOF terminate only the owned display child',async()=>{
  const s=setup({noReady:true,ignoreEof:true});s.host.start();const child=children.at(-1);
  s.time.advance(2001);assert.equal(s.host.state,'failed');assert.equal(child.killed,false);
  s.time.advance(251);await tick();assert.equal(child.killed,true);assert.equal(s.time.tasks.size,0);
});

test('geometry changes recompute layout and configured scale is preserved',async()=>{
  const s=setup({}, {feedbackSettings:{uiScale:2}});s.host.start();await tick();const child=children.at(-1);
  try{
    const next={...geometry,workArea:{x:0,y:0,width:1920,height:1000},uiScale:1};
    child.send({type:'geometry',...next});await tick();
    assert.equal(s.feedback.settings.uiScale,2);assert.ok(s.feedback.snapshot().badge.y+s.feedback.snapshot().badge.height<1000);
    const sequence=s.host.sequence;child.send({type:'refresh'});await tick();assert.ok(s.host.sequence>sequence);
  }finally{s.host.close();await tick();}
});

test('close cancels frame/heartbeat timers and late worker messages cannot revive it',async()=>{
  const s=setup();s.host.start();await tick();const child=children.at(-1);
  s.feedback.complete(s.feedback.begin('click'),{ok:true,point:[500,300]});await tick();assert.ok(s.time.tasks.size>0);
  s.host.close();await tick();const count=child.packets.length;
  s.host.receive({type:'ready',...geometry});s.feedback.begin('type_text');s.time.advance(1000);
  assert.equal(child.packets.length,count);assert.equal(s.host.state,'closed');assert.equal(s.time.tasks.size,0);
});

test('native scene serialization omits labels, errors, arguments and unknown properties',()=>{
  const session=new FeedbackSession({enabled:true,viewport:geometry.viewport});
  const scene=session.snapshot();scene.label='PRIVATE';scene.error='PRIVATE';scene.arguments={text:'PRIVATE'};
  assert.ok(!JSON.stringify(nativeScene(scene)).includes('PRIVATE'));
});
