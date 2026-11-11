/** Executes the real preview script with memory DOM/timers, without a browser. */
import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const component=readFileSync(new URL('../dsh-wdc-computer-use/lib/visual-feedback.js',import.meta.url),'utf8').replace(/^export /gm,'');
const template=readFileSync(new URL('../dsh-wdc-computer-use/preview/visual-feedback.html',import.meta.url),'utf8');
const script=template.match(/<script type="module">([\s\S]*?)<\/script>/)[1].replace('/*__VISUAL_FEEDBACK_SOURCE__*/',()=>component);
function setup(){
  const elements=new Map(),events=new Map(),timers=new Map(),frames=new Map(),saved=[];
  let now=0,id=0;
  class Element {
    listeners=new Map();value='';checked=true;innerHTML='';textContent='';
    clientWidth=800;clientHeight=360;clientLeft=0;clientTop=0;
    addEventListener(name,fn){this.listeners.set(name,fn);}
    getBoundingClientRect(){return {left:0,top:0,width:this.clientWidth,height:this.clientHeight};}
    fire(name,data={}){this.listeners.get(name)?.({currentTarget:this,...data});}
  }
  for(const key of ['state','position','opacity','enabled'])elements.set(`[data-control="${key}"]`,new Element());
  for(const key of ['[data-screen]','[data-overlay]','[data-detail]','[data-value="opacity"]','[data-action="target"]','[data-action="replay"]'])elements.set(key,new Element());
  const root={querySelector(selector){if(!elements.has(selector))throw Error(`Missing preview element: ${selector}`);return elements.get(selector);}};
  const context=vm.createContext({performance:{now:()=>now},document:{getElementById(name){assert.equal(name,'wdc-feedback-preview');return root;}},
    matchMedia:()=>({matches:false}),ResizeObserver:class {constructor(fn){this.fn=fn;}observe(){this.fn();}disconnect(){}},
    requestAnimationFrame:fn=>{frames.set(++id,fn);return id;},cancelAnimationFrame:key=>frames.delete(key),
    setTimeout:(fn,delay)=>{timers.set(++id,{fn,at:now+delay});return id;},clearTimeout:key=>timers.delete(key),
    window:{openai:{setWidgetState:value=>{saved.push(value);return Promise.resolve();}},addEventListener:(name,fn)=>events.set(name,fn)}});
  vm.runInContext(script,context,{timeout:1000});
  return {elements,events,saved,timers,frames,get:selector=>elements.get(selector),
    advance(time){now=time;for(const [key,timer] of [...timers])if(timer.at<=now){timers.delete(key);timer.fn();}
      for(const [key,fn] of [...frames]){frames.delete(key);fn(now);}}};
}

test('preview state/position/opacity controls update the actual SVG and persist only on interaction',()=>{
  const ui=setup();assert.equal(ui.saved.length,0);
  const state=ui.get('[data-control="state"]');state.value='typing';state.fire('change');
  assert.match(ui.get('[data-overlay]').innerHTML,/data-feedback-badge="typing"/);
  const position=ui.get('[data-control="position"]');position.value='top-left';position.fire('change');
  assert.match(ui.get('[data-overlay]').innerHTML,/<rect x="16" y="16"/);
  const opacity=ui.get('[data-control="opacity"]');opacity.value='50';opacity.fire('input');opacity.fire('change');
  assert.match(ui.get('[data-overlay]').innerHTML,/opacity="0.5"/);
  assert.equal(ui.saved.at(-1).modelContent.opacity,0.5);
});

test('preview click produces a transient ring; hiding clears it and page exit clears timers',()=>{
  const ui=setup();
  ui.get('[data-action="target"]').fire('click');
  assert.equal(ui.get('[data-action="target"]').textContent,'测试按钮 · 1');
  ui.get('[data-screen]').fire('click',{clientX:250,clientY:150});
  assert.match(ui.get('[data-overlay]').innerHTML,/data-click-x="250" data-click-y="150"/);
  const enabled=ui.get('[data-control="enabled"]');enabled.checked=false;enabled.fire('change');
  assert.doesNotMatch(ui.get('[data-overlay]').innerHTML,/data-click-x|data-feedback-badge/);
  enabled.checked=true;enabled.fire('change');
  assert.doesNotMatch(ui.get('[data-overlay]').innerHTML,/data-click-x/);
  ui.events.get('pagehide')();
  assert.equal(ui.frames.size,0);assert.equal(ui.timers.size,0);assert.equal(ui.get('[data-overlay]').innerHTML,'');
});

test('bounded replay steps through observation, click, typing, waiting and returns to idle',()=>{
  const ui=setup(),state=ui.get('[data-control="state"]');
  ui.get('[data-action="replay"]').fire('click');assert.equal(state.value,'observing');
  for(const [at,value] of [[650,'clicking'],[1350,'typing'],[2150,'waiting'],[3000,'idle']]){
    ui.advance(at);assert.equal(state.value,value);
  }
  assert.equal(ui.frames.size,0);assert.equal(ui.timers.size,0);
});
