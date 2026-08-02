from __future__ import annotations

import re
import secrets

from serious_game_backend.application.ports import NPCMemoryRepository
from serious_game_backend.domain.llm_runtime import NPCMemory, runtime_now_iso


INSTRUCTION_MARKERS = (
    "忽略以上", "忽略系统", "system prompt", "developer message",
    "你现在必须", "flag_", "state_version", "结局轴",
)


class NPCMemoryService:
    """NPC 私有情节记忆；只保存短事实，不保存可执行指令。"""

    def __init__(
        self,
        repository: NPCMemoryRepository,
        *,
        retrieval_limit: int = 6,
        compression_threshold: int = 9,
        ttl_days: int = 30,
    ) -> None:
        self._repository = repository
        self._retrieval_limit = retrieval_limit
        self._compression_threshold = compression_threshold
        self._ttl_days = ttl_days

    def retrieve(
        self,
        *,
        session_id: str,
        npc_id: str,
        story_day: int,
        query: str,
    ) -> tuple[str, ...]:
        memories = self._repository.active_for_npc(session_id, npc_id, story_day)
        terms = set(self._keywords(query))
        ranked = sorted(
            memories,
            key=lambda item: (
                bool(terms & set(item.keywords)),
                item.valid_from_day,
                item.created_at,
            ),
            reverse=True,
        )
        return tuple(item.content for item in ranked[: self._retrieval_limit])

    def record(
        self,
        *,
        session_id: str,
        account_id: str,
        npc_id: str,
        operation_id: str,
        story_day: int,
        candidate: str | None,
    ) -> NPCMemory | None:
        content = self._sanitize(candidate)
        if not content:
            return None
        memory = NPCMemory(
            memory_id=f"mem_{secrets.token_hex(12)}",
            session_id=session_id,
            account_id=account_id,
            npc_id=npc_id,
            source_operation_id=operation_id,
            content=content,
            memory_type="episode",
            keywords=self._keywords(content),
            valid_from_day=story_day,
            expires_after_day=min(90, story_day + self._ttl_days),
        )
        self._repository.save(memory)
        self._compress_if_needed(memory)
        return memory

    def invalidate(self, memory_ids: tuple[str, ...]) -> None:
        self._repository.invalidate(memory_ids, runtime_now_iso())

    def _compress_if_needed(self, newest: NPCMemory) -> None:
        active = self._repository.active_for_npc(
            newest.session_id, newest.npc_id, newest.valid_from_day
        )
        if len(active) < self._compression_threshold:
            return
        sources = tuple(active[:6])
        summary_text = "；".join(item.content.rstrip("。；") for item in sources)
        summary_text = self._sanitize(summary_text[:480])
        if not summary_text:
            return
        summary = NPCMemory(
            memory_id=f"mem_{secrets.token_hex(12)}",
            session_id=newest.session_id,
            account_id=newest.account_id,
            npc_id=newest.npc_id,
            source_operation_id=newest.source_operation_id,
            content=summary_text,
            memory_type="summary",
            keywords=self._keywords(summary_text),
            valid_from_day=newest.valid_from_day,
            expires_after_day=min(90, newest.valid_from_day + self._ttl_days),
        )
        self._repository.save(summary)
        self._repository.invalidate(
            tuple(item.memory_id for item in sources), runtime_now_iso()
        )

    @staticmethod
    def _sanitize(candidate: str | None) -> str:
        if candidate is None:
            return ""
        value = re.sub(r"\s+", " ", candidate).strip()[:500]
        lowered = value.lower()
        if any(marker.lower() in lowered for marker in INSTRUCTION_MARKERS):
            return ""
        return value

    @staticmethod
    def _keywords(text: str) -> tuple[str, ...]:
        values = re.findall(r"[\u4e00-\u9fff]{2,6}|[A-Za-z0-9]{3,}", text)
        return tuple(dict.fromkeys(item.lower() for item in values))[:20]
