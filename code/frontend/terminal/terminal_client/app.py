from __future__ import annotations

import shlex
import time
from getpass import getpass
from typing import Callable

from .api_client import ApiClient, ApiError
from .renderer import (
    render_actions,
    render_decision,
    render_feed,
    render_opportunities,
    render_state,
)


HELP_TEXT = """可用命令：
  register <username>         注册本地测试账号（交互输入密码）
  login <username>            登录已有账号（交互输入密码）
  whoami                     查看当前登录账号
  logout                     退出账号
  origins                     查看五种开局出身
  new <origin_id>             选择出身并新开一局
  continue                    继续当前账号最近一局
  load <session_id>           载入已有游戏
  scene                       刷新并显示新剧情、状态和待决策
  status                      显示当前玩家可见状态
  choose <A|option_id>        提交当前强制决策
  order <A B C D [E]>         提交当前排序题的完整顺序
  allocate <A B C D额度>      提交分配题四项整数额度
  actions                     查看自主行动目录
  opportunities               查看当前 NPC 互动机会
  do <action_id>              按服务端提示配置、报价并执行资源行动
  talk <opportunity_id> <话>  在服务端开放的机会中与 NPC 交谈
  leave                       结束当前进行中的 NPC 会谈
  knowledge                   查看已掌握的事实、线索与证据
  map                         查看当前地图入口和可用状态
  review                      查看本局只读复盘
  validate                    查看完整剧本包校验报告
  end                         结束当天并执行夜间推进
  overtime <1|2|3>            行动点用尽后申请本章加班额度
  help                        显示帮助
  quit                        退出客户端（不会删除存档）"""


