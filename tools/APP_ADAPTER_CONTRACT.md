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
- The adapter may expose app-specific launch, module-root/reset, module-entry,
  and page-predicate behavior, but it must not start an auxiliary Agent or
  parse Excel prose.
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
  --output output/guojin-run
```

Full runs automatically retest first-pass blocked rows once under
`output/guojin-run/blocked-retest`. The second pass reuses the validated
action plan, starts with fresh setup, and merges both attempts into
`execution_records.retested.json`.
Use `--no-auto-retest-blocked` only when inspecting the raw first pass.

The executor consumes only the validated low-level actions in the
Agent action plan. Runtime execution does not start a second Agent transport;
exceptional rows are recorded in the evidence and exception queues for
post-run LLM review.

`--legacy-deterministic` is intentionally available only to an App that
explicitly implements legacy hooks. The Guotou compatibility parser now lives
under `apps/guotou/legacy.py`; the old detail-sheet compatibility overrides
live under `apps/guotou/legacy_detail.py`. Neither is part of the generic
execution path. `.codex_spreadsheet_work/run_quote_p0.py` is now only a
backward-compatible launcher for that Guotou module.
