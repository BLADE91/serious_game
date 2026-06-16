"""剧本生成结果校验。"""

from src.domain.script_design import ScriptDesign
from src.domain.source_context import SourceContext


class ScriptValidationError(ValueError):
    """剧本不满足结构或交付约束。"""

    def __init__(self, issues: list[str]) -> None:
        self.issues = issues
        super().__init__("；".join(issues))


class ScriptValidator:
    """检查剧本中的标识符、引用、规模和关键数值。"""

    def validate(
        self,
        script: ScriptDesign,
        contexts: list[SourceContext],
        full_draft: bool = False,
    ) -> None:
        issues: list[str] = []

        # 旧结构校验
        self._check_unique(
            [npc.npc_id for npc in script.npc_seed],
            "NPC ID",
            issues,
        )
        self._check_unique(
            [rule.action_id for rule in script.action_rules],
            "行动规则 ID",
            issues,
        )
        self._check_unique(
            [event.event_id for event in script.event_outline],
            "事件 ID",
            issues,
        )
        self._check_unique(
            [citation.citation_id for citation in script.citations],
            "引用 ID",
            issues,
        )
        self._check_state(script, issues)
        self._check_citations(script, contexts, issues)

        # 新结构校验
        self._check_decision_points(script, issues)
        self._check_acts(script, issues)
        self._check_endings(script, issues)
        self._check_npc_relationships(script, issues)

        if full_draft:
            self._check_full_draft_requirements(script, issues)

        if issues:
            raise ScriptValidationError(issues)

    def _check_unique(self, values: list[str], label: str, issues: list[str]) -> None:
        duplicates = sorted({value for value in values if values.count(value) > 1})
        if duplicates:
            issues.append(f"{label} 重复: {', '.join(duplicates)}")

    def _check_state(self, script: ScriptDesign, issues: list[str]) -> None:
        state = script.initial_game_state
        if state.action_points < 0:
            issues.append("初始行动点不能为负数")
        if state.budget_remaining < 0:
            issues.append("初始预算不能为负数")
        if state.signed_households < 0:
            issues.append("初始签约户数不能为负数")
        if state.signed_households > state.total_households:
            issues.append("初始签约户数不能超过总户数")

        for name, value in (
            ("社会稳定指数", state.social_stability_index),
            ("政治信用", state.political_credit),
            ("干部执行指数", state.cadre_execution_index),
        ):
            if value < 0 or value > 100:
                issues.append(f"{name}必须在 0 到 100 之间")

    def _check_citations(
        self,
        script: ScriptDesign,
        contexts: list[SourceContext],
        issues: list[str],
    ) -> None:
        if not contexts:
            return

        valid_ids = {context.id for context in contexts}
        valid_ids.add("query")
        for citation in script.citations:
            if citation.source_context_id not in valid_ids:
                issues.append(
                    f"引用 {citation.citation_id} 指向未知资料: {citation.source_context_id}"
                )

        for owner, citations in self._inline_citations(script):
            for citation in citations:
                if not any(self._matches_reference(citation, context_id) for context_id in valid_ids):
                    issues.append(f"{owner} 使用了未知资料引用: {citation}")

    def _inline_citations(self, script: ScriptDesign):
        for rule in script.action_rules:
            yield f"行动 {rule.action_id}", rule.citations
        for event in script.event_outline:
            yield f"事件 {event.event_id}", event.citations

    def _matches_reference(self, citation: str, context_id: str) -> bool:
        return citation == context_id or citation.startswith(f"{context_id} ") or citation.startswith(
            f"{context_id}-"
        )

    # ---- 新结构校验 ----

    def _check_decision_points(self, script: ScriptDesign, issues: list[str]) -> None:
        if not script.decision_points:
            return

        self._check_unique(
            [dp.decision_id for dp in script.decision_points],
            "决策点 ID",
            issues,
        )

        valid_npc_ids = {npc.npc_id for npc in script.npc_seed}
        for dp in script.decision_points:
            # 每个决策点至少 3 个选项
            if len(dp.options) < 3:
                issues.append(
                    f"决策点 {dp.decision_id} 只有 {len(dp.options)} 个选项，至少需要 3 个"
                )
            # 选项 ID 唯一性
            option_ids = [opt.option_id for opt in dp.options]
            dupes = sorted({oid for oid in option_ids if option_ids.count(oid) > 1})
            if dupes:
                issues.append(f"决策点 {dp.decision_id} 内选项 ID 重复: {', '.join(dupes)}")
            # 关联的 NPC 必须存在
            if valid_npc_ids:
                for npc_id in dp.affected_npc_ids:
                    if npc_id not in valid_npc_ids:
                        issues.append(
                            f"决策点 {dp.decision_id} 引用了不存在的 NPC: {npc_id}"
                        )

    def _check_acts(self, script: ScriptDesign, issues: list[str]) -> None:
        if not script.acts:
            return

        act_numbers = [act.act_number for act in script.acts]
        if sorted(act_numbers) != [1, 2, 3]:
            issues.append("三幕结构必须包含 act_number 为 1、2、3 的三幕")

        if script.decision_points:
            valid_dp_ids = {dp.decision_id for dp in script.decision_points}
            for act in script.acts:
                for dp_id in act.decision_point_ids:
                    if dp_id not in valid_dp_ids:
                        issues.append(f"第{act.act_number}幕引用了不存在的决策点: {dp_id}")

    def _check_endings(self, script: ScriptDesign, issues: list[str]) -> None:
        if not script.endings:
            return

        self._check_unique(
            [e.ending_id for e in script.endings],
            "结局 ID",
            issues,
        )

        ending_types = {e.ending_type for e in script.endings}
        if "good" not in ending_types:
            issues.append("至少需要一个 good 类型结局")
        if "bad" not in ending_types:
            issues.append("至少需要一个 bad 类型结局")

    def _check_npc_relationships(self, script: ScriptDesign, issues: list[str]) -> None:
        if not script.npc_relationships:
            return

        valid_npc_ids = {npc.npc_id for npc in script.npc_seed}
        if not valid_npc_ids:
            return

        for rel in script.npc_relationships:
            if rel.from_npc_id not in valid_npc_ids:
                issues.append(f"NPC 关系引用了不存在的 from_npc: {rel.from_npc_id}")
            if rel.to_npc_id not in valid_npc_ids:
                issues.append(f"NPC 关系引用了不存在的 to_npc: {rel.to_npc_id}")

    def _check_full_draft_requirements(
        self,
        script: ScriptDesign,
        issues: list[str],
    ) -> None:
        """完整初稿的最低数量要求（新旧结构分别检查）。"""
        # 如果使用新结构，检查新字段
        if script.decision_points or script.acts or script.endings:
            if script.npc_seed:
                self._check_minimum_count("NPC", len(script.npc_seed), 10, issues)
            self._check_minimum_count("决策点", len(script.decision_points), 12, issues)
            self._check_minimum_count("结局", len(script.endings), 3, issues)
            if not script.acts:
                issues.append("完整初稿必须包含三幕结构")
            return

        # 旧结构的最低数量要求
        self._check_minimum_count("NPC", len(script.npc_seed), 10, issues)
        self._check_minimum_count("行动规则", len(script.action_rules), 10, issues)
        self._check_minimum_count("事件", len(script.event_outline), 12, issues)
        self._check_minimum_count("夜间规则", len(script.night_rules), 3, issues)

    # ---- 通用工具 ----

    def _check_minimum_count(
        self,
        label: str,
        actual: int,
        minimum: int,
        issues: list[str],
    ) -> None:
        if actual < minimum:
            issues.append(f"完整初稿至少需要 {minimum} 个{label}，当前只有 {actual} 个")
