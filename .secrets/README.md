# .secrets/ 说明

本目录内容默认 gitignore（仅本文件与 `*.example` 例外入仓）。放两类本地文件：

1. **`hmac.key`**（32 字节随机密钥，用于给 `apps/guojin/env.yaml` 签名）：

   ```bash
   python -c "import os,pathlib;pathlib.Path('.secrets/hmac.key').write_bytes(os.urandom(32))"
   ```

2. **`guojin.accounts.yaml`**（完整账号 ↔ alias 映射，供 `tools/safety/secrets.account_hmac` 比对）：复制
   `.secrets/guojin.accounts.yaml.example` 为 `.secrets/guojin.accounts.yaml`，填真实账号。

## 负责人认证流程（P0：执行程序绝不可自签/伪造环境认证）

1. 把**真实**认证值填进 `apps/guojin/env.yaml`：真名 `attested_by`、真实 `attested_at`/`valid_until`、真实 `basis`。
2. 设 `revoked: false`。
3. 用本地密钥对 `env.yaml` 签名，生成 `env.yaml.sig`：

   ```bash
   python -c "from tools.safety import env_sign; env_sign.sign('apps/guojin/env.yaml', env_sign.load_key('.secrets/hmac.key'), write_sig=True)"
   ```

## 禁止事项

**禁止直接把 `apps/guojin/env.yaml.example` 改名为 `env.yaml`。** `env.yaml.example` 是**假认证样例**（假名
`EXAMPLE-OPERATOR`、假日期、`revoked:false` 仅为演示形状）。`verify_env` 现已把占位/空 `attested_by`
（`EXAMPLE-OPERATOR`、`PENDING_OPERATOR_ATTESTATION`、`PENDING_`/`EXAMPLE-` 前缀等）判为 `attestation_placeholder`
并回退 `confirm_only`——所以改名也**达不到 `simulated_submit`**。但这不是替代品：**真实认证必须由负责人本人在
`env.yaml` 填写真实署名（真名/工号，绝非占位名）+ 真日期，并用 `.secrets` 密钥签名**，example 只作照抄参考。
