/** Grants belong to DSH session objects and never come from tool arguments. */
const READS = new Set(['screen_info', 'screenshot', 'list_windows', 'clipboard_get', 'wait']);
const MODES = new Set(['ask', 'autonomous', 'read-only', 'disabled']);
const PERMISSION_EVENTS = new Set(['permission/preset', 'approval/policy', 'sandbox/mode']);
export const permissionError = (code, message) => Object.assign(new Error(message), {code});

function waitForPermission(promise, signal) {
  if (signal.aborted) return Promise.reject(permissionError('operation_cancelled', '电脑操作审批已取消'));
  let cancel;
  const interrupted = new Promise((_, reject) => {
    cancel = () => reject(permissionError('operation_cancelled', '电脑操作审批已取消'));
    signal.addEventListener('abort', cancel, {once:true});
  });
  return Promise.race([promise, interrupted]).finally(() => signal.removeEventListener('abort', cancel));
}

export class SessionPermissions {
  constructor({mode='ask', timeoutMs=60000, rememberApprovals=true,
    getApproval=()=>undefined, getPresets=()=>undefined}={}) {
    this.mode = MODES.has(mode) ? mode : 'ask';
    this.timeoutMs = timeoutMs;
    this.rememberApprovals = rememberApprovals;
    this.getApproval = getApproval;
    this.getPresets = getPresets;
    this.sessions = new WeakMap();
    this.preflights = new WeakMap();
    this.active = new Set();
    this.closed = false;
  }

  state(session) {
    let state = this.sessions.get(session);
    if (!state) {
      state = {mode:this.mode, version:0, grants:new Set(), pending:new Map()};
      this.sessions.set(session, state);
    }
    return state;
  }

  hostAccess(session) {
    const policy = this.getApproval()?.effectivePolicy?.(session);
    const service = this.getPresets();
    let preset, sandbox, autoLive = false;
    try {
      preset = service?.current?.(session);
      sandbox = service?.permissionState?.(session)?.sandbox;
      if (!sandbox && preset && preset !== 'custom') sandbox = service.resolve?.(preset)?.sandbox;
      autoLive = preset === 'auto' && service.names?.includes('auto') === true;
    } catch {
      // A missing or not-yet-ready host service cannot grant elevated access.
      preset = undefined;
      autoLive = false;
    }
    return {preset, sandbox, policy, autoLive, key:JSON.stringify([preset, sandbox, policy, autoLive])};
  }

  preflight(exec) {
    const session = exec?.agent?.session;
    if (!session || typeof session !== 'object') return;
    this.preflights.set(exec, {
      session, version:this.state(session).version, key:this.hostAccess(session).key,
    });
  }

  // Called by the host permission events and the user-only command.
  revoke(session) {
    const state = this.sessions.get(session);
    if (!state) return;
    state.version += 1;
    state.grants.clear();
    state.pending.clear();
    for (const lease of this.active) {
      if (lease.session === session) lease.controller.abort();
    }
  }

  onSessionEvent(session, event) {
    if (PERMISSION_EVENTS.has(event?.type)) this.revoke(session);
  }

  setMode(session, mode) {
    if (!session || typeof session !== 'object' || !MODES.has(mode)) {
      throw new TypeError('Invalid session permission mode');
    }
    this.state(session).mode = mode;
    this.revoke(session);
  }

  command({agent, rawInput, signal}) {
    const session = agent?.session;
    if (!session || typeof session !== 'object' || signal?.aborted || this.closed) {
      return {kind:'error', text:'电脑操作权限命令需要有效的 DSH 会话。'};
    }
    const mode = rawInput.trim();
    if (mode === '') {
      const {preset} = this.hostAccess(session);
      return {kind:'success', text:`电脑操作模式：${this.state(session).mode}。DSH 当前预设：${preset ?? '不可用'}。可用命令：/wdc-access ask、read-only、disabled、revoke、stop。`};
    }
    if (mode === 'revoke') {
      this.revoke(session);
      return {kind:'success', text:'已撤销本会话的插件授权，并取消在途操作。DSH 当前权限预设继续生效。'};
    }
    if (mode !== 'ask' && mode !== 'read-only' && mode !== 'disabled') {
      return {kind:'error', text:'用法：/wdc-access <ask|read-only|disabled|revoke>'};
    }
    this.setMode(session, mode);
    return {kind:'success', text:`本会话电脑操作模式已设为 ${mode}。`};
  }

