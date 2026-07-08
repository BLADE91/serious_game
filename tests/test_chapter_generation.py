"""测试 6-Call 章节式生成管线。"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from src.domain.chapter_structure import (
    Chapter,
    ChapterCheckpoint,
    ChapterDecisionPoint,
    ChapterNode,
    ChapterOption,
    ChapterResult,
    ChapterStateSnapshot,
)
from src.generation.chapter_prompts import (
    CHAPTER_TEMPLATE_MD,
    build_call1_prompt,
    build_call2_prompt,
    build_call3_prompt,
    build_call4_prompt,
    build_call5a_prompt,
    build_call5b_slice_prompt,
    build_call5_prompt,
    build_initial_state_snapshot_text,
    build_state_snapshot_text,
)
from src.generation.chapter_validator import ChapterValidator


# ============================================================
# Fixtures
# ============================================================

SAMPLE_GAME_SETTINGS_MD = """# 剧本全局设定

## 游戏主题
测试主题

## 玩家角色
- 姓名: 测试
- 职位: 镇长

## 核心冲突
测试冲突

## 全局变量表
- signed: 0
- social_stability: 70
- political_credit: 70
- public_trust: 50
- env_clue: 0
- media_pressure: 30
- budget: 8000
- days_left: 90

## 章节数量: 3

## 结局类型
### ending_01: 好结局 (good)
### ending_02: 中性结局 (neutral)
### ending_03: 坏结局 (bad)
"""

SAMPLE_OUTLINE_MD = """# 章节大纲

## 结局可达性验证
### 结局 ending_01 (good)
- 需要的最终变量状态: signed >= 20
- 必须携带的 flag: [flag_evi_has_report]

## 第 1 章：开局
- chapter_id: ch01
- core_task: 测试任务1
- main_question: 测试问题1?

## 第 2 章：发展
- chapter_id: ch02
- core_task: 测试任务2
- main_question: 测试问题2?

## 第 3 章：结局
- chapter_id: ch03
- core_task: 测试任务3
- main_question: 测试问题3?

## Flag 全局规划表
| flag_id | 创建于 | 作用 | 参与哪个结局 |
|---|---|---|---|
| flag_test | ch01_A | 解锁 ch02_info | ending_01 |
"""

SAMPLE_CHAPTER_MD = """# 第1章：开局

## 章节信息
- chapter_id: ch01
- day_range: 第1-10天
- core_task: 测试任务
- main_question: 测试问题?

## 背景情境
测试背景

## 信息节点
### 信息节点 1
- node_id: ch01_info_01
- node_type: INFO
- next: ch01_choice

测试信息内容。

## 核心决策点
### 决策点：测试决策
- node_id: ch01_choice
- node_type: CHOICE
- question: 测试问题?

#### 选项 A：测试选项A
- choice_id: ch01_A
- option_label: A
- 选项文本: 测试选项A
- 变量影响:
  - signed: +3
  - social_stability: -5
  - political_credit: 0
  - public_trust: 0
  - env_clue: +15
  - media_pressure: 0
  - budget: -200
  - days_left: -7
- 解锁节点: [ch02_info_test]
- 关闭节点: []
- 新增 flag: [flag_test]
- 移除 flag: []
- 即时后果: 测试后果
- 长期影响: 测试影响
- 教学反馈: 测试反馈

#### 选项 B：测试选项B
- choice_id: ch01_B
- option_label: B
- 选项文本: 测试选项B
- 变量影响:
  - signed: +1
  - social_stability: +5
  - political_credit: -5
  - public_trust: +10
  - env_clue: 0
  - media_pressure: 0
  - budget: -100
  - days_left: -3
- 解锁节点: []
- 关闭节点: []
- 新增 flag: [flag_alt]
- 移除 flag: []
- 即时后果: 测试后果B
- 长期影响: 测试影响B
- 教学反馈: 测试反馈B

#### 选项 C：测试选项C
- choice_id: ch01_C
- option_label: C
- 选项文本: 测试选项C
- 变量影响:
  - signed: +5
  - social_stability: -10
  - political_credit: +5
  - public_trust: -5
  - env_clue: +5
  - media_pressure: +5
  - budget: -300
  - days_left: -5
- 解锁节点: []
- 关闭节点: []
- 新增 flag: []
- 移除 flag: []
- 即时后果: 测试后果C
- 长期影响: 测试影响C
- 教学反馈: 测试反馈C

## 分支结果
### 结果 A
- node_id: ch01_result_A
- node_type: RESULT
- from_choice: ch01_A
- next: ch01_checkpoint
测试结果A

### 结果 B
- node_id: ch01_result_B
- node_type: RESULT
- from_choice: ch01_B
- next: ch01_checkpoint
测试结果B

### 结果 C
- node_id: ch01_result_C
- node_type: RESULT
- from_choice: ch01_C
- next: ch01_checkpoint
测试结果C

## 章节结算
- checkpoint_id: ch01_checkpoint
- node_type: CHECKPOINT
- merge_from:
  - ch01_result_A
  - ch01_result_B
  - ch01_result_C
- next_chapter: ch02

## 状态快照

### 变量范围（考虑本章所有可能路径）
| 变量 | 最低值 | 最高值 | 最可能路径 |
|---|---|---|---|
| signed | +1 | +5 | +3（选项 A） |
| social_stability | -10 | +5 | -5（选项 A） |
| political_credit | -5 | +5 | 0（选项 A） |
| public_trust | -5 | +10 | 0（选项 A） |
| env_clue | 0 | +15 | +15（选项 A） |
| media_pressure | 0 | +5 | 0（选项 A） |
| budget | -300 | -100 | -200（选项 A） |
| days_left | -7 | -3 | -7（选项 A） |

### 可能激活的 Flag 集合
- flag_test: 来自 ch01_A（始终激活）
- flag_alt: 来自 ch01_B（始终激活）

### 已解锁的后续节点
- ch02_info_test（需 flag_test）

### 已关闭的后续节点
（无）

