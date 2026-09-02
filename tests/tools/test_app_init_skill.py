"""Tests for the project-local app-init skill wrapper."""
import importlib.util
import pathlib

import pytest
import yaml

from tools.contracts.validate import validate
from tools.lint_profile import lint


ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".codex" / "skills" / "app-init" / "scripts" / "create_app.py"


def _load_skill_script():
    spec = importlib.util.spec_from_file_location("app_init_skill_create_app", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_skill_template_has_schema_valid_standard_codes():
    module = _load_skill_script()
    codes = module.load_standard_known_codes()
    prereq = {
        "slug": "newapp",
        "account_capabilities": [],
        "instrument_properties": [],
        "known_codes": codes,
    }
    assert len(codes) == 12
    assert validate(prereq, "prerequisites") == []
    assert {item["code"] for item in codes} >= {"950025", "950001", "600008"}
    optional = {
        "has_nav",
        "has_holding",
        "orderbook_depth",
        "collateral_eligible",
        "financing_eligible",
        "in_subscription",
    }
    assert all(optional.isdisjoint(item["attributes"]) for item in codes)


def test_skill_creates_files_populates_codes_and_reminder(tmp_path):
    module = _load_skill_script()
    app_dir = tmp_path / "apps" / "newapp"
    written = module.create_app(
        "newapp",
        "com.example.newapp",
        "1.2.3",
        repo_root=ROOT,
        app_dir=app_dir,
        verified_at="2026-08-30",
    )

    assert app_dir / "app.yaml" in written
    assert app_dir / "env.yaml.example" in written
    assert app_dir / "待补充.md" in written
    assert not (app_dir / "env.yaml").exists()
    with (app_dir / "app.yaml").open(encoding="utf-8") as f:
        app = yaml.safe_load(f)
    with (app_dir / "prerequisites.yaml").open(encoding="utf-8") as f:
        prereq = yaml.safe_load(f)
    with (app_dir / "env.yaml.example").open(encoding="utf-8") as f:
        env_example = yaml.safe_load(f)

    assert app["packages"] == ["com.example.newapp"]
    assert app["verified_versions"] == [{"version": "1.2.3", "verified_at": "2026-08-30"}]
    assert prereq["known_codes"] == module.load_standard_known_codes()
    assert len(prereq["known_codes"]) == 12
    assert env_example["type"] == "simulation"
    assert env_example["assurance_level"] == "trusted_internal"
    assert env_example["evidence"]["package"] == "com.example.newapp"
    assert env_example["evidence"]["version_range"]["min"] == "1.2.3"
    assert env_example["revoked"] is True
    assert validate(prereq, "prerequisites") == []
    assert "env.yaml" in (app_dir / "待补充.md").read_text(encoding="utf-8")
    assert "known_codes" in (app_dir / "待补充.md").read_text(encoding="utf-8")
    assert lint(app_dir, "2026-08-30") == []


def test_skill_refuses_non_empty_existing_directory(tmp_path):
    module = _load_skill_script()
    app_dir = tmp_path / "apps" / "existing"
    app_dir.mkdir(parents=True)
    sentinel = app_dir / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(FileExistsError, match="非空"):
        module.create_app(
            "existing",
            "com.example.existing",
            "1.0.0",
            repo_root=ROOT,
            app_dir=app_dir,
        )

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert list(app_dir.iterdir()) == [sentinel]
