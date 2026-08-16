import importlib.util, pathlib
spec = importlib.util.spec_from_file_location(
    "guard_git_add",
    pathlib.Path(__file__).resolve().parents[2] / ".claude/hooks/guard_git_add.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
decide = mod.decide

def test_blocks_git_add_dot():
    allow, reason = decide("git add .")
    assert allow is False and "git add" in reason

def test_blocks_git_add_all_flags():
    for cmd in ("git add -A", "git add --all", "git add -A .", "git add   ."):
        assert decide(cmd)[0] is False

def test_blocks_secrets_path():
    assert decide("git add .secrets/guojin.accounts.yaml")[0] is False
    assert decide("git add .secrets/hmac.key")[0] is False
    assert decide("git add runs/x/snapshots/a.png")[0] is False
    assert decide("git add foo.private.png")[0] is False

def test_allows_secrets_whitelist():
    # 镜像 .gitignore 的 !.secrets/README.md / !.secrets/*.example 白名单例外（Task 4 Step 7）
    assert decide("git add .secrets/README.md")[0] is True
    assert decide("git add .secrets/guojin.accounts.yaml.example")[0] is True

def test_allows_explicit_safe_add():
    assert decide("git add tools/derive_docs.py")[0] is True
    assert decide("git add apps/guojin/profile.yaml tests/tools/test_x.py")[0] is True

def test_ignores_non_git_add():
    assert decide("git commit -m x")[0] is True
    assert decide("ls .secrets/")[0] is True

def test_blocks_git_add_chained_after_other_git_subcommand():
    # 复审发现1：compound command，前段无害 git 子命令不得掩护后段危险 add。
    assert decide("git rm --cached x && git add .")[0] is False
    assert decide("git add .secrets && git commit -m x")[0] is False
    assert decide("git add runs/x/snapshots && git commit -m x")[0] is False

def test_blocks_secret_dir_without_trailing_slash():
    # 复审发现2：裸目录（无尾随 '/'）本身也是最自然的 add 写法，必须识别为秘密路径。
    assert decide("git add .secrets")[0] is False
    assert decide("git add ./.secrets")[0] is False
    assert decide("git add runs/x/snapshots")[0] is False

def test_blocks_git_invoked_via_path_or_exe_suffix():
    # 复审发现3：按字面 "git" token 匹配会被完整路径/平台可执行名绕过。
    assert decide("/usr/bin/git add .")[0] is False
    assert decide("git.exe add .")[0] is False
    assert decide("git.exe add .secrets")[0] is False

def test_blocks_add_after_git_global_options():
    # 终审 Important：git 全局选项夹在 git 与子命令之间不得绕过护栏。
    assert decide("git --no-pager add .")[0] is False
    assert decide("git -C sub add .")[0] is False
    assert decide("git -c core.x=1 add .")[0] is False
    assert decide("git --git-dir=.git --work-tree=. add .")[0] is False
    # 全局选项 + 秘密路径同样拦截
    assert decide("git -C sub add .secrets/hmac.key")[0] is False

def test_blocks_dangerous_flag_combinations():
    # 终审 Minor：不依赖精确集合——含 A(全部)/u(已跟踪) 的短 flag 组合皆危险。
    assert decide("git add -vA")[0] is False
    assert decide("git add -An")[0] is False
    assert decide("git add -u")[0] is False

def test_blocks_bare_star_and_magic_pathspec():
    # 终审 Minor：裸 * 与 pathspec magic :(top)/:! 同 :/ 一样视为危险。
    assert decide("git add *")[0] is False
    assert decide("git add ':(top)'")[0] is False
    assert decide("git add ':!foo'")[0] is False
    assert decide("git add :/")[0] is False

def test_malformed_json_payload_fail_closed(monkeypatch):
    # 终审 Minor：硬护栏 fail-closed——stdin/JSON 解析异常应 exit 2 拦截，不再 return 0 放行。
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO("{not valid json"))
    assert mod._main() == 2

def test_utf8_payload_parsed_via_stdin_buffer(monkeypatch):
    # 回归：hook 挂在所有 Bash 上；payload 含 § / 中文时须按 UTF-8 从 sys.stdin.buffer 解码，
    # 不因 Windows locale(GBK) 误解码 UTF-8 字节 → 异常 → fail-closed 误拦无关命令。
    import io, json as _json
    class _FakeStdin:
        def __init__(self, raw):
            self.buffer = io.BytesIO(raw)
    safe = _json.dumps({"tool_input": {"command": "echo 自测经验总结 §二"}},
                       ensure_ascii=False).encode("utf-8")
    monkeypatch.setattr("sys.stdin", _FakeStdin(safe))
    assert mod._main() == 0  # § 的 UTF-8 字节被正确解码，安全命令放行
    danger = _json.dumps({"tool_input": {"command": "git add . §"}},
                         ensure_ascii=False).encode("utf-8")
    monkeypatch.setattr("sys.stdin", _FakeStdin(danger))
    assert mod._main() == 2  # 危险 git add 仍拦截