### 章节总结
本章测试总结。
"""


# ============================================================
# Tests: Domain Models
# ============================================================

class TestDomainModels(unittest.TestCase):
    """测试领域模型。"""

    def test_chapter_state_snapshot_defaults(self):
        snap = ChapterStateSnapshot()
        assert snap.signed == 0
        assert snap.social_stability == 70
        assert snap.days_left == 90
        assert snap.active_flags == set()

    def test_chapter_creation(self):
        ch = Chapter(
            chapter_id="ch01", order=1, title="测试",
            day_range="第1-10天", core_task="任务", main_question="问题?",
        )
        assert ch.chapter_id == "ch01"
        assert ch.order == 1

    def test_chapter_option_default_effects(self):
        opt = ChapterOption(choice_id="ch01_A", option_label="A", text="测试")
        assert len(opt.effects) == 8
        assert opt.effects["signed"] == 0
        assert opt.npc_state_changes == {}

    def test_game_state_keeps_all_chapter_variables(self):
        from src.domain.game_state import GameState

        state = GameState(
            public_trust=41,
            env_clue=17,
            media_pressure=63,
            days_left=24,
        )

        assert state.public_trust == 41
        assert state.env_clue == 17
        assert state.media_pressure == 63
        assert state.days_left == 24


# ============================================================
# Tests: Prompt Builders
# ============================================================

class TestPromptBuilders(unittest.TestCase):
    """测试 Prompt 构建函数。"""

    def test_call1_prompt_structure(self):
        msgs = build_call1_prompt(
            scenario="生态搬迁", player_role="镇长",
            learning_goals=["目标1", "目标2"],
            chapter_count=6, ending_count=4,
        )
        assert len(msgs) == 1
        assert msgs[0].role == "user"
        assert "生态搬迁" in msgs[0].content
        assert "镇长" in msgs[0].content
        assert "6" in msgs[0].content  # chapter_count
        assert "4" in msgs[0].content  # ending_count
        assert "剧本制作包" in msgs[0].content
        assert "变量与机制表" in msgs[0].content
        assert "制作约束" in msgs[0].content
        # 不包含给建议的提示
        assert "不要给建议" in msgs[0].content or "不要在末尾添加任何建议" in msgs[0].content

    def test_call2_prompt_includes_settings(self):
        msgs = build_call2_prompt(SAMPLE_GAME_SETTINGS_MD)
        assert len(msgs) == 1
        assert "测试主题" in msgs[0].content
        assert "分支与状态追踪总表" in msgs[0].content
        assert "制作备注" in msgs[0].content
        assert "章节写作包" in msgs[0].content
        assert "global_facts_for_this_chapter" in msgs[0].content

    def test_call3_prompt_includes_all_sections(self):
        msgs = build_call3_prompt(
            game_settings_md=SAMPLE_GAME_SETTINGS_MD,
            chapter_outline_md=SAMPLE_OUTLINE_MD,
            current_chapter_num=1,
            total_chapters=3,
            current_chapter_outline_entry="## 第 1 章\n- chapter_id: ch01\n- core_task: 测试任务1",
            state_snapshot_text=build_initial_state_snapshot_text(),
            unlocked_nodes_text="（无）",
            locked_nodes_text="（无）",
            chapter_template_md=CHAPTER_TEMPLATE_MD,
            few_shot_example_md="示例章节",
        )
        assert len(msgs) == 1
        content = msgs[0].content
        assert "第 1 章" in content
        assert "当前章写作包" in content
        assert "测试任务1" in content
        assert "测试主题" not in content
        assert "示例章节" not in content
        assert "状态快照" in content
        assert "核心决策点" in content
        assert "NPC 状态变化" in content
        assert "制作备注" in content

    def test_custom_decision_point_count_reaches_all_creation_prompts(self):
        call1 = build_call1_prompt(
            scenario="生态搬迁",
            player_role="镇长",
            learning_goals=[],
            chapter_count=4,
            ending_count=3,
            decision_point_count=5,
        )[0].content
        call2 = build_call2_prompt(
            SAMPLE_GAME_SETTINGS_MD,
            decision_point_count=5,
        )[0].content
        call3 = build_call3_prompt(
            game_settings_md=SAMPLE_GAME_SETTINGS_MD,
            chapter_outline_md=SAMPLE_OUTLINE_MD,
            current_chapter_num=1,
            total_chapters=3,
            current_chapter_outline_entry="## 第 1 章",
            state_snapshot_text=build_initial_state_snapshot_text(),
            unlocked_nodes_text="（无）",
            locked_nodes_text="（无）",
            chapter_template_md=CHAPTER_TEMPLATE_MD,
            few_shot_example_md="示例章节",
            decision_point_count=5,
        )[0].content

        assert "每章决策点数: 5" in call1
        assert "恰好 5 个顺序执行" in call2
        assert "恰好 5 个顺序决策点" in call3

    def test_single_decision_point_removes_second_template_block(self):
        call2 = build_call2_prompt(
            SAMPLE_GAME_SETTINGS_MD,
            decision_point_count=1,
        )[0].content
        call3 = build_call3_prompt(
            game_settings_md=SAMPLE_GAME_SETTINGS_MD,
            chapter_outline_md=SAMPLE_OUTLINE_MD,
            current_chapter_num=1,
            total_chapters=3,
            current_chapter_outline_entry="## 第 1 章",
            state_snapshot_text=build_initial_state_snapshot_text(),
            unlocked_nodes_text="（无）",
            locked_nodes_text="（无）",
            chapter_template_md=CHAPTER_TEMPLATE_MD,
            few_shot_example_md="示例章节",
            decision_point_count=1,
        )[0].content

        assert "决策点 2：标题" not in call2
        assert "### 决策点 2：决策标题" not in call3
        assert "decision_point_count: 1" in call3

    def test_call4_prompt_uses_flash_format(self):
        msgs = build_call4_prompt(SAMPLE_CHAPTER_MD)
        assert len(msgs) == 2
        assert msgs[0].role == "system"
        assert msgs[1].role == "user"
        assert "事实连续性" in msgs[1].content
        assert "continuity_issues" in msgs[1].content
        assert "策略张力" in msgs[1].content
        assert "不要检查" in msgs[1].content

    def test_call5_prompt_uses_flash_format(self):
        msgs = build_call5_prompt(SAMPLE_CHAPTER_MD)
        assert len(msgs) == 2
        assert msgs[0].role == "system"
        assert "提取" in msgs[0].content
        assert "conditions" in msgs[1].content
        assert '"budget": ">= 1000"' in msgs[1].content

    def test_initial_state_snapshot_text(self):
        text = build_initial_state_snapshot_text()
        assert "signed" in text
        assert "social_stability" in text
        assert "当前 0" in text or "0" in text
        assert "（无）" in text  # no flags initially

    def test_state_snapshot_text_with_data(self):
        snap = ChapterStateSnapshot(
            signed=5, social_stability=65,
            active_flags={"flag_test", "flag_alt"},
            unlocked_nodes={"ch02_info_test"},
        )
        text = build_state_snapshot_text(snap)
        assert "signed" in text
        assert "flag_test" in text
        assert "ch02_info_test" in text


# ============================================================
# Tests: State Snapshot Extraction
# ============================================================

class TestStateSnapshotExtraction(unittest.TestCase):
    """测试从 Markdown 提取状态快照（使用生成器的方法）。"""

    @staticmethod
    def _make_generator():
        """创建一个最小化的 ChapterScriptGenerator 用于测试提取方法。"""
        from src.generation.chapter_script_generator import ChapterScriptGenerator
        # 不需要真实客户端
        gen = ChapterScriptGenerator.__new__(ChapterScriptGenerator)
        return gen

    def test_extract_state_snapshot_from_chapter(self):
        gen = self._make_generator()
        snap = gen._extract_state_snapshot(SAMPLE_CHAPTER_MD)

        assert snap.signed == 3  # most_likely from option A
        assert snap.social_stability == -5
        assert "flag_test" in snap.active_flags
        assert "flag_alt" in snap.active_flags
        assert "ch02_info_test" in snap.unlocked_nodes

    def test_extract_variable_ranges(self):
        gen = self._make_generator()
        snapshot_section = gen._extract_md_section(SAMPLE_CHAPTER_MD, "状态快照")
        ranges = gen._extract_variable_ranges(snapshot_section)

        assert "signed" in ranges
        assert ranges["signed"]["min"] == 1
        assert ranges["signed"]["max"] == 5
        assert ranges["signed"]["most_likely"] == 3

    def test_extract_flags(self):
        gen = self._make_generator()
        snapshot_section = gen._extract_md_section(SAMPLE_CHAPTER_MD, "状态快照")
        flags = gen._extract_flags(snapshot_section)

        assert "flag_test" in flags
        assert "flag_alt" in flags

    def test_accumulate_snapshot(self):
        gen = self._make_generator()
        prev = ChapterStateSnapshot(
            signed=0, active_flags={"flag_prev"},
            unlocked_nodes={"ch01_info"},
        )
        curr = ChapterStateSnapshot(
            signed=3, active_flags={"flag_curr"},
            unlocked_nodes={"ch02_info"},
        )
        acc = gen._accumulate_snapshot(prev, curr)

        assert acc.signed == 3  # current overwrites
        assert "flag_prev" in acc.active_flags  # preserved
        assert "flag_curr" in acc.active_flags  # added
        assert "ch01_info" in acc.unlocked_nodes  # preserved
        assert "ch02_info" in acc.unlocked_nodes  # added

    def test_extract_chapter_outline_entries(self):
        gen = self._make_generator()
        entries = gen._extract_chapter_outline_entries(SAMPLE_OUTLINE_MD, 3)
        assert len(entries) == 3
        assert "ch01" in entries[0]
        assert "ch02" in entries[1]
        assert "ch03" in entries[2]

    def test_chapter_has_required_sections(self):
        gen = self._make_generator()
        assert gen._chapter_has_required_sections(SAMPLE_CHAPTER_MD)

        bad_md = "# 第1章\n没有状态快照"
        assert not gen._chapter_has_required_sections(bad_md)

    def test_call3_saves_each_chapter_immediately(self):
        from pathlib import Path
        from src.generation.chapter_script_generator import ChapterScriptGenerator

        fake_pa = MagicMock()
        fake_pa.complete.return_value = SAMPLE_CHAPTER_MD
        fake_pa.reset_conversation = MagicMock()

        gen = ChapterScriptGenerator(
            pa_client=fake_pa,
            flash_client=MagicMock(),
        )
        with tempfile.TemporaryDirectory() as tmp:
            gen._output_dir = Path(tmp)
            chapters = gen._call3_generate_chapters(
                game_settings_md=SAMPLE_GAME_SETTINGS_MD,
                chapter_outline_md=SAMPLE_OUTLINE_MD,
                chapter_count=2,
            )

            assert len(chapters) == 2
            assert (Path(tmp) / "03_ch01.md").exists()
            assert (Path(tmp) / "03_ch02.md").exists()


# ============================================================
# Tests: Validator
# ============================================================

class TestChapterValidator(unittest.TestCase):
    """测试校验器。"""

    def test_empty_chapters(self):
        result = ChapterValidator.validate_programmatic({"chapters": [], "endings": []})
        assert result["valid"] is False
        assert any(i["code"] == "NO_CHAPTERS" for i in result["issues"])

    def test_valid_script(self):
        script = {
            "chapters": [
                {
                    "chapter_id": "ch01",
                    "decision_points": [
                        {
                            "node_id": "ch01_choice_1",
                            "order": 1,
                            "options": [
                                {"choice_id": "ch01_1A", "effects": {"signed": 1, "social_stability": 0, "political_credit": 0, "public_trust": 0, "env_clue": 0, "media_pressure": 0, "budget": 0, "days_left": 0}, "flags_added": ["flag_x"]},
                                {"choice_id": "ch01_1B", "effects": {"signed": 0, "social_stability": 1, "political_credit": 0, "public_trust": 0, "env_clue": 0, "media_pressure": 0, "budget": 0, "days_left": 0}, "flags_added": []},
                                {"choice_id": "ch01_1C", "effects": {"signed": 0, "social_stability": 0, "political_credit": 1, "public_trust": 0, "env_clue": 0, "media_pressure": 0, "budget": 0, "days_left": 0}, "flags_added": []},
                            ],
                        },
                        {
                            "node_id": "ch01_choice_2",
                            "order": 2,
                            "options": [
                                {"choice_id": "ch01_2A", "effects": {"signed": 0, "social_stability": 0, "political_credit": 0, "public_trust": 0, "env_clue": 0, "media_pressure": 0, "budget": 0, "days_left": 0}, "flags_added": []},
                                {"choice_id": "ch01_2B", "effects": {"signed": 0, "social_stability": 0, "political_credit": 0, "public_trust": 0, "env_clue": 0, "media_pressure": 0, "budget": 0, "days_left": -5}, "flags_added": []},
                                {"choice_id": "ch01_2C", "effects": {"signed": 0, "social_stability": 0, "political_credit": 0, "public_trust": 0, "env_clue": 0, "media_pressure": 0, "budget": 0, "days_left": 0}, "flags_added": []},
                            ],
                        },
                    ],
                    "checkpoint": {"checkpoint_id": "ch01_cp", "merge_from": ["ch01_result_2A"], "next_chapter": "ch02"},
                },
                {
                    "chapter_id": "ch02",
                    "decision_points": [
                        {
                            "node_id": "ch02_choice_1",
                            "order": 1,
                            "options": [
                                {"choice_id": "ch02_1A", "effects": {"signed": 0, "social_stability": 0, "political_credit": 0, "public_trust": 0, "env_clue": 0, "media_pressure": 0, "budget": 0, "days_left": 0}, "flags_added": []},
                                {"choice_id": "ch02_1B", "effects": {"signed": 0, "social_stability": 0, "political_credit": 0, "public_trust": 0, "env_clue": 0, "media_pressure": 0, "budget": 0, "days_left": 0}, "flags_added": []},
                                {"choice_id": "ch02_1C", "effects": {"signed": 0, "social_stability": 0, "political_credit": 0, "public_trust": 0, "env_clue": 0, "media_pressure": 0, "budget": 0, "days_left": 0}, "flags_added": []},
                            ],
                        },
                        {
                            "node_id": "ch02_choice_2",
                            "order": 2,
                            "options": [
                                {"choice_id": "ch02_2A", "effects": {"signed": 0, "social_stability": 0, "political_credit": 0, "public_trust": 0, "env_clue": 0, "media_pressure": 0, "budget": 0, "days_left": 0}, "flags_added": []},
                                {"choice_id": "ch02_2B", "effects": {"signed": 0, "social_stability": 0, "political_credit": 0, "public_trust": 0, "env_clue": 0, "media_pressure": 0, "budget": 0, "days_left": 0}, "flags_added": []},
                                {"choice_id": "ch02_2C", "effects": {"signed": 0, "social_stability": 0, "political_credit": 0, "public_trust": 0, "env_clue": 0, "media_pressure": 0, "budget": 0, "days_left": 0}, "flags_added": []},
                            ],
                        },
                    ],
                    "checkpoint": {"checkpoint_id": "ch02_cp", "merge_from": ["ch02_result_2A"], "next_chapter": "ending_evaluation"},
                },
            ],
            "endings": [
                {"ending_id": "end_01", "type": "good", "conditions": {"variables": {"signed": ">= 30"}}},
                {"ending_id": "end_02", "type": "neutral", "conditions": {"flags_required": ["flag_x"]}},
                {"ending_id": "end_03", "type": "bad", "conditions": {"variables": {"social_stability": "< 30"}}},
            ],
        }
        result = ChapterValidator.validate_programmatic(script)
        assert result["valid"] is True
        assert result["error_count"] == 0

    def test_chain_broken_detection(self):
        script = {
            "chapters": [
                {
                    "chapter_id": "ch01",
                    "decision_point": {"options": [
                        {"choice_id": "ch01_A", "effects": {"signed": 0, "social_stability": 0, "political_credit": 0, "public_trust": 0, "env_clue": 0, "media_pressure": 0, "budget": 0, "days_left": 0}, "flags_added": []},
                        {"choice_id": "ch01_B", "effects": {"signed": 0, "social_stability": 0, "political_credit": 0, "public_trust": 0, "env_clue": 0, "media_pressure": 0, "budget": 0, "days_left": 0}, "flags_added": []},
                        {"choice_id": "ch01_C", "effects": {"signed": 0, "social_stability": 0, "political_credit": 0, "public_trust": 0, "env_clue": 0, "media_pressure": 0, "budget": 0, "days_left": 0}, "flags_added": []},
                    ]},
                    "checkpoint": {"next_chapter": "ch03"},  # should be ch02
                },
                {
                    "chapter_id": "ch02",
                    "decision_point": {"options": [
                        {"choice_id": "ch02_A", "effects": {"signed": 0, "social_stability": 0, "political_credit": 0, "public_trust": 0, "env_clue": 0, "media_pressure": 0, "budget": 0, "days_left": 0}, "flags_added": []},
                        {"choice_id": "ch02_B", "effects": {"signed": 0, "social_stability": 0, "political_credit": 0, "public_trust": 0, "env_clue": 0, "media_pressure": 0, "budget": 0, "days_left": 0}, "flags_added": []},
                        {"choice_id": "ch02_C", "effects": {"signed": 0, "social_stability": 0, "political_credit": 0, "public_trust": 0, "env_clue": 0, "media_pressure": 0, "budget": 0, "days_left": 0}, "flags_added": []},
                    ]},
                },
            ],
            "endings": [{"ending_id": "end_01", "type": "good"}, {"ending_id": "end_02", "type": "bad"}],
        }
        result = ChapterValidator.validate_programmatic(script)
        assert any(i["code"] == "CHAIN_BROKEN" for i in result["issues"])

    def test_orphan_flag_detection(self):
        script = {
            "chapters": [
                {
                    "chapter_id": "ch01",
                    "decision_point": {"options": [
                        {"choice_id": "ch01_A", "effects": {"signed": 0, "social_stability": 0, "political_credit": 0, "public_trust": 0, "env_clue": 0, "media_pressure": 0, "budget": 0, "days_left": 0}, "flags_added": []},
                        {"choice_id": "ch01_B", "effects": {"signed": 0, "social_stability": 0, "political_credit": 0, "public_trust": 0, "env_clue": 0, "media_pressure": 0, "budget": 0, "days_left": 0}, "flags_added": []},
                        {"choice_id": "ch01_C", "effects": {"signed": 0, "social_stability": 0, "political_credit": 0, "public_trust": 0, "env_clue": 0, "media_pressure": 0, "budget": 0, "days_left": 0}, "flags_added": []},
                    ]},
                    "info_nodes": [
                        {"unlock_condition": {"flags_required": ["flag_never_created"]}}
                    ],
                },
            ],
            "endings": [{"ending_id": "end_01", "type": "good"}, {"ending_id": "end_02", "type": "bad"}],
        }
        result = ChapterValidator.validate_programmatic(script)
        assert any(i["code"] == "ORPHAN_FLAGS" for i in result["issues"])

    def test_flag_lifecycle_includes_chapter_text_nodes_and_endings(self):
        script = {
            "chapters": [
                {
                    "chapter_id": "ch01",
                    "decision_point": {"node_id": "ch01_dp", "options": [{
                        "choice_id": "ch01_A",
                        "flags_added": ["flag_style_consultation"],
                    }]},
                },
                {
                    "chapter_id": "ch02",
                    "background": "[若 flag_style_consultation]信任生效[/若]",
                    "info_nodes": [{
                        "node_id": "ch02_info",
                        "unlock_condition": {"flags_required": ["flag_style_consultation"]},
                    }],
                },
            ],
            "endings": [{
                "ending_id": "ending_01",
                "conditions": {"flags_required": ["flag_style_consultation"]},
            }],
        }

        lifecycle = ChapterValidator.build_flag_lifecycle(script)["flag_style_consultation"]

        assert lifecycle["created_at"] == [
            "chapters/ch01/decision_points/ch01_dp/options/ch01_A/flags_added"
        ]
        assert "chapters/ch02/background" in lifecycle["referenced_at"]
        assert any("ch02_info" in path for path in lifecycle["referenced_at"])
        assert lifecycle["used_by_endings"] == [
            "endings/ending_01/conditions/flags_required"
        ]

    def test_numeric_ending_condition_is_rejected(self):
        script = {
            "chapters": [{
                "chapter_id": "ch01",
                "decision_point": {"options": [
                    {"choice_id": "A", "effects": {name: 0 for name in ChapterValidator.EXPECTED_VARIABLES}},
                    {"choice_id": "B", "effects": {name: 0 for name in ChapterValidator.EXPECTED_VARIABLES}},
                    {"choice_id": "C", "effects": {name: 0 for name in ChapterValidator.EXPECTED_VARIABLES}},
                ]},
            }],
            "endings": [
                {"ending_id": "end_01", "type": "good", "conditions": {"variables": {"budget": 1000}}},
                {"ending_id": "end_02", "type": "neutral", "conditions": {"variables": {"signed": ">= 10"}}},
                {"ending_id": "end_03", "type": "bad", "conditions": {"variables": {"signed": "< 10"}}},
            ],
        }

        result = ChapterValidator.validate_programmatic(script)

        assert any(i["code"] == "INVALID_ENDING_VARIABLE_CONDITION" for i in result["issues"])


    def test_too_few_options(self):
        script = {
            "chapters": [{
                "chapter_id": "ch01",
                "decision_point": {"options": [
                    {"choice_id": "ch01_A", "effects": {"signed": 0, "social_stability": 0, "political_credit": 0, "public_trust": 0, "env_clue": 0, "media_pressure": 0, "budget": 0, "days_left": 0}, "flags_added": []},
                ]},
            }],
            "endings": [{"ending_id": "end_01", "type": "good"}, {"ending_id": "end_02", "type": "bad"}],
        }
        result = ChapterValidator.validate_programmatic(script)
        issue = next(i for i in result["issues"] if i["code"] == "TOO_FEW_OPTIONS")
        assert issue["severity"] == "warning"

    def test_too_few_endings(self):
        script = {
            "chapters": [{
                "chapter_id": "ch01",
                "decision_point": {"options": [
                    {"choice_id": "ch01_A", "effects": {"signed": 0, "social_stability": 0, "political_credit": 0, "public_trust": 0, "env_clue": 0, "media_pressure": 0, "budget": 0, "days_left": 0}, "flags_added": []},
                    {"choice_id": "ch01_B", "effects": {"signed": 0, "social_stability": 0, "political_credit": 0, "public_trust": 0, "env_clue": 0, "media_pressure": 0, "budget": 0, "days_left": 0}, "flags_added": []},
                    {"choice_id": "ch01_C", "effects": {"signed": 0, "social_stability": 0, "political_credit": 0, "public_trust": 0, "env_clue": 0, "media_pressure": 0, "budget": 0, "days_left": 0}, "flags_added": []},
                ]},
            }],
            "endings": [{"ending_id": "end_01", "type": "good"}],
        }
        result = ChapterValidator.validate_programmatic(script)
        codes = {i["code"] for i in result["issues"]}
        assert "TOO_FEW_ENDINGS" in codes

    def test_programmatic_validation_does_not_limit_variable_delta_size(self):
        script = {
            "chapters": [{
                "chapter_id": "ch01",
                "decision_point": {"options": [
                    {"choice_id": "budget_normal", "effects": {"signed": 0, "social_stability": 0, "political_credit": 0, "public_trust": 0, "env_clue": 0, "media_pressure": 0, "budget": -800, "days_left": 0}, "flags_added": []},
                    {"choice_id": "score_large", "effects": {"signed": 0, "social_stability": 30, "political_credit": 0, "public_trust": 0, "env_clue": 0, "media_pressure": 0, "budget": 0, "days_left": 0}, "flags_added": []},
                    {"choice_id": "neutral", "effects": {"signed": 0, "social_stability": 0, "political_credit": 0, "public_trust": 0, "env_clue": 0, "media_pressure": 0, "budget": 0, "days_left": 0}, "flags_added": []},
                ]},
                "checkpoint": {"checkpoint_id": "ch01_cp", "merge_from": ["neutral"], "next_chapter": "ending_evaluation"},
            }],
            "endings": [
                {"ending_id": "end_01", "type": "good", "conditions": {"variables": {"signed": ">= 30"}}},
                {"ending_id": "end_02", "type": "neutral", "conditions": {"flags_required": ["flag_x"]}},
                {"ending_id": "end_03", "type": "bad", "conditions": {"variables": {"social_stability": "< 30"}}},
            ],
        }

        result = ChapterValidator.validate_programmatic(script)
        codes = {issue["code"] for issue in result["issues"]}

        assert "LARGE_VARIABLE_CHANGE" not in codes

    def test_production_validation_rejects_unknown_npc_state_change(self):
        script = {
            "npcs": [{
                "npc_id": "npc_01", "name": "测试 NPC", "npc_type": "villager",
            }],
            "chapters": [{
                "chapter_id": "ch01",
                "decision_points": [{
                    "node_id": "ch01_dp01",
                    "options": [
                        {"choice_id": "ch01_A", "effects": {"signed": 0, "social_stability": 0, "political_credit": 0, "public_trust": 0, "env_clue": 0, "media_pressure": 0, "budget": 0, "days_left": 0}, "npc_state_changes": {"npc_missing": {}}},
                        {"choice_id": "ch01_B", "effects": {"signed": 0, "social_stability": 0, "political_credit": 0, "public_trust": 0, "env_clue": 0, "media_pressure": 0, "budget": 0, "days_left": 0}, "npc_state_changes": {}},
                        {"choice_id": "ch01_C", "effects": {"signed": 0, "social_stability": 0, "political_credit": 0, "public_trust": 0, "env_clue": 0, "media_pressure": 0, "budget": 0, "days_left": 0}, "npc_state_changes": {}},
                    ],
                }],
                "checkpoint": {"checkpoint_id": "ch01_cp", "merge_from": ["ch01_A"], "next_chapter": "ending_evaluation"},
            }],
            "endings": [
                {"ending_id": "end_01", "type": "good"},
                {"ending_id": "end_02", "type": "neutral"},
                {"ending_id": "end_03", "type": "bad"},
            ],
        }

        result = ChapterValidator.validate_programmatic(script, expected_npc_count=1)

        assert any(i["code"] == "UNKNOWN_NPC_STATE_CHANGE" for i in result["issues"])

    def test_normalizes_extracted_npc_state_change_keys(self):
        from src.generation.chapter_script_generator import ChapterScriptGenerator

        global_json = {
            "npcs": [
                {
                    "npc_id": "NPC-01",
                    "name": "陈建国",
                    "group": "村干部",
                    "npc_type": "cadre",
                    "trust_to_player": 20,
                    "attitude_score": 30,
                    "anxiety_level": 40,
                    "granovetter_threshold": 50,
                },
                {
                    "npc_id": "NPC-03",
                    "name": "王宗福",
                    "group": "承包商",
                    "npc_type": "external",
                    "trust_to_player": 20,
                    "attitude_score": 30,
                    "anxiety_level": 40,
                    "granovetter_threshold": 50,
                },
            ],
            "endings": [
                {"ending_id": "end_01", "type": "good"},
                {"ending_id": "end_02", "type": "neutral"},
                {"ending_id": "end_03", "type": "bad"},
            ],
        }
        chapters = [{
            "chapter_id": "ch01",
            "decision_points": [{
                "node_id": "ch01_dp01",
                "options": [
                    {
                        "choice_id": "ch01_A",
                        "effects": {"signed": 0, "social_stability": 0, "political_credit": 0, "public_trust": 0, "env_clue": 0, "media_pressure": 0, "budget": 0, "days_left": 0},
                        "npc_state_changes": {"陈建国": "trust+5", "wang_zongfu": {"anxiety_level": 10}, "trust": "+3"},
                    },
                    {"choice_id": "ch01_B", "effects": {"signed": 0, "social_stability": 0, "political_credit": 0, "public_trust": 0, "env_clue": 0, "media_pressure": 0, "budget": 0, "days_left": 0}, "npc_state_changes": {}},
                    {"choice_id": "ch01_C", "effects": {"signed": 0, "social_stability": 0, "political_credit": 0, "public_trust": 0, "env_clue": 0, "media_pressure": 0, "budget": 0, "days_left": 0}, "npc_state_changes": {}},
                ],
            }],
            "checkpoint": {"checkpoint_id": "ch01_cp", "merge_from": ["ch01_A"], "next_chapter": "ending_evaluation"},
        }]

        ChapterScriptGenerator._normalize_extracted_structure(global_json, chapters)
        script = dict(global_json)
        script["chapters"] = chapters
        result = ChapterValidator.validate_programmatic(script, expected_npc_count=2)

        changes = chapters[0]["decision_points"][0]["options"][0]["npc_state_changes"]
        assert set(changes) == {"NPC-01", "NPC-03"}
        assert global_json["_extraction_normalization"]["npc_state_changes"]["mapped_count"] == 2
        assert global_json["_extraction_normalization"]["npc_state_changes"]["dropped_count"] == 1
        assert not any(i["code"] == "UNKNOWN_NPC_STATE_CHANGE" for i in result["issues"])

    def test_chapter_extraction_view_compacts_long_prose(self):
        from src.generation.chapter_script_generator import ChapterScriptGenerator

        prose = "普通叙事。\n" * 6000
        chapter_md = f"""# 第 1 章

