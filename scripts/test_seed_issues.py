from pathlib import Path
import seed_issues as SI


def test_parse_sections_titles_labels_bodies(tmp_path):
    doc = tmp_path / "issues.md"
    doc.write_text("# Issues\n\nintro\n\n## T1 · Device pairing\n`labels: track, size:M`\n\nBody one.\n\n**Acceptance.** x\n\n## G2 · Test isolation\n`labels: good-first-issue, size:S, tests`\n\nBody two.\n")
    s = SI.parse(doc.read_text())
    assert [x["title"] for x in s] == ["T1 · Device pairing", "G2 · Test isolation"]
    assert s[0]["labels"] == ["track", "size:M"] and s[1]["labels"] == ["good-first-issue", "size:S", "tests"]
    assert s[0]["body"].startswith("Body one.") and "§T1" in s[0]["body"]
    assert "labels:" not in s[1]["body"]


def test_real_doc_parses_every_section():
    s = SI.parse(SI.DOC.read_text())
    assert len(s) >= 24 and all(x["labels"] for x in s), [x["title"] for x in s if not x["labels"]]
