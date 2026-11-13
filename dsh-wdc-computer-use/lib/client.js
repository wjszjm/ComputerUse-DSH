window.__ModuleLoader__.load({id:'dsh-wdc-computer-use',factory:require=>{
const TOOL_CATALOG={"tools":[{"name":"screen_info"},{"name":"screenshot"},{"name":"click"},{"name":"move_mouse"},{"name":"drag"},{"name":"scroll"},{"name":"type_text"},{"name":"press_key"},{"name":"clipboard_get"},{"name":"clipboard_set"},{"name":"list_windows"},{"name":"activate_window"},{"name":"window_action"},{"name":"launch_app"},{"name":"run_command"},{"name":"wait"},{"name":"stop"},{"name":"resume"},{"name":"window_screenshot"},{"name":"inspect_controls"},{"name":"control_action"},{"name":"recognize_text"},{"name":"locate_target"},{"name":"batch_windows"}]};
// UI-only policy. No authorization, files, screenshots, timers or desktop APIs.
const INTENT='【使用电脑操作】 ';
function toggleIntent(shell) {
  const {draft,draftRev}=shell.snapshot;
  if(typeof draft!=='string')return false;
  const selected=draft.startsWith(INTENT);
  const accepted=shell.insertText(selected?'':INTENT,{start:0,end:selected?INTENT.length:0,draftRev});
  if(accepted)shell.focus();
  return accepted;
}
function canonicalPath(value) {
  if(typeof value!=='string')return '';
  try {value=decodeURIComponent(value.split(/[?#]/u)[0]);}catch{return '';}
  value=value.replace(/^file:\/\/\//iu,'').replace(/\\/gu,'/');
  // Only absolute, normalized paths are admitted; no filename heuristics.
  if(!/^(?:[a-z]:\/|\/|\/\/)/iu.test(value))return '';
  const windows=/^(?:[a-z]:\/|\/\/)/iu.test(value),parts=[];
  for(const part of value.split('/')){if(part==='.')continue;if(part==='..')parts.pop();else parts.push(part);}
  const path=parts.join('/');return windows?path.toLowerCase():path;
}
function privacyIndex(records) {
  const paths=new Set(),ids=new Set();
  for(const data of records){
    if(data.meta?.wdc?.version===2&&['screenshot','window_screenshot'].includes(data.meta.wdc.tool)){
      for(const id of data.meta.wdc.imageIds??[])if(typeof id==='string')ids.add(id);
    }
    if(data.meta?.wdc?.version!==1)continue;
    for(const raw of data.meta.wdc.screenshots??[]){const p=canonicalPath(raw);if(p)paths.add(p);}
  }
  // Two passes make replay/backfill independent of event arrival order.
  for(const data of records){
    if(!paths.has(canonicalPath(data.meta?.path)))continue;
    for(const item of data.message?.content??[])if(item.type==='image'&&typeof item.attachment?.attachmentId==='string')ids.add(item.attachment.attachmentId);
  }
  return {paths,ids};
}
function visibleImages(images,index) {
  return images.filter(image=>!index.ids.has(image.attachment?.attachmentId));
}
function callDisplay(toolName,call,index,names) {
  const own=names.has(toolName);
  const image=toolName==='read_image'&&index.paths.has(canonicalPath(call.block?.meta?.path ?? readPath(call.block)));
  if(!own&&!image)return undefined;
  // A preparing call has not begun executing; approvals have their own UI.
  return call.phase==='start'?'电脑操作调用中':null;
}
function readPath(block){try{return JSON.parse(block?.argsRaw??block?.call?.argsRaw??'{}').file_path;}catch{return '';}}


const inject=['slots','commandUi','sessions','uiConversation','conversation'];
const settingsTools = {
  window_screenshot:'窗口截图', inspect_controls:'读取控件', control_action:'操作控件',
  recognize_text:'本机 OCR', locate_target:'定位控件或文字', batch_windows:'验证式窗口批次',
};
const baseSettingsTools = {
  screenshot:'截图',screen_info:'屏幕信息',click:'点击',move_mouse:'移动鼠标',drag:'拖拽',
  type_text:'输入文字',press_key:'按键',scroll:'滚动',list_windows:'列出窗口',
  activate_window:'激活窗口',window_action:'窗口操作',clipboard_get:'读取剪贴板',wait:'等待',
};

function registerSettings(slots, react) {
  if (!react) return;
  const {createElement:h,useEffect,useState}=react;
  const call=async (payload,signal)=>{
    const endpoint=new URL('api/computeruse-dsh/settings',document.baseURI);
    const response=await fetch(endpoint,{
      method:payload?'POST':'GET',credentials:'same-origin',signal,
      headers:{'x-dsh-plugin':'dsh-wdc-computer-use',...(payload?{'content-type':'application/json'}:{})},
      body:payload?JSON.stringify(payload):undefined,
    });
    if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('DSH 设置接口不可用。');
    const data=await response.json();
    if (!response.ok) throw new Error(data.error || 'DSH 设置接口不可用。');
    return data;
  };
  slots.inject('plugins.bundle.config',()=>slots.register({
    name:'plugins.bundle.config',key:'dsh-wdc-computer-use',
  },function ComputerUseSettings(){
    const [draft,update]=useState(null);
    const [message,show]=useState('正在读取设置');
    const [busy,setBusy]=useState(false);
    useEffect(()=>{
      const controller=new AbortController();
      call(null,controller.signal).then(data=>{update(data);show('');},
        error=>{if(!controller.signal.aborted)show(error.message);});
      return ()=>controller.abort();
    },[]);
    if (!draft) return h('p',{role:'status'},message);
    const set=(key,value)=>update(current=>({...current,[key]:value}));
    const choices=(label,key,items)=>h('label',{style:{display:'block'}},label,' ',
      h('select',{value:draft[key],onChange:event=>set(key,event.target.value)},
        Object.entries(items).map(([value,title])=>h('option',{key:value,value},title))));
    const toggle=(label,checked,change)=>h('label',{style:{display:'block'}},
      h('input',{type:'checkbox',checked,onChange:event=>change(event.target.checked)}),' ',label);
    return h('section',null,
      h('p',null,`已注册 ${draft.registered} 项工具 · ${draft.runtime} · 权限模式 ${draft.permissionMode}`),
      h('fieldset',{disabled:busy},
        h('legend',null,'电脑操作设置'),
        choices('工具档位','profile',{minimal:'精简',standard:'标准',full:'全部基础工具'}),
        choices('停止快捷键','stopHotkey',{'':'关闭',esc:'Esc','ctrl+shift+f12':'Ctrl + Shift + F12'}),
        h('label',{style:{display:'block'}},'截图缩放（0 为自动） ',
          h('input',{type:'number',min:0,max:1,step:0.05,value:draft.screenshotScale,
            onChange:event=>set('screenshotScale',Number(event.target.value))})),
        toggle('显示桌面操作提示',draft.feedbackEnabled,value=>set('feedbackEnabled',value)),
        ...Object.entries(settingsTools).map(([key,label])=>h('div',{key},toggle(label,draft.enableTools.includes(key),
          enabled=>set('enableTools',enabled?[...draft.enableTools,key]:draft.enableTools.filter(item=>item!==key))))),
        h('details',null,h('summary',null,'从当前档位停用工具'),
          ...Object.entries(baseSettingsTools).map(([key,label])=>h('div',{key},toggle(label,draft.disabledTools.includes(key),
            disabled=>set('disabledTools',disabled?[...draft.disabledTools,key]:draft.disabledTools.filter(item=>item!==key)))))),
        h('button',{type:'button',onClick:async()=>{
          setBusy(true);
          try {
            await call(draft);
            update(await call(null));
            show('已保存');
          } catch(error) {show(error.message);}
          finally {setBusy(false);}
        }},'保存')),
      h('p',{role:'status'},message));
  }));
}

function apply(ctx,config={},react) {
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

return {inject,apply:(ctx,config)=>apply(ctx,config,typeof require==='function'?require('react'):undefined)};
}});
