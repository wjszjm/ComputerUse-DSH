import {TOOL_CATALOG} from './tool-catalog.js';
import {toggleIntent,privacyIndex,visibleImages,canonicalPath,callDisplay} from './client-policy.js';

export const inject=['slots','commandUi','sessions','uiConversation','conversation'];
const settingsTools = {
  window_screenshot:'窗口截图', inspect_controls:'读取控件', control_action:'操作控件',
  recognize_text:'本机 OCR', locate_target:'定位控件或文字', batch_windows:'验证式窗口批次',
};
const baseSettingsTools = {
  screenshot:'截图',screen_info:'屏幕信息',click:'点击',move_mouse:'移动鼠标',drag:'拖拽',
  type_text:'输入文字',press_key:'按键',scroll:'滚动',list_windows:'列出窗口',
  activate_window:'激活窗口',window_action:'窗口操作',clipboard_get:'读取剪贴板',wait:'等待',
};
const settingsId='computeruse-dsh';
const settingsGroup={margin:0,padding:'16px',border:'1px solid color-mix(in srgb, currentColor 20%, transparent)',borderRadius:12};
const settingsRow={display:'flex',alignItems:'center',justifyContent:'space-between',gap:12,minHeight:32};
const settingsGrid={display:'grid',gridTemplateColumns:'repeat(auto-fit, minmax(min(100%, 168px), 1fr))',gap:'8px 16px'};
const runtimeLabels={dormant:'休眠',idle:'待命',active:'运行中',starting:'启动中',stopping:'正在停止',failed:'启动失败'};
const permissionLabels={ask:'按需审批',autonomous:'按宿主授权执行','read-only':'只读',disabled:'已停用'};

