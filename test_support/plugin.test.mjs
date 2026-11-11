import assert from 'node:assert/strict';
import { test, mock } from 'node:test';
import * as fs from 'node:fs';
import { EventEmitter } from 'node:events';
import { PassThrough, Writable } from 'node:stream';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const runtime = path.join(root, '.runtime', 'node-tests', `${process.pid}-${Date.now()}`);
fs.mkdirSync(runtime, {recursive:true});
const names = ['screen_info','screenshot','click','move_mouse','drag','scroll','type_text','press_key',
  'clipboard_get','clipboard_set','list_windows','activate_window','window_action','launch_app',
  'run_command','wait','stop','resume'];
const {TOOL_CATALOG} = await import('../dsh-wdc-computer-use/lib/tool-catalog.js');
const mockExec={agent:{session:{}},name:'wdc_test',callId:'test-call',signal:new AbortController().signal};
const children = [];
const displayChildren = [];
const scenarios = [];
const touched = [];
let deniedWritePath;
let denyImages=false;
function checked(file) {
  const target = path.resolve(String(file));
  assert.ok(target.startsWith(runtime + path.sep) || target === runtime, `Non-test filesystem path: ${target}`);
  touched.push(target);
  return target;
}
mock.module('node:fs', {namedExports:Object.fromEntries(
  ['mkdirSync','readdirSync','statSync','unlinkSync','writeFileSync'].map(name =>
    [name, (file,...args) => {
      const target=checked(file);
      if(name==='writeFileSync' && (target===deniedWritePath || (denyImages && /shot-.*\.png$/.test(target)))) throw Object.assign(Error('mock access denied'),{code:'EACCES'});
      return fs[name](target,...args);
    }]))});
mock.module('node:child_process', {namedExports:{spawn(command,args,options) {
  assert.equal(path.resolve(command), path.join(root,'.venv','Scripts','python.exe'));
  assert.equal(options.cwd, path.join(root,'win-desktop-control'));
  const child = new EventEmitter();
  child.stdout = new PassThrough(); child.stderr = new PassThrough(); child.killed = false;
  if(args.includes('wdc.overlay_host')) {
    assert.deepEqual(args,['-B','-m','wdc.overlay_host']);assert.equal(options.windowsHide,true);
    child.packets=[];child.exitCode=null;
    const exit=()=>{child.exitCode=0;child.stdout.end();child.stderr.end();child.emit('exit',0,null);};
    child.stdin=new Writable({write(chunk,encoding,done){
      const packet=JSON.parse(String(chunk));child.packets.push(packet);
      queueMicrotask(()=>{if(child.exitCode===null)child.stdout.write(JSON.stringify({type:'ack',sequence:packet.sequence})+'\n');});done();
    },final(done){exit();done();}});
    child.kill=()=>{child.killed=true;exit();};displayChildren.push(child);
    queueMicrotask(()=>child.stdout.write(JSON.stringify({type:'ready',captureExcluded:true,uiScale:1,
      viewport:{x:0,y:0,width:1920,height:1080},workArea:{x:0,y:0,width:1920,height:1040}})+'\n'));
    return child;
  }
  assert.equal(options.env.WDC_STOP_FILE.startsWith(runtime),true);
  const scenario = scenarios.shift() || {};
  child.requests = [];
  child.reply = (id,result) => child.stdout.write(JSON.stringify({jsonrpc:'2.0',id,result})+'\n');
  child.stdin = new Writable({write(chunk,encoding,done) {
    for (const line of String(chunk).trim().split('\n')) {
      const request = JSON.parse(line);
      if (!request.id) continue;
      child.requests.push(request);
      if(scenario.listError && request.method==='tools/list') {
        queueMicrotask(()=>child.stdout.write(JSON.stringify({jsonrpc:'2.0',id:request.id,error:{code:-32603,message:'mock list failure'}})+'\n'));
        continue;
      }
      if (scenario.killName && scenario.killName === request.params?.name) { child.kill(); continue; }
      if ((scenario.hangMethod && scenario.hangMethod === request.method) || (scenario.hangName && scenario.hangName === request.params?.name) || scenario.hangNames?.includes(request.params?.name)) continue;
      const result = request.method === 'initialize' ? {serverInfo:{name:'memory-server'}, instructions:'Mock safety instructions'}
        : request.method === 'tools/list' ? {tools:scenario.catalogMismatch ? [] : TOOL_CATALOG.tools}
        : (typeof scenario.callResult==='function'?scenario.callResult(request):scenario.callResult) || {content:[{type:'text',text:JSON.stringify({ok:true,action:request.params.name})}]};
      queueMicrotask(()=>{if(!child.killed) child.reply(request.id,result);});
    }
    done();
  }});
  child.kill = () => { child.killed=true; child.stdout.end(); child.stderr.end(); child.emit('exit',0,null); };
  children.push(child);
  return child;
}}});
const {apply, McpBridge,annotateSchema,pruneSchema,buildDescription} = await import('../dsh-wdc-computer-use/lib/index.js');
const options = {
  python:path.join(root,'.venv','Scripts','python.exe'),
  cwd:path.join(root,'win-desktop-control'),
  permissionMode:'autonomous',
  shotDir:runtime, initTimeoutMs:1000, callTimeoutMs:1000,
  stopFile:path.join(runtime,'stop.flag'),
};

test('bridge handshake, tool list and call use an in-memory child', async () => {
  const bridge = new McpBridge(options,()=>{});
  try {
    await bridge.start();
    assert.equal((await bridge.listTools()).length,18);
    const result = await bridge.callTool('type_text',{text:'memory only'});
    assert.equal(JSON.parse(result.content[0].text).action,'type_text');
  } finally { bridge.close(); }
  assert.equal(children.at(-1).killed,true);
});

