/** Deterministic workload. Counts every request, including setup and a retry. */
import {parseArgs} from 'node:util';
import {TOOL_CATALOG} from '../dsh-wdc-computer-use/lib/tool-catalog.js';
import {buildDescription,annotateSchema,pruneSchema} from '../dsh-wdc-computer-use/lib/index.js';
import {createLazyTools,pruneObservations,rememberObservationEvidence,observationDigest}
  from '../dsh-wdc-computer-use/lib/context-budget.js';

const {values} = parseArgs({options:{'image-cost':{type:'string',default:'384'}}});
const imageCost = Number(values['image-cost']);
if (!Number.isSafeInteger(imageCost) || imageCost < 0) throw Error('Pass a nonnegative --image-cost.');
const advanced = new Set(['inspect_controls','control_action','recognize_text','locate_target','batch_windows']);
const core = new Set(['screenshot','screen_info','click','type_text','press_key','scroll',
  'list_windows','activate_window','window_action','wait','move_mouse','drag','stop','resume']);
const all = TOOL_CATALOG.tools.filter(tool=>core.has(tool.name) || advanced.has(tool.name));
const serialize = ({name,description,inputSchema})=>({name:'wdc_'+name,
  description:buildDescription(name,description,name==='screenshot' ? TOOL_CATALOG.instructions : ''),
  parameters:annotateSchema(name,pruneSchema(inputSchema))});
const fixedTools = all.map(serialize);
const estimateMessage = message => 4+message.content.reduce((n,block)=>n+
  (block.type==='image' ? (block.offloaded ? 8 : imageCost) : 4+Math.ceil(block.text.length/4)),0);
const message = (text,role='tool')=>({role,content:[{type:'text',text}]});

function replay(scenario,mode) {
  let attempts=0, retries=0, management=0, imagesOffloaded=0, fixed=0, input=0, output=0;
  const history=[message('Complete the specified task in the synthetic editor. Confirm the final state.','user')];
  const events=[];
  const session={surface:{nodes:[]},currentBatchPublication:null,
    eventAt:seq=>events[seq],deriveEventMessage:event=>event.data.message,
    append(rows){
      for(const [event,data] of rows) {
        if (event!=='image/offload') continue;
        for(const {seq,imageIndexes} of data.targets) {
          for(const index of imageIndexes) events[seq].data.message.content.filter(block=>block.type==='image')[index].offloaded=true;
          imagesOffloaded++;
        }
      }
    }};
  const lazy = createLazyTools(all,{toolPrefix:'wdc_'});
  const manager = lazy.definition();
  const {name,description,parameters} = manager;
  const execution = {agent:{session},signal:new AbortController().signal};
  const request = (reply,{retry=false}={})=>{
    const tools = mode==='fixed' ? fixedTools :
      [...all.filter(tool=>core.has(tool.name) || lazy.condition(tool.name)?.when(execution)).map(serialize),
        {name,description,parameters}];
    const definitions = 4+Math.ceil(JSON.stringify(tools).length/4);
    fixed+=definitions;
    input+=definitions+history.reduce((total,item)=>total+estimateMessage(item),0);
    attempts++;
    if (retry) { retries++; return; }
    history.push(message(reply,'assistant'));
    output += Math.ceil(reply.length/4);
  };
  const takeShot=()=>{
    const seq=events.length;
    const text=JSON.stringify({ok:true,action:'screenshot',frame_id:'fixture-'+seq,coordinate_context_valid:true,
      target:{handle:1,pid:2},screen_region:[32,48,640,360],image_size:[640,360],grid:false});
    const data=message(text);
    const attachmentId='fixture-'+seq;
    data.content.push({type:'image',attachment:{attachmentId}});
    const evidence=observationDigest(rememberObservationEvidence({ok:true,text},'screenshot',
      {content:[{type:'image',data:'synthetic identical image'}]}));
    events.push({seq,type:'tool/result',data:{turn:1,message:data,
      meta:{wdc:{version:2,tool:'screenshot',imageIds:[attachmentId],evidence}}}});
    session.surface.nodes.push(seq);
    history.push(data);
  };
  for(let index=0;index<3;index++) {
    request('wdc_screenshot');
    takeShot();
  }
  if(mode==='lazy+history') pruneObservations(session,2,{estimateMessage});
  if(scenario==='advanced') {
    if(mode!=='fixed') {
      request('wdc_load_tools: inspect_controls');
      management++;
      history.push(message(JSON.stringify(manager.execute({names:['inspect_controls']},execution))));
    }
    request('wdc_inspect_controls', {retry:true});
    request('wdc_inspect_controls');
    history.push(message(JSON.stringify({controls:[{id:'1.2',name:'Save',enabled:true}]})));
  }
  request('Verified the final state.');
  return {scenario,mode,attempts,retries,management,imagesOffloaded,
    estimatedSchemaInput:fixed,estimatedInput:input,estimatedOutput:output,
    providerCacheRead:null,providerCacheWrite:null};
}

console.log(JSON.stringify({scope:'deterministic complete-task replay; 4 characters per estimated text token',
  imageCost,rows:['basic','advanced'].flatMap(scenario=>['fixed','lazy','lazy+history'].map(mode=>replay(scenario,mode)))},null,2));
