# App adapter contract

`tools/_run_three_sheets.py` is the generic execution core. It reads the
workbook, validates an external `agent_action_plan`, dispatches only approved
low-level actions, captures evidence, and writes the journal/results.

App-specific behaviour is loaded with `--app <slug>` from
`apps/<slug>/app.yaml`:

- `packages` lists launch candidates. `runtime_package` may put a preferred
  white-label package first.
- `adapter: adapter.py` is optional. If the file is absent,
  `tools.app_adapter.GenericAdapter` is used.
- `transient_overlay_signals` is optional. It lists high-signal texts such as
  an App's update, notification, or new-stock popup; matching one only asks
  the configured runtime Agent to inspect the screen and never performs a
  blind close.
- A specialized adapter may provide launch, module-root/reset, module-entry,
  page predicates, and legacy compatibility hooks. It must not parse Excel
  prose or replace the Agent action-plan contract.
- Generic apps may declare `module_roots` in `app.yaml` when they want a
  verified soft-reset contract. Without that declaration, the generic adapter
  fails closed at a new page-group boundary and lets the runner perform a
  bounded cold launch.
- Generic apps may declare optional `probe_canaries` as `[[Sheet, row], ...]`;
  otherwise navigation probing uses one conservative route candidate per
  navigation context.

Example:

```powershell
python tools/_run_three_sheets.py `
  --app guojin `
  --source cases.xlsx `
  --action-plan run/agent_action_plan.json `
  --output output/guojin-run `
  --recovery-agent-command "python tools/my_recovery_agent.py"
```

The optional `--recovery-agent-command` (or
`SIXGILL_RECOVERY_AGENT_COMMAND`) enables row-scoped runtime recovery. The
command receives one JSON request on stdin and returns one validated
`agent_runtime_recovery` object on stdout. The command is deliberately not
named after a particular Agent; Codex, OpenCode, Trae, Claude, or another
provider can implement the same stdio contract. Once invoked, the Agent owns
the current Excel row until the row completes or reaches a terminal blocked
state; every later blocker starts another bounded turn with the prior takeover
history.

The canonical recovery enums and low-level action vocabulary are shared by
the planner and recovery validator in `tools/agent_contract.py`. The bridge
validates its provider response before writing stdout, performs one bounded
schema-repair request when validation fails, and only normalizes unambiguous
coordinate spellings. Ambiguous recovery decisions fail closed as
`blocked`; they are never silently converted into a potentially unsafe replay.

This repository includes `tools/agent_runtime_recovery_bridge.py` as a
provider-configurable bridge. Its default transport is the native Codex
Desktop app-server shipped under `%LOCALAPPDATA%\OpenAI\Codex\bin`, using the
JSON-RPC stdio protocol. It does not fall back to the npm `codex.cmd exec`
interface. `SIXGILL_DESKTOP_CODEX_BIN` can select an explicit desktop binary;
`SIXGILL_RUNTIME_AGENT_MODEL`, `SIXGILL_RUNTIME_AGENT_REASONING`, and
`SIXGILL_RUNTIME_AGENT_NAME` override the desktop Agent metadata. Other Agent
providers can opt into the generic command transport with
`SIXGILL_RUNTIME_AGENT_TRANSPORT=command` and an explicit
`SIXGILL_RUNTIME_AGENT_CLI` command, without changing the executor.

`--legacy-deterministic` is intentionally available only to an App that
explicitly implements legacy hooks. The Guotou compatibility parser now lives
under `apps/guotou/legacy.py`; the old detail-sheet compatibility overrides
live under `apps/guotou/legacy_detail.py`. Neither is part of the generic
execution path. `.codex_spreadsheet_work/run_quote_p0.py` is now only a
backward-compatible launcher for that Guotou module.
