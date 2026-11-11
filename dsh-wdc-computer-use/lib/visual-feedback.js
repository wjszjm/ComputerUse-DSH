/** Display-only feedback. No desktop, filesystem, process, or network access. */
export const FEEDBACK_STATES = Object.freeze({
  idle: Object.freeze({label:'空闲', tone:'quiet'}),
  observing: Object.freeze({label:'正在观察', tone:'active'}),
  clicking: Object.freeze({label:'点击中', tone:'active'}),
  typing: Object.freeze({label:'输入中', tone:'active'}),
  scrolling: Object.freeze({label:'滚动中', tone:'active'}),
  operating: Object.freeze({label:'操作中', tone:'active'}),
  waiting: Object.freeze({label:'等待界面响应', tone:'waiting'}),
  stopping: Object.freeze({label:'正在停止', tone:'waiting'}),
  stopped: Object.freeze({label:'已停止', tone:'waiting'}),
  error: Object.freeze({label:'需要处理', tone:'error'}),
});

const DEFAULTS = Object.freeze({enabled:false, position:'bottom-right', opacity:0.88,
  compactIdle:true, reducedMotion:false, uiScale:1, margin:16, ringLifetimeMs:550});
const POSITIONS = new Set(['top-left','top-right','bottom-left','bottom-right']);
const number = (value,name) => {
  if (typeof value !== 'number' || !Number.isFinite(value)) throw new TypeError(`${name} must be finite`);
  return value;
};
const within = (value,min,max,name) => {
  number(value,name);
  if(value<min || value>max) throw new RangeError(`${name} out of range`);
};
function rect(value) {
  const copy={};
  for(const key of ['x','y','width','height']) copy[key]=number(value[key],key);
  if(copy.width<=0 || copy.height<=0) throw new RangeError('Empty viewport');
  return Object.freeze(copy);
}
function contains(area,x,y) {
  return x>=area.x && y>=area.y && x<area.x+area.width && y<area.y+area.height;
}

