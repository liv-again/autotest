import pathlib, re
SK = pathlib.Path(__file__).resolve().parents[2] / ".claude/skills/app-selftest"

def test_skill_has_frontmatter():
    txt = (SK / "SKILL.md").read_text(encoding="utf-8")
    assert txt.startswith("---")
    assert re.search(r"^name:\s*app-selftest", txt, re.M)
    assert re.search(r"^description:", txt, re.M)

def test_references_exist():
    for r in ("workflow.md", "tiering.md", "pitfalls.md", "safety-policy.md", "explore.md"):
        assert (SK / "references" / r).exists()

def test_tiering_encodes_high_default():
    txt = (SK / "references/tiering.md").read_text(encoding="utf-8")
    assert "high" in txt and "BLOCKED_ENVIRONMENT" in txt

def test_skill_wires_explore_mode():
    # explore mode（画像路径半自动探索）必须被 SKILL.md 生命周期与薄索引引用。
    txt = (SK / "SKILL.md").read_text(encoding="utf-8")
    assert "explore" in txt and "references/explore.md" in txt


def test_prepare_skill_exists_and_wires():
    # 前置任务 skill 须存在，缺码复用硬引擎、缺路径软核对、产出 scope_hash 交付物。
    PREP = pathlib.Path(__file__).resolve().parents[2] / ".claude/skills/app-selftest-prepare"
    txt = (PREP / "SKILL.md").read_text(encoding="utf-8")
    assert txt.startswith("---")
    assert re.search(r"^name:\s*app-selftest-prepare", txt, re.M)
    assert "prereq_extract.py" in txt   # 缺码复用硬引擎
    assert "软核对" in txt               # 缺路径先软核对
    assert "scope_hash" in txt          # 交付物 gate


def test_main_skill_wires_prepare_gate():
    # 主任务第 0 步必须引用前置任务，避免现场重新收集核对。
    txt = (SK / "SKILL.md").read_text(encoding="utf-8")
    assert "app-selftest-prepare" in txt
