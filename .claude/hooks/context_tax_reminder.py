# -*- coding: utf-8 -*-
"""上下文税提醒（D2 降级态）：hook 拿不到实时 token，故按 turn/action/时长阈值提醒。
D2 真实触发点是 tools/metrics.py（每批 metrics 算完后调用 assess）；本文件只出 assess 纯函数。
_main 仅为可选手动 CLI（stdin 传 {"metrics": {...}} JSON），**不接为 Claude Code Stop hook**——
Stop payload 无实时 token/cache/metrics，接了永不发声。见 docs/superpowers/spikes/2026-07-29-hook-context-tax-feasibility.md"""
import json, sys

DEFAULT_THRESHOLDS = {"turns": 40, "actions": 60, "minutes": 30}

def assess(metrics, thresholds=None):
    th = thresholds or DEFAULT_THRESHOLDS
    msgs = []
    if metrics.get("turns", 0) > th["turns"]:
        msgs.append(f"本会话 turn={metrics['turns']}>{th['turns']}：上下文税(cache_read)累积，考虑新开精简会话或固化 Maestro。")
    if metrics.get("actions", 0) > th["actions"]:
        msgs.append(f"动作数={metrics['actions']}>{th['actions']}：批次偏大，按屏合并取证以省 token。")
    if metrics.get("minutes", 0) > th["minutes"]:
        msgs.append(f"批次时长={metrics['minutes']}min>{th['minutes']}：长会话含税成本虚高，考虑分批。")
    return msgs

def _main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    for m in assess(payload.get("metrics", {})):
        print(m, file=sys.stderr)
    return 0

if __name__ == "__main__":
    sys.exit(_main())