## 开场叙事
{prose}

### 玩家行动选项
**选项A：如实说明**
- 变量变化：public_trust+5

### 状态快照
| 变量 | 最小 | 最大 | 最可能 |
| signed | 0 | 1 | 0 |
"""

        view = ChapterScriptGenerator._build_chapter_extraction_view(chapter_md)

        assert len(view) < len(chapter_md)
        assert "玩家行动选项" in view
        assert "选项A" in view
        assert "状态快照" in view
        assert "普通叙事" not in view[:1000]

    def test_long_chapter_extraction_is_split_into_cacheable_slices(self):
        from src.generation.chapter_script_generator import ChapterScriptGenerator

        blocks = []
        for index in range(1, 8):
            blocks.append(
                f"### 玩家行动选项 {index}\n"
                f"**选项A：方案{index}A**\n- 变量变化：public_trust+{index}\n"
                f"**选项B：方案{index}B**\n- 变量变化：budget-{index}\n"
                + ("叙事补充。\n" * 1100)
            )
        chapter_md = "# 第 1 章\n- chapter_id: ch01\n\n" + "\n\n".join(blocks)

        slices = ChapterScriptGenerator._build_chapter_extraction_slices(
            chapter_md,
            max_chars=7000,
        )

        assert len(slices) > 1
        assert all(len(item) <= 7600 for item in slices)
        assert all("chapter_id: ch01" in item for item in slices)
        assert any("玩家行动选项 7" in item for item in slices)

    def test_merges_chapter_extract_parts_without_duplicates(self):
        from src.generation.chapter_script_generator import ChapterScriptGenerator

        merged = ChapterScriptGenerator._merge_chapter_extract_parts("ch01", [
            {
                "chapter_id": "ch01",
                "title": "第一章",
                "learning_goals": ["目标A"],
                "decision_points": [{"node_id": "dp01", "options": []}],
                "results": [{"node_id": "r01", "text": "结果"}],
                "checkpoint": {},
            },
            {
                "chapter_id": "ch01",
                "title": "",
                "learning_goals": ["目标A", "目标B"],
                "decision_points": [
                    {"node_id": "dp01", "options": []},
                    {"node_id": "dp02", "options": []},
                ],
                "results": [{"node_id": "r02", "text": "结果2"}],
                "checkpoint": {"checkpoint_id": "cp01", "next_chapter": "ch02"},
            },
        ])

        assert merged["title"] == "第一章"
        assert merged["learning_goals"] == ["目标A", "目标B"]
        assert [item["node_id"] for item in merged["decision_points"]] == ["dp01", "dp02"]
        assert merged["checkpoint"]["checkpoint_id"] == "cp01"


class TestValidationPrompts(unittest.TestCase):
    def test_call5a_requires_ending_comparison_operators(self):
        prompt = build_call5a_prompt("budget >= 1000", ["ch01"])[1].content

        assert '"budget": ">= 1000"' in prompt
        assert "禁止把 \"budget >= 1000\" 抽取成裸数字 1000" in prompt
        assert "flags_required_any" in prompt
        assert "禁止把“或”抽成“且”" in prompt

    def test_ending_condition_flattening_preserves_boolean_logic(self):
        from src.generation.chapter_script_generator import ChapterScriptGenerator

        lines = ChapterScriptGenerator._flatten_ending_conditions({
            "variable_expression": "stability < 30 OR pressure > 90",
            "flags_required_any": ["flag_a", "flag_b"],
            "flags_forbidden": ["flag_c"],
        })

        assert "变量条件: stability < 30 OR pressure > 90" in lines
        assert "至少具备一个 flag: flag_a, flag_b" in lines
        assert "不得有 flag: flag_c" in lines

    def test_call5b_slice_prompt_limits_scope_to_current_slice(self):
        prompt = build_call5b_slice_prompt("### 玩家行动选项", "ch01", 1, 2, 4)[1].content

        assert "结构片段 2/4" in prompt
        assert "不要补全其他片段" in prompt
        assert "checkpoint 只有在本片段出现" in prompt

    def test_outdated_validation_report_is_not_reused(self):
        from pathlib import Path
        from src.generation.chapter_script_generator import ChapterScriptGenerator

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "07_validation_report.json"
            path.write_text(json.dumps({"validation_version": 1}), encoding="utf-8")
            gen = ChapterScriptGenerator()
            gen._output_dir = Path(tmp)
            call_fn = MagicMock(return_value={"validation_version": 2})

            result = gen._load_or_call_json(
                "07_validation_report.json",
                call_fn=call_fn,
                label="Call 6/6: 校验",
                stage=8,
                reuse_if=lambda report: report.get("validation_version") == 2,
            )

            call_fn.assert_called_once_with()
            assert result["validation_version"] == 2

    def test_json_artifact_is_invalidated_when_dependency_changes(self):
        from pathlib import Path
        from src.generation.chapter_script_generator import ChapterScriptGenerator

        with tempfile.TemporaryDirectory() as tmp:
            gen = ChapterScriptGenerator()
            gen._output_dir = Path(tmp)
            first_call = MagicMock(return_value={"value": 1})
            second_call = MagicMock(return_value={"value": 2})

            first = gen._load_or_call_json(
                "artifact.json",
                call_fn=first_call,
                label="测试产物",
                stage=1,
                depends_on_content="source-a",
            )
            reused = gen._load_or_call_json(
                "artifact.json",
                call_fn=MagicMock(return_value={"value": 9}),
                label="测试产物",
                stage=1,
                depends_on_content="source-a",
            )
            rebuilt = gen._load_or_call_json(
                "artifact.json",
                call_fn=second_call,
                label="测试产物",
                stage=1,
                depends_on_content="source-b",
            )

            self.assertEqual({"value": 1}, first)
            self.assertEqual({"value": 1}, reused)
            self.assertEqual({"value": 2}, rebuilt)
            first_call.assert_called_once_with()
            second_call.assert_called_once_with()

    def test_json_artifact_input_index_preserves_concurrent_updates(self):
        from concurrent.futures import ThreadPoolExecutor
        from pathlib import Path
        from src.generation.chapter_script_generator import ChapterScriptGenerator

        with tempfile.TemporaryDirectory() as tmp:
            gen = ChapterScriptGenerator()
            gen._output_dir = Path(tmp)

            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = [
                    executor.submit(
                        gen._load_or_call_json,
                        f"artifact-{index}.json",
                        lambda value=index: {"value": value},
                        f"测试产物 {index}",
                        1,
                        None,
                        f"source-{index}",
                    )
                    for index in range(4)
                ]
                for future in futures:
                    future.result()

            inputs = json.loads(
                (Path(tmp) / ".artifact_inputs.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                {f"artifact-{index}.json" for index in range(4)},
                set(inputs),
            )


class TestValidationPolicy(unittest.TestCase):
    def test_expected_decision_point_count_is_enforced(self):
        effects = {name: 0 for name in ChapterValidator.EXPECTED_VARIABLES}
        script = {
            "chapters": [{
                "chapter_id": "ch01",
                "decision_points": [{
                    "node_id": "ch01_dp",
                    "options": [
                        {"choice_id": "A", "effects": effects},
                        {"choice_id": "B", "effects": effects},
                        {"choice_id": "C", "effects": effects},
                    ],
                }],
            }],
            "npcs": [],
            "endings": [],
        }

        report = ChapterValidator.validate_programmatic(
            script,
            expected_npc_count=0,
            expected_decision_point_count=3,
        )

        assert report["valid"] is False
        assert any(
            issue["code"] == "DECISION_POINT_COUNT_MISMATCH"
            for issue in report["issues"]
        )

    def test_expected_ending_count_is_enforced(self):
        effects = {name: 0 for name in ChapterValidator.EXPECTED_VARIABLES}
        script = {
            "chapters": [{
                "chapter_id": "ch01",
                "decision_points": [{
                    "node_id": "ch01_dp",
                    "options": [
                        {"choice_id": "A", "effects": effects},
                        {"choice_id": "B", "effects": effects},
                        {"choice_id": "C", "effects": effects},
                    ],
                }],
            }],
            "npcs": [],
            "endings": [
                {"ending_id": "end_01", "type": "good", "conditions": {"variables": {"signed": ">= 10"}}},
                {"ending_id": "end_02", "type": "neutral", "conditions": {"variables": {"signed": ">= 5"}}},
                {"ending_id": "end_03", "type": "bad", "conditions": {"variables": {"signed": "< 5"}}},
            ],
        }

        report = ChapterValidator.validate_programmatic(
            script,
            expected_npc_count=0,
            expected_ending_count=4,
        )

        assert report["valid"] is False
        assert any(
            issue["code"] == "ENDING_COUNT_MISMATCH"
            for issue in report["issues"]
        )

    def test_source_invariants_detect_household_total_conflict(self):
        source = """# 《三十六签》

