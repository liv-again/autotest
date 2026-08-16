"""PreToolUse(Bash) 护栏：拦 `git add .`/`-A`/`--all` 与触碰 .secrets/snapshots/*.private.png 的 add。
放行显式安全 add 与 .secrets/ 白名单例外(README.md / *.example)。见 git-commit skill「禁 git add .」与 spec §L3。

复审修复（decide 必须逐次 git-add 独立评估，不能只看整行第一个 "git"）：
- 复合命令（`&&`/`||`/`;`/`|`/`&` 分隔）里每一次 `git ... add ...` 都独立判定，不只判前段。
- `.secrets`/`snapshots` 秘密路径匹配裸目录（无尾随 `/` 也算），不只匹配带尾随 `/` 的路径。
- 定位 git 调用按 basename（去掉 `.exe`）比对，认出 `/usr/bin/git`、`git.exe` 等非字面 "git" 的调用方式。
"""
import json, os, re, shlex, sys

_SECRET_PAT = re.compile(r"(^|/)\.secrets(/|$)|(^|/)snapshots(/|$)|\.private\.png$")
# .secrets/ 白名单例外：镜像 .gitignore 的 !.secrets/README.md / !.secrets/*.example（Task 4 Step 7）——两处必须一致。
_SECRET_ALLOW_PAT = re.compile(r"(^|/)\.secrets/(README\.md|[^/]*\.example)$")
# 批量 pathspec：. 与裸 * 都会把整目录纳入（* 由 shell 展开，但落到 shlex 里仍是字面 *）。
_DANGEROUS_PATHSPEC = {".", "*"}
# pathspec magic 前缀：:/(从仓根)、:((top)/(exclude) 等)、:!(排除) 都可越过白名单批量纳入，同等处理。
_MAGIC_PATHSPEC_PREFIXES = (":/", ":(", ":!")
# 复合命令分隔符：链式命令里每个分隔符之后都可能另起一次独立的 git 调用。
_SEPARATORS = {"&&", "||", ";", "|", "&"}
# git 全局选项（子命令之前）：不带值的单 token 选项。
_GIT_GLOBAL_FLAGS = {
    "--no-pager", "--paginate", "-p", "--bare",
    "--literal-pathspecs", "--glob-pathspecs", "--noglob-pathspecs",
    "--icase-pathspecs", "--no-replace-objects", "--no-optional-locks",
}
# 带值全局选项：空格形式（`-C dir` / `-c k=v`）吃掉下一个 token。
_GIT_GLOBAL_VALUE_FLAGS = {"-C", "-c"}
# 带值长选项：`--opt=val`（= 形式一个 token）或 `--opt val`（空格形式吃下一个 token）。
_GIT_GLOBAL_LONG_VALUE_FLAGS = (
    "--git-dir", "--work-tree", "--namespace", "--exec-path", "--super-prefix",
)

def _is_git_token(tok):
    """按 basename（忽略大小写的 .exe 后缀）判断 token 是否为 git 调用，
    认出 `/usr/bin/git`、`git.exe`、`C:\\...\\git.EXE` 等非字面 "git" 写法。"""
    base = os.path.basename(tok)
    if base.lower().endswith(".exe"):
        base = base[: -len(".exe")]
    return base == "git"

def _is_dangerous_flag(a):
    """短 flag 危险性不依赖精确集合：形如 `-XYZ`（纯字母）且含 `A`(全部)/`u`(已跟踪) 即危险，
    覆盖 `-A`/`-u`/`-vA`/`-An`/`-uv` 等一切组合；长选项 `--all` 单列。"""
    if a == "--all":
        return True
    if re.fullmatch(r"-[A-Za-z]+", a):
        return "A" in a or "u" in a
    return False

