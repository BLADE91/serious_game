"""6-Call 章节式剧本生成器。

管线：
  Call 1: PA Backend → game_settings.md（全局设定）
  Call 2: PA Backend → chapter_outline.md（章节大纲）
  Call 3: PA Backend ×N → ch01.md ~ ch0N.md（逐章生成）
  Call 4: Qwen Flash → 审查并修复源 Markdown，输出 continuity_review.json
  Call 5: Qwen Flash → script_structure.json（JSON 抽取）
  Call 6: 程序规则 → validation_report.json（结构与提取校验）
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import threading
from pathlib import Path
from typing import Any, Callable

from src.config import load_dotenv
from src.domain.chapter_structure import (
    Chapter,
    ChapterCheckpoint,
    ChapterDecisionPoint,
    ChapterEnding,
    ChapterNode,
    ChapterNPCProfile,
    ChapterOption,
    ChapterResult,
    ChapterStateSnapshot,
)
from src.domain.ending_condition import EndingCondition
from src.domain.npc_state import NPCState
from src.domain.script_design import ScriptDesign
from src.generation.chapter_prompts import (
    CHAPTER_TEMPLATE_MD,
    build_call1_prompt,
    build_call2_prompt,
    build_call3_prompt,
    build_call4_prompt,
    build_call4_repair_prompt,
    build_call5_prompt,
    build_initial_state_snapshot_text,
    build_locked_nodes_text,
    build_state_snapshot_text,
    build_unlocked_nodes_text,
)
from src.generation.pa_backend_script_client import PABackendScriptClient
from src.generation.chapter_validator import ChapterValidator
from src.generation.qwen_client import ChatMessage, QwenChatClient
from src.services.source_snapshot_manager import SourceSnapshotManager

GenerationProgressCallback = Callable[[int, int, str, int], None]


def _load_few_shot_example() -> str:
    """加载 few-shot 示例章节。"""
    candidates = [
        Path(__file__).resolve().parent / "chapter_example.md",
        Path.cwd() / "src" / "generation" / "chapter_example.md",
    ]
    for path in candidates:
        if path.exists():
            return path.read_text(encoding="utf-8")
    return ""


class ChapterGenerationError(RuntimeError):
    """章节式生成失败时抛出。"""


class ChapterScriptGenerator:
    """6-Call 章节式剧本生成器。"""

    # Call 3 每章最多重试次数
    MAX_CHAPTER_RETRIES = 2
    MAX_CONTINUITY_REPAIR_ROUNDS = 2

    def __init__(
        self,
        pa_client: PABackendScriptClient | None = None,
        flash_client: QwenChatClient | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self._cancel_event = cancel_event
        self._pa_client = pa_client
        self._flash_client = flash_client
        self._few_shot = _load_few_shot_example()
        self._artifact_inputs_lock = threading.Lock()

    # ============================================================
    # 公开接口
    # ============================================================

    def generate_full(
        self,
        request: Any,  # ScriptGenerationRequest
        progress_callback: GenerationProgressCallback | None = None,
        output_dir: str | Path | None = None,
    ) -> tuple[ScriptDesign, str]:
        """执行完整 6-Call 管线。

        output_dir 下的创作源按文件复用；派生 JSON 同时校验输入指纹，
        输入变化或文件不存在时执行对应 Call 并保存。

        Args:
            request: ScriptGenerationRequest
            progress_callback: 进度回调
            output_dir: 中间产物目录（也用于断点续跑）

        Returns:
            (ScriptDesign, complete_script_md)
        """
        self._ensure_clients()
        self._output_dir = Path(output_dir) if output_dir else None

        # 从 request 提取参数
        scenario = getattr(request, "scenario", "")
        player_role = getattr(request, "player_role", "")
        learning_goals = getattr(request, "learning_goal", "")
        learning_goals_list = [g.strip() for g in learning_goals.split("\n") if g.strip()] if learning_goals else []
        chapter_count = getattr(request, "chapter_count", 6)
        ending_count = getattr(request, "ending_count", 4)
        decision_point_count = getattr(request, "decision_point_count", 3)
        duration_minutes = getattr(request, "duration_minutes", 45)
        character_settings = getattr(request, "character_settings", "")
        story_background = getattr(request, "story_background", "")
        extra_requirements = getattr(request, "extra_requirements", "")
        npc_count = getattr(request, "npc_count", 8)

        # ---- Call 1: 全局设定 ----
        game_settings_md = self._load_or_call(
            "01_game_settings.md",
            call_fn=lambda: self._call1_generate_settings(
                scenario=scenario, player_role=player_role,
                learning_goals=learning_goals_list, chapter_count=chapter_count,
                ending_count=ending_count, duration_minutes=duration_minutes,
                character_settings=character_settings,
                story_background=story_background, extra_requirements=extra_requirements,
                npc_count=npc_count, decision_point_count=decision_point_count,
                progress_callback=progress_callback,
            ),
            label="Call 1/6: 生成全局设定", stage=1,
            progress_callback=progress_callback,
        )

        # ---- Call 2: 章节大纲 ----
        chapter_outline_md = self._load_or_call(
            "02_chapter_outline.md",
            call_fn=lambda: self._call2_generate_outline(
                game_settings_md,
                decision_point_count=decision_point_count,
                progress_callback=progress_callback,
            ),
            label="Call 2/6: 生成章节大纲", stage=2,
            progress_callback=progress_callback,
        )

        # ---- Call 3: 逐章生成 ----
        chapters_md = self._load_or_call_chapters(
            chapter_count,
            call_fn=lambda: self._call3_generate_chapters(
                game_settings_md=game_settings_md,
                chapter_outline_md=chapter_outline_md,
                chapter_count=chapter_count,
                decision_point_count=decision_point_count,
                progress_callback=progress_callback,
            ),
            label_prefix="Call 3/6: 逐章生成",
            progress_callback=progress_callback,
        )

        # ---- Call 4: 事实连续性审查与源文件定点修复 ----
        sources = {
            "01_game_settings.md": game_settings_md,
            "02_chapter_outline.md": chapter_outline_md,
            **{
                f"03_ch{index:02d}.md": chapter_md
                for index, chapter_md in enumerate(chapters_md, start=1)
            },
        }
        continuity_path = (
            self._output_dir / "05_continuity_review.json"
            if self._output_dir is not None else None
        )
        continuity_review = None
        current_source_hash = self._source_content_hash(sources)
        if continuity_path is not None and continuity_path.exists():
            cached_review = json.loads(continuity_path.read_text(encoding="utf-8"))
            if (
                cached_review.get("repair_version") == 1
                and cached_review.get("source_hash") == current_source_hash
            ):
                continuity_review = cached_review
                print("  ⏭  跳过 Call 4/6: 连续性审查与修复（已完成）")
        if continuity_review is None:
            self._report(progress_callback, 6, 8, "Call 4/6: 连续性审查与修复")
            continuity_review, sources = self.review_and_repair_sources(sources)
            for filename, content in sources.items():
                self._save_intermediate(filename, content)
            continuity_review["source_hash"] = self._source_content_hash(sources)
            self._save_intermediate(
                "05_continuity_review.json",
                json.dumps(continuity_review, ensure_ascii=False, indent=2),
            )

        game_settings_md = sources["01_game_settings.md"]
        chapter_outline_md = sources["02_chapter_outline.md"]
        chapters_md = [
            sources[f"03_ch{index:02d}.md"]
            for index in range(1, chapter_count + 1)
        ]
        if self._output_dir is not None:
            SourceSnapshotManager.capture_if_missing(self._output_dir)

        # ---- 合并修复后的创作源（始终重做，无 API 调用） ----
        merged_script_md = self._merge_chapters(game_settings_md, chapter_outline_md, chapters_md)
        self._save_intermediate("04_merged.md", merged_script_md)
        complete_script_md = merged_script_md

        # ---- Call 5: JSON 抽取（分段：5a 全局 + 5b 逐章） ----
        script_json = self._load_or_call_json(
            "06_script_structure.json",
            call_fn=lambda: self._call5_extract_json(
                merged_script_md, chapters_md, progress_callback,
            ),
            label="Call 5/6: JSON 抽取", stage=7,
            progress_callback=progress_callback,
            depends_on_content=merged_script_md,
        )

        # ---- Call 6: 校验 ----
        validation_report = self._load_or_call_json(
            "07_validation_report.json",
            call_fn=lambda: self._call6_validate(
                script_json,
                expected_npc_count=npc_count,
                expected_decision_point_count=decision_point_count,
                complete_script_md=merged_script_md,
                continuity_review=continuity_review,
            ),
            label="Call 6/6: 校验", stage=8,
            progress_callback=progress_callback,
            depends_on_content=json.dumps({
                "script": script_json,
                "merged_md": merged_script_md,
                "continuity": continuity_review,
                "expected_npc_count": npc_count,
                "expected_decision_point_count": decision_point_count,
            }, ensure_ascii=False, sort_keys=True),
            reuse_if=lambda report: report.get("validation_version") == 2,
        )
        script_json["_validation"] = validation_report

        # ---- 组装 ScriptDesign ----
        script_design = self._build_script_design(script_json)

        self._report(progress_callback, 8, 8, "生成完成")
        return script_design, complete_script_md

    # ============================================================
    # Call 1: 全局设定
    # ============================================================

    def _call1_generate_settings(
        self,
        scenario: str,
        player_role: str,
        learning_goals: list[str],
        chapter_count: int,
        ending_count: int,
        duration_minutes: int,
        character_settings: str,
        story_background: str,
        extra_requirements: str,
        npc_count: int = 8,
        decision_point_count: int = 3,
        progress_callback: GenerationProgressCallback | None = None,
    ) -> str:
        messages = build_call1_prompt(
            scenario=scenario,
            player_role=player_role,
            learning_goals=learning_goals,
            chapter_count=chapter_count,
            ending_count=ending_count,
            duration_minutes=duration_minutes,
            character_settings=character_settings,
            story_background=story_background,
            extra_requirements=extra_requirements,
            npc_count=npc_count,
            decision_point_count=decision_point_count,
        )
        result = self._pa_complete(
            messages,
            temperature=0.3,
            progress_callback=progress_callback,
            stage=1,
            label="Call 1/6: 生成全局设定",
        )
        return self._clean_md_output(result)

    # ============================================================
    # Call 2: 章节大纲
    # ============================================================

    def _call2_generate_outline(
        self,
        game_settings_md: str,
        decision_point_count: int = 3,
        progress_callback: GenerationProgressCallback | None = None,
    ) -> str:
        messages = build_call2_prompt(game_settings_md, decision_point_count)
        result = self._pa_complete(
            messages,
            temperature=0.3,
            progress_callback=progress_callback,
            stage=2,
            label="Call 2/6: 生成章节大纲",
        )
        return self._clean_md_output(result)

    # ============================================================
    # Call 3: 逐章生成
    # ============================================================

    def _call3_generate_chapters(
        self,
        game_settings_md: str,
        chapter_outline_md: str,
        chapter_count: int,
        decision_point_count: int = 3,
        progress_callback: GenerationProgressCallback | None = None,
    ) -> list[str]:
        """逐章生成，每章独立 PA Backend 对话。

        状态快照在章间累积传递。
        """
        chapters_md: list[str] = []
        current_snapshot = ChapterStateSnapshot()

        # 提取各章大纲条目
        outline_entries = self._extract_chapter_outline_entries(chapter_outline_md, chapter_count)

        for i in range(chapter_count):
            chapter_num = i + 1
            existing_chapter = self._load_existing_chapter(chapter_num)
            if existing_chapter is not None:
                chapters_md.append(existing_chapter)
                chapter_snapshot = self._extract_state_snapshot(existing_chapter)
                current_snapshot = self._accumulate_snapshot(current_snapshot, chapter_snapshot)
                self._report(
                    progress_callback, 3, 8,
                    f"Call 3/6: 复用第 {chapter_num}/{chapter_count} 章",
                )
                continue

            stage = 3  # Call 3 spans stages 3-5 in the 8-stage progress
            self._report(
                progress_callback, stage, 8,
                f"Call 3/6: 生成第 {chapter_num}/{chapter_count} 章",
            )

            # 构建前序章节摘要（控制上下文长度）
            previous_summary = self._build_previous_summary(chapters_md, i)

            # 状态快照文本
            if i == 0:
                state_text = build_initial_state_snapshot_text()
            else:
                state_text = build_state_snapshot_text(current_snapshot)

            unlocked_text = build_unlocked_nodes_text(current_snapshot)
            locked_text = build_locked_nodes_text(current_snapshot)

            outline_entry = outline_entries[i] if i < len(outline_entries) else f"## 第 {chapter_num} 章（大纲缺失，请根据全局设定和大纲自行设计）"

            messages = build_call3_prompt(
                game_settings_md=game_settings_md,
                chapter_outline_md=chapter_outline_md,
                current_chapter_num=chapter_num,
                total_chapters=chapter_count,
                current_chapter_outline_entry=outline_entry,
                state_snapshot_text=state_text,
                unlocked_nodes_text=unlocked_text,
                locked_nodes_text=locked_text,
                chapter_template_md=CHAPTER_TEMPLATE_MD,
                few_shot_example_md=self._few_shot,
                previous_chapters_summary=previous_summary,
                decision_point_count=decision_point_count,
            )

            ch_md = None
            last_error = None
            for attempt in range(self.MAX_CHAPTER_RETRIES + 1):
                try:
                    # 每章使用全新的 PA Backend 对话
                    self._pa_client.reset_conversation()
                    result = self._pa_complete(
                        messages,
                        temperature=0.4,
                        progress_callback=progress_callback,
                        stage=stage,
                        label=f"Call 3/6: 生成第 {chapter_num}/{chapter_count} 章",
                    )
                    ch_md = self._clean_md_output(result)

                    # 即时检查：必须有状态快照和章节结算
                    if not self._chapter_has_required_sections(ch_md):
                        if attempt < self.MAX_CHAPTER_RETRIES:
                            continue
                        # 最后一次尝试，即使不完美也接受
                    break
                except Exception as exc:
                    last_error = exc
                    if attempt < self.MAX_CHAPTER_RETRIES:
                        continue
                    raise ChapterGenerationError(
                        f"第 {chapter_num} 章生成失败（已重试 {self.MAX_CHAPTER_RETRIES} 次）: {exc}"
                    ) from exc

            if ch_md is None:
                raise ChapterGenerationError(
                    f"第 {chapter_num} 章生成失败: {last_error}"
                )

            chapters_md.append(ch_md)
            self._save_intermediate(f"03_ch{chapter_num:02d}.md", ch_md)

            # 提取本章状态快照，为下一章准备
            chapter_snapshot = self._extract_state_snapshot(ch_md)
            current_snapshot = self._accumulate_snapshot(current_snapshot, chapter_snapshot)

            # 更新进度（Call 3 内部使用子阶段 3-5）
            sub_stage = 3 + int((i + 1) / chapter_count * 2)  # 3, 4, or 5
            self._report(
                progress_callback, min(sub_stage, 5), 8,
                f"Call 3/6: 第 {chapter_num}/{chapter_count} 章完成",
            )

        return chapters_md

    def _load_existing_chapter(self, chapter_num: int) -> str | None:
        """读取已生成章节，用于 Call 3 断点续跑。"""
        if self._output_dir is None:
            return None
        filepath = self._output_dir / f"03_ch{chapter_num:02d}.md"
        if not filepath.exists():
            return None
        return filepath.read_text(encoding="utf-8")

    # ============================================================
    # Call 4: 事实连续性审查
    # ============================================================

    def _call4_consistency_review(self, complete_script_md: str) -> dict:
        messages = build_call4_prompt(complete_script_md)
        result = self._flash_complete(
            messages,
            temperature=0.1,
            response_format="json_object",
            max_tokens=2048,
        )
        review = self._parse_json(result)
        issues = review.get("continuity_issues")
        if not isinstance(issues, list):
            review["continuity_issues"] = []
        if review.get("status") not in {"pass", "review_recommended"}:
            review["status"] = "review_recommended" if review["continuity_issues"] else "pass"
        return review

    def review_and_repair_sources(
        self,
        sources: dict[str, str],
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """Review source Markdown and apply only uniquely matching text patches."""
        current = dict(sources)
        rounds: list[dict[str, Any]] = []
        all_fixes: list[dict[str, Any]] = []
        previous_issues: list[dict[str, Any]] = []

        for round_number in range(1, self.MAX_CONTINUITY_REPAIR_ROUNDS + 2):
            messages = build_call4_repair_prompt(current, previous_issues)
            result = self._flash_complete(
                messages,
                temperature=0.1,
                response_format="json_object",
                max_tokens=8192,
            )
            review = self._parse_json(result)
            issues = review.get("continuity_issues")
            patches = review.get("patches")
            if not isinstance(issues, list):
                issues = []
            if not isinstance(patches, list):
                patches = []

            round_result: dict[str, Any] = {
                "round": round_number,
                "issues": issues,
                "applied_fixes": [],
                "rejected_patches": [],
            }
            if not issues:
                rounds.append(round_result)
                return ({
                    "repair_version": 1,
                    "status": "pass",
                    "continuity_issues": [],
                    "applied_fixes": all_fixes,
                    "rounds": rounds,
                }, current)

            if round_number > self.MAX_CONTINUITY_REPAIR_ROUNDS:
                previous_issues = issues
                rounds.append(round_result)
                break

            for patch in patches:
                applied, detail = self._apply_continuity_patch(current, patch)
                key = "applied_fixes" if applied else "rejected_patches"
                round_result[key].append(detail)
                if applied:
                    all_fixes.append(detail)
            rounds.append(round_result)
            previous_issues = issues
            if not round_result["applied_fixes"]:
                break

        return ({
            "repair_version": 1,
            "status": "review_required",
            "continuity_issues": previous_issues,
            "applied_fixes": all_fixes,
            "rounds": rounds,
        }, current)

    @staticmethod
    def _apply_continuity_patch(
        sources: dict[str, str],
        patch: Any,
    ) -> tuple[bool, dict[str, Any]]:
        if not isinstance(patch, dict):
            return False, {"error": "patch 不是对象", "patch": patch}
        filename = str(patch.get("file") or "").strip()
        old_text = patch.get("old_text")
        new_text = patch.get("new_text")
        detail = {
            "issue_code": str(patch.get("issue_code") or ""),
            "file": filename,
            "old_text": old_text,
            "new_text": new_text,
            "reason": str(patch.get("reason") or ""),
        }
        if filename not in sources:
            detail["error"] = "目标文件不存在"
            return False, detail
        if not isinstance(old_text, str) or not old_text:
            detail["error"] = "old_text 不能为空"
            return False, detail
        if not isinstance(new_text, str) or not new_text.strip():
            detail["error"] = "new_text 不能为空"
            return False, detail
        occurrences = sources[filename].count(old_text)
        if occurrences != 1:
            detail["error"] = f"old_text 匹配次数为 {occurrences}，要求唯一匹配"
            return False, detail
        if old_text == new_text:
            detail["error"] = "补丁没有产生变化"
            return False, detail
        sources[filename] = sources[filename].replace(old_text, new_text, 1)
        return True, detail

    # ============================================================
    # Call 5: JSON 抽取
    # ============================================================

    def _call5_extract_json(
        self,
        complete_script_md: str,
        chapters_md: list[str] | None = None,
        progress_callback: GenerationProgressCallback | None = None,
    ) -> dict:
        """分段 JSON 抽取：5a 抽全局+NPC+结局，5b 逐章抽剧情树，最后合并。

        子调用各自有中间文件（06a / 06b_chNN），删除对应文件即可重跑该步。
        """
        # -- 5a: 全局结构 + NPC + 结局 --
        chapter_ids = [f"ch{i+1:02d}" for i in range(len(chapters_md) if chapters_md else 4)]
        global_json = self._load_or_call_json(
            "06a_global.json",
            call_fn=lambda: self._call5a_extract_global(complete_script_md, chapter_ids),
            label="Call 5a/6: 抽取全局结构 + NPC + 结局", stage=7,
            progress_callback=progress_callback,
            depends_on_content=complete_script_md,
        )

        # -- 5b: 逐章抽取 --
        chapters_json: list[dict] = []
        if chapters_md:
            for i, ch_md in enumerate(chapters_md):
                chapter_num = i + 1
                chapter_id = f"ch{chapter_num:02d}"
                ch_json = self._load_or_call_json(
                    f"06b_{chapter_id}.json",
                    call_fn=lambda md=ch_md, cid=chapter_id, cn=chapter_num:
                        self._call5b_extract_chapter(md, cid, cn),
                    label=f"Call 5b/6: 抽取第 {chapter_num} 章剧情树", stage=7,
                    progress_callback=progress_callback,
                    depends_on_content=ch_md,
                )
                chapters_json.append(ch_json)
        else:
            # 回退：无分章 MD 时跳过
            chapters_json = []

        # -- 合并 --
        merged = dict(global_json)
        merged.pop("_chapter_ids", None)
        merged["chapters"] = chapters_json
        self._save_intermediate("06_script_structure.json",
                                json.dumps(merged, ensure_ascii=False, indent=2))
        return merged

    def _call5a_extract_global(
        self,
        complete_script_md: str,
        chapter_ids: list[str],
    ) -> dict:
        """Call 5a: 从完整剧本中抽取全局结构、NPC 表、结局。"""
        from src.generation.chapter_prompts import build_call5a_prompt
        messages = build_call5a_prompt(complete_script_md, chapter_ids)
        result = self._flash_complete(
            messages, temperature=0.1, response_format="json_object",
            max_tokens=8192,
        )
        return self._parse_json(result)

    def _call5b_extract_chapter(
        self,
        chapter_md: str,
        chapter_id: str,
        chapter_num: int,
    ) -> dict:
        """Call 5b: 从单章 Markdown 中抽取剧情树 JSON。"""
        from src.generation.chapter_prompts import build_call5b_prompt
        messages = build_call5b_prompt(chapter_md, chapter_id, chapter_num)
        result = self._flash_complete(
            messages, temperature=0.1, response_format="json_object",
            max_tokens=8192,
        )
        return self._parse_json(result)

    # ============================================================
    # Call 6: 校验
    # ============================================================

    def _call6_validate(
        self,
        script_json: dict,
        expected_npc_count: int,
        expected_decision_point_count: int | None = None,
        complete_script_md: str = "",
        continuity_review: dict[str, Any] | None = None,
    ) -> dict:
        """只执行确定性的结构校验和 Markdown→JSON 提取一致性校验。"""
        programmatic = ChapterValidator.validate_programmatic(
            script_json,
            expected_npc_count=expected_npc_count,
            expected_decision_point_count=expected_decision_point_count,
        )
        extraction = ChapterValidator.validate_extraction_fidelity(
            script_json, complete_script_md,
        )
        all_issues = programmatic["issues"] + extraction["issues"]
        errors = [i for i in all_issues if i.get("severity") == "error"]
        return {
            "validation_version": 2,
            "valid": len(errors) == 0,
            "issues": all_issues,
            "error_count": len(errors),
            "warning_count": len([i for i in all_issues if i.get("severity") == "warning"]),
            "structure_validation": programmatic,
            "extraction_validation": extraction,
            "continuity_review": continuity_review or {
                "status": "not_run",
                "continuity_issues": [],
            },
        }

    # ============================================================
    # 状态快照提取与累积
    # ============================================================

    def _extract_state_snapshot(self, chapter_md: str) -> ChapterStateSnapshot:
        """从章节 Markdown 的状态快照节提取结构化数据。

        使用正则匹配，因为格式固定。
        """
        snapshot = ChapterStateSnapshot()

        # 提取「状态快照」节
        snapshot_section = self._extract_md_section(chapter_md, "状态快照")
        if not snapshot_section:
            return snapshot

        # 提取变量范围表格
        var_ranges = self._extract_variable_ranges(snapshot_section)
        snapshot = ChapterStateSnapshot(
            signed=var_ranges.get("signed", {}).get("most_likely", 0),
            social_stability=var_ranges.get("social_stability", {}).get("most_likely", 70),
            political_credit=var_ranges.get("political_credit", {}).get("most_likely", 70),
            public_trust=var_ranges.get("public_trust", {}).get("most_likely", 50),
            env_clue=var_ranges.get("env_clue", {}).get("most_likely", 0),
            media_pressure=var_ranges.get("media_pressure", {}).get("most_likely", 30),
            budget=var_ranges.get("budget", {}).get("most_likely", 8000),
            days_left=var_ranges.get("days_left", {}).get("most_likely", 90),
            active_flags=self._extract_flags(snapshot_section),
            unlocked_nodes=self._extract_node_list(snapshot_section, "已解锁"),
            locked_nodes=self._extract_node_list(snapshot_section, "已关闭"),
            variable_ranges=var_ranges,
        )
        return snapshot

    def _extract_variable_ranges(self, section: str) -> dict[str, dict[str, int]]:
        """从状态快照节提取变量范围表格。"""
        ranges: dict[str, dict[str, int]] = {}

        # 匹配变量范围表格行：| signed | 0 | 15 | 3（选项 C）|
        var_names = [
            "signed", "social_stability", "political_credit", "public_trust",
            "env_clue", "media_pressure", "budget", "days_left",
        ]
        for var_name in var_names:
            # 匹配表格行：| var_name | min | max | most_likely |
            pattern = rf'\|\s*{re.escape(var_name)}\s*\|\s*([+-]?\d+)\s*\|\s*([+-]?\d+)\s*\|\s*([+-]?\d+)'
            m = re.search(pattern, section)
            if m:
                ranges[var_name] = {
                    "min": int(m.group(1)),
                    "max": int(m.group(2)),
                    "most_likely": int(m.group(3)),
                }
        return ranges

    def _extract_flags(self, section: str) -> set[str]:
        """从状态快照节提取 flag 集合。"""
        flags: set[str] = set()

        # 匹配「可能激活的 Flag 集合」子节
        flags_section = self._extract_md_subsection(section, "可能激活的 Flag 集合")
        if not flags_section:
            flags_section = self._extract_md_subsection(section, "活跃标记")

        if flags_section:
            # 匹配：- flag_xxx: 来自 ch0X_A
            for m in re.finditer(r'-\s*(flag_\w+)', flags_section):
                flags.add(m.group(1))
        return flags

    def _extract_node_list(self, section: str, label: str) -> set[str]:
        """从状态快照节提取节点 ID 列表（已解锁/已关闭）。"""
        nodes: set[str] = set()
        subsection = self._extract_md_subsection(section, f"{label}的后续节点")
        if not subsection:
            return nodes

        # 匹配：- chXX_xxx 或 - chXX_xxx（需 flag_xxx）
        for m in re.finditer(r'-\s*(ch\d+_\w+)', subsection):
            nodes.add(m.group(1))
        return nodes

    def _accumulate_snapshot(
        self,
        previous: ChapterStateSnapshot,
        current: ChapterStateSnapshot,
    ) -> ChapterStateSnapshot:
        """累积状态快照：将当前章的增量叠加到前序状态上。"""
        return ChapterStateSnapshot(
            signed=current.signed,
            social_stability=current.social_stability,
            political_credit=current.political_credit,
            public_trust=current.public_trust,
            env_clue=current.env_clue,
            media_pressure=current.media_pressure,
            budget=current.budget,
            days_left=current.days_left,
            active_flags=previous.active_flags | current.active_flags,
            unlocked_nodes=previous.unlocked_nodes | current.unlocked_nodes,
            locked_nodes=previous.locked_nodes | current.locked_nodes,
            variable_ranges=current.variable_ranges,
        )

    # ============================================================
    # 程序化校验（Call 6a）
    # ============================================================

    def _validate_programmatic(self, script_json: dict) -> list[dict]:
        """兼容旧调用；生产路径统一委托 ChapterValidator。"""
        return ChapterValidator.validate_programmatic(script_json).get("issues", [])

    # ============================================================
    # ScriptDesign 组装
    # ============================================================

    def _build_script_design(self, script_json: dict) -> ScriptDesign:
        """从 Call 5 的 JSON 构建 ScriptDesign。"""
        chapters_data = script_json.get("chapters", [])
        chapter_npcs = self._build_chapter_npcs(script_json.get("npcs", []))
        chapter_endings = self._build_chapter_endings(script_json.get("endings", []))

        chapters = []
        for i, ch_data in enumerate(chapters_data):
            # 解析多个决策点（JSON 中为 decision_points 数组）
            dps_data = ch_data.get("decision_points", [])
            if not dps_data:
                # 向后兼容：如果旧格式只有单个 decision_point，包装为列表
                single_dp = ch_data.get("decision_point")
                if single_dp:
                    dps_data = [single_dp]

            decision_points = []
            for dp_data in dps_data:
                if not dp_data:
                    continue
                options = []
                for opt_data in dp_data.get("options", []):
                    options.append(ChapterOption(
                        choice_id=opt_data.get("choice_id", ""),
                        option_label=opt_data.get("option_label", ""),
                        text=opt_data.get("text", ""),
                        availability=opt_data.get("availability"),
                        effects=opt_data.get("effects", {}),
                        npc_state_changes=opt_data.get("npc_state_changes", {}),
                        unlock_nodes=opt_data.get("unlock_nodes", []),
                        lock_nodes=opt_data.get("lock_nodes", []),
                        flags_added=opt_data.get("flags_added", []),
                        flags_removed=opt_data.get("flags_removed", []),
                        immediate_result_text=opt_data.get("immediate_result_text", ""),
                        long_term_effect=opt_data.get("long_term_effect", ""),
                        teaching_feedback=opt_data.get("teaching_feedback", ""),
                    ))
                decision_points.append(ChapterDecisionPoint(
                    node_id=dp_data.get("node_id", ""),
                    question=dp_data.get("question", ""),
                    order=dp_data.get("order", len(decision_points) + 1),
                    options=options,
                ))

            info_nodes = []
            for node_data in ch_data.get("info_nodes", []):
                info_nodes.append(ChapterNode(
                    node_id=node_data.get("node_id", ""),
                    node_type="INFO",
                    title=node_data.get("title", ""),
                    text=node_data.get("content", ""),
                    next=[node_data.get("next", "")] if node_data.get("next") else [],
                    unlock_condition=node_data.get("unlock_condition"),
                ))

            results = []
            for res_data in ch_data.get("results", []):
                results.append(ChapterResult(
                    node_id=res_data.get("node_id", ""),
                    from_choice=res_data.get("from_choice", ""),
                    text=res_data.get("text", ""),
                    next=res_data.get("next", ""),
                ))

            cp_data = ch_data.get("checkpoint", {}) or {}
            checkpoint = ChapterCheckpoint(
                checkpoint_id=cp_data.get("checkpoint_id", ""),
                merge_from=cp_data.get("merge_from", []),
                next_chapter=cp_data.get("next_chapter", ""),
                summary=cp_data.get("summary", ""),
            )

            snap_data = cp_data.get("variable_snapshot", {}) or {}
            state_snapshot = ChapterStateSnapshot(
                signed=snap_data.get("signed", 0),
                social_stability=snap_data.get("social_stability", 70),
                political_credit=snap_data.get("political_credit", 70),
                public_trust=snap_data.get("public_trust", 50),
                env_clue=snap_data.get("env_clue", 0),
                media_pressure=snap_data.get("media_pressure", 30),
                budget=snap_data.get("budget", 8000),
                days_left=snap_data.get("days_left", 90),
                active_flags=set(cp_data.get("active_flags", [])),
                unlocked_nodes=set(cp_data.get("unlocked_nodes", [])),
                locked_nodes=set(cp_data.get("locked_nodes", [])),
            )

            chapters.append(Chapter(
                chapter_id=ch_data.get("chapter_id", f"ch{i+1:02d}"),
                order=i + 1,
                title=ch_data.get("title", ""),
                day_range=ch_data.get("day_range", ""),
                core_task=ch_data.get("core_task", ""),
                main_question=ch_data.get("main_question", ""),
                learning_goals=ch_data.get("learning_goals", []),
                unlock_condition=ch_data.get("unlock_condition"),
                background=ch_data.get("background", ""),
                info_nodes=info_nodes,
                decision_points=decision_points,
                results=results,
                checkpoint=checkpoint,
                state_snapshot=state_snapshot,
            ))

        return ScriptDesign(
            title=script_json.get("title", ""),
            premise="",
            player_role=script_json.get("player_role", ""),
            core_conflict=script_json.get("core_conflict", ""),
            initial_game_state=self._build_initial_game_state(script_json),
            npc_seed=self._build_npc_states(chapter_npcs),
            chapters=chapters,
            endings=[self._to_ending_condition(ending) for ending in chapter_endings],
            chapter_endings=chapter_endings,
            chapter_npcs=chapter_npcs,
            quality_review={},
            validation_report=script_json.get("_validation", {}),
        )

    @staticmethod
    def _build_chapter_npcs(value: Any) -> list[ChapterNPCProfile]:
        if not isinstance(value, list):
            return []
        profiles: list[ChapterNPCProfile] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            profiles.append(ChapterNPCProfile(
                npc_id=str(item.get("npc_id") or "").strip(),
                name=str(item.get("name") or "").strip(),
                npc_type=str(item.get("npc_type") or "").strip(),
                group=str(item.get("group") or "").strip(),
                core_demand=str(item.get("core_demand") or "").strip(),
                bottom_line=str(item.get("bottom_line") or "").strip(),
                persuasion_conditions=ChapterScriptGenerator._string_list(item.get("persuasion_conditions")),
                known_info=ChapterScriptGenerator._string_list(item.get("known_info")),
                relationships=ChapterScriptGenerator._string_list(item.get("relationships")),
                initial_stance=str(item.get("initial_stance") or "").strip(),
                attitude_path=str(item.get("attitude_path") or "").strip(),
                trust_to_player=ChapterScriptGenerator._int_value(item.get("trust_to_player"), 50),
                attitude_score=ChapterScriptGenerator._int_value(item.get("attitude_score"), 50),
                anxiety_level=ChapterScriptGenerator._int_value(item.get("anxiety_level"), 50),
                reference_point=ChapterScriptGenerator._int_value(item.get("reference_point"), 0),
                granovetter_threshold=ChapterScriptGenerator._int_value(item.get("granovetter_threshold"), 50),
                core_demand_satisfied=bool(item.get("core_demand_satisfied", False)),
                signed=bool(item.get("signed", False)),
            ))
        return profiles

    @staticmethod
    def _build_npc_states(profiles: list[ChapterNPCProfile]) -> list[NPCState]:
        states: list[NPCState] = []
        for npc in profiles:
            try:
                states.append(NPCState(
                    npc_id=npc.npc_id,
                    name=npc.name,
                    npc_type=npc.npc_type,
                    group=npc.group,
                    trust_to_player=npc.trust_to_player,
                    attitude_score=npc.attitude_score,
                    anxiety_level=npc.anxiety_level,
                    reference_point=npc.reference_point,
                    granovetter_threshold=npc.granovetter_threshold,
                    core_demand_satisfied=npc.core_demand_satisfied,
                    signed=npc.signed,
                    known_info=npc.known_info,
                ))
            except ValueError:
                # Call 6 已报告不合法 NPC；保留主表原文，避免构建过程静默中断。
                continue
        return states

    @staticmethod
    def _build_chapter_endings(value: Any) -> list[ChapterEnding]:
        if not isinstance(value, list):
            return []
        endings: list[ChapterEnding] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            ending_type = str(item.get("type") or item.get("ending_type") or "neutral").strip()
            if ending_type not in {"good", "neutral", "bad"}:
                ending_type = "neutral"
            conditions = item.get("conditions")
            endings.append(ChapterEnding(
                ending_id=str(item.get("ending_id") or "").strip(),
                title=str(item.get("title") or "").strip(),
                ending_type=ending_type,
                priority=ChapterScriptGenerator._int_value(item.get("priority"), 0),
                description=str(item.get("description") or "").strip(),
                conditions=conditions if isinstance(conditions, dict) else {},
                ending_text=str(item.get("ending_text") or "").strip(),
                teaching_summary=str(item.get("teaching_summary") or "").strip(),
                strategy_profile=str(item.get("strategy_profile") or "").strip(),
            ))
        return endings

    @staticmethod
    def _to_ending_condition(ending: ChapterEnding) -> EndingCondition:
        return EndingCondition(
            ending_id=ending.ending_id,
            title=ending.title,
            description=ending.description or ending.ending_text,
            conditions=ChapterScriptGenerator._flatten_ending_conditions(ending.conditions),
            ending_type=ending.ending_type,
        )

    @staticmethod
    def _flatten_ending_conditions(conditions: dict) -> list[str]:
        lines: list[str] = []
        variable_expression = str(conditions.get("variable_expression") or "").strip()
        if variable_expression:
            lines.append(f"变量条件: {variable_expression}")
        else:
            variables = conditions.get("variables", {})
            if isinstance(variables, dict):
                lines.extend(f"{name}: {value}" for name, value in variables.items())
        for flag in ChapterScriptGenerator._string_list(conditions.get("flags_required")):
            lines.append(f"需要 flag: {flag}")
        for flag in ChapterScriptGenerator._string_list(conditions.get("flags_required_all")):
            lines.append(f"必须全部具备 flag: {flag}")
        required_any = ChapterScriptGenerator._string_list(conditions.get("flags_required_any"))
        if required_any:
            lines.append(f"至少具备一个 flag: {', '.join(required_any)}")
        for flag in ChapterScriptGenerator._string_list(conditions.get("flags_forbidden")):
            lines.append(f"不得有 flag: {flag}")
        npc_states = conditions.get("npc_states", {})
        if isinstance(npc_states, dict):
            lines.extend(f"NPC {npc_id}: {state}" for npc_id, state in npc_states.items())
        return lines

    def _build_initial_game_state(self, script_json: dict) -> Any:
        """从 JSON 构建初始 GameState。"""
        from src.domain.game_state import GameState

        init = script_json.get("initial_state", {})
        if not isinstance(init, dict):
            init = {}
        return GameState(
            day=1,
            action_points=3,
            budget_remaining=self._int_value(init.get("budget"), 8000),
            budget_unit="万元",
            signed_households=self._int_value(init.get("signed"), 0),
            total_households=36,
            social_stability_index=self._int_value(init.get("social_stability"), 70),
            political_credit=self._int_value(init.get("political_credit"), 70),
            cadre_execution_index=60,
            public_trust=self._int_value(init.get("public_trust"), 50),
            env_clue=self._int_value(init.get("env_clue"), 0),
            media_pressure=self._int_value(init.get("media_pressure"), 30),
            days_left=self._int_value(init.get("days_left"), 90),
        )

    # ============================================================
    # 辅助方法
    # ============================================================

    def _ensure_clients(self) -> None:
        """确保 PA Backend 和 Qwen Flash 客户端已初始化。"""
        load_dotenv(override=False)

        if self._pa_client is None:
            self._pa_client = PABackendScriptClient(cancel_event=self._cancel_event)

        if self._flash_client is None:
            from src.config import QwenConfig
            config = QwenConfig.from_env()
            self._flash_client = QwenChatClient(QwenConfig(
                api_key=config.api_key,
                base_url=config.base_url,
                model="qwen-flash",
                timeout_seconds=config.timeout_seconds,
            ))

    def _pa_complete(
        self,
        messages: list[ChatMessage],
        temperature: float = 0.3,
        progress_callback: GenerationProgressCallback | None = None,
        stage: int = 0,
        label: str = "PA Backend 生成中",
    ) -> str:
        """调用 PA Backend 并处理取消。"""
        if self._cancel_event and self._cancel_event.is_set():
            raise ChapterGenerationError("生成已被用户取消")
        stream_callback = self._make_pa_stream_callback(
            progress_callback, stage, 8, label,
        )
        if isinstance(self._pa_client, PABackendScriptClient):
            return self._pa_client.complete(
                messages,
                temperature=temperature,
                stream_callback=stream_callback,
            )
        return self._pa_client.complete(messages, temperature=temperature)

    def _make_pa_stream_callback(
        self,
        progress_callback: GenerationProgressCallback | None,
        stage: int,
        total: int,
        label: str,
    ) -> Callable[[int], None] | None:
        """Create a throttled callback for PA Backend streaming progress."""
        if progress_callback is None or stage <= 0:
            return None

        last_reported = 0
        min_delta = 2048

        def _callback(received_chars: int) -> None:
            nonlocal last_reported
            if received_chars - last_reported < min_delta:
                return
            last_reported = received_chars
            self._report(
                progress_callback,
                stage,
                total,
                f"{label}（已接收约 {received_chars / 1024:.1f} KiB）",
                received_chars,
            )

        return _callback

    def _flash_complete(
        self,
        messages: list[ChatMessage],
        temperature: float = 0.1,
        response_format: str = "json_object",
        max_tokens: int | None = None,
    ) -> str:
        """调用 Qwen Flash 并处理取消。"""
        if self._cancel_event and self._cancel_event.is_set():
            raise ChapterGenerationError("生成已被用户取消")
        return self._flash_client.complete(
            messages, temperature=temperature, response_format=response_format,
            max_tokens=max_tokens,
        )

    def _merge_chapters(
        self,
        game_settings_md: str,
        chapter_outline_md: str,
        chapters_md: list[str],
    ) -> str:
        """合并全局设定、大纲和所有章节为完整剧本 Markdown。

        大纲中的「## 第 N 章」降级为「### 大纲：第 N 章」，
        避免与正式章节的标题重复。
        """
        import re
        deduped_outline = re.sub(
            r'^## (第 \d+ 章)', r'### 大纲：\1',
            chapter_outline_md, flags=re.MULTILINE,
        )

        parts = [
            "# 完整剧本",
            "",
            game_settings_md,
            "",
            deduped_outline,
            "",
        ]
        for i, ch_md in enumerate(chapters_md):
            parts.append(ch_md)
            parts.append("")
        return "\n\n".join(parts)

    def _extract_chapter_outline_entries(self, outline_md: str, chapter_count: int) -> list[str]:
        """从大纲 Markdown 中提取各章的条目。"""
        entries: list[str] = []
        for i in range(1, chapter_count + 1):
            # 匹配 "## 第 N 章" 到下一个 "## " 之间的内容
            pattern = rf'(## 第 {i} 章[^\n]*\n.*?)(?=\n## 第 {i+1} 章|\n## Flag 全局规划|\n## 结局可达|\Z)'
            m = re.search(pattern, outline_md, re.DOTALL)
            if m:
                entries.append(m.group(1).strip())
            else:
                # 尝试匹配 "## 第N章"（无空格）
                pattern2 = rf'(## 第{i}章[^\n]*\n.*?)(?=\n## 第{i+1}章|\n## Flag 全局|\n## 结局可达|\Z)'
                m2 = re.search(pattern2, outline_md, re.DOTALL)
                if m2:
                    entries.append(m2.group(1).strip())
                else:
                    entries.append(f"## 第 {i} 章：章节 {i}\n- chapter_id: ch{i:02d}\n（大纲未找到，请根据全局设定自行设计）")
        return entries

    def _build_previous_summary(self, chapters_md: list[str], current_index: int) -> str:
        """构建前序章节摘要，限制长度以控制上下文窗口。"""
        if not chapters_md:
            return ""

        summaries: list[str] = []
        for i, ch_md in enumerate(chapters_md):
            # 提取章节总结
            summary_section = self._extract_md_subsection(ch_md, "章节总结")
            if not summary_section:
                summary_section = self._extract_md_section(ch_md, "章节结算")
                if summary_section:
                    summary_section = summary_section[:300]

            if summary_section:
                summaries.append(f"第 {i+1} 章总结: {summary_section[:200]}")
            else:
                # 回退：取最后 200 字符
                summaries.append(f"第 {i+1} 章: {ch_md[-200:]}")

        return "\n".join(summaries)

    def _chapter_has_required_sections(self, chapter_md: str) -> bool:
        """检查章节 Markdown 是否包含必需的节。"""
        required = ["状态快照", "章节结算", "核心决策点"]
        for section in required:
            if section not in chapter_md:
                return False
        return True

    @staticmethod
    def _extract_md_section(text: str, section_name: str) -> str:
        """从 Markdown 中提取 ## 级别标题的内容。

        只在下一个 ## 或 # 标题处停止，不会被子标题（###）截断。
        """
        escaped = re.escape(section_name)
        # 匹配 "## SectionName\n...content..." 直到下一个 "## " 或 "# "（行首）
        pattern = rf'##\s+{escaped}\s*\n(.*?)(?=\n##\s|\n#\s|\Z)'
        m = re.search(pattern, text, re.DOTALL)
        return m.group(1).strip() if m else ""

    @staticmethod
    def _extract_md_subsection(text: str, subsection_name: str) -> str:
        """从 Markdown 中提取 ### 级别子标题的内容。

        在下一个 ### 或 ## 标题处停止。
        """
        escaped = re.escape(subsection_name)
        pattern = rf'###\s+{escaped}\s*\n(.*?)(?=\n###\s|\n##\s|\Z)'
        m = re.search(pattern, text, re.DOTALL)
        return m.group(1).strip() if m else ""

    @staticmethod
    def _clean_md_output(text: str) -> str:
        """清理 LLM 输出的 Markdown 文本。"""
        text = text.strip()
        # 移除可能的代码块包裹
        if text.startswith("```"):
            lines = text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines)
        return text.strip()

    @staticmethod
    def _parse_json(text: str) -> dict:
        """解析 JSON 文本。"""
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`").strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            preview = text[:240].replace("\n", " ")
            raise ChapterGenerationError(
                f"JSON 解析失败: {preview!r}... — {exc}"
            ) from exc
        if not isinstance(parsed, dict):
            raise ChapterGenerationError("JSON 必须是对象")
        return parsed

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    @staticmethod
    def _int_value(value: Any, default: int) -> int:
        if isinstance(value, bool):
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _save_intermediate(self, filename: str, content: str) -> None:
        """如果指定了 output_dir，将中间产物写入磁盘。"""
        if self._output_dir is None:
            return
        self._output_dir.mkdir(parents=True, exist_ok=True)
        filepath = self._output_dir / filename
        filepath.write_text(content, encoding="utf-8")

    def _load_or_call(
        self,
        filename: str,
        call_fn: Callable[[], str],
        label: str,
        stage: int,
        progress_callback: GenerationProgressCallback | None = None,
        depends_on_content: str | None = None,
    ) -> str:
        """如果 output_dir/filename 存在则读取，否则执行 call_fn 并保存。

        depends_on_content 参数已废弃（保留签名兼容），现在只靠文件存在/不存在来控制重跑。
        想重跑某个步骤，删掉对应文件即可。
        """
        if self._output_dir is not None:
            filepath = self._output_dir / filename
            if filepath.exists():
                print(f"  ⏭  跳过 {label}（{filename} 已存在）")
                return filepath.read_text(encoding="utf-8")

        self._report(progress_callback, stage, 8, label)
        result = call_fn()
        self._save_intermediate(filename, result)
        return result

    def _load_or_call_json(
        self,
        filename: str,
        call_fn: Callable[[], dict],
        label: str,
        stage: int,
        progress_callback: GenerationProgressCallback | None = None,
        depends_on_content: str | None = None,
        reuse_if: Callable[[dict], bool] | None = None,
    ) -> dict:
        """JSON 版本的 _load_or_call。"""
        input_hash = (
            hashlib.sha256(depends_on_content.encode("utf-8")).hexdigest()
            if depends_on_content is not None else ""
        )
        if self._output_dir is not None:
            filepath = self._output_dir / filename
            if filepath.exists():
                cached = json.loads(filepath.read_text(encoding="utf-8"))
                input_matches = (
                    depends_on_content is None
                    or self._artifact_input_matches(filename, input_hash)
                )
                if input_matches and (reuse_if is None or reuse_if(cached)):
                    print(f"  ⏭  跳过 {label}（{filename} 已存在）")
                    return cached
                print(f"  ↻  重试 {label}（输入变化或文件未完成）")

        self._report(progress_callback, stage, 8, label)
        result = call_fn()
        self._save_intermediate(filename, json.dumps(result, ensure_ascii=False, indent=2))
        if depends_on_content is not None:
            self._record_artifact_input(filename, input_hash)
        return result

    def _artifact_input_matches(self, filename: str, input_hash: str) -> bool:
        with self._artifact_inputs_lock:
            return self._load_artifact_inputs().get(filename) == input_hash

    def _record_artifact_input(self, filename: str, input_hash: str) -> None:
        with self._artifact_inputs_lock:
            artifact_inputs = self._load_artifact_inputs()
            artifact_inputs[filename] = input_hash
            self._save_artifact_inputs(artifact_inputs)

    def _load_artifact_inputs(self) -> dict[str, str]:
        if self._output_dir is None:
            return {}
        path = self._output_dir / ".artifact_inputs.json"
        if not path.exists():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return value if isinstance(value, dict) else {}

    def _save_artifact_inputs(self, value: dict[str, str]) -> None:
        if self._output_dir is None:
            return
        self._save_intermediate(
            ".artifact_inputs.json",
            json.dumps(value, ensure_ascii=False, indent=2),
        )

    @staticmethod
    def _source_content_hash(sources: dict[str, str]) -> str:
        digest = hashlib.sha256()
        for filename, content in sorted(sources.items()):
            digest.update(filename.encode("utf-8"))
            digest.update(b"\0")
            digest.update(content.encode("utf-8"))
            digest.update(b"\0")
        return digest.hexdigest()

    def _load_or_call_chapters(
        self,
        chapter_count: int,
        call_fn: Callable[[], list[str]],
        label_prefix: str,
        progress_callback: GenerationProgressCallback | None = None,
    ) -> list[str]:
        """Call 3 专用：如果所有 03_ch*.md 都存在则读取，否则全部重新生成。"""
        if self._output_dir is not None:
            existing: list[str] = []
            all_found = True
            for i in range(1, chapter_count + 1):
                filename = f"03_ch{i:02d}.md"
                filepath = self._output_dir / filename
                if filepath.exists():
                    existing.append(filepath.read_text(encoding="utf-8"))
                else:
                    all_found = False
                    break
            if all_found and len(existing) == chapter_count:
                print(f"  ⏭  跳过 Call 3（{chapter_count} 章均已存在）")
                return existing

        # 需要重新生成缺失章节；已存在章节会在 _call3_generate_chapters 内复用。
        chapters_md = call_fn()
        return chapters_md

    @staticmethod
    def _report(
        callback: GenerationProgressCallback | None,
        stage: int,
        total: int,
        name: str,
        request_bytes: int = 0,
    ) -> None:
        if callback is None:
            return
        callback(stage, total, name, request_bytes)
