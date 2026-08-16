import pathlib

import pytest
import yaml


@pytest.fixture
def forbidden_full_accounts():
    """脱敏哨兵：从 gitignored 的 `.secrets/guojin.accounts.yaml` 读完整账号，
    供各 masked 产物断言"这些真号不得出现在仓内"。

    刻意**不**在测试源码里字面写真号（否则脱敏测试自身又把真号写回受控源码，
    也会被 git 历史脱敏反复命中）。`.secrets` 缺失（如全新 clone）时跳过该检查。
    """
    p = pathlib.Path(".secrets/guojin.accounts.yaml")
    if not p.exists():
        pytest.skip(".secrets/guojin.accounts.yaml 不存在(脱敏哨兵源)，跳过完整账号泄漏检查")
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    nums = set()
    for a in doc.get("accounts", []):
        for k in ("login", "fund", "holder"):
            v = a.get(k)
            if v:
                nums.add(str(v))
    assert nums, ".secrets/guojin.accounts.yaml 未提供任何账号"
    return nums
