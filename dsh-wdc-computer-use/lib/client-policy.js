// UI-only policy. No authorization, files, screenshots, timers or desktop APIs.
export const INTENT='【使用电脑操作】 ';
export function toggleIntent(shell) {
  const {draft,draftRev}=shell.snapshot;
  if(typeof draft!=='string')return false;
  const selected=draft.startsWith(INTENT);
  const accepted=shell.insertText(selected?'':INTENT,{start:0,end:selected?INTENT.length:0,draftRev});
  if(accepted)shell.focus();
  return accepted;
}
export function canonicalPath(value) {
  if(typeof value!=='string')return '';
  try {value=decodeURIComponent(value.split(/[?#]/u)[0]);}catch{return '';}
  value=value.replace(/^file:\/\/\//iu,'').replace(/\\/gu,'/');
  // Only absolute, normalized paths are admitted; no filename heuristics.
  if(!/^(?:[a-z]:\/|\/|\/\/)/iu.test(value))return '';
  const windows=/^(?:[a-z]:\/|\/\/)/iu.test(value),parts=[];
  for(const part of value.split('/')){if(part==='.')continue;if(part==='..')parts.pop();else parts.push(part);}
  const path=parts.join('/');return windows?path.toLowerCase():path;
}
export function privacyIndex(records) {
  const paths=new Set(),ids=new Set();
  for(const data of records){
    if(data.meta?.wdc?.version===2&&data.meta.wdc.tool==='screenshot'){
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
export function visibleImages(images,index) {
  return images.filter(image=>!index.ids.has(image.attachment?.attachmentId));
}
export function callDisplay(toolName,call,index,names) {
  const own=names.has(toolName);
  const image=toolName==='read_image'&&index.paths.has(canonicalPath(call.block?.meta?.path ?? readPath(call.block)));
  if(!own&&!image)return undefined;
  // A preparing call has not begun executing; approvals have their own UI.
  return call.phase==='start'?'电脑操作调用中':null;
}
function readPath(block){try{return JSON.parse(block?.argsRaw??block?.call?.argsRaw??'{}').file_path;}catch{return '';}}