  async request(tool, exec, hostName, lease, remember) {
    const approval = this.getApproval();
    if (!approval?.request) throw permissionError('approval_unavailable', 'DSH 审批通道不可用');
    const observation = READS.has(tool);
    const reason = remember
      ? (observation
        ? '授权本会话的电脑观察？点击「允许一次」后，插件会在本会话记住截图、窗口查询和剪贴板读取授权。'
        : '授权本会话的电脑输入与控制？点击「允许一次」后，插件会在本会话记住点击、输入和窗口控制授权。')
      : (observation ? '允许本次电脑观察操作？' : '允许本次电脑输入或控制操作？');
    const timer = setTimeout(() => lease.controller.abort(), this.timeoutMs);
    try {
      const outcome = await waitForPermission(Promise.resolve().then(() => {
        lease.check();
        return approval.request({agent:exec.agent, toolName:hostName, callId:exec.callId, reason, signal:lease.signal});
      }), lease.signal);
      lease.check();
      if (outcome === 'unavailable') throw permissionError('approval_unavailable', 'DSH 审批通道不可用');
      if (outcome !== 'allowed-once') throw permissionError('permission_denied', '本次电脑操作未获批准');
    } finally {
      clearTimeout(timer);
    }
  }

  async acquire(tool, exec, hostName=exec?.name) {
    if (this.closed) throw permissionError('permission_closed', '电脑操作权限服务已关闭');
    const session = exec?.agent?.session;
    if (!session || typeof session !== 'object' || !exec?.signal?.addEventListener) {
      throw permissionError('permission_context_missing', '电脑操作需要 DSH 会话和取消上下文');
    }
    const state = this.state(session);
    const version = state.version;
    const access = this.hostAccess(session);
    const preflight = this.preflights.get(exec);
    this.preflights.delete(exec);
    const matchedPreflight = preflight?.session === session && preflight.version === version && preflight.key === access.key;
    if (preflight && !matchedPreflight) throw permissionError('permission_revoked', '电脑操作权限已变更，请重新发起');

    const auto = matchedPreflight && access.autoLive;
    const full = access.preset === 'danger-full-access' && access.sandbox === 'danger-full-access' && access.policy === 'never';
    const read = READS.has(tool);
    const controller = new AbortController();
    const cancel = () => controller.abort();
    const lease = {
      session, controller, signal:controller.signal,
      release:() => {
        exec.signal.removeEventListener('abort', cancel);
        this.active.delete(lease);
      },
      check:() => {
        if (this.closed || controller.signal.aborted) throw permissionError('operation_cancelled', '电脑操作已取消或权限已撤销');
        if (state.version !== version || this.hostAccess(session).key !== access.key) {
          this.revoke(session);
          throw permissionError('permission_revoked', '电脑操作权限已变更，请重新发起');
        }
        if (state.mode === 'disabled' || access.preset === 'disabled' ||
          (!read && (state.mode === 'read-only' || access.sandbox === 'read-only' || access.preset === 'read-only'))) {
          throw permissionError('permission_denied', '当前会话不允许该电脑操作');
        }
      },
    };
    exec.signal.addEventListener('abort', cancel, {once:true});
    this.active.add(lease);
    if (exec.signal.aborted) cancel();

    try {
      lease.check();
      if (state.mode === 'ask' && !auto && !full) {
        if (access.policy === 'never') throw permissionError('permission_denied', '当前 DSH 权限预设不允许插件申请审批');
        const remember = this.rememberApprovals && typeof access.preset === 'string' && access.preset !== 'auto';
        // App launch and shell, when exposed by configuration, remain per-call.
        const scope = tool === 'launch_app' || tool === 'run_command' ? null : (read ? 'observe' : 'control');
        const key = remember && scope ? `${access.key}:${scope}` : null;
        if (!key) {
          await this.request(tool, exec, hostName, lease, false);
        } else if (!state.grants.has(key)) {
          let pending = state.pending.get(key);
          if (!pending) {
            pending = this.request(tool, exec, hostName, lease, true).then(() => {
              lease.check();
              state.grants.add(key);
            }).finally(() => {
              if (state.pending.get(key) === pending) state.pending.delete(key);
            });
            state.pending.set(key, pending);
          }
          await waitForPermission(pending, lease.signal);
        }
      }
      lease.check();
      return lease;
    } catch (error) {
      lease.release();
      throw error;
    }
  }

  close() {
    this.closed = true;
    for (const lease of this.active) lease.controller.abort();
  }
}
