import assert from 'node:assert/strict';
import {test} from 'node:test';
import {VisualFeedback,FEEDBACK_STATES,feedbackSvg,mountFeedback} from '../dsh-wdc-computer-use/lib/visual-feedback.js';

const create=settings=>new VisualFeedback({settings:{enabled:true,...settings},clock:()=>0,
  viewport:{x:0,y:0,width:800,height:600},workArea:{x:0,y:0,width:800,height:560}});

test('disabled by default; all ten states use fixed labels and no arbitrary text',()=>{
  const off=new VisualFeedback({clock:()=>0});
  assert.equal(off.snapshot().visible,false);
  assert.equal(off.recordClick(10,10),false);
  const model=create();
  assert.equal(Object.keys(FEEDBACK_STATES).length,10);
  for(const [state,info] of Object.entries(FEEDBACK_STATES)){
    model.setState(state);assert.equal(model.snapshot().label,info.label);
    assert.match(feedbackSvg(model.snapshot()),/focusable="false"/);
  }
  assert.throws(()=>model.setState('private typing text'),RangeError);
  assert.throws(()=>model.configure({text:'secret'}),RangeError);
  assert.doesNotMatch(JSON.stringify(model.snapshot()),/secret|private typing text/);
});

test('idle is a small dot; active badge avoids taskbar in every corner',()=>{
  const model=create();
  assert.equal(model.snapshot().badge.width,26);
  model.setState('typing');
  for(const position of ['top-left','top-right','bottom-left','bottom-right']){
    model.configure({position});const b=model.snapshot().badge;
    assert.equal(b.width,170);assert.equal(b.height,34);
    assert.ok(b.x>=16 && b.x+b.width<=784);
    assert.ok(b.y>=16 && b.y+b.height<=544);
    assert.equal(b.x,position.endsWith('right')?614:16);
    assert.equal(b.y,position.startsWith('bottom')?510:16);
  }
});

test('negative screen origin and scaled geometry preserve click coordinates',()=>{
  const model=new VisualFeedback({viewport:{x:-1920,y:0,width:1920,height:1080},
    workArea:{x:-1920,y:0,width:1920,height:1020},settings:{enabled:true,uiScale:1.5},clock:()=>0});
  model.setState('clicking');model.recordClick(-1000,400);
  const scene=model.snapshot(100);
  assert.equal(scene.badge.width,255);
  assert.equal(scene.rings[0].x,-1000);assert.equal(scene.rings[0].y,400);
  assert.equal(scene.rings[0].strokeWidth,3);
  assert.equal(model.recordClick(20,20),false);
  assert.match(feedbackSvg(scene),/viewBox="-1920 0 1920 1080"/);
});

test('ring fades and expires, with no filled center',()=>{
  const model=create();model.recordClick(250,200,0);
  const start=model.snapshot(0).rings[0],middle=model.snapshot(275).rings[0];
  assert.ok(middle.radius>start.radius && middle.opacity<start.opacity);
  assert.equal(middle.opacity,0.44);
  assert.match(feedbackSvg(model.snapshot(275)),/fill="none"/);
  assert.equal(model.snapshot(550).rings.length,0);
  assert.equal(model.snapshot(550).animated,false);
});

test('bursts are bounded and hidden clicks are never replayed',()=>{
  const model=create();
  for(let i=0;i<100;i++)model.recordClick(i+100,200,i);
  assert.equal(model.snapshot(100).rings.length,1);
  for(let i=0;i<4;i++)model.recordClick(100+i*30,300,100+i*100);
  assert.equal(model.snapshot(400).rings.length,2);
  model.configure({enabled:false});
  assert.equal(model.snapshot(401).badge,null);
  assert.equal(model.recordClick(400,300,402),false);
  model.configure({enabled:true});assert.equal(model.snapshot(403).rings.length,0);
});

test('reduced motion holds radius still and expires; viewport changes drop stale marks',()=>{
  const model=create({reducedMotion:true});model.recordClick(700,300,0);
  assert.equal(model.snapshot(0).rings[0].radius,model.snapshot(300).rings[0].radius);
  model.setViewport({x:0,y:0,width:400,height:300});
  assert.equal(model.snapshot(301).rings.length,0);
  model.setState('error');model.setViewport({x:0,y:0,width:70,height:40});
  const b=model.snapshot().badge;
  assert.ok(b.compact && b.x>=0 && b.y>=0 && b.x+b.width<=70 && b.y+b.height<=40);
});

test('invalid settings or non-finite points fail before corrupting state',()=>{
  const model=create();
  for(const settings of [{opacity:0},{position:'center'},{uiScale:NaN},{ringLifetimeMs:Infinity},{enabled:'yes'}])assert.throws(()=>model.configure(settings));
  assert.equal(model.settings.opacity,0.88);
  assert.throws(()=>model.recordClick(NaN,0));
  assert.throws(()=>model.snapshot(Infinity));
  assert.throws(()=>model.setViewport({x:0,y:0,width:10,height:10},{x:0,y:0,width:20,height:20}));
  assert.equal(model.snapshot().viewport.width,800);
});

test('renderer schedules only transient animation and cleans up its own surface',()=>{
  const model=create();const container={innerHTML:''};let next=0;
  const pending=new Map();const requestFrame=callback=>{pending.set(++next,callback);return next;};
  const cancelFrame=id=>pending.delete(id);
  const view=mountFeedback(container,model,{requestFrame,cancelFrame});
  assert.equal(pending.size,0);
  model.recordClick(100,100);view.render(0);assert.equal(pending.size,1);
  const [id,callback]=pending.entries().next().value;pending.delete(id);callback(550);
  assert.equal(pending.size,0);
  model.recordClick(100,100,600);view.render(600);assert.equal(pending.size,1);
  model.configure({enabled:false});view.render(601);assert.equal(pending.size,0);
  view.destroy();assert.equal(container.innerHTML,'');
  assert.equal(view.render(602),undefined);
});

test('renderer escapes an invalid external label and remains pointer-transparent',()=>{
  const model=create();model.setState('error');const scene=model.snapshot();
  scene.label='"<script>secret</script>';
  const svg=feedbackSvg(scene);
  assert.doesNotMatch(svg,/<script>/);
  assert.match(svg,/&lt;script&gt;/);
  assert.match(svg,/pointer-events:none/);
});

test('badge temporarily avoids a click at its corner and returns after the mark expires',()=>{
  const model=create();model.setState('clicking');const initial=model.snapshot().badge;
  const x=initial.x+initial.width/2,y=initial.y+initial.height/2;
  model.recordClick(x,y,0);
  const moved=model.snapshot(100).badge;
  assert.equal(moved.relocated,true);
  assert.ok(x<moved.x || x>moved.x+moved.width || y<moved.y || y>moved.y+moved.height);
  const restored=model.snapshot(550).badge;
  assert.equal(restored.x,initial.x);assert.equal(restored.y,initial.y);
});
