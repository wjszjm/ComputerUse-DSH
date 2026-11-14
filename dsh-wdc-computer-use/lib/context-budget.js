import { createHmac, randomBytes } from 'node:crypto';

const advanced = new Set([
  'inspect_controls','control_action','recognize_text','locate_target','batch_windows',
]);
const textObservations = new Set(['list_windows','inspect_controls','recognize_text']);
const imageObservations = new Set(['screenshot','window_screenshot']);
const observations = new Set([...textObservations,...imageObservations]);
const querySalt = randomBytes(32);

export function supportsLazyTools(tools) {
  return typeof tools?.resolve === 'function' && typeof tools?.executable === 'function';
}

export function createLazyTools(selected, options) {
  const allowed = Object.freeze(selected.map(({name})=>name).filter(name=>advanced.has(name)));
  const loaded = new WeakMap();
  let fallback = false;
  const canUse = session => session && typeof session === 'object';
  return {
    allowed,
    useFixedTools() { fallback = true; },
    condition(name) {
      if (!allowed.includes(name)) return undefined;
      return {when:({agent,signal})=>!signal.aborted &&
        (fallback || loaded.get(agent?.session)?.has(name) === true)};
    },
    definition() {
      const prefix = options.toolPrefix;
      return {
        name:prefix+'load_tools',
        description:'Load permitted Windows UIA, OCR, or verified window-batch tools for this session. Omit names to list. Loading grants no desktop permission.',
        parameters:{type:'object',properties:{
          names:{type:'array',items:{type:'string',enum:allowed},minItems:1,maxItems:allowed.length},
        },additionalProperties:false},
        output:{
          schema:{type:'object',properties:{available:{type:'array',items:{type:'string'}},
            loaded:{type:'array',items:{type:'string'}},error:{type:'string'}},additionalProperties:false},
          render:(_args,value)=>[{type:'text',text:JSON.stringify(value)}],
        },
        execute({names}={},exec) {
          const session = exec?.agent?.session;
          if (!canUse(session) || exec.signal?.aborted) return {error:'Tool loading requires an active DSH session.'};
          if (names !== undefined && (!Array.isArray(names) || !names.length || names.some(name=>!allowed.includes(name)))) {
            return {error:'Requested tool is disabled.'};
          }
          const active = loaded.get(session) ?? new Set();
          for (const name of names ?? []) active.add(name);
          loaded.set(session,active);
          return {available:allowed,loaded:allowed.filter(name=>active.has(name)).map(name=>prefix+name)};
        },
      };
    },
  };
}

function parseObservation(event, message) {
  if (event?.type !== 'tool/result' ||
      event.data.error || message?.isError || !Array.isArray(message?.content)) return null;
  const meta = event.data.meta?.wdc;
  if (meta?.version !== 2 || !observations.has(meta.tool)) return null;
  const text = message.content.find(block=>block.type==='text')?.text;
  if (typeof text !== 'string') return null;
  let data;
  try { data=JSON.parse(text); } catch { return null; }
  if (data?.ok !== true || data.action !== meta.tool) return null;
  return {tool:meta.tool,data,text,meta};
}

function observationKey({tool,data,meta}) {
  if (imageObservations.has(tool)) {
    if (!data.target || !data.coordinate_context_valid || !data.screen_region || !data.image_size) return null;
    return JSON.stringify([tool,data.target,data.monitor,data.screen_region,data.image_size,data.pointer_input]);
  }
  // UIA/OCR require a known identical selector. Older results lack provenance.
  if (meta.queryKey === undefined) return null;
  return JSON.stringify([tool,meta.queryKey]);
}

export function pruneObservations(session, turn, meter, signal) {
  if (signal?.aborted || !Number.isSafeInteger(turn) || turn<2 ||
      typeof session?.eventAt !== 'function' || typeof session?.deriveEventMessage !== 'function' ||
      typeof session?.append !== 'function' || !('currentBatchPublication' in session) ||
      typeof meter?.estimateMessage !== 'function') return 0;
  const latest = new Map();
  const images = [];
  const text = [];
  const recent = session.surface.nodes.slice(-256).reverse().map(seq=>session.eventAt(seq));
  const problemTurns = new Set();
  for (const event of recent) {
    if (event?.type !== 'tool/result' || !event.data.meta?.wdc) continue;
    if (event.data.error || event.data.message?.isError ||
        event.data.meta.wdc.verified === false) problemTurns.add(event.data.turn);
  }
  for (const event of recent) {
    if (event?.type !== 'tool/result' || !Number.isSafeInteger(event.data.turn) || event.data.turn >= turn) continue;
    const {seq} = event;
    const message = session.deriveEventMessage(event);
    const result = parseObservation(event,message);
    if (!result) continue;
    const key = observationKey(result);
    if (key === null) continue;
    const count = latest.get(key) ?? 0;
    latest.set(key,count+1);
    if (count < 2 || problemTurns.has(event.data.turn)) continue;
    if (imageObservations.has(result.tool)) {
      const imageBlocks = message.content.filter(block=>block.type==='image');
      if (images.length<8 && imageBlocks.length===1 && !imageBlocks[0].offloaded &&
          result.meta.imageIds?.includes(imageBlocks[0].attachment?.attachmentId)) {
        images.push({seq,imageIndexes:[0]});
      }
    } else if (text.length<16 && event.surfaceOp === 'append' && result.text.length>1800 && message.content.length===1) {
      const retained = {
        ok:true,action:result.tool,archived:true,next_step:'Repeat the observation for current details.',
        truncated:result.data.truncated,ambiguous:result.data.ambiguous,
      };
      const replacement = {...message,content:[{type:'text',text:JSON.stringify(retained)}]};
      text.push(
        ['compaction/prune',{shadowedRange:{start:seq,end:seq},shadowedSeqs:[seq],
          shadowedTokenCount:meter.estimateMessage(message)}],
        ['tool/result',{...event.data,message:replacement},
          {surfaceOp:{op:'replace',startSeq:seq,endSeq:seq},sourceEventSeqs:[seq]}],
      );
    }
  }
  if (signal?.aborted || (!images.length && !text.length)) return 0;
  // Official event types keep the append-only log and the detached reader compatible.
  session.append([...text,...(images.length ? [['image/offload',{targets:images}]] : [])]);
  return text.length/2+images.length;
}

export function queryKey(tool,args,value) {
  if (!textObservations.has(tool) || !value?.ok) return undefined;
  const fields = tool === 'list_windows'
    ? [args.title ?? '',args.include_hidden ?? false,args.limit ?? 50,args.detail ?? 'compact']
    : tool === 'inspect_controls'
      ? [args.handle,args.expected_pid,args.name ?? '',args.control_type ?? '',args.background ?? false,
        args.limit ?? 40,args.detail ?? 'compact']
      : [args.frame_id,args.language ?? 'en-US',args.query ?? '',args.limit ?? 160,args.detail ?? 'compact'];
  return createHmac('sha256',querySalt).update(JSON.stringify(fields)).digest('hex');
}

export function resultVerified(tool,value) {
  if (value?.ok===false) return false;
  if (observations.has(tool) || typeof value?.text!=='string') return undefined;
  // Only inspect actions that could invalidate visual evidence.
  if (!['click','drag','press_key','type_text','scroll','control_action','activate_window','window_action','batch_windows'].includes(tool)) return undefined;
  try { return JSON.parse(value.text)?.verification?.status === 'verified'; }
  catch { return false; }
}