for (const [profile,count] of [['minimal',9],['standard',14],['full',17]]) {
  test(`apply ${profile}: registration, execution, status and disposal stay isolated`, async () => {
    const registered = [], cleanups = [], disposed = [];
    let resolveReady;
    const ready = new Promise(resolve => {resolveReady=resolve;});
    const statusPath = path.join(runtime,`${profile}-status.json`);
    apply({logger:{warn(message) { if(message.includes('已注册')) resolveReady(); }},
      inject(deps,callback) { callback({tools:{register(tool) {
        registered.push({...tool,execute:(args,exec=mockExec)=>tool.execute(args,exec)}); return ()=>disposed.push(tool.name);
      }},effect(factory) { cleanups.push(factory()); }}); }
    }, {...options,statusPath,profile});
    try {
      await Promise.race([ready,new Promise((_,reject)=>{
        const timer=setTimeout(()=>reject(Error('Mock registration timed out')),2000); timer.unref();
      })]);
      assert.equal(registered.length,count);
      assert.ok(registered.every(tool=>tool.name.startsWith('wdc_')));
      const tool=registered.find(tool=>tool.name==='wdc_screenshot');
      const result=await tool.execute({});
      assert.equal(result.ok,true);
      assert.equal(tool.output.render({},result)[0].type,'text');
      assert.equal(JSON.parse(fs.readFileSync(statusPath,'utf8')).registered,count);
    } finally { for(const cleanup of cleanups) cleanup(); }
    assert.equal(disposed.length,count);
    assert.equal(children.at(-1).killed,true);
  });
}

test('all plugin file mutations stayed inside its disposable test directory', () => {
  assert.ok(touched.length>0);
  assert.ok(children.every(child=>child.killed));
});

let caseId = 0;
function faultBridge(scenario = {}, overrides = {}) {
  scenarios.push(scenario);
  return new McpBridge({...options,stopFile:path.join(runtime,`fault-${++caseId}.flag`),...overrides},()=>{});
}

test('write completed before worker exit is never replayed', async () => {
  const b=faultBridge({killName:'type_text'});
  await b.start(); const child=b.child, before=children.length;
  try {
    await assert.rejects(b.callTool('type_text',{text:'only once'}),e=>e.code==='outcome_unknown');
    assert.equal(children.length,before);
    assert.equal(child.requests.filter(r=>r.params?.name==='type_text').length,1);
    assert.equal(b.pending.size,0);
    assert.equal(b.isStopped(),true);
    await assert.rejects(b.callTool('click',{x:1,y:1}),e=>e.code==='emergency_stop');
    await b.callTool('resume',{});
    assert.equal(b.isStopped(),false);
  } finally { b.close(); }
});

test('read-only recovery is shared and stale exits cannot reject new requests', async () => {
  const b=faultBridge({killName:'screenshot'});
  await b.start(); const old=b.child, before=children.length;
  try {
    await Promise.all([b.callTool('screenshot',{}),b.callTool('screen_info',{})]);
    assert.equal(children.length,before+1);
    const current=b.child;
    const response=b.request('tools/list',{},1000);
    old.emit('exit',1,null);
    assert.equal(b.child,current);
    assert.equal((await response).tools.length,18);
  } finally { b.close(); }
});

test('input timeout latches stop, rejects overlap and refuses premature resume', async () => {
  const b=faultBridge({hangName:'type_text'},{callTimeoutMs:15});
  await b.start(); const child=b.child;
  try {
    const input=b.callTool('type_text',{text:'unknown'});
    await assert.rejects(b.callTool('press_key',{keys:'enter'}),e=>e.code==='action_busy');
    await assert.rejects(input,e=>e.code==='outcome_unknown');
    await assert.rejects(b.callTool('resume',{}),e=>e.code==='action_busy');
    await assert.rejects(b.restart(),e=>e.code==='action_busy');
    assert.equal(child.requests.filter(r=>r.params?.name==='type_text').length,1);
    assert.equal(b.pending.size,0);
    const original=child.requests.find(r=>r.params?.name==='type_text');
    child.reply(original.id,{content:[{type:'text',text:'{"ok":true}'}]});
    assert.equal(b.isStopped(),true); // A late reply must not silently resume input.
    await b.callTool('resume',{});
    assert.equal(b.isStopped(),false);
  } finally { b.close(); }
});

test('stop signal is immediate during a pending call and survives a new bridge', async () => {
  const b=faultBridge({hangName:'type_text'});
  await b.start(); const child=b.child;
  try {
    const input=b.callTool('type_text',{text:'pending'});
    const stopped=await b.callTool('stop',{});
    assert.equal(JSON.parse(stopped.content[0].text).stopped,true);
    assert.equal(fs.readFileSync(b.stopFile(),'utf8'),'stop\n');
    const next=new McpBridge({...options,stopFile:b.stopFile()},()=>{});
    await assert.rejects(next.callTool('click',{x:1,y:1}),e=>e.code==='emergency_stop');
    next.close();
    await assert.rejects(b.callTool('resume',{}),e=>e.code==='action_busy');
    child.reply(child.requests.at(-1).id,{isError:true,content:[{type:'text',text:'{"ok":false,"error_code":"emergency_stop"}'}]});
    await input;
    await b.callTool('resume',{});
    assert.equal(b.isStopped(),false);
  } finally { b.close(); }
});

test('close and failed handshake reject pending work and clear timers', async () => {
  const failed=faultBridge({hangMethod:'initialize'},{initTimeoutMs:10});
  await assert.rejects(failed.start(),e=>e.code==='timeout');
  assert.equal(failed.child,null); assert.equal(failed.pending.size,0); assert.equal(failed.exits.size,0);
  failed.close();
  const b=faultBridge({hangName:'screenshot'});
  await b.start();
  const pending=b.callTool('screenshot',{});
  b.close();
  await assert.rejects(pending,e=>e.code==='bridge_closed');
  assert.equal(b.pending.size,0);
});

