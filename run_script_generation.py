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
from src.generation.pa_backend_script_client import PABackendClientError
from src.generation.qwen_client import QwenClientError
from src.generation.script_generator import ScriptGenerationError
from src.services import ScriptGenService
from src.services.script_validator import ScriptValidationError


DEFAULT_SCRIPT_QUERY = (
    "基于《父母官》生态搬迁政治博弈仿真游戏，生成一个小规模剧本初稿。"
    "重点输出规则、约束、payoff、首批NPC、基础行动、事件概要和夜间互动规则，"
    "不要只写文学设定。"
)


def main() -> None:
    load_dotenv(override=True)

    parser = ArgumentParser(description="本地调试剧本生成器")
    # 新结构化输入（推荐）
    parser.add_argument(
        "--scenario", "-s",
        default="",
        help="政策场景，如 '生态搬迁'、'征地拆迁'",
    )
    parser.add_argument(
        "--player-role", "-r",
        default="",
        help="玩家角色，如 '乡镇党委副书记'",
    )
    parser.add_argument(
        "--learning-goal", "-g",
        default="",
        help="教育目标，如 '体验基层政策执行中的多重压力'",
    )
    parser.add_argument(
        "--duration", "-d",
        type=int,
        default=45,
        help="目标游戏时长（分钟），默认 45",
    )
    parser.add_argument(
        "--complexity", "-c",
        choices=["simple", "medium", "complex"],
        default="medium",
        help="剧本复杂度，默认 medium",
    )
    parser.add_argument(
        "--extra", "-x",
        default="",
        help="额外要求，自由文本",
    )
    # 旧输入（兼容）
    parser.add_argument(
        "query",
        nargs="?",
        default="",
        help="剧本生成需求（自由文本，兼容旧版）",
    )
    parser.add_argument(
        "--queries",
        default="",
        help="人工指定检索 query，多个 query 用英文逗号分隔",
    )
    parser.add_argument(
        "--review-queries",
        action="store_true",
        help="检索前先打印 query，并允许人工确认或替换",
    )
    parser.add_argument(
        "--feedback",
        default="",
        help="传给剧本生成器的人工反馈或额外要求",
    )
    parser.add_argument(
        "--revise",
        default="",
        metavar="JSON_PATH",
        help="读取已有剧本 JSON，根据 --feedback 生成修订稿",
    )
    parser.add_argument(
        "--full-draft",
        action="store_true",
        default=True,
        help="分阶段生成完整结构化初稿（默认开启）",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="使用紧凑模式（与 --full-draft 互斥）",
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

    try:
        service = ScriptGenService.from_env()
        if args.revise:
            if not args.feedback.strip():
                print("--revise 必须同时提供非空的 --feedback。")
                return
            previous_result = load_result(Path(args.revise))
            # 修订时从旧稿提取 query
            original_query = previous_result.get("original_query", args.query or DEFAULT_SCRIPT_QUERY)
            result = service.revise_script(
                previous_result=previous_result,
                query=original_query,
                feedback=args.feedback,
            )
        else:
            full_draft = not args.compact
            request = ScriptGenerationRequest(
                scenario=args.scenario,
                player_role=args.player_role,
                learning_goal=args.learning_goal,
                duration_minutes=args.duration,
                complexity=args.complexity,
                extra_requirements=args.extra,
                query=args.query,
                manual_queries=_parse_manual_queries(args.queries),
                feedback=args.feedback,
                max_contexts=args.max_contexts,
                full_draft=full_draft,
            )
            if args.review_queries:
                reviewed_queries = review_queries(service.resolve_queries(request))
                if not reviewed_queries:
                    print("已取消生成。")
                    return
                request = ScriptGenerationRequest(
                    scenario=args.scenario,
                    player_role=args.player_role,
                    learning_goal=args.learning_goal,
                    duration_minutes=args.duration,
                    complexity=args.complexity,
                    extra_requirements=args.extra,
                    query=args.query,
                    manual_queries=reviewed_queries,
                    feedback=args.feedback,
                    max_contexts=args.max_contexts,
                    full_draft=full_draft,
                )
            result = service.generate_script(
                request,
                progress_callback=print_generation_progress if full_draft else None,
            )
    except (OSError, json.JSONDecodeError) as exc:
        print_error("读取旧稿失败", exc)
        return
    except ScriptValidationError as exc:
        print_error("剧本结构校验失败", exc)
        return
    except ValueError as exc:
        print_error("参数或配置校验失败", exc)
        return
    except OpenSearchClientError as exc:
        print_error("OpenSearch 连接或查询失败", exc)
        return
    except (QwenClientError, PABackendClientError, ScriptGenerationError) as exc:
        print_error("剧本生成失败", exc)
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


def load_result(path: Path) -> dict[str, Any]:
    """读取已有剧本生成结果。"""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("旧稿 JSON 顶层必须是对象")
    return payload


def review_queries(queries: list[str]) -> list[str]:
    print_queries("待使用的检索 query", queries)
    print("\n直接回车使用这些 query；输入新的 query 可替换，多个 query 用英文逗号分隔；输入 q 取消。")
    user_input = input("确认检索 query: ").strip()
    if not user_input:
        return queries
    if user_input.lower() in {"q", "quit", "cancel"}:
        return []
    return _parse_manual_queries(user_input)


def print_queries(title: str, queries: list[str]) -> None:
    print(f"\n=== {title} ===")
    for index, query in enumerate(queries, start=1):
        print(f"{index}. {query}")


def print_generation_progress(
    stage: int,
    total_stages: int,
    name: str,
    request_bytes: int,
) -> None:
    print(
        f"\n=== 完整初稿阶段 {stage}/{total_stages}: {name} ===\n"
        f"Qwen 请求体: {request_bytes / 1024:.1f} KiB",
        flush=True,
    )


def print_contexts(contexts) -> None:
    print("\n=== 使用到的资料 ===")
    print(f"Total: {len(contexts)}")
    for index, context in enumerate(contexts, start=1):
        print(f"\n[{index}] {context.title}")
        print(f"ID: {context.id}")
        print(_preview(context.content))


def print_script(script) -> None:
    print("\n=== 剧本初稿 ===")
    print(f"标题: {script.title}")
    print(f"设定: {script.premise}")
    print(f"玩家角色: {script.player_role}")
    print(f"核心冲突: {script.core_conflict}")

    state = script.initial_game_state
    print(f"\n=== 初始状态 ===")
    print(
        f"第{state.day}天 | AP={state.action_points} | "
        f"预算={state.budget_remaining}{state.budget_unit} | "
        f"签约={state.signed_households}/{state.total_households}"
    )
    print(
        f"社会稳定={state.social_stability_index} | "
        f"政治信用={state.political_credit} | "
        f"干部执行={state.cadre_execution_index}"
    )

    # 三幕结构
    if script.acts:
        print(f"\n=== 三幕结构 ===")
        for act in script.acts:
            print(f"第{act.act_number}幕：{act.title}（{act.day_range}）")
            print(f"  目标: {act.goal}")
            print(f"  概述: {act.description}")
            print(f"  决策点: {len(act.decision_point_ids)} 个")

    # NPC 列表
    if script.npc_seed:
        print(f"\n=== NPC 角色（共 {len(script.npc_seed)} 人）===")
        type_labels = {"cadre": "干部", "external": "外部", "villager": "村民"}
        for npc in script.npc_seed:
            print(
                f"- {npc.npc_id} | {npc.name} | {type_labels.get(npc.npc_type, npc.npc_type)} | "
                f"{npc.group} | 信任={npc.trust_to_player} | 态度={npc.attitude_score}"
            )

    # NPC 关系网
    if script.npc_relationships:
        print(f"\n=== NPC 关系网（共 {len(script.npc_relationships)} 条）===")
        name_map = {npc.npc_id: npc.name for npc in script.npc_seed}
        # 按关系类型分组
        by_type: dict[str, list] = {}
        for rel in script.npc_relationships:
            by_type.setdefault(rel.relation_type, []).append(rel)
        for rel_type, rels in by_type.items():
            print(f"\n  [{rel_type}]")
            for rel in rels[:5]:  # 每种类型最多显示 5 条
                from_name = name_map.get(rel.from_npc_id, rel.from_npc_id)
                to_name = name_map.get(rel.to_npc_id, rel.to_npc_id)
                print(f"    {from_name} → {to_name} (强度={rel.strength})：{rel.description}")

    # 决策点
    if script.decision_points:
        print(f"\n=== 决策点序列（共 {len(script.decision_points)} 个）===")
        for dp in script.decision_points:
            critical_mark = " ★关键决策" if dp.is_critical else ""
            print(f"\n[{dp.decision_id}]{critical_mark} {dp.title}（{dp.day_window}）")
            print(f"  情境: {dp.situation}")
            if dp.affected_npc_ids:
                npc_names = [name_map.get(nid, nid) for nid in dp.affected_npc_ids if name_map]
                print(f"  关联NPC: {', '.join(npc_names) if npc_names else ', '.join(dp.affected_npc_ids)}")
            print(f"  选项 ({len(dp.options)} 个):")
            for opt in dp.options:
                costs = []
                if opt.cost_action_points:
                    costs.append(f"AP×{opt.cost_action_points}")
                if opt.budget_cost:
                    costs.append(f"预算{opt.budget_cost}{state.budget_unit}")
                cost_str = f"（{'，'.join(costs)}）" if costs else ""
                print(f"    ◇ {opt.option_id} | {opt.label}{cost_str}")
                print(f"      {opt.description}")
                if opt.risks:
                    print(f"      风险: {'；'.join(opt.risks)}")

    # 多结局
    if script.endings:
        print(f"\n=== 多结局（共 {len(script.endings)} 个）===")
        type_labels = {"good": "✓ 好结局", "neutral": "≈ 中性结局", "bad": "✗ 坏结局"}
        for end in script.endings:
            label = type_labels.get(end.ending_type, end.ending_type)
            print(f"\n[{end.ending_id}] {label}: {end.title}")
            print(f"  {end.description}")
            if end.conditions:
                print(f"  条件: {' AND '.join(end.conditions)}")


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