雀林村共有三十六户农家。

## 第八章
- signed = 38
全村三十八户，全部签约完成。
"""

        report = ChapterValidator.validate_source_invariants(source)

        assert report["valid"] is False
        assert any(
            issue["code"] == "HOUSEHOLD_TOTAL_CONFLICT"
            for issue in report["issues"]
        )

    def test_continuity_review_does_not_control_valid(self):
        from src.generation.chapter_script_generator import ChapterScriptGenerator

        effects = {name: 0 for name in ChapterValidator.EXPECTED_VARIABLES}
        script = {
            "npcs": [],
            "chapters": [{
                "chapter_id": "ch01",
                "decision_point": {"node_id": "ch01_dp", "options": [
                    {"choice_id": "A", "effects": effects},
                    {"choice_id": "B", "effects": effects},
                    {"choice_id": "C", "effects": effects},
                ]},
            }],
            "endings": [
                {"ending_id": "end_01", "type": "good", "conditions": {"variables": {"signed": ">= 10"}}},
                {"ending_id": "end_02", "type": "neutral", "conditions": {"variables": {"signed": ">= 5"}}},
                {"ending_id": "end_03", "type": "bad", "conditions": {"variables": {"signed": "< 5"}}},
            ],
        }
        gen = ChapterScriptGenerator.__new__(ChapterScriptGenerator)
        report = gen._call6_validate(
            script,
            expected_npc_count=0,
            continuity_review={
                "status": "review_recommended",
                "continuity_issues": [{"code": "CONTINUITY_001"}],
            },
        )

        assert report["valid"] is True
        assert report["error_count"] == 0
        assert report["continuity_review"]["status"] == "review_recommended"

    def test_extraction_fidelity_detects_missing_choice(self):
        source = """- chapter_id: ch01
