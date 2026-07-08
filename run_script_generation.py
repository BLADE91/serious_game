"""本地调试剧本生成器。"""

from argparse import ArgumentParser
from dataclasses import asdict, is_dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from src.config import load_dotenv
from src.domain.script_design import ScriptGenerationRequest
from src.generation.pa_backend_script_client import PABackendClientError
from src.generation.qwen_client import QwenClientError
from src.services import ScriptGenService
from src.services.chapter_revision_service import ChapterRevisionService


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
    # 旧输入（兼容）
    parser.add_argument(
        "query",
        nargs="?",
        default="",
        help="剧本生成需求（自由文本，兼容旧版）",
    )
    parser.add_argument(
        "--feedback",
        default="",
        help="传给剧本生成器的人工反馈或额外要求",
    )
    parser.add_argument(
        "--chapter-revise",
        default="",
        metavar="VERSION",
        help="修订章节式版本，如 v01 或 v01/revisions/r01",
    )
    parser.add_argument(
        "--revision-target",
        default="",
        metavar="TARGET",
        help="章节修订目标：game_settings、chapter_outline 或 chNN",
    )
    parser.add_argument(
        "--revision-file",
        default="",
        metavar="MD_PATH",
        help="直接应用该 Markdown 文件；不提供时根据 --feedback 生成 AI 修订候选",
    )
    parser.add_argument(
        "--revision-preview-only",
        action="store_true",
        help="仅输出章节修订 diff，不创建修订版本",
    )
    parser.add_argument(
        "--revision-sync-affected",
        action="store_true",
        help="已弃用；全局设定或大纲修订现在始终自动同步受影响章节",
    )
    parser.add_argument(
        "--chapter",
        action="store_true",
        help="兼容旧命令；当前 CLI 始终使用章节式管线",
    )
    parser.add_argument(
        "--chapter-count",
        type=int,
        default=6,
        help="章节数量，默认 6",
    )
    parser.add_argument(
        "--ending-count",
        type=int,
        default=4,
        help="结局数量，默认 4",
    )
    parser.add_argument(
        "--decision-point-count",
        type=int,
        default=3,
        help="每章决策点数量，默认 3",
    )
    parser.add_argument(
        "--out-dir",
        default="outputs/script_drafts",
        help="JSON 输出目录",
    )
    args = parser.parse_args()

    try:
        if args.chapter_revise:
            if not args.revision_target:
                raise ValueError("--chapter-revise 必须同时提供 --revision-target")
            revision_service = ChapterRevisionService(outputs_dir=args.out_dir)
            if args.revision_file:
                revision_path = Path(args.revision_file)
                revised_content = revision_path.read_text(encoding="utf-8")
                preview = revision_service.preview_manual(
                    args.chapter_revise,
                    args.revision_target,
                    revised_content,
                )
                mode = "manual"
            else:
                if not args.feedback.strip():
                    raise ValueError(
                        "章节 AI 修订必须提供 --feedback；直接修改请提供 --revision-file"
                    )
                preview = revision_service.preview_ai(
                    args.chapter_revise,
                    args.revision_target,
                    args.feedback,
                )
                revised_content = preview["revised_content"]
                mode = "ai"

            print("\n=== 修订差异 ===")
            print(preview["diff"] or "内容没有变化")
            if args.revision_preview_only or not preview["changed"]:
                return

            impact = revision_service.analyze_impact(
                args.chapter_revise,
                args.revision_target,
                revised_content,
            )
            affected = [
                item["chapter_id"]
                for item in impact.get("affected_chapters", [])
            ]
            if affected:
                print("\n=== 修订影响 ===")
                print(f"影响级别: {impact['impact_level']}")
                print(f"受影响章节: {', '.join(affected)}")
            revised = revision_service.apply_revision(
                base_version=args.chapter_revise,
                target=args.revision_target,
                content=revised_content,
                mode=mode,
                feedback=args.feedback,
                impact_acknowledged=True,
            )
            print("\n=== 章节修订完成 ===")
            print(f"修订版本: {revised['revision_dir']}")
            print(f"结果文件: {revised['saved_as']}")
            validation = revised["script"].get("validation_report", {})
            print(f"程序校验: {'通过' if validation.get('valid') else '未通过'}")
            print(f"修订状态: {revised.get('revision_status', 'complete')}")
            return

        print("=== 章节式管线生成 ===")
        print(
            f"章节数: {args.chapter_count}，结局数: {args.ending_count}，"
            f"每章决策点: {args.decision_point_count}"
        )
        request = ScriptGenerationRequest(
            scenario=args.scenario,
            player_role=args.player_role,
            learning_goal=args.learning_goal,
            duration_minutes=args.duration,
            extra_requirements=args.extra,
            npc_count=args.npc_count,
            character_settings=args.character_settings,
            story_background=args.story_background,
            query=args.query or DEFAULT_SCRIPT_QUERY,
            feedback=args.feedback,
            chapter_count=args.chapter_count,
            ending_count=args.ending_count,
            decision_point_count=args.decision_point_count,
        )
        service = ScriptGenService()
        version_dir = _resolve_version_dir(Path(args.out_dir))
        result = service.generate_chapter_script(
            request,
            progress_callback=print_chapter_progress,
            output_dir=str(version_dir),
        )
        _save_final_md(result, version_dir)
    except (OSError, json.JSONDecodeError) as exc:
        print_error("读取旧稿失败", exc)
        return
    except ValueError as exc:
        print_error("参数或配置校验失败", exc)
        return
    except (QwenClientError, PABackendClientError) as exc:
        print_error("剧本生成失败", exc)
        return

    print("\n=== 生成完成 ===")
    print(f"标题: {result.script.title}")
    print(f"章节数: {len(result.script.chapters)}")
    print(f"结局数: {len(result.script.chapter_endings or result.script.endings)}")
    print(f"完整 MD 长度: {len(result.full_md)} 字符")
    print(f"\n=== MD 剧本预览（前 2000 字符）===")
    print(result.full_md[:2000])
    if len(result.full_md) > 2000:
        print(f"\n...（省略 {len(result.full_md) - 2000} 字符）...")
    output_path = save_result(result, version_dir, prefix="script_generate")
    print(f"\n=== 已保存 JSON ===\n{output_path}")


def _resolve_version_dir(out_dir: Path) -> Path:
    """决定版本目录。

    如果 out_dir 本身已经是 v{num} 格式的目录（如 v06），直接复用。
    否则在 out_dir 下新建 v{gen+1:02d}。
    """
    import re
    from src.api.server import _reserve_generation_number

    # 如果传入的就是版本目录（如 outputs/.../v06），直接用它
    if re.match(r"^v\d{2}$", out_dir.name) and out_dir.is_dir():
        print(f"\n📁 复用版本目录: {out_dir}")
        return out_dir

    # 否则新建
    gen = _reserve_generation_number(out_dir)
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


def save_result(result, out_dir: Path, prefix: str = "script_generate") -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
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


def print_error(title: str, exc: Exception) -> None:
    print(f"\n=== {title} ===")
    print(str(exc))


if __name__ == "__main__":
    main()
