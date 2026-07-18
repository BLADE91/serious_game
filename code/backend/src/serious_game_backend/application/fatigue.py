from __future__ import annotations


def settle_fatigue(
    *,
    current: int,
    points_spent: int,
    overtime_used: bool,
    overtime_points: int,
    active_rest: bool,
    chapter_transition: bool,
) -> int:
    """按最终剧本第 9.1 节进行确定性日终疲惫结算。"""

    if active_rest:
        result = current - 15
    else:
        increase = 0
        if overtime_used:
            increase += 5 + 8 * overtime_points
        if points_spent >= 8:
            increase += 3
        elif points_spent >= 6:
            increase += 1
        increase = min(increase, 32)
        result = current + increase
        if points_spent <= 3 and not overtime_used:
            result -= 5
    if chapter_transition:
        result -= 10
    return max(0, min(100, result))


def action_point_cap_for(fatigue: int, consecutive_full_load_days: int) -> int:
    if fatigue >= 75:
        cap = 5
    elif fatigue >= 50:
        cap = 6
    elif fatigue >= 25:
        cap = 7
    else:
        cap = 8
    if consecutive_full_load_days >= 3:
        cap = min(cap, 6)
    return cap