class TerminalApp:
    def __init__(
        self,
        api: ApiClient,
        *,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
        sleep_fn: Callable[[float], None] = time.sleep,
        password_fn: Callable[[str], str] = getpass,
        menu_mode: bool = False,
    ) -> None:
        self.api = api
        self.input = input_fn
        self.output = output_fn
        self.sleep = sleep_fn
        self.password = password_fn
        self.menu_mode = menu_mode
        self.session_id: str | None = None
        self.state_version: int | None = None
        self.feed_cursor = 0
        self.state: dict = {}
        self.option_labels: dict[str, str] = {}
        self.commands: dict = {}
        self.authentication_required = False
        self.authenticated = False
        self.pending_operation_id: str | None = None

    def run(self) -> int:
        self.output("《浊流之下·清江搬迁记》文字测试客户端")
        try:
            readiness_call = getattr(self.api, "readiness", None)
            readiness = readiness_call() if readiness_call else {}
        except ApiError:
            readiness = {}
        self.authentication_required = bool(readiness.get("authentication_required"))
        self.authenticated = not self.authentication_required
        if self.authentication_required:
            try:
                self.api.me()
                self.authenticated = True
            except ApiError:
                self.authenticated = False
            if self.authenticated:
                self._post_auth_guide()
            else:
                self.output(
                    "尚未登录，请在账号入口选择登录或注册。" if self.menu_mode else
                    "尚未登录。下一步：输入 register <用户名> 注册，或 login <用户名> 登录。"
                )
        else:
            self.output(
                "请在游戏入口选择继续存档或开始新游戏。" if self.menu_mode else
                "输入 origins 查看出身；输入 new <origin_id> 开始游戏。"
            )
        if self.menu_mode:
            return self._run_menu()
        while True:
            try:
                raw = self.input(self._command_prompt()).strip()
            except (EOFError, KeyboardInterrupt):
                self.output("")
                return 0
            if not raw:
                continue
            try:
                if not self.handle(raw):
                    return 0
            except ApiError as exc:
                self.output(f"[后端错误 {exc.code}] {exc.message}")
                if exc.code in {"AUTHENTICATION_REQUIRED", "INVALID_CREDENTIALS"}:
                    self.authenticated = False
                    self.output("下一步：输入 login <用户名> 重试；没有账号则输入 register <用户名>。")
                elif self.session_id:
                    self._safe_refresh()
            except ValueError as exc:
                self.output(f"[输入错误] {exc}")

    def handle(self, raw: str) -> bool:
        try:
            parts = shlex.split(raw)
        except ValueError as exc:
            raise ValueError(f"无法解析命令：{exc}") from exc
        if not parts:
            return True
        command = parts[0].lower()
        args = parts[1:]

        if self.pending_operation_id and command not in {"quit", "exit", "help", "?"}:
            self._resume_pending_operation()
            self.output("上一轮会谈结果已经恢复，请确认回复后再决定下一步。")
            return True

        if command in {"quit", "exit"}:
            return False
        if command in {"help", "?"}:
            self.output(HELP_TEXT)
        elif command == "register":
            if len(args) != 1:
                raise ValueError("用法：register <username>")
            password = self._read_new_password()
            result = self.api.register(args[0], password)
            self.authenticated = True
            self.output(f"注册并登录成功：{result['account_id']}")
            self._post_auth_guide()
        elif command == "login":
            if len(args) != 1:
                raise ValueError("用法：login <username>")
            result = self.api.login(args[0], self.password("密码："))
            self.authenticated = True
            self.output(f"登录成功：{result['account_id']}")
            self._post_auth_guide()
        elif command == "whoami":
            result = self.api.me()
            self.authenticated = True
            self.output(
                f"当前账号：{result['account_id']}｜角色："
                f"{','.join(result.get('roles', []))}"
            )
        elif command == "logout":
            self.api.logout()
            self.authenticated = False
            self.session_id = None
            self.pending_operation_id = None
            self.state_version = None
            self.output("已退出登录。")
            self.output("下一步：输入 login <用户名>，或 register <用户名> 注册新账号。")
        elif command == "new":
            if len(args) != 1:
                raise ValueError("用法：new <origin_id>；先输入 origins 查看选项")
            self._new(args[0])
        elif command == "origins":
            self._origins()
        elif command == "continue":
            self._continue_latest()
        elif command == "load":
            if len(args) != 1:
                raise ValueError("用法：load <session_id>")
            self._load(args[0])
        elif command == "scene":
            self._refresh()
        elif command == "status":
            self._require_session()
            self._write_lines(render_state(self.state))
            self._guide_current(self.commands)
        elif command == "choose":
            if len(args) != 1:
                raise ValueError("用法：choose <A|option_id>")
            self._choose(args[0])
        elif command == "order":
            if len(args) not in {3, 4, 5}:
                raise ValueError("用法：order A B C D [E]")
            self._order(args)
        elif command == "allocate":
            if len(args) != 4:
                raise ValueError("用法：allocate <签约补偿> <民生安抚> <环评复检> <应急维稳>")
            self._allocate(args)
        elif command == "actions":
            self._actions()
        elif command == "opportunities":
            self._opportunities()
        elif command == "do":
            if len(args) != 1:
                raise ValueError("用法：do <action_id>")
            self._do(args[0])
        elif command == "talk":
            if len(args) < 2:
                raise ValueError("用法：talk <opportunity_id> <你要说的话>")
            self._command_talk(args[0], " ".join(args[1:]))
        elif command == "leave":
            active = self.state.get("active_conversation") or {}
            if not active.get("conversation_id"):
                raise ValueError("当前没有进行中的会谈")
            self._end_conversation(str(active["conversation_id"]))
        elif command == "knowledge":
            self._knowledge()
        elif command == "map":
            self._map()
        elif command == "review":
            self._review()
        elif command == "validate":
            self._validate_package()
        elif command == "end":
            self._end_day()
        elif command == "overtime":
            if len(args) != 1 or args[0] not in {"1", "2", "3"}:
                raise ValueError("用法：overtime <1|2|3>")
            self._request_overtime(int(args[0]))
        else:
            raise ValueError(f"未知命令：{parts[0]}；输入 help 查看命令")
        return True

    def _new(self, origin_id: str) -> None:
        state = self.api.new_session(origin_id=origin_id)
        self.session_id = str(state["session_id"])
        self.pending_operation_id = None
        self.state_version = int(state["state_version"])
        self.feed_cursor = 0
        self.state = state
        self.option_labels = {}
        self.commands = {}
        self.output(f"已创建游戏：{self.session_id}")
        self._refresh()

    def _origins(self) -> None:
        document = self.api.get_origins()
        self.output("开局出身：")
        for item in document.get("origins", []):
            self.output(
                f"  {item.get('origin_id')}｜{item.get('title')}｜"
                f"{item.get('description')}"
            )
        self.output("下一步：输入 new <origin_id>，例如 new technical。")

    def _continue_latest(self) -> None:
        state = self.api.get_latest_active()
        self._load(str(state["session_id"]))

    def _load(self, session_id: str) -> None:
        self.session_id = session_id
        self.pending_operation_id = None
        self.state_version = None
        self.feed_cursor = 0
        self.state = {}
        self.option_labels = {}
        self.commands = {}
        self._refresh()

    def _refresh(self) -> None:
        session_id = self._require_session()
        document = self.api.get_view(session_id, after=self.feed_cursor)
        self.state = document["state"]
        self.state_version = int(self.state["state_version"])
        feed = document.get("feed", {})
        self._write_lines(render_feed(feed.get("items", [])))
        self.feed_cursor = max(self.feed_cursor, int(feed.get("cursor", self.feed_cursor)))
        self._write_lines(render_state(self.state))
        decision_lines, self.option_labels = render_decision(
            self.state.get("pending_decision"), show_options=not self.menu_mode
        )
        self._write_lines(decision_lines)
        commands = document.get("commands", {})
        self.commands = commands
        if not commands.get("can_choose") and not any(
            commands.get(key) for key in ("can_act", "can_talk", "can_end_day")
        ):
            self.output("当前剧情节点暂无可提交命令。")
        self._guide_current(commands)

    def _choose(self, value: str) -> None:
        session_id = self._require_session()
        pending = self.state.get("pending_decision")
        if not pending:
            raise ValueError("当前没有待处理决策")
        option_id = self.option_labels.get(value.upper(), value)
        allowed = {
            str(item["option_id"])
            for item in pending.get("options", [])
            if item.get("available", True)
        }
        if option_id not in allowed:
            raise ValueError("选项不存在；请使用当前显示的字母或 option_id")
        client_action_id = self.api.new_key("decision")
        result = self.api.submit_decision(
            session_id,
            state_version=self._require_version(),
            decision_id=str(pending["decision_id"]),
            option_id=option_id,
            client_action_id=client_action_id,
        )
        result = self._await_operation(client_action_id, result)
        self.state_version = int(result["state_version"])
        self.output("决策已提交。")
        self._refresh()

    def _order(self, values: list[str]) -> None:
        session_id = self._require_session()
        pending = self.state.get("pending_decision")
        if not pending or pending.get("input_kind") != "sorting":
            raise ValueError("当前待决策不是排序题")
        ordered = [item.lower() for item in values]
        required = [str(item).lower() for item in pending.get("input_schema", {}).get("items", [])]
        if sorted(ordered) != sorted(required):
            raise ValueError("排序项必须完整且不能重复")
        client_action_id = self.api.new_key("decision")
        result = self.api.submit_decision(
            session_id,
            state_version=self._require_version(),
            decision_id=str(pending["decision_id"]),
            ordered_option_ids=ordered,
            client_action_id=client_action_id,
        )
        result = self._await_operation(client_action_id, result)
        self.state_version = int(result["state_version"])
        self.output("排序决策已提交。")
        self._refresh()

    def _allocate(self, values: list[str]) -> None:
        session_id = self._require_session()
        pending = self.state.get("pending_decision")
        if not pending or pending.get("input_kind") != "allocation":
            raise ValueError("当前待决策不是分配题")
        try:
            amounts = [int(item) for item in values]
        except ValueError as exc:
            raise ValueError("四项额度必须是整数") from exc
        schema = pending.get("input_schema", {})
        fields = list(schema.get("fields", []))
        if len(fields) != 4 or sum(amounts) != int(schema.get("total", 0)):
            raise ValueError(f"四项额度之和必须为 {schema.get('total')} {schema.get('unit', '')}")
        client_action_id = self.api.new_key("decision")
        result = self.api.submit_decision(
            session_id,
            state_version=self._require_version(),
            decision_id=str(pending["decision_id"]),
            parameters={"allocations": dict(zip(fields, amounts, strict=True))},
            client_action_id=client_action_id,
        )
        result = self._await_operation(client_action_id, result)
        self.state_version = int(result["state_version"])
        self.output("分配决策已提交。")
        self._refresh()

    def _actions(self) -> None:
        document = self.api.get_actions(self._require_session())
        self.state_version = int(document["state_version"])
        self._write_lines(render_actions(document))
        if any(item.get("available") for item in document.get("actions", [])):
            self.output("下一步：输入 do <action_id>，再按提示选择对象和参数；输入 scene 返回剧情。")
        else:
            self.output("当前没有可执行的自主行动。下一步：输入 scene 查看剧情，或 end 尝试日终。")

    def _opportunities(self) -> None:
        document = self.api.get_opportunities(self._require_session())
        self.state_version = int(document["state_version"])
        self._write_lines(render_opportunities(document))
        if document.get("opportunities"):
            self.output("下一步：输入 talk <opportunity_id> <你要说的话>；输入 scene 返回剧情。")
        else:
            self.output("当前没有可交谈对象。下一步：输入 scene 查看剧情，或 actions 查看行动。")

    def _do(self, action_id: str) -> None:
        session_id = self._require_session()
        document = self.api.get_actions(session_id)
        actions = {str(item["action_id"]): item for item in document.get("actions", [])}
        item = actions.get(action_id)
        if item is None:
            raise ValueError("行动 ID 不存在；先输入 actions 查看目录")
        if not item.get("available"):
            raise ValueError(str(item.get("unavailable_reason") or "当前行动不可用"))
        if item.get("execution_mode") == "conversation":
            raise ValueError("该行动必须从“交谈”入口选择具体人物")
        self._run_resource_action(item)

    def _run_resource_action(self, item: dict) -> None:
        target_schema = item.get("target_schema") or {}
        minimum = int(target_schema.get("min_items", 0))
        choices = list(item.get("target_choices", []))
        targets: list[str] = []
        remaining = list(choices)
        for position in range(minimum):
            if not remaining:
                raise ValueError("当前可选对象不足，尚不能执行这个行动")
            selected = self._select(
                f"选择行动对象（{position + 1}/{minimum}）",
                [str(value.get("label", "行动对象")) for value in remaining],
                back_label="取消本次行动",
            )
            if selected is None:
                return
            targets.append(str(remaining.pop(selected)["target_id"]))
        parameter_schema = item.get("parameter_schema") or {}
        properties = dict(parameter_schema.get("properties", {}))
        parameters: dict = {}
        for key in parameter_schema.get("required", []):
            spec = properties.get(key, {})
            enum_values = list(spec.get("enum", []))
            if not enum_values:
                raise ValueError(f"参数 {key} 没有可供选择的登记值")
            selected = self._select(
                f"选择{key}",
                [str(value) for value in enum_values],
                back_label="取消本次行动",
            )
            if selected is None:
                return
            parameters[str(key)] = enum_values[selected]
        session_id = self._require_session()
        quote = self.api.quote_action(
            session_id,
            state_version=self._require_version(),
            action_id=str(item["action_id"]),
            target_ids=targets,
            parameters=parameters,
        )
        self.output(
            f"执行前报价：行动点 {quote['cost_action_points']}｜"
            f"直接财政支出 {quote['direct_budget_cost']} {quote['budget_unit']}"
        )
        if quote.get("resource_ids"):
            self.output("承办资源：" + "、".join(quote["resource_ids"]))
        if quote.get("narrative_preview"):
            self.output("程序说明：" + str(quote["narrative_preview"]))
        if self._select(
            "确认执行该行动？", ["按以上对象、参数与报价执行"], back_label="取消"
        ) is None:
            return
        client_action_id = self.api.new_key("resource")
        result = self.api.execute_resource_action(
            session_id,
            state_version=int(quote["state_version"]),
            action_id=str(item["action_id"]),
            target_ids=targets,
            parameters=parameters,
            quote_id=str(quote["quote_id"]),
            client_action_id=client_action_id,
        )
        result = self._await_operation(client_action_id, result)
        self.state_version = int(result["state_version"])
        self.output(str(result.get("narrative", "行动已完成。")))
        self._refresh()

    def _request_overtime(self, points: int) -> None:
        client_action_id = self.api.new_key("overtime")
        result = self.api.request_overtime(
            self._require_session(),
            state_version=self._require_version(),
            points=points,
            client_action_id=client_action_id,
        )
        result = self._await_operation(client_action_id, result)
        self.state_version = int(result["state_version"])
        self.output(str(result.get("narrative", "加班额度已发放。")))
        self._refresh()

    def _talk(
        self, opportunity_id: str, conversation_id: str, player_text: str
    ) -> dict:
        session_id = self._require_session()
        document = self.api.get_opportunities(session_id)
        opportunities = {
            str(item["opportunity_id"]): item
            for item in document.get("opportunities", [])
        }
        opportunity = opportunities.get(opportunity_id)
        if opportunity is None:
            reason = document.get("blocked_reason")
            raise ValueError(str(reason or "当前没有这个互动机会"))
        client_action_id = self.api.new_key("talk")
        self.pending_operation_id = client_action_id
        npc_name = str(opportunity.get("npc_name") or opportunity.get("npc_id") or "角色")
        self.output(f"{npc_name}正在思考，请稍候……")
        try:
            try:
                result = self.api.submit_free_text(
                    session_id,
                    state_version=int(document["state_version"]),
                    opportunity_id=opportunity_id,
                    target_npc_id=str(opportunity["npc_id"]),
                    conversation_id=conversation_id,
                    player_text=player_text,
                    client_action_id=client_action_id,
                )
            except ApiError as exc:
                if exc.code != "CLIENT_TIMEOUT":
                    raise
                self.output(
                    "模型响应时间较长，但本回合已经取得唯一操作编号。"
                    "正在查询原操作结果，请勿重复输入。"
                )
                result = {"status": "processing", "poll_after_ms": 750}
            result = self._await_operation(client_action_id, result)
        except ApiError as exc:
            if exc.code not in {
                "CLIENT_POLL_TIMEOUT", "CLIENT_TIMEOUT", "CLIENT_CONNECTION_ERROR"
            }:
                self.pending_operation_id = None
            raise
        self.pending_operation_id = None
        self.state_version = int(result["state_version"])
        self._refresh()
        return result

    def _command_talk(self, opportunity_id: str, player_text: str) -> None:
        document = self.api.get_opportunities(self._require_session())
        opportunity = next(
            (
                item for item in document.get("opportunities", [])
                if str(item["opportunity_id"]) == opportunity_id
            ),
            None,
        )
        if opportunity is None:
            raise ValueError("当前没有这个互动机会")
        if opportunity.get("conversation_active"):
            conversation_id = str(opportunity["conversation_id"])
        else:
            started = self._start_conversation(opportunity)
            conversation_id = str(started["conversation"]["conversation_id"])
        self._talk(opportunity_id, conversation_id, player_text)

    def _start_conversation(self, opportunity: dict) -> dict:
        client_action_id = self.api.new_key("conversation-start")
        result = self.api.start_conversation(
            self._require_session(),
            state_version=int(self.state_version or 0),
            opportunity_id=str(opportunity["opportunity_id"]),
            target_npc_id=str(opportunity["npc_id"]),
            client_action_id=client_action_id,
        )
        result = self._await_operation(client_action_id, result)
        self.state_version = int(result["state_version"])
        self._refresh()
        return result

    def _end_conversation(self, conversation_id: str) -> dict:
        client_action_id = self.api.new_key("conversation-end")
        result = self.api.end_conversation(
            self._require_session(),
            state_version=int(self.state_version or 0),
            conversation_id=conversation_id,
            client_action_id=client_action_id,
        )
        result = self._await_operation(client_action_id, result)
        self.state_version = int(result["state_version"])
        self._refresh()
        return result

    def _knowledge(self) -> None:
        document = self.api.get_knowledge(self._require_session())
        self.state_version = int(document["state_version"])
        values = []
        for key, title in (("facts", "事实"), ("clues", "线索"), ("evidence", "证据")):
            items = document.get(key, [])
            values.append(f"{title}：{len(items)} 项")
            for item in items:
                values.extend((
                    f"  【{item.get('title') or item.get('fact_id') or item}】",
                    f"    内容：{item.get('text') or '暂无正文。'}",
                    f"    来源：{item.get('source_label') or '剧情中已确认'}",
                    f"    可用于：{item.get('use_hint') or '可在后续会谈、调查和决策中引用。'}",
                ))
        self._write_lines(values)
        self.output(
            "查看完毕，返回游戏菜单。" if self.menu_mode else
            "下一步：输入 scene 返回当前剧情；输入 map、actions 或 opportunities 查看其他入口。"
        )

    def _write_related_materials(self, materials: list[dict]) -> None:
        if not materials:
            self.output("当前没有与此人直接关联的已知材料。")
            return
        self.output("本次会谈可参考的材料：")
        for item in materials:
            self._write_lines((
                f"  【{item.get('title') or item.get('material_id') or '材料'}】",
                f"    内容：{item.get('text') or '暂无正文。'}",
                f"    来源：{item.get('source_label') or '县长案头'}",
                f"    可用于：{item.get('use_hint') or '可在会谈中引用。'}",
            ))

    def _menu_desk(self) -> None:
        document = self.api.get_desk(self._require_session())
        self.state_version = int(document["state_version"])
        options = [
            "任务与硬约束", "五份背景卷宗", "补偿政策与财政盘子",
            "当前可调资源", "全部行动工具", "36户公开底表",
            "事实、线索与证据",
        ]
        while True:
            selected = self._select("县长案头", options, back_label="返回游戏菜单")
            if selected is None:
                return
            if selected == 0:
                mission = document["mission"]
                self.output(f"【{mission['title']}】\n{mission['summary']}")
                for item in mission.get("hard_constraints", []):
                    self.output(f"  - {item['label']}：{item['value']}｜{item['detail']}")
            elif selected == 1:
                for item in document.get("dossiers", []):
                    self.output(f"【{item['title']}】\n  {item['summary']}")
                    for point in item.get("known_points", []):
                        self.output(f"  - {point}")
            elif selected == 2:
                policy = document["compensation_policy"]
                budget = policy["current_budget"]
                self.output(f"【{policy['title']}】{policy['status']}")
                self.output(
                    f"当前财政盘：基础授权 {budget['base_authorized']}、"
                    f"有来源调整 {budget['approved_adjustments']}、"
                    f"已承诺 {budget['committed']}、已支出 {budget['paid']}、"
                    f"当前可安排 {budget['remaining']} {budget['unit']}"
                )
                if budget.get("precoord_suspense"):
                    self.output(
                        f"疑点挂账：前期协调费 {budget['precoord_suspense']} {budget['unit']}。"
                    )
                self.output("已确定资金口径：")
                for item in policy.get("funding", []):
                    self.output(f"  - {item['label']}：{item['value']}")
                self.output("公开执行原则：")
                for item in policy.get("principles", []):
                    self.output(f"  - {item}")
                self.output(f"数字边界：{policy['numeric_guardrail']}")
            elif selected == 3:
                for item in document.get("authorities", []):
                    self.output(f"【{item['name']}】{item['description']}\n  边界：{item['limitation']}")
            elif selected == 4:
                categories = {item["name"]: item["description"] for item in document.get("tool_categories", [])}
                current_category = None
                for item in document.get("tools", []):
                    if item["category"] != current_category:
                        current_category = item["category"]
                        self.output(f"\n【{current_category}】{categories.get(current_category, '')}")
                    state = "现在可用" if item.get("available") else f"当前不可用：{item.get('unavailable_reason')}"
                    self.output(
                        f"  - {item['name']}｜消耗 {item['cost_action_points']} 行动点｜{state}\n"
                        f"    作用：{item['description']}\n"
                        f"    条件：{item['availability_note']}"
                    )
            elif selected == 5:
                households = list(document.get("household_registry", []))
                self.output(
                    f"【36户公开底表】共 {len(households)} 户。"
                    "以下是已登记基础量；地类、附属物数量等未给出的明细仍待核验。"
                )
                for item in households:
                    other_land = ""
                    if item.get("other_land_mu"):
                        other_land = f"，其他土地 {item['other_land_mu']:g}亩"
                    self.output(
                        f"  - {item['household_id']}｜户籍 {item['registered_population']} 人｜"
                        f"住宅 {item['legal_residential_area_m2']:g}㎡｜"
                        f"宅基地认定 {item['homestead_recognized_m2']:g}㎡｜"
                        f"承包地 {item['contracted_land_mu']:g}亩{other_land}｜"
                        f"权属：{item['ownership_status']}"
                    )
            else:
                self._knowledge()

    def _map(self) -> None:
        document = self.api.get_map(self._require_session())
        locations = list(document.get("locations", []))
        if self.menu_mode:
            selected_location = self._select(
                f"地图入口（D{document.get('story_day', '?')}）",
                [
                    f"{item.get('name')}｜{item.get('description')}｜{item.get('visual_state')}"
                    for item in locations
                ],
                back_label="返回游戏菜单",
            )
            if selected_location is None:
                return
            location = locations[selected_location]
            cards = list(location.get("entry_cards", []))
            if not cards:
                self.output("这个地点今天没有可进入的人物、事件或资源行动。")
                return
            selected_card = self._select(
                str(location.get("name", "地点入口")),
                [
                    f"{card.get('title')}｜{card.get('description')}｜"
                    + (
                        f"消耗 {card.get('cost_action_points')} 行动点"
                        if card.get("available") else
                        f"不可用：{card.get('unavailable_reason') or '条件不足'}"
                    )
                    for card in cards
                ],
                back_label="返回地图",
            )
            if selected_card is None:
                return
            card = cards[selected_card]
            if not card.get("available"):
                self.output(str(card.get("unavailable_reason") or "当前入口不可用。"))
                return
            submit = card.get("submit") or {}
            if card.get("entry_type") == "conversation":
                opportunities = self.api.get_opportunities(
                    self._require_session()
                ).get("opportunities", [])
                opportunity = next(
                    (
                        item for item in opportunities
                        if item.get("opportunity_id") == submit.get("opportunity_id")
                    ),
                    None,
                )
                if opportunity is None:
                    self.output("该人物入口刚刚发生变化，请刷新地图后重试。")
                    return
                self._run_conversation(opportunity)
                return
            actions = self.api.get_actions(self._require_session())
            self.state_version = int(actions["state_version"])
            action = next(
                (
                    item for item in actions.get("actions", [])
                    if item.get("action_id") == submit.get("action_id")
                ),
                None,
            )
            if action is None or not action.get("available"):
                self.output("该行动入口刚刚发生变化，请刷新地图后重试。")
                return
            self._run_resource_action(action)
            return
        self.output(f"地图入口（D{document.get('story_day', '?')}）：")
        for item in locations:
            cards = item.get("entry_cards", [])
            self.output(
                f"  {item.get('name')}｜{item.get('visual_state')}｜"
                f"当前入口 {len(cards)} 项"
            )
        self.output(
            "查看完毕，返回游戏菜单。" if self.menu_mode else
            "下一步：输入 opportunities 查看可交谈对象，或 scene 返回当前剧情。"
        )

    def _review(self) -> None:
        document = self.api.get_review(self._require_session())
        self.output(
            f"复盘：决策 {len(document.get('decision_timeline', []))}｜"
            f"行动 {len(document.get('action_timeline', []))}｜"
            f"会谈记录 {len(document.get('conversation_timeline', []))}｜"
            f"夜间 {len(document.get('night_timeline', []))}"
        )
        for item in document.get("decision_timeline", []):
            self.output(
                f"  D{item.get('story_day')} 决策｜{item.get('title')}｜"
                f"选择：{item.get('choice')}"
            )
        for item in document.get("action_timeline", []):
            self.output(
                f"  D{item.get('story_day')} 行动｜{item.get('name')}｜"
                f"行动点 {item.get('cost_action_points', 0)}｜"
                f"财政 {item.get('budget_cost', 0)}"
            )
            if item.get("public_result"):
                self.output(f"    公开结果：{item['public_result']}")
        for item in document.get("known_facts", []):
            self.output(
                f"  材料｜{item.get('title')}｜{item.get('source_label')}\n"
                f"    {item.get('text')}"
            )
        ending = document.get("ending")
        if ending:
            self.output(
                f"结局：{ending.get('main_ending_name')} · "
                f"{ending.get('sub_ending_title')}"
            )
        self.output(
            "查看完毕，返回游戏菜单。" if self.menu_mode else
            "下一步：输入 scene 返回当前剧情；若本局已结束，可输入 new <origin_id> 新开一局。"
        )

    def _validate_package(self) -> None:
        document = self.api.get_package_validation()
        counts = document.get("counts", {})
        self.output(
            f"剧本包校验：{'通过' if document.get('valid') else '失败'}｜"
            f"D{counts.get('story_days')}｜决策 {counts.get('decision_catalog')}｜"
            f"事件 {counts.get('event_catalog')}｜"
            f"运行实例 {counts.get('runtime_decisions')}｜"
            f"结局 {counts.get('main_endings')}/{counts.get('sub_endings')}"
        )
        self.output(
            "查看完毕，返回游戏菜单。" if self.menu_mode else
            "下一步：输入 origins 开始新局，或 scene 返回当前游戏。"
        )

    def _end_day(self, *, active_rest: bool = False) -> None:
        client_action_id = self.api.new_key("end")
        result = self.api.end_day(
            self._require_session(),
            state_version=self._require_version(),
            active_rest=active_rest,
            client_action_id=client_action_id,
        )
        result = self._await_operation(client_action_id, result)
        self.state_version = int(result["state_version"])
        self.output(
            "你主动收工，剩余行动点已经作废；夜间结算完成。"
            if active_rest else "夜间模拟完成。"
        )
        self._refresh()

    def _run_menu(self) -> int:
        while True:
            try:
                if self.authentication_required and not self.authenticated:
                    if not self._menu_authentication():
                        return 0
                    continue
                if not self.session_id:
                    if not self._menu_session_entry():
                        return 0
                    continue
                if not self.state:
                    self._refresh()
                if not self._menu_game_step():
                    return 0
            except ApiError as exc:
                self.output(f"[后端错误 {exc.code}] {exc.message}")
                if exc.code in {"AUTHENTICATION_REQUIRED", "INVALID_CREDENTIALS"}:
                    self.authenticated = False
                    self.session_id = None
                    self.output("登录状态无效，请在下一页重新登录或注册。")
                else:
                    self._safe_refresh()
                    if exc.code == "CLIENT_POLL_TIMEOUT":
                        self.output(
                            "原回合仍在服务端处理中；客户端会继续查询同一操作，"
                            "不会重复提交你的发言。"
                        )
                    else:
                        self.output("已刷新服务端权威状态，请按下一页菜单继续。")
            except ValueError as exc:
                self.output(f"[输入错误] {exc}。请按下方菜单重新选择。")

    def _menu_authentication(self) -> bool:
        selected = self._select(
            "账号入口",
            ["登录已有账号", "注册新账号"],
            back_label="退出程序",
        )
        if selected is None:
            return False
        username = self.input("请输入用户名：").strip()
        if not username:
            self.output("用户名不能为空，返回账号入口。")
            return True
        if selected == 0:
            result = self.api.login(username, self.password("请输入密码："))
            self.authenticated = True
            self.output(f"登录成功：{result['account_id']}")
        else:
            result = self.api.register(username, self._read_new_password())
            self.authenticated = True
            self.output(f"注册并登录成功：{result['account_id']}")
        return True

    def _menu_session_entry(self) -> bool:
        latest = None
        try:
            latest = self.api.get_latest_active()
        except ApiError as exc:
            if exc.status != 404 and exc.code != "NOT_FOUND":
                raise
        options: list[tuple[str, str]] = []
        if latest:
            story = latest.get("story", {})
            options.append((
                "continue",
                f"继续活动存档（D{story.get('day', '?')}，{latest.get('session_id')}）",
            ))
        options.extend((
            ("new", "开始新游戏并选择出身"),
            ("logout", "退出当前账号"),
        ))
        selected = self._select(
            "游戏入口", [label for _, label in options], back_label="退出程序"
        )
        if selected is None:
            return False
        action = options[selected][0]
        if action == "continue":
            self._load(str(latest["session_id"]))
        elif action == "new":
            self._menu_choose_origin()
        else:
            self._menu_logout()
        return True

    def _menu_choose_origin(self) -> None:
        document = self.api.get_origins()
        origins = list(document.get("origins", []))
        selected = self._select(
            "请选择开局出身",
            [f"{item.get('title')}｜{item.get('description')}" for item in origins],
            back_label="返回游戏入口",
        )
        if selected is not None:
            self._new(str(origins[selected]["origin_id"]))

    def _menu_game_step(self) -> bool:
        if self.pending_operation_id:
            self.output("正在确认上一轮会谈结果，请勿再次提交相同发言……")
            self._resume_pending_operation()

        pending = self.state.get("pending_decision")
        if pending:
            self._menu_decision(pending)
            return True
        if self.state.get("status") != "active":
            options = [
                ("review", "查看本局复盘"),
                ("new", "开始新游戏"),
                ("logout", "退出当前账号"),
            ]
            selected = self._select(
                "本局已经结束", [label for _, label in options], back_label="退出程序"
            )
            if selected is None:
                return False
            action = options[selected][0]
            if action == "review":
                self._review()
            elif action == "new":
                self.session_id = None
                self.state = {}
                self._menu_choose_origin()
            else:
                self._menu_logout()
            return True

        options: list[tuple[str, str]] = []
        if self.commands.get("can_talk"):
            active = self.state.get("active_conversation")
            options.append((
                "talk",
                "继续当前 NPC 会谈（调用真实 LLM）"
                if active else "与当前可互动的 NPC 交谈（调用真实 LLM）",
            ))
        if self.commands.get("can_act"):
            options.append(("action", "执行自主行动"))
        if self.commands.get("can_end_day"):
            options.append(("end", "结束当天并执行夜间推进"))
        points = self.state.get("ledger", {}).get("action_points", {})
        if points.get("overtime_available"):
            options.append(("overtime", "申请加班 1–3 点（本章最多三次）"))
        options.extend((
            ("desk", "打开县长案头（任务、政策、资源和材料）"),
            ("knowledge", "查看已掌握的事实、线索和证据"),
            ("map", "查看地图与当前入口"),
            ("review", "查看本局复盘"),
            ("status", "查看当前状态"),
            ("validate", "检查剧本包完整性"),
            ("refresh", "刷新当前剧情"),
            ("logout", "保存并退出当前账号"),
        ))
        selected = self._select(
            "请选择下一步", [label for _, label in options], back_label="退出程序"
        )
        if selected is None:
            return False
        action = options[selected][0]
        if action == "talk":
            self._menu_talk()
        elif action == "action":
            self._menu_action()
        elif action == "end":
            rest_choice = self._select(
                "选择今天的收束方式",
                [
                    "正常结束当天并进入夜间",
                    "主动收工休息（剩余行动点作废，日终额外恢复疲惫）",
                ],
                back_label="取消",
            )
            if rest_choice is not None:
                self._end_day(active_rest=rest_choice == 1)
        elif action == "overtime":
            selected_points = self._select(
                "选择加班额度",
                ["加班 1 点", "加班 2 点", "加班 3 点"],
                back_label="取消",
            )
            if selected_points is not None:
                self._request_overtime(selected_points + 1)
        elif action == "desk":
            self._menu_desk()
        elif action == "knowledge":
            self._knowledge()
        elif action == "map":
            self._map()
        elif action == "review":
            self._review()
        elif action == "status":
            self._write_lines(render_state(self.state))
        elif action == "validate":
            self._validate_package()
        elif action == "refresh":
            self._refresh()
        else:
            self._menu_logout()
        return True

    def _menu_decision(self, pending: dict) -> None:
        kind = pending.get("input_kind", "choice")
        if kind == "sorting":
            remaining = [
                item for item in pending.get("options", []) if item.get("available", True)
            ]
            ordered: list[str] = []
            for position in range(1, len(remaining) + 1):
                selected = self._select(
                    f"请选择第 {position} 优先项",
                    [str(item.get("text", "")) for item in remaining],
                    back_label="取消本次排序",
                )
                if selected is None:
                    return
                item = remaining.pop(selected)
                ordered.append(str(item["option_id"]))
            self._order(ordered)
            return
        if kind == "allocation":
            if self._select("是否开始填写分配额度？", ["开始填写"], back_label="稍后再决定") is None:
                return
            schema = pending.get("input_schema") or {}
            fields = list(schema.get("fields", []))
            labels = schema.get("labels", {})
            total = int(schema.get("total", 0))
            unit = str(schema.get("unit", ""))
            while True:
                values: list[str] = []
                valid = True
                for field in fields:
                    raw = self.input(f"{labels.get(field, field)}（{unit}，请输入非负整数）：").strip()
                    try:
                        value = int(raw)
                        if value < 0:
                            raise ValueError
                    except ValueError:
                        self.output("额度必须是非负整数，本轮分配作废，请重新填写四项。")
                        valid = False
                        break
                    values.append(str(value))
                if not valid:
                    continue
                if sum(int(value) for value in values) != total:
                    self.output(f"四项合计必须等于 {total} {unit}，请重新填写。")
                    continue
                self._allocate(values)
                return
        available = [
            item for item in pending.get("options", []) if item.get("available", True)
        ]
        selected = self._select(
            str(pending.get("title", "请选择")),
            [str(item.get("text", "")) for item in available],
            back_label="暂不选择",
        )
        if selected is not None:
            self._choose(str(available[selected]["option_id"]))

    def _menu_talk(self) -> None:
        document = self.api.get_opportunities(self._require_session())
        opportunities = list(document.get("opportunities", []))
        if not opportunities:
            self.output(str(document.get("blocked_reason") or "当前没有可交谈对象。"))
            return
        selected = self._select(
            "请选择交谈对象",
            [
                (
                    f"{item.get('npc_name') or item.get('npc_id')}｜"
                    f"{item.get('npc_title') or '剧情人物'}｜"
                    + (
                        "会谈进行中，本次继续不再扣行动点\n"
                        if item.get("conversation_active")
                        else f"消耗 {item.get('cost_action_points')} 行动点（进入后不限轮次）\n"
                    )
                    +
                    f"     人物简介：{item.get('npc_introduction') or '暂无公开介绍。'}\n"
                    f"     本次接触：{item.get('conversation_context') or item.get('action_name') or '自由交谈'}\n"
                    f"     前情提要：{item.get('opening_narrative') or '你在当前剧情中获得了与此人交谈的机会。'}\n"
                    f"     会谈方向：{item.get('conversation_goal') or '了解对方当前的真实想法。'}\n"
                    f"     可参考材料：{'; '.join(material.get('title', '材料') for material in item.get('related_materials', [])) or '暂无'}"
                )
                for item in opportunities
            ],
            back_label="返回上一级",
        )
        if selected is None:
            return
        self._run_conversation(opportunities[selected])

    def _run_conversation(self, opportunity: dict) -> None:
        if opportunity.get("conversation_active"):
            conversation_id = str(opportunity["conversation_id"])
        else:
            confirmed = self._select(
                "确认进入会谈",
                ["进入会谈（只在此时扣除行动点，后续交谈不限轮次）"],
                back_label="暂不进入",
            )
            if confirmed is None:
                return
            started = self._start_conversation(opportunity)
            conversation_id = str(started["conversation"]["conversation_id"])

        npc_name = str(opportunity.get("npc_name") or opportunity.get("npc_id"))
        while True:
            selected_action = self._select(
                f"与{npc_name}的会谈",
                ["继续交谈", "查看本次会谈相关材料", "结束本次会谈"],
                back_label="暂时离开终端（会谈仍保持）",
            )
            if selected_action is None:
                return
            if selected_action == 1:
                self._write_related_materials(list(opportunity.get("related_materials", [])))
                continue
            if selected_action == 2:
                self._end_conversation(conversation_id)
                return
            text = self.input("你想说什么（直接回车返回会谈菜单）：").strip()
            if not text:
                self.output("没有提交内容，会谈仍在继续。")
                continue
            result = self._talk(
                str(opportunity["opportunity_id"]), conversation_id, text
            )
            if result.get("conversation", {}).get("status") == "ended":
                self.output(f"{npc_name}已经结束了这次会谈。")
                return

    def _menu_action(self) -> None:
        document = self.api.get_actions(self._require_session())
        self.state_version = int(document["state_version"])
        entries = [
            action for action in document.get("actions", [])
            if action.get("available")
            and action.get("execution_mode") == "resource_action"
        ]
        if not entries:
            self.output("当前没有可直接执行的资源行动；人物类行动请从“交谈”进入。")
            return
        selected = self._select(
            "请选择自主行动",
            [
                f"{action.get('name')}｜消耗 {action.get('cost_action_points')} 行动点｜"
                f"直接财政支出 {action.get('direct_budget_cost', 0)}"
                for action in entries
            ],
            back_label="返回上一级",
        )
        if selected is not None:
            self._run_resource_action(entries[selected])

    def _menu_logout(self) -> None:
        self.api.logout()
        self.authenticated = False
        self.session_id = None
        self.pending_operation_id = None
        self.state_version = None
        self.state = {}
        self.commands = {}
        self.output("存档已保留，当前账号已退出。")

    def _select(
        self, title: str, options: list[str], *, back_label: str = "返回上一级"
    ) -> int | None:
        self.output("")
        self.output(f"【{title}】")
        for index, label in enumerate(options, start=1):
            label_lines = label.splitlines() or [""]
            self.output(f"  {index}. {label_lines[0]}")
            for continuation in label_lines[1:]:
                self.output(f"     {continuation.strip()}")
        self.output(f"  0. {back_label}")
        while True:
            raw = self.input("请选择序号：").strip()
            try:
                value = int(raw)
            except ValueError:
                self.output("请输入菜单前的数字序号。")
                continue
            if value == 0:
                return None
            if 1 <= value <= len(options):
                return value - 1
            self.output(f"请输入 0 到 {len(options)} 之间的序号。")

    def _read_new_password(self) -> str:
        while True:
            password = self.password("密码（至少 8 个字符）：")
            if len(password) < 8:
                self.output("密码少于 8 个字符，请重新输入。")
                continue
            if len(password) > 256:
                self.output("密码不能超过 256 个字符，请重新输入。")
                continue
            confirmation = self.password("再次输入密码：")
            if password != confirmation:
                self.output("两次输入的密码不一致，请重新设置。")
                continue
            return password

    def _post_auth_guide(self) -> None:
        try:
            state = self.api.get_latest_active()
        except ApiError as exc:
            if exc.status == 404 or exc.code == "NOT_FOUND":
                if self.menu_mode:
                    self.output("当前账号没有活动存档，请在游戏入口选择“开始新游戏”。")
                else:
                    self.output("当前账号没有活动存档，下面是可选出身：")
                    self._origins()
                return
            self.output("已登录，但暂时无法检查存档。下一步：输入 continue 重试，或 origins 新开一局。")
            return
        if self.menu_mode:
            self.output(f"检测到活动存档 {state.get('session_id')}，请在游戏入口选择是否继续。")
        else:
            self.output(
                f"检测到活动存档 {state.get('session_id')}。"
                "下一步：输入 continue 继续；若要另开一局，先输入 origins。"
            )

    def _command_prompt(self) -> str:
        if self.authentication_required and not self.authenticated:
            return "[未登录｜register/login/help] > "
        if not self.session_id:
            return "[未开始｜origins/new/continue/help] > "
        day = self.state.get("story", {}).get("day", "?")
        pending = self.state.get("pending_decision")
        if pending:
            kind = pending.get("input_kind")
            next_command = "order" if kind == "sorting" else (
                "allocate" if kind == "allocation" else "choose"
            )
            return f"[D{day} 决策｜{next_command}/help] > "
        available = []
        if self.commands.get("can_talk"):
            available.append("opportunities")
        if self.commands.get("can_act"):
            available.append("actions")
        if self.commands.get("can_end_day"):
            available.append("end")
        available.extend(("scene", "help"))
        return f"[D{day}｜{'/'.join(available)}] > "

    def _guide_current(self, commands: dict) -> None:
        pending = self.state.get("pending_decision")
        if self.menu_mode:
            if pending:
                self.output("请在下方编号菜单完成当前决策。")
            else:
                self.output("请在下方编号菜单选择下一步。")
            return
        if commands.get("can_choose") and pending:
            input_kind = pending.get("input_kind")
            if input_kind == "sorting":
                self.output("下一步：这是排序决策，请输入 order <完整选项顺序>。")
            elif input_kind == "allocation":
                self.output("下一步：这是分配决策，请输入 allocate <四项整数额度>。")
            else:
                self.output("下一步：输入 choose <字母> 提交当前决策，例如 choose A。")
            return
        choices = []
        if commands.get("can_talk"):
            choices.append("opportunities 查看对象，再用 talk 交谈")
        if commands.get("can_act"):
            choices.append("actions 查看行动，再用 do 执行")
        if commands.get("can_end_day"):
            choices.append("end 结束当天")
        if choices:
            self.output("下一步可选：" + "；".join(choices) + "。")
        else:
            self.output("下一步：输入 scene 刷新剧情；输入 help 查看全部命令。")

    def _safe_refresh(self) -> None:
        if self.session_id:
            try:
                self._refresh()
            except ApiError:
                pass

    def _resume_pending_operation(self) -> dict:
        client_action_id = self.pending_operation_id
        if not client_action_id:
            raise ValueError("当前没有待确认的服务端操作")
        result = self._await_operation(
            client_action_id,
            {"status": "processing", "poll_after_ms": 1000},
        )
        self.pending_operation_id = None
        self.state_version = int(result["state_version"])
        self._refresh()
        return result

    def _await_operation(self, client_action_id: str, result: dict) -> dict:
        if result.get("status") != "processing":
            return result
        session_id = self._require_session()
        wait_seconds = min(2.0, max(0.05, result.get("poll_after_ms", 500) / 1000))
        self.output("操作处理中，正在等待服务端提交……")
        # The backend can spend up to roughly 90 seconds on three 30-second
        # provider attempts.  Polling for two minutes keeps the same idempotency
        # key alive instead of inviting the player to submit the turn again.
        max_polls = max(1, int(120 / wait_seconds))
        for _ in range(max_polls):
            self.sleep(wait_seconds)
            operation = self.api.get_operation(session_id, client_action_id)
            status = operation.get("status")
            if status == "succeeded" and isinstance(operation.get("response"), dict):
                return operation["response"]
            if status in {"failed_retryable", "failed_final"}:
                error = operation.get("error") or {}
                raise ApiError(
                    str(error.get("message") or "服务端操作失败"),
                    code=str(error.get("code") or "OPERATION_FAILED"),
                    status=error.get("http_status"),
                    details=(
                        error.get("details")
                        if isinstance(error.get("details"), dict)
                        else {}
                    ),
                )
        raise ApiError("服务端操作仍在处理中，请稍后刷新", code="CLIENT_POLL_TIMEOUT")

    def _require_session(self) -> str:
        if not self.session_id:
            raise ValueError("尚未载入游戏；请先输入 new 或 load <session_id>")
        return self.session_id

    def _require_version(self) -> int:
        if self.state_version is None:
            raise ValueError("尚未取得状态版本；请先输入 scene")
        return self.state_version

    def _write_lines(self, lines: list[str]) -> None:
        for line in lines:
            self.output(line)
