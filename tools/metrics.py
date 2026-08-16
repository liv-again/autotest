# -*- coding: utf-8 -*-
"""metrics.py —— 从 Claude Code 会话 transcript(jsonl) 按时间窗统计 token 消耗，
用于衡量"AI 自动测试"的性价比。

用法:
  python tools/metrics.py now                         # 打印当前 UTC 时间戳(用作批次起止标记)
  python tools/metrics.py tokens <SINCE_ISO> [UNTIL_ISO]   # 统计时间窗内 token + 估算成本
  python tools/metrics.py session                     # 全会话累计

时间戳格式: 2026-07-28T13:45:00Z （UTC，与 transcript 一致）。
单价为假设值(见 PRICE)，可按实际账单调整——token 数是硬数据，成本是粗估。
"""
import json, sys, os, glob, datetime, importlib.util, pathlib

PROJECT_DIR = r"C:\Users\38024\.claude\projects\C--Users-38024-orca-workspaces-autotest-sixgill"
# USD / 1M tokens —— 假设单价(Opus 量级)，按实际调整
PRICE = {"input": 15.0, "output": 75.0, "cache_read": 1.5, "cache_creation": 18.75}

def transcript():
    over = os.environ.get("CC_TRANSCRIPT")
    if over and os.path.isfile(over): return over
    files = glob.glob(os.path.join(PROJECT_DIR, "*.jsonl"))
    if not files: raise SystemExit("找不到 transcript jsonl，请设 CC_TRANSCRIPT 环境变量")
    return max(files, key=os.path.getmtime)

def parse_ts(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))

def sum_usage(since=None, until=None):
    f = transcript()
    s = parse_ts(since) if since else None
    u_ = parse_ts(until) if until else None
    tot = {"input":0,"output":0,"cache_read":0,"cache_creation":0,"turns":0,"actions":0}
    for line in open(f, encoding="utf-8"):
        line = line.strip()
        if not line: continue
        try: o = json.loads(line)
        except Exception: continue
        us = (o.get("message") or {}).get("usage")
        if not us: continue
        ts = o.get("timestamp")
        if ts:
            t = parse_ts(ts)
            if s and t < s: continue
            if u_ and t > u_: continue
        tot["input"] += us.get("input_tokens",0) or 0
        tot["output"] += us.get("output_tokens",0) or 0
        tot["cache_read"] += us.get("cache_read_input_tokens",0) or 0
        tot["cache_creation"] += us.get("cache_creation_input_tokens",0) or 0
        tot["turns"] += 1
        content = (o.get("message") or {}).get("content") or []
        if isinstance(content, list):
            tot["actions"] += sum(1 for block in content if isinstance(block, dict) and block.get("type") == "tool_use")
    return tot, f

def cost(t):
    return (t["input"]*PRICE["input"] + t["output"]*PRICE["output"]
            + t["cache_read"]*PRICE["cache_read"] + t["cache_creation"]*PRICE["cache_creation"]) / 1e6

def show(t, f):
    total_in = t["input"] + t["cache_read"] + t["cache_creation"]
    print(f"transcript: {os.path.basename(f)}")
    print(f"回合数(带usage的assistant轮): {t['turns']}")
    print(f"output_tokens          : {t['output']:>12,}   ← 最能代表AI实际工作量")
    print(f"input_tokens(未缓存)    : {t['input']:>12,}")
    print(f"cache_read_tokens      : {t['cache_read']:>12,}   ← 每轮重读上下文(便宜)")
    print(f"cache_creation_tokens  : {t['cache_creation']:>12,}")
    print(f"输入侧合计             : {total_in:>12,}")
    print(f"估算成本(USD, 单价可调) : ${cost(t):>10.3f}")

# —— D2 上下文税提醒接线（复用 .claude/hooks/context_tax_reminder.py 的 assess） ——

def _load_assess():
    p = pathlib.Path(__file__).resolve().parent.parent / ".claude/hooks/context_tax_reminder.py"
    spec = importlib.util.spec_from_file_location("context_tax_reminder", p)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod.assess

def context_tax_metrics(tot, since=None, until=None):
    """把 sum_usage 的 tot 折算成 assess 需要的 {turns,actions,minutes}。"""
    m = {"turns": tot.get("turns", 0), "actions": tot.get("actions", tot.get("turns", 0))}
    if since and until:
        m["minutes"] = int((parse_ts(until) - parse_ts(since)).total_seconds() // 60)
    return m

def remind(tot, since=None, until=None):
    """D2 真实触发点：批次 metrics 算完后打印阈值提醒（空=不发声）。"""
    for msg in _load_assess()(context_tax_metrics(tot, since, until)):
        print("⚠ 上下文税提醒:", msg)

def main():
    if len(sys.argv) < 2:
        print(__doc__); return
    cmd = sys.argv[1]
    if cmd == "now":
        print(datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    elif cmd == "session":
        t, f = sum_usage(); show(t, f); remind(t)
    elif cmd == "tokens":
        since = sys.argv[2] if len(sys.argv) > 2 else None
        until = sys.argv[3] if len(sys.argv) > 3 else None
        t, f = sum_usage(since, until)
        print(f"窗口: {since or '(开头)'}  ~  {until or '(现在)'}")
        show(t, f)
        remind(t, since, until)
    else:
        print("unknown:", cmd); print(__doc__)

if __name__ == "__main__":
    main()
