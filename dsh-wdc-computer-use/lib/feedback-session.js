/** Sanitized operation-to-view adapter; never receives arguments or displays raw errors. */
import {VisualFeedback} from './visual-feedback.js';

const TOOL_STATE=Object.freeze({screen_info:'observing',screenshot:'observing',list_windows:'observing',clipboard_get:'observing',
  click:'clicking',type_text:'typing',press_key:'typing',scroll:'scrolling',wait:'waiting',
  move_mouse:'operating',drag:'operating',activate_window:'operating',window_action:'operating',launch_app:'operating',
  clipboard_set:'operating',run_command:'operating',stop:'waiting',resume:'waiting',
  window_screenshot:'observing',inspect_controls:'observing',recognize_text:'observing',locate_target:'observing',
  control_action:'operating',batch_windows:'operating'});
const PRIORITY=Object.freeze({observing:1,waiting:2,clicking:3,typing:3,scrolling:3,operating:3});
const MUTATING=new Set(['click','type_text','press_key','scroll','move_mouse','drag','activate_window','window_action','launch_app','clipboard_set','run_command','control_action','batch_windows']);
const finite=value=>typeof value==='number'&&Number.isFinite(value);
const validRect=value=>Boolean(value&&['x','y','width','height'].every(key=>finite(value[key]))&&value.width>0&&value.height>0);

/** Whitelist output only. Callers must not pass input text, raw content or errors to a view. */
export function summarizeFeedbackResult(tool,result) {
  let payload;
  let failed=!result || result.isError===true || !Array.isArray(result.content) || result.content.length===0;
  for(const item of result?.content || []) {
    if(item?.type!=='text'||typeof item.text!=='string')continue;
    try {
      const candidate=JSON.parse(item.text);
      if(candidate?.ok===false)failed=true;
      if(candidate&&typeof candidate==='object'&&typeof candidate.ok==='boolean'&&(!candidate.action||candidate.action===tool))payload=candidate;
    } catch { /* No text is forwarded to observers. */ }
  }
  const summary={ok:!failed};
  if(failed&&payload?.error_code==='emergency_stop')summary.cancelled=true;
  if(!failed&&tool==='click'&&payload?.ok===true&&Array.isArray(payload.screen_point)&&payload.screen_point.length===2&&payload.screen_point.every(finite)){
    summary.point=[...payload.screen_point];
  }
  if(!failed&&tool==='screen_info'&&payload?.ok===true){
    const screen=payload.screen;
    const viewport=screen&&{x:screen.left,y:screen.top,width:screen.width,height:screen.height};
    if(validRect(viewport))summary.viewport=viewport;
  }
  return summary;
}

