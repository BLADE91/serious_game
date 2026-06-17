"""基于 LLM 的剧本初稿生成器。"""

from dataclasses import asdict, replace
import json
import os
import threading
from typing import Any, Callable

from src.config import QwenConfig, load_dotenv
from src.domain.act_structure import ActStructure
from src.domain.decision_point import DecisionOption, DecisionPoint
from src.domain.ending_condition import EndingCondition
from src.domain.game_action import GameActionRule
from src.domain.game_state import GameState
from src.domain.npc_relationship import NPCRelationship
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

    def __init__(
        self,
        client: Any | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self._cancel_event = cancel_event
        self._client = client or self._client_from_env()

    def _client_from_env(self) -> Any:
        load_dotenv(override=False)
        backend = os.getenv("SCRIPT_GENERATION_BACKEND", "qwen").strip().lower()
        if backend == "pa_backend":
            return PABackendScriptClient(cancel_event=self._cancel_event)
        if backend != "qwen":
            raise ValueError(f"Unsupported SCRIPT_GENERATION_BACKEND: {backend}")
        return QwenChatClient(QwenConfig.from_env())

    def cancel_active_request(self) -> None:
        """取消正在进行的 HTTP 请求（委托给底层 PABackendScriptClient）。"""
        if hasattr(self._client, 'cancel_active_request'):
            self._client.cancel_active_request()

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
        """通过模块化多阶段生成完整结构化初稿。

        新流程（7 阶段）：
        1. 总体设计 + 三幕结构
        2. NPC 网络（NPC + 关系网）
        3. 第一幕决策点（开局破冰）
        4. 第二幕决策点 A（矛盾升级前半）
        5. 第二幕决策点 B（矛盾升级后半）
        6. 第三幕决策点（收束结局）
        7. 多结局条件
        """

        # 阶段 1：总体设计 + 三幕结构
        messages = self._build_skeleton_stage_messages(query, contexts, feedback)
        self._report_progress(progress_callback, 1, 7, "总体设计与三幕结构", messages)
        skeleton_payload = self._parse_json_object(self._client.complete(messages, temperature=0.2))
        script = self._build_script_design(skeleton_payload)
        acts = self._build_acts(skeleton_payload.get("acts", []))
        if len(acts) != 3:
            raise ScriptGenerationError(
                f"骨架阶段必须返回 3 幕，实际返回 {len(acts)} 幕"
            )
        script = replace(script, acts=acts)

        # 阶段 2：NPC 网络
        npc_messages = self._build_npc_network_stage_messages(query, contexts, script, feedback)
        self._report_progress(progress_callback, 2, 7, "NPC 关系网络", npc_messages)
        npc_payload = self._parse_json_object(self._client.complete(npc_messages, temperature=0.2))
        npc_seed = self._build_npc_states(
            self._module_value(npc_payload, "npc_seed", "npcs")
        )
        if not npc_seed:
            raise ScriptGenerationError(
                f"NPC 阶段返回空 npc_seed: {self._payload_preview(npc_payload)}"
            )
        npc_relationships = self._build_npc_relationships(
            self._module_value(npc_payload, "npc_relationships", "relationships"),
            {npc.npc_id for npc in npc_seed},
        )
        script = replace(script, npc_seed=npc_seed, npc_relationships=npc_relationships)

        # 阶段 3-6：按幕生成决策点
        act_decision_batches = [
            (3, acts[0], "第一幕：开局破冰"),
            (4, acts[1], "第二幕前半：矛盾激化"),
            (5, acts[1], "第二幕后半：博弈转折"),
            (6, acts[2], "第三幕：收束结局"),
        ]
        all_decision_points: list[DecisionPoint] = []
        all_citations = list(script.citations)
        for stage, act, batch_name in act_decision_batches:
            is_second_half = (stage == 5)
            decision_messages = self._build_decision_stage_messages(
                query, contexts, script, feedback,
                act=act,
                existing_decision_ids=[dp.decision_id for dp in all_decision_points],
                is_second_half_of_act=is_second_half,
            )
            self._report_progress(progress_callback, stage, 7, batch_name, decision_messages)
            decision_payload = self._parse_json_object(
                self._client.complete(decision_messages, temperature=0.2)
            )
            batch_decisions = self._build_decision_points(
                self._module_value(decision_payload, "decision_points", "decisions"),
                {npc.npc_id for npc in script.npc_seed},
            )
            if not batch_decisions:
                raise ScriptGenerationError(
                    f"{batch_name} 阶段返回空 decision_points: "
                    f"{self._payload_preview(decision_payload)}"
                )
            all_decision_points.extend(batch_decisions)
            batch_citations = self._build_citations(
                self._module_value(decision_payload, "citations")
            )
            all_citations = self._merge_citations(all_citations, batch_citations)

        # 将决策点 ID 分配回各幕
        decision_ids_per_act: dict[int, list[str]] = {1: [], 2: [], 3: []}
        act1_count = 0
        act2_count = 0
        act3_count = 0
        for dp in all_decision_points:
            # 按 day_window 判断归属
            window = dp.day_window
            if "第1-" in window or "第2-" in window or "第5-" in window or "第10-" in window:
                # 粗略判断：如果窗口起点在 1-15 天，归第一幕
                try:
                    start_day = int(window.replace("第", "").split("-")[0].split("天")[0])
                except (ValueError, IndexError):
                    start_day = 1
                if start_day <= 15:
                    decision_ids_per_act[1].append(dp.decision_id)
                    act1_count += 1
                elif start_day <= 50:
                    decision_ids_per_act[2].append(dp.decision_id)
                    act2_count += 1
                else:
                    decision_ids_per_act[3].append(dp.decision_id)
                    act3_count += 1
            else:
                # 回退：按顺序分配
                if act1_count < 6:
                    decision_ids_per_act[1].append(dp.decision_id)
                    act1_count += 1
                elif act2_count < 8:
                    decision_ids_per_act[2].append(dp.decision_id)
                    act2_count += 1
                else:
                    decision_ids_per_act[3].append(dp.decision_id)
                    act3_count += 1

        updated_acts = [
            replace(act, decision_point_ids=decision_ids_per_act.get(act.act_number, []))
            for act in acts
        ]

        script = replace(
            script,
            decision_points=all_decision_points,
            acts=updated_acts,
            citations=all_citations,
        )

        # 阶段 7：多结局条件
        endings_messages = self._build_endings_stage_messages(query, contexts, script, feedback)
        self._report_progress(progress_callback, 7, 7, "多结局条件", endings_messages)
        endings_payload = self._parse_json_object(
            self._client.complete(endings_messages, temperature=0.2)
        )
        endings = self._build_endings(
            self._module_value(endings_payload, "endings", "ending_conditions")
        )
        if len(endings) < 3:
            raise ScriptGenerationError(
                f"结局阶段至少需要 3 个结局，实际返回 {len(endings)} 个"
            )
        script = replace(script, endings=endings)

        return script

    def _build_skeleton_stage_messages(
        self,
        query: str,
        contexts: list[SourceContext],
        feedback: str,
    ) -> list[ChatMessage]:
        """阶段 1：生成总体设定、GameState 和三幕结构。"""
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
                    "budget_unit": "万元",
                    "signed_households": 0,
                    "total_households": 36,
                    "social_stability_index": 70,
                    "political_credit": 70,
                    "cadre_execution_index": 60,
                },
                "acts": [
                    {
                        "act_number": 1,
                        "title": "开局破冰",
                        "day_range": "第1-15天",
                        "goal": "本幕要达成的阶段性目标",
                        "description": "当前形势概述",
                    },
                    {
                        "act_number": 2,
                        "title": "矛盾升级",
                        "day_range": "第16-50天",
                        "goal": "本幕要达成的阶段性目标",
                        "description": "当前形势概述",
                    },
                    {
                        "act_number": 3,
                        "title": "收束结局",
                        "day_range": "第51-90天",
                        "goal": "本幕要达成的阶段性目标",
                        "description": "当前形势概述",
                    },
                ],
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
                "只输出总体设定、玩家角色、核心冲突、初始状态、三幕结构和 citations。",
                "不要生成 NPC、决策点或结局，这些模块会在后续阶段生成。",
                "三幕的 day_range 必须连续覆盖全部剧情时间。",
                "budget_unit 必须明确填写（如'万元'），所有数额字段为整数。",
                "citations 只能使用 source_contexts 中的 reference_id，或使用 query 表示原始需求。",
                "语言必须是中文，输出必须是合法 JSON 对象，不要 Markdown。",
            ],
        }
        return self._stage_messages("总体设计与三幕规划器", payload)

    def _build_npc_network_stage_messages(
        self,
        query: str,
        contexts: list[SourceContext],
        script: ScriptDesign,
        feedback: str,
    ) -> list[ChatMessage]:
        """阶段 2：生成 NPC 列表和 NPC 之间的关系网络。"""
        payload = self._stage_payload(query, contexts, script, feedback)
        payload["act_structure"] = [
            {
                "act_number": act.act_number,
                "title": act.title,
                "day_range": act.day_range,
                "goal": act.goal,
            }
            for act in script.acts
        ] if script.acts else []
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
            ],
            "npc_relationships": [
                {
                    "from_npc_id": "string",
                    "to_npc_id": "string",
                    "relation_type": "亲属 | 上下级 | 利益同盟 | 矛盾对立 | 信息渠道 | 情感纽带",
                    "strength": 50,
                    "description": "一句话描述关系",
                }
            ],
        }
        payload["rules"] = [
            "只输出 npc_seed 和 npc_relationships，不要输出剧本的其他字段。",
            "生成 12 至 15 个 NPC，完整替换骨架中的 npc_seed。",
            "必须同时覆盖干部、外部角色和村民三类，并形成利益冲突和信息不对称。",
            "NPC 类型只能使用 cadre、external、villager，所有分数字段使用 0 到 100 的整数。",
            "npc_relationships 至少 15 条，构建一个立体的社会关系网络：",
            "  - 亲属关系连接村民 NPC；",
            "  - 上下级关系连接不同层级的干部和外部角色；",
            "  - 利益同盟连接在搬迁中有共同利益的 NPC；",
            "  - 矛盾对立连接利益冲突或历史恩怨的 NPC；",
            "  - 信息渠道连接消息传递链上的 NPC；",
            "  - 情感纽带（恩情、友情等）丰富人物层次。",
            "每条关系的 from_npc_id 和 to_npc_id 必须来自 npc_seed 中的 npc_id。",
            "关系强度 strength 应反映该关系在当前情境下的紧密度（0=形同陌路，100=牢不可破）。",
            "输出必须是合法 JSON 对象，不要 Markdown。",
        ]
        return self._stage_messages("NPC 关系网络设计器", payload)

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

    def _build_decision_stage_messages(
        self,
        query: str,
        contexts: list[SourceContext],
        script: ScriptDesign,
        feedback: str,
        act: ActStructure,
        existing_decision_ids: list[str],
        is_second_half_of_act: bool = False,
    ) -> list[ChatMessage]:
        """阶段 3-6：为指定幕生成决策点序列。"""
        payload = self._stage_payload(query, contexts, script, feedback)
        payload["target_act"] = {
            "act_number": act.act_number,
            "title": act.title,
            "day_range": act.day_range,
            "goal": act.goal,
        }
        payload["is_second_half"] = is_second_half_of_act
        payload["existing_decision_ids"] = existing_decision_ids
        payload["npc_summary"] = [
            {
                "npc_id": npc.npc_id,
                "name": npc.name,
                "npc_type": npc.npc_type,
                "group": npc.group,
            }
            for npc in script.npc_seed
        ]
        payload["relationship_summary"] = [
            {
                "from": rel.from_npc_id,
                "to": rel.to_npc_id,
                "type": rel.relation_type,
            }
            for rel in script.npc_relationships[:30]
        ] if script.npc_relationships else []
        payload["output_contract"] = {
            "decision_points": [
                {
                    "decision_id": "string",
                    "title": "决策点标题",
                    "day_window": "时间窗口，如 第3-5天",
                    "situation": "当前面临的具体困境描述",
                    "options": [
                        {
                            "option_id": "string",
                            "label": "简短选项标签，如 亲自上门劝说",
                            "description": "该选项的具体行动说明",
                            "cost_action_points": 1,
                            "budget_cost": 0,
                            "payoffs": {
                                "global": {"social_stability_index": 5, "political_credit": -3},
                                "npc_V01": {"trust_to_player": 10},
                            },
                            "risks": ["风险说明"],
                            "citation": "reference_id - title",
                        }
                    ],
                    "affected_npc_ids": ["npc_id"],
                    "trigger_condition": "触发条件（可为空字符串表示必定触发）",
                    "is_critical": False,
                    "citations": ["reference_id - title"],
                }
            ]
        }
        # 根据幕和复杂度调整决策点数量
        if act.act_number == 1:
            count_range = "4 至 6 个"
        elif act.act_number == 2:
            count_range = "3 至 4 个" if is_second_half_of_act else "4 至 5 个"
        else:
            count_range = "4 至 5 个"

        payload["rules"] = [
            f"为第{act.act_number}幕（{act.title}，{act.day_range}）生成 {count_range} 决策点。",
            "每个决策点必须有 3 至 5 个选项，选项之间应有明显的策略差异",
            "（如：强硬 vs 怀柔、公开 vs 私下、花钱 vs 省钱的权衡）。",
            "decision_id 不能与 existing_decision_ids 重复。",
            "选项的 payoffs 必须同时包含对全局状态（global）和具体 NPC 的影响。",
            "cost_action_points 和 budget_cost 必须真实反映该选项的代价。",
            "is_critical 标记其中 1-2 个对结局走向有重大影响的关键决策。",
            "决策点之间应有因果关联，后续决策的情境应反映前面选择的后果。",
            "affected_npc_ids 只使用上面 npc_summary 中给出的 npc_id。",
            "citations 只能使用 source_contexts 中的 reference_id，或使用 query。",
            "输出必须是合法 JSON 对象，不要 Markdown。",
        ]
        return self._stage_messages(f"第{act.act_number}幕决策点设计器", payload)

    def _build_endings_stage_messages(
        self,
        query: str,
        contexts: list[SourceContext],
        script: ScriptDesign,
        feedback: str,
    ) -> list[ChatMessage]:
        """阶段 7：生成多结局条件。"""
        payload = self._stage_payload(query, contexts, script, feedback)
        payload["act_structure"] = [
            {
                "act_number": act.act_number,
                "title": act.title,
                "goal": act.goal,
            }
            for act in script.acts
        ] if script.acts else []
        payload["key_decisions"] = [
            {
                "decision_id": dp.decision_id,
                "title": dp.title,
                "is_critical": dp.is_critical,
            }
            for dp in script.decision_points
            if dp.is_critical
        ] if script.decision_points else []
        payload["output_contract"] = {
            "endings": [
                {
                    "ending_id": "string",
                    "title": "结局标题",
                    "description": "结局叙述",
                    "conditions": [
                        "触发条件，如 signed_households >= 34",
                        "触发条件，如 social_stability_index >= 60",
                    ],
                    "ending_type": "good | neutral | bad",
                }
            ]
        }
        payload["rules"] = [
            "至少生成 3 个结局，必须覆盖 good（好结局）、neutral（中性结局）、bad（坏结局）三种类型。",
            "建议生成 4 个结局：1 个 good + 1 个 neutral + 2 个 bad（不同失败路径）。",
            "每个结局的 conditions 应基于 GameState 中的量化指标（signed_households、",
            "social_stability_index、political_credit、budget_remaining 等）。",
            "好结局条件应严格但可达（如签满 34 户以上且社会稳定指数 >= 60）。",
            "坏结局应反映不同失败模式（如资金断裂、民怨爆发、被上级问责）。",
            "中性结局应是勉强完成任务但留下隐患的状态。",
            "描述应体现 45 分钟严肃游戏的反思价值，让玩家在结局中看到选择的长期后果。",
            "输出必须是合法 JSON 对象，不要 Markdown。",
        ]
        return self._stage_messages("多结局设计器", payload)

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
        """根据旧稿和人工反馈生成修订稿（旧格式兼容）。"""

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

    def revise_element(
        self,
        element_type: str,
        element_id: str,
        current_element: dict[str, Any],
        context: dict[str, Any],
        feedback: str,
    ) -> dict[str, Any]:
        """局部定向修订：只修改目标元素，返回修改后的元素 JSON。

        不发送完整剧本，只发送目标元素 + 最小上下文，
        LLM 返回修改后的元素，前端做局部 merge。
        """
        if not feedback.strip():
            raise ValueError("feedback must not be empty")
        if not current_element:
            raise ValueError("current_element must not be empty")

        messages = self._build_revise_element_messages(
            element_type, element_id, current_element, context, feedback,
        )
        content = self._client.complete(messages, temperature=0.2)
        revised = self._parse_json_object(content)
        return revised

    def _build_revise_element_messages(
        self,
        element_type: str,
        element_id: str,
        current_element: dict[str, Any],
        context: dict[str, Any],
        feedback: str,
    ) -> list[ChatMessage]:
        """构建局部修订 prompt：只包含目标元素和必要上下文。"""
        npc_summary = context.get("npc_summary", [])
        script_title = context.get("script_title", "")
        script_premise = context.get("script_premise", "")

        element_labels = {
            "decision_point": "决策点",
            "npc": "NPC",
            "relationship": "关系",
            "option": "决策选项",
            "ending": "结局",
            "act": "幕",
        }
        label = element_labels.get(element_type, element_type)

        user_payload = {
            "element_type": element_type,
            "element_id": element_id,
            "label": label,
            "feedback": feedback.strip(),
            "current_element": current_element,
            "context": {
                "script_title": script_title,
                "script_premise": script_premise,
                "npc_summary": npc_summary,
            },
            "output_contract": current_element,
            "rules": [
                f"你是严肃游戏《父母官》剧本的定向修订器。",
                f"只修改上面这个{label}，不要改动未提及的内容。",
                f"根据 feedback 中的要求进行定向修改。",
                f"保留元素的原始结构（相同的 key），只改需要改的值。",
                f"如果 feedback 要求增加内容（如增加选项），在现有内容基础上追加。",
                f"如果 feedback 要求删除内容，明确执行删除。",
                f"返回修改后的完整{label} JSON 对象，不要 Markdown，不要解释。",
                f"所有数值字段必须是整数。",
            ],
        }
        return [
            ChatMessage(
                role="system",
                content=(
                    f"你是严肃游戏《父母官》的{label}修订器。"
                    "只修改指定的元素，返回修改后的完整 JSON 对象。"
                ),
            ),
            ChatMessage(role="user", content=json.dumps(user_payload, ensure_ascii=False)),
        ]

    def revise_structured(
        self,
        query: str,
        previous_result: dict[str, Any],
        contexts: list[SourceContext],
        feedback: str,
    ) -> ScriptDesign:
        """根据完整旧稿和新格式反馈生成修订稿。

        单次 prompt，保留新结构（三幕、决策点、关系网、结局），
        只对反馈指出的部分做定向修改。
        """
        if not query.strip():
            raise ValueError("query must not be empty")
        if not feedback.strip():
            raise ValueError("feedback must not be empty")
        if not previous_result:
            raise ValueError("previous_result must not be empty")

        content = self._client.complete(
            self._build_revise_structured_messages(query, previous_result, contexts, feedback),
            temperature=0.2,
        )
        payload = self._parse_json_object(content)
        return self._build_script_design(payload)

    def _build_revise_structured_messages(
        self,
        query: str,
        previous_result: dict[str, Any],
        contexts: list[SourceContext],
        feedback: str,
    ) -> list[ChatMessage]:
        """构建新格式修订 prompt：保留结构，定向修改。"""
        previous_script = previous_result.get("script") if isinstance(previous_result, dict) else None
        user_payload = {
            "query": query,
            "human_feedback": feedback.strip(),
            "previous_script": previous_script,
            "source_contexts": self._context_payload(contexts),
            "output_contract": {
                "title": "string",
                "premise": "string",
                "player_role": "string",
                "core_conflict": "string",
                "initial_game_state": {
                    "day": 1, "action_points": 3, "budget_remaining": 8000,
                    "budget_unit": "万元", "signed_households": 0,
                    "total_households": 36, "social_stability_index": 70,
                    "political_credit": 70, "cadre_execution_index": 60,
                },
                "acts": [
                    {"act_number": 1, "title": "string", "day_range": "string",
                     "goal": "string", "description": "string"},
                ],
                "npc_seed": [
                    {"npc_id": "string", "name": "string", "npc_type": "cadre | external | villager",
                     "group": "string", "trust_to_player": 0, "attitude_score": 0,
                     "anxiety_level": 0, "reference_point": 0, "granovetter_threshold": 0,
                     "core_demand_satisfied": False, "signed": False,
                     "known_info": ["string"], "player_promises": []},
                ],
                "npc_relationships": [
                    {"from_npc_id": "string", "to_npc_id": "string",
                     "relation_type": "亲属 | 上下级 | 利益同盟 | 矛盾对立 | 信息渠道 | 情感纽带",
                     "strength": 50, "description": "string"},
                ],
                "decision_points": [
                    {"decision_id": "string", "title": "string", "day_window": "string",
                     "situation": "string",
                     "options": [
                         {"option_id": "string", "label": "string", "description": "string",
                          "cost_action_points": 1, "budget_cost": 0,
                          "payoffs": {}, "risks": ["string"], "citation": "string"},
                     ],
                     "affected_npc_ids": ["string"], "trigger_condition": "",
                     "is_critical": False, "citations": ["string"]},
                ],
                "endings": [
                    {"ending_id": "string", "title": "string", "description": "string",
                     "conditions": ["string"], "ending_type": "good | neutral | bad"},
                ],
                "citations": [
                    {"citation_id": "string", "source_context_id": "string",
                     "title": "string", "note": "string"},
                ],
            },
            "rules": [
                "你是严肃游戏《父母官》的剧本修订器。",
                "在 previous_script 基础上，根据 human_feedback 进行定向修订。",
                "保留不受反馈影响的所有原有内容，不要无故从零重写。",
                "如果反馈要求增加 NPC 关系，只改 npc_relationships 和相关 NPC。",
                "如果反馈要求修改某个决策点的选项，只改那个决策点。",
                "如果反馈要求调整结局条件，只改 endings。",
                "确保修订后 NPC、决策点和关系网之间的交叉引用仍然一致。",
                "输出完整剧本 JSON（不是部分更新），包含所有字段。",
                "所有数值字段必须是整数，语言必须是中文，不要 Markdown。",
            ],
        }
        return [
            ChatMessage(
                role="system",
                content=(
                    "你是严肃游戏《父母官》的剧本修订器。"
                    "在已有剧本基础上根据反馈定向修改，保留未提及的内容。"
                    "必须输出完整合法 JSON 对象。"
                ),
            ),
            ChatMessage(role="user", content=json.dumps(user_payload, ensure_ascii=False)),
        ]

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
            # 旧字段
            npc_seed=self._build_npc_states(payload.get("npc_seed", [])),
            action_rules=self._build_action_rules(payload.get("action_rules", [])),
            event_outline=self._build_event_outline(payload.get("event_outline", [])),
            night_rules=self._string_list(payload.get("night_rules")),
            payoff_notes=self._string_list(payload.get("payoff_notes")),
            citations=self._build_citations(payload.get("citations", [])),
            # 新字段（阶段 1 骨架输出中已包含 acts）
            npc_relationships=self._build_npc_relationships(
                payload.get("npc_relationships", []), set()
            ),
            decision_points=self._build_decision_points(
                payload.get("decision_points", []), set()
            ),
            acts=self._build_acts(payload.get("acts", [])),
            endings=self._build_endings(payload.get("endings", [])),
        )

    def _build_game_state(self, payload: Any) -> GameState:
        if not isinstance(payload, dict):
            payload = {}
        return GameState(
            day=self._int_value(payload.get("day"), 1),
            action_points=self._int_value(payload.get("action_points"), 3),
            budget_remaining=self._int_value(payload.get("budget_remaining"), 8000),
            budget_unit=str(payload.get("budget_unit", "万元")).strip() or "万元",
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

    def _build_acts(self, value: Any) -> list[ActStructure]:
        """从 LLM 响应解析三幕结构。"""
        if not isinstance(value, list):
            return []
        acts = []
        for item in value:
            if not isinstance(item, dict):
                continue
            try:
                acts.append(ActStructure(
                    act_number=self._int_value(item.get("act_number"), 0),
                    title=str(item.get("title", "")).strip(),
                    day_range=str(item.get("day_range", "")).strip(),
                    goal=str(item.get("goal", "")).strip(),
                    description=str(item.get("description", "")).strip(),
                ))
            except ValueError:
                continue
        return acts

    def _build_npc_relationships(
        self,
        value: Any,
        valid_npc_ids: set[str],
    ) -> list[NPCRelationship]:
        """从 LLM 响应解析 NPC 关系网。"""
        if not isinstance(value, list):
            return []
        relationships = []
        for item in value:
            if not isinstance(item, dict):
                continue
            from_id = str(item.get("from_npc_id", "")).strip()
            to_id = str(item.get("to_npc_id", "")).strip()
            if not from_id or not to_id:
                continue
            if valid_npc_ids and (from_id not in valid_npc_ids or to_id not in valid_npc_ids):
                continue  # 跳过引用不存在 NPC 的关系
            if from_id == to_id:
                continue
            relation_type = str(item.get("relation_type", "")).strip()
            if relation_type not in {"亲属", "上下级", "利益同盟", "矛盾对立", "信息渠道", "情感纽带"}:
                continue
            try:
                relationships.append(NPCRelationship(
                    from_npc_id=from_id,
                    to_npc_id=to_id,
                    relation_type=relation_type,
                    strength=self._int_value(item.get("strength"), 50),
                    description=str(item.get("description", "")).strip(),
                ))
            except ValueError:
                continue
        return relationships

    def _build_decision_points(
        self,
        value: Any,
        valid_npc_ids: set[str],
    ) -> list[DecisionPoint]:
        """从 LLM 响应解析决策点列表。"""
        if not isinstance(value, list):
            return []
        decisions = []
        for item in value:
            if not isinstance(item, dict):
                continue
            try:
                options = self._build_decision_options(
                    item.get("options", []),
                    valid_npc_ids,
                )
                if len(options) < 2:
                    continue  # 选项不足，跳过
                affected_ids = [
                    nid.strip() for nid in self._string_list(item.get("affected_npc_ids"))
                    if not valid_npc_ids or nid.strip() in valid_npc_ids
                ]
                decisions.append(DecisionPoint(
                    decision_id=self._required_str(item, "decision_id"),
                    title=self._required_str(item, "title"),
                    day_window=str(item.get("day_window", "")).strip(),
                    situation=self._required_str(item, "situation"),
                    options=options,
                    affected_npc_ids=affected_ids,
                    trigger_condition=str(item.get("trigger_condition", "")).strip(),
                    is_critical=bool(item.get("is_critical", False)),
                    citations=self._string_list(item.get("citations")),
                ))
            except (ValueError, ScriptGenerationError):
                continue
        return decisions

    def _build_decision_options(
        self,
        value: Any,
        valid_npc_ids: set[str],
    ) -> list[DecisionOption]:
        """从 LLM 响应解析决策选项。"""
        if not isinstance(value, list):
            return []
        options = []
        for item in value:
            if not isinstance(item, dict):
                continue
            try:
                options.append(DecisionOption(
                    option_id=self._required_str(item, "option_id"),
                    label=self._required_str(item, "label"),
                    description=str(item.get("description", "")).strip(),
                    cost_action_points=self._int_value(item.get("cost_action_points"), 1),
                    budget_cost=self._int_value(item.get("budget_cost"), 0),
                    payoffs=item.get("payoffs", {}) if isinstance(item.get("payoffs"), dict) else {},
                    risks=self._string_list(item.get("risks")),
                    citation=str(item.get("citation", "")).strip(),
                ))
            except (ValueError, ScriptGenerationError):
                continue
        return options

    def _build_endings(self, value: Any) -> list[EndingCondition]:
        """从 LLM 响应解析多结局条件。"""
        if not isinstance(value, list):
            return []
        endings = []
        for item in value:
            if not isinstance(item, dict):
                continue
            ending_type = str(item.get("ending_type", "neutral")).strip()
            if ending_type not in {"good", "neutral", "bad"}:
                ending_type = "neutral"
            try:
                endings.append(EndingCondition(
                    ending_id=self._required_str(item, "ending_id"),
                    title=self._required_str(item, "title"),
                    description=str(item.get("description", "")).strip(),
                    conditions=self._string_list(item.get("conditions")),
                    ending_type=ending_type,
                ))
            except (ValueError, ScriptGenerationError):
                continue
        return endings

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
