"""Call 6 校验逻辑 — 程序化规则校验 + Qwen Flash 语义校验。

分为两步：
  Step 6a: 程序自动校验（确定性规则）
  Step 6b: Qwen Flash 语义校验（策略张力、Flag 逻辑、教学反馈一致性）
"""

from __future__ import annotations

import json
from typing import Any


class ChapterValidator:
    """章节式剧本校验器。

    既可以独立使用，也可以配合 Qwen Flash 做语义校验。
    """

    @staticmethod
    def _get_decision_points(ch: dict) -> list[dict]:
        """规范化提取章节的决策点列表（兼容新旧格式）。"""
        dps = ch.get("decision_points")
        if dps:
            return dps
        single = ch.get("decision_point")
        if single:
            return [single]
        return []

    @staticmethod
    def validate_programmatic(script_json: dict) -> dict:
        """Step 6a: 程序自动校验。

        Returns:
            {"valid": bool, "issues": [{"code": str, "message": str, "severity": str}]}
        """
        issues: list[dict] = []
        chapters = script_json.get("chapters", [])

        if not chapters:
            return {
                "valid": False,
                "issues": [{"code": "NO_CHAPTERS", "message": "剧本不包含任何章节", "severity": "error"}],
            }

        # ---- 结构完整性 ----
        for i, ch in enumerate(chapters):
            ch_id = ch.get("chapter_id", f"ch{i+1:02d}")

            # 章节 ID
            if not ch.get("chapter_id"):
                issues.append({"code": "MISSING_CHAPTER_ID", "message": f"第 {i+1} 个章节缺失 chapter_id", "severity": "error"})

            # 决策点
            dps = ChapterValidator._get_decision_points(ch)
            if not dps:
                issues.append({"code": "MISSING_DECISION", "message": f"{ch_id} 缺失决策点", "severity": "error"})
            else:
                if len(dps) < 2:
                    issues.append({"code": "TOO_FEW_DECISION_POINTS", "message": f"{ch_id} 仅有 {len(dps)} 个决策点（建议 2-4 个）", "severity": "warning"})

                for dp in dps:
                    dp_id = dp.get("node_id", "?")
                    opts = dp.get("options", [])
                    if len(opts) < 3:
                        issues.append({"code": "TOO_FEW_OPTIONS", "message": f"{dp_id} 选项少于 3 个（当前 {len(opts)} 个）", "severity": "error"})

                    for opt in opts:
                        choice_id = opt.get("choice_id", "?")
                        effects = opt.get("effects", {})
                        if len(effects) != 8:
                            issues.append({"code": "INCOMPLETE_EFFECTS", "message": f"{choice_id} 变量影响不足 8 个（当前 {len(effects)} 个）", "severity": "warning"})

                        # 检查是否所有 8 个变量都有
                        expected_vars = {"signed", "social_stability", "political_credit", "public_trust", "env_clue", "media_pressure", "budget", "days_left"}
                        actual_vars = set(effects.keys())
                        missing = expected_vars - actual_vars
                        if missing:
                            issues.append({"code": "MISSING_VARIABLES", "message": f"{choice_id} 缺少变量: {sorted(missing)}", "severity": "warning"})

            # 信息节点
            info_nodes = ch.get("info_nodes", [])
            for node in info_nodes:
                if not node.get("node_id"):
                    issues.append({"code": "MISSING_NODE_ID", "message": f"{ch_id} 中信息节点缺失 node_id", "severity": "warning"})

            # 章节结算
            cp = ch.get("checkpoint")
            if not cp:
                issues.append({"code": "MISSING_CHECKPOINT", "message": f"{ch_id} 缺失章节结算", "severity": "warning"})
            else:
                if not cp.get("checkpoint_id"):
                    issues.append({"code": "MISSING_CHECKPOINT_ID", "message": f"{ch_id} checkpoint 缺失 checkpoint_id", "severity": "warning"})
                if not cp.get("merge_from"):
                    issues.append({"code": "EMPTY_MERGE_FROM", "message": f"{ch_id} checkpoint merge_from 为空", "severity": "warning"})

        # ---- 衔接性 ----
        for i in range(len(chapters) - 1):
            cp = chapters[i].get("checkpoint", {}) or {}
            next_ch = cp.get("next_chapter", "")
            expected_next = chapters[i + 1].get("chapter_id", "")
            if next_ch and next_ch != expected_next and next_ch != "ending_evaluation":
                issues.append({"code": "CHAIN_BROKEN", "message": f"第 {i+1} 章 next_chapter='{next_ch}' 不指向第 {i+2} 章 ('{expected_next}')", "severity": "error"})

        # ---- Flag 一致性 ----
        all_created: set[str] = set()
        all_referenced: set[str] = set()

        for ch in chapters:
            for dp in ChapterValidator._get_decision_points(ch):
                for opt in dp.get("options", []):
                    for flag in opt.get("flags_added", []):
                        if flag:
                            all_created.add(flag)

            for node in ch.get("info_nodes", []):
                cond = node.get("unlock_condition") or {}
                if isinstance(cond, dict):
                    for flag in cond.get("flags_required", []):
                        if flag:
                            all_referenced.add(flag)
                    for flag in cond.get("flags_forbidden", []):
                        if flag:
                            all_referenced.add(flag)

            for dp in ChapterValidator._get_decision_points(ch):
                for opt in dp.get("options", []):
                    avail = opt.get("availability") or {}
                    if isinstance(avail, dict):
                        for flag in avail.get("flags_required", []):
                            if flag:
                                all_referenced.add(flag)
                        for flag in avail.get("flags_forbidden", []):
                            if flag:
                                all_referenced.add(flag)

        orphan_flags = all_referenced - all_created
        if orphan_flags:
            issues.append({"code": "ORPHAN_FLAGS", "message": f"引用了未创建的 flag: {sorted(orphan_flags)}", "severity": "warning"})

        # ---- 结局检查 ----
        endings = script_json.get("endings", [])
        if len(endings) < 3:
            issues.append({"code": "TOO_FEW_ENDINGS", "message": f"结局少于 3 个（当前 {len(endings)} 个）", "severity": "warning"})

        has_good = any(e.get("type") == "good" for e in endings)
        has_bad = any(e.get("type") == "bad" for e in endings)
        if not has_good:
            issues.append({"code": "NO_GOOD_ENDING", "message": "缺少 good 类型结局", "severity": "warning"})
        if not has_bad:
            issues.append({"code": "NO_BAD_ENDING", "message": "缺少 bad 类型结局", "severity": "warning"})

        # ---- 变量范围检查 ----
        for ch in chapters:
            for dp in ChapterValidator._get_decision_points(ch):
                for opt in dp.get("options", []):
                    effects = opt.get("effects", {})
                    for var_name, delta in effects.items():
                        if isinstance(delta, (int, float)) and abs(delta) > 25:
                            issues.append({"code": "LARGE_VARIABLE_CHANGE", "message": f"{opt.get('choice_id', '?')} 的 {var_name} 变化为 {delta}，超过 ±25 限制", "severity": "warning"})

        errors = [i for i in issues if i.get("severity") == "error"]
        return {
            "valid": len(errors) == 0,
            "issues": issues,
            "error_count": len(errors),
            "warning_count": len([i for i in issues if i.get("severity") == "warning"]),
        }

    @staticmethod
    def merge_validation(programmatic: dict, semantic: dict | None) -> dict:
        """合并程序化校验和语义校验的结果。"""
        all_issues = list(programmatic.get("issues", []))
        if semantic:
            all_issues.extend(semantic.get("semantic_issues", []))

        errors = [i for i in all_issues if i.get("severity") == "error"]
        return {
            "valid": len(errors) == 0,
            "issues": all_issues,
            "error_count": len(errors),
            "warning_count": len([i for i in all_issues if i.get("severity") == "warning"]),
        }
