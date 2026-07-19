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
    ) -> None:
        self.api = api
        self.input = input_fn
        self.output = output_fn
        self.sleep = sleep_fn
        self.password = password_fn
        self.session_id: str | None = None
        self.state_version: int | None = None
        self.feed_cursor = 0
        self.state: dict = {}
        self.option_labels: dict[str, str] = {}

    def run(self) -> int:
        self.output("《浊流之下·清江搬迁记》文字测试客户端")
        try:
            readiness_call = getattr(self.api, "readiness", None)
            readiness = readiness_call() if readiness_call else {}
        except ApiError:
            readiness = {}
        if readiness.get("authentication_required"):
            self.output("本地账号模式已启用：输入 register <用户名> 或 login <用户名>。")
        else:
            self.output("输入 origins 查看出身；输入 new <origin_id> 开始游戏。")
        while True:
            try:
                raw = self.input("> ").strip()
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
            password = self.password("密码（至少 12 个字符）：")
            if password != self.password("再次输入密码："):
                raise ValueError("两次输入的密码不一致")
            result = self.api.register(args[0], password)
            self.output(f"注册并登录成功：{result['account_id']}")
        elif command == "login":
            if len(args) != 1:
                raise ValueError("用法：login <username>")
            result = self.api.login(args[0], self.password("密码："))
            self.output(f"登录成功：{result['account_id']}")
        elif command == "whoami":
            result = self.api.me()
            self.output(
                f"当前账号：{result['account_id']}｜角色："
                f"{','.join(result.get('roles', []))}"
            )
        elif command == "logout":
            self.api.logout()
            self.session_id = None
            self.state_version = None
            self.output("已退出登录。")
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

    def _continue_latest(self) -> None:
        state = self.api.get_latest_active()
        self._load(str(state["session_id"]))

    def _load(self, session_id: str) -> None:
        self.session_id = session_id
        self.state_version = None
        self.feed_cursor = 0
        self.state = {}
        self.option_labels = {}
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
            self.state.get("pending_decision")
        )
        self._write_lines(decision_lines)
        commands = document.get("commands", {})
        if not commands.get("can_choose") and not any(
            commands.get(key) for key in ("can_act", "can_talk", "can_end_day")
        ):
            self.output("当前剧情节点暂无可提交命令。")

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

    def _opportunities(self) -> None:
        document = self.api.get_opportunities(self._require_session())
        self.state_version = int(document["state_version"])
        self._write_lines(render_opportunities(document))

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

    def _map(self) -> None:
        document = self.api.get_map(self._require_session())
        self.output(f"地图入口（D{document.get('story_day', '?')}）：")
        for item in document.get("locations", []):
            opportunities = ", ".join(item.get("opportunity_ids", [])) or "无"
            self.output(
                f"  {item.get('location_id')}｜{item.get('name')}｜"
                f"{item.get('visual_state')}｜机会：{opportunities}"
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