def _check_add_args(args):
    """检查单次 `git add <args>` 的参数，返回该次调用的 (allow, reason)。"""
    for a in args:
        if _is_dangerous_flag(a):
            return (False, "禁用批量 `git add`（-A/--all/-u 及其组合如 -vA/-An）：请显式 `git add <files>`（git-commit skill 硬规则）。")
        if a in _DANGEROUS_PATHSPEC:
            return (False, "禁用批量 `git add .`/`*`：请显式 `git add <files>`（git-commit skill 硬规则）。")
        if _SECRET_ALLOW_PAT.search(a):
            continue  # 白名单例外：.secrets/README.md 与 .secrets/*.example 允许入仓（镜像 .gitignore 的 ! 例外）
        if a.startswith(_MAGIC_PATHSPEC_PREFIXES):
            return (False, f"拒绝 magic pathspec `{a}`：:/ /:(...)/:! 可越过白名单批量纳入，请显式 `git add <files>`。")
        if _SECRET_PAT.search(a):
            return (False, f"拒绝把秘密/私密路径加入版本控制：{a}（.secrets/、snapshots/、*.private.png 应 gitignore；仅 .secrets/README.md 与 *.example 例外）。")
    return (True, "")

def _git_subcommand_index(toks, gi):
    """toks[gi] 是 git token；跳过其后的 git 全局选项，返回子命令 token 的下标；
    若到分隔符/行尾都没遇到非选项 token 则返回 None。"""
    j, n = gi + 1, len(toks)
    while j < n:
        tok = toks[j]
        if tok in _SEPARATORS:
            return None
        if tok in _GIT_GLOBAL_FLAGS:
            j += 1
            continue
        if tok in _GIT_GLOBAL_VALUE_FLAGS:
            j += 2  # 空格形式带值：吃掉选项与其值（如 `-C sub`、`-c k=v`）
            continue
        matched = False
        for lf in _GIT_GLOBAL_LONG_VALUE_FLAGS:
            if tok == lf:
                j += 2  # 空格形式：`--git-dir .git`
                matched = True
                break
            if tok.startswith(lf + "="):
                j += 1  # = 形式：`--git-dir=.git` 单 token
                matched = True
                break
        if matched:
            continue
        if tok.startswith("-"):
            j += 1  # 其它未列出的全局选项：保守跳过单 token，继续找子命令
            continue
        return j  # 第一个非选项 token = 子命令
    return None

def decide(command):
    cmd = command.strip()
    try:
        toks = shlex.split(cmd)
    except ValueError:
        toks = cmd.split()
    # 扫描整行里每一处 git 调用（而非只看第一个 "git"），且定位 git token 后先跳过
    # git 全局选项再取子命令——防止 `git --no-pager/-C dir/-c k=v/--git-dir=... add .`
    # 把全局选项夹在中间绕过护栏，也防前段无害 git 子命令掩护后段危险 add。
    n = len(toks)
    for i in range(n):
        if not _is_git_token(toks[i]):
            continue
        sub_idx = _git_subcommand_index(toks, i)
        if sub_idx is None or toks[sub_idx] != "add":
            continue
        args = []
        for tok in toks[sub_idx + 1:]:
            if tok in _SEPARATORS:
                break  # 到下一个链式分隔符为止，只属于这一次 git add 调用
            args.append(tok)
        allow, reason = _check_add_args(args)
        if not allow:
            return (allow, reason)
    return (True, "")

def _read_stdin_payload():
    """读 hook payload 文本：优先 sys.stdin.buffer 原始字节按 UTF-8 显式解码——
    本 hook 挂在所有 Bash 上，而 Windows 中文环境 python 的 sys.stdin 默认按 locale(GBK)
    解码，会把 payload 里 § / 中文等 UTF-8 字节误解码为异常；叠加 fail-closed 就会误拦无关命令。
    显式按 UTF-8 读字节可消除该编码歧义。无 buffer(如测试注入 StringIO)时退回文本读。"""
    buf = getattr(sys.stdin, "buffer", None)
    if buf is not None:
        return buf.read().decode("utf-8")
    return sys.stdin.read()

def _main():
    try:
        payload = json.loads(_read_stdin_payload())
        command = payload.get("tool_input", {}).get("command", "")
    except Exception:
        # 硬护栏 fail-closed：读 stdin / 解析 JSON 失败也拦截（exit 2），绝不放行——
        # 一个拿不到/读不懂 payload 的护栏必须往安全侧倒，而非默认放过。
        print("guard_git_add: 无法读取/解析 hook payload，fail-closed 拦截。", file=sys.stderr)
        return 2
    allow, reason = decide(command)
    if not allow:
        print(reason, file=sys.stderr)
        return 2  # PreToolUse: 非零阻断
    return 0

if __name__ == "__main__":
    sys.exit(_main())
