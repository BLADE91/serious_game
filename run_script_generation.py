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
        "--extra", "-x",
        default="",
        help="额外要求，自由文本",
    )
    parser.add_argument(
        "--npc-count",
        type=int,
        default=36,
        help="NPC 数量，默认 36",
    )
    parser.add_argument(
        "--character-settings",
        default="",
        help="人物设定，自由文本",
    )
    parser.add_argument(
        "--story-background",
        default="",
        help="故事背景，自由文本",
    )
    parser.add_argument(
        "--md-only",
        action="store_true",
        help="只生成 MD 剧本原文，不提取结构化 JSON",
    )
    parser.add_argument(
        "--extract-from-md",
        default="",
        metavar="MD_PATH",
        help="从已有 MD 文件提取结构化 JSON",
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
        "--chapter",
        action="store_true",
        help="使用新的 6-Call 章节式管线生成（PA Backend ×3 + Qwen Flash ×3）",
    )
    parser.add_argument(
        "--chapter-count",
        type=int,
        default=6,
        help="章节数量（仅 --chapter 模式，默认 6）",
    )
    parser.add_argument(
        "--ending-count",
        type=int,
        default=4,
        help="结局数量（仅 --chapter 模式，默认 4）",
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
                extra_requirements=args.extra,
                npc_count=args.npc_count,
                character_settings=args.character_settings,
                story_background=args.story_background,
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
                    extra_requirements=args.extra,
                    npc_count=args.npc_count,
                    character_settings=args.character_settings,
                    story_background=args.story_background,
                    query=args.query,
                    manual_queries=reviewed_queries,
                    feedback=args.feedback,
                    max_contexts=args.max_contexts,
                    full_draft=full_draft,
                )
            if args.chapter:
                print("=== 6-Call 章节式管线生成 ===")
                print(f"章节数: {args.chapter_count}，结局数: {args.ending_count}")
                request = ScriptGenerationRequest(
                    scenario=args.scenario,
                    player_role=args.player_role,
                    learning_goal=args.learning_goal,
                    duration_minutes=args.duration,
                    extra_requirements=args.extra,
                    npc_count=args.npc_count,
                    character_settings=args.character_settings,
                    story_background=args.story_background,
                    query=args.query,
                    manual_queries=_parse_manual_queries(args.queries),
                    feedback=args.feedback,
                    max_contexts=args.max_contexts,
                    full_draft=True,
                    chapter_count=args.chapter_count,
                    ending_count=args.ending_count,
                )
                # 如果 out_dir 已是版本目录（如 v06），直接复用；否则新建
                version_dir = _resolve_version_dir(Path(args.out_dir))
                result = service.generate_chapter_script(
                    request,
                    progress_callback=print_chapter_progress,
                    output_dir=str(version_dir),
                )
                # 把最终 MD 也存进版本目录
                _save_final_md(result, version_dir)
            elif args.extract_from_md:
                md_path = Path(args.extract_from_md)
                if not md_path.exists():
                    print(f"MD 文件不存在：{md_path}")
                    return
                raw = md_path.read_text(encoding="utf-8")
                # .md 文件直接读取；.json 文件提取 full_md 字段
                if md_path.suffix == '.json':
                    try:
                        data = json.loads(raw)
                        md_text = data.get("full_md", raw) if isinstance(data, dict) else raw
                        print(f"从 JSON 提取 full_md（{len(md_text)} 字符）")
                    except (json.JSONDecodeError, TypeError):
                        md_text = raw
                else:
                    md_text = raw
                    print(f"读取 MD 文件（{len(md_text)} 字符）")
                query = request.query or args.query or DEFAULT_SCRIPT_QUERY
                script = service.extract_from_md(
                    full_md=md_text,
                    query=query,
                    npc_count=args.npc_count,
                    progress_callback=print_generation_progress,
                )
                from src.domain.script_design import ScriptGenerationResult
                result = ScriptGenerationResult(
                    script=script,
                    full_md=md_text,
                    generation_notes=["从 MD 文件提取结构化 JSON"],
                    original_query=query,
                    generation_mode="full",
                )
            elif args.md_only:
                print("=== Phase 1 only：仅生成 MD 剧本 ===")
                full_md, query = service.generate_md_only(
                    request,
                    progress_callback=print_generation_progress,
                )
                print(f"\n=== MD 剧本（共 {len(full_md)} 字符）===")
                print(full_md[:2000])
                if len(full_md) > 2000:
                    print(f"\n...（省略 {len(full_md) - 2000} 字符）...")
                # 保存为纯 .md 文件
                out_dir = Path(args.out_dir)
                out_dir.mkdir(parents=True, exist_ok=True)
                from src.api.server import _load_counter, _save_counter
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                counter = _load_counter()
                gen = counter["gen"] + 1
                rev = 0
                counter["gen"] = gen
                counter["rev"] = rev
                _save_counter(counter)
                filename = f"v{gen:02d}-{rev:02d}_script_generate_{timestamp}.md"
                output_path = out_dir / filename
                output_path.write_text(full_md, encoding="utf-8")
                print(f"\n=== 已保存 ===\n{output_path}")
                return
            else:
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

    if args.chapter:
        print("\n=== 生成完成 ===")
        print(f"标题: {result.script.title}")
        print(f"章节数: {len(result.script.chapters)}")
        # 从各章 checkpoint 中统计结局引用
        ending_refs = set()
        for ch in result.script.chapters:
            if ch.checkpoint and ch.checkpoint.next_chapter:
                if ch.checkpoint.next_chapter.startswith("ending_"):
                    ending_refs.add(ch.checkpoint.next_chapter)
        print(f"引用结局: {len(ending_refs)} 个（{', '.join(sorted(ending_refs)) if ending_refs else '各章内置'}）")
        print(f"完整 MD 长度: {len(result.full_md)} 字符")
        # 打印 MD 开头预览
        print(f"\n=== MD 剧本预览（前 2000 字符）===")
        print(result.full_md[:2000])
        if len(result.full_md) > 2000:
            print(f"\n...（省略 {len(result.full_md) - 2000} 字符）...")
    else:
        print_queries("检索 query", result.rewritten_queries)
        print_contexts(result.contexts_used)
        print_script(result.script)
    if args.chapter:
        # 章节模式：JSON 直接存入版本目录
        output_path = save_result(result, version_dir, prefix="script_generate", new_version=False)
    else:
        prefix = "script_revise" if args.revise else "script_generate"
        output_path = save_result(result, Path(args.out_dir), prefix=prefix)
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


