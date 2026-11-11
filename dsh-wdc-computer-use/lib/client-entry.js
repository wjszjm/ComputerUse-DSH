import {TOOL_CATALOG} from './tool-catalog.js';
import {toggleIntent,privacyIndex,visibleImages,canonicalPath,callDisplay} from './client-policy.js';

export const inject=['slots','commandUi','sessions','uiConversation','conversation'];
export function apply(ctx,config={}) {
  const names=new Set(TOOL_CATALOG.tools.map(tool=>`${config.toolPrefix??'wdc_'}${tool.name}`));
  const target='wdc-private-images';
  ctx.uiConversation.events.register({kind:target,target,
    match:event=>event.type==='tool/result'&&(event.data.meta?.wdc?.version===1||
      (event.data.meta?.wdc?.version===2&&event.data.meta.wdc.tool==='screenshot')||
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
  ctx.commandUi.register({name:'computer-use',label:()=> '电脑操作',description:()=> '选择或取消当前草稿的电脑操作意图；调用仍需权限',
    available:session=>!!ctx.sessions.binding(session.sessionId),ui:{kind:'action',run(session){
      const binding=ctx.sessions.binding(session.sessionId);
      if(binding)toggleIntent(ctx.conversation.input.for(binding.ctx));
    }}});
  ctx.slots.inject('tool.call.toolview',()=>{
    for(const name of names){
      ctx.slots.register({name:'tool.call.toolview',key:name,priority:1000},
        props=>props.phase==='start'?'电脑操作调用中':null);
    }
  });
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
