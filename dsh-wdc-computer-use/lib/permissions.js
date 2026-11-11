/** DSH execution boundary; grants belong to session objects, never tool arguments. */
const READS=new Set(['screen_info','screenshot','list_windows','clipboard_get','wait']);
const MODES=new Set(['ask','autonomous','read-only','disabled']);
export const permissionError=(code,message)=>Object.assign(new Error(message),{code});

export class SessionPermissions {
  constructor({mode='ask',timeoutMs=60000,getApproval=()=>undefined}={}) {
    this.mode=MODES.has(mode)?mode:'ask';this.timeoutMs=timeoutMs;
    this.getApproval=getApproval;this.sessions=new WeakMap();this.active=new Set();this.closed=false;
  }
  state(session){
    let state=this.sessions.get(session);
    if(!state){state={mode:this.mode,version:0};this.sessions.set(session,state);}
    return state;
  }
  // Host-only API for a subsequent user-command adapter; never registered as a model tool.
  setMode(session,mode){
    if(!session||typeof session!=='object'||!MODES.has(mode))throw new TypeError('Invalid session permission mode');
    const state=this.state(session);state.mode=mode;state.version++;
    for(const lease of this.active)if(lease.session===session)lease.controller.abort();
  }
  async acquire(tool,exec){
    if(this.closed)throw permissionError('permission_closed','电脑操作权限服务已关闭');
    const session=exec?.agent?.session;
    if(!session||typeof session!=='object'||!exec?.signal?.addEventListener)throw permissionError('permission_context_missing','电脑操作需要 DSH 会话和取消上下文');
    const state=this.state(session),version=state.version,controller=new AbortController();
    const lease={session,controller,signal:controller.signal};this.active.add(lease);
    const cancel=()=>controller.abort();exec.signal.addEventListener('abort',cancel,{once:true});
    if(exec.signal.aborted)cancel();
    lease.release=()=>{exec.signal.removeEventListener('abort',cancel);this.active.delete(lease);};
    lease.check=()=>{
      if(this.closed||controller.signal.aborted)throw permissionError('operation_cancelled','电脑操作已取消或权限已撤销');
      if(state.version!==version)throw permissionError('permission_revoked','电脑操作权限已变更');
      if(state.mode==='disabled'||(state.mode==='read-only'&&!READS.has(tool)))throw permissionError('permission_denied','当前会话不允许该电脑操作');
      if(this.getApproval()?.effectivePolicy?.(session)==='never'&&state.mode==='ask')throw permissionError('permission_denied','当前会话禁止权限申请');
    };
    try {
      lease.check();
      if(state.mode==='ask'){
        const approval=this.getApproval();
        if(!approval?.request)throw permissionError('approval_unavailable','DSH 审批通道不可用');
        let timer,abort;
        try {
          const interrupted=new Promise((_,reject)=>{
            abort=()=>reject(permissionError('operation_cancelled','电脑操作审批已取消'));
            controller.signal.addEventListener('abort',abort,{once:true});
            timer=setTimeout(()=>controller.abort(),this.timeoutMs);
          });
          const outcome=await Promise.race([interrupted,Promise.resolve().then(()=>{
            lease.check();
            return approval.request({agent:exec.agent,toolName:exec.name,callId:exec.callId,
              reason:READS.has(tool)?'允许本次电脑观察操作？':'允许本次电脑输入或控制操作？',signal:controller.signal});
          })]);
          lease.check();
          if(outcome!=='allowed-once')throw permissionError('permission_denied','本次电脑操作未获批准');
        }finally{clearTimeout(timer);controller.signal.removeEventListener('abort',abort);}
      }
      lease.check();return lease;
    }catch(error){lease.release();throw error;}
  }
  close(){this.closed=true;for(const lease of this.active)lease.controller.abort();}
}
