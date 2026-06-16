"""NPC 之间的关系网络。"""

from dataclasses import dataclass


# 预定义关系类型
RELATION_TYPES = (
    "亲属",      # 血缘或姻亲关系
    "上下级",    # 组织内的汇报/指令关系
    "利益同盟",  # 基于共同利益的合作关系
    "矛盾对立",  # 存在利益冲突或历史恩怨
    "信息渠道",  # 消息传递的依赖关系
    "情感纽带",  # 友情、恩情等非利益关系
)


@dataclass(frozen=True)
class NPCRelationship:
    """NPC 之间的有向关系边。

    关系是有向的（A 是 B 的上级 ≠ B 是 A 的上级），
    但可以同时存在反向边来表达双向关系。
    """

    from_npc_id: str
    to_npc_id: str
    relation_type: str  # 取值见 RELATION_TYPES
    strength: int = 50  # 关系强度 0-100
    description: str = ""  # 一句话描述

    def __post_init__(self) -> None:
        if not self.from_npc_id.strip():
            raise ValueError("NPCRelationship.from_npc_id must not be empty")
        if not self.to_npc_id.strip():
            raise ValueError("NPCRelationship.to_npc_id must not be empty")
        if self.from_npc_id == self.to_npc_id:
            raise ValueError("NPCRelationship cannot connect an NPC to itself")
        if self.relation_type not in RELATION_TYPES:
            raise ValueError(
                f"NPCRelationship.relation_type must be one of {RELATION_TYPES}"
            )
        if self.strength < 0 or self.strength > 100:
            raise ValueError("NPCRelationship.strength must be 0-100")