async function mounted(scenario,config={}) {
  scenarios.push(scenario);
  const registered=[],cleanups=[],hooks=new Map(),commands=new Map();
  let ready, attachPresets;
  const done=new Promise(resolve=>{ready=resolve;});
  const policy=apply({get:name=>({approval:config.testApproval,permissionPresets:config.testPresets})[name],
    on(name,handler){hooks.set(name,handler);},
    logger:{warn(message){if(message.includes('已注册'))ready();}},inject(deps,callback){
    if(deps.includes('permissionPresets')){
      attachPresets=service=>callback({permissionPresets:service,effect:factory=>cleanups.push(factory())});
      return;
    }
    if(deps.includes('commands')){
      callback({commands:{register(command){commands.set(command.name,command);}}});
      return;
    }
    callback({tools:{register(tool){registered.push({...tool,execute:(args,exec=mockExec)=>tool.execute(args,exec)}); return ()=>{};}},effect(factory){cleanups.push(factory());}});
  }},{...options,stopFile:path.join(runtime,`mount-${++caseId}.flag`),statusPath:path.join(runtime,`mount-${caseId}.json`),...config});
  await done;
  assert.equal(typeof policy,'function','Cordis apply must return an effect disposer');
  return {tools:registered,policy:policy.permissions,hooks,commands,attachPresets,close(){for(const cleanup of cleanups)cleanup();policy();}};
}

test('host sees legacy business errors as failures and safety controls cannot be hidden', async () => {
  const m=await mounted({callResult:{isError:false,content:[{type:'text',text:'{"ok":false,"error_code":"read_only"}'}]}},
    {profile:'minimal',hideTools:['stop','resume']});
  try {
    assert.ok(m.tools.some(t=>t.name==='wdc_stop'));
    assert.ok(m.tools.some(t=>t.name==='wdc_resume'));
    assert.equal((await m.tools.find(t=>t.name==='wdc_click').execute({x:1,y:1})).ok,false);
    assert.match(m.tools.find(t=>t.name==='wdc_screenshot').description,/标准循环/);
  } finally { m.close(); }
});

test('a new stop wins over a resume that is awaiting a server reply', async () => {
  const b=faultBridge({hangName:'resume'});
  await b.start(); const child=b.child;
  try {
    await b.callTool('stop',{});
    const resuming=b.callTool('resume',{});
    await b.callTool('stop',{});
    child.reply(child.requests.at(-1).id,{content:[{type:'text',text:'{"ok":true}'}]});
    await assert.rejects(resuming,e=>e.code==='emergency_stop');
    assert.equal(b.isStopped(),true);
  } finally { b.close(); }
});

test('cold writes start lazily, and restart cannot interrupt an active input', async () => {
  const b=faultBridge({hangName:'type_text'});
  const before=children.length;
  await b.callTool('click',{coordinate_mode:'screen',x:1,y:1});
  assert.equal(children.length,before+1);
  const child=b.child;
  try {
    const input=b.callTool('type_text',{text:'pending'});
    await assert.rejects(b.restart(),e=>e.code==='action_busy');
    child.reply(child.requests.at(-1).id,{content:[{type:'text',text:'{"ok":true}'}]});
    await input;
    assert.equal(child.killed,false);
  } finally { b.close(); }
});

test('replacement worker waits for actual old-worker exit', async () => {
  const b=faultBridge();
  await b.start(); const old=b.child, before=children.length;
  old.kill=()=>{old.killed=true;};
  try {
    const restarting=b.restart();
    await Promise.resolve();
    assert.equal(children.length,before);
    assert.equal(b.child,null);
    old.emit('exit',0,null); old.stdout.end(); old.stderr.end();
    await restarting;
    assert.equal(children.length,before+1);
    assert.notEqual(b.child,old);
  } finally { b.close(); }
});

test('failed stop-file write reports failure and still blocks new local input', async () => {
  const b=faultBridge();
  await b.start();
  deniedWritePath=b.stopFile();
  try {
    await assert.rejects(b.callTool('stop',{}),e=>e.code==='stop_signal_failed');
    await assert.rejects(b.callTool('click',{x:1,y:1}),e=>e.code==='emergency_stop');
  } finally { deniedWritePath=undefined; b.close(); }
});

const feedbackOptions={feedbackEnabled:true,feedbackViewport:{x:0,y:0,width:1920,height:1080},
  feedbackWorkArea:{x:0,y:0,width:1920,height:1040}};

test('bridge feeds actual final screen points to the view without leaking input/result text', async () => {
  const b=faultBridge({callResult:{content:[{type:'text',text:JSON.stringify({ok:true,action:'click',screen_point:[800,600],requested_point:[10,20],private:'SECRET-RESULT'})}]}},feedbackOptions);
  const scenes=[];b.feedback.subscribe(scene=>scenes.push(scene));
  await b.start();
  try {
    await b.callTool('click',{x:10,y:20,text:'SECRET-INPUT'});
    const scene=b.feedback.snapshot();assert.equal(scene.rings.length,0);
    assert.equal(scene.visible,false);assert.equal(scene.animated,false);
    assert.equal(scene.state,'idle');assert.ok(scenes.some(s=>s.state==='clicking'));
    assert.ok(!JSON.stringify(scenes).includes('SECRET'));
  } finally {b.close();}
  assert.equal(b.feedback.snapshot().visible,false);
});

test('bridge completion order keeps the active input visible',async()=>{
  const b=faultBridge({hangNames:['screenshot','type_text']},feedbackOptions);
  const states=[];b.feedback.subscribe(scene=>states.push(scene.state));
  await b.start();const child=b.child;
  try {
    const observing=b.callTool('screenshot',{});const typing=b.callTool('type_text',{text:'private'});
    assert.equal(b.feedback.state,'typing');
    await assert.rejects(b.callTool('click',{x:1,y:2}),e=>e.code==='action_busy');
    assert.ok(!states.includes('clicking')); // Rejected input was never actually attempted.
    child.reply(child.requests.find(r=>r.params?.name==='screenshot').id,{content:[{type:'text',text:'{"ok":true}'}]});
    await observing;assert.equal(b.feedback.state,'typing');
    child.reply(child.requests.find(r=>r.params?.name==='type_text').id,{content:[{type:'text',text:'{"ok":true}'}]});
    await typing;assert.equal(b.feedback.state,'idle');
  } finally {b.close();}
});

