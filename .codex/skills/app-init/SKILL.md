---
name: app-init
description: 初始化新的 Android App 配置，生成标准文件和安全 env.yaml.example，写入 skill 配置中的 known_codes，并提醒后续人工补充项；已有非空 App 目录不覆盖。
---

# App 初始化

当用户要求接入或创建一个新的 Android App 配置时使用本 skill。它适用于仓库内 `apps/<slug>/` 的新 App，不用于修改已有 App 的配置。

## 输入

从用户请求中取得：

- `slug`：小写字母、数字、连字符或下划线组成的目录标识；
- Android 包名；
- 当前 App 版本号。

如果包名或版本号缺失，先向用户询问，不要猜测。可选的别名、白标种子和兼容区间只有在用户提供时才传入。

## 执行

在仓库根目录运行：

```text
python .codex/skills/app-init/scripts/create_app.py <slug> --package <包名> --version <版本>
```

脚本会：

1. 检查 `apps/<slug>`；目录不存在或为空目录才允许继续，存在任何文件/子目录时停止并提示，绝不覆盖；
2. 调用仓库现有的 `tools/init_app.py` 生成 `app.yaml`、`profile.yaml`、`prerequisites.yaml` 及 3 个派生 Markdown 文件；
3. 读取本 skill 的 [`config/standard_known_codes.yaml`](config/standard_known_codes.yaml)，将其中的 `known_codes` 原样写入 `prerequisites.yaml`；
4. 生成安全的 `env.yaml.example`，写入当前包名和版本范围，但默认 `revoked: true`；
5. 生成 `待补充.md`，记录必须由用户确认或补录的项目；
6. 不生成真实 `env.yaml`，不写入完整账号、密码、密钥或签名；
7. 在写盘前复用仓库 schema 校验，完成后重新派生文档。

可选参数：

- `--aliases A,B`：写入 App 别名；
- `--seed-from apps/<seed>`：仅复制入口 `entries`，不复制能力矩阵、链路或前置代码；
- `--compat-min`、`--compat-max-excl`、`--verified-at`：显式设置对应元数据；
- `--app-dir <path>`：用户明确指定其他输出目录时使用。

## 完成后的回复

先报告创建结果、输出目录、写入的标准 `known_codes` 数量和生成的文件。然后再次提醒用户检查 `待补充.md`，至少包括：

- 以 `env.yaml.example` 为起点手工创建并认证 `env.yaml`；确认包名、版本范围、账户别名后才解除 `revoked: true`；
- 在 `app.yaml.test_accounts` 中补充脱敏账户别名、类型和尾号，不写完整账号；
- 将 `compatibility.max_exclusive` 从占位值收紧到实际上限；
- 在目标 App 中逐个确认标准 `known_codes` 是否存在及属性是否正确，删除不适用代码并补充 App 专属代码；
- 按测试需求补齐 `instrument_properties`；
- 通过设备探索补齐 `profile.yaml` 的入口、能力和已验证链路，然后运行 `tools/derive_docs.py` 与 `tools/lint_profile.py`。

标准代码只是可复用的测试数据候选，不能当作新 App 已验证事实。除非用户明确要求，不要自动创建认证文件或把模板条目标成已验证。
