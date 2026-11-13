const route = '/api/computeruse-dsh/settings';
const packageName = 'dsh-wdc-computer-use';
const profiles = new Set(['minimal','standard','full']);
const hotkeys = new Set(['','esc','ctrl+shift+f12']);
export const optionalTools = new Set([
  'window_screenshot','inspect_controls','control_action','recognize_text','locate_target','batch_windows',
]);
const selectableBaseTools = new Set([
  'screenshot','screen_info','click','move_mouse','drag','type_text','press_key','scroll',
  'list_windows','activate_window','window_action','clipboard_get','wait',
]);

export function mountSettings(ctx, options, snapshot, isBusy) {
  ctx.inject(['webServer','connection','configEditor'], scope => {
    const server = scope.get?.('webServer');
    const connection = scope.get?.('connection');
    const editor = scope.get?.('configEditor');
    if (typeof server?.register !== 'function' || typeof connection?.requestRejection !== 'function' ||
        typeof editor?.edit !== 'function' || typeof editor?.entries !== 'function') return;
    scope.effect(() => server.register({kind:'exact',path:route,handler:async (req,res) => {
      function send(code, body) {
        res.writeHead(code,{'content-type':'application/json','cache-control':'no-store'});
        res.end(JSON.stringify(body));
      }
      const fetchSite = req.headers['sec-fetch-site'];
      if (req.headers['x-dsh-plugin'] !== packageName ||
          req.headers['sec-fetch-site'] === 'cross-site' ||
          connection.requestRejection({headers:req.headers}) !== undefined) {
        send(403,{error:'需要在已认证的 DSH 窗口中打开设置。'});
        return;
      }
      if (req.method === 'GET') {
        const current=snapshot();
        send(200,{
          profile:options.profile,enableTools:options.enableTools.filter(tool=>optionalTools.has(tool)),
          disabledTools:options.hideTools.filter(tool=>selectableBaseTools.has(tool)),
          feedbackEnabled:options.feedbackEnabled && options.feedbackNativeEnabled,
          stopHotkey:options.stopHotkey,screenshotScale:options.screenshotScale,
          permissionMode:options.permissionMode, registered:current.registered ?? 0,
          runtime:current.runtime?.state ?? 'dormant',
        });
        return;
      }
      if (req.method !== 'POST' || req.headers['content-type']?.split(';')[0] !== 'application/json') {
        send(405,{error:'不支持的设置请求。'});
        return;
      }
      if (fetchSite !== 'same-origin') {
        send(403,{error:'设置只能从 DSH 界面保存。'});
        return;
      }
      if (isBusy()) {send(409,{error:'电脑操作结束后才能修改设置。'});return;}
      try {
        let body='';
        for await (const chunk of req) {
          body += chunk;
          if (Buffer.byteLength(body) > 4096) {send(413,{error:'设置请求过大。'});return;}
        }
        const {profile,enableTools,disabledTools,feedbackEnabled,stopHotkey,screenshotScale}=JSON.parse(body);
        if (!profiles.has(profile) || !Array.isArray(enableTools) ||
            enableTools.length > optionalTools.size || enableTools.some(tool=>!optionalTools.has(tool)) ||
            !Array.isArray(disabledTools) || disabledTools.length > selectableBaseTools.size ||
            disabledTools.some(tool=>!selectableBaseTools.has(tool)) ||
            typeof feedbackEnabled !== 'boolean' || !hotkeys.has(stopHotkey) ||
            !Number.isFinite(screenshotScale) || screenshotScale < 0 || screenshotScale > 1) {
          send(400,{error:'设置值不在允许范围内。'});
          return;
        }
        const entries=editor.entries().filter(entry=>entry.options?.name===packageName && entry.options?.id==='wdc-computer-use');
        if (entries.length !== 1) {send(409,{error:'当前 Profile 没有唯一可编辑的电脑操作设置。'});return;}
        await editor.edit(entries[0],current=>{
          if (isBusy()) throw new Error('busy');
          const retained=Array.isArray(current.enableTools)?current.enableTools.filter(tool=>!optionalTools.has(tool)):[];
          const hidden=Array.isArray(current.hideTools)?current.hideTools.filter(tool=>!selectableBaseTools.has(tool)):['run_command'];
          return {...current,profile,enableTools:[...retained,...new Set(enableTools)],feedbackEnabled,
            hideTools:[...hidden,...new Set(disabledTools)],feedbackNativeEnabled:feedbackEnabled,stopHotkey,screenshotScale};
        });
        send(200,{ok:true});
      } catch {send(400,{error:'DSH 未保存设置，请刷新后重试。'});}
    }}));
  });
}