test('bridge shows stopping until cancelled input completes, then stays stopped through observation',async()=>{
  const b=faultBridge({hangName:'type_text'},feedbackOptions);
  await b.start();const child=b.child;
  try {
    const input=b.callTool('type_text',{text:'pending'});assert.equal(b.feedback.state,'typing');
    await b.callTool('stop',{});assert.equal(b.feedback.state,'stopping');
    await b.callTool('screenshot',{});assert.equal(b.feedback.state,'stopping');
    child.reply(child.requests.find(r=>r.params?.name==='type_text').id,{isError:true,content:[{type:'text',text:'{"ok":false,"error_code":"emergency_stop"}'}]});
    await input;assert.equal(b.feedback.state,'stopped');
    await b.callTool('list_windows',{});assert.equal(b.feedback.state,'stopped');
    await b.callTool('resume',{});assert.equal(b.feedback.state,'idle');
  } finally {b.close();}
});

test('unknown click outcome stays an error and a late reply never draws a stale ring',async()=>{
  const b=faultBridge({hangName:'click'},{...feedbackOptions,callTimeoutMs:15});
  await b.start();const child=b.child;
  try {
    await assert.rejects(b.callTool('click',{x:1,y:2}),e=>e.code==='outcome_unknown');
    assert.equal(b.feedback.state,'error');
    const id=child.requests.find(r=>r.params?.name==='click').id;
    child.reply(id,{content:[{type:'text',text:'{"ok":true,"action":"click","screen_point":[600,400]}'}]});
    assert.equal(b.feedback.snapshot().rings.length,0);assert.equal(b.feedback.state,'error');
    await b.callTool('resume',{});assert.equal(b.feedback.state,'idle');
  } finally {b.close();}
});

test('observer failure or invalid feedback settings never fail an otherwise valid tool',async()=>{
  const b=faultBridge({},feedbackOptions);const states=[];
  b.feedback.subscribe(()=>{throw Error('private observer error');});
  b.feedback.subscribe(scene=>states.push(scene.state));
  await b.start();
  try{const result=await b.callTool('wait',{ms:1});assert.equal(JSON.parse(result.content[0].text).ok,true);assert.ok(states.includes('waiting'));}
  finally{b.close();}
  const invalid=faultBridge({},{...feedbackOptions,feedbackSettings:{opacity:NaN}});
  await invalid.start();try{await invalid.callTool('screen_info',{});assert.equal(invalid.feedback.settings.enabled,false);}
  finally{invalid.close();}
});

test('hiding feedback mid-call does not affect input or replay its hidden point',async()=>{
  const b=faultBridge({hangName:'click'},feedbackOptions);
  await b.start();const child=b.child;
  try {
    const input=b.callTool('click',{x:1,y:2});b.feedback.configure({enabled:false});
    child.reply(child.requests.at(-1).id,{content:[{type:'text',text:'{"ok":true,"action":"click","screen_point":[600,400]}'}]});
    await input;assert.equal(b.feedback.snapshot().visible,false);
    b.feedback.configure({enabled:true});assert.equal(b.feedback.snapshot().rings.length,0);
  } finally{b.close();}
});

test('bridge feedback drives the actual SVG renderer and disposal cancels its animation',async()=>{
  const {mountFeedback}=await import('../dsh-wdc-computer-use/lib/visual-feedback.js');
  const b=faultBridge({callResult:{content:[{type:'text',text:'{"ok":true,"action":"click","screen_point":[800,600]}'}]}},feedbackOptions);
  const container={innerHTML:''},frames=new Map();let next=0;
  const view=mountFeedback(container,b.feedback,{requestFrame:fn=>{frames.set(++next,fn);return next;},cancelFrame:id=>frames.delete(id)});
  const unsubscribe=b.feedback.subscribe(()=>view.render());
  await b.start();
  try {
    await b.callTool('click',{x:10,y:20});
    assert.doesNotMatch(container.innerHTML,/data-click-x/);
    assert.doesNotMatch(container.innerHTML,/data-feedback-badge/);
    assert.equal(frames.size,0);
    b.close();assert.equal(frames.size,0);assert.doesNotMatch(container.innerHTML,/data-feedback-badge/);
  }finally{unsubscribe();view.destroy();b.close();}
});

for(const listError of [false,true])test(`apply native opt-in is dormant until a call, then closes (listError=${listError})`,async()=>{
  const before=children.length, displaysBefore=displayChildren.length;
  const m=await mounted({listError,hangName:listError?undefined:'click'},
    {feedbackEnabled:true,feedbackNativeEnabled:true});
  try {
    assert.equal(children.length,before);assert.equal(displayChildren.length,displaysBefore);
    assert.equal(m.tools.length,14);
    const pending=m.tools.find(t=>t.name==='wdc_click').execute({x:10,y:20});
    await new Promise(setImmediate);
    if(!listError){
      const child=children.at(-1);
      assert.ok(displayChildren.at(-1).packets.some(p=>p.scene?.state==='clicking'));
      child.reply(child.requests.find(r=>r.params?.name==='click').id,{content:[{type:'text',text:'{"ok":true}'}]});
    }
    const result=await pending;assert.equal(result.ok,!listError);
    await new Promise(setImmediate);
    assert.equal(displayChildren.at(-1).exitCode,0);
  } finally {m.close();}
  assert.equal(children.at(-1).killed,true);
});

test('frame binding and window identity survive schema pruning and reach the worker unchanged',async()=>{
  const schema=annotateSchema('press_key',pruneSchema({type:'object',properties:{frame_id:{type:'string',title:'Frame Id'},keys:{type:'string'}}}));
  assert.match(schema.properties.frame_id.description,/fresh screenshot/);
  const b=faultBridge();await b.start();
  try{
    await b.callTool('click',{x:20,y:30,frame_id:'observed-frame',coordinate_mode:'image'});
    await b.callTool('window_action',{action:'maximize',handle:123,expected_pid:44,timeout:2});
    const calls=b.child.requests.filter(r=>r.method==='tools/call');
    assert.equal(calls[0].params.arguments.frame_id,'observed-frame');
    assert.equal(calls[1].params.arguments.expected_pid,44);
    assert.match(buildDescription('type_text','',''),/clipboard contents do not prove/);
    assert.match(buildDescription('screenshot','',''),/not_verified/);
  }finally{b.close();}
});

