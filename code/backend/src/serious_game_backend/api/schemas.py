from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from serious_game_backend.domain.action import ActionCommand
from serious_game_backend.domain.enums import ActionInputMode


class StartSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    client_request_id: str = Field(min_length=8, max_length=128)
    package_id: str | None = Field(default=None, min_length=1, max_length=128)
    origin_id: str = Field(min_length=1, max_length=64)


class ActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    input_mode: ActionInputMode
    client_action_id: str = Field(min_length=8, max_length=128)
    state_version: int = Field(ge=1)
    action_id: str | None = None
    opportunity_id: str | None = None
    player_text: str | None = Field(default=None, max_length=4000)
    target_npc_id: str | None = None
    decision_id: str | None = None
    option_id: str | None = None
    ordered_option_ids: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    retry: bool = False

    @model_validator(mode="after")
    def validate_union(self) -> "ActionRequest":
        if self.input_mode is ActionInputMode.TOOL:
            if not self.action_id or not self.opportunity_id:
                raise ValueError("tool 模式必须提供 opportunity_id 和 action_id")
            if self.decision_id or self.option_id:
                raise ValueError("tool 模式不能提供 decision_id/option_id")
        elif self.input_mode is ActionInputMode.FREE_TEXT:
            if (
                not self.opportunity_id
                or not self.target_npc_id
                or not (self.player_text or "").strip()
            ):
                raise ValueError("free_text 模式必须提供 opportunity_id、target_npc_id 和 player_text")
            if self.decision_id or self.option_id:
                raise ValueError("free_text 模式不能提供 decision_id/option_id")
            if self.action_id:
                raise ValueError("free_text 模式不能提供 action_id")
        elif self.input_mode is ActionInputMode.DECISION:
            if not self.decision_id or not (
                self.option_id or self.ordered_option_ids or self.parameters
            ):
                raise ValueError(
                    "decision 模式必须提供 decision_id，以及 option_id、ordered_option_ids 或 parameters"
                )
            if self.action_id or self.opportunity_id or self.player_text or self.target_npc_id:
                raise ValueError("decision 模式不能提供工具或自由文本字段")
            if len(self.ordered_option_ids) != len(set(self.ordered_option_ids)):
                raise ValueError("ordered_option_ids 不能包含重复项")
        return self

    def to_command(self) -> ActionCommand:
        return ActionCommand(
            input_mode=self.input_mode,
            client_action_id=self.client_action_id,
            state_version=self.state_version,
            action_id=self.action_id,
            opportunity_id=self.opportunity_id,
            player_text=self.player_text,
            target_npc_id=self.target_npc_id,
            decision_id=self.decision_id,
            option_id=self.option_id,
            ordered_option_ids=tuple(self.ordered_option_ids),
            parameters=self.parameters,
            retry=self.retry,
        )


class EndDayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    client_action_id: str = Field(min_length=8, max_length=128)
    state_version: int = Field(ge=1)
    active_rest: bool = False
