"""基于 Qwen 的剧本初稿生成器。"""

from dataclasses import asdict, replace
import json
import os
from typing import Any, Callable

from src.config import QwenConfig, load_dotenv
from src.domain.game_action import GameActionRule
from src.domain.game_state import GameState
from src.domain.npc_state import NPCState
from src.domain.script_design import ScriptCitation, ScriptDesign, ScriptEventOutline
from src.domain.source_context import SourceContext
from src.generation.pa_backend_script_client import PABackendScriptClient
from src.generation.qwen_client import ChatMessage, QwenChatClient


class ScriptGenerationError(RuntimeError):
    """当剧本生成结果无法解析时抛出。"""


GenerationProgressCallback = Callable[[int, int, str, int], None]


class QwenScriptGenerator:
    """根据检索资料生成结构化剧本初稿。"""

    def __init__(self, client: Any | None = None) -> None:
        self._client = client or self._client_from_env()

    def _client_from_env(self) -> Any:
        load_dotenv(override=False)
        backend = os.getenv("SCRIPT_GENERATION_BACKEND", "qwen").strip().lower()
        if backend == "pa_backend":
            return PABackendScriptClient()
        if backend != "qwen":
            raise ValueError(f"Unsupported SCRIPT_GENERATION_BACKEND: {backend}")
        return QwenChatClient(QwenConfig.from_env())

    def generate(
        self,
        query: str,
        contexts: list[SourceContext],
        feedback: str = "",
    ) -> ScriptDesign:
        if not query.strip():
            raise ValueError("query must not be empty")

        content = self._client.complete(self._build_messages(query, contexts, feedback), temperature=0.2)
        payload = self._parse_json_object(content)
        return self._build_script_design(payload)

    def generate_full(
        self,
        query: str,
        contexts: list[SourceContext],
        feedback: str = "",
        progress_callback: GenerationProgressCallback | None = None,
    ) -> ScriptDesign:
        """通过模块化多阶段生成完整结构化初稿。"""

        messages = self._build_outline_stage_messages(query, contexts, feedback)
        self._report_progress(progress_callback, 1, 7, "生成剧本骨架", messages)
        content = self._client.complete(messages, temperature=0.2)
        script = self._build_script_design(self._parse_json_object(content))

        npc_messages = self._build_npc_stage_messages(query, contexts, script, feedback)
        self._report_progress(progress_callback, 2, 7, "扩充 NPC", npc_messages)
        npc_payload = self._parse_json_object(self._client.complete(npc_messages, temperature=0.2))
        npc_seed = self._build_npc_states(
            self._module_value(npc_payload, "npc_seed", "npcs")
        )
        if not npc_seed:
            raise ScriptGenerationError(
                f"NPC stage returned an empty npc_seed: {self._payload_preview(npc_payload)}"
            )
        script = replace(script, npc_seed=npc_seed)

        action_batches = [
            ("沟通调查类行动", "沟通、入户、调查、信息核验、公开说明和承诺协商"),
            ("资源协调类行动", "资源分配、行政施压、跨部门协调、舆情应对和上级汇报"),
        ]
        action_rules: list[GameActionRule] = []
        for batch_index, (batch_name, categories) in enumerate(action_batches, start=3):
            action_messages = self._build_action_stage_messages(
                query,
                contexts,
                script,
                feedback,
                categories,
                [rule.action_id for rule in action_rules],
            )
            self._report_progress(progress_callback, batch_index, 7, batch_name, action_messages)
            action_payload = self._parse_json_object(
                self._client.complete(action_messages, temperature=0.2)
            )
            batch_rules = self._build_action_rules(
                self._module_value(action_payload, "action_rules", "actions")
            )
            if not batch_rules:
                raise ScriptGenerationError(
                    f"{batch_name} stage returned empty action_rules: "
                    f"{self._payload_preview(action_payload)}"
                )
            action_rules.extend(batch_rules)
        script = replace(script, action_rules=action_rules)

        event_batches = [
            ("第 1 至 30 天事件", "第 1 至 30 天，完成启动、摸底和初步分化", False),
            ("第 31 至 60 天事件", "第 31 至 60 天，推动矛盾升级、督查介入和策略转折", False),
            ("第 61 至 90 天事件", "第 61 至 90 天，完成危机收束、结果分化和结局铺垫", True),
        ]
        event_outline: list[ScriptEventOutline] = []
        night_rules: list[str] = []
        payoff_notes: list[str] = []
        citations = list(script.citations)
        for stage, (batch_name, period, include_summary) in enumerate(event_batches, start=5):
            event_messages = self._build_event_stage_messages(
                query,
                contexts,
                script,
                feedback,
                period,
                [event.event_id for event in event_outline],
                include_summary,
            )
            self._report_progress(progress_callback, stage, 7, batch_name, event_messages)
            event_payload = self._parse_json_object(
                self._client.complete(event_messages, temperature=0.2)
            )
            batch_events = self._build_event_outline(
                self._module_value(event_payload, "event_outline", "events")
            )
            if not batch_events:
                raise ScriptGenerationError(
                    f"{batch_name} stage returned empty event_outline: "
                    f"{self._payload_preview(event_payload)}"
                )
            event_outline.extend(batch_events)
            if include_summary:
                night_rules = self._string_list(
                    self._module_value(event_payload, "night_rules")
                )
                payoff_notes = self._string_list(
                    self._module_value(event_payload, "payoff_notes")
                )
            citations = self._merge_citations(
                citations,
                self._build_citations(self._module_value(event_payload, "citations")),
            )
        script = replace(
            script,
            event_outline=event_outline,
            night_rules=night_rules,
            payoff_notes=payoff_notes,
            citations=citations,
        )

        return script

    def _build_outline_stage_messages(
        self,
        query: str,
        contexts: list[SourceContext],
        feedback: str,
    ) -> list[ChatMessage]:
        payload = {
            "query": query,
            "source_contexts": self._context_payload(contexts),
            "human_feedback": feedback.strip(),
            "output_contract": {
                "title": "string",
                "premise": "string",
                "player_role": "string",
                "core_conflict": "string",
                "initial_game_state": {
                    "day": 1,
                    "action_points": 3,
                    "budget_remaining": 8000,
                    "signed_households": 0,
                    "total_households": 36,
                    "social_stability_index": 70,
                    "political_credit": 70,
                    "cadre_execution_index": 60,
                },
                "citations": [
                    {
                        "citation_id": "string",
                        "source_context_id": "string",
                        "title": "string",
                        "note": "string",
                    }
                ],
            },
            "rules": [
                "只输出总体设定、玩家角色、核心冲突、初始状态和 citations。",
                "不要生成 NPC、行动规则、事件或夜间规则，这些模块会在后续阶段生成。",
                "剧本时间跨度为 90 天，初始状态应体现资源、绩效和稳定压力。",
                "citations 只能使用 source_contexts 中的 reference_id，或使用 query 表示原始需求。",
                "所有数值字段必须是整数，输出必须是合法 JSON 对象，不要 Markdown。",
            ],
        }
        return self._stage_messages("总体设计器", payload)

    def _build_npc_stage_messages(
        self,
        query: str,
        contexts: list[SourceContext],
        script: ScriptDesign,
        feedback: str,
    ) -> list[ChatMessage]:
        payload = self._stage_payload(query, contexts, script, feedback)
        payload["output_contract"] = {
            "npc_seed": [
                {
                    "npc_id": "string",
                    "name": "string",
                    "npc_type": "cadre | external | villager",
                    "group": "string",
                    "trust_to_player": 0,
                    "attitude_score": 0,
                    "anxiety_level": 0,
                    "reference_point": 0,
                    "granovetter_threshold": 0,
                    "core_demand_satisfied": False,
                    "signed": False,
                    "known_info": ["string"],
                    "player_promises": [],
                }
            ]
        }
        payload["rules"] = [
            "只输出 npc_seed，不要输出剧本的其他字段。",
            "生成 12 至 15 个 NPC，完整替换骨架中的 npc_seed。",
            "必须覆盖干部、外部角色和村民，并形成利益冲突、信息差和不同阈值。",
            "NPC 类型只能使用 cadre、external、villager，所有分数字段使用 0 到 100 的整数。",
            "输出必须是合法 JSON 对象，不要 Markdown。",
        ]
        return self._stage_messages("NPC 设计器", payload)

    def _build_action_stage_messages(
        self,
        query: str,
        contexts: list[SourceContext],
        script: ScriptDesign,
        feedback: str,
        categories: str,
        existing_action_ids: list[str],
    ) -> list[ChatMessage]:
        payload = self._stage_payload(query, contexts, script, feedback)
        payload["npc_seed"] = [asdict(npc) for npc in script.npc_seed]
        payload["action_categories"] = categories
        payload["existing_action_ids"] = existing_action_ids
        payload["output_contract"] = {
            "action_rules": [
                {
                    "action_id": "string",
                    "name": "string",
                    "cost_action_points": 1,
                    "budget_cost": 0,
                    "allowed_targets": ["villager"],
                    "preconditions": ["string"],
                    "forbidden_conditions": ["string"],
                    "direct_payoff": {},
                    "side_effects": ["string"],
                    "risk_notes": ["string"],
                    "citations": ["reference_id - title"],
                }
            ]
        }
        payload["rules"] = [
            "只输出 action_rules，不要输出剧本或 NPC 的其他字段。",
            "为 action_categories 指定的类别生成 6 至 8 条行动规则。",
            "action_id 不能与 existing_action_ids 重复。",
            "每条行动都必须有成本、条件、payoff、副作用、风险和资料引用。",
            "citations 只能使用 source_contexts 中的 reference_id，或使用 query 表示原始需求。",
            "输出必须是合法 JSON 对象，不要 Markdown。",
        ]
        return self._stage_messages("行动规则设计器", payload)

    def _build_event_stage_messages(
        self,
        query: str,
        contexts: list[SourceContext],
        script: ScriptDesign,
        feedback: str,
        period: str,
        existing_event_ids: list[str],
        include_summary: bool,
    ) -> list[ChatMessage]:
        payload = self._stage_payload(query, contexts, script, feedback)
        payload["npc_seed"] = [
            {
                "npc_id": npc.npc_id,
                "name": npc.name,
                "npc_type": npc.npc_type,
                "group": npc.group,
            }
            for npc in script.npc_seed
        ]
        payload["action_rules"] = [
            {
                "action_id": rule.action_id,
                "name": rule.name,
                "allowed_targets": rule.allowed_targets,
            }
            for rule in script.action_rules
        ]
        payload["event_period"] = period
        payload["existing_event_ids"] = existing_event_ids
        output_contract: dict[str, Any] = {
            "event_outline": [
                {
                    "event_id": "string",
                    "name": "string",
                    "day_window": "string",
                    "trigger_condition": "string",
                    "description": "string",
                    "payoff": {},
                    "citations": ["reference_id - title"],
                }
            ]
        }
        if include_summary:
            output_contract["night_rules"] = ["string"]
            output_contract["payoff_notes"] = ["string"]
            output_contract["citations"] = [
                {
                    "citation_id": "string",
                    "source_context_id": "string",
                    "title": "string",
                    "note": "string",
                }
            ]
        payload["output_contract"] = output_contract
        payload["rules"] = [
            "只为 event_period 指定的时间段生成 5 至 7 个事件。",
            "event_id 不能与 existing_event_ids 重复。",
            "事件应引用已给出的 NPC ID 和行动 ID，形成前后相连的触发链。",
            "每个事件必须有 payoff 和资料引用。",
            "citations 只能使用 source_contexts 中的 reference_id，或使用 query 表示原始需求。",
            "输出必须是合法 JSON 对象，不要 Markdown。",
        ]
        if include_summary:
            payload["rules"].append(
                "同时生成至少 3 条夜间互动规则和完整 payoff_notes。"
            )
        return self._stage_messages("事件与夜间推演设计器", payload)

    def _stage_payload(
        self,
        query: str,
        contexts: list[SourceContext],
        script: ScriptDesign,
        feedback: str,
    ) -> dict[str, Any]:
        return {
            "query": query,
            "human_feedback": feedback.strip(),
            "script_summary": {
                "title": script.title,
                "premise": script.premise,
                "player_role": script.player_role,
                "core_conflict": script.core_conflict,
                "initial_game_state": asdict(script.initial_game_state),
            },
            "source_contexts": self._context_payload(contexts),
        }

    def _stage_messages(self, role_name: str, payload: dict[str, Any]) -> list[ChatMessage]:
        return [
            ChatMessage(
                role="system",
                content=(
                    f"你是严肃游戏《父母官》的{role_name}。"
                    "只生成当前模块要求的字段，必须输出一个合法 JSON 对象。"
                ),
            ),
            ChatMessage(role="user", content=json.dumps(payload, ensure_ascii=False)),
        ]

    def _report_progress(
        self,
        callback: GenerationProgressCallback | None,
        stage: int,
        total_stages: int,
        name: str,
        messages: list[ChatMessage],
    ) -> None:
        if callback is None:
            return
        size_method = getattr(self._client, "request_size_bytes", None)
        if callable(size_method):
            request_bytes = size_method(messages, temperature=0.2)
        else:
            request_bytes = sum(len(message.content.encode("utf-8")) for message in messages)
        callback(stage, total_stages, name, request_bytes)

    def revise(
        self,
        query: str,
        previous_script: dict[str, Any],
        contexts: list[SourceContext],
        feedback: str,
    ) -> ScriptDesign:
        """根据旧稿和人工反馈生成修订稿。"""

        if not query.strip():
            raise ValueError("query must not be empty")
        if not feedback.strip():
            raise ValueError("feedback must not be empty")
        if not previous_script:
            raise ValueError("previous_script must not be empty")

        content = self._client.complete(
            self._build_messages(
                query,
                contexts,
                feedback,
                previous_script=previous_script,
            ),
            temperature=0.2,
        )
        payload = self._parse_json_object(content)
        return self._build_script_design(payload)

    def _build_messages(
        self,
        query: str,
        contexts: list[SourceContext],
        feedback: str = "",
        previous_script: dict[str, Any] | None = None,
    ) -> list[ChatMessage]:
        user_payload = {
            "query": query,
            "source_contexts": self._context_payload(contexts),
            "human_feedback": feedback.strip(),
            "previous_script": previous_script,
            "output_contract": {
                "title": "string",
                "premise": "string",
                "player_role": "string",
                "core_conflict": "string",
                "initial_game_state": {
                    "day": 1,
                    "action_points": 3,
                    "budget_remaining": 8000,
                    "signed_households": 0,
                    "total_households": 36,
                    "social_stability_index": 70,
                    "political_credit": 70,
                    "cadre_execution_index": 60,
                },
                "npc_seed": [
                    {
                        "npc_id": "string",
                        "name": "string",
                        "npc_type": "cadre | external | villager",
                        "group": "string",
                        "trust_to_player": 0,
                        "attitude_score": 0,
                        "anxiety_level": 0,
                        "reference_point": 0,
                        "granovetter_threshold": 0,
                        "core_demand_satisfied": False,
                        "signed": False,
                        "known_info": ["string"],
                        "player_promises": [],
                    }
                ],
                "action_rules": [
                    {
                        "action_id": "string",
                        "name": "string",
                        "cost_action_points": 1,
                        "budget_cost": 0,
                        "allowed_targets": ["villager"],
                        "preconditions": ["string"],
                        "forbidden_conditions": ["string"],
                        "direct_payoff": {},
                        "side_effects": ["string"],
                        "risk_notes": ["string"],
                        "citations": ["reference_id - title"],
                    }
                ],
                "event_outline": [
                    {
                        "event_id": "string",
                        "name": "string",
                        "day_window": "string",
                        "trigger_condition": "string",
                        "description": "string",
                        "payoff": {},
                        "citations": ["reference_id - title"],
                    }
                ],
                "night_rules": ["string"],
                "payoff_notes": ["string"],
                "citations": [
                    {
                        "citation_id": "string",
                        "source_context_id": "string",
                        "title": "string",
                        "note": "string",
                    }
                ],
            },
            "rules": [
                "生成《父母官》方向的剧本初稿，重点是规则、约束和 payoff，不要只写文学设定。",
                "输出必须是一个合法 JSON 对象，不要 Markdown。",
                "首版控制规模：7 个左右 NPC、8 个左右行动规则、5 个左右事件概要。",
                "所有 action_rules 和 event_outline 都必须带 citations。",
                "citations 只能引用 source_contexts 中真实存在的 reference_id 和 title。",
                "如果资料不足，允许使用 query 本身作为设定来源，并在 citation note 中写明来自原始需求。",
                "NPC 类型只能使用 cadre、external、villager。",
                "所有数值字段必须是整数，不要使用百分号字符串。",
                "语言必须是中文。",
                "如果 human_feedback 非空，必须优先满足其中的人工反馈，但不能破坏 JSON 输出结构。",
                "如果 previous_script 非空，应在旧稿基础上修订，保留不受反馈影响且合理的内容，不要无故从零重写。",
                "修订后仍需保证行动规则、事件、NPC 和全局状态之间的数据约束一致。",
            ],
        }

        return [
            ChatMessage(
                role="system",
                content=(
                    "你是严肃游戏《父母官》的剧本生成器。"
                    "你的输出要服务后续代码实现：规则、约束、payoff、NPC 初始状态必须结构化。"
                    "必须只输出一个合法 JSON 对象。"
                ),
            ),
            ChatMessage(role="user", content=json.dumps(user_payload, ensure_ascii=False)),
        ]

    def _context_payload(self, contexts: list[SourceContext]) -> list[dict[str, Any]]:
        return [
            {
                "reference_id": context.id,
                "title": context.title,
                "content": context.content,
                "metadata": context.metadata,
            }
            for context in contexts
        ]

    def _parse_json_object(self, content: str) -> dict[str, Any]:
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`").strip()
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].strip()

        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            preview = cleaned[:240].replace("\n", " ")
            suffix = cleaned[-240:].replace("\n", " ")
            raise ScriptGenerationError(
                f"Script generation response was not valid JSON: start={preview!r}, end={suffix!r}"
            ) from exc

        if not isinstance(parsed, dict):
            raise ScriptGenerationError("Script generation response must be a JSON object")
        return parsed

    def _build_script_design(self, payload: dict[str, Any]) -> ScriptDesign:
        return ScriptDesign(
            title=self._required_str(payload, "title"),
            premise=self._required_str(payload, "premise"),
            player_role=self._required_str(payload, "player_role"),
            core_conflict=self._required_str(payload, "core_conflict"),
            initial_game_state=self._build_game_state(payload.get("initial_game_state", {})),
            npc_seed=self._build_npc_states(payload.get("npc_seed", [])),
            action_rules=self._build_action_rules(payload.get("action_rules", [])),
            event_outline=self._build_event_outline(payload.get("event_outline", [])),
            night_rules=self._string_list(payload.get("night_rules")),
            payoff_notes=self._string_list(payload.get("payoff_notes")),
            citations=self._build_citations(payload.get("citations", [])),
        )

    def _build_game_state(self, payload: Any) -> GameState:
        if not isinstance(payload, dict):
            payload = {}
        return GameState(
            day=self._int_value(payload.get("day"), 1),
            action_points=self._int_value(payload.get("action_points"), 3),
            budget_remaining=self._int_value(payload.get("budget_remaining"), 8000),
            signed_households=self._int_value(payload.get("signed_households"), 0),
            total_households=self._int_value(payload.get("total_households"), 36),
            social_stability_index=self._int_value(payload.get("social_stability_index"), 70),
            political_credit=self._int_value(payload.get("political_credit"), 70),
            cadre_execution_index=self._int_value(payload.get("cadre_execution_index"), 60),
        )

    def _build_npc_states(self, value: Any) -> list[NPCState]:
        if not isinstance(value, list):
            return []

        npcs = []
        for item in value:
            if not isinstance(item, dict):
                continue
            npcs.append(
                NPCState(
                    npc_id=self._required_str(item, "npc_id"),
                    name=self._required_str(item, "name"),
                    npc_type=self._required_str(item, "npc_type"),
                    group=self._required_str(item, "group"),
                    trust_to_player=self._int_value(item.get("trust_to_player"), 50),
                    attitude_score=self._int_value(item.get("attitude_score"), 50),
                    anxiety_level=self._int_value(item.get("anxiety_level"), 50),
                    reference_point=self._int_value(item.get("reference_point"), 0),
                    granovetter_threshold=self._int_value(item.get("granovetter_threshold"), 50),
                    core_demand_satisfied=bool(item.get("core_demand_satisfied", False)),
                    signed=bool(item.get("signed", False)),
                    known_info=self._string_list(item.get("known_info")),
                    player_promises=self._string_list(item.get("player_promises")),
                )
            )
        return npcs

    def _build_action_rules(self, value: Any) -> list[GameActionRule]:
        if not isinstance(value, list):
            return []

        rules = []
        for item in value:
            if not isinstance(item, dict):
                continue
            rules.append(
                GameActionRule(
                    action_id=self._required_str(item, "action_id"),
                    name=self._required_str(item, "name"),
                    cost_action_points=self._int_value(item.get("cost_action_points"), 1),
                    budget_cost=self._int_value(item.get("budget_cost"), 0),
                    allowed_targets=self._string_list(item.get("allowed_targets")),
                    preconditions=self._string_list(item.get("preconditions")),
                    forbidden_conditions=self._string_list(item.get("forbidden_conditions")),
                    direct_payoff=item.get("direct_payoff", {}) if isinstance(item.get("direct_payoff"), dict) else {},
                    side_effects=self._string_list(item.get("side_effects")),
                    risk_notes=self._string_list(item.get("risk_notes")),
                    citations=self._string_list(item.get("citations")),
                )
            )
        return rules

    def _build_event_outline(self, value: Any) -> list[ScriptEventOutline]:
        if not isinstance(value, list):
            return []

        events = []
        for item in value:
            if not isinstance(item, dict):
                continue
            events.append(
                ScriptEventOutline(
                    event_id=self._required_str(item, "event_id"),
                    name=self._required_str(item, "name"),
                    day_window=self._required_str(item, "day_window"),
                    trigger_condition=self._required_str(item, "trigger_condition"),
                    description=self._required_str(item, "description"),
                    payoff=item.get("payoff", {}) if isinstance(item.get("payoff"), dict) else {},
                    citations=self._string_list(item.get("citations")),
                )
            )
        return events

    def _build_citations(self, value: Any) -> list[ScriptCitation]:
        if not isinstance(value, list):
            return []

        citations = []
        for item in value:
            if not isinstance(item, dict):
                continue
            source_context_id = str(item.get("source_context_id", "")).strip()
            if not source_context_id:
                source_context_id = "query"
            citations.append(
                ScriptCitation(
                    citation_id=self._required_str(item, "citation_id"),
                    source_context_id=source_context_id,
                    title=self._required_str(item, "title"),
                    note=self._required_str(item, "note"),
                )
            )
        return citations

    def _merge_citations(
        self,
        existing: list[ScriptCitation],
        additions: list[ScriptCitation],
    ) -> list[ScriptCitation]:
        merged: dict[str, ScriptCitation] = {
            citation.citation_id: citation for citation in existing
        }
        for citation in additions:
            merged[citation.citation_id] = citation
        return list(merged.values())

    def _module_value(
        self,
        payload: dict[str, Any],
        key: str,
        *aliases: str,
    ) -> Any:
        candidate_keys = (key, *aliases)
        for candidate in candidate_keys:
            if candidate in payload:
                return payload[candidate]

        for wrapper in ("data", "result", "script"):
            nested = payload.get(wrapper)
            if not isinstance(nested, dict):
                continue
            for candidate in candidate_keys:
                if candidate in nested:
                    return nested[candidate]
        return None

    def _payload_preview(self, payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False)[:800]

    def _required_str(self, payload: dict[str, Any], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ScriptGenerationError(f"script field '{key}' must be a non-empty string")
        return value.strip()

    def _string_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]

    def _int_value(self, value: Any, default: int) -> int:
        if isinstance(value, bool):
            return default
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
        return default
