# Architecture

```text
DSH Agent
   │  registered wdc_* tools
   ▼
Node plugin (dsh-wdc-computer-use)
   │  MCP JSON-RPC over stdio, started on first call
   ▼
Python service (win-desktop-control)
   │
   ├─ screenshot and display metadata
   ├─ guarded mouse and keyboard input
   ├─ window and clipboard operations
   └─ stop, frame validity and safety policy
```

The Node plugin registers a static, versioned tool catalog without starting Python. On the first call it launches the configured Python interpreter, performs the MCP handshake, verifies the live tool contract, and forwards the call. Read-only failures may be retried only when dispatch is known not to have happened. Mutating calls are never replayed after dispatch uncertainty.

DSH runs its pre-execution gate before invoking the tool body. For Auto, the plugin records the host preset against the host execution object and uses it only when the same execution arrives at the body with unchanged session permissions. Manual approvals are kept separately for observation and control in the live session object. Host preset, sandbox, and approval policy events invalidate grants and abort leases. The `wdc-access` slash command is registered with DSH's direct user command service.

The browser client adds a draft-level “computer use” intent, hides terminal desktop-tool details, and filters screenshot presentation using persisted provenance. The model-facing local file reference remains available for image reading.

The native feedback host is separately opted in. It receives fixed operation states and screen points only. It is created during a tool call and closed after the last active call.
