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
  do <action_id> <机会ID>     从服务端开放的入口执行自主行动
  talk <opportunity_id> <话>  在服务端开放的机会中与 NPC 交谈
  knowledge                   查看已掌握的事实、线索与证据
  map                         查看当前地图入口和可用状态
  review                      查看本局只读复盘
  validate                    查看完整剧本包校验报告
  end                         结束当天并执行夜间推进
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
                if exc.code in {"STATE_VERSION_CONFLICT", "DECISION_REQUIRED"}:
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
            if len(args) != 2:
                raise ValueError("用法：do <action_id> <opportunity_id>")
            self._do(args[0], args[1])
        elif command == "talk":
            if len(args) < 2:
                raise ValueError("用法：talk <opportunity_id> <你要说的话>")
            self._talk(args[0], " ".join(args[1:]))
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
        else:
            raise ValueError(f"未知命令：{parts[0]}；输入 help 查看命令")
        return True

    def _new(self, origin_id: str) -> None:
        state = self.api.new_session(origin_id=origin_id)
        self.session_id = str(state["session_id"])
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
            self.output("下一步：输入 do <action_id> <opportunity_id>；输入 scene 返回剧情。")
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

    def _do(self, action_id: str, opportunity_id: str) -> None:
        session_id = self._require_session()
        document = self.api.get_actions(session_id)
        actions = {str(item["action_id"]): item for item in document.get("actions", [])}
        item = actions.get(action_id)
        if item is None:
            raise ValueError("行动 ID 不存在；先输入 actions 查看目录")
        if not item.get("available"):
            raise ValueError(str(item.get("unavailable_reason") or "当前行动不可用"))
        if opportunity_id not in item.get("opportunity_ids", []):
            raise ValueError("行动入口不可用；先输入 actions 查看当前入口")
        client_action_id = self.api.new_key("tool")
        result = self.api.execute_tool(
            session_id,
            state_version=int(document["state_version"]),
            action_id=action_id,
            opportunity_id=opportunity_id,
            client_action_id=client_action_id,
        )
        result = self._await_operation(client_action_id, result)
        self.state_version = int(result["state_version"])
        self.output(str(result.get("narrative", "行动已完成。")))
        self._refresh()

    def _talk(self, opportunity_id: str, player_text: str) -> None:
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
        result = self.api.submit_free_text(
            session_id,
            state_version=int(document["state_version"]),
            opportunity_id=opportunity_id,
            target_npc_id=str(opportunity["npc_id"]),
            player_text=player_text,
            client_action_id=client_action_id,
        )
        result = self._await_operation(client_action_id, result)
        self.state_version = int(result["state_version"])
        self.output("互动已完成。")
        self._refresh()

    def _knowledge(self) -> None:
        document = self.api.get_knowledge(self._require_session())
        self.state_version = int(document["state_version"])
        values = []
        for key, title in (("facts", "事实"), ("clues", "线索"), ("evidence", "证据")):
            items = document.get(key, [])
            values.append(f"{title}：{len(items)} 项")
            for item in items:
                values.append(f"  - {item.get('title') or item.get('fact_id') or item}")
        self._write_lines(values)
        self.output(
            "查看完毕，返回游戏菜单。" if self.menu_mode else
            "下一步：输入 scene 返回当前剧情；输入 map、actions 或 opportunities 查看其他入口。"
        )

    def _map(self) -> None:
        document = self.api.get_map(self._require_session())
        self.output(f"地图入口（D{document.get('story_day', '?')}）：")
        for item in document.get("locations", []):
            opportunities = ", ".join(item.get("opportunity_ids", [])) or "无"
            self.output(
                f"  {item.get('location_id')}｜{item.get('name')}｜"
                f"{item.get('visual_state')}｜机会：{opportunities}"
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
            f"夜间 {len(document.get('night_timeline', []))}"
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

    def _end_day(self) -> None:
        client_action_id = self.api.new_key("end")
        result = self.api.end_day(
            self._require_session(),
            state_version=self._require_version(),
            client_action_id=client_action_id,
        )
        result = self._await_operation(client_action_id, result)
        self.state_version = int(result["state_version"])
        self.output("夜间模拟完成。")
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
                elif exc.code in {"STATE_VERSION_CONFLICT", "DECISION_REQUIRED"}:
                    self._safe_refresh()
                else:
                    self.output("本次操作没有提交。请从下一页菜单重新选择，或选择退出后稍后再试。")
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
            options.append(("talk", "与当前可互动的 NPC 交谈（调用真实 LLM）"))
        if self.commands.get("can_act"):
            options.append(("action", "执行自主行动"))
        if self.commands.get("can_end_day"):
            options.append(("end", "结束当天并执行夜间推进"))
        options.extend((
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
            if self._select("确认结束当天？", ["确认结束当天"], back_label="取消") is not None:
                self._end_day()
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
                f"{item.get('npc_name') or item.get('npc_id')}｜"
                f"消耗 {item.get('cost_action_points')} 行动点"
                for item in opportunities
            ],
            back_label="返回上一级",
        )
        if selected is None:
            return
        text = self.input("请输入你想对该角色说的话（直接回车取消）：").strip()
        if not text:
            self.output("已取消交谈。")
            return
        self._talk(str(opportunities[selected]["opportunity_id"]), text)

    def _menu_action(self) -> None:
        document = self.api.get_actions(self._require_session())
        entries: list[tuple[dict, str]] = []
        for action in document.get("actions", []):
            if not action.get("available"):
                continue
            for opportunity_id in action.get("opportunity_ids", []):
                entries.append((action, str(opportunity_id)))
        if not entries:
            self.output("当前没有可执行的自主行动。")
            return
        selected = self._select(
            "请选择自主行动",
            [
                f"{action.get('name')}｜对象 "
                f"{action.get('opportunity_labels', {}).get(opportunity_id, '当前剧情对象')}｜"
                f"消耗 {action.get('cost_action_points')} 行动点"
                for action, opportunity_id in entries
            ],
            back_label="返回上一级",
        )
        if selected is not None:
            action, opportunity_id = entries[selected]
            self._do(str(action["action_id"]), opportunity_id)

    def _menu_logout(self) -> None:
        self.api.logout()
        self.authenticated = False
        self.session_id = None
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
            self.output(f"  {index}. {label}")
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

    def _await_operation(self, client_action_id: str, result: dict) -> dict:
        if result.get("status") != "processing":
            return result
        session_id = self._require_session()
        wait_seconds = min(2.0, max(0.05, result.get("poll_after_ms", 500) / 1000))
        self.output("操作处理中，正在等待服务端提交……")
        for _ in range(60):
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