def _resolve_version_dir(out_dir: Path) -> Path:
    """决定版本目录。

    如果 out_dir 本身已经是 v{num} 格式的目录（如 v06），直接复用。
    否则在 out_dir 下新建 v{gen+1:02d}。
    """
    import re
    from src.api.server import _load_counter, _save_counter

    # 如果传入的就是版本目录（如 outputs/.../v06），直接用它
    if re.match(r"^v\d{2}$", out_dir.name) and out_dir.is_dir():
        print(f"\n📁 复用版本目录: {out_dir}")
        return out_dir

    # 否则新建
    counter = _load_counter()
    gen = counter["gen"] + 1
    counter["gen"] = gen
    counter["rev"] = 0
    _save_counter(counter)
    version_dir = out_dir / f"v{gen:02d}"
    version_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n📁 新建版本目录: {version_dir}")
    return version_dir


def _save_final_md(result, version_dir: Path) -> Path:
    """保存最终 MD 到版本目录。"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"final_{timestamp}.md"
    output_path = version_dir / filename
    output_path.write_text(result.full_md, encoding="utf-8")
    print(f"💾 最终 MD: {output_path}（{len(result.full_md)} 字符）")
    return output_path


def print_chapter_progress(
    stage: int,
    total_stages: int,
    name: str,
    request_bytes: int,
) -> None:
    bar = "█" * stage + "░" * (total_stages - stage)
    kb = f" ({request_bytes / 1024:.1f} KiB)" if request_bytes else ""
    print(
        f"\n📖 [{bar}] {stage}/{total_stages}  {name}{kb}",
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


def save_result(result, out_dir: Path, prefix: str = "script_generate", new_version: bool = True) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if new_version:
        from src.api.server import _load_counter, _save_counter
        counter = _load_counter()
        if prefix == "script_generate":
            gen = counter["gen"] + 1
            rev = 0
        else:
            gen = counter["gen"] if counter["gen"] > 0 else 1
            rev = counter["rev"] + 1
        counter["gen"] = gen
        counter["rev"] = rev
        _save_counter(counter)
        save_dir = out_dir / f"v{gen:02d}"
        save_dir.mkdir(parents=True, exist_ok=True)
        filename = f"v{gen:02d}-{rev:02d}_{prefix}_{timestamp}.json"
    else:
        # 直接存入已有目录，不创建版本子目录
        save_dir = out_dir
        save_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{prefix}_{timestamp}.json"

    output_path = save_dir / filename
    payload = to_jsonable(result)
    # 确保 full_md 写入
    if hasattr(result, 'full_md') and result.full_md:
        payload["full_md"] = result.full_md
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
    if isinstance(value, set):
        return sorted(list(value))
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
