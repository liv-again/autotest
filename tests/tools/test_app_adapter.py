from pathlib import Path

import yaml

from tools.app_adapter import (
    GenericAdapter,
    infer_app_slug_from_profile,
    load_app_adapter,
    load_app_config,
)


ROOT = Path(__file__).resolve().parents[2]


def test_guotou_uses_specialized_adapter_and_keeps_runtime_package():
    config = load_app_config(ROOT, "guotou")
    adapter = load_app_adapter(config)

    assert adapter.name == "guotou"
    assert adapter.supports_legacy_deterministic is True
    assert adapter.preferred_packages[0] == "com.hexin.plat.android.AnxinSecurity"
    assert config.profile_path == (ROOT / "apps/guotou/profile.yaml").resolve()


def test_app_without_adapter_uses_generic_fallback(tmp_path):
    app_dir = tmp_path / "apps" / "example"
    app_dir.mkdir(parents=True)
    (app_dir / "app.yaml").write_text(
        yaml.safe_dump(
            {
                "slug": "example",
                "packages": ["com.example.app"],
                "module_roots": {"交易": {"all_text": ["交易"]}},
                "probe_canaries": [["交易", 2]],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    config = load_app_config(tmp_path, "example")
    adapter = load_app_adapter(config)

    assert isinstance(adapter, GenericAdapter)
    assert adapter.name == "generic"
    assert adapter.supports_legacy_deterministic is False
    assert adapter.probe_canaries == (("交易", 2),)
    assert adapter.is_module_root("交易", [{"text": "交易"}])
    assert not adapter.is_module_root("交易", [{"text": "行情"}])


def test_generic_profile_can_be_loaded_without_app_yaml(tmp_path):
    config = load_app_config(tmp_path, "generic")
    adapter = load_app_adapter(config)

    assert isinstance(adapter, GenericAdapter)
    assert config.packages == ()


def test_profile_slug_can_select_app_when_app_flag_is_omitted(tmp_path):
    profile = tmp_path / "profile.yaml"
    profile.write_text("slug: guojin\n", encoding="utf-8")

    assert infer_app_slug_from_profile(profile) == "guojin"
