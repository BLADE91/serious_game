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

    def test_call3_prompt_includes_all_sections(self):
        msgs = build_call3_prompt(
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
        )
        assert len(msgs) == 1
        content = msgs[0].content
        assert "第 1 章" in content
        assert "测试主题" in content
        assert "状态快照" in content
        assert "NPC 状态变化" in content
        assert "制作备注" in content

    def test_call4_prompt_uses_flash_format(self):
        msgs = build_call4_prompt(SAMPLE_CHAPTER_MD)
        assert len(msgs) == 2
        assert msgs[0].role == "system"
        assert msgs[1].role == "user"
        assert "质量评分表" in msgs[1].content
        assert "8 项质量评分标准" in msgs[1].content
        assert "必改项" in msgs[1].content

    def test_call5_prompt_uses_flash_format(self):
        msgs = build_call5_prompt(SAMPLE_CHAPTER_MD)
        assert len(msgs) == 2
        assert msgs[0].role == "system"
        assert "提取" in msgs[0].content
        assert "npc_state_changes" in msgs[1].content

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
                {"ending_id": "end_01", "type": "good"},
                {"ending_id": "end_02", "type": "neutral"},
                {"ending_id": "end_03", "type": "bad"},
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
        assert any(i["code"] == "TOO_FEW_OPTIONS" for i in result["issues"])

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


# ============================================================
# Tests: Regression - Backward Compatibility
# ============================================================

class TestBackwardCompatibility(unittest.TestCase):
    """确保旧管线不受影响。"""

    def test_script_design_supports_both_acts_and_chapters(self):
        from src.domain.script_design import ScriptDesign
        from src.domain.game_state import GameState

        # 旧式（3-act）
        old = ScriptDesign(
            title="旧", premise="", player_role="", core_conflict="",
            initial_game_state=GameState(),
            acts=[],  # 3-act field
        )
        assert old.acts is not None

        # 新式（chapters）
        new = ScriptDesign(
            title="新", premise="", player_role="", core_conflict="",
            initial_game_state=GameState(),
            chapters=[],  # new field
        )
        assert new.chapters is not None

    def test_script_generation_request_has_new_fields(self):
        from src.domain.script_design import ScriptGenerationRequest

        req = ScriptGenerationRequest(
            scenario="测试", player_role="测试",
            chapter_count=8, ending_count=5,
        )
        assert req.chapter_count == 8
        assert req.ending_count == 5

        # 默认值
        req_default = ScriptGenerationRequest(scenario="测试", player_role="测试")
        assert req_default.chapter_count == 6
        assert req_default.ending_count == 4
