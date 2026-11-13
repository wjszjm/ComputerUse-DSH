import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {INTENT,toggleIntent,privacyIndex,visibleImages,canonicalPath,callDisplay} from '../dsh-wdc-computer-use/lib/client-policy.js';
const own={meta:{wdc:{version:1,tool:'screenshot',screenshots:['D:\\shots\\shot-1.png']}},message:{callId:'shot-call',content:[]}};
const read={meta:{path:'d:/shots/shot-1.png'},message:{callId:'read-call',content:[{type:'image',attachment:{attachmentId:'private-image'}}]}};
const ordinary={meta:{path:'D:/other/shot-1.png'},message:{content:[{type:'image',attachment:{attachmentId:'ordinary-image'}}]}};
const native={meta:{wdc:{version:2,tool:'screenshot',imageIds:['native-screenshot']}}};
test('intent toggles by guarded range, preserves the task, and never submits or grants',()=>{
  let draft='点击保存',rev=3,focus=0;
  const shell={get snapshot(){return {draft,draftRev:rev};},insertText(text,span){assert.equal(span.draftRev,rev);draft=draft.slice(0,span.start)+text+draft.slice(span.end);rev++;return true;},focus(){focus++;}};
  assert.equal(toggleIntent(shell),true);assert.equal(draft,INTENT+'点击保存');
  assert.equal(toggleIntent(shell),true);assert.equal(draft,'点击保存');assert.equal(focus,2);
  const other={snapshot:{draft:'另一会话',draftRev:1},insertText:()=>false,focus:()=>assert.fail()};assert.equal(toggleIntent(other),false);
});
test('replay and backfill use exact persisted provenance, never the shot filename',()=>{
  for(const records of [[own,read,ordinary],[ordinary,read,own]]){
    const index=privacyIndex(records);assert.deepEqual([...index.ids],['private-image']);
    assert.deepEqual(visibleImages([{attachment:{attachmentId:'private-image'}},{attachment:{attachmentId:'ordinary-image'}}],index),[{attachment:{attachmentId:'ordinary-image'}}]);
  }
  assert.equal(privacyIndex([read,ordinary]).ids.size,0);
  assert.equal(canonicalPath('file:///D:/shots/../shots/shot-1.png#L2'),'d:/shots/shot-1.png');
});
test('only active own calls show a generic label; terminal, failure and approval-preparing details disappear',()=>{
  const index=privacyIndex([own,read]),names=new Set(['wdc_screenshot']);
  assert.equal(callDisplay('wdc_screenshot',{phase:'start',block:{argsRaw:'secret'}},index,names),'电脑操作调用中');
  for(const phase of ['result','preparing'])assert.equal(callDisplay('wdc_screenshot',{phase,block:{isError:true}},index,names),null);
  assert.equal(callDisplay('read_image',{phase:'result',block:{meta:{path:'D:/shots/shot-1.png'}}},index,names),null);
  assert.equal(callDisplay('read_image',{phase:'result',block:{meta:{path:'D:/other/shot-1.png'}}},index,names),undefined);
});
test('browser bundle mounts through the actual module-loader contract and disposes its policy',()=>{
  let entry;const sandbox={window:{__ModuleLoader__:{load:e=>entry=e}},URL,Symbol};vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(new URL('../dsh-wdc-computer-use/lib/client.js',import.meta.url),'utf8'),sandbox);
  const client=entry.factory(),slots=[],effects=[],commands=[],events=[],views=[];
  let activated=false;
  const store={getSnapshot:()=>activated?[own,read,ordinary,native]:undefined};
  const binding={target:()=>store,activate:target=>{assert.equal(target,'wdc-private-images');activated=true;}};
  assert.ok(client.inject.includes('commandUi'));assert.ok(!client.inject.includes('commands'));
  client.apply({uiConversation:{events:{register:e=>events.push(e)},views:{register:e=>views.push(e)},binding:()=>binding},
    sessions:{list:{getSnapshot:()=>({byId:{one:{}}})},binding:()=>({ctx:{}})},conversation:{input:{for:()=>{}}},
    slots:{inject:(name,fn)=>fn(),register:(options,component)=>slots.push({options,component})},
    commandUi:{register:c=>commands.push(c)},effect:fn=>effects.push(fn())});
  const policy=vm.runInContext("globalThis[Symbol.for('dsh.wdc.ui-policy.v1')]",sandbox);
  assert.equal(policy.hiddenMedia('dsh-app://app/api/file?path=D%3A%2Fshots%2Fshot-1.png&load=1'),true);
  assert.equal(policy.hiddenMedia('https://example.com/ordinary.png'),false);
  assert.equal(commands[0].label(),'电脑操作');
  const toolViews=slots.filter(entry=>entry.options.name==='tool.call.toolview');
  assert.equal(toolViews.length,24);
  const screenshotView=toolViews.find(entry=>entry.options.key==='wdc_screenshot').component;
  assert.equal(screenshotView({phase:'start'}),'电脑操作调用中');
  assert.equal(screenshotView({phase:'result'}),null);
  assert.ok(toolViews.every(entry=>entry.options.key.startsWith('wdc_')));
  const galleries=slots.filter(entry=>entry.options.name!=='tool.call.toolview');
  assert.equal(galleries.length,4);
  for(const {component} of galleries){
    let rendered=false;
    assert.equal(component({images:[{attachment:{attachmentId:'private-image'}}],useWdcRecords:select=>select(store.getSnapshot()),renderFactorySlot:()=>{rendered=true;}}),null);
    assert.equal(rendered,false);
    const images=['native-screenshot','ordinary-image'].map(attachmentId=>({attachment:{attachmentId}}));
    assert.deepEqual(component({images,useWdcRecords:select=>select(store.getSnapshot()),
      renderFactorySlot:(_slot,props)=>props.images}),[images[1]]);
  }
  const event={type:'tool/result',seq:3,data:own};const definition=events[0];assert.equal(definition.match(event).role,'start');assert.equal(definition.start(null,{event}).meta,own.meta);
  assert.equal(definition.match({type:'tool/result',seq:4,data:native}).role,'start');
  const view=views[0].create();assert.equal(view.replace({nodes:[{id:'1',data:own}]}).length,1);assert.equal(view.apply({upserts:[{id:'2',data:read}]}).length,2);
  effects.forEach(fn=>fn());assert.equal(vm.runInContext("globalThis[Symbol.for('dsh.wdc.ui-policy.v1')]",sandbox),undefined);
});
