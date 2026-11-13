import {parseArgs} from 'node:util';
import {resolve} from 'node:path';
import {performance} from 'node:perf_hooks';
import {McpBridge} from '../dsh-wdc-computer-use/lib/index.js';
import {TOOL_CATALOG} from '../dsh-wdc-computer-use/lib/tool-catalog.js';

const {values}=parseArgs({options:{python:{type:'string'},cwd:{type:'string'},samples:{type:'string',default:'20'}}});
const count=Number(values.samples);
if (!values.python || !values.cwd || !Number.isSafeInteger(count) || count<1 || count>100) {
  throw new Error('Usage: node scripts/benchmark-desktop.mjs --python <path> --cwd <runtime> [--samples 20]');
}
const bridge=new McpBridge({
  python:resolve(values.python),cwd:resolve(values.cwd),expectedTools:TOOL_CATALOG.tools,
  initTimeoutMs:15000,callTimeoutMs:5000,idleTimeoutMs:60000,
},()=>{});
const cpuStart=process.cpuUsage();
const rssStart=process.memoryUsage().rss;
const percentile=(ordered,p)=>Number(ordered[Math.ceil(ordered.length*p)-1].toFixed(2));
try {
  const start=performance.now();
  await bridge.start();
  const coldMs=performance.now()-start;
  const samples=[];
  for (let i=0;i<count;i++) {
    const before=performance.now();
    const result=await bridge.callTool('wait',{ms:0});
    if (result.isError || JSON.parse(result.content[0].text).ok !== true) throw new Error('Benchmark wait failed');
    samples.push(performance.now()-before);
  }
  samples.sort((a,b)=>a-b);
  console.log(JSON.stringify({
    scope:'Python MCP startup and wait(0), no desktop capture or input',samples:count,
    coldMs:Number(coldMs.toFixed(2)),warmMs:{p50:percentile(samples,0.50),p95:percentile(samples,0.95)},
    nodeCpuMicroseconds:process.cpuUsage(cpuStart),nodeRssDeltaBytes:process.memoryUsage().rss-rssStart,
  },null,2));
} finally {bridge.close();}