test('window verification failure remains a failure without replay or a success ring',async()=>{
  const b=faultBridge({callResult:{isError:true,content:[{type:'text',text:JSON.stringify({ok:false,error_code:'verification_failed',sent:true,
    verification:{status:'failed',method:'window_state_readback'}})}]}},feedbackOptions);
  await b.start();const before=children.length;
  try{
    const result=await b.callTool('window_action',{action:'close',handle:123,expected_pid:44});
    assert.equal(result.isError,true);assert.equal(JSON.parse(result.content[0].text).sent,true);
    assert.equal(b.child.requests.filter(r=>r.params?.name==='window_action').length,1);
    assert.equal(children.length,before);assert.equal(b.feedback.snapshot().rings.length,0);
    assert.equal(b.feedback.state,'error');
  }finally{b.close();}
});

// Stage 9: exercise the registered DSH execute/render boundary, not just the bridge.
for (const mode of ['file','requested-direct','disk-error']) test(`DSH screenshot delivery ${mode}`,async()=>{
  const data='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jG1sAAAAASUVORK5CYII=';
  scenarios.push({callResult:{content:[{type:'text',text:JSON.stringify({ok:true,frame_id:'frame-test'})},{type:'image',mimeType:'image/png',data}]}});
  const registered=[],cleanups=[];let done;
  const ready=new Promise(resolve=>{done=resolve;});
  apply({logger:{warn(message){if(message.includes('已注册'))done();}},inject(deps,callback){callback({tools:{register(tool){registered.push({...tool,execute:(args,exec=mockExec)=>tool.execute(args,exec)});return ()=>{};}},effect(factory){cleanups.push(factory());}});}},
    {...options,statusPath:path.join(runtime,`delivery-${mode}.json`),passImageContent:mode==='requested-direct'});
  try {
    await ready;
    const tool=registered.find(t=>t.name==='wdc_screenshot');
    // Deny only generated image writes, keep the process and all other I/O mocked.
    denyImages=mode==='disk-error';
    const value=await tool.execute({});const rendered=tool.output.render({},value);
    assert.equal(value.ok,mode!=='disk-error');
    if(mode==='disk-error') assert.match(value.text,/image_delivery_failed/);
    else {
      const match=rendered[0].text.match(/\[截图已保存\] ([^\n]+)/);assert.ok(match,'DSH must receive a usable image reference');
      assert.deepEqual(fs.readFileSync(match[1]),Buffer.from(data,'base64'));
      assert.match(rendered[0].text,/frame-test/);
    }
    assert.equal(children.at(-1).requests.filter(q=>q.params?.name==='screenshot').length,1);
  } finally {denyImages=false;for(const cleanup of cleanups)cleanup();}
});

test('DSH bridge repeatable isolated measurement',async()=>{
  const reports=[];
  for(const feedbackEnabled of [false,true]) {
    const b=faultBridge({}, {feedbackEnabled});await b.start();
    try {
      for(let i=0;i<20;i++)await b.callTool('wait',{ms:0});
      const samples=[],cpu=process.cpuUsage(),rss=process.memoryUsage().rss;
      for(let i=0;i<200;i++) {
        const start=performance.now();const result=await b.callTool('wait',{ms:0});
        samples.push(performance.now()-start);assert.equal(JSON.parse(result.content[0].text).ok,true);
      }
      samples.sort((a,b)=>a-b);
      reports.push({feedbackEnabled,cases:200,successes:200,p50_ms:samples[99],p95_ms:samples[189],cpu_us:process.cpuUsage(cpu),rss_delta_bytes:process.memoryUsage().rss-rss,pending:b.pending.size});
      assert.equal(b.pending.size,0);
    }finally{b.close();}
  }
  fs.writeFileSync(path.join(root,'.runtime','stage9-benchmark.json'),JSON.stringify({scope:'in-memory DSH bridge; excludes model, desktop, real transport and native display',reports},null,2));
});

const tick=()=>new Promise(setImmediate);
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));

test('registration and local stop never spawn a worker; status separates registration from runtime',async()=>{
  const count=children.length,displayCount=displayChildren.length;
  const m=await mounted({}, {feedbackEnabled:true,feedbackNativeEnabled:true});
  try {
    await delay(20);
    assert.equal(children.length,count);assert.equal(displayChildren.length,displayCount);
    const status=JSON.parse(fs.readFileSync(path.join(runtime,`mount-${caseId}.json`),'utf8'));
    assert.equal(status.stage,'registered');assert.equal(status.runtime.state,'dormant');
    await m.tools.find(t=>t.name==='wdc_stop').execute({});
    assert.equal(children.length,count);assert.equal(displayChildren.length,displayCount);
    const blocked=await m.tools.find(t=>t.name==='wdc_click').execute({x:1,y:2});
    assert.equal(blocked.ok,false);assert.match(blocked.text,/emergency_stop/);
    assert.equal(children.length,count);
  }finally{m.close();scenarios.pop();}
});

test('simultaneous cold reads share one handshake, and idle time sends no desktop calls',async()=>{
  const b=faultBridge({}, {idleTimeoutMs:25}),count=children.length;
  try {
    await Promise.all([b.callTool('screenshot',{}),b.callTool('screen_info',{})]);
    assert.equal(children.length,count+1);
    const child=b.child,requests=child.requests.length;
    await delay(60);
    assert.equal(child.requests.length,requests);assert.equal(child.killed,true);
    assert.equal(b.child,null);assert.equal(b.idleTimer,null);
    await b.callTool('screenshot',{});assert.equal(children.length,count+2);
  }finally{b.close();}
});

test('stop during cold input handshake prevents dispatch and survives process recycling',async()=>{
  const b=faultBridge({hangMethod:'initialize'},{idleTimeoutMs:20});
  try {
    const input=b.callTool('type_text',{text:'never send'});await tick();
    const child=b.child;await b.callTool('stop',{});
    child.reply(child.requests[0].id,{serverInfo:{name:'mock'}});
    await assert.rejects(input,e=>e.code==='emergency_stop');
    assert.ok(!child.requests.some(r=>r.method==='tools/call'));
    await delay(50);assert.equal(b.child,null);
    const count=children.length;
    await assert.rejects(b.callTool('click',{x:1,y:2}),e=>e.code==='emergency_stop');
    assert.equal(children.length,count);
    await b.callTool('resume',{});assert.equal(b.isStopped(),false);
  }finally{b.close();}
});

