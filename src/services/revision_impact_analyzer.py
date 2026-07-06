"""Deterministic impact analysis for global chapter-script revisions."""

from __future__ import annotations

import re
from typing import Any


class RevisionImpactAnalyzer:
    """Find chapter Markdown files that may be invalidated by an upstream edit."""

    NPC_ID_PATTERN = re.compile(
        r"\bnpc_(?!id\b)[A-Za-z0-9_-]+\b",
        re.IGNORECASE,
    )
    CHAPTER_ID_PATTERN = re.compile(r"\bch\d{2}\b", re.IGNORECASE)
    NPC_BLOCK_PATTERN = re.compile(
        r"(?ms)^###\s+NPC\b.*?(?=^###\s+NPC\b|^##\s+|\Z)",
    )
    LEVEL2_SECTION_PATTERN = re.compile(
        r"(?ms)^##\s+([^\n]+)\n(.*?)(?=^##\s+|\Z)",
    )

    def analyze(
        self,
        target: str,
        original_content: str,
        revised_content: str,
        chapters: dict[str, str],
    ) -> dict[str, Any]:
        chapter_ids = sorted(chapters, key=self._chapter_number)
        if original_content == revised_content or re.fullmatch(r"ch\d{2}", target):
            return self._report(target, "low", {}, [], [])
        if target == "game_settings":
            return self._analyze_settings(
                original_content, revised_content, chapters, chapter_ids,
            )
        if target == "chapter_outline":
            return self._analyze_outline(
                original_content, revised_content, chapters, chapter_ids,
            )
        raise ValueError(f"不支持的修订影响分析目标: {target}")

    def analyze_batch(
        self,
        changes: dict[str, tuple[str, str]],
        chapters: dict[str, str],
    ) -> dict[str, Any]:
        """Merge deterministic impact reports for several source edits."""
        reports = [
            self.analyze(target, original, revised, chapters)
            for target, (original, revised) in changes.items()
        ]
        reasons: dict[str, list[str]] = {}
        structural_changes: list[str] = []
        blocking_changes: list[str] = []
        level_rank = {"low": 0, "medium": 1, "high": 2}
        level = "low"

        for report in reports:
            if level_rank.get(report.get("impact_level", "low"), 0) > level_rank[level]:
                level = report["impact_level"]
            target = report.get("target", "")
            for item in report.get("affected_chapters", []):
                chapter_id = item.get("chapter_id")
                if not chapter_id:
                    continue
                for reason in item.get("reasons", []):
                    self._add_reason(reasons, chapter_id, f"{target}: {reason}")
            for value in report.get("structural_changes", []):
                labeled = f"{target}: {value}"
                if labeled not in structural_changes:
                    structural_changes.append(labeled)
            for value in report.get("blocking_changes", []):
                labeled = f"{target}: {value}"
                if labeled not in blocking_changes:
                    blocking_changes.append(labeled)

        return {
            "target": "batch",
            "changed_targets": sorted(changes),
            "impact_level": level,
            "affected_chapters": [
                {"chapter_id": chapter_id, "reasons": chapter_reasons}
                for chapter_id, chapter_reasons in sorted(reasons.items())
            ],
            "structural_changes": structural_changes,
            "blocking_changes": blocking_changes,
            "requires_confirmation": False,
        }

    def conservative_external_impact(
        self,
        chapters: dict[str, str],
    ) -> dict[str, Any]:
        """Fallback for externally edited legacy versions without a baseline."""
        return {
            "target": "batch",
            "changed_targets": ["game_settings", "chapter_outline", *sorted(chapters)],
            "impact_level": "high",
            "affected_chapters": [
                {
                    "chapter_id": chapter_id,
                    "reasons": ["缺少修改前基线，按外部全量修订保守同步"],
                }
                for chapter_id in sorted(chapters, key=self._chapter_number)
            ],
            "structural_changes": ["缺少源 Markdown 基线，无法精确识别外部变化"],
            "blocking_changes": [],
            "requires_confirmation": False,
            "baseline_available": False,
        }

    def _analyze_settings(
        self,
        original: str,
        revised: str,
        chapters: dict[str, str],
        chapter_ids: list[str],
    ) -> dict[str, Any]:
        reasons: dict[str, list[str]] = {}
        structural_changes: list[str] = []
        original_blocks = self._npc_blocks(original)
        revised_blocks = self._npc_blocks(revised)
        changed_npcs = sorted(
            npc_id
            for npc_id in set(original_blocks) | set(revised_blocks)
            if self._normalize(original_blocks.get(npc_id, ""))
            != self._normalize(revised_blocks.get(npc_id, ""))
        )

        removed_or_added = sorted(set(original_blocks) ^ set(revised_blocks))
        if removed_or_added:
            structural_changes.append(
                f"NPC 集合变化: {', '.join(removed_or_added)}"
            )

        for npc_id in changed_npcs:
            for chapter_id, content in chapters.items():
                if re.search(rf"\b{re.escape(npc_id)}\b", content, re.IGNORECASE):
                    self._add_reason(
                        reasons,
                        chapter_id,
                        f"引用了已修改的 NPC {npc_id}",
                    )

        original_without_npcs = self.NPC_BLOCK_PATTERN.sub("", original)
        revised_without_npcs = self.NPC_BLOCK_PATTERN.sub("", revised)
        if self._normalize(original_without_npcs) != self._normalize(revised_without_npcs):
            structural_changes.append("全局机制、背景、结局或制作约束发生变化")
            for chapter_id in chapter_ids:
                self._add_reason(reasons, chapter_id, "依赖已修改的全局设定")

        level = "high" if structural_changes else "medium" if reasons else "low"
        return self._report("game_settings", level, reasons, structural_changes, [])

    def _analyze_outline(
        self,
        original: str,
        revised: str,
        chapters: dict[str, str],
        chapter_ids: list[str],
    ) -> dict[str, Any]:
        reasons: dict[str, list[str]] = {}
        structural_changes: list[str] = []
        blocking_changes: list[str] = []
        original_sections, original_other = self._outline_sections(original)
        revised_sections, revised_other = self._outline_sections(revised)
        original_order = list(original_sections)
        revised_order = list(revised_sections)

        if original_order != revised_order:
            change = (
                "章节数量或顺序变化: "
                f"{original_order or ['无']} -> {revised_order or ['无']}"
            )
            structural_changes.append(change)
            blocking_changes.append(change)
            for chapter_id in chapter_ids:
                self._add_reason(reasons, chapter_id, "章节数量或顺序发生变化")

        changed = [
            chapter_id
            for chapter_id in set(original_sections) | set(revised_sections)
            if self._normalize(original_sections.get(chapter_id, ""))
            != self._normalize(revised_sections.get(chapter_id, ""))
        ]
        for changed_id in sorted(changed, key=self._chapter_number):
            start = self._chapter_number(changed_id)
            for chapter_id in chapter_ids:
                if self._chapter_number(chapter_id) >= start:
                    self._add_reason(
                        reasons,
                        chapter_id,
                        f"{changed_id} 大纲变化可能影响本章状态承接",
                    )

        if self._normalize(original_other) != self._normalize(revised_other):
            structural_changes.append("结局可达性、Flag 规划或跨章追踪表发生变化")
            for chapter_id in chapter_ids:
                self._add_reason(reasons, chapter_id, "依赖已修改的跨章规划")

        return self._report(
            "chapter_outline",
            "high" if reasons else "low",
            reasons,
            structural_changes,
            blocking_changes,
        )

    def _outline_sections(self, content: str) -> tuple[dict[str, str], str]:
        sections: dict[str, str] = {}
        other_parts: list[str] = []
        cursor = 0
        for match in self.LEVEL2_SECTION_PATTERN.finditer(content):
            other_parts.append(content[cursor:match.start()])
            heading = match.group(1).strip()
            block = match.group(0)
            if re.match(r"第\s*\d+\s*章", heading):
                chapter_match = self.CHAPTER_ID_PATTERN.search(block)
                if chapter_match:
                    sections[chapter_match.group(0).lower()] = block
                else:
                    number_match = re.search(r"第\s*(\d+)\s*章", heading)
                    if number_match:
                        sections[f"ch{int(number_match.group(1)):02d}"] = block
            else:
                other_parts.append(block)
            cursor = match.end()
        other_parts.append(content[cursor:])
        return sections, "\n".join(other_parts)

    def _npc_blocks(self, content: str) -> dict[str, str]:
        blocks: dict[str, str] = {}
        for match in self.NPC_BLOCK_PATTERN.finditer(content):
            block = match.group(0)
            npc_match = self.NPC_ID_PATTERN.search(block)
            if npc_match:
                blocks[npc_match.group(0).lower()] = block
        return blocks

    @staticmethod
    def _normalize(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _chapter_number(value: str) -> int:
        match = re.search(r"(\d+)$", value)
        return int(match.group(1)) if match else 0

    @staticmethod
    def _add_reason(reasons: dict[str, list[str]], chapter_id: str, reason: str) -> None:
        items = reasons.setdefault(chapter_id, [])
        if reason not in items:
            items.append(reason)

    @staticmethod
    def _report(
        target: str,
        level: str,
        reasons: dict[str, list[str]],
        structural_changes: list[str],
        blocking_changes: list[str],
    ) -> dict[str, Any]:
        affected = [
            {"chapter_id": chapter_id, "reasons": chapter_reasons}
            for chapter_id, chapter_reasons in sorted(reasons.items())
        ]
        return {
            "target": target,
            "impact_level": level,
            "affected_chapters": affected,
            "structural_changes": structural_changes,
            "blocking_changes": blocking_changes,
            "requires_confirmation": bool(affected or structural_changes),
        }