export class FeedbackSession {
  #model;
  #geometryKnown=false;
  #geometryExplicit=false;
  #next=0;
  #operations=new Map();
  #listeners=new Set();
  #control={stopped:false,busy:false};
  #fault=false;
  #remoteStopped=false;
  #transportFault=false;
  #disposed=false;
  #warn;
  #activeOnly=false;
  #pointer=null;
  #clock;
  constructor({enabled=false,settings={},viewport,workArea,clock,activeOnly=false,onObserverError=()=>{}}={}) {
    this.#activeOnly=activeOnly;
    this.#clock=clock||(()=>performance.now());
    this.#warn=onObserverError;
    this.#geometryKnown=validRect(viewport);
    this.#geometryExplicit=this.#geometryKnown;
    this.#model=new VisualFeedback({settings:{...settings,enabled:enabled===true},
      ...(this.#geometryKnown?{viewport,workArea:validRect(workArea)?workArea:viewport}:{}),...(clock?{clock}:{})});
  }
  get settings(){return this.#model.settings;}
  get state(){return this.#model.state;}
  configure(settings){if(this.#disposed)return;this.#model.configure(settings);this.#publish();}
  setGeometry(viewport,workArea=viewport){
    if(this.#disposed)return;
    this.#pointer=null;this.#model.setViewport(viewport,workArea);this.#geometryKnown=true;this.#geometryExplicit=true;this.#publish();
  }
  snapshot(at){
    const scene=this.#model.snapshot(at);
    const mark=this.#pointer;
    scene.pointer=null;
    if(mark&&scene.visible&&this.#operations.has(mark.token)&&!this.#control.stopped&&this.#geometryKnown&&(at??this.#clock())-mark.at<750){
      const v=scene.viewport;
      if(mark.x>=v.x&&mark.y>=v.y&&mark.x<v.x+v.width&&mark.y<v.y+v.height){
        scene.pointer={x:mark.x,y:mark.y,scale:this.settings.uiScale};scene.animated=true;
        const b=scene.badge;
        if(b&&mark.x>=b.x-32&&mark.y>=b.y-32&&mark.x<=b.x+b.width+32&&mark.y<=b.y+b.height+32)scene.badge=null;
      }
    }
    if(!this.#geometryKnown||this.#disposed||(this.#activeOnly&&!this.#operations.size&&!this.#control.busy)){scene.visible=false;scene.badge=null;scene.rings=[];scene.pointer=null;scene.animated=false;}
    return {...scene,geometryKnown:this.#geometryKnown,activeOperations:this.#operations.size};
  }
  subscribe(listener){
    if(typeof listener!=='function')throw new TypeError('Feedback observer must be a function');
    if(this.#disposed)return ()=>{};
    this.#listeners.add(listener);this.#deliver(listener);
    return ()=>this.#listeners.delete(listener);
  }
  begin(tool){
    if(this.#disposed)return null;
    // A new call must not resurrect a mark from a completed call.
    if(this.#activeOnly&&!this.#operations.size){
      const enabled=this.#model.settings.enabled;
      this.#model.configure({enabled:false});this.#model.configure({enabled});
    }
    const token=++this.#next;
    const known=Object.hasOwn(TOOL_STATE,tool);
    this.#operations.set(token,{tool:known?tool:'unknown',state:known?TOOL_STATE[tool]:'operating'});
    if(MUTATING.has(tool)&&!this.#control.stopped&&!this.#remoteStopped)this.#fault=false;
    this.#refresh();return token;
  }
  pointer(token,point){
    const operation=this.#operations.get(token);
    if(!operation||!['click','move_mouse','drag','scroll'].includes(operation.tool)||this.#disposed||this.#control.stopped||!point.every(finite))return;
    this.#pointer={token,x:point[0],y:point[1],at:this.#clock()};
    if(operation.tool==='click'&&this.#geometryKnown)this.#model.recordClick(...point);
    this.#publish();
  }
  complete(token,summary){
    if(this.#disposed||!this.#operations.has(token))return;
    const {tool}=this.#operations.get(token);this.#operations.delete(token);
    if(!summary.ok&&!summary.cancelled)this.#fault=true;
    if(summary.cancelled)this.#remoteStopped=true;
    if(summary.ok&&tool==='resume'){this.#fault=false;this.#remoteStopped=false;}
    if(summary.ok&&summary.viewport&&!this.#geometryExplicit){
      try{this.#model.setViewport(summary.viewport);this.#geometryKnown=true;}catch{/* Invalid geometry stays hidden. */}
    }
    if(!this.#activeOnly&&summary.ok&&summary.point&&this.#geometryKnown)this.#model.recordClick(...summary.point);
    this.#refresh();
  }
  fail(token,code){
    if(this.#disposed||!this.#operations.has(token))return;
    this.#operations.delete(token);
    // Busy/cancelled attempts must not overwrite the operation that is still active.
    if(code!=='action_busy'&&code!=='emergency_stop')this.#fault=true;
    this.#refresh();
  }
  setControl({stopped,busy}){
    if(this.#disposed)return;
    this.#control={stopped:stopped===true,busy:busy===true};this.#refresh();
  }
  setTransportAvailable(available){if(this.#disposed)return;this.#transportFault=!available;this.#refresh();}
  dispose(){
    if(this.#disposed)return;
    this.#operations.clear();this.#model.configure({enabled:false});this.#model.setState('idle');
    this.#disposed=true;this.#publish();this.#listeners.clear();
  }
  #refresh(){
    let state='idle';
    if(this.#control.stopped||this.#remoteStopped)state=this.#fault?'error':this.#control.busy?'stopping':'stopped';
    else if(this.#transportFault)state='error';
    else if(this.#operations.size){
      let top=-1;
      for(const operation of this.#operations.values())if(PRIORITY[operation.state]>=top){state=operation.state;top=PRIORITY[state];}
    }else if(this.#fault)state='error';
    this.#model.setState(state);this.#publish();
  }
  #publish(){for(const listener of [...this.#listeners])this.#deliver(listener);}
  #deliver(listener){
    const failed=()=>{this.#listeners.delete(listener);try{this.#warn('feedback_observer_disabled');}catch{/* Display errors never affect tools. */}};
    try{
      const value=listener(this.snapshot());
      if(value&&typeof value.then==='function')Promise.resolve(value).catch(failed);
    }catch{failed();}
  }
}
