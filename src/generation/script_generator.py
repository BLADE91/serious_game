"""基于 LLM 的剧本初稿生成器。

支持两种工作流：
1. 新流程（full_draft=True）：3 轮 ReAct Agent 生成 MD 剧本 → Qwen Flash 分步提取 JSON
2. 旧流程（full_draft=False）：单次 prompt 生成紧凑 JSON 草稿
"""

from dataclasses import asdict, replace
import json
import os
import threading
from pathlib import Path
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


def _load_chapter_four() -> str:
    """从 剧本写作参考.md 加载第四章（典型真实案例）。"""
    candidates = [
        Path(__file__).resolve().parent.parent.parent / "剧本写作参考.md",
        Path.cwd() / "剧本写作参考.md",
    ]
    for path in candidates:
        if path.exists():
            text = path.read_text(encoding="utf-8")
            # 提取第四章：从 "## 四、" 到下一个 "## " 或文件末尾
            marker = "## 四、"
            idx = text.find(marker)
            if idx == -1:
                return ""
            chapter4 = text[idx:]
            # 截断到下一个大标题
            next_section = chapter4.find("\n## ", len(marker))
            if next_section != -1:
                chapter4 = chapter4[:next_section]
            return chapter4.strip()
    return ""


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

    def _flash_client(self) -> QwenChatClient:
        """创建一个 Qwen Flash 客户端，用于 Phase 2 的轻量提取任务。"""
        config = QwenConfig.from_env()
        return QwenChatClient(QwenConfig(
            api_key=config.api_key,
            base_url=config.base_url,
            model="qwen-flash",
            timeout_seconds=config.timeout_seconds,
        ))

    def cancel_active_request(self) -> None:
        """取消正在进行的 HTTP 请求（委托给底层客户端）。"""
        if hasattr(self._client, 'cancel_active_request'):
            self._client.cancel_active_request()

    # ============================================================
    # 旧方法（保留向后兼容）
    # ============================================================

    def generate(
        self,
        query: str,
        contexts: list[SourceContext],
        feedback: str = "",
    ) -> ScriptDesign:
        if not query.strip():
            raise ValueError("query must not be empty")
        content = self._client.complete(
            self._build_messages(query, contexts, feedback), temperature=0.2
        )
        payload = self._parse_json_object(content)
        return self._build_script_design(payload)

    # ============================================================
    # 新流程：3 轮 MD + 5 步 JSON 提取
    # ============================================================

    def generate_md(
        self,
        query: str,
        contexts: list[SourceContext],
        feedback: str = "",
        progress_callback: GenerationProgressCallback | None = None,
        npc_count: int = 12,
        character_settings: str = "",
        story_background: str = "",
    ) -> str:
        """Phase 1 only：3 轮 ReAct Agent 生成完整 MD 剧本。

        返回三幕完整 MD 字符串，供前端审核编辑。
        progress: 1/3, 2/3, 3/3
        """
        chapter_four = _load_chapter_four()

        self._report_progress(progress_callback, 1, 3, "第一幕：开局破冰（检索+写作）", [])
        act1_md = self._generate_round_md(
            round_num=1,
            act_name="第一幕：开局破冰",
            act_goal="建立世界观，引入核心冲突，展示玩家角色和关键NPC，铺设决策困境",
            day_range="第1-20天",
            chapter_four=chapter_four,
            query=query,
            previous_md="",
            npc_count=npc_count,
            character_settings=character_settings,
            story_background=story_background,
        )

        self._report_progress(progress_callback, 2, 3, "第二幕：矛盾激化+博弈转折（检索+写作）", [])
        act2_md = self._generate_round_md(
            round_num=2,
            act_name="第二幕：矛盾激化与博弈转折",
            act_goal="激化冲突，展现NPC之间的利益博弈，增加玩家的道德困境，推动剧情向高潮发展",
            day_range="第21-55天",
            chapter_four=chapter_four,
            query=query,
            previous_md=act1_md,
            npc_count=npc_count,
            character_settings=character_settings,
            story_background=story_background,
        )

        self._report_progress(progress_callback, 3, 3, "第三幕：收束结局（检索+写作）", [])
        act3_md = self._generate_round_md(
            round_num=3,
            act_name="第三幕：收束结局",
            act_goal="收束故事线，展现不同分支结局，给玩家留下反思空间",
            day_range="第56-90天",
            chapter_four=chapter_four,
            query=query,
            previous_md=act1_md + "\n\n" + act2_md,
            npc_count=npc_count,
            character_settings=character_settings,
            story_background=story_background,
        )

        return f"# 第一幕：开局破冰\n\n{act1_md}\n\n# 第二幕：矛盾激化与博弈转折\n\n{act2_md}\n\n# 第三幕：收束结局\n\n{act3_md}"

    def _split_md_by_act(self, full_md: str) -> tuple[str, str, str]:
        """将完整 MD 拆分为三幕，返回 (act1_md, act2_md, act3_md)。

        处理可能的重复标题（generate_md 拼接的标题 + 模型输出自带标题）。
        """
        import re
        # 先去掉明显的重复标题行（连续两个相同的 "# 第X幕"）
        cleaned = re.sub(r'(# 第[一二三]幕[^\n]*)\n\n\1', r'\1', full_md)
        # 按 "# 第X幕" 标题拆分
        parts = re.split(r'\n(?=# 第[一二三]幕)', cleaned)
        act_map: dict[int, list[str]] = {1: [], 2: [], 3: []}
        for part in parts:
            part = part.strip()
            if not part:
                continue
            m = re.match(r'# 第([一二三])幕', part)
            if m:
                num = {"一": 1, "二": 2, "三": 3}[m.group(1)]
                # 去掉标题行本身，只保留内容
                body = re.sub(r'^# 第[一二三]幕[^\n]*\n?', '', part).strip()
                if body:
                    act_map[num].append(body)
        return (
            "\n\n".join(act_map[1]),
            "\n\n".join(act_map[2]),
            "\n\n".join(act_map[3]),
        )

    def _merge_npc_data(self, all_npc_data: list[dict]) -> dict:
        """合并多幕的 NPC 提取结果，按名称去重。"""
        seen_names: set[str] = set()
        merged_npcs: list[dict] = []
        merged_rels: list[dict] = []
        seen_rel_keys: set[tuple[str, str, str]] = set()

        for data in all_npc_data:
            for npc in data.get("npc_seed", []):
                name = npc.get("name", "").strip()
                if name and name not in seen_names:
                    seen_names.add(name)
                    merged_npcs.append(npc)
                elif name:
                    # 同名 NPC：补充缺失字段
                    for existing in merged_npcs:
                        if existing.get("name", "").strip() == name:
                            for k, v in npc.items():
                                if k not in existing or not existing[k]:
                                    existing[k] = v
                            break

            for rel in data.get("npc_relationships", []):
                key = (rel.get("from_npc_id", ""), rel.get("to_npc_id", ""), rel.get("relation_type", ""))
                if key not in seen_rel_keys:
                    seen_rel_keys.add(key)
                    merged_rels.append(rel)

        return {"npc_seed": merged_npcs, "npc_relationships": merged_rels}

    def _merge_decision_data(self, all_decision_data: list[dict]) -> dict:
        """合并多幕的决策点，保持时间顺序。"""
        merged: list[dict] = []
        seen_ids: set[str] = set()
        for data in all_decision_data:
            for dp in data.get("decision_points", []):
                did = dp.get("decision_id", "")
                if did and did not in seen_ids:
                    seen_ids.add(did)
                    merged.append(dp)
        # 按 day_window 排序
        def _day(d: dict) -> int:
            dw = d.get("day_window", "1")
            try:
                return int(dw.replace("第", "").split("-")[0].replace("天", ""))
            except (ValueError, IndexError):
                return 999
        merged.sort(key=_day)
        return {"decision_points": merged}

    def extract_json_from_md(
        self,
        full_md: str,
        query: str = "",
        npc_count: int = 12,
        progress_callback: GenerationProgressCallback | None = None,
    ) -> ScriptDesign:
        """Phase 2 only：从 MD 剧本提取结构化 JSON。

        按三幕分别提取 NPC、决策点，再合并；结局从第三幕提取。
        progress: 1/5 ~ 5/5
        """
        flash = self._flash_client()
        act1, act2, act3 = self._split_md_by_act(full_md)
        acts_md = [act1, act2, act3]
        act_names = ["第一幕", "第二幕", "第三幕"]

        # Step 1: 逐幕提取 NPC 并合并
        self._report_progress(progress_callback, 1, 5, "逐幕提取 NPC 与关系网", [])
        all_npc_data: list[dict] = []
        for i, (act_md, act_name) in enumerate(zip(acts_md, act_names)):
            if not act_md.strip():
                continue
            per_act_count = max(6, npc_count // 3)
            npc_data = self._extract_npc_from_md(flash, act_md, per_act_count, act_label=act_name)
            all_npc_data.append(npc_data)
        merged_npc = self._merge_npc_data(all_npc_data)

        # Step 2: 逐幕提取决策点并合并
        self._report_progress(progress_callback, 2, 5, "逐幕提取决策点与选项", [])
        all_decision_data: list[dict] = []
        for i, (act_md, act_name) in enumerate(zip(acts_md, act_names)):
            if not act_md.strip():
                continue
            dp_data = self._extract_decisions_from_md(flash, act_md, merged_npc, act_label=act_name)
            all_decision_data.append(dp_data)
        merged_decisions = self._merge_decision_data(all_decision_data)

        # Step 3: 结局（仅从第三幕提取）
        self._report_progress(progress_callback, 3, 5, "提取结局条件", [])
        ending_source = act3 if act3.strip() else full_md
        ending_data = self._extract_endings_from_md(flash, ending_source, merged_decisions)

        # Step 4: 三幕结构（从全文提取）
        self._report_progress(progress_callback, 4, 5, "提取三幕结构", [])
        skeleton_data = self._extract_skeleton_from_md(flash, full_md, query)

        # Step 5: 组装
        self._report_progress(progress_callback, 5, 5, "组装最终 JSON", [])
        return self._assemble_script(
            skeleton_data, merged_npc, merged_decisions, ending_data
        )

    def generate_full(
        self,
        query: str,
        contexts: list[SourceContext],
        feedback: str = "",
        progress_callback: GenerationProgressCallback | None = None,
        npc_count: int = 12,
        character_settings: str = "",
        story_background: str = "",
    ) -> tuple[ScriptDesign, str]:
        """一键完成 Phase 1 + Phase 2，返回 (ScriptDesign, full_md)。

        Phase 1: 3 轮 MD 生成（进度 1-3/8）
        Phase 2: 5 步 JSON 提取（进度 4-8/8）
        """

        def _md_progress(stage: int, _total: int, name: str, req_bytes: int) -> None:
            if progress_callback:
                progress_callback(stage, 8, name, req_bytes)

        def _extract_progress(stage: int, _total: int, name: str, req_bytes: int) -> None:
            if progress_callback:
                progress_callback(stage + 3, 8, name, req_bytes)

        full_md = self.generate_md(
            query, contexts, feedback, _md_progress,
            npc_count, character_settings, story_background,
        )
        script = self.extract_json_from_md(
            full_md, query, npc_count, _extract_progress,
        )
        return script, full_md

    # ============================================================
    # Phase 1: MD 生成
    # ============================================================

    def _generate_round_md(
        self,
        round_num: int,
        act_name: str,
        act_goal: str,
        day_range: str,
        chapter_four: str,
        query: str,
        previous_md: str,
        npc_count: int,
        character_settings: str,
        story_background: str,
    ) -> str:
        """调用 ReAct Agent 生成一幕 MD 叙事。"""
        system_prompt = self._build_round_system_prompt(
            round_num, act_name, act_goal, day_range,
            chapter_four, query, previous_md,
            npc_count, character_settings, story_background,
        )
        messages = [
            ChatMessage(role="system", content=system_prompt),
        ]
        result = self._client.complete(messages, temperature=0.4)
        # 清理可能的前后缀
        result = result.strip()
        if result.startswith("```"):
            lines = result.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            result = "\n".join(lines)
        return result.strip()

    def _build_round_system_prompt(
        self,
        round_num: int,
        act_name: str,
        act_goal: str,
        day_range: str,
        chapter_four: str,
        query: str,
        previous_md: str,
        npc_count: int,
        character_settings: str,
        story_background: str,
    ) -> str:
        """构建每轮 MD 生成的系统 prompt。"""
        parts = [
            f"你是一个严肃游戏的剧本作家，正在为游戏撰写第{round_num}幕的叙事剧本。",
            "",
            "## 本幕信息",
            f"- 名称：{act_name}",
            f"- 时间跨度：{day_range}",
            f"- 目标：{act_goal}",
            f"- NPC数量：约{npc_count}人",
        ]

        if story_background:
            parts.append(f"- 故事背景：{story_background}")
        if character_settings:
            parts.append(f"- 人物设定：{character_settings}")

        parts.append("")
        parts.append("## 原始需求")
        parts.append(query)

        if previous_md:
            parts.append("")
            parts.append("## 前面各幕的剧情（必须承接）")
            parts.append(previous_md[-5000:])  # 只取最后5000字作为上下文

        parts.append("")
        parts.append("## 检索要求")
        parts.append("在开始写作之前，你必须完成以下两步检索：")
        parts.append("")
        parts.append("### 1. 经典文献检索")
        parts.append("使用 os_search 在指定的知识库中搜索与本幕主题相关的学术/政策文献。")
        parts.append("对有价值的文献调用 fetch_os_document_fulltext 读取全文。")
        parts.append("提取可用的理论框架、政策背景和制度性知识。")
        parts.append("")
        parts.append("### 2. 真实事件检索")
        parts.append("使用 w_baidu_search 搜索以下典型真实案例，了解事件经过和关键细节。")

        if chapter_four:
            # 嵌入第四章全文（截断到合理长度）
            parts.append("")
            parts.append("以下是可参考的典型案例清单及搜索关键词：")
            parts.append("```")
            parts.append(chapter_four[:8000])
            parts.append("```")

        parts.append("")
        parts.append("请根据本幕主题，从上述清单中选择 3-5 个最相关的案例，")
        parts.append("使用 w_baidu_search 搜索其详细信息。")

        parts.append("")
        parts.append("## 写作要求")
        parts.append(f"综合以上两方面检索结果，以小说笔法写出{act_name}的完整叙事。")
        parts.append("")
        parts.append("必须包含以下要素：")
        parts.append("1. 场景描写：营造时代和地域氛围，让读者身临其境")
        parts.append("2. NPC 对话：每个主要 NPC 都应有符合其身份的台词和互动")
        parts.append("3. 决策节点：在关键情节处嵌入决策点，标注为 【决策点：标题】")
        parts.append("   - 每个决策点下列出 3-5 个选项，每个选项说明代价和可能后果")
        parts.append("   - 至少包含 5 个决策点")
        parts.append("4. 分支暗示：不同选择暗示不同的后续走向")
        parts.append("5. 数据埋点：在叙事中提及相关的 GameState 数值变化")
        parts.append("   （如：社会稳定指数、财政预算、签约户数、政治信用等）")
        parts.append("")
        parts.append("写作风格：")
        parts.append("- 使用小说的叙事笔法，不仅仅是知识堆砌")
        parts.append("- NPC 是有血有肉的人，有各自的动机、弱点和成长")
        parts.append("- 决策困境应是真实的两难，不存在明显正确或错误的选项")
        parts.append("- 适当引用检索到的真实事件细节增强真实感")
        parts.append("")
        parts.append("直接输出剧本正文，不要输出其他内容。")
        parts.append("不要使用 request_clarification 追问用户——信息不足时基于常识做出合理假设。")

        return "\n".join(parts)

    # ============================================================
    # Phase 2: MD → JSON 提取（Qwen Flash）
    # ============================================================

    def _extract_npc_from_md(
        self, flash: QwenChatClient, full_md: str, npc_count: int, act_label: str = ""
    ) -> dict:
        """Step 1: 从 MD 提取 NPC 列表和关系网。"""
        scope = f"仅提取{act_label}中新出现或重点描写的 NPC" if act_label else "提取所有 NPC 角色"
        prompt = f"""{scope}及其关系。

## 剧本
{full_md}

## 输出格式
请输出一个 JSON 对象：
{{
  "npc_seed": [
    {{
      "npc_id": "唯一ID，如 NPC_01",
      "name": "角色名",
      "npc_type": "cadre | external | villager",
      "group": "所属群体，如 镇干部、上访户、搬迁村民",
      "trust_to_player": 0-100,
      "attitude_score": 0-100,
      "anxiety_level": 0-100,
      "reference_point": 0,
      "granovetter_threshold": 0-100,
      "core_demand_satisfied": false,
      "signed": false,
      "known_info": ["该角色已知的关键信息"],
      "player_promises": []
    }}
  ],
  "npc_relationships": [
    {{
      "from_npc_id": "NPC_01",
      "to_npc_id": "NPC_02",
      "relation_type": "亲属 | 上下级 | 利益同盟 | 矛盾对立 | 信息渠道 | 情感纽带",
      "strength": 0-100,
      "description": "一句话描述关系"
    }}
  ]
}}

## 规则
- 提取 {npc_count} 个左右的 NPC，覆盖 cadre、external、villager 三类
- 每个 NPC 的数值基于剧本中的表现推断
- 关系至少 12 条，形成立体的社会网络
- 只输出 JSON，不要 Markdown"""
        content = flash.complete(
            [
                ChatMessage(role="system", content="你是剧本结构化提取器。只输出合法 JSON。"),
                ChatMessage(role="user", content=prompt),
            ],
            temperature=0.1,
        )
        return self._parse_json_object(content)

    def _extract_decisions_from_md(
        self, flash: QwenChatClient, full_md: str, npc_data: dict, act_label: str = ""
    ) -> dict:
        """Step 2: 从 MD 提取决策点和选项。"""
        scope = f"仅提取{act_label}中的决策点" if act_label else "提取所有决策点"
        npc_ids = [
            n.get("npc_id", "") for n in npc_data.get("npc_seed", [])
        ]
        prompt = f"""{scope}。

## 剧本
{full_md}

## 已有的 NPC ID 列表
{npc_ids}

## 输出格式
{{
  "decision_points": [
    {{
      "decision_id": "唯一ID，如 DP_01",
      "title": "决策点标题",
      "day_window": "时间窗口，如 第3-5天",
      "situation": "当前面临的具体困境描述",
      "options": [
        {{
          "option_id": "DP_01_A",
          "label": "简短选项标签",
          "description": "具体行动说明",
          "cost_action_points": 1,
          "budget_cost": 0,
          "payoffs": {{
            "global": {{"social_stability_index": 5, "political_credit": -3}},
            "npc_具体ID": {{"trust_to_player": 10}}
          }},
          "risks": ["风险说明"],
          "citation": ""
        }}
      ],
      "affected_npc_ids": ["NPC_01"],
      "trigger_condition": "",
      "is_critical": false,
      "citations": []
    }}
  ]
}}

## 规则
- 从剧本中找到所有【决策点：标题】标记的位置
- 每个决策点 3-5 个选项，选项之间有明显策略差异
- payoffs 需包含全局影响和具体 NPC 影响
- is_critical 标记 3-5 个对结局影响最大的决策
- affected_npc_ids 只用上面列表中的 ID
- 只输出 JSON，不要 Markdown"""
        content = flash.complete(
            [
                ChatMessage(role="system", content="你是剧本结构化提取器。只输出合法 JSON。"),
                ChatMessage(role="user", content=prompt),
            ],
            temperature=0.1,
        )
        return self._parse_json_object(content)

    def _extract_endings_from_md(
        self, flash: QwenChatClient, full_md: str, decision_data: dict
    ) -> dict:
        """Step 3: 从 MD 提取结局条件。"""
        critical_ids = [
            d.get("decision_id", "") for d in decision_data.get("decision_points", [])
            if d.get("is_critical")
        ]
        prompt = f"""从以下剧本中提取所有结局条件。

## 剧本
{full_md}

## 关键决策点
{critical_ids}

## 输出格式
{{
  "endings": [
    {{
      "ending_id": "唯一ID，如 END_01",
      "title": "结局标题",
      "description": "结局叙述",
      "conditions": [
        "signed_households >= 34",
        "social_stability_index >= 60"
      ],
      "ending_type": "good | neutral | bad"
    }}
  ]
}}

## 规则
- 至少 3 个结局（good + neutral + bad）
- 建议 4 个：1 good + 1 neutral + 2 bad
- conditions 使用量化指标（signed_households、social_stability_index、political_credit、budget_remaining 等）
- 好结局条件严格但可达
- 只输出 JSON，不要 Markdown"""
        content = flash.complete(
            [
                ChatMessage(role="system", content="你是剧本结构化提取器。只输出合法 JSON。"),
                ChatMessage(role="user", content=prompt),
            ],
            temperature=0.1,
        )
        return self._parse_json_object(content)

    def _extract_skeleton_from_md(
        self, flash: QwenChatClient, full_md: str, query: str
    ) -> dict:
        """Step 4: 从 MD 提取三幕结构和 GameState。"""
        prompt = f"""从以下剧本中提取总体设定、三幕结构和初始游戏状态。

## 剧本
{full_md}

## 原始需求
{query}

## 输出格式
{{
  "title": "剧本标题",
  "premise": "世界观设定简介",
  "player_role": "玩家角色",
  "core_conflict": "核心冲突",
  "initial_game_state": {{
    "day": 1,
    "action_points": 3,
    "budget_remaining": 8000,
    "budget_unit": "万元",
    "signed_households": 0,
    "total_households": 36,
    "social_stability_index": 70,
    "political_credit": 70,
    "cadre_execution_index": 60
  }},
  "acts": [
    {{
      "act_number": 1,
      "title": "开局破冰",
      "day_range": "第1-20天",
      "goal": "本幕目标",
      "description": "本幕概述"
    }},
    {{
      "act_number": 2,
      "title": "矛盾激化与博弈转折",
      "day_range": "第21-55天",
      "goal": "本幕目标",
      "description": "本幕概述"
    }},
    {{
      "act_number": 3,
      "title": "收束结局",
      "day_range": "第56-90天",
      "goal": "本幕目标",
      "description": "本幕概述"
    }}
  ]
}}

## 规则
- 从剧本中推断初始 GameState 的具体数值
- 三幕的 day_range 必须连续覆盖全部时间
- 只输出 JSON，不要 Markdown"""
        content = flash.complete(
            [
                ChatMessage(role="system", content="你是剧本结构化提取器。只输出合法 JSON。"),
                ChatMessage(role="user", content=prompt),
            ],
            temperature=0.1,
        )
        return self._parse_json_object(content)

    def _assemble_script(
        self,
        skeleton: dict,
        npc_data: dict,
        decision_data: dict,
        ending_data: dict,
    ) -> ScriptDesign:
        """Step 5: 组装所有提取结果为 ScriptDesign。"""
        # 先构建基本骨架
        script = self._build_script_design(skeleton)

        # 覆盖 NPC
        npc_seed = self._build_npc_states(
            npc_data.get("npc_seed", npc_data.get("npcs", []))
        )
        valid_ids = {n.npc_id for n in npc_seed}
        npc_relationships = self._build_npc_relationships(
            npc_data.get("npc_relationships", npc_data.get("relationships", [])),
            valid_ids,
        )

        # 覆盖决策点
        decision_points = self._build_decision_points(
            decision_data.get("decision_points", decision_data.get("decisions", [])),
            valid_ids,
        )

        # 覆盖结局
        endings = self._build_endings(
            ending_data.get("endings", ending_data.get("ending_conditions", []))
        )

        # 覆盖三幕（保留骨架中的 acts 或从 decision day_window 推断）
        acts = self._build_acts(skeleton.get("acts", []))
        if len(acts) != 3:
            # 回退到旧逻辑：根据决策点 day_window 分配
            acts = self._distribute_decisions_to_acts(acts, decision_points)

        return replace(
            script,
            npc_seed=npc_seed,
            npc_relationships=npc_relationships,
            decision_points=decision_points,
            endings=endings,
            acts=acts,
        )

    def _distribute_decisions_to_acts(
        self, acts: list[ActStructure], decision_points: list[DecisionPoint]
    ) -> list[ActStructure]:
        """根据决策点的 day_window 分配回三幕。"""
        decision_ids_per_act: dict[int, list[str]] = {1: [], 2: [], 3: []}
        for dp in decision_points:
            try:
                window = dp.day_window
                # 尝试从 "第X-Y天" 中提取起始天
                day_str = window.replace("第", "").split("-")[0].split("天")[0]
                start_day = int(day_str)
            except (ValueError, IndexError):
                start_day = 1
            if start_day <= 20:
                decision_ids_per_act[1].append(dp.decision_id)
            elif start_day <= 55:
                decision_ids_per_act[2].append(dp.decision_id)
            else:
                decision_ids_per_act[3].append(dp.decision_id)

        return [
            replace(act, decision_point_ids=decision_ids_per_act.get(act.act_number, []))
            for act in acts
        ]

    # ============================================================
    # 修订方法（保留）
    # ============================================================

    def revise(
        self,
        query: str,
        previous_script: dict[str, Any],
        contexts: list[SourceContext],
        feedback: str,
    ) -> ScriptDesign:
        if not query.strip():
            raise ValueError("query must not be empty")
        if not feedback.strip():
            raise ValueError("feedback must not be empty")
        if not previous_script:
            raise ValueError("previous_script must not be empty")
        content = self._client.complete(
            self._build_messages(query, contexts, feedback, previous_script=previous_script),
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

    def revise_structured(
        self,
        query: str,
        previous_result: dict[str, Any],
        contexts: list[SourceContext],
        feedback: str,
    ) -> ScriptDesign:
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

    # ============================================================
    # Prompt 构建（保留旧方法用于 revise / compact 模式）
    # ============================================================

    def _stage_payload(
        self, query: str, contexts: list[SourceContext],
        script: ScriptDesign, feedback: str,
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
                    f"你是严肃游戏的{role_name}。"
                    "只生成当前模块要求的字段，必须输出一个合法 JSON 对象。"
                ),
            ),
            ChatMessage(role="user", content=json.dumps(payload, ensure_ascii=False)),
        ]

    def _report_progress(
        self, callback: GenerationProgressCallback | None,
        stage: int, total: int, name: str, messages: list[ChatMessage],
    ) -> None:
        if callback is None:
            return
        size_method = getattr(self._client, "request_size_bytes", None)
        if callable(size_method):
            request_bytes = size_method(messages, temperature=0.2)
        else:
            request_bytes = sum(len(m.content.encode("utf-8")) for m in messages)
        callback(stage, total, name, request_bytes)

    def _context_payload(self, contexts: list[SourceContext]) -> list[dict[str, Any]]:
        return [
            {
                "reference_id": c.id, "title": c.title,
                "content": c.content, "metadata": c.metadata,
            }
            for c in contexts
        ]

    def _build_messages(
        self, query: str, contexts: list[SourceContext],
        feedback: str = "", previous_script: dict[str, Any] | None = None,
    ) -> list[ChatMessage]:
        user_payload = {
            "query": query,
            "source_contexts": self._context_payload(contexts),
            "human_feedback": feedback.strip(),
            "previous_script": previous_script,
            "output_contract": {
                "title": "string", "premise": "string", "player_role": "string",
                "core_conflict": "string",
                "initial_game_state": {
                    "day": 1, "action_points": 3, "budget_remaining": 8000,
                    "budget_unit": "万元", "signed_households": 0,
                    "total_households": 36, "social_stability_index": 70,
                    "political_credit": 70, "cadre_execution_index": 60,
                },
                "npc_seed": [{
                    "npc_id": "string", "name": "string",
                    "npc_type": "cadre | external | villager", "group": "string",
                    "trust_to_player": 0, "attitude_score": 0, "anxiety_level": 0,
                    "reference_point": 0, "granovetter_threshold": 0,
                    "core_demand_satisfied": False, "signed": False,
                    "known_info": ["string"], "player_promises": [],
                }],
                "action_rules": [], "event_outline": [], "night_rules": [],
                "payoff_notes": [], "citations": [],
            },
            "rules": [
                "生成严肃游戏剧本初稿。",
                "输出必须是一个合法 JSON 对象，不要 Markdown。",
                "NPC 类型只能使用 cadre、external、villager。",
                "所有数值字段必须是整数。",
                "语言必须是中文。",
            ],
        }
        return [
            ChatMessage(
                role="system",
                content="你是严肃游戏的剧本生成器。必须只输出一个合法 JSON 对象。",
            ),
            ChatMessage(role="user", content=json.dumps(user_payload, ensure_ascii=False)),
        ]

    def _build_revise_element_messages(
        self, element_type: str, element_id: str,
        current_element: dict[str, Any], context: dict[str, Any], feedback: str,
    ) -> list[ChatMessage]:
        npc_summary = context.get("npc_summary", [])
        script_title = context.get("script_title", "")
        script_premise = context.get("script_premise", "")
        element_labels = {
            "decision_point": "决策点", "npc": "NPC", "relationship": "关系",
            "option": "决策选项", "ending": "结局", "act": "幕",
        }
        label = element_labels.get(element_type, element_type)
        user_payload = {
            "element_type": element_type, "element_id": element_id, "label": label,
            "feedback": feedback.strip(), "current_element": current_element,
            "context": {
                "script_title": script_title, "script_premise": script_premise,
                "npc_summary": npc_summary,
            },
            "output_contract": current_element,
            "rules": [
                f"你是严肃游戏剧本的定向修订器。",
                f"只修改上面这个{label}，不要改动未提及的内容。",
                f"根据 feedback 中的要求进行定向修改。",
                f"保留元素的原始结构（相同的 key），只改需要改的值。",
                f"如果 feedback 要求增加内容，在现有内容基础上追加。",
                f"返回修改后的完整{label} JSON 对象，不要 Markdown，不要解释。",
            ],
        }
        return [
            ChatMessage(
                role="system",
                content=f"你是严肃游戏的{label}修订器。只修改指定元素，返回完整 JSON。",
            ),
            ChatMessage(role="user", content=json.dumps(user_payload, ensure_ascii=False)),
        ]

    def _build_revise_structured_messages(
        self, query: str, previous_result: dict[str, Any],
        contexts: list[SourceContext], feedback: str,
    ) -> list[ChatMessage]:
        previous_script = previous_result.get("script") if isinstance(previous_result, dict) else None
        user_payload = {
            "query": query, "human_feedback": feedback.strip(),
            "previous_script": previous_script,
            "source_contexts": self._context_payload(contexts),
            "output_contract": {
                "title": "string", "premise": "string", "player_role": "string",
                "core_conflict": "string",
                "initial_game_state": {
                    "day": 1, "action_points": 3, "budget_remaining": 8000,
                    "budget_unit": "万元", "signed_households": 0,
                    "total_households": 36, "social_stability_index": 70,
                    "political_credit": 70, "cadre_execution_index": 60,
                },
                "acts": [{"act_number": 1, "title": "string", "day_range": "string",
                         "goal": "string", "description": "string"}],
                "npc_seed": [{"npc_id": "string", "name": "string",
                             "npc_type": "cadre | external | villager", "group": "string"}],
                "npc_relationships": [{"from_npc_id": "string", "to_npc_id": "string",
                                      "relation_type": "string", "strength": 50}],
                "decision_points": [{"decision_id": "string", "title": "string",
                                    "day_window": "string", "situation": "string",
                                    "options": [], "is_critical": False}],
                "endings": [{"ending_id": "string", "title": "string",
                            "description": "string", "conditions": [], "ending_type": "string"}],
                "citations": [{"citation_id": "string", "source_context_id": "string"}],
            },
            "rules": [
                "你是严肃游戏的剧本修订器。",
                "在 previous_script 基础上根据 feedback 定向修订。",
                "保留不受反馈影响的所有原有内容。",
                "输出完整剧本 JSON，包含所有字段。",
                "语言必须是中文，不要 Markdown。",
            ],
        }
        return [
            ChatMessage(
                role="system",
                content="你是严肃游戏剧本修订器。在已有基础上定向修改，输出完整 JSON。",
            ),
            ChatMessage(role="user", content=json.dumps(user_payload, ensure_ascii=False)),
        ]

    # ============================================================
    # JSON 解析与构建（保留）
    # ============================================================

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
            npc_relationships=self._build_npc_relationships(
                payload.get("npc_relationships", []), set(),
            ),
            decision_points=self._build_decision_points(
                payload.get("decision_points", []), set(),
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
            npcs.append(NPCState(
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
            ))
        return npcs

    def _build_action_rules(self, value: Any) -> list[GameActionRule]:
        if not isinstance(value, list):
            return []
        rules = []
        for item in value:
            if not isinstance(item, dict):
                continue
            rules.append(GameActionRule(
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
            ))
        return rules

    def _build_event_outline(self, value: Any) -> list[ScriptEventOutline]:
        if not isinstance(value, list):
            return []
        events = []
        for item in value:
            if not isinstance(item, dict):
                continue
            events.append(ScriptEventOutline(
                event_id=self._required_str(item, "event_id"),
                name=self._required_str(item, "name"),
                day_window=self._required_str(item, "day_window"),
                trigger_condition=self._required_str(item, "trigger_condition"),
                description=self._required_str(item, "description"),
                payoff=item.get("payoff", {}) if isinstance(item.get("payoff"), dict) else {},
                citations=self._string_list(item.get("citations")),
            ))
        return events

    def _build_acts(self, value: Any) -> list[ActStructure]:
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
        self, value: Any, valid_npc_ids: set[str],
    ) -> list[NPCRelationship]:
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
                continue
            if from_id == to_id:
                continue
            relation_type = str(item.get("relation_type", "")).strip()
            if relation_type not in {"亲属", "上下级", "利益同盟", "矛盾对立", "信息渠道", "情感纽带"}:
                continue
            try:
                relationships.append(NPCRelationship(
                    from_npc_id=from_id, to_npc_id=to_id,
                    relation_type=relation_type,
                    strength=self._int_value(item.get("strength"), 50),
                    description=str(item.get("description", "")).strip(),
                ))
            except ValueError:
                continue
        return relationships

    def _build_decision_points(
        self, value: Any, valid_npc_ids: set[str],
    ) -> list[DecisionPoint]:
        if not isinstance(value, list):
            return []
        decisions = []
        for item in value:
            if not isinstance(item, dict):
                continue
            try:
                options = self._build_decision_options(item.get("options", []), valid_npc_ids)
                if len(options) < 2:
                    continue
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
        self, value: Any, valid_npc_ids: set[str],
    ) -> list[DecisionOption]:
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
            citations.append(ScriptCitation(
                citation_id=self._required_str(item, "citation_id"),
                source_context_id=source_context_id,
                title=self._required_str(item, "title"),
                note=self._required_str(item, "note"),
            ))
        return citations

    def _merge_citations(
        self, existing: list[ScriptCitation], additions: list[ScriptCitation],
    ) -> list[ScriptCitation]:
        merged = {c.citation_id: c for c in existing}
        for c in additions:
            merged[c.citation_id] = c
        return list(merged.values())

    def _module_value(self, payload: dict[str, Any], key: str, *aliases: str) -> Any:
        for candidate in (key, *aliases):
            if candidate in payload:
                return payload[candidate]
        for wrapper in ("data", "result", "script"):
            nested = payload.get(wrapper)
            if not isinstance(nested, dict):
                continue
            for candidate in (key, *aliases):
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
