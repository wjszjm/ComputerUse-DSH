/**
 * dsh-wdc-computer-use
 *
 * 把本机 Win Desktop Control（Python MCP Server，stdio 传输）接入 DeepSeek Harness。
 *
 * 工作方式：
 *   1. 插件激活时仅注册随包校验的工具定义；首次调用才启动 Python
 *   2. 走标准 MCP JSON-RPC（换行分隔）：initialize → notifications/initialized → tools/list
 *   3. 把服务端声明的每个工具注册到 DSH 的 tools 服务，名称加 `wdc_` 前缀避免冲突
 *   4. 模型调用时转发 tools/call，返回结果
 *
 * 设计约束：
 *   - 任何一步失败都不能让 DSH 启动失败：全部 try/catch，失败只记日志、不抛异常
 *   - 截图通过 DSH 原生附件交给模型，保留工具坐标对应的图像尺寸
 *   - 子进程随插件卸载而关闭
 */

import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { mkdirSync, statSync, unlinkSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import {FeedbackSession,summarizeFeedbackResult} from './feedback-session.js';
import {NativeFeedbackHost} from './native-feedback-host.js';
import {SessionPermissions, OBSERVATION_TOOLS} from './permissions.js';
import {TOOL_CATALOG} from './tool-catalog.js';
import {recordFailure} from './failure-log.js';
import {pluginName, pluginVersion, versionLabel} from './version.js';
import {mountSettings, optionalTools} from './settings.js';
import {createLazyTools, supportsLazyTools, supportsCompactHistory, pruneObservations, queryKey, resultVerified} from './context-budget.js';

export const name = 'wdc-computer-use';
export const inject = [];

/** 默认不暴露的工具：run_command 需要服务端开 allow_shell，暴露它只是占上下文 */
const DEFAULT_HIDDEN = ['run_command'];
const CONTROL_TOOLS = new Set(['stop', 'resume']);
const RETRY_SAFE_TOOLS = OBSERVATION_TOOLS;
const IMAGE_TOOLS = new Set(['screenshot','window_screenshot']);
const OPT_IN_TOOLS = optionalTools;

function bridgeError(code, message, dispatched = false) {
  return Object.assign(new Error(message), { code, dispatched });
}

function toolFailureCode(result) {
  if (!Array.isArray(result?.content) || !result.content.length) return 'tool_failed';
  for (const item of result.content) {
    if (item?.type !== 'text') continue;
    try {
      const value = JSON.parse(item.text);
      if (value?.ok === false) return value.error_code || 'tool_failed';
    } catch { /* Ordinary text is allowed. */ }
  }
  return result.isError === true ? 'tool_failed' : undefined;
}

function toolSucceeded(result) {
  return toolFailureCode(result) === undefined;
}

/**
 * 工具档位。
 *
 * 工具定义是每轮都要重发的固定开销，而绝大多数任务只用得到 4-5 个。
 * 按使用场景裁剪能力集，比"全部常驻"更省；需要全量时切到 full。
 */
const TOOL_PROFILES = {
  /** 极简：只保留完成任务闭环的最小集，每轮开销最低 */
  minimal: ['screenshot', 'click', 'type_text', 'press_key', 'window_action', 'launch_app', 'wait'],
  /** 日常使用：补齐屏幕信息、滚动、窗口枚举与激活、剪贴板读取 */
  standard: [
    'screenshot', 'screen_info', 'click', 'type_text', 'press_key', 'scroll',
    'launch_app', 'list_windows', 'activate_window', 'window_action', 'clipboard_get', 'wait',
  ],
  /** 全部工具（run_command 仍受 hideTools 控制） */
  full: null,
};

const DEFAULTS = {
  /** 安装目录下 venv 的解释器，必须是绝对路径 */
  python: path.resolve(import.meta.dirname, '../../.venv/Scripts/python.exe'),
  /** 子进程工作目录（设为项目根，便于 `-m wdc` 解析） */
  cwd: path.resolve(import.meta.dirname, '../../win-desktop-control'),
  statusPath: path.resolve(import.meta.dirname, '../../.runtime/wdc-plugin-status.json'),
  stopFile: path.resolve(import.meta.dirname, '../../.runtime/wdc-stop.flag'),
  stopHotkey: '',
  screenshotScale: 0,
  lazyAdvancedTools: false,
  compactHistory: false,
  /** Both display flags must be enabled explicitly before starting an own-process host. */
  feedbackEnabled: false,
  feedbackNativeEnabled: false,
  feedbackSettings: {},
  feedbackViewport: {},
  feedbackWorkArea: {},
  /** 工具名前缀，避免与宿主内置工具重名 */
  toolPrefix: 'wdc_',
  /** 单次工具调用超时（毫秒） */
  callTimeoutMs: 120000,
  /** 握手超时（毫秒） */
  initTimeoutMs: 30000,
  /** Quiet reuse between calls; release the own worker after one idle minute. */
  idleTimeoutMs: 60000,
  permissionMode: 'ask',
  rememberSessionApprovals: true,
  approvalTimeoutMs: 60000,
  /** 不注册的工具名（服务端工具名，非 wdc_ 前缀名），用于给上下文减负 */
  hideTools: DEFAULT_HIDDEN,
  enableTools: [],
  /** 是否精简 schema（去 title / 空默认值 / 截断超长描述） */
  pruneSchema: true,
  /** 工具档位：minimal | standard | full，决定注册哪些工具 */
  profile: 'standard',
};

const PROTOCOL_VERSION = '2024-11-05';

/**
 * 精简 JSON Schema，压掉纯冗余。
 *
 * 工具定义是每轮对话都要重新发一遍的固定开销。实测 18 个工具原文 7,419 字符，
 * 其中 title 字段（每个参数都带）、default: null、超长描述贡献了大半，而它们
 * 对模型判断"何时调用、怎么调用"没有增量信息。压缩后语义不变，开销减半。
 */
function pruneSchema(schema, depth = 0) {
  if (!schema || typeof schema !== 'object' || depth > 8) return schema;
  if (Array.isArray(schema)) return schema.map((item) => pruneSchema(item, depth + 1));

  const out = {};
  for (const [key, value] of Object.entries(schema)) {
    if (key === 'title') continue;                              // 参数名已经表达了含义
    if (key === 'default' && (value === null || value === '')) continue; // 空默认值无信息
    if (key === 'description' && typeof value === 'string') {
      const text = value.replace(/\s+/g, ' ').trim();
      out[key] = text.length > 160 ? `${text.slice(0, 160)}…` : text;
      continue;
    }
    out[key] = pruneSchema(value, depth + 1);
  }
  return out;
}

// Keep the safety contract in one stable place, including when screenshot is disabled.
function buildDescription(toolName, description, instructions) {
  const summary = description || TOOL_CATALOG.tools.find(tool => tool.name === toolName)?.description || '';
  const guidance = instructions || (toolName === 'screenshot' ? TOOL_CATALOG.instructions : '');
  return guidance ? [summary, guidance].filter(Boolean).join('\n\n') : summary;
}

function annotateSchema(_toolName, schema) {
  if (schema?.properties?.frame_id && !schema.properties.frame_id.description) {
    return {...schema, properties:{...schema.properties,
      frame_id:{...schema.properties.frame_id, description:'ID from a fresh screenshot; expires after input.'}}};
  }
  return schema;
}

function pickConfig(config) {
  const out = { ...DEFAULTS };
  if (config && typeof config === 'object') {
    for (const key of Object.keys(DEFAULTS)) {
      const value = config[key];
      if (typeof value === typeof DEFAULTS[key] && (typeof value !== 'string' || value !== '')) {
        out[key] = value;
      }
    }
  }
  if (!Number.isFinite(out.idleTimeoutMs) || out.idleTimeoutMs < 1000 || out.idleTimeoutMs > 3600000) out.idleTimeoutMs = DEFAULTS.idleTimeoutMs;
  if (!Number.isFinite(out.approvalTimeoutMs) || out.approvalTimeoutMs < 1000 || out.approvalTimeoutMs > 300000) out.approvalTimeoutMs = 60000;
  if (!['esc','ctrl+shift+f12'].includes(out.stopHotkey)) out.stopHotkey = '';
  if (!Number.isFinite(out.screenshotScale) || out.screenshotScale < 0 || out.screenshotScale > 1) out.screenshotScale = 0;
  return out;
}

/**
 * 最小 MCP stdio 客户端。
 * 只实现 initialize / tools/list / tools/call，够用且不引入额外依赖。
 */
class McpBridge {
  constructor(options, log) {
    this.options = options;
    this.log = log;
    this.child = null;
    this.pending = new Map();
    this.nextId = 1;
    this.stderrTail = [];
    this.starting = null;
    this.restarting = null;
    this.waking = null;
    this.ready = false;
    this.pythonHandshake = null;
    this.activeCalls = 0;
    this.idleTimer = null;
    this.needsFreshFrame = false;
    this.pendingImageDelivery = false;
    this.exits = new Map();
    this.activeMutations = 0;
    this.stopped = false;
    this.controlVersion = 0;
    this.inDoubt = new Map();
    this.closed = false;
    /** 服务端在 initialize 响应里下发的使用指引（标准循环 / 坐标规则 / 安全约束） */
    this.instructions = '';
    try {
      this.feedback = new FeedbackSession({enabled:options.feedbackEnabled,settings:options.feedbackSettings,
        activeOnly:true,
        viewport:options.feedbackViewport,workArea:options.feedbackWorkArea,
        onObserverError:()=>{try{this.log('操作提示观察者已禁用，不影响工具执行');}catch{}}});
    } catch {
      this.feedback = new FeedbackSession();
      try{this.log('操作提示配置无效，已关闭提示，不影响工具执行');}catch{}
    }
  }

  runtimeState(state) {
    try { this.options.onRuntimeState?.({state, activeCalls:this.activeCalls, stopped:this.isStopped()}); } catch { /* Diagnostic only. */ }
  }

  cancelIdle() { clearTimeout(this.idleTimer); this.idleTimer = null; }

  scheduleIdle() {
    this.cancelIdle();
    if (this.closed || this.activeCalls || !this.child || this.inDoubt.size || this.pending.size) return;
    this.runtimeState('idle');
    this.idleTimer = setTimeout(() => {
      this.idleTimer = null;
      if (this.closed || this.activeCalls || this.pending.size || this.inDoubt.size || this.restarting || this.waking) return;
      this.retire(this.child, bridgeError('service_idle', '空闲服务已回收；下一次调用重新启动'));
      this.runtimeState('dormant');
    }, this.options.idleTimeoutMs ?? DEFAULTS.idleTimeoutMs);
    this.idleTimer.unref?.();
  }

  async waitForExit() {
    const waits = [...this.exits.values()];
    if (!waits.length) return;
    let timer;
    try {
      await Promise.race([Promise.all(waits), new Promise((_,reject) => {
        timer = setTimeout(() => reject(bridgeError('service_busy', '旧 WDC 进程未退出，未启动新进程。')), 2000);
      })]);
    } finally { clearTimeout(timer); }
  }

  ensureReady() {
    if (this.closed) return Promise.reject(bridgeError('bridge_closed', 'WDC 桥已关闭'));
    if (this.restarting) return this.restarting;
    if (this.waking) return this.waking;
    if (this.child) return this.starting;
    const task = (async () => {
      if (this.waitForPreviousWorker) await this.waitForPreviousWorker();
      await this.waitForExit();
      return this.start();
    })();
    this.waking = task;
    task.finally(() => { if (this.waking === task) this.waking = null; }).catch(() => {});
    return task;
  }

  feedbackInvoke(method,...args) {
    try {return this.feedback?.[method](...args);}
    catch {
      try{this.feedback?.dispose();}catch{}
      this.feedback=null;
      try{this.log('操作提示已关闭，不影响工具执行');}catch{}
    }
  }

  refreshFeedbackControl() {
    // The safety state remains authoritative; rendering cannot change it.
    this.feedbackInvoke('setControl',{stopped:this.isStopped(),
      busy:this.activeMutations>0||[...this.inDoubt.values()].some(child=>this.exits.has(child))});
  }

  start() {
    if (this.closed) return Promise.reject(bridgeError('bridge_closed', 'WDC 桥已关闭'));
    if (this.starting) return this.starting;
    if (this.exits.size) return Promise.reject(bridgeError('service_busy', '旧 WDC 进程尚未退出'));
    this.cancelIdle();
    this.pythonHandshake = null;
    this.runtimeState('starting');
    const starting = new Promise((resolve, reject) => {
      let settled = false;
      const finish = (error) => {
        if (settled) return;
        settled = true;
        if (error) reject(error);
        else resolve();
      };

      let child;
      try {
        child = spawn(this.options.python, ['-m', 'wdc', 'serve'], {
          cwd: this.options.cwd,
          stdio: ['pipe', 'pipe', 'pipe'],
          windowsHide: true,
          env: { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8', WDC_STOP_FILE: this.stopFile(),
            WDC_STOP_HOTKEY:this.options.stopHotkey ?? '', WDC_FEEDBACK_EVENTS: this.options.feedbackEnabled === true ? '1' : '0' },
        });
      } catch (error) {
        this.options.onFailure?.('python_start', 'service_start_failed');
        finish(error instanceof Error ? error : new Error(String(error)));
        return;
      }

      this.child = child;
      let exited;
      this.exits.set(child, new Promise(resolveExit => { exited = resolveExit; }));

      child.on('error', (error) => {
        if (!this.closed && this.child === child) this.options.onFailure?.('python_process', 'transport_error');
        this.log(`子进程启动失败：${error instanceof Error ? error.message : String(error)}`);
        finish(error instanceof Error ? error : new Error(String(error)));
        this.retire(child, bridgeError('transport_error', 'WDC 子进程发生错误'));
      });

      const onExit = (code, signal) => {
        if (!this.exits.has(child)) return;
        if (!this.closed && this.child === child) this.options.onFailure?.('python_process', 'service_exited');
        const tail = this.stderrTail.slice(-5).join(' | ');
        this.log(`子进程退出 code=${code} signal=${signal}${tail ? `；stderr: ${tail}` : ''}`);
        exited();
        this.exits.delete(child);
        if (this.child === child) {
          this.child = null;
          this.starting = null;
          this.ready = false;
          this.needsFreshFrame = true;
          if(!this.closed)this.feedbackInvoke('setTransportAvailable',false);
        }
        this.rejectPending(child, bridgeError('service_exited', 'WDC 服务进程已退出'));
        for (const [id, source] of this.inDoubt) { if (source === child) this.inDoubt.delete(id); }
        this.refreshFeedbackControl();
        if (!this.closed && !this.activeCalls) this.runtimeState('dormant');
        finish(bridgeError('service_exited', 'WDC 服务进程在就绪前退出'));
      };
      child.on('exit', onExit);
      child.on('close', onExit); // Spawn errors may emit close without exit.
      child.stdin.on('error', () => this.rejectPending(child, bridgeError('transport_error', 'WDC 输入管道错误')));

      const rl = createInterface({ input: child.stdout });
      rl.on('line', (line) => this.handleLine(line, child));

      const errRl = createInterface({ input: child.stderr });
      errRl.on('line', (line) => {
        if (line.startsWith('WDC_POINTER ')) { this.handlePointer(line.slice(12), child); return; }
        this.stderrTail.push(line.slice(0, 300));
        if (this.stderrTail.length > 20) this.stderrTail.shift();
      });

      this.request('initialize', {
        protocolVersion: PROTOCOL_VERSION,
        capabilities: {},
        clientInfo: {name:pluginName, version:pluginVersion},
      }, this.options.initTimeoutMs)
        .then(async (result) => {
          if (this.closed || this.child !== child) throw bridgeError('service_exited', '握手进程已失效');
          this.notify('notifications/initialized', {});
          // 这段 instructions 是给模型的用法指导，不能丢——宿主没有系统提示注入点，
          // 后面会把它附到 screenshot 工具的描述里。
          this.instructions = typeof result?.instructions === 'string' ? result.instructions : '';
          const pythonVersion = versionLabel(result?.serverInfo?.version);
          if (this.options.expectedTools) {
            const actual = await this.listTools();
            for (const expected of this.options.expectedTools) {
              const found = actual.find(tool => tool.name === expected.name);
              const keys = schema => Object.keys(schema?.properties || {}).sort().join(',');
              const required = schema => [...(schema?.required || [])].sort().join(',');
              if (!found || keys(found.inputSchema) !== keys(expected.inputSchema) || required(found.inputSchema) !== required(expected.inputSchema)) {
                if (!this.closed && this.child === child) this.pythonHandshake = {version:pythonVersion, catalog:'mismatch'};
                throw bridgeError('catalog_mismatch', '服务工具定义与安装包不一致，请更新配套安装包。');
              }
            }
          }
          if (this.closed || this.child !== child) throw bridgeError('bridge_closed', '握手期间服务已关闭');
          this.pythonHandshake = {
            version:pythonVersion,
            catalog: this.options.expectedTools ? 'matched' : 'unchecked',
          };
          this.ready = true;
          this.log(`握手成功：${JSON.stringify(result?.serverInfo ?? {})}，instructions ${this.instructions.length} 字符`);
          this.feedbackInvoke('setTransportAvailable',true);
          this.refreshFeedbackControl();
          this.runtimeState(this.activeCalls ? 'active' : 'idle');
          finish();
        })
        .catch((error) => {
          if (!this.closed && this.child === child) this.options.onFailure?.('python_handshake', error.code || 'initialization_failed');
          this.log(`握手失败：${error instanceof Error ? error.message : String(error)}`);
          finish(error instanceof Error ? error : new Error(String(error)));
          this.retire(child, error);
        });
    });
    this.starting = starting;
    starting.catch(() => { if (this.starting === starting) this.starting = null; });
    return starting;
  }

  rejectPending(child, error) {
    for (const [id, entry] of this.pending) {
      if (entry.child !== child) continue;
      clearTimeout(entry.timer);
      this.pending.delete(id);
      if (entry.dispatched && entry.mutating) this.inDoubt.set(id, child);
      entry.reject(bridgeError(error.code || 'transport_error', error.message, entry.dispatched));
    }
  }

  retire(child, error) {
    if (!child) return;
    if (this.child === child) { this.child = null; this.starting = null; this.ready = false; this.needsFreshFrame = true; this.feedbackInvoke('setTransportAvailable',false); }
    this.rejectPending(child, error);
    try { child.stdin.end(); } catch { /* Closing only our own transport. */ }
    try { child.kill(); } catch { /* Restart waits for actual exit. */ }
  }

  handlePointer(line, child) {
    if(this.closed || child!==this.child || this.isStopped() || line.length>1024)return;
    try {
      const event=JSON.parse(line),entry=this.pending.get(Number(event.id));
      if(!entry || entry.child!==child || !entry.mutating || !Number.isSafeInteger(event.sequence) || event.sequence<=entry.pointerSequence)return;
      if(!Array.isArray(event.point)||event.point.length!==2||!event.point.every(v=>typeof v==='number'&&Number.isFinite(v)))return;
      entry.pointerSequence=event.sequence;
      this.feedbackInvoke('pointer',entry.feedbackToken,event.point);
    }catch{/* Malformed telemetry is ignored, never shown as raw diagnostics. */}
  }

  handleLine(line, child = this.child) {
    const text = line.trim();
    if (!text) return;
    let message;
    try {
      message = JSON.parse(text);
    } catch {
      return; // 非 JSON 行（日志混入 stdout）直接忽略
    }
    const id = message?.id;
    if (id === undefined || id === null) return; // 服务端通知
    const entry = this.pending.get(id);
    if (!entry) {
      // Late completion resolves uncertainty, but never clears the stop latch.
      if (this.inDoubt.get(id) === child && (message.error || Object.hasOwn(message, 'result'))) {
        this.inDoubt.delete(id);this.refreshFeedbackControl();
        if (this.closed && this.child === child && !this.inDoubt.size) {
          this.retire(child, bridgeError('bridge_closed', 'WDC 桥已关闭'));
        } else this.scheduleIdle();
      }
      return;
    }
    if (entry.child !== child) return;
    this.pending.delete(id);
    if (entry.timer) clearTimeout(entry.timer);
    if (message.error) {
      entry.reject(bridgeError('rpc_error', message.error.message ?? 'MCP 调用返回错误', true));
    } else {
      entry.resolve(message.result);
    }
  }

  request(method, params, timeoutMs, feedbackToken = null, lease = null) {
    return new Promise((resolve, reject) => {
      if (this.closed) {
        reject(bridgeError('bridge_closed', 'WDC 桥已关闭'));
        return;
      }
      try {lease?.check();}catch(error){reject(error);return;}
      const child = this.child;
      if (!child || !child.stdin.writable) {
        reject(bridgeError('service_unavailable', 'WDC 服务进程不可写'));
        return;
      }
      const id = this.nextId++;
      const timer = setTimeout(() => {
        this.pending.delete(id);
        if (entry.dispatched && entry.mutating) this.inDoubt.set(id, child);
        entry.reject(bridgeError('timeout', `MCP ${method} 超时（${timeoutMs}ms）`, entry.dispatched));
        if (method === 'tools/call' && RETRY_SAFE_TOOLS.has(params?.name) &&
            this.child === child && !this.activeMutations && !this.inDoubt.size) {
          this.retire(child, bridgeError('service_exited', 'WDC 读取超时，已回收无响应的服务'));
        }
      }, timeoutMs ?? this.options.callTimeoutMs);
      const entry = { resolve, reject, timer, child, feedbackToken, pointerSequence:0, dispatched: false,
        mutating: method === 'tools/call' && !RETRY_SAFE_TOOLS.has(params?.name) && !CONTROL_TOOLS.has(params?.name) };
      let abort;
      const clean=()=>lease?.signal.removeEventListener('abort',abort);
      entry.resolve=value=>{clean();resolve(value);};entry.reject=error=>{clean();reject(error);};
      abort=()=>{
        if(this.pending.get(id)!==entry)return;
        this.pending.delete(id);clearTimeout(timer);
        if(entry.dispatched&&entry.mutating)this.inDoubt.set(id,child);
        if(entry.dispatched&&entry.mutating)try{this.latchStop();}catch{}
        if(entry.dispatched&&params?.name==='wait'&&child.stdin.writable) {
          const notification={jsonrpc:'2.0',method:'notifications/cancelled',params:{requestId:id}};
          try { child.stdin.write(JSON.stringify(notification)+'\n',()=>{}); } catch {}
        }
        entry.reject(bridgeError('operation_cancelled','操作已取消；已发出的输入须核对结果。',entry.dispatched));
      };
      this.pending.set(id, entry);
      lease?.signal.addEventListener('abort',abort,{once:true});
      if(lease?.signal.aborted){abort();return;}
      try {
        const line = `${JSON.stringify({ jsonrpc: '2.0', id, method, params })}\n`;
        entry.dispatched = true;
        child.stdin.write(line, (error) => {
          if (!error || this.pending.get(id) !== entry) return;
          this.pending.delete(id); clearTimeout(timer);
          if (entry.mutating) this.inDoubt.set(id, child);
          entry.reject(bridgeError('transport_error', 'WDC 请求写入失败', true));
        });
      } catch (error) {
        this.pending.delete(id);
        clearTimeout(timer);
        if (entry.dispatched && entry.mutating) this.inDoubt.set(id, child);
        entry.reject(bridgeError(entry.dispatched ? 'transport_error' : 'bad_argument', error instanceof Error ? error.message : String(error), entry.dispatched));
      }
    });
  }

  notify(method, params) {
    const child = this.child;
    if (!child || !child.stdin.writable) return;
    try {
      child.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', method, params })}\n`);
    } catch {
      /* 通知失败不影响主流程 */
    }
  }

  async listTools() {
    const result = await this.request('tools/list', {}, this.options.initTimeoutMs);
    const tools = Array.isArray(result?.tools) ? result.tools : [];
    return tools.filter((tool) => tool && typeof tool.name === 'string' && tool.name !== '');
  }

  stopFile() {
    return this.options.stopFile || path.join(this.options.cwd, '.wdc-stop.flag');
  }

  isStopped() {
    if (this.stopped) return true;
    try { statSync(this.stopFile()); return true; }
    catch (error) { return error.code !== 'ENOENT'; }
  }

  latchStop() {
    this.stopped = true;
    this.controlVersion += 1;
    mkdirSync(path.dirname(this.stopFile()), { recursive: true });
    writeFileSync(this.stopFile(), 'stop\n', 'utf8');
    this.refreshFeedbackControl();
  }

  async callTool(toolName, args, allowRetry = true, lease = null) {
    this.cancelIdle();
    this.activeCalls += 1;
    this.runtimeState('active');
    this.refreshFeedbackControl();
    const blockedInput=!CONTROL_TOOLS.has(toolName)&&!RETRY_SAFE_TOOLS.has(toolName)&&(this.activeMutations>0||this.isStopped());
    const token=blockedInput?null:this.feedbackInvoke('begin',toolName);
    try {
      const result=await this.callToolCore(toolName,args,allowRetry,token,lease);
      this.refreshFeedbackControl();
      // Only whitelisted scalar output reaches the view. Never forward arguments/content.
      try{this.feedbackInvoke('complete',token,summarizeFeedbackResult(toolName,result));}
      catch{this.feedbackInvoke('fail',token,'feedback_error');}
      return result;
    }catch(error){
      this.refreshFeedbackControl();this.feedbackInvoke('fail',token,error.code);
      throw error;
    }finally{
      this.activeCalls -= 1;
      this.scheduleIdle();
      if (!this.closed && !this.activeCalls && !this.child) this.runtimeState('dormant');
    }
  }

  async callToolCore(toolName, args, allowRetry = true, feedbackToken = null, lease = null) {
    lease?.check();
    if (this.closed) throw bridgeError('bridge_closed', 'WDC 桥已关闭');
    if (toolName === 'stop') {
      try { this.latchStop(); }
      catch { throw bridgeError('stop_signal_failed', '已在插件侧禁止新输入，但停止文件写入失败，执行中的操作可能继续。'); }
      return { content: [{ type: 'text', text: JSON.stringify({ok:true, action:'stop', stopped:true,
        warning:'停止信号已设置；正在进行的系统调用只能在返回后的检查点停止。'}) }] };
    }
    if (toolName === 'resume') return this.resumeControl(args,lease);
    const mutating = !RETRY_SAFE_TOOLS.has(toolName);
    if (mutating && this.isStopped()) throw bridgeError('emergency_stop', '已停止；请先调用 resume。');
    if (mutating && this.activeMutations) throw bridgeError('action_busy', '前一个输入操作尚未结束。');
    if (mutating) this.activeMutations += 1;
    let inputRequestStarted = false;
    try {
      if (!this.ready || this.restarting) await this.ensureReady();
      lease?.check();
      // A stop arriving during cold start must win before any input is dispatched.
      if (mutating && this.isStopped()) throw bridgeError('emergency_stop', '启动期间已停止；未发送输入。');
      const usesFrame = args?.frame_id || (['click','move_mouse','drag','scroll'].includes(toolName) &&
        (args?.coordinate_mode ?? 'auto') !== 'screen');
      if (usesFrame && (this.needsFreshFrame || this.pendingImageDelivery)) {
        throw bridgeError('stale_frame', '需要已成功交付的截图才能使用图像坐标，请重新观察。');
      }
      inputRequestStarted = true;
      const result = await this.request('tools/call', { name: toolName, arguments: args ?? {} }, this.options.callTimeoutMs, feedbackToken, lease);
      if (mutating && toolFailureCode(result) === 'emergency_stop') {
        try { this.latchStop(); }
        catch { this.options.onFailure?.('tool_call','stop_signal_failed',toolName); }
        finally { this.options.onEmergencyStop?.(); }
      }
      if (IMAGE_TOOLS.has(toolName) && toolSucceeded(result)) this.needsFreshFrame = false;
      return result;
    } catch (error) {
      if (this.closed && !inputRequestStarted) throw bridgeError('bridge_closed', 'WDC 桥已关闭，未发送操作。');
      if (mutating && inputRequestStarted && error.dispatched) {
        try { this.latchStop(); } catch { /* Local latch still blocks new input. */ }
        throw bridgeError('outcome_unknown', '操作可能已部分或全部执行；已停止后续输入，禁止自动重试，请先核对结果。', true);
      }
      if (!mutating && allowRetry && !this.closed && ['service_exited','service_unavailable','transport_error'].includes(error.code)) {
        await this.restart();
        return this.callToolCore(toolName, args, false, feedbackToken,lease);
      }
      throw error;
    } finally {
      if (mutating) this.activeMutations -= 1;
      this.refreshFeedbackControl();
    }
  }

  async resumeControl(args,lease=null) {
    if (this.activeMutations || [...this.inDoubt.values()].some(child => this.exits.has(child))) {
      throw bridgeError('action_busy', '操作尚未结束或结果未知；请等待服务结束并核对结果后再恢复。');
    }
    const version = this.controlVersion;
    if (!this.ready || this.restarting) await this.ensureReady();
    const result = await this.request('tools/call', {name:'resume', arguments:args ?? {}}, this.options.callTimeoutMs,null,lease);
    lease?.check();
    if (!toolSucceeded(result)) return result;
    if (version !== this.controlVersion || this.closed) throw bridgeError('emergency_stop', '恢复期间再次收到停止请求。');
    try { unlinkSync(this.stopFile()); }
    catch (error) { if (error.code !== 'ENOENT') throw bridgeError('stop_signal_failed', '无法清除停止文件，输入保持禁用。'); }
    this.stopped = false;
    return {content:[{type:'text',text:JSON.stringify({ok:true,action:'resume',stopped:false})}]};
  }

  restart() {
    if (this.closed) return Promise.reject(bridgeError('bridge_closed', 'WDC 桥已关闭'));
    if (this.restarting) return this.restarting;
    if (this.activeMutations) return Promise.reject(bridgeError('action_busy', '输入操作尚未结束，禁止重启服务。'));
    if ([...this.inDoubt.values()].some(child => this.exits.has(child))) {
      return Promise.reject(bridgeError('action_busy', '仍有结果未知的输入操作，禁止强制重启服务。'));
    }
    const task = (async () => {
      if (this.waitForPreviousWorker) await this.waitForPreviousWorker();
      if (this.child) this.retire(this.child, bridgeError('service_exited', 'WDC 服务重启中'));
      const waits = [...this.exits.values()];
      if (waits.length) {
        let timer;
        try {
          await Promise.race([Promise.all(waits), new Promise((_,reject) => {
            timer = setTimeout(() => reject(bridgeError('service_busy', '旧 WDC 进程未退出，未启动新进程。')),2000);
          })]);
        } finally { clearTimeout(timer); }
      }
      return this.start();
    })();
    this.restarting = task;
    task.finally(() => { if (this.restarting === task) this.restarting = null; }).catch(() => {});
    return task;
  }

  close() {
    if (this.closed) return;
    this.closed = true;
    this.cancelIdle();
    this.feedbackInvoke('dispose');
    if (this.child && (this.activeMutations || this.inDoubt.size)) {
      try { this.latchStop(); } catch { /* Keep the in-memory stop even if disk is unavailable. */ }
      // Let Python unwind and release its held inputs before terminating it.
      const child = this.child;
      this.rejectPending(child, bridgeError('bridge_closed', 'WDC 桥已关闭'));
      this.closeAfterInput(child);
      return;
    }
    this.retire(this.child, bridgeError('bridge_closed', 'WDC 桥已关闭'));
  }

  closeAfterInput(child) {
    if (![...this.inDoubt.values()].includes(child)) {
      this.retire(child, bridgeError('bridge_closed', 'WDC 桥已关闭'));
      return;
    }
    const timeout = setTimeout(() => {
      if (this.child === child) this.retire(child, bridgeError('bridge_closed', 'WDC 桥已关闭'));
    }, 1000);
    child.once('close', () => clearTimeout(timeout));
    child.once('exit', () => clearTimeout(timeout));
  }
}

/** 把 MCP 返回的 content 数组转成 DSH 工具结果 */
async function toHostContent(result, attachments, expectedImageSize) {
  const items = Array.isArray(result?.content) ? result.content : [];
  const parts = [];
  for (const item of items) {
    if (!item || typeof item !== 'object') continue;
    if (item.type === 'text' && typeof item.text === 'string') {
      parts.push({ type: 'text', text: item.text });
      continue;
    }
    if (item.type === 'image') {
      let attachment;
      try {
        const data = Buffer.from(item.data, 'base64');
        attachment = await attachments.saveImage({data, mediaType: item.mimeType});
        if (typeof attachment?.attachmentId !== 'string' || !attachment.attachmentId ||
            !Number.isSafeInteger(attachment.width) || attachment.width <= 0 ||
            !Number.isSafeInteger(attachment.height) || attachment.height <= 0) throw new Error('Invalid image reference');
      } catch {
        throw bridgeError('image_delivery_failed', 'DSH 未能接收截图。请检查附件服务，或缩小截图区域后重试。');
      }
      const original = attachment.originalDimensions;
      if ((original && (original.width !== attachment.width || original.height !== attachment.height)) ||
          (expectedImageSize && (expectedImageSize[0] !== attachment.width || expectedImageSize[1] !== attachment.height))) {
        throw bridgeError('image_resized', 'DSH 缩放了截图，图像坐标已失效。请用较小的区域或 scale 重新截图。');
      }
      parts.push({type:'image', attachment});
      continue;
    }
    parts.push({ type: 'text', text: JSON.stringify(item) });
  }
  if (parts.length === 0) {
    parts.push({ type: 'text', text: result === undefined ? '(无返回)' : JSON.stringify(result) });
  }
  return parts;
}

async function convertToolResult(result, attachments, imageSize, lease, timeoutMs) {
  if (!imageSize) return toHostContent(result, attachments);
  lease?.check();
  let timer, abort;
  const interrupted = new Promise((_, reject) => {
    timer = setTimeout(() => reject(bridgeError('image_delivery_timeout', 'DSH 附件交付超时，请重新截图。')), timeoutMs);
    abort = () => reject(bridgeError('operation_cancelled', '截图交付已取消。'));
    lease?.signal.addEventListener('abort', abort, {once:true});
  });
  try {
    return await Promise.race([interrupted, toHostContent(result, attachments, imageSize)]);
  } finally {
    clearTimeout(timer);
    lease?.signal.removeEventListener('abort', abort);
  }
}

function screenshotSize(result) {
  for (const item of result.content) {
    if (item?.type !== 'text') continue;
    let data;
    try { data = JSON.parse(item.text); } catch { continue; }
    if (typeof data?.frame_id !== 'string' || !data.frame_id) continue;
    if (Array.isArray(data.image_size) && data.image_size.length === 2 &&
        data.image_size.every(value => Number.isSafeInteger(value) && value > 0) &&
        result.content.filter(part => part?.type === 'image').length === 1) return data.image_size;
  }
  throw bridgeError('invalid_screenshot_frame', '截图缺少可验证的图像或坐标帧，请重新观察。');
}

/** Tool result schema: loose object so the host does not over-validate */
const OUTPUT_SCHEMA = {
  type: 'object',
  properties: {
    ok: { type: 'boolean', description: 'Whether the action succeeded' },
    text: { type: 'string', description: 'Text form of the result' },
  },
  additionalProperties: true,
};

/** 把插件运行状态写到 ~/.dsh/wdc-plugin-status.json，便于宿主外排障 */
function writeStatus(statusPath, payload) {
  try {
    mkdirSync(path.dirname(statusPath), { recursive: true });
    writeFileSync(
      statusPath,
      JSON.stringify({ at: new Date().toISOString(), pid: process.pid, ...payload }, null, 2),
      'utf8',
    );
  } catch {
    /* 状态文件只是诊断用，写不了不影响功能 */
  }
}

export { McpBridge, toHostContent, buildDescription, annotateSchema, pruneSchema };

export function apply(ctx, config) {
  // 每次挂载独立记账：模块级共享会让插件卸载后重载时误判"已注册过"而全部跳过
  const manifest = [];
  const options = pickConfig(config);
  options.onFailure = recordFailure;
  const statusPath = options.statusPath;
  const log = (message) => {
    try {
      ctx?.logger?.warn?.(`wdc-computer-use: ${message}`);
    } catch {
      /* logger 不可用时静默 */
    }
  };

  let injectedPresets;
  const getPresets = () => injectedPresets ?? ctx.get?.('permissionPresets');
  const createPermissions = () => new SessionPermissions({
    mode:options.permissionMode,
    timeoutMs:options.approvalTimeoutMs,
    rememberApprovals:options.rememberSessionApprovals,
    getApproval:() => ctx.get?.('approval'),
    getPresets,
  });
  let permissions = createPermissions();
  options.onEmergencyStop = () => permissions.stopAll();
  function createBridge(previous) {
    const instance = new McpBridge(options, log);
    if (previous) instance.waitForPreviousWorker = async () => {
      await previous.waitForPreviousWorker?.();
      await previous.waitForExit();
      instance.waitForPreviousWorker = undefined;
    };
    instance.instructions = TOOL_CATALOG.instructions;
    return instance;
  }
  options.expectedTools = TOOL_CATALOG.tools.filter(tool =>
    !OPT_IN_TOOLS.has(tool.name) || options.enableTools.includes(tool.name));
  let bridge = createBridge();
  async function stopAll() {
    try { return await bridge.callTool('stop', {}); }
    finally { permissions.stopAll(); }
  }
  let statusPayload={ok:false,stage:'apply-called',note:'插件已加载，等待 tools 服务'};
  let statusClosed=false;
  function onDisplayStatus(status) {
    if (status.state === 'failed') recordFailure('desktop_feedback', status.reason);
    reportStatus({});
  }
  let nativeDisplay=new NativeFeedbackHost(options,bridge.feedback,log,onDisplayStatus);
  let displayClosing=Promise.resolve(true), displayStarting=null, executions=0;
  options.onRuntimeState = runtime => reportStatus({runtime});
  async function startDisplay() {
    if (displayStarting) return displayStarting;
    if (nativeDisplay.child || statusClosed || options.feedbackEnabled !== true || options.feedbackNativeEnabled !== true) return;
    const task = (async () => {
      const exited = await displayClosing;
      if (!exited || statusClosed || !executions) return;
      if (nativeDisplay.closed) nativeDisplay=new NativeFeedbackHost(options,bridge.feedback,log,onDisplayStatus);
      nativeDisplay.start();
      await nativeDisplay.readyWait;
    })();
    displayStarting=task;
    try { await task; } finally { if(displayStarting===task)displayStarting=null; }
  }
  function reportStatus(update){
    if(statusClosed)return;
    statusPayload={...statusPayload,...update};
    writeStatus(statusPath,{
      ...statusPayload,
      compatibility:{
        tools:typeof ctx.get?.('tools')?.register === 'function' || manifest.length > 0,
        attachments:typeof ctx.get?.('attachments')?.saveImage === 'function',
        approval:typeof ctx.get?.('approval')?.request === 'function',
      },
      versions:{
        nodePlugin:pluginVersion,
        pluginDirectory:path.resolve(import.meta.dirname, '..'),
        pythonService:bridge.pythonHandshake,
      },
      nativeFeedback:nativeDisplay.status(),
      permissionIntegration:{
        hostPresetsAvailable:typeof getPresets()?.current === 'function',
        directCommandsAvailable:typeof ctx.get?.('commands')?.register === 'function',
      },
    });
  }

  reportStatus({});
  function contextSupport() {
    return {
      lazyAdvancedTools:supportsLazyTools(ctx.get?.('tools')),
      compactHistory:supportsCompactHistory(ctx.get?.('sessions'),ctx.get?.('agents'),ctx.get?.('tokenMeter')),
    };
  }
  mountSettings(ctx,options,()=>statusPayload,()=>executions>0 || bridge.activeMutations>0 || bridge.inDoubt.size>0,contextSupport);
  if (options.compactHistory === true) {
    const lastPruned = new WeakMap();
    ctx.on?.('agent/pre-step', ({agent,turn,step,signal},next) => {
      if (step===1 && agent?.session && !signal?.aborted && contextSupport().compactHistory &&
          !bridge.activeMutations && !bridge.inDoubt.size && lastPruned.get(agent.session)!==turn) {
        try {
          const boundary = ctx.get('agents').turnBoundaryOf(agent.session);
          // The released replay validator accepts tool result replacements only in an open turn.
          if (boundary?.openTurnStartSeq != null && boundary.lastTurn === turn) {
            pruneObservations(agent.session,turn,ctx.get('tokenMeter'),signal);
            lastPruned.set(agent.session,turn);
          }
        } catch {
          recordFailure('tool_result','history_compaction_failed');
        }
      }
      return next();
    });
  }

  ctx.inject(['attachments'], (attachmentCtx) => {
    if (typeof attachmentCtx.attachments?.saveImage !== 'function') return;
    attachmentCtx.effect(() => {
      reportStatus({});
      return () => reportStatus({});
    });
  });

  // Optional host service. Tools may register before the preset service starts.
  ctx.inject(['permissionPresets'], (presetCtx) => {
    const service = presetCtx.permissionPresets;
    if (typeof service?.current !== 'function') return;
    presetCtx.effect(() => {
      injectedPresets = service;
      reportStatus({});
      return () => {
        if (injectedPresets === service) {
          injectedPresets = undefined;
          reportStatus({});
        }
      };
    });
  });

  ctx.on?.('session/event', (session, event) => permissions.onSessionEvent(session, event));
  ctx.on?.('tools/pre-execute', (exec, next) => {
    if (manifest.some(entry => entry.name === exec.name && entry.source !== 'stop')) {
      permissions.preflight(exec);
    }
    return next();
  }, {prepend:true});

  // DSH sends slash commands through its direct UI command service.
  ctx.inject(['commands'], (commandCtx) => {
    commandCtx.commands?.register({
      definitionId:'computeruse-dsh/access',
      name:'wdc-access',
      description:'设置或撤销本会话的电脑操作权限，也可紧急停止',
      input:{hint:'ask | read-only | disabled | revoke | stop'},
      async handler(invocation) {
        if (invocation.rawInput.trim() !== 'stop') return permissions.command(invocation);
        if (!invocation.agent?.session || invocation.signal?.aborted) {
          return {kind:'error', text:'电脑操作权限命令需要有效的 DSH 会话。'};
        }
        try {
          await stopAll();
          return {kind:'success', text:'已停止电脑操作并撤销所有会话的插件授权。'};
        } catch {
          recordFailure('tool_call', 'stop_signal_failed', 'stop');
          return {kind:'error', text:'停止信号写入失败，插件已阻止后续输入。'};
        }
      },
    });
  });

  ctx.inject(['tools'], (toolsCtx) => {
    const tools = toolsCtx?.tools;
    if (!tools || typeof tools.register !== 'function') {
      recordFailure('registration', 'tools_unavailable');
      log('宿主未提供 tools 服务，插件不挂载任何工具');
      reportStatus({ ok: false, stage: 'tools-unavailable', error: '宿主未提供 tools 服务' });
      return;
    }

    if (statusClosed) {
      permissions = createPermissions();
      bridge = createBridge(bridge);
      nativeDisplay = new NativeFeedbackHost(options,bridge.feedback,log,onDisplayStatus);
      statusClosed = false;
    }
    const currentBridge = bridge;
    const currentPermissions = permissions;
    let disposed = false;
    const disposers = [];

    Promise.resolve(TOOL_CATALOG.tools)
      .then((list) => {
          if (disposed) return;
        const hidden = new Set(Array.isArray(options.hideTools) ? options.hideTools : []);
        const profileName = String(options.profile || 'standard').toLowerCase();
        const allowed = Object.prototype.hasOwnProperty.call(TOOL_PROFILES, profileName)
          ? TOOL_PROFILES[profileName]
          : TOOL_PROFILES.standard;
        const allowSet = allowed ? new Set(allowed) : null;
        const failedTools = [];
        const selected = list.filter(({name}) => {
          const enabled = options.enableTools.includes(name);
          return CONTROL_TOOLS.has(name) || (!hidden.has(name) &&
            (OPT_IN_TOOLS.has(name) ? enabled : (enabled || !allowSet || allowSet.has(name))));
        });
        const guidanceTool = selected.find(tool => tool.name === 'screenshot') ??
          selected.find(tool => !CONTROL_TOOLS.has(tool.name));
        const lazy = options.lazyAdvancedTools === true && supportsLazyTools(tools)
          ? createLazyTools(selected,options) : undefined;
        const skipped = list.length - selected.length;
        for (const tool of selected) {
          const hostName = `${options.toolPrefix}${tool.name}`;
          if (manifest.some((entry) => entry.name === hostName)) continue;
          const rawSchema = tool.inputSchema && typeof tool.inputSchema === 'object'
            ? tool.inputSchema
            : { type: 'object', properties: {}, additionalProperties: true };
          try {
            const dispose = tools.register({
              name: hostName,
              description: buildDescription(
                tool.name,
                tool.description,
                tool === guidanceTool ? bridge.instructions : '',
              ),
              parameters: annotateSchema(
                tool.name,
                options.pruneSchema ? pruneSchema(rawSchema) : rawSchema,
              ),
              timeoutMs: options.approvalTimeoutMs+2*(options.initTimeoutMs+options.callTimeoutMs)+5000,
              output: {
                schema: OUTPUT_SCHEMA,
                presentationMeta(args,value) {
                  const key = options.compactHistory === true ? queryKey(tool.name,args,value) : undefined;
                  const verified = options.compactHistory === true ? resultVerified(tool.name,value) : undefined;
                  return {wdc:{version:2,tool:tool.name,imageIds:(value?.images ?? []).map(item => item.attachment.attachmentId),
                    ...(key===undefined ? {} : {queryKey:key}),...(verified===undefined ? {} : {verified})}};
                },
                render(_args, value) {
                  const text = value && typeof value === 'object' && typeof value.text === 'string'
                    ? value.text
                    : JSON.stringify(value ?? null);
                  return [{ type: 'text', text }, ...(value?.images ?? [])];
                },
              },
              async execute(args,exec) {
                let input = args && typeof args === 'object' ? args : {};
                if (tool.name === 'wait' && input.condition?.kind === 'control_enabled' &&
                    (!options.enableTools.includes('inspect_controls') || options.hideTools.includes('inspect_controls'))) {
                  return {ok:false,text:JSON.stringify({ok:false,error_code:'tool_disabled',
                    error:'Enable UI Automation inspection to wait for a control.'})};
                }
                if (IMAGE_TOOLS.has(tool.name) && input.scale === undefined && options.screenshotScale) {
                  input = {...input,scale:options.screenshotScale};
                }
                let lease;
                try {
                  if (disposed) throw bridgeError('bridge_closed', 'WDC 桥已关闭');
                  if (tool.name !== 'stop') lease = await currentPermissions.acquire(tool.name, exec, hostName);
                } catch (error) {
                  const code = error.code || 'approval_error';
                  recordFailure('approval', code, tool.name);
                  const message = /^(permission_|approval_unavailable$|operation_cancelled$)/.test(code)
                    ? error.message : '电脑操作未获授权或已取消';
                  return {ok:false,text:JSON.stringify({ok:false,error_code:code,error:message})};
                }
                executions += 1;
                let ownsImageDelivery = false;
                try {
                // Stop is local and immediate, even while Python/display is starting.
                const attachments = toolsCtx.get?.('attachments') ?? ctx.get?.('attachments');
                if (IMAGE_TOOLS.has(tool.name) && typeof attachments?.saveImage !== 'function') {
                  recordFailure('attachment', 'image_delivery_unavailable', tool.name);
                  return {ok:false,text:JSON.stringify({ok:false,error_code:'image_delivery_unavailable',
                    error:'此 DSH 未提供原生附件服务，暂无法交付截图。'})};
                }
                if (IMAGE_TOOLS.has(tool.name)) {
                  if (currentBridge.pendingImageDelivery) {
                    return {ok:false,text:JSON.stringify({ok:false,error_code:'action_busy',error:'上一张截图尚未完成交付。'})};
                  }
                  ownsImageDelivery = currentBridge.pendingImageDelivery = true;
                  currentBridge.needsFreshFrame = true;
                }
                if (tool.name !== 'stop') await startDisplay();
                let result;
                try {
                  lease?.check();
                  result = tool.name === 'stop' ? await stopAll() : await currentBridge.callTool(tool.name, input,true,lease);
                }
                catch (error) {
                  recordFailure('tool_call', error.code || 'bridge_error', tool.name);
                  return {ok:false,text:JSON.stringify({ok:false,action:tool.name,error_code:error.code || 'bridge_error',error:error.message})};
                }
                let content;
                const failureCode = toolFailureCode(result);
                try {
                  const imageSize = ownsImageDelivery && failureCode === undefined ? screenshotSize(result) : undefined;
                  const convertible = failureCode === undefined ? result : {
                    content:Array.isArray(result?.content) ? result.content.filter(item => item?.type === 'text') : [],
                  };
                  content = await convertToolResult(convertible, attachments, imageSize, lease, options.callTimeoutMs);
                  lease?.check();
                }
                catch (error) {
                  if (IMAGE_TOOLS.has(tool.name)) currentBridge.needsFreshFrame = true;
                  recordFailure('tool_result', error.code || 'result_conversion_failed', tool.name);
                  return {ok:false,text:JSON.stringify({ok:false,action:tool.name,error_code:error.code || 'result_conversion_failed',error:error.message})};
                }
                const text = content
                  .filter(item => item.type === 'text')
                  .map(item => item.text)
                  .join('\n');
                if (failureCode !== undefined) recordFailure('tool_result', failureCode, tool.name);
                return {ok:failureCode === undefined, text, images:content.filter(item => item.type === 'image')};
                } finally {
                  if (ownsImageDelivery) currentBridge.pendingImageDelivery = false;
                  lease?.release();
                  executions -= 1;
                  if (!executions && !disposed) displayClosing=nativeDisplay.close();
                }
              },
            }, lazy?.condition(tool.name));
            if (typeof dispose === 'function') disposers.push(dispose);
            manifest.push({ name: hostName, source: tool.name });
          } catch (error) {
            recordFailure('registration', 'registration_failed', tool.name);
            failedTools.push(hostName);
            log(`注册工具 ${hostName} 失败：${error instanceof Error ? error.message : String(error)}`);
          }
        }
        if (lazy?.allowed.length) {
          const definition = lazy.definition();
          try {
            const dispose = tools.register(definition);
            if (typeof dispose === 'function') disposers.push(dispose);
            manifest.push({name:definition.name,source:'load_tools'});
          } catch {
            failedTools.push(definition.name);
            lazy.useFixedTools();
            recordFailure('registration','registration_failed');
          }
        }
        log(`已注册 ${manifest.length} 个工具${skipped ? `（另有 ${skipped} 个按配置隐藏）` : ''}`);
        reportStatus({
          ok: failedTools.length === 0,
          stage: failedTools.length === 0 ? 'registered' : 'registration-incomplete',
          note: '工具已注册；服务按调用启动，空闲时静默并回收',
          runtime: {state:'dormant',activeCalls:0,stopped:bridge.isStopped()},
          registered: manifest.length,
          failedTools,
          profile: profileName,
          tools: manifest.map((entry) => entry.name),
          instructionsChars: bridge.instructions.length,
          python: options.python,
          cwd: options.cwd,
        });
      })
      .catch((error) => {
        if(disposed)return;
        recordFailure('registration', 'initialization_failed');
        nativeDisplay.close();
        const message = error instanceof Error ? error.message : String(error);
        log(`初始化未完成，本次不提供桌面工具：${message}`);
        reportStatus({ ok: false, error: message, python: options.python, cwd: options.cwd });
      });

    toolsCtx.effect(() => () => {
      disposed = true;
      currentPermissions.close();
      for (const dispose of disposers.splice(0)) {
        try {
          dispose();
        } catch {
          /* ignore */
        }
      }
      manifest.length = 0;
      displayClosing = nativeDisplay.close();
      reportStatus({ok:false,stage:'disposed',registered:0,tools:[]});statusClosed=true;
      currentBridge.close();
    });
  });
  // Cordis requires an effect disposer, not an arbitrary service object.
  const disposePermissions=()=>permissions.close();
  disposePermissions.permissions=permissions; // Host/test handle, never a model tool.
  return disposePermissions;
}