function registerSettings(slots, react) {
  if (!react) return;
  const {createElement:h,useEffect,useState}=react;
  const call=async (payload,signal)=>{
    const endpoint=new URL('api/computeruse-dsh/settings',document.baseURI);
    const response=await fetch(endpoint,{
      method:payload?'POST':'GET',credentials:'same-origin',signal,
      headers:{'x-dsh-plugin':'dsh-wdc-computer-use',...(payload?{'content-type':'application/json','x-dsh-settings-token':payload.writeToken}:{})},
      body:payload?JSON.stringify(payload, (key,value)=>key==='writeToken'?undefined:value):undefined,
    });
    if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('DSH 设置接口不可用。');
    const data=await response.json();
    if (!response.ok) throw new Error(data.error || 'DSH 设置接口不可用。');
    return data;
  };
  function ComputerUseSettings({standalone=false}={}) {
    const [draft,update]=useState(null);
    const [baseline,setBaseline]=useState(null);
    const [message,show]=useState('正在读取设置');
    const [busy,setBusy]=useState(false);
    const [reload,setReload]=useState(0);
    useEffect(()=>{
      const controller=new AbortController();
      call(null,controller.signal).then(data=>{if(!controller.signal.aborted){update(data);setBaseline(data);show('');}},
        error=>{if(!controller.signal.aborted)show(error.message);});
      return ()=>controller.abort();
    },[reload]);
    const header=standalone?h('header',null,h('h2',{style:{margin:'0 0 6px'}},'电脑操控（Computer Use）'),
      h('p',{style:{margin:0,opacity:0.75}},'管理 DSH 的 Windows 桌面操作。')):null;
    if (!draft) return h('section',null,header,h('p',{role:'status'},message),
      message!=='正在读取设置'&&h('button',{type:'button',onClick:()=>{show('正在读取设置');setReload(value=>value+1);}},'重试'));
    const set=(key,value)=>update(current=>({...current,[key]:value}));
    const changed=JSON.stringify(draft)!==JSON.stringify(baseline);
    const choices=(label,key,items)=>h('label',{style:settingsRow},label,
      h('select',{value:draft[key],onChange:event=>set(key,event.target.value)},
        Object.entries(items).map(([value,title])=>h('option',{key:value,value},title))));
    const toggle=(label,checked,change,disabled=false)=>h('label',{style:settingsRow},label,
      h('input',{type:'checkbox',role:'switch',checked,disabled,onChange:event=>change(event.target.checked)}));
    const toolToggles=(tools,key)=>h('div',{style:settingsGrid},...Object.entries(tools).map(([tool,label])=>
      h('div',{key:tool},toggle(label,draft[key].includes(tool),
        checked=>set(key,checked?[...draft[key],tool]:draft[key].filter(value=>value!==tool))))));
    return h('section',{style:{display:'grid',gap:16,maxWidth:800}},header,
      h('p',{style:{margin:0,opacity:0.75}},`已注册 ${draft.registered} 项工具 · ${runtimeLabels[draft.runtime]??draft.runtime} · ${permissionLabels[draft.permissionMode]??draft.permissionMode}`),
      h('fieldset',{disabled:busy,style:settingsGroup},
        h('legend',null,'工具'),
        choices('工具档位','profile',{minimal:'精简',standard:'标准',full:'全部基础工具'}),
        h('h3',{style:{fontSize:'inherit',margin:'12px 0 4px'}},'可选桌面工具'),
        toolToggles(settingsTools,'enableTools'),
        h('details',{style:{marginTop:12}},h('summary',null,'从当前档位停用工具'),toolToggles(baseSettingsTools,'disabledTools'))),
      h('fieldset',{disabled:busy,style:settingsGroup},
        h('legend',null,'截图与操作提示'),
        h('label',{style:settingsRow},'截图缩放（0 为自动）',
          h('input',{type:'number',min:0,max:1,step:0.05,value:draft.screenshotScale,
            onChange:event=>set('screenshotScale',Number(event.target.value))})),
        toggle('显示桌面操作提示',draft.feedbackEnabled,value=>set('feedbackEnabled',value))),
      h('fieldset',{disabled:busy,style:settingsGroup},
        h('legend',null,'紧急停止'),
        choices('停止快捷键','stopHotkey',{'':'关闭',esc:'Esc','ctrl+shift+f12':'Ctrl + Shift + F12'})),
      h('fieldset',{disabled:busy,style:settingsGroup},
        h('legend',null,'上下文优化'),
        ...Object.entries({lazyAdvancedTools:'按需加载高级工具',compactHistory:'精简已替代的历史观察'}).map(([key,label])=>
          h('div',{key},toggle(draft.contextSupport?.[key]?label:label+'（当前 DSH 不支持）',
            draft[key]===true,value=>set(key,value),!draft.contextSupport?.[key]&&!draft[key])))),
      h('div',{style:{display:'flex',alignItems:'center',gap:10}},
        h('button',{type:'button',disabled:busy||!changed,onClick:async()=>{
          setBusy(true);
          try {
            await call(draft);
            const accepted=await call(null);
            update(accepted);
            setBaseline(accepted);
            show('已保存');
          } catch(error) {show(error.message);}
          finally {setBusy(false);}
        }},busy?'正在保存…':'保存'),
        h('button',{type:'button',disabled:busy||!changed,onClick:()=>{update(baseline);show('');}},'取消修改'),
        h('span',{role:'status'},message)));
  }
  slots.inject('settings.section',()=>slots.register({
    name:'settings.section',id:settingsId,order:16,label:()=> '电脑操控',
  },()=>h(ComputerUseSettings,{standalone:true})));
  slots.inject('settings.section.icon',()=>slots.register({
    name:'settings.section.icon',id:settingsId,
  },({size=16})=>h('span',{'aria-hidden':true,style:{display:'inline-grid',placeItems:'start center',width:size*0.65,height:size,
    border:'1.5px solid currentColor',borderRadius:size/2,boxSizing:'border-box'}},
  h('span',{style:{marginTop:2,width:1.5,height:4,borderRadius:2,background:'currentColor'}}))));
  slots.inject('plugins.bundle.config',()=>slots.register({
    name:'plugins.bundle.config',key:'dsh-wdc-computer-use',
  },ComputerUseSettings));
}

