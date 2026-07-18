from __future__ import annotations

from serious_game_backend.domain.llm import RoleTurnContext, RoleTurnResult


class FakeRoleLLMGateway:
    """确定性契约替身；只为垂直切片生成可重复的受限角色回合。"""

    def run_turn(self, context: RoleTurnContext) -> RoleTurnResult:
        if context.npc_id == "npc_wu_xiuying":
            forceful = any(
                phrase in context.player_text
                for phrase in ("必须配合", "不识抬举", "命令你", "马上签")
            )
            if forceful:
                return RoleTurnResult(
                    npc_id=context.npc_id,
                    dialogue=(
                        "县长要是只想听一句服从，那这村里的真话，"
                        "恐怕还是没人敢说。"
                    ),
                    portrait_state="guarded",
                    attitude_direction="decrease",
                    attitude_band="micro",
                    anxiety_direction="increase",
                    anxiety_band="light",
                    memory_candidate="新县长第一次交谈时更看重服从。",
                )
            return RoleTurnResult(
                npc_id=context.npc_id,
                dialogue=(
                    "周家、何家、杨家，面上一团和气，底下各有各的算盘。"
                    "县长要在这村里办事，先得看明白，谁的话在谁面前好使。"
                    "县长，这村里的水看着浅，趟下去才知道深浅。您慢慢看。"
                ),
                portrait_state="warm",
                attitude_direction="increase",
                attitude_band="micro",
                anxiety_direction="decrease",
                anxiety_band="light",
                disclosure_id="fact_clan_power_map",
                memory_candidate="新县长愿意先听她讲村里的真实关系。",
            )
        return RoleTurnResult(
            npc_id=context.npc_id,
            dialogue="对方沉默片刻，只说这件事还要再想一想。",
            portrait_state="neutral",
        )