- choice_id: ch01_A
- choice_id: ch01_B
- **npc_id:** npc_01
- **ending_id:** ending_01
"""
        script = {
            "chapters": [{
                "chapter_id": "ch01",
                "decision_point": {"options": [{"choice_id": "ch01_A"}]},
            }],
            "npcs": [{"npc_id": "npc_01"}],
            "endings": [{"ending_id": "ending_01"}],
        }

        report = ChapterValidator.validate_extraction_fidelity(script, source)

        assert report["valid"] is False
        assert any(issue["code"] == "EXTRACTION_MISSING_CHOICE_IDS" for issue in report["issues"])


# ============================================================
# Tests: Integration
# ============================================================

class TestFullPipeline(unittest.TestCase):
    """集成测试：使用 FakeClient 验证完整 6-Call 管线。"""

    def _make_fake_pa_client(self):
        """创建一个返回预定义 Markdown 的 Fake PA Backend 客户端。"""
        client = MagicMock()
        client.complete.return_value = SAMPLE_CHAPTER_MD
        client.reset_conversation = MagicMock()
        return client

    def _make_fake_flash_client(self):
        """创建一个返回预定义 JSON 的 Fake Qwen Flash 客户端。"""
        client = MagicMock()
        client.complete.return_value = json.dumps({
            "title": "测试剧本",
            "chapters": [],
            "endings": [],
        }, ensure_ascii=False)
        return client

    def test_generate_full_with_fakes(self):
        """验证完整管线可以走通（使用 FakeClient）。"""
        from src.generation.chapter_script_generator import ChapterScriptGenerator
        from src.domain.script_design import ScriptGenerationRequest

        fake_pa = self._make_fake_pa_client()
        fake_flash = self._make_fake_flash_client()

        gen = ChapterScriptGenerator(
            pa_client=fake_pa,
            flash_client=fake_flash,
        )

        request = ScriptGenerationRequest(
            scenario="测试场景",
            player_role="测试角色",
            learning_goal="测试目标",
            chapter_count=3,
            ending_count=3,
        )

        script_design, complete_md = gen.generate_full(request)

        assert script_design is not None
        assert script_design.title == "测试剧本"
        assert complete_md is not None
        assert len(complete_md) > 0

        # 验证 Call 1-3 调用了 PA Backend（每章 reset + complete）
        assert fake_pa.complete.call_count >= 5  # Call1 + Call2 + 3 chapters

        # 验证 Call 4-5 调用了 Qwen Flash
        assert fake_flash.complete.call_count >= 2

    def test_generate_full_progress_callback(self):
        """验证进度回调被正确调用。"""
        from src.generation.chapter_script_generator import ChapterScriptGenerator
        from src.domain.script_design import ScriptGenerationRequest

        fake_pa = self._make_fake_pa_client()
        fake_flash = self._make_fake_flash_client()

        gen = ChapterScriptGenerator(
            pa_client=fake_pa,
            flash_client=fake_flash,
        )

        progress_calls = []
        def track_progress(stage, total, name, request_bytes):
            progress_calls.append((stage, total, name))

        request = ScriptGenerationRequest(
            scenario="测试", player_role="测试", learning_goal="测试",
            chapter_count=3, ending_count=3,
        )

        gen.generate_full(request, progress_callback=track_progress)

        # 至少应该有 Call 1, Call 2, Call 4, Call 5, Call 6 的进度
        assert len(progress_calls) >= 5
        stages = {c[0] for c in progress_calls}
        assert 1 in stages  # Call 1
        assert 2 in stages  # Call 2
        assert 8 in stages  # 完成

    def test_call5_excludes_continuity_review(self):
        from src.generation.chapter_script_generator import ChapterScriptGenerator
        from src.domain.script_design import ScriptGenerationRequest

        fake_pa = self._make_fake_pa_client()
        review = {
            "status": "review_recommended",
            "continuity_issues": [{
                "code": "CONTINUITY_001",
                "message": "测试矛盾",
                "source_a": {"location": "ch01", "quote": "证据 A"},
                "source_b": {"location": "ch02", "quote": "证据 B"},
            }],
        }
        extracted = {"title": "测试剧本", "chapters": [], "endings": [], "npcs": []}
        extracted_chapter = {
            "chapter_id": "ch01",
            "decision_points": [],
            "info_nodes": [],
            "results": [],
            "checkpoint": {},
        }
        fake_flash = MagicMock()
        fake_flash.complete.side_effect = [
            json.dumps(review, ensure_ascii=False),
            json.dumps(extracted, ensure_ascii=False),
            json.dumps(extracted_chapter, ensure_ascii=False),
            json.dumps(extracted_chapter, ensure_ascii=False),
            json.dumps(extracted_chapter, ensure_ascii=False),
        ]
        gen = ChapterScriptGenerator(pa_client=fake_pa, flash_client=fake_flash)
        request = ScriptGenerationRequest(
            scenario="测试", player_role="测试", learning_goal="测试",
            chapter_count=3, ending_count=3,
        )

        _, full_md = gen.generate_full(request)

        call5_messages = fake_flash.complete.call_args_list[1].args[0]
        assert "测试矛盾" not in call5_messages[1].content
        assert "测试矛盾" not in full_md

    def test_build_script_design_keeps_npcs_endings_and_initial_state(self):
        from src.generation.chapter_script_generator import ChapterScriptGenerator

        gen = ChapterScriptGenerator.__new__(ChapterScriptGenerator)
        script = gen._build_script_design({
            "title": "测试剧本",
            "player_role": "测试角色",
            "core_conflict": "测试冲突",
            "initial_state": {
                "signed": 2, "social_stability": 61, "political_credit": 55,
                "public_trust": 44, "env_clue": 12, "media_pressure": 38,
                "budget": 7000, "days_left": 45,
            },
            "npcs": [{
                "npc_id": "npc_01", "name": "张三", "npc_type": "villager", "group": "村民",
                "trust_to_player": 40, "attitude_score": 35, "anxiety_level": 60,
                "reference_point": 0, "granovetter_threshold": 50,
            }],
            "chapters": [],
            "endings": [{
                "ending_id": "ending_01", "title": "稳步推进", "type": "good",
                "description": "测试描述",
                "conditions": {"variables": {"signed": ">= 30"}, "flags_required": ["flag_open"]},
                "ending_text": "结局正文", "teaching_summary": "教学复盘",
            }],
        })

        assert len(script.chapter_npcs) == 1
        assert len(script.npc_seed) == 1
        assert len(script.endings) == 1
        assert len(script.chapter_endings) == 1
        assert script.endings[0].ending_type == "good"
        assert script.initial_game_state.public_trust == 44
        assert script.initial_game_state.env_clue == 12
        assert script.initial_game_state.media_pressure == 38
        assert script.initial_game_state.days_left == 45


# ============================================================
# Tests: Regression - Chapter Request Shape
# ============================================================

class TestChapterRequestShape(unittest.TestCase):
    """确保章节式请求和结构字段可用。"""

    def test_script_design_supports_chapters(self):
        from src.domain.script_design import ScriptDesign
        from src.domain.game_state import GameState

        script = ScriptDesign(
            title="新", premise="", player_role="", core_conflict="",
            initial_game_state=GameState(),
            chapters=[],
        )
        assert script.chapters is not None

    def test_script_generation_request_has_new_fields(self):
        from src.domain.script_design import ScriptGenerationRequest

        req = ScriptGenerationRequest(
            scenario="测试", player_role="测试",
            chapter_count=8, ending_count=5, decision_point_count=6,
        )
        assert req.chapter_count == 8
        assert req.ending_count == 5
        assert req.decision_point_count == 6

        # 默认值
        req_default = ScriptGenerationRequest(scenario="测试", player_role="测试")
        assert req_default.chapter_count == 6
        assert req_default.ending_count == 4
        assert req_default.decision_point_count == 3