export function apply(ctx,config={},react) {
  const slots=ctx.slots;
  if(typeof slots?.inject!=='function'||typeof slots?.register!=='function')return;
  registerSettings(slots,react);
  const names=new Set(TOOL_CATALOG.tools.map(tool=>`${config.toolPrefix??'wdc_'}${tool.name}`));
  slots.inject('tool.call.toolview',()=>{
    for(const name of names){
      slots.register({name:'tool.call.toolview',key:name,priority:1000},
        props=>props.phase==='start'?'电脑操作调用中':null);
    }
  });
  if(typeof ctx.commandUi?.register==='function'&&typeof ctx.sessions?.binding==='function'&&
     typeof ctx.conversation?.input?.for==='function'){
    ctx.commandUi.register({name:'computer-use',label:()=> '电脑操作',description:()=> '选择或取消当前草稿的电脑操作意图；调用仍需权限',
      available:session=>!!ctx.sessions.binding(session.sessionId),ui:{kind:'action',run(session){
        const binding=ctx.sessions.binding(session.sessionId);
        if(binding)toggleIntent(ctx.conversation.input.for(binding.ctx));
      }}});
  }
  if(typeof ctx.uiConversation?.events?.register!=='function'||
     typeof ctx.uiConversation?.views?.register!=='function'||typeof ctx.uiConversation?.binding!=='function')return;
  const target='wdc-private-images';
  ctx.uiConversation.events.register({kind:target,target,
    match:event=>event.type==='tool/result'&&(event.data.meta?.wdc?.version===1||
      (event.data.meta?.wdc?.version===2&&['screenshot','window_screenshot'].includes(event.data.meta.wdc.tool))||
      (typeof event.data.meta?.path==='string'&&event.data.message?.content?.some(item=>item.type==='image')))?{id:String(event.seq),role:'start'}:null,
    start:(_context,match)=>({meta:match.event.data.meta,message:{content:(match.event.data.message?.content??[])
      .filter(item=>item.type==='image').map(item=>({type:'image',attachment:{attachmentId:item.attachment?.attachmentId}}))}}),update:context=>context.state,
    buildViewNode:context=>({key:context.key,kind:context.kind,id:context.id,target,data:context.state})});
  ctx.uiConversation.views.register({target,create:()=>{
    let records=new Map();return {empty:[],replace:({nodes})=>{records=new Map(nodes.map(n=>[n.id,n.data]));return [...records.values()];},
      apply:({upserts})=>{for(const n of upserts)records.set(n.id,n.data);return [...records.values()];}};
  }});
  const activated=new WeakSet();
  function recordsFor(id){
    const binding=ctx.uiConversation.binding(id);
    // A target is lazy: getSnapshot alone does not activate it. Markdown may
    // render before an image gallery ever subscribes to this source.
    if(!activated.has(binding)){
      activated.add(binding);
      try{binding.activate(target);}catch(error){activated.delete(binding);throw error;}
    }
    return binding.target(target);
  }
  let previous=[],cached=privacyIndex([]);
  const empty=[];
  function index(){
    const snapshots=[];
    for(const id of Object.keys(ctx.sessions.list.getSnapshot().byId).sort()){
      if(ctx.sessions.binding(id))snapshots.push(recordsFor(id).getSnapshot()??empty);
    }
    if(snapshots.length!==previous.length||snapshots.some((value,i)=>value!==previous[i])){
      previous=snapshots;cached=privacyIndex(snapshots.flat());
    }
    return cached;
  }
  // Keep legacy patched installations working during migration.
  const key=Symbol.for('dsh.wdc.ui-policy.v1');
  if(globalThis[key])throw new Error('A WDC client policy is already mounted');
  const policy={hiddenPath:value=>index().paths.has(canonicalPath(value)),
    hiddenMedia(value){
      if(this.hiddenPath(value))return true;
      try {const url=new URL(value);return url.pathname.endsWith('/api/file')&&this.hiddenPath(url.searchParams.get('path'));}catch{return false;}
    },
    callDisplay:(name,call)=>callDisplay(name,call,index(),names)};
  globalThis[key]=policy;
  ctx.effect(()=>()=>{if(globalThis[key]===policy)delete globalThis[key];});
  for(const name of ['conversation.chat.message.images','conversation.chat.user.images','conversation.trajectory.images','tool.call.images']){
    ctx.slots.inject(name,()=>ctx.slots.register({name,priority:1000,
      inject:id=>({hooks:{wdcRecords:recordsFor(id)}})},function Images(props){
      const records=props.useWdcRecords(value=>value)??[];
      const images=visibleImages(props.images,privacyIndex(records));
      if(!images.length)return null;
      return props.renderFactorySlot('conversation.images.content',{...props,images});
    }));
  }
}
