/** Opt-in, own-process display transport. No shell, browser, or input permissions. */
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {FEEDBACK_STATES} from './visual-feedback.js';

const finite=value=>typeof value==='number'&&Number.isFinite(value);
const rect=value=>value&&['x','y','width','height'].every(key=>finite(value[key]))&&value.width>0&&value.height>0;
const fields=(value,keys)=>Object.fromEntries(keys.map(key=>[key,value[key]]));

export function nativeScene(scene){
  if(!Object.hasOwn(FEEDBACK_STATES,scene.state)||!rect(scene.viewport))throw Error('invalid_native_scene');
  return {visible:scene.visible===true,geometryKnown:scene.geometryKnown===true,state:scene.state,
    viewport:fields(scene.viewport,['x','y','width','height']),
    pointer:scene.pointer?fields(scene.pointer,['x','y','scale']):null,
    badge:scene.badge?fields(scene.badge,['x','y','width','height','compact','scale','opacity']):null,
    rings:scene.rings.slice(0,2).map(r=>fields(r,['x','y','radius','strokeWidth','opacity']))};
}

export class NativeFeedbackHost {
  constructor(options,feedback,log=()=>{},onStatus=()=>{},timers={setTimeout,clearTimeout,setInterval,clearInterval}){
    this.options=options;this.feedback=feedback;this.log=log;this.onStatus=onStatus;
    this.timers=timers;
    this.child=null;this.state='disabled';this.reason='not_enabled';this.ready=false;this.closed=false;
    this.sequence=0;this.pending=null;this.dirty=false;this.unsubscribe=null;
    this.frame=null;this.heartbeat=null;this.deadline=null;this.startup=null;this.killTimer=null;
    this.exitWait=null;
    this.readyWait=Promise.resolve(false);this.resolveReady=null;
  }
  status(){return {state:this.state,reason:this.reason,captureExcluded:this.ready};}
  report(){try{this.onStatus(this.status());}catch{/* Diagnostics cannot affect tools. */}}
  start(){
    if(this.closed||this.child||this.state==='failed')return false;
    if(this.options.feedbackNativeEnabled!==true||this.options.feedbackEnabled!==true||!this.feedback?.settings.enabled)return false;
    this.state='starting';this.reason='initializing';this.report();
    this.readyWait=new Promise(resolve=>{this.resolveReady=resolve;});
    try{
      const child=spawn(this.options.python,['-B','-m','wdc.overlay_host'],{cwd:this.options.cwd,windowsHide:true,
        stdio:['pipe','pipe','pipe'],env:{...process.env,PYTHONUTF8:'1',PYTHONIOENCODING:'utf-8'}});
      this.child=child;
      child.once('error',()=>this.fail('process_error'));
      const exited=()=>{
        this.timers.clearTimeout(this.killTimer);this.killTimer=null;
        if(this.child===child){this.child=null;if(!this.closed)this.fail('process_exited');}
      };
      child.once('exit',exited);child.once('close',exited);
      child.stdin.on('error',()=>this.fail('pipe_error'));
      const lines=createInterface({input:child.stdout});
      lines.on('line',line=>{
        if(this.closed||this.child!==child)return;
        try{if(Buffer.byteLength(line)>16384)throw Error();this.receive(JSON.parse(line));}
        catch{this.fail('invalid_response');}
      });
      lines.on('error',()=>this.fail('pipe_error'));
      // Consume diagnostics without exposing arbitrary worker text to the UI/log.
      child.stderr.on('data',()=>{});
      this.startup=this.timers.setTimeout(()=>this.fail('startup_timeout'),2000);
      this.unsubscribe=this.feedback.subscribe(()=>{this.dirty=true;this.flush();});
      return true;
    }catch{this.fail('startup_failed');return false;}
  }
  receive(message){
    if(this.closed)return;
    if(!message||typeof message!=='object')throw Error('invalid_response');
    if(message.type==='ready'||message.type==='geometry'){
      if(message.captureExcluded!==true||!rect(message.viewport)||!rect(message.workArea)||!finite(message.uiScale)){
        this.fail('capture_or_geometry_unavailable');return;
      }
      this.ready=false;
      this.feedback.setGeometry(message.viewport,message.workArea);
      if(!Object.hasOwn(this.options.feedbackSettings||{},'uiScale'))this.feedback.configure({uiScale:Math.min(3,Math.max(.75,message.uiScale))});
      this.ready=true;this.state='ready';this.reason='ready';this.timers.clearTimeout(this.startup);this.startup=null;
      this.resolveReady?.(true);this.resolveReady=null;
      this.report();this.dirty=true;this.flush();
      if(this.heartbeat===null)this.heartbeat=this.timers.setInterval(()=>{
        if(this.pending!==null)return;
        if(this.dirty)this.flush();else this.send({type:'heartbeat'});
      },500);
      this.heartbeat?.unref?.();
    }else if(message.type==='ack'){
      if(message.sequence!==this.pending)return;
      this.pending=null;this.timers.clearTimeout(this.deadline);this.deadline=null;
      if(this.dirty)this.flush();
    }else if(message.type==='refresh'){this.dirty=true;this.flush();}
    else if(message.type==='disabled')this.fail('worker_disabled');
    else throw Error('invalid_response');
  }
  flush(){
    if(!this.ready||this.closed||this.pending!==null||!this.child)return;
    try{
      const scene=this.feedback.snapshot();this.dirty=false;
      this.send({type:'scene',scene:nativeScene(scene)});
      if(scene.animated&&this.frame===null)this.frame=this.timers.setTimeout(()=>{this.frame=null;this.dirty=true;this.flush();},50);
    }catch{this.fail('scene_failed');}
  }
  send(packet){
    if(!this.ready||this.closed||this.pending!==null||!this.child)return;
    const sequence=++this.sequence;
    this.pending=sequence;
    this.deadline=this.timers.setTimeout(()=>this.fail('ack_timeout'),1000);
    try{
      this.child.stdin.write(JSON.stringify({...packet,sequence})+'\n',error=>{if(error&&!this.closed)this.fail('pipe_error');});
    }catch{this.fail('pipe_error');}
  }
  fail(reason){
    if(this.closed)return;
    this.state='failed';this.reason=reason;
    try{this.log('原生提示层已关闭；电脑操控工具不受影响');}catch{}
    this.shutdown();this.report();
  }
  close(){
    if(this.closed)return this.exitWait || Promise.resolve(!this.child);
    this.state='closed';this.reason='disposed';this.shutdown();this.report();
    return this.exitWait;
  }
  shutdown(){
    this.resolveReady?.(false);this.resolveReady=null;
    this.closed=true;this.ready=false;this.pending=null;
    for(const timer of [this.frame,this.deadline,this.startup])this.timers.clearTimeout(timer);
    this.timers.clearInterval(this.heartbeat);this.frame=this.deadline=this.startup=this.heartbeat=null;
    this.unsubscribe?.();this.unsubscribe=null;
    const child=this.child;this.child=null;
    this.exitWait=Promise.resolve(true);
    if(child){
      // Never overlap an old display worker with a new one. A missing exit disables reopening.
      this.exitWait=new Promise(resolve=>{
        let finished=false;
        const deadline=this.timers.setTimeout(()=>finish(false),2000);
        deadline?.unref?.();
        const finish=value=>{
          if(finished)return;finished=true;this.timers.clearTimeout(deadline);
          child.removeListener('exit',onExit);child.removeListener('close',onExit);resolve(value);
        };
        const onExit=()=>finish(true);
        child.once('exit',onExit);child.once('close',onExit);
        if(child.exitCode!==null&&child.exitCode!==undefined)finish(true);
      });
      // EOF is the normal exit signal; only our own worker is eligible for fallback termination.
      this.killTimer=this.timers.setTimeout(()=>{if(child.exitCode===null||child.exitCode===undefined)try{child.kill();}catch{}},250);
      this.killTimer?.unref?.();
      try{child.stdin.end();}catch{try{child.kill();}catch{}}
    }
  }
}
