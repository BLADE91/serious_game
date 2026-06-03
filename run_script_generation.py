"""本地调试剧本生成器。"""

from argparse import ArgumentParser
from dataclasses import asdict, is_dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from src.config import load_dotenv
from src.domain.script_design import ScriptGenerationRequest
from src.generation.opensearch_client import OpenSearchClientError
from src.generation.qwen_client import QwenClientError
from src.generation.script_generator import ScriptGenerationError
from src.services import ScriptGenService


DEFAULT_SCRIPT_QUERY = (
    "基于《父母官》生态搬迁政治博弈仿真游戏，生成一个小规模剧本初稿。"
    "重点输出规则、约束、payoff、首批NPC、基础行动、事件概要和夜间互动规则，"
    "不要只写文学设定。"
)


def main() -> None:
    load_dotenv(override=True)

    parser = ArgumentParser(description="本地调试剧本生成器")
    parser.add_argument(
        "query",
        nargs="?",
        default=DEFAULT_SCRIPT_QUERY,
        help="剧本生成需求",
    )
    parser.add_argument(
        "--queries",
        default="",
        help="人工指定检索 query，多个 query 用英文逗号分隔；设置后跳过自动改写",
    )
    parser.add_argument(
        "--max-contexts",
        type=int,
        default=4,
        help="最多传给剧本生成器的资料数量",
    )
    parser.add_argument(
        "--out-dir",
        default="outputs/script_drafts",
        help="JSON 输出目录",
    )
    args = parser.parse_args()

    request = ScriptGenerationRequest(
        query=args.query,
        manual_queries=_parse_manual_queries(args.queries),
        max_contexts=args.max_contexts,
    )

    try:
        result = ScriptGenService.from_env().generate_script(request)
    except OpenSearchClientError as exc:
        print_error("OpenSearch 连接或查询失败", exc)
        return
    except (QwenClientError, ScriptGenerationError) as exc:
        print_error("Qwen 剧本生成失败", exc)
        return

    print_queries("检索 query", result.rewritten_queries)
    print_contexts(result.contexts_used)
    print_script(result.script)
    output_path = save_result(result, Path(args.out_dir))
    print(f"\n=== 已保存 JSON ===\n{output_path}")


def _parse_manual_queries(raw_queries: str) -> list[str]:
    if not raw_queries.strip():
        return []
    return [query.strip() for query in raw_queries.split(",") if query.strip()]


def print_queries(title: str, queries: list[str]) -> None:
    print(f"\n=== {title} ===")
    for index, query in enumerate(queries, start=1):
        print(f"{index}. {query}")


def print_contexts(contexts) -> None:
    print("\n=== 使用到的资料 ===")
    print(f"Total: {len(contexts)}")
    for index, context in enumerate(contexts, start=1):
        print(f"\n[{index}] {context.title}")
        print(f"ID: {context.id}")
        print(_preview(context.content))


def print_script(script) -> None:
    print("\n=== 剧本初稿 ===")
    print(f"Title: {script.title}")
    print(f"Premise: {script.premise}")
    print(f"Player Role: {script.player_role}")
    print(f"Core Conflict: {script.core_conflict}")

    state = script.initial_game_state
    print("\n=== 初始 GameState ===")
    print(
        f"day={state.day}, action_points={state.action_points}, "
        f"budget_remaining={state.budget_remaining}, signed={state.signed_households}/{state.total_households}, "
        f"SSI={state.social_stability_index}, PC={state.political_credit}, TEI={state.cadre_execution_index}"
    )

    print("\n=== 首批 NPC ===")
    for npc in script.npc_seed:
        print(
            f"- {npc.npc_id} | {npc.name} | {npc.npc_type} | {npc.group} | "
            f"trust={npc.trust_to_player}, attitude={npc.attitude_score}, anxiety={npc.anxiety_level}"
        )

    print("\n=== 基础行动规则 ===")
    for rule in script.action_rules:
        print(f"- {rule.action_id} | {rule.name} | AP={rule.cost_action_points} | budget={rule.budget_cost}")
        if rule.risk_notes:
            print(f"  风险: {'；'.join(rule.risk_notes)}")
        if rule.citations:
            print(f"  参考: {'；'.join(rule.citations)}")

    print("\n=== 事件概要 ===")
    for event in script.event_outline:
        print(f"- {event.event_id} | {event.name} | {event.day_window}")
        print(f"  触发: {event.trigger_condition}")
        print(f"  描述: {event.description}")

    print("\n=== 夜间规则 ===")
    for index, rule in enumerate(script.night_rules, start=1):
        print(f"{index}. {rule}")

    print("\n=== Payoff Notes ===")
    for index, note in enumerate(script.payoff_notes, start=1):
        print(f"{index}. {note}")


def save_result(result, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = out_dir / f"script_draft_{timestamp}.json"
    payload = to_jsonable(result)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path


def to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {key: to_jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    return value


def _preview(content: str, limit: int = 360) -> str:
    normalized = " ".join(content.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit]}..."


def print_error(title: str, exc: Exception) -> None:
    print(f"\n=== {title} ===")
    print(str(exc))


if __name__ == "__main__":
    main()
