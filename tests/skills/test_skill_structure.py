import pathlib, re
SK = pathlib.Path(__file__).resolve().parents[2] / ".claude/skills/app-selftest"

def test_skill_has_frontmatter():
    txt = (SK / "SKILL.md").read_text(encoding="utf-8")
    assert txt.startswith("---")
    assert re.search(r"^name:\s*app-selftest", txt, re.M)
    assert re.search(r"^description:", txt, re.M)

def test_references_exist():
    for r in ("workflow.md", "tiering.md", "pitfalls.md", "safety-policy.md"):
        assert (SK / "references" / r).exists()

def test_tiering_encodes_high_default():
    txt = (SK / "references/tiering.md").read_text(encoding="utf-8")
    assert "high" in txt and "BLOCKED_ENVIRONMENT" in txt
