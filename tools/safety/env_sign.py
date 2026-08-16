# tools/safety/env_sign.py
"""环境认证签名器：用 .secrets 本地密钥对 env.yaml 正文算 HMAC-SHA256，写 <path>.sig。
执行程序据此校验认证未被篡改（配合 secrets.env_integrity_ok）。密钥不入仓。"""
import hashlib, hmac, pathlib

def load_key(path):
    return pathlib.Path(path).read_bytes()

def sign(env_yaml_path, key, write_sig=False):
    data = pathlib.Path(env_yaml_path).read_bytes()
    digest = hmac.new(key, data, hashlib.sha256).hexdigest()
    if write_sig:
        pathlib.Path(str(env_yaml_path) + ".sig").write_text(digest, encoding="utf-8")
    return digest