export class VisualFeedback {
  #settings=DEFAULTS;
  #state='idle';
  #marks=[];
  #viewport;
  #workArea;
  #clock;
  constructor({viewport={x:0,y:0,width:1280,height:800},workArea=viewport,settings={},clock=()=>performance.now()}={}) {
    this.#clock=clock;
    this.setViewport(viewport,workArea);
    this.configure(settings);
  }
  get settings() { return this.#settings; }
  get state() { return this.#state; }
  configure(patch) {
    const next={...this.#settings};
    for(const [key,value] of Object.entries(patch)) {
      if(!Object.hasOwn(DEFAULTS,key)) throw new RangeError('Unknown feedback setting');
      next[key]=value;
    }
    for(const key of ['enabled','compactIdle','reducedMotion']) if(typeof next[key]!=='boolean') throw new TypeError(`${key} must be boolean`);
    if(!POSITIONS.has(next.position)) throw new RangeError('Unknown position');
    within(next.opacity,0.35,1,'opacity'); within(next.uiScale,0.75,3,'uiScale');
    within(next.margin,0,80,'margin'); within(next.ringLifetimeMs,280,1200,'ringLifetimeMs');
    this.#settings=Object.freeze(next);
    if(!next.enabled) this.#marks=[];
  }
  setViewport(viewport,workArea=viewport) {
    const screen=rect(viewport),work=rect(workArea);
    if(work.x<screen.x || work.y<screen.y || work.x+work.width>screen.x+screen.width || work.y+work.height>screen.y+screen.height)
      throw new RangeError('Work area must fit inside viewport');
    this.#viewport=screen; this.#workArea=work;
    this.#marks=this.#marks.filter(mark=>contains(screen,mark.x,mark.y));
  }
  setState(state) {
    if(!Object.hasOwn(FEEDBACK_STATES,state)) throw new RangeError('Unknown feedback state');
    this.#state=state;
  }
  recordClick(x,y,at=this.#clock()) {
    number(x,'x'); number(y,'y'); number(at,'time');
    if(!this.#settings.enabled || !contains(this.#viewport,x,y)) return false;
    this.#marks=this.#marks.filter(mark=>at-mark.at<this.#settings.ringLifetimeMs);
    const previous=this.#marks.at(-1);
    // At most two marks. Bursts replace the last mark instead of flashing a trail.
    if(previous && (at-previous.at<80 || Math.hypot(x-previous.x,y-previous.y)<8*this.#settings.uiScale)) this.#marks.pop();
    this.#marks.push({x,y,at});
    this.#marks=this.#marks.slice(-2);
    return true;
  }
  snapshot(at=this.#clock()) {
    number(at,'time');
    const settings=this.#settings,scale=settings.uiScale;
    const base={visible:settings.enabled,viewport:{...this.#viewport},state:this.#state,label:FEEDBACK_STATES[this.#state].label,
      badge:null,rings:[],animated:false};
    if(!settings.enabled) return base;
    this.#marks=this.#marks.filter(mark=>at-mark.at<settings.ringLifetimeMs);
    const area=this.#workArea;
    const margin=Math.min(settings.margin*scale,area.width/4,area.height/4);
    const compact=(this.#state==='idle' && settings.compactIdle) || area.width-2*margin<170*scale || area.height-2*margin<34*scale;
    const width=Math.min((compact?26:170)*scale,area.width-2*margin);
    const height=Math.min((compact?26:34)*scale,area.height-2*margin);
    const place=position=>({x:position.endsWith('right')?area.x+area.width-margin-width:area.x+margin,
      y:position.startsWith('bottom')?area.y+area.height-margin-height:area.y+margin});
    base.badge={...place(settings.position),width,height,compact,tone:FEEDBACK_STATES[this.#state].tone,scale,opacity:settings.opacity,relocated:false};
    base.rings=this.#marks.map(mark=>{
      const progress=Math.max(0,(at-mark.at)/settings.ringLifetimeMs);
      return {x:mark.x,y:mark.y,radius:(settings.reducedMotion?17:12+16*progress)*scale,
        opacity:settings.opacity*(settings.reducedMotion?0.85:1-progress),strokeWidth:2*scale};
    });
    // Keep the badge away from the latest click location, then return to its chosen corner.
    const overlaps=point=>base.rings.some(r=>{
      const closestX=Math.max(point.x,Math.min(r.x,point.x+width));
      const closestY=Math.max(point.y,Math.min(r.y,point.y+height));
      return Math.hypot(r.x-closestX,r.y-closestY)<r.radius+6*scale;
    });
    if(overlaps(base.badge)){
      const alternative=[...POSITIONS].map(place).find(point=>!overlaps(point));
      base.badge=alternative?{...base.badge,...alternative,relocated:true}:null;
    }
    base.animated=base.rings.length>0; // Runs only until expiry, including reduced-motion mode.
    return base;
  }
}

const format = n => Number(number(n,'SVG coordinate').toFixed(3));
const xml = text => String(text).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'})[c]);
const toneColor = tone => ({quiet:'var(--wdc-quiet, GrayText)',active:'var(--wdc-active, Highlight)',
  waiting:'var(--wdc-waiting, Mark)',error:'var(--wdc-error, LinkText)'})[tone];

export function feedbackSvg(scene) {
  const v=rect(scene.viewport);
  const marks=scene.rings.map(r=>`<circle data-click-x="${format(r.x)}" data-click-y="${format(r.y)}" cx="${format(r.x)}" cy="${format(r.y)}" r="${format(r.radius)}" fill="none" stroke="var(--wdc-active, Highlight)" stroke-width="${format(r.strokeWidth)}" opacity="${format(r.opacity)}"/>`).join('');
  const cursor=scene.pointer;
  const arrow=cursor?`<path data-agent-pointer="true" transform="translate(${format(cursor.x)} ${format(cursor.y)}) scale(${format(cursor.scale)})" d="M0 0 L0 21 L5 16 L9 25 L13 23 L9 14 L17 14 Z" fill="#111" stroke="white" stroke-width="1.4" stroke-linejoin="round"/>`:'';
  const b=scene.badge;
  let badge='';
  if(b) {
    const middle=b.y+b.height/2;
    const dotX=b.compact?b.x+b.width/2:b.x+17*b.scale;
    badge=`<g data-feedback-badge="${xml(scene.state)}" opacity="${format(b.opacity)}">
      <rect x="${format(b.x)}" y="${format(b.y)}" width="${format(b.width)}" height="${format(b.height)}" rx="${format(Math.min(b.width,b.height)/2)}" fill="var(--wdc-panel, Canvas)" stroke="var(--wdc-border, GrayText)" stroke-width="0.7"/>
      <circle cx="${format(dotX)}" cy="${format(middle)}" r="${format(3.5*b.scale)}" fill="${toneColor(b.tone)}"/>
      ${b.compact?'':`<text x="${format(b.x+30*b.scale)}" y="${format(middle)}" dominant-baseline="central" font-family="Segoe UI, Microsoft YaHei, sans-serif" font-size="${format(13*b.scale)}" fill="var(--wdc-text, CanvasText)">${xml(scene.label)}</text>`}
    </g>`;
  }
  return `<svg xmlns="http://www.w3.org/2000/svg" width="100%" height="100%" viewBox="${v.x} ${v.y} ${v.width} ${v.height}" role="img" aria-label="${scene.visible?xml(scene.label):'提示已隐藏'}" focusable="false" style="pointer-events:none;display:block;overflow:hidden"><title>${scene.visible?xml(scene.label):'提示已隐藏'}</title>${marks}${badge}${arrow}</svg>`;
}

/** Mount only into a caller-owned preview/overlay surface. Never creates a window. */
export function mountFeedback(container,model,{requestFrame=cb=>globalThis.requestAnimationFrame(cb),cancelFrame=id=>globalThis.cancelAnimationFrame(id)}={}) {
  let frame=null,destroyed=false;
  function render(at) {
    if(destroyed) return;
    const scene=model.snapshot(at);
    container.innerHTML=feedbackSvg(scene);
    if(scene.animated && frame===null) frame=requestFrame(time=>{frame=null;render(time);});
    if(!scene.animated && frame!==null) {cancelFrame(frame);frame=null;}
    return scene;
  }
  render();
  return {render,destroy(){destroyed=true;if(frame!==null)cancelFrame(frame);frame=null;container.innerHTML='';}};
}