test('new call during delayed idle exit waits for old worker and does not overlap',async()=>{
  const b=faultBridge({}, {idleTimeoutMs:20});
  try {
    await b.callTool('wait',{});const old=b.child,count=children.length;
    old.kill=()=>{old.killed=true;};
    await delay(50);assert.equal(b.child,null);
    const pending=b.callTool('wait',{});await tick();
    assert.equal(children.length,count);
    old.emit('exit',0,null);old.stdout.end();old.stderr.end();
    await pending;assert.equal(children.length,count+1);
  }finally{b.close();}
});

test('active and unknown inputs are never retired by an idle timer',async()=>{
  const b=faultBridge({hangName:'type_text'},{idleTimeoutMs:10,callTimeoutMs:25});
  try {
    const input=b.callTool('type_text',{text:'unknown'});
    const rejection=assert.rejects(input,e=>e.code==='outcome_unknown');
    await tick();const child=b.child;
    await delay(15);assert.equal(child.killed,false);assert.equal(b.idleTimer,null);
    await rejection;await delay(25);
    assert.equal(child.killed,false);assert.equal(b.idleTimer,null);
    child.reply(child.requests.find(r=>r.params?.name==='type_text').id,{content:[{type:'text',text:'{"ok":true}'}]});
    await delay(30);assert.equal(child.killed,true);assert.equal(b.isStopped(),true);
  }finally{b.close();}
});

test('idle restart rejects old implicit image coordinates before sending input',async()=>{
  const b=faultBridge({}, {idleTimeoutMs:15});
  try {
    await b.callTool('screenshot',{});await delay(40);
    await assert.rejects(b.callTool('click',{x:1,y:2}),e=>e.code==='stale_frame');
    assert.ok(!b.child.requests.some(r=>r.params?.name==='click'));
    assert.equal(b.isStopped(),false);
    await b.callTool('screenshot',{});await b.callTool('click',{x:1,y:2});
    assert.equal(b.child.requests.filter(r=>r.params?.name==='click').length,1);
  }finally{b.close();}
});

test('catalog mismatch rejects the first action, without input replay or a false unknown-outcome latch',async()=>{
  const m=await mounted({catalogMismatch:true});
  try {
    const result=await m.tools.find(t=>t.name==='wdc_type_text').execute({text:'never send'});
    assert.equal(result.ok,false);assert.match(result.text,/catalog_mismatch/);
    assert.ok(!children.at(-1).requests.some(r=>r.method==='tools/call'));
    assert.equal(children.at(-1).killed,true);
  }finally{m.close();}
});

test('unload during cold start cannot dispatch a late input',async()=>{
  const b=faultBridge({hangMethod:'initialize'});
  const input=b.callTool('type_text',{text:'never send'});
  const rejected=assert.rejects(input,e=>e.code==='bridge_closed');
  await tick();const child=b.child;b.close();await rejected;
  assert.ok(!child.requests.some(r=>r.method==='tools/call'));assert.equal(b.idleTimer,null);
});

test('subsequent native calls reopen only after closing the previous display; idle has no animation',async()=>{
  const m=await mounted({}, {feedbackEnabled:true,feedbackNativeEnabled:true});
  try {
    for(let i=0;i<2;i++){
      const result=await m.tools.find(t=>t.name==='wdc_wait').execute({ms:0});
      assert.equal(result.ok,true);await tick();
      const child=displayChildren.at(-1),count=child.packets.length;
      assert.equal(child.exitCode,0);await delay(20);assert.equal(child.packets.length,count);
    }
    assert.ok(displayChildren.slice(-2).every(c=>c.exitCode===0));
  }finally{m.close();}
});


test('pointer events bind live request, ignore stale children/sequences, and vanish on stop/completion',async()=>{
  const b=faultBridge({hangName:'click'},feedbackOptions);await b.start();
  try {
    const pending=b.callTool('click',{x:10,y:20});const child=b.child;
    const request=child.requests.find(r=>r.params?.name==='click');
    const packet={id:String(request.id),sequence:1,point:[800,600]};
    child.stderr.write('WDC_POINTER '+JSON.stringify(packet)+'\n');
    assert.deepEqual(b.feedback.snapshot().pointer,{x:800,y:600,scale:1});
    b.handlePointer(JSON.stringify({...packet,sequence:2,point:[1,2]}),{});
    b.handlePointer(JSON.stringify({...packet,point:[3,4]}),child);
    assert.equal(b.feedback.snapshot().pointer.x,800);
    const {feedbackSvg}=await import('../dsh-wdc-computer-use/lib/visual-feedback.js');
    assert.match(feedbackSvg(b.feedback.snapshot()),/data-agent-pointer/);
    await b.callTool('stop',{});assert.equal(b.feedback.snapshot().pointer,null);
    child.reply(request.id,{content:[{type:'text',text:'{"ok":true}'}]});await pending;
    b.handlePointer(JSON.stringify({...packet,sequence:3}),child);
    assert.equal(b.feedback.snapshot().pointer,null);
  }finally{b.close();}
});


const execution=(name='wdc_click',session={})=>({name,agent:{session},callId:'permission-test',signal:new AbortController().signal});
for(const answer of ['rejected','cancelled','unavailable','unknown'])test(`approval ${answer} has zero worker/display side effects`,async()=>{
  const before=children.length,displays=displayChildren.length;
  const m=await mounted({}, {permissionMode:'ask',feedbackEnabled:true,feedbackNativeEnabled:true,testApproval:{request:async()=>answer}});
  try{
    const result=await m.tools.find(t=>t.name==='wdc_click').execute({x:1,y:2},execution());
    assert.equal(result.ok,false);assert.equal(children.length,before);assert.equal(displayChildren.length,displays);
  }finally{m.close();scenarios.pop();}
});

