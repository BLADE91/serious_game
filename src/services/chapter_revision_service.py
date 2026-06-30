"""章节式剧本的人工与 AI 辅助修订服务。"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
import difflib
import json
from pathlib import Path
import re
import shutil
import threading
from typing import Any

from src.config import QwenConfig, load_dotenv
from src.generation.chapter_script_generator import ChapterScriptGenerator
from src.generation.qwen_client import ChatMessage, QwenChatClient


class ChapterRevisionService:
    """以 Markdown 为唯一创作源，重建受影响的章节式管线产物。"""

    TARGET_FILES = {
        "game_settings": "01_game_settings.md",
        "chapter_outline": "02_chapter_outline.md",
    }

    def __init__(
        self,
        outputs_dir: str | Path = "outputs/script_drafts",
        flash_client: QwenChatClient | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self._outputs_dir = Path(outputs_dir).resolve()
        self._flash_client = flash_client
        self._cancel_event = cancel_event

    def load_source(self, base_version: str, target: str) -> dict[str, Any]:
        base_dir = self._resolve_base_dir(base_version)
        filename = self._target_filename(target)
        path = base_dir / filename
        if not path.exists():
            raise ValueError(f"修订目标不存在: {target}")
        content = path.read_text(encoding="utf-8")
        return {
            "base_version": self._relative_ref(base_dir),
            "target": target,
            "filename": filename,
            "content": content,
        }

    def preview_manual(
        self,
        base_version: str,
        target: str,
        content: str,
    ) -> dict[str, Any]:
        source = self.load_source(base_version, target)
        if not content.strip():
            raise ValueError("修订后的 Markdown 不能为空")
        return self._preview_payload(source, content, mode="manual")

    def preview_ai(
        self,
        base_version: str,
        target: str,
        feedback: str,
    ) -> dict[str, Any]:
        if not feedback.strip():
            raise ValueError("AI 修订反馈不能为空")
        source = self.load_source(base_version, target)
        base_dir = self._resolve_base_dir(base_version)
        messages = self._build_ai_revision_messages(
            base_dir=base_dir,
            target=target,
            current_content=source["content"],
            feedback=feedback,
        )
        revised = self._flash().complete(
            messages,
            temperature=0.2,
            response_format="text",
            max_tokens=8192,
        )
        revised = self._clean_markdown(revised)
        if not revised.strip():
            raise ValueError("AI 返回了空的 Markdown")
        payload = self._preview_payload(source, revised, mode="ai")
        payload["feedback"] = feedback.strip()
        return payload

    def apply_revision(
        self,
        base_version: str,
        target: str,
        content: str,
        mode: str,
        feedback: str = "",
    ) -> dict[str, Any]:
        if mode not in {"manual", "ai"}:
            raise ValueError("mode 必须是 manual 或 ai")
        if not content.strip():
            raise ValueError("修订后的 Markdown 不能为空")

        base_dir = self._resolve_base_dir(base_version)
        filename = self._target_filename(target)
        if not (base_dir / filename).exists():
            raise ValueError(f"修订目标不存在: {target}")

        revision_dir, revision_name = self._reserve_revision_dir(base_dir)
        self._copy_revision_inputs(base_dir, revision_dir)
        (revision_dir / filename).write_text(content, encoding="utf-8")

        manifest = {
            "revision": revision_name,
            "parent": self._relative_ref(base_dir),
            "mode": mode,
            "target": target,
            "feedback": feedback.strip(),
            "changed_files": [filename],
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "status": "building",
        }
        self._write_json(revision_dir / "revision_manifest.json", manifest)

        try:
            result = self._rebuild_revision(
                base_dir=base_dir,
                revision_dir=revision_dir,
                target=target,
                manifest=manifest,
            )
        except Exception:
            manifest["status"] = "failed"
            self._write_json(revision_dir / "revision_manifest.json", manifest)
            raise

        manifest["status"] = "complete"
        manifest["validation"] = result["script"].get("validation_report", {})
        self._write_json(revision_dir / "revision_manifest.json", manifest)
        result["revision_manifest"] = manifest
        return result

    def _rebuild_revision(
        self,
        base_dir: Path,
        revision_dir: Path,
        target: str,
        manifest: dict[str, Any],
    ) -> dict[str, Any]:
        chapter_paths = sorted(revision_dir.glob("03_ch[0-9][0-9].md"))
        if not chapter_paths:
            raise ValueError("修订版本缺少章节 Markdown")

        settings_md = (revision_dir / "01_game_settings.md").read_text(encoding="utf-8")
        outline_md = (revision_dir / "02_chapter_outline.md").read_text(encoding="utf-8")
        chapters_md = [path.read_text(encoding="utf-8") for path in chapter_paths]

        generator = ChapterScriptGenerator(
            flash_client=self._flash(),
            cancel_event=self._cancel_event,
        )
        generator._output_dir = revision_dir
        merged_md = generator._merge_chapters(settings_md, outline_md, chapters_md)
        generator._save_intermediate("04_merged_before_review.md", merged_md)

        try:
            continuity = generator._call4_consistency_review(merged_md)
        except Exception as exc:
            continuity = {
                "status": "not_run",
                "continuity_issues": [],
                "error": str(exc),
            }
        generator._save_intermediate(
            "05_continuity_review.json",
            json.dumps(continuity, ensure_ascii=False, indent=2),
        )

        chapter_ids = [f"ch{index:02d}" for index in range(1, len(chapters_md) + 1)]
        global_path = revision_dir / "06a_global.json"
        if target in {"game_settings", "chapter_outline"} or not global_path.exists():
            global_json = generator._call5a_extract_global(merged_md, chapter_ids)
            self._write_json(global_path, global_json)
        else:
            global_json = json.loads(global_path.read_text(encoding="utf-8"))

        chapter_jsons: list[dict[str, Any]] = []
        for index, chapter_md in enumerate(chapters_md, start=1):
            chapter_id = f"ch{index:02d}"
            chapter_json_path = revision_dir / f"06b_{chapter_id}.json"
            if target == chapter_id or not chapter_json_path.exists():
                chapter_json = generator._call5b_extract_chapter(
                    chapter_md, chapter_id, index,
                )
                self._write_json(chapter_json_path, chapter_json)
            else:
                chapter_json = json.loads(chapter_json_path.read_text(encoding="utf-8"))
            chapter_jsons.append(chapter_json)

        merged_json = dict(global_json)
        merged_json.pop("_chapter_ids", None)
        merged_json["chapters"] = chapter_jsons
        self._write_json(revision_dir / "06_script_structure.json", merged_json)

        expected_npc_count = len(merged_json.get("npcs", []))
        validation = generator._call6_validate(
            merged_json,
            expected_npc_count=expected_npc_count,
            complete_script_md=merged_md,
            continuity_review=continuity,
        )
        self._write_json(revision_dir / "07_validation_report.json", validation)
        merged_json["_validation"] = validation
        script = generator._build_script_design(merged_json)

        base_payload = self._load_latest_result(base_dir)
        revision_round = self._revision_number(revision_dir.name)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        final_name = f"final_{timestamp}.md"
        result_name = f"script_revise_{timestamp}.json"
        (revision_dir / final_name).write_text(merged_md, encoding="utf-8")

        payload = {
            "script": self._jsonable(script),
            "full_md": merged_md,
            "contexts_used": base_payload.get("contexts_used", []),
            "rewritten_queries": base_payload.get("rewritten_queries", []),
            "generation_notes": [
                f"章节式修订 {revision_dir.name}: {target} ({manifest['mode']})",
                "Markdown 源文件已更新，并重新执行连续性、抽取和程序校验。",
            ],
            "original_query": base_payload.get("original_query", ""),
            "feedback": manifest.get("feedback", ""),
            "revision_round": revision_round,
            "generation_mode": "chapter",
        }
        self._write_json(revision_dir / result_name, payload)
        payload["saved_as"] = self._relative_ref(revision_dir / result_name)
        payload["revision_dir"] = self._relative_ref(revision_dir)
        return payload

    def _build_ai_revision_messages(
        self,
        base_dir: Path,
        target: str,
        current_content: str,
        feedback: str,
    ) -> list[ChatMessage]:
        context_parts = []
        for filename in ("01_game_settings.md", "02_chapter_outline.md"):
            path = base_dir / filename
            if path.exists() and path.name != self._target_filename(target):
                context_parts.append(f"## {filename}\n{path.read_text(encoding='utf-8')}")
        context = "\n\n".join(context_parts)
        return [
            ChatMessage(
                role="system",
                content=(
                    "你是章节式剧本 Markdown 定向修订器。只修改用户指定的目标文件，"
                    "保留未被反馈要求修改的内容、ID、结构和数值。"
                    "如果反馈中包含元素 ID，只修改该 ID 所属的章节、节点、选项、NPC 或结局内容块，"
                    "目标块之外的 Markdown 必须原样保留。只输出修改后的完整 Markdown。"
                ),
            ),
            ChatMessage(
                role="user",
                content=(
                    f"修订目标: {target}\n修订反馈: {feedback.strip()}\n\n"
                    f"## 约束上下文\n{context}\n\n"
                    f"## 当前目标文件\n{current_content}\n\n"
                    "直接输出修改后的完整目标文件，不要解释，不要代码块。"
                ),
            ),
        ]

    def _flash(self) -> QwenChatClient:
        if self._flash_client is None:
            load_dotenv(override=False)
            config = QwenConfig.from_env()
            self._flash_client = QwenChatClient(QwenConfig(
                api_key=config.api_key,
                base_url=config.base_url,
                model="qwen-flash",
                timeout_seconds=config.timeout_seconds,
            ))
        return self._flash_client

    def _resolve_base_dir(self, base_version: str) -> Path:
        raw = base_version.strip().strip("/")
        if not raw:
            raise ValueError("base_version 不能为空")
        path = (self._outputs_dir / raw).resolve()
        try:
            path.relative_to(self._outputs_dir)
        except ValueError as exc:
            raise ValueError("非法的版本路径") from exc
        if path.is_file():
            path = path.parent
        if not path.is_dir():
            raise ValueError(f"版本目录不存在: {base_version}")
        if not (path / "01_game_settings.md").exists():
            raise ValueError("该版本不是可修订的章节式版本")
        return path

    @classmethod
    def _target_filename(cls, target: str) -> str:
        if target in cls.TARGET_FILES:
            return cls.TARGET_FILES[target]
        if re.fullmatch(r"ch\d{2}", target):
            return f"03_{target}.md"
        raise ValueError("target 必须是 game_settings、chapter_outline 或 chNN")

    def _reserve_revision_dir(self, base_dir: Path) -> tuple[Path, str]:
        generation_dir = self._generation_dir(base_dir)
        root = generation_dir / "revisions"
        root.mkdir(parents=True, exist_ok=True)
        numbers = [
            self._revision_number(path.name)
            for path in root.iterdir()
            if path.is_dir() and re.fullmatch(r"r\d{2}", path.name)
        ]
        name = f"r{(max(numbers, default=0) + 1):02d}"
        path = root / name
        path.mkdir()
        return path, name

    def _generation_dir(self, base_dir: Path) -> Path:
        """Return the owning vNN directory for an original or revised version."""
        relative = base_dir.resolve().relative_to(self._outputs_dir)
        if not relative.parts or not re.fullmatch(r"v\d{2}", relative.parts[0]):
            raise ValueError("章节修订版本必须位于 vNN 目录中")
        generation_dir = self._outputs_dir / relative.parts[0]
        if not generation_dir.is_dir():
            raise ValueError("找不到修订版本所属的生成目录")
        return generation_dir

    @staticmethod
    def _revision_number(value: str) -> int:
        match = re.fullmatch(r"r(\d{2})", value)
        return int(match.group(1)) if match else 0

    @staticmethod
    def _copy_revision_inputs(base_dir: Path, revision_dir: Path) -> None:
        filenames = [
            "01_game_settings.md",
            "02_chapter_outline.md",
            "06a_global.json",
        ]
        filenames.extend(path.name for path in sorted(base_dir.glob("03_ch[0-9][0-9].md")))
        filenames.extend(path.name for path in sorted(base_dir.glob("06b_ch[0-9][0-9].json")))
        for filename in filenames:
            source = base_dir / filename
            if source.exists():
                shutil.copy2(source, revision_dir / filename)

    def _preview_payload(
        self,
        source: dict[str, Any],
        revised: str,
        mode: str,
    ) -> dict[str, Any]:
        diff = "\n".join(difflib.unified_diff(
            source["content"].splitlines(),
            revised.splitlines(),
            fromfile=f"{source['target']}:before",
            tofile=f"{source['target']}:after",
            lineterm="",
        ))
        return {
            **source,
            "mode": mode,
            "revised_content": revised,
            "diff": diff,
            "changed": source["content"] != revised,
        }

    def _load_latest_result(self, base_dir: Path) -> dict[str, Any]:
        candidates = sorted(
            list(base_dir.glob("script_generate_*.json"))
            + list(base_dir.glob("script_revise_*.json")),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            return {}
        try:
            return json.loads(candidates[0].read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _relative_ref(self, path: Path) -> str:
        return str(path.resolve().relative_to(self._outputs_dir))

    @staticmethod
    def _clean_markdown(value: str) -> str:
        cleaned = value.strip()
        if cleaned.startswith("```") and cleaned.endswith("```"):
            cleaned = cleaned[3:-3].strip()
            if cleaned.lower().startswith("markdown"):
                cleaned = cleaned[8:].strip()
            elif cleaned.lower().startswith("md"):
                cleaned = cleaned[2:].strip()
        return cleaned + "\n"

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _jsonable(value: Any) -> Any:
        if is_dataclass(value):
            return {key: ChapterRevisionService._jsonable(item) for key, item in asdict(value).items()}
        if isinstance(value, dict):
            return {str(key): ChapterRevisionService._jsonable(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [ChapterRevisionService._jsonable(item) for item in value]
        if isinstance(value, set):
            return sorted(ChapterRevisionService._jsonable(item) for item in value)
        return value
