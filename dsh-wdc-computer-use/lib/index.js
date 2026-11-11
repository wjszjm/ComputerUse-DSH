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
 *   - 截图默认落盘为 PNG 并返回路径（DSH 工具结果的图片直通未经验证，避免冒险）
 *   - 子进程随插件卸载而关闭
 */

import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { mkdirSync, readdirSync, statSync, unlinkSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import {FeedbackSession,summarizeFeedbackResult} from './feedback-session.js';
import {NativeFeedbackHost} from './native-feedback-host.js';
import {SessionPermissions} from './permissions.js';
import {TOOL_CATALOG} from './tool-catalog.js';

export const name = 'wdc-computer-use';
export const inject = [];

/** 默认不暴露的工具：run_command 需要服务端开 allow_shell，暴露它只是占上下文 */
const DEFAULT_HIDDEN = ['run_command'];
const CONTROL_TOOLS = new Set(['stop', 'resume']);
const RETRY_SAFE_TOOLS = new Set(['screen_info', 'screenshot', 'list_windows', 'clipboard_get', 'wait']);

function bridgeError(code, message, dispatched = false) {
  return Object.assign(new Error(message), { code, dispatched });
}

function toolSucceeded(result) {
  if (!result || result.isError === true || !Array.isArray(result.content) || !result.content.length) return false;
  for (const item of result.content) {
    if (item?.type !== 'text') continue;
    try { if (JSON.parse(item.text)?.ok === false) return false; } catch { /* Ordinary text is allowed. */ }
  }
  return true;
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
  /** 截图落盘目录，留空则用系统临时目录下的 wdc-shots */
  shotDir: '',
  /** 兼容旧配置；DSH 图片直通未验证，当前统一返回可读取的图片路径。 */
  passImageContent: false,
  /** 不注册的工具名（服务端工具名，非 wdc_ 前缀名），用于给上下文减负 */
  hideTools: DEFAULT_HIDDEN,
  /** 是否精简 schema（去 title / 空默认值 / 截断超长描述） */
  pruneSchema: true,
  /** 工具档位：minimal | standard | full，决定注册哪些工具 */
  profile: 'standard',
};

const PROTOCOL_VERSION = '2024-11-05';

/**
 * 清理截图目录里的历史文件。
 *
 * 每张截图都会落盘，长任务一轮七八张，不清理会累积成上百 MB 的垃圾。
 * 只删本插件自己写的 shot-*.png/jpg，不碰目录里的其他东西。
 */
function cleanOldShots(dir, maxAgeMs = 24 * 60 * 60 * 1000) {
  let removed = 0;
  try {
    const now = Date.now();
    for (const name of readdirSync(dir)) {
      if (!/^shot-\d+-\d+\.(png|jpg|jpeg|webp)$/i.test(name)) continue;
      const file = path.join(dir, name);
      try {
        if (now - statSync(file).mtimeMs > maxAgeMs) {
          unlinkSync(file);
          removed += 1;
        }
      } catch {
        /* 单个文件删不掉不影响其余 */
      }
    }
  } catch {
    /* 目录不存在或不可读，忽略 */
  }
  return removed;
}

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

/**
 * Per-tool "when to use" hints.
 *
 * Written in English on purpose: these strings ship on every single turn, and
 * the same meaning costs roughly 1.5–2x more tokens in Chinese. The model reads
 * English fine, so the only thing that changes is the bill.
 *
 * What they add is the TRIGGER (when to reach for the tool), not the function —
 * the raw docstring already says what the tool does.
 */
const SCENE_HINTS = {
  screen_info: 'Use to check resolution, DPI scale, or permission state; also for diagnosing coordinate mismatches.',
  screenshot: 'Use whenever you need to SEE the screen: report what is on it, locate a target, or verify the previous action took effect. Always the first step before acting.',
  click: 'Screenshot first, then pass image pixels with its frame_id. Invalid/stale image targeting is rejected; take a new screenshot rather than bypassing with screen mode. Input completion does not prove the target responded: verify before continuing or retrying.',
  move_mouse: 'Use to hover (reveal tooltips or menus) or reposition the pointer without clicking.',
  drag: 'Use to drag files, select text, or move sliders and windows.',
  scroll: 'Use to scroll a page, list, or document. Positive = up/right, negative = down/left.',
  type_text: 'Use to type into a field, especially CJK or long text. Pass a fresh frame_id to check window context. Verify the destination using a fresh screenshot or target readback; clipboard contents do not prove the field accepted the text.',
  press_key: 'Use for a single key or chord, e.g. enter, esc, f5, ctrl+s, alt+tab. A completed input call is not proof of application response; verify the target before retrying.',
  clipboard_get: 'Use to read what is currently on the clipboard.',
  clipboard_set: 'Use to put text on the clipboard for a later paste or for another program.',
  list_windows: 'Use to enumerate open windows, or to find the handle of a specific program window.',
  activate_window: 'Use to bring a window to the foreground. Prefer handle plus expected_pid from list_windows; ambiguous title matches are rejected. Take a fresh screenshot after activation.',
  window_action: 'Use to maximize, minimize, restore, close, move, or resize a window. Prefer handle plus expected_pid. The requested window state is read back; a failed verification requires observation, not blind replay.',
  launch_app: 'Use to open a program, document, or URL, e.g. notepad, calc, a browser.',
  run_command: 'Use for shell commands when desktop automation cannot do the job. Disabled by default; needs allow_shell.',
  wait: 'Use while an interface is loading. Prefer this over taking repeated screenshots in a loop.',
  stop: 'Use to halt mouse and keyboard control immediately, e.g. when the action is going the wrong way.',
  resume: 'Use to restore control after a previous stop.',
};

/**
 * 关键参数的取值说明。
 *
 * 原始 docstring 整段透传太贵（中文），但完全不给说明又会伤精度——
 * 例如 coordinate_mode 有四个取值，不说明的话模型很容易把屏幕坐标当图片坐标传。
 * 折中：只给「选错就会点偏」的参数补一句英文说明，其余交给字段名。
 */
const PARAM_HINTS = {
  click: {
    coordinate_mode: 'auto (image pixels when a frame exists) | image (strict) | screen (explicit absolute pixels) | normalized (0-1); stale/out-of-image targets are rejected',
    frame_id: 'frame_id from the screenshot used for targeting; a new screenshot is required after input',
    button: 'left | right | middle',
    clicks: '1 = single, 2 = double',
    modifiers: 'keys held during the click, e.g. "ctrl", "shift+ctrl"',
  },
  drag: {
    coordinate_mode: 'auto (default, image pixels) | image | screen | normalized (0-1)',
    frame_id: 'bind both endpoints to the same screenshot frame_id',
  },
  scroll: {
    amount: 'wheel clicks; positive = up/right, negative = down/left',
    horizontal: 'true to scroll horizontally',
  },
  type_text: {
    use_clipboard: 'always (default, clipboard paste, exact) | auto | never (types key by key; IME may rewrite punctuation)',
    press_enter: 'press Enter after typing',
  },
  press_key: {
    keys: 'single key or chord, e.g. "enter", "ctrl+shift+s", "alt+tab"',
    repeat: 'how many times to repeat (1-50)',
    hold_ms: 'hold the keys for this many ms before releasing',
  },
  window_action: {
    action: 'maximize | minimize | restore | close | focus | topmost | untopmost | move | resize',
    title: 'window title; multiple matches at the best rank are rejected',
    handle: 'window handle (HWND), preferred over title',
    expected_pid: 'PID returned with the handle by list_windows',
    timeout: 'window-state verification timeout, 0-5 seconds; default 2',
  },
  launch_app: {
    path: 'executable, document, or URL',
    args: 'command-line arguments as a single string',
    wait: 'seconds to wait after launch',
  },
  screenshot: {
    monitor: '"primary" (default) | "all" | index 0..N',
    region: 'crop to [x, y, width, height] in screen coordinates',
    scale: '0 = auto (fit max_image_edge), 1 = original size',
    grid: 'overlay coordinate grid; turn on when you need to click precisely',
  },
};

/** 把参数说明注入精简后的 schema（只在缺 description 时补，不覆盖服务端已有的） */
function annotateSchema(toolName, schema) {
  const hints = {frame_id:'frame_id from a fresh screenshot; binds context and expires after input',
    expected_pid:'target PID from list_windows; use together with its handle',...PARAM_HINTS[toolName]};
  if (!schema || typeof schema !== 'object') return schema;
  const props = schema.properties;
  if (!props || typeof props !== 'object') return schema;
  for (const [key, text] of Object.entries(hints)) {
    const prop = props[key];
    if (prop && typeof prop === 'object' && !prop.description) {
      prop.description = text;
    }
  }
  return schema;
}

/** Assemble the final description: trigger + original docstring (+ loop rules for screenshot) */
function buildDescription(toolName, rawDescription, instructions) {
  const parts = [];
  const hint = SCENE_HINTS[toolName];
  if (hint) parts.push(`[When] ${hint}`);

  // 原始 docstring 不再透传：Args: 段与 parameters schema 重复，功能概述又与
  // [When] 重叠，保留只会让每轮多付一份 token。参数含义由 schema 字段名承担。

  if (toolName === 'screenshot') {
    if (instructions) parts.push(`[Safety and usage]\n${instructions}`);
    parts.push(
      '[Loop] screenshot → locate target in the image → click / type_text → screenshot again to confirm. '
      + 'click x/y are interpreted as image pixels of the latest screenshot and converted server-side; never multiply by the scale yourself. '
      + 'Pass the returned frame_id to bind the observation. Input invalidates the old frame. verification.status=not_verified means the target outcome still needs observation. '
      + 'To save tokens, crop with region when you only need part of the screen, and use wdc_wait instead of repeated screenshots.',
    );
  }
  return parts.join('\n\n');
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
    this.activeCalls = 0;
    this.idleTimer = null;
    this.needsFreshFrame = false;
    this.exits = new Map();
    this.activeMutations = 0;
    this.stopped = false;
    this.controlVersion = 0;
    this.inDoubt = new Map();
    this.closed = false;
    this.shotIndex = 0;
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
    const task = (async () => { await this.waitForExit(); return this.start(); })();
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
          env: { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8', WDC_STOP_FILE: this.stopFile(), WDC_FEEDBACK_EVENTS: this.options.feedbackEnabled === true ? '1' : '0' },
        });
      } catch (error) {
        finish(error instanceof Error ? error : new Error(String(error)));
        return;
      }

      this.child = child;
      let exited;
      this.exits.set(child, new Promise(resolveExit => { exited = resolveExit; }));

      child.on('error', (error) => {
        this.log(`子进程启动失败：${error instanceof Error ? error.message : String(error)}`);
        finish(error instanceof Error ? error : new Error(String(error)));
        this.retire(child, bridgeError('transport_error', 'WDC 子进程发生错误'));
      });

      const onExit = (code, signal) => {
        if (!this.exits.has(child)) return;
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
        clientInfo: { name: 'dsh-wdc-computer-use', version: '1.0.0' },
      }, this.options.initTimeoutMs)
        .then(async (result) => {
          if (this.closed || this.child !== child) throw bridgeError('service_exited', '握手进程已失效');
          this.notify('notifications/initialized', {});
          // 这段 instructions 是给模型的用法指导，不能丢——宿主没有系统提示注入点，
          // 后面会把它附到 screenshot 工具的描述里。
          this.instructions = typeof result?.instructions === 'string' ? result.instructions : '';
          if (this.options.expectedTools) {
            const actual = await this.listTools();
            for (const expected of this.options.expectedTools) {
              const found = actual.find(tool => tool.name === expected.name);
              const keys = schema => Object.keys(schema?.properties || {}).sort().join(',');
              const required = schema => [...(schema?.required || [])].sort().join(',');
              if (!found || keys(found.inputSchema) !== keys(expected.inputSchema) || required(found.inputSchema) !== required(expected.inputSchema)) {
                throw bridgeError('catalog_mismatch', '服务工具定义与安装包不一致，请更新配套安装包。');
              }
            }
          }
          if (this.closed || this.child !== child) throw bridgeError('bridge_closed', '握手期间服务已关闭');
          this.ready = true;
          this.log(`握手成功：${JSON.stringify(result?.serverInfo ?? {})}，instructions ${this.instructions.length} 字符`);
          const removed = cleanOldShots(this.shotDirectory());
          if (removed > 0) this.log(`清理 24 小时前的历史截图 ${removed} 张`);
          this.feedbackInvoke('setTransportAvailable',true);
          this.refreshFeedbackControl();
          this.runtimeState(this.activeCalls ? 'active' : 'idle');
          finish();
        })
        .catch((error) => {
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
        this.scheduleIdle();
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
        entry.reject(bridgeError('timeout', `MCP ${method} 超时（${timeoutMs}ms）`, true));
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
        if(method==='tools/call'&&!RETRY_SAFE_TOOLS.has(params?.name))try{this.latchStop();}catch{}
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
      if (this.needsFreshFrame && ['click','move_mouse','drag','scroll'].includes(toolName) && (args?.coordinate_mode ?? 'auto') !== 'screen') {
        throw bridgeError('stale_frame', '服务已重新启动，请先重新截图，不能复用旧图坐标。');
      }
      inputRequestStarted = true;
      const result = await this.request('tools/call', { name: toolName, arguments: args ?? {} }, this.options.callTimeoutMs, feedbackToken, lease);
      if (toolName === 'screenshot' && toolSucceeded(result)) this.needsFreshFrame = false;
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

  /** 截图落盘目录：优先用配置值，未配置则放在项目目录下的 shots/，方便查看与清理 */
  shotDirectory() {
    if (this.options.shotDir) return this.options.shotDir;
    return path.join(this.options.cwd, 'shots');
  }

  /** 把 MCP 的 image content 落盘，返回文件绝对路径 */
  saveImage(item) {
    const base64 = typeof item?.data === 'string' ? item.data : '';
    if (!base64) return null;
    const dir = this.shotDirectory();
    try {
      mkdirSync(dir, { recursive: true });
    } catch {
      return null;
    }
    const mime = typeof item?.mimeType === 'string' ? item.mimeType : 'image/png';
    const ext = mime.includes('jpeg') ? '.jpg' : mime.includes('webp') ? '.webp' : '.png';
    this.shotIndex += 1;
    const file = path.join(dir, `shot-${Date.now()}-${this.shotIndex}${ext}`);
    try {
      writeFileSync(file, Buffer.from(base64, 'base64'));
      return file;
    } catch {
      return null;
    }
  }

  close() {
    this.closed = true;
    this.cancelIdle();
    this.feedbackInvoke('dispose');
    this.retire(this.child, bridgeError('bridge_closed', 'WDC 桥已关闭'));
  }
}

/** 把 MCP 返回的 content 数组转成 DSH 工具结果 */
function toHostContent(result, bridge) {
  const items = Array.isArray(result?.content) ? result.content : [];
  const parts = [];
  for (const item of items) {
    if (!item || typeof item !== 'object') continue;
    if (item.type === 'text' && typeof item.text === 'string') {
      parts.push({ type: 'text', text: item.text });
      continue;
    }
    if (item.type === 'image') {
      // DSH currently renders text-only tool output. Always supply a file reference
      // until its native image contract has been validated end to end.
      const file = bridge.saveImage(item);
      if (!file) throw bridgeError('image_delivery_failed', '截图未能交付给 DSH；请检查截图目录权限后重新观察。');
      parts.push({
        type: 'text',
        wdcScreenshotPath: file,
        text: file
          ? `[截图已保存] ${file}\n（用文件读取工具打开该 PNG 查看；坐标按图片像素给出，服务端会自动换算成屏幕坐标）`
          : '[截图已生成，但落盘失败；请检查临时目录权限]',
      });
      continue;
    }
    parts.push({ type: 'text', text: JSON.stringify(item) });
  }
  if (parts.length === 0) {
    parts.push({ type: 'text', text: result === undefined ? '(无返回)' : JSON.stringify(result) });
  }
  return parts;
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
  const permissions = new SessionPermissions({
    mode:options.permissionMode,
    timeoutMs:options.approvalTimeoutMs,
    rememberApprovals:options.rememberSessionApprovals,
    getApproval:() => ctx.get?.('approval'),
    getPresets,
  });
  const bridge = new McpBridge(options, log);
  bridge.instructions = TOOL_CATALOG.instructions;
  options.expectedTools = TOOL_CATALOG.tools;
  let statusPayload={ok:false,stage:'apply-called',note:'插件已加载，等待 tools 服务'};
  let statusClosed=false;
  let nativeDisplay=new NativeFeedbackHost(options,bridge.feedback,log,()=>reportStatus({}));
  let displayClosing=Promise.resolve(true), displayStarting=null, executions=0;
  options.onRuntimeState = runtime => reportStatus({runtime});
  async function startDisplay() {
    if (displayStarting) return displayStarting;
    if (nativeDisplay.child || statusClosed || options.feedbackEnabled !== true || options.feedbackNativeEnabled !== true) return;
    const task = (async () => {
      const exited = await displayClosing;
      if (!exited || statusClosed || !executions) return;
      if (nativeDisplay.closed) nativeDisplay=new NativeFeedbackHost(options,bridge.feedback,log,()=>reportStatus({}));
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
      nativeFeedback:nativeDisplay.status(),
      permissionIntegration:{
        hostPresetsAvailable:typeof getPresets()?.current === 'function',
        directCommandsAvailable:typeof ctx.get?.('commands')?.register === 'function',
      },
    });
  }

  reportStatus({});

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
        permissions.revoke(invocation.agent.session);
        try {
          await bridge.callTool('stop', {});
          return {kind:'success', text:'已停止电脑操作并撤销本会话的插件授权。'};
        } catch {
          return {kind:'error', text:'停止信号写入失败，插件已阻止后续输入。'};
        }
      },
    });
  });

  ctx.inject(['tools'], (toolsCtx) => {
    const tools = toolsCtx?.tools;
    if (!tools || typeof tools.register !== 'function') {
      log('宿主未提供 tools 服务，插件不挂载任何工具');
      reportStatus({ ok: false, stage: 'tools-unavailable', error: '宿主未提供 tools 服务' });
      return;
    }

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
        let skipped = 0;
        for (const tool of list) {
          if (!CONTROL_TOOLS.has(tool.name) && (hidden.has(tool.name) || (allowSet && !allowSet.has(tool.name)))) {
            skipped += 1;
            continue;
          }
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
                typeof tool.description === 'string' && tool.description
                  ? tool.description
                  : `Win Desktop Control 工具：${tool.name}`,
                bridge.instructions,
              ),
              parameters: annotateSchema(
                tool.name,
                options.pruneSchema ? pruneSchema(rawSchema) : rawSchema,
              ),
              timeoutMs: options.approvalTimeoutMs+2*(options.initTimeoutMs+options.callTimeoutMs)+5000,
              output: {
                schema: OUTPUT_SCHEMA,
                presentationMeta(_args,value) {
                  return {wdc:{version:1,tool:tool.name,screenshots:value?.wdcScreenshots ?? []}};
                },
                render(_args, value) {
                  const text = value && typeof value === 'object' && typeof value.text === 'string'
                    ? value.text
                    : JSON.stringify(value ?? null);
                  return [{ type: 'text', text }];
                },
              },
              async execute(args,exec) {
                const input = args && typeof args === 'object' ? args : {};
                let lease;
                try {
                  if (tool.name === 'stop') permissions.revoke(exec?.agent?.session);
                  else lease = await permissions.acquire(tool.name, exec, hostName);
                } catch (error) {
                  const code = error.code || 'approval_error';
                  const message = /^(permission_|approval_unavailable$|operation_cancelled$)/.test(code)
                    ? error.message : '电脑操作未获授权或已取消';
                  return {ok:false,text:JSON.stringify({ok:false,error_code:code,error:message})};
                }
                executions += 1;
                try {
                // Stop is local and immediate, even while Python/display is starting.
                if (tool.name !== 'stop') await startDisplay();
                let result;
                try { lease?.check();result = await bridge.callTool(tool.name, input,true,lease); }
                catch (error) {
                  return {ok:false,text:JSON.stringify({ok:false,action:tool.name,error_code:error.code || 'bridge_error',error:error.message})};
                }
                let content;
                try { content = toHostContent(result, bridge); }
                catch (error) {
                  return {ok:false,text:JSON.stringify({ok:false,action:tool.name,error_code:error.code || 'result_conversion_failed',error:error.message})};
                }
                const text = content
                  .map((item) => (item.type === 'text' ? item.text : `[图片 ${item.mimeType}]`))
                  .join('\n');
                return { ok: toolSucceeded(result), text, wdcScreenshots:content.flatMap(item=>item.wdcScreenshotPath?[item.wdcScreenshotPath]:[]) };
                } finally {
                  lease?.release();
                  executions -= 1;
                  if (!executions) displayClosing=nativeDisplay.close();
                }
              },
            });
            if (typeof dispose === 'function') disposers.push(dispose);
            manifest.push({ name: hostName, source: tool.name });
          } catch (error) {
            log(`注册工具 ${hostName} 失败：${error instanceof Error ? error.message : String(error)}`);
          }
        }
        log(`已注册 ${disposers.length} 个工具${skipped ? `（另有 ${skipped} 个按配置隐藏）` : ''}`);
        reportStatus({
          ok: true,
          stage: 'registered',
          note: '工具已注册；服务按调用启动，空闲时静默并回收',
          runtime: {state:'dormant',activeCalls:0,stopped:bridge.isStopped()},
          registered: disposers.length,
          profile: profileName,
          tools: manifest.map((entry) => entry.name),
          instructionsChars: bridge.instructions.length,
          python: options.python,
          cwd: options.cwd,
        });
      })
      .catch((error) => {
        if(disposed)return;
        nativeDisplay.close();
        const message = error instanceof Error ? error.message : String(error);
        log(`初始化未完成，本次不提供桌面工具：${message}`);
        reportStatus({ ok: false, error: message, python: options.python, cwd: options.cwd });
      });

    toolsCtx.effect(() => () => {
      disposed = true;
      permissions.close();
      for (const dispose of disposers.splice(0)) {
        try {
          dispose();
        } catch {
          /* ignore */
        }
      }
      nativeDisplay.close();
      reportStatus({ok:false,stage:'disposed',registered:0,tools:[]});statusClosed=true;
      bridge.close();
    });
  });
  // Cordis requires an effect disposer, not an arbitrary service object.
  const disposePermissions=()=>permissions.close();
  disposePermissions.permissions=permissions; // Host/test handle, never a model tool.
  return disposePermissions;
}
