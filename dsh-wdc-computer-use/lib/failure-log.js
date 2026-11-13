import {closeSync, fstatSync, ftruncateSync, lstatSync, openSync, writeSync} from 'node:fs';
import {fileURLToPath} from 'node:url';

const logPath = fileURLToPath(new URL('../failures.log', import.meta.url));
const maxBytes = 256 * 1024;

// Accept only known identifiers. A host or worker can put arbitrary text in an
// exception or tool result. Keep only exact known codes.
const knownCodes = new Set([
  'tools_unavailable', 'registration_failed', 'initialization_failed',
  'approval_unavailable', 'approval_error', 'permission_context_missing',
  'service_start_failed', 'service_exited', 'service_unavailable', 'service_busy',
  'catalog_mismatch', 'rpc_error', 'transport_error', 'timeout', 'bridge_error',
  'outcome_unknown', 'stop_signal_failed', 'image_delivery_unavailable',
  'image_delivery_failed', 'image_delivery_timeout', 'image_resized', 'invalid_screenshot_frame',
  'result_conversion_failed', 'tool_failed',
  'bad_argument', 'capture_failed', 'input_unavailable', 'internal_error',
  'window_not_found', 'verification_failed', 'stale_frame',
  'feedback_error', 'process_error', 'process_exited', 'pipe_error', 'invalid_response',
  'startup_timeout', 'startup_failed', 'capture_or_geometry_unavailable',
  'worker_disabled', 'scene_failed', 'ack_timeout',
]);
const expectedRejections = new Set([
  'permission_denied', 'permission_revoked', 'permission_closed', 'operation_cancelled',
  'bridge_closed', 'emergency_stop', 'read_only', 'input_disabled', 'action_busy',
  'secure_desktop', 'blocked_shortcut', 'rate_limited',
]);

export function recordFailure(stage, code, tool) {
  if (expectedRejections.has(code)) return;
  const line = Buffer.from(JSON.stringify({
    at: new Date().toISOString(),
    stage,
    code: knownCodes.has(code) ? code : 'unknown_failure',
    ...(tool ? {tool} : {}),
  }) + '\n');
  let fd;
  try {
    const existing = lstatSync(logPath, {throwIfNoEntry: false});
    if (existing && (!existing.isFile() || existing.nlink !== 1)) return;
    fd = openSync(logPath, 'a', 0o600);
    const stat = fstatSync(fd);
    const current = lstatSync(logPath);
    if (!stat.isFile() || stat.nlink !== 1 || !current.isFile() ||
        stat.dev !== current.dev || stat.ino !== current.ino) return;
    if (stat.size + line.length > maxBytes) ftruncateSync(fd, 0);
    writeSync(fd, line);
  } catch {
    // A read-only package or unavailable disk must not break a tool call.
  } finally {
    if (fd !== undefined) {
      try { closeSync(fd); } catch { /* Ignore logging I/O failures. */ }
    }
  }
}
