import hmac, hashlib, subprocess

def account_hmac(account_no, key):
    return hmac.new(key, account_no.encode("utf-8"), hashlib.sha256).hexdigest()

def env_integrity_ok(env_yaml_path, sig_path, key):
    try:
        with open(env_yaml_path, "rb") as f:
            body = f.read()
    except FileNotFoundError:
        return False
    expect = hmac.new(key, body, hashlib.sha256).hexdigest()
    try:
        with open(sig_path, encoding="utf-8") as f:
            got = f.read().strip()
    except FileNotFoundError:
        return False
    return hmac.compare_digest(expect, got)

def is_git_committed(path):
    try:
        r = subprocess.run(["git", "status", "--porcelain", "--", path],
                           capture_output=True, text=True)
    except Exception:
        return False
    if r.returncode != 0:
        return False
    return r.stdout.strip() == ""