test('older hosts without presets still ask per call before startup',async()=>{
  let requests=0;const before=children.length;
  const m=await mounted({}, {permissionMode:'ask',testApproval:{request:async req=>{
    requests++;assert.equal(req.toolName,'wdc_click');assert.equal(req.callId,'permission-test');
    if(requests===1)assert.equal(children.length,before);
    return requests===1?'allowed-once':'rejected';
  }}});
  try{
    const tool=m.tools.find(t=>t.name==='wdc_click'),exec=execution();
    assert.equal((await tool.execute({x:1,y:2},exec)).ok,true);
    assert.equal((await tool.execute({x:1,y:2},exec)).ok,false);
    assert.equal(children.at(-1).requests.filter(r=>r.params?.name==='click').length,1);assert.equal(requests,2);
  }finally{m.close();}
});

test('preset service may become available after the desktop tools register',async()=>{
  let requests=0;
  const m=await mounted({}, {permissionMode:'ask',
    testApproval:{request:async()=>{requests++;return 'allowed-once';}},
  });
  const session={};
  const tool=m.tools.find(t=>t.name==='wdc_wait');
  const run=()=>tool.execute({ms:1},execution(tool.name,session));
  try {
    assert.equal((await run()).ok,true);
    assert.equal((await run()).ok,true);
    m.attachPresets({current:()=> 'workspace-write'});
    assert.equal((await run()).ok,true);
    assert.equal((await run()).ok,true);
    assert.equal(requests,3);
    const result=await m.commands.get('wdc-access').handler({agent:{session},rawInput:''});
    assert.match(result.text,/DSH 当前预设：workspace-write/);
  } finally {m.close();}
});

test('manual approvals remember separate observation and control grants in one session',async()=>{
  const requested=[];
  const m=await mounted({}, {permissionMode:'ask',
    testPresets:{current:()=> 'workspace-write'},
    testApproval:{request:async request=>{requested.push(request);return 'allowed-once';}},
  });
  const session={};
  const call=(name,args={},target=session)=>m.tools.find(t=>t.name===name).execute(args,execution(name,target));
  try {
    for(let i=0;i<5;i++) assert.equal((await call('wdc_wait',{ms:1})).ok,true);
    for(let i=0;i<2;i++) assert.equal((await call('wdc_click',{x:1,y:2})).ok,true);
    assert.deepEqual(requested.map(r=>r.toolName),['wdc_wait','wdc_click']);
    assert.ok(requested.every(r=>r.reason.includes('本会话')));
    assert.equal((await call('wdc_wait',{ms:1},{})).ok,true);
    assert.equal(requested.length,3);
    const command=m.commands.get('wdc-access');
    assert.ok(command);
    assert.ok(!m.tools.some(tool=>tool.name.includes('access')));
    assert.equal((await command.handler({agent:{session},rawInput:'revoke'})).kind,'success');
    assert.equal((await call('wdc_wait',{ms:1})).ok,true);
    assert.equal(requested.length,4);
    m.hooks.get('session/event')(session,{type:'permission/preset'});
    assert.equal((await call('wdc_wait',{ms:1})).ok,true);
    assert.equal(requested.length,5);
  } finally {m.close();}
});

test('concurrent observation approvals share one prompt and revocation aborts all callers',async()=>{
  const {SessionPermissions}=await import('../dsh-wdc-computer-use/lib/permissions.js');
  let requests=0, resolveApproval;
  const policy=new SessionPermissions({
    getPresets:()=>({current:()=> 'custom'}),
    getApproval:()=>({request:()=>{requests++;return new Promise(resolve=>{resolveApproval=resolve;});}}),
  });
  const session={};
  const first=policy.acquire('screenshot',execution('wdc_screenshot',session));
  const second=policy.acquire('list_windows',execution('wdc_list_windows',session));
  const pending=Promise.allSettled([first,second]);
  await tick();
  assert.equal(requests,1);
  policy.setMode(session,'disabled');
  resolveApproval('allowed-once');
  for(const result of await pending) assert.equal(result.reason?.code,'operation_cancelled');
  assert.equal(policy.state(session).grants.size,0);
  assert.equal(policy.active.size,0);
  policy.close();
});

test('Auto reuses the DSH pre-execute gate and cannot grant a direct call',async()=>{
  let requests=0, preset='auto';
  const m=await mounted({}, {permissionMode:'ask',
    testPresets:{current:()=>preset,names:['auto','workspace-write']},
    testApproval:{request:async()=>{requests++;return 'rejected';}},
  });
  try {
    const tool=m.tools.find(t=>t.name==='wdc_wait');
    const prepare=m.hooks.get('tools/pre-execute');
    const session={};
    const allowed=execution(tool.name,session);
    assert.deepEqual(await prepare(allowed,async()=>({kind:'allow'})),{kind:'allow'});
    assert.equal((await tool.execute({ms:1},allowed)).ok,true);
    const denied=execution(tool.name,session);
    assert.deepEqual(await prepare(denied,async()=>({kind:'deny'})),{kind:'deny'});
    assert.equal(requests,0);
    const stale=execution(tool.name,session);
    await prepare(stale,async()=>({kind:'allow'}));
    preset='workspace-write';
    m.hooks.get('session/event')(session,{type:'permission/preset'});
    preset='auto';
    assert.match((await tool.execute({ms:1},stale)).text,/permission_revoked/);
    assert.equal(requests,0);
    assert.equal((await tool.execute({ms:1},execution(tool.name,session))).ok,false);
    assert.equal(requests,1);
  } finally {m.close();}
});

test('full access accepts the verified preset and respects user-only restrictions and stop',async()=>{
  let preset='danger-full-access', sandbox='danger-full-access', requests=0;
  const m=await mounted({}, {permissionMode:'ask',
    testPresets:{current:()=>preset,permissionState:()=>({sandbox})},
    testApproval:{effectivePolicy:()=> 'never',request:async()=>{requests++;return 'rejected';}},
  });
  const session={};
  const call=(name,args={})=>m.tools.find(t=>t.name===name).execute(args,execution(name,session));
  const command=rawInput=>m.commands.get('wdc-access').handler({agent:{session},rawInput});
  try {
    assert.equal((await call('wdc_wait',{ms:1})).ok,true);
    assert.equal((await command('read-only')).kind,'success');
    assert.match((await call('wdc_click',{x:1,y:2})).text,/permission_denied/);
    assert.equal((await command('disabled')).kind,'success');
    assert.match((await call('wdc_wait',{ms:1})).text,/permission_denied/);
    assert.equal((await command('stop')).kind,'success');
    assert.equal((await call('wdc_stop')).ok,true);
    assert.equal((await command('ask')).kind,'success');
    assert.equal((await call('wdc_resume')).ok,true);
    preset='custom';
    sandbox='read-only';
    assert.match((await call('wdc_click',{x:1,y:2})).text,/permission_denied/);
    assert.match((await call('wdc_wait',{ms:1})).text,/permission_denied/);
    assert.equal(requests,0);
  } finally {m.close();}
});

