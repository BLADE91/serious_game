"""Call 6 校验逻辑：确定性的结构规则与 Markdown→JSON 提取一致性。"""

from __future__ import annotations

import re
from typing import Any


class ChapterValidator:
    """章节式剧本校验器。

    既可以独立使用，也可以配合 Qwen Flash 做语义校验。
    """

    EXPECTED_VARIABLES = {
        "signed", "social_stability", "political_credit", "public_trust",
        "env_clue", "media_pressure", "budget", "days_left",
    }
    ENDING_CONDITION_PATTERN = re.compile(r"^\s*(?:>=|<=|>|<|==|=)\s*-?\d+(?:\.\d+)?\s*$")
    CONDITIONAL_FLAG_PATTERN = re.compile(
        r"\[若\s*(?:没有|无|not\s+|!)?\s*(flag_[A-Za-z0-9_]+)",
        re.IGNORECASE,
    )

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
    def build_flag_lifecycle(script_json: dict) -> dict[str, dict[str, list[str]]]:
        """建立完整 flag 证据索引，供程序校验和语义校验共同使用。"""
        lifecycle: dict[str, dict[str, list[str]]] = {}

        def record(flag: Any, event: str, location: str) -> None:
            if not isinstance(flag, str) or not flag.strip():
                return
            item = lifecycle.setdefault(flag.strip(), {
                "created_at": [],
                "removed_at": [],
                "referenced_at": [],
                "used_by_endings": [],
            })
            if location not in item[event]:
                item[event].append(location)

        def record_conditions(conditions: Any, location: str) -> None:
            if not isinstance(conditions, dict):
                return
            for key in (
                "flags_required", "flags_required_all", "flags_required_any",
                "flags_forbidden",
            ):
                for flag in conditions.get(key, []) or []:
                    record(flag, "referenced_at", f"{location}/{key}")

        def record_conditional_text(value: Any, location: str) -> None:
            if not isinstance(value, str):
                return
            for flag in ChapterValidator.CONDITIONAL_FLAG_PATTERN.findall(value):
                record(flag, "referenced_at", location)

        for chapter in script_json.get("chapters", []):
            chapter_id = str(chapter.get("chapter_id") or "chapter")
            record_conditions(chapter.get("unlock_condition"), f"chapters/{chapter_id}/unlock_condition")
            record_conditional_text(chapter.get("background"), f"chapters/{chapter_id}/background")

            for node in chapter.get("info_nodes", []):
                node_id = str(node.get("node_id") or "info_node")
                base = f"chapters/{chapter_id}/info_nodes/{node_id}"
                record_conditions(node.get("unlock_condition"), f"{base}/unlock_condition")
                record_conditional_text(node.get("content"), f"{base}/content")

            for dp in ChapterValidator._get_decision_points(chapter):
                dp_id = str(dp.get("node_id") or "decision_point")
                for option in dp.get("options", []):
                    choice_id = str(option.get("choice_id") or "option")
                    base = f"chapters/{chapter_id}/decision_points/{dp_id}/options/{choice_id}"
                    for flag in option.get("flags_added", []) or []:
                        record(flag, "created_at", f"{base}/flags_added")
                    for flag in option.get("flags_removed", []) or []:
                        record(flag, "removed_at", f"{base}/flags_removed")
                    record_conditions(option.get("availability"), f"{base}/availability")
                    for field in ("text", "immediate_result_text", "long_term_effect", "teaching_feedback"):
                        record_conditional_text(option.get(field), f"{base}/{field}")

            for result in chapter.get("results", []):
                node_id = str(result.get("node_id") or "result")
                record_conditional_text(
                    result.get("text"),
                    f"chapters/{chapter_id}/results/{node_id}/text",
                )

        for ending in script_json.get("endings", []):
            ending_id = str(ending.get("ending_id") or "ending")
            conditions = ending.get("conditions") or {}
            if not isinstance(conditions, dict):
                continue
            for key in (
                "flags_required", "flags_required_all", "flags_required_any",
                "flags_forbidden",
            ):
                for flag in conditions.get(key, []) or []:
                    record(flag, "used_by_endings", f"endings/{ending_id}/conditions/{key}")

        return {flag: lifecycle[flag] for flag in sorted(lifecycle)}

    @staticmethod
    def validate_extraction_fidelity(script_json: dict, source_md: str) -> dict:
        """检查 Markdown 中的核心标识符是否被 JSON 完整、忠实地提取。"""

        def source_ids(field: str) -> set[str]:
            pattern = (
                rf"(?m)^\s*-\s*(?:\*\*)?{re.escape(field)}\s*:\s*"
                rf"(?:\*\*)?\s*`?([A-Za-z0-9_-]+)`?\s*$"
            )
            return set(re.findall(pattern, source_md))

        source = {
            "chapter": source_ids("chapter_id"),
            "choice": source_ids("choice_id"),
            "npc": source_ids("npc_id"),
            "ending": source_ids("ending_id"),
        }
        extracted = {
            "chapter": {
                str(ch.get("chapter_id"))
                for ch in script_json.get("chapters", [])
                if ch.get("chapter_id")
            },
            "choice": {
                str(option.get("choice_id"))
                for ch in script_json.get("chapters", [])
                for dp in ChapterValidator._get_decision_points(ch)
                for option in dp.get("options", [])
                if option.get("choice_id")
            },
            "npc": {
                str(npc.get("npc_id"))
                for npc in script_json.get("npcs", [])
                if npc.get("npc_id")
            },
            "ending": {
                str(ending.get("ending_id"))
                for ending in script_json.get("endings", [])
                if ending.get("ending_id")
            },
        }

        issues: list[dict] = []
        labels = {
            "chapter": "章节",
            "choice": "选项",
            "npc": "NPC",
            "ending": "结局",
        }
        for kind in ("chapter", "choice", "npc", "ending"):
            if not source[kind]:
                continue
            missing = sorted(source[kind] - extracted[kind])
            invented = sorted(extracted[kind] - source[kind])
            if missing:
                issues.append({
                    "code": f"EXTRACTION_MISSING_{kind.upper()}_IDS",
                    "message": f"Markdown 中的{labels[kind]}未完整提取到 JSON: {missing}",
                    "severity": "error",
                })
            if invented:
                issues.append({
                    "code": f"EXTRACTION_INVENTED_{kind.upper()}_IDS",
                    "message": f"JSON 中出现 Markdown 不存在的{labels[kind]}: {invented}",
                    "severity": "error",
                })

        errors = [issue for issue in issues if issue["severity"] == "error"]
        return {
            "valid": not errors,
            "issues": issues,
            "error_count": len(errors),
            "warning_count": 0,
            "source_counts": {key: len(value) for key, value in source.items()},
            "extracted_counts": {key: len(value) for key, value in extracted.items()},
        }

    @staticmethod
    def validate_programmatic(
        script_json: dict,
        expected_npc_count: int | None = None,
        expected_decision_point_count: int | None = None,
    ) -> dict:
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
                if expected_decision_point_count is not None and len(dps) != expected_decision_point_count:
                    issues.append({
                        "code": "DECISION_POINT_COUNT_MISMATCH",
                        "message": (
                            f"{ch_id} 决策点数量应为 {expected_decision_point_count} 个，"
                            f"当前 {len(dps)} 个"
                        ),
                        "severity": "error",
                    })
                elif len(dps) < 2:
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
                            issues.append({"code": "INCOMPLETE_EFFECTS", "message": f"{choice_id} 变量影响不足 8 个（当前 {len(effects)} 个）", "severity": "error"})

                        # 检查是否所有 8 个变量都有
                        actual_vars = set(effects.keys())
                        missing = ChapterValidator.EXPECTED_VARIABLES - actual_vars
                        if missing:
                            issues.append({"code": "MISSING_VARIABLES", "message": f"{choice_id} 缺少变量: {sorted(missing)}", "severity": "error"})

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

        # ---- NPC 主表与状态变化 ----
        npcs = script_json.get("npcs", [])
        npc_ids: set[str] = set()
        if expected_npc_count is not None and len(npcs) != expected_npc_count:
            issues.append({
                "code": "NPC_COUNT_MISMATCH",
                "message": f"NPC 数量应为 {expected_npc_count}，当前 {len(npcs)}",
                "severity": "error",
            })
        for npc in npcs:
            npc_id = str(npc.get("npc_id") or "").strip()
            if not npc_id:
                issues.append({"code": "MISSING_NPC_ID", "message": "NPC 主表存在缺失 npc_id 的条目", "severity": "error"})
                continue
            if npc_id in npc_ids:
                issues.append({"code": "DUPLICATE_NPC_ID", "message": f"NPC ID 重复: {npc_id}", "severity": "error"})
            npc_ids.add(npc_id)
            if not str(npc.get("name") or "").strip():
                issues.append({"code": "MISSING_NPC_NAME", "message": f"{npc_id} 缺少 name", "severity": "error"})
            if not str(npc.get("group") or "").strip():
                issues.append({"code": "MISSING_NPC_GROUP", "message": f"{npc_id} 缺少 group", "severity": "error"})
            if npc.get("npc_type") not in {"cadre", "external", "villager"}:
                issues.append({"code": "INVALID_NPC_TYPE", "message": f"{npc_id} 的 npc_type 非法", "severity": "error"})
            for state_key in ("trust_to_player", "attitude_score", "anxiety_level", "granovetter_threshold"):
                value = npc.get(state_key)
                if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 100:
                    issues.append({"code": "INVALID_NPC_STATE", "message": f"{npc_id} 的 {state_key} 必须在 0-100 之间", "severity": "error"})

        if expected_npc_count is not None:
            for ch in chapters:
                for dp in ChapterValidator._get_decision_points(ch):
                    for opt in dp.get("options", []):
                        changes = opt.get("npc_state_changes") or {}
                        if not isinstance(changes, dict):
                            issues.append({"code": "INVALID_NPC_STATE_CHANGES", "message": f"{opt.get('choice_id', '?')} 的 npc_state_changes 必须是对象", "severity": "error"})
                            continue
                        unknown_ids = set(changes) - npc_ids
                        if unknown_ids:
                            issues.append({"code": "UNKNOWN_NPC_STATE_CHANGE", "message": f"{opt.get('choice_id', '?')} 引用了不存在的 NPC: {sorted(unknown_ids)}", "severity": "error"})

        # ---- 衔接性 ----
        for i in range(len(chapters) - 1):
            cp = chapters[i].get("checkpoint", {}) or {}
            next_ch = cp.get("next_chapter", "")
            expected_next = chapters[i + 1].get("chapter_id", "")
            if next_ch and next_ch != expected_next and next_ch != "ending_evaluation":
                issues.append({"code": "CHAIN_BROKEN", "message": f"第 {i+1} 章 next_chapter='{next_ch}' 不指向第 {i+2} 章 ('{expected_next}')", "severity": "error"})

        # ---- Flag 一致性 ----
        flag_lifecycle = ChapterValidator.build_flag_lifecycle(script_json)
        orphan_flags = {
            flag for flag, evidence in flag_lifecycle.items()
            if not evidence["created_at"]
            and (evidence["referenced_at"] or evidence["used_by_endings"])
        }
        redundant_forbidden_flags = {
            flag for flag in orphan_flags
            if all(
                path.endswith("/flags_forbidden")
                for path in (
                    flag_lifecycle[flag]["referenced_at"]
                    + flag_lifecycle[flag]["used_by_endings"]
                )
            )
        }
        missing_flags = orphan_flags - redundant_forbidden_flags
        if missing_flags:
            issues.append({"code": "ORPHAN_FLAGS", "message": f"引用了未创建的 flag: {sorted(missing_flags)}", "severity": "warning"})
        if redundant_forbidden_flags:
            issues.append({
                "code": "REDUNDANT_FORBIDDEN_FLAGS",
                "message": f"结局禁止了不会被创建的 flag，可删除这些冗余条件: {sorted(redundant_forbidden_flags)}",
                "severity": "warning",
            })

        # ---- 结局检查 ----
        endings = script_json.get("endings", [])
        if len(endings) < 3:
            issues.append({"code": "TOO_FEW_ENDINGS", "message": f"结局少于 3 个（当前 {len(endings)} 个）", "severity": "error"})

        has_good = any(e.get("type") == "good" for e in endings)
        has_bad = any(e.get("type") == "bad" for e in endings)
        if not has_good:
            issues.append({"code": "NO_GOOD_ENDING", "message": "缺少 good 类型结局", "severity": "error"})
        if not has_bad:
            issues.append({"code": "NO_BAD_ENDING", "message": "缺少 bad 类型结局", "severity": "error"})
        ending_ids: set[str] = set()
        for ending in endings:
            ending_id = str(ending.get("ending_id") or "").strip()
            if not ending_id:
                issues.append({"code": "MISSING_ENDING_ID", "message": "结局缺失 ending_id", "severity": "error"})
            elif ending_id in ending_ids:
                issues.append({"code": "DUPLICATE_ENDING_ID", "message": f"结局 ID 重复: {ending_id}", "severity": "error"})
            ending_ids.add(ending_id)
            conditions = ending.get("conditions")
            if not isinstance(conditions, dict) or not any(conditions.get(key) for key in (
                "variables", "variable_expression", "flags_required",
                "flags_required_all", "flags_required_any", "flags_forbidden",
                "npc_states",
            )):
                issues.append({"code": "MISSING_ENDING_CONDITIONS", "message": f"{ending_id or '?'} 缺少可执行结局条件", "severity": "error"})
                continue
            variable_conditions = conditions.get("variables") or {}
            if not isinstance(variable_conditions, dict):
                issues.append({
                    "code": "INVALID_ENDING_VARIABLE_CONDITION",
                    "message": f"{ending_id or '?'} 的 variables 条件必须是对象",
                    "severity": "error",
                })
                continue
            for variable, expression in variable_conditions.items():
                if not isinstance(expression, str) or not ChapterValidator.ENDING_CONDITION_PATTERN.fullmatch(expression):
                    issues.append({
                        "code": "INVALID_ENDING_VARIABLE_CONDITION",
                        "message": f"{ending_id or '?'} 的 {variable} 条件必须保留比较符，例如 '>= 1000'；当前为 {expression!r}",
                        "severity": "error",
                    })

        errors = [i for i in issues if i.get("severity") == "error"]
        return {
            "valid": len(errors) == 0,
            "issues": issues,
            "error_count": len(errors),
            "warning_count": len([i for i in issues if i.get("severity") == "warning"]),
        }
