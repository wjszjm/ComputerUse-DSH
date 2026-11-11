window.__ModuleLoader__.load({id:'dsh-wdc-computer-use',factory:()=>{
const TOOL_CATALOG={"tools":[{"name":"screen_info"},{"name":"screenshot"},{"name":"click"},{"name":"move_mouse"},{"name":"drag"},{"name":"scroll"},{"name":"type_text"},{"name":"press_key"},{"name":"clipboard_get"},{"name":"clipboard_set"},{"name":"list_windows"},{"name":"activate_window"},{"name":"window_action"},{"name":"launch_app"},{"name":"run_command"},{"name":"wait"},{"name":"stop"},{"name":"resume"}]};
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
function apply(ctx,config={}) {
  const names=new Set(TOOL_CATALOG.tools.map(tool=>`${config.toolPrefix??'wdc_'}${tool.name}`));
  const target='wdc-private-images';
  ctx.uiConversation.events.register({kind:target,target,
    match:event=>event.type==='tool/result'&&(event.data.meta?.wdc?.version===1||
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
  // Version-pinned host adapters consult this before constructing any image/card.
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
  ctx.commandUi.register({name:'computer-use',label:()=> '电脑操作',description:()=> '选择或取消当前草稿的电脑操作意图；调用仍需权限',
    available:session=>!!ctx.sessions.binding(session.sessionId),ui:{kind:'action',run(session){
      const binding=ctx.sessions.binding(session.sessionId);
      if(binding)toggleIntent(ctx.conversation.input.for(binding.ctx));
    }}});
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

return {inject,apply};
}});
