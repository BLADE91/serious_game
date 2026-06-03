"""用于支撑 NPC Agent 生成的资料上下文。"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SourceContext:
    """一段可用于 Agent 生成的检索资料。"""

    id: str
    title: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("SourceContext.id must not be empty")
        if not self.title.strip():
            raise ValueError("SourceContext.title must not be empty")
        if not self.content.strip():
            raise ValueError("SourceContext.content must not be empty")
