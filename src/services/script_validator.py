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

        if full_draft:
            self._check_minimum_count("NPC", len(script.npc_seed), 10, issues)
            self._check_minimum_count("行动规则", len(script.action_rules), 10, issues)
            self._check_minimum_count("事件", len(script.event_outline), 12, issues)
            self._check_minimum_count("夜间规则", len(script.night_rules), 3, issues)

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

    def _check_minimum_count(
        self,
        label: str,
        actual: int,
        minimum: int,
        issues: list[str],
    ) -> None:
        if actual < minimum:
            issues.append(f"完整初稿至少需要 {minimum} 个{label}，当前只有 {actual} 个")
