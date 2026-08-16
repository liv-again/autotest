# -*- coding: utf-8 -*-
"""droid.py —— 极简 Android 驱动助手（adb + uiautomator），给 AI 做移动端用例实时驱动用。

用法:
  python droid.py current                 # 前台 package/activity
  python droid.py screen                   # dump 当前屏幕，打印可交互/有文本的元素清单
  python droid.py find "文字"              # 在当前屏幕找包含该文字的元素
  python droid.py tap  --text "买入"       # 按文本点击（点其可点击祖先的中心）
  python droid.py tap  --id  com.x:id/ok   # 按 resource-id 点击
  python droid.py tap  --xy  630 1400      # 按坐标点击
  python droid.py type "512000"            # 输入文本（需先聚焦输入框）
  python droid.py key BACK|HOME|ENTER|DEL  # 按键
  python droid.py swipe 630 2000 630 800 300   # 滑动(默认300ms)
  python droid.py shot [out.png]           # 尽力截图(FLAG_SECURE会得到0字节)

设计要点：不依赖第三方库；adb 通过 subprocess 参数列表调用，避免 Git Bash 路径改写。
"""
import subprocess, sys, os, re, xml.etree.ElementTree as ET, json, tempfile

DEV_XML = "/sdcard/_droid_dump.xml"

def adb(*args, binary=False, timeout=60):
    cmd = ["adb", *args]
    r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if binary:
        return r.returncode, r.stdout, r.stderr.decode("utf-8", "replace")
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")

def current():
    _, out, _ = adb("shell", "dumpsys", "activity", "activities")
    for line in out.splitlines():
        if "mResumedActivity" in line or "ResumedActivity:" in line:
            m = re.search(r"u0 (\S+)", line)
            if m:
                return m.group(1)
    return "(unknown)"

def _bounds_center(b):
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", b or "")
    if not m: return None
    x1,y1,x2,y2 = map(int, m.groups())
    return ((x1+x2)//2, (y1+y2)//2, x1, y1, x2, y2)

def dump_xml(local=None):
    adb("shell", "uiautomator", "dump", DEV_XML)
    if local is None:
        local = os.path.join(tempfile.gettempdir(), "_droid_dump.xml")
    adb("pull", DEV_XML, local)
    with open(local, "r", encoding="utf-8") as f:
        return f.read()

def parse(xml):
    root = ET.fromstring(xml)
    els = []
    for i, n in enumerate(root.iter("node")):
        a = n.attrib
        text = (a.get("text") or "").strip()
        desc = (a.get("content-desc") or "").strip()
        rid  = a.get("resource-id") or ""
        clk  = a.get("clickable") == "true"
        scr  = a.get("scrollable") == "true"
        cls  = (a.get("class") or "").split(".")[-1]
        c = _bounds_center(a.get("bounds"))
        if not c: continue
        if not (text or desc or clk or scr or rid): continue
        els.append({"cx":c[0],"cy":c[1],"text":text,"desc":desc,
                    "id":rid.split("/")[-1] if "/" in rid else rid,
                    "cls":cls,"clk":clk,"scr":scr,"bounds":a.get("bounds")})
    return els

def screen():
    els = parse(dump_xml())
    for e in els:
        tag = "C" if e["clk"] else ("S" if e["scr"] else " ")
        label = e["text"] or (f"«{e['desc']}»" if e["desc"] else "")
        print(f"[{tag}] ({e['cx']:>4},{e['cy']:>4}) {e['cls']:<16} id={e['id']:<22} {label[:40]}")
    print(f"--- {len(els)} elements ---")
    return els

def find(kw):
    els = parse(dump_xml())
    hit = [e for e in els if kw in e["text"] or kw in e["desc"] or kw in e["id"]]
    for e in hit:
        print(f"({e['cx']},{e['cy']}) clk={e['clk']} id={e['id']} cls={e['cls']} text={e['text'][:40]} desc={e['desc'][:30]}")
    if not hit: print(f"(未找到包含 '{kw}' 的元素)")
    return hit

def tap_text(kw):
    """找到包含kw的节点；若自身不可点，回退到其可点击祖先——这里简化为点该节点中心。"""
    els = parse(dump_xml())
    cand = [e for e in els if kw in e["text"] or kw in e["desc"]]
    cand.sort(key=lambda e: (not e["clk"], len(e["text"] or e["desc"])))  # 优先可点击、文本最短(最精确)
    if not cand:
        print(f"tap: 未找到 '{kw}'"); return 1
    e = cand[0]
    adb("shell", "input", "tap", str(e["cx"]), str(e["cy"]))
    print(f"tapped '{kw}' @({e['cx']},{e['cy']}) id={e['id']} text={e['text'][:30]}")
    return 0

def tap_id(rid):
    els = parse(dump_xml())
    cand = [e for e in els if e["id"] == rid or rid in e["id"]]
    if not cand:
        print(f"tap: 未找到 id '{rid}'"); return 1
    e = cand[0]; adb("shell","input","tap",str(e["cx"]),str(e["cy"]))
    print(f"tapped id={rid} @({e['cx']},{e['cy']})"); return 0

def has(kws):
    """断言：当前屏幕是否出现每个关键词（文本/desc）。返回全部命中则0，否则1。"""
    els = parse(dump_xml())
    blob = " ".join((e["text"]+" "+e["desc"]) for e in els)
    ok = True
    for kw in kws:
        found = kw in blob
        print(f"  [{'✓' if found else '✗'}] {kw}")
        ok = ok and found
    return 0 if ok else 1

def main():
    if len(sys.argv) < 2:
        print(__doc__); return
    cmd = sys.argv[1]
    _log = os.environ.get("DROID_LOG")   # 设了就记动作日志，供 metrics 统计动作数
    if _log:
        try:
            with open(_log, "a", encoding="utf-8") as lf:
                lf.write(f"{cmd}\t{' '.join(sys.argv[2:])}\n")
        except Exception:
            pass
    if cmd == "current": print(current())
    elif cmd in ("screen","dump"): screen()
    elif cmd == "has": sys.exit(has(sys.argv[2:]))
    elif cmd == "find": find(sys.argv[2])
    elif cmd == "tap":
        if "--text" in sys.argv: sys.exit(tap_text(sys.argv[sys.argv.index("--text")+1]))
        elif "--id" in sys.argv: sys.exit(tap_id(sys.argv[sys.argv.index("--id")+1]))
        elif "--xy" in sys.argv:
            i=sys.argv.index("--xy"); adb("shell","input","tap",sys.argv[i+1],sys.argv[i+2]); print("tapped xy")
    elif cmd == "type":
        txt = sys.argv[2]; adb("shell","input","text",txt); print(f"typed: {txt}")
    elif cmd == "key":
        k = sys.argv[2].upper(); adb("shell","input","keyevent","KEYCODE_"+k); print(f"key {k}")
    elif cmd == "swipe":
        a=sys.argv; ms = a[6] if len(a)>6 else "300"
        adb("shell","input","swipe",a[2],a[3],a[4],a[5],ms); print("swiped")
    elif cmd == "shot":
        out = sys.argv[2] if len(sys.argv)>2 else "shot.png"
        adb("shell","screencap","-p",DEV_XML+".png")
        adb("pull", DEV_XML+".png", out)
        sz = os.path.getsize(out) if os.path.exists(out) else 0
        print(f"screenshot -> {out} ({sz} bytes)" + ("  [FLAG_SECURE? 0字节=被防截屏挡了]" if sz==0 else ""))
    else:
        print("unknown cmd:", cmd); print(__doc__)

if __name__ == "__main__":
    main()
