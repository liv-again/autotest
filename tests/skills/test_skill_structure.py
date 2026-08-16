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
