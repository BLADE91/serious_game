from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_DIR = REPO_ROOT / "code" / "backend" / "content" / "packages" / "pkg_gameplay_v3"
STORY_PATH = PACKAGE_DIR / "story_beats.json"
BRIEFING_PATH = PACKAGE_DIR / "public_briefing.json"
SOURCE_PATH = REPO_ROOT / "最终剧本.md"


def _story_text() -> str:
    payload = json.loads(STORY_PATH.read_text(encoding="utf-8"))
    return json.dumps(payload, ensure_ascii=False)


def test_d1_uses_36_households_but_keeps_the_30_household_target() -> None:
    story = _story_text()
    source = SOURCE_PATH.read_text(encoding="utf-8")

    assert "九十天，三十六户，你手里只有一本账" in story
    assert "九十天，三十六户，你手里只有一本账" in source
    assert "九十天，三十户，你手里只有一本账" not in story
    assert "三十六户，签满三十户算达标" in story


def test_d1_introduces_the_same_five_dossiers_as_the_public_desk() -> None:
    expected_intro = (
        "他把那摞卷宗推到你手边，一共五份：上级交办、财政与项目、"
        "村庄社会、企业与后台、前任与旧案。"
    )
    story = _story_text()
    source = SOURCE_PATH.read_text(encoding="utf-8")
    briefing = json.loads(BRIEFING_PATH.read_text(encoding="utf-8"))

    assert expected_intro in story
    assert expected_intro in source
    assert [item["title"] for item in briefing["dossiers"]] == [
        "第一卷·上级交办",
        "第二卷·财政与项目",
        "第三卷·村庄社会",
        "第四卷·企业与后台",
        "第五卷·前任与旧案",
    ]
