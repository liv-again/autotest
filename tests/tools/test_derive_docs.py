# tests/tools/test_derive_docs.py
from tools.derive_docs import derive
import pathlib
APP = pathlib.Path(__file__).resolve().parents[2] / "apps/guojin"

def test_derive_produces_three_docs():
    out = derive(APP)
    assert set(out) == {"画像.md", "前置条件.md", "速览.md"}

def test_derived_has_autogen_banner_and_sections():
    out = derive(APP)
    for name, content in out.items():
        assert "勿手改" in content.splitlines()[0]
    assert "功能支持" in out["画像.md"] or "能力" in out["画像.md"]
    assert "950025" in out["前置条件.md"]

def test_derive_idempotent():
    assert derive(APP) == derive(APP)

def test_derived_masked(forbidden_full_accounts):
    out = derive(APP)
    for content in out.values():
        for full in forbidden_full_accounts:
            assert full not in content
