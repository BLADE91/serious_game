from __future__ import annotations

import re

from serious_game_backend.domain.errors import ContentValidationError


_HIDDEN_METRIC_DELTA = re.compile(
    r"(?:政治资本|政治信用|群众信任|社会稳定|舆论压力|班子不满|"
    r"信任(?:值|分)?|焦虑(?:值|分)?|态度(?:值|分)?)"
    r"\s*(?:[：:]\s*)?(?:[+\-±]\s*\d|"
    r"\d+\s*(?:到|至|[-~—])\s*[+\-]?\d+)"
)
_ADJACENT_PUNCTUATION = re.compile(r"[，。；：！？]{2,}")
_TERMINAL = ("。", "！", "？", "…")
_SOFT_TERMINAL = ("，", "；", "：", ",", ";", ":")


def validate_player_visible_text(text: str) -> None:
    if _HIDDEN_METRIC_DELTA.search(text):
        raise ContentValidationError("玩家可见文本不得暴露隐藏指标的精确数值变化")
    if _ADJACENT_PUNCTUATION.search(text):
        raise ContentValidationError("玩家可见文本包含紧邻的重复标点")


def player_visible_sentence(text: str) -> str:
    value = str(text).strip()
    validate_player_visible_text(value)
    if not value:
        return value
    if value.endswith(_SOFT_TERMINAL):
        value = value[:-1].rstrip() + "。"
    elif not value.endswith(_TERMINAL):
        value += "。"
    validate_player_visible_text(value)
    return value