test('rememberSessionApprovals false keeps per-call approvals on supported hosts',async()=>{
  let requests=0;
  const m=await mounted({}, {permissionMode:'ask',rememberSessionApprovals:false,
    testPresets:{current:()=> 'workspace-write'},
    testApproval:{request:async()=>{requests++;return 'allowed-once';}},
  });
  const session={};
  try {
    const wait=m.tools.find(t=>t.name==='wdc_wait');
    for(let i=0;i<2;i++) assert.equal((await wait.execute({ms:1},execution(wait.name,session))).ok,true);
    assert.equal(requests,2);
  } finally {m.close();}
});

test('session mode is isolated, model arguments cannot grant permissions, and stop bypasses approval',async()=>{
  const m=await mounted({}, {permissionMode:'ask'}),one=execution(),two=execution();
  try{
    m.policy.setMode(one.agent.session,'autonomous');
    assert.equal((await m.tools.find(t=>t.name==='wdc_wait').execute({},one)).ok,true);
    const denied=await m.tools.find(t=>t.name==='wdc_click').execute({x:1,y:2,permissionMode:'autonomous'},two);
    assert.equal(denied.ok,false);
    assert.equal((await m.tools.find(t=>t.name==='wdc_stop').execute({},two)).ok,true);
    m.policy.setMode(one.agent.session,'read-only');
    assert.equal((await m.tools.find(t=>t.name==='wdc_resume').execute({},one)).ok,false);
    assert.equal((await m.tools.find(t=>t.name==='wdc_wait').execute({},one)).ok,true);
  }finally{m.close();}
});

test('cancellation during approval ignores a late grant without starting workers',async()=>{
  let answer;const before=children.length;
  const m=await mounted({}, {permissionMode:'ask',testApproval:{request:()=>new Promise(resolve=>{answer=resolve;})}});
  try{
    const abort=new AbortController(),exec={...execution(),signal:abort.signal};
    const pending=m.tools.find(t=>t.name==='wdc_click').execute({x:1,y:2},exec);await tick();
    abort.abort();assert.equal((await pending).ok,false);answer('allowed-once');await tick();
    assert.equal(children.length,before);assert.equal(m.policy.active.size,0);
  }finally{m.close();scenarios.pop();}
});

test('revocation during cold start prevents input dispatch',async()=>{
  const m=await mounted({hangMethod:'initialize'}),exec=execution();
  try{
    const pending=m.tools.find(t=>t.name==='wdc_click').execute({x:1,y:2},exec);await tick();
    const child=children.at(-1);m.policy.setMode(exec.agent.session,'disabled');
    child.reply(child.requests[0].id,{serverInfo:{name:'mock'}});
    const result=await pending;assert.equal(result.ok,false);
    assert.ok(!child.requests.some(r=>r.method==='tools/call'));
  }finally{m.close();}
});

test('cancel dispatched input latches stop, preserves uncertainty and never replays',async()=>{
  const m=await mounted({hangName:'type_text'}),abort=new AbortController(),exec={...execution('wdc_type_text'),signal:abort.signal};
  try{
    const pending=m.tools.find(t=>t.name==='wdc_type_text').execute({text:'test'},exec);await tick();
    const child=children.at(-1);abort.abort();const result=await pending;
    assert.equal(result.ok,false);assert.match(result.text,/outcome_unknown/);
    assert.equal(child.requests.filter(r=>r.params?.name==='type_text').length,1);
    assert.equal((await m.tools.find(t=>t.name==='wdc_resume').execute({},execution())).ok,false);
    child.reply(child.requests.find(r=>r.params?.name==='type_text').id,{content:[{type:'text',text:'{"ok":true}'}]});
    assert.equal((await m.tools.find(t=>t.name==='wdc_click').execute({x:1,y:2},execution())).ok,false);
  }finally{m.close();}
});

test('approval timeout and unavailable context fail closed; unload aborts outstanding approval',async()=>{
  const {SessionPermissions}=await import('../dsh-wdc-computer-use/lib/permissions.js');
  const policy=new SessionPermissions({timeoutMs:10,getApproval:()=>({request:()=>new Promise(()=>{})})});
  await assert.rejects(policy.acquire('click',{}),e=>e.code==='permission_context_missing');
  await assert.rejects(policy.acquire('click',execution()),e=>e.code==='operation_cancelled');
  const pending=policy.acquire('click',execution());policy.close();
  await assert.rejects(pending,e=>e.code==='operation_cancelled');assert.equal(policy.active.size,0);
});

test('screenshot provenance survives host presentation without removing the model file reference',async()=>{
  const m=await mounted({callResult:{content:[{type:'text',text:'{"ok":true,"frame_id":"private-test"}'},
    {type:'image',mimeType:'image/png',data:Buffer.from('mock image bytes').toString('base64')}]}});
  try{
    const tool=m.tools.find(t=>t.name==='wdc_screenshot'),value=await tool.execute({});
    assert.equal(value.ok,true);assert.equal(value.wdcScreenshots.length,1);
    assert.ok(value.wdcScreenshots[0].startsWith(runtime));assert.ok(value.text.includes(value.wdcScreenshots[0]));
    const meta=tool.output.presentationMeta({},value);
    assert.deepEqual(meta,{wdc:{version:1,tool:'screenshot',screenshots:value.wdcScreenshots}});
    assert.deepEqual(tool.output.render({},value),[{type:'text',text:value.text}]);
    assert.equal(fs.existsSync(value.wdcScreenshots[0]),true);
  }finally{m.close();}
});
