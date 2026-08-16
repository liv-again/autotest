from tools.safety.secrets import account_hmac, env_integrity_ok
import hmac, hashlib

KEY = b"test-key"

def test_account_hmac_stable_and_secret():
    h = account_hmac("9999999999", KEY)
    assert h == hmac.new(KEY, b"9999999999", hashlib.sha256).hexdigest()
    assert "9999999999" not in h

def test_env_integrity_detects_tamper(tmp_path):
    env = tmp_path / "env.yaml"; env.write_text("type: simulation\n", encoding="utf-8")
    sig = tmp_path / "env.sig"
    good = hmac.new(KEY, env.read_bytes(), hashlib.sha256).hexdigest()
    sig.write_text(good, encoding="utf-8")
    assert env_integrity_ok(str(env), str(sig), KEY) is True
    env.write_text("type: simulation\nrevoked: false\n", encoding="utf-8")  # tamper
    assert env_integrity_ok(str(env), str(sig), KEY) is False

def test_is_git_committed_fails_closed_on_error(monkeypatch):
    import tools.safety.secrets as s
    class R:
        returncode = 128; stdout = ""; stderr = "fatal"
    monkeypatch.setattr(s.subprocess, "run", lambda *a, **k: R())
    assert s.is_git_committed("whatever") is False

def test_is_git_committed_fails_closed_on_exception(monkeypatch):
    import tools.safety.secrets as s
    def boom(*a, **k): raise FileNotFoundError("git missing")
    monkeypatch.setattr(s.subprocess, "run", boom)
    assert s.is_git_committed("whatever") is False
