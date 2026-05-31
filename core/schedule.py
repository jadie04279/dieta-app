"""
Goal-line builder and trajectory management.

The goal schedule is a week-by-week weight target from start to goal.
Instead of a simple linear ramp, it uses a weight-proportional cap curve:
each week's loss is at most min(current_weight × 1%, 1.0 kg).
This creates a natural deceleration: faster early, gentler near the goal.

Two modes:
  rate_safe      – follow safe-speed curve; project the actual arrival date
  deadline_fixed – distribute loss across the user's deadline; flag weeks
                   that exceed the safe cap (but still enforce calorie floor / BMI gate)
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from core.safety import speed_cap, check_bmi_gate, min_weight_for_bmi


# ── Internal simulation helpers ─────────────────────────────────────────────

def _simulate_safe(start_weight: float, goal_weight: float, start_date: date) -> list[dict]:
    """
    Simulate week-by-week loss at the weight-proportional safe cap.
    Returns weekly_targets_json entries.
    """
    targets: list[dict] = []
    current = start_weight
    current_date = start_date

    for _ in range(520):  # hard safety limit (~10 years)
        if current <= goal_weight + 0.005:
            break
        cap = speed_cap(current)
        loss = min(cap, current - goal_weight)
        next_weight = current - loss
        targets.append({
            "week_start": str(current_date),
            "target_weight_kg": round(next_weight, 2),
            "planned_loss_kg": round(loss, 3),
            "exceeds_safe_cap": False,
        })
        current = next_weight
        current_date += timedelta(weeks=1)

    return targets


def _simulate_fixed(
    start_weight: float,
    goal_weight: float,
    start_date: date,
    total_weeks: int,
) -> list[dict]:
    """
    Distribute total loss evenly across total_weeks.
    Marks weeks where the uniform loss exceeds the per-week safe cap.
    """
    total_loss = start_weight - goal_weight
    if total_weeks <= 0 or total_loss <= 0:
        return []

    weekly_loss = total_loss / total_weeks
    targets: list[dict] = []
    current = start_weight
    current_date = start_date

    for _ in range(total_weeks):
        if current <= goal_weight + 0.005:
            break
        actual_loss = min(weekly_loss, current - goal_weight)
        cap = speed_cap(current)
        next_weight = max(goal_weight, current - actual_loss)
        targets.append({
            "week_start": str(current_date),
            "target_weight_kg": round(next_weight, 2),
            "planned_loss_kg": round(actual_loss, 3),
            "exceeds_safe_cap": actual_loss > cap + 1e-6,
        })
        current = next_weight
        current_date += timedelta(weeks=1)

    return targets


# ── Public API ──────────────────────────────────────────────────────────────

def build_goal_line(
    start_weight: float,
    start_date: date | str,
    goal_weight: float,
    target_date: date | str,
    sex: str,
    height_cm: float,
    mode: str = "rate_safe",
) -> dict:
    """
    Build the full goal schedule from start to goal.

    Returns a dict ready for save_goal_schedule() plus a 'flags' list for the UI:
      mode, start_weight_kg, start_date, goal_weight_kg, target_date,
      projected_date, feasible, weekly_targets_json, flags
    """
    if isinstance(start_date, str):
        start_date = date.fromisoformat(start_date)
    if isinstance(target_date, str):
        target_date = date.fromisoformat(target_date)

    # BMI gate: clamp goal if it would result in underweight
    gate = check_bmi_gate(start_weight, height_cm, goal_weight)
    flags: list[dict] = list(gate["flags"])
    clamped_goal = gate["clamped_goal_weight"] if gate["clamped_goal_weight"] is not None else goal_weight

    # Safe-speed simulation (determines feasibility + rate_safe trajectory)
    safe_targets = _simulate_safe(start_weight, clamped_goal, start_date)

    # Projected arrival date (rate_safe speed)
    if safe_targets:
        last_week_start = date.fromisoformat(safe_targets[-1]["week_start"])
        projected_date = last_week_start + timedelta(weeks=1)
    else:
        projected_date = start_date  # already at goal

    available_weeks = (target_date - start_date).days / 7.0
    needed_weeks = len(safe_targets)
    feasible = 1 if needed_weeks <= available_weeks + 0.5 else 0

    if mode == "rate_safe":
        weekly_targets = safe_targets
        effective_projected = projected_date
    else:  # deadline_fixed
        total_weeks = max(1, round(available_weeks))
        weekly_targets = _simulate_fixed(start_weight, clamped_goal, start_date, total_weeks)
        effective_projected = target_date

        if feasible == 0:
            flags.append({
                "code": "deadline_aggressive",
                "message": (
                    f"목표 기간({total_weeks}주) 내 안전 속도 달성이 어렵습니다. "
                    f"안전 속도로는 {projected_date.strftime('%Y년 %m월 %d일')}에 도달 예상입니다. "
                    "초과 주차는 빨간색으로 표시됩니다."
                ),
                "severity": "warning",
            })

    return {
        "mode": mode,
        "start_weight_kg": start_weight,
        "start_date": str(start_date),
        "goal_weight_kg": round(clamped_goal, 2),
        "target_date": str(target_date),
        "projected_date": str(effective_projected),
        "feasible": feasible,
        "weekly_targets_json": weekly_targets,
        "flags": flags,
        # Extra context for reproject (not stored in DB)
        "_sex": sex,
        "_height_cm": height_cm,
    }


def daily_target(goal_line: dict, query_date: date | str) -> dict:
    """
    Linearly interpolate between weekly checkpoints to get the expected weight
    and day-over-day target delta for any given date.

    Returns:
        target_weight – interpolated goal weight (kg)
        target_delta  – expected change from prior day (kg, negative = loss)
    """
    if isinstance(query_date, str):
        query_date = date.fromisoformat(query_date)

    weekly = goal_line.get("weekly_targets_json", [])
    start_weight = goal_line["start_weight_kg"]
    start_date = date.fromisoformat(goal_line["start_date"])
    goal_weight = goal_line["goal_weight_kg"]

    if not weekly:
        return {"target_weight": round(goal_weight, 2), "target_delta": 0.0}

    # Build interpolation table: (date, weight) pairs
    checkpoints: list[tuple[date, float]] = [(start_date, start_weight)]
    for entry in weekly:
        # Each entry covers the week starting at week_start; weight target is at week end
        week_end = date.fromisoformat(entry["week_start"]) + timedelta(weeks=1)
        checkpoints.append((week_end, entry["target_weight_kg"]))

    # Find the surrounding pair
    for i in range(len(checkpoints) - 1):
        d0, w0 = checkpoints[i]
        d1, w1 = checkpoints[i + 1]
        if d0 <= query_date <= d1:
            span = (d1 - d0).days
            if span == 0:
                interp = w1
            else:
                frac = (query_date - d0).days / span
                interp = w0 + frac * (w1 - w0)
            # Delta vs previous day (uniform daily rate within the week)
            daily_rate = (w1 - w0) / span if span > 0 else 0.0
            return {
                "target_weight": round(interp, 2),
                "target_delta": round(daily_rate, 4),
            }

    # Past the last checkpoint: at goal
    return {"target_weight": round(goal_weight, 2), "target_delta": 0.0}


def reproject(
    goal_line: dict,
    current_trend_weight: float,
    sex: str,
    height_cm: float,
) -> dict:
    """
    Rebuild the goal line from today's actual trend weight.
    Used weekly after adaptive TDEE estimation to keep the trajectory current.

    If current_trend_weight is ahead of plan → slightly gentler pace.
    If behind → safe cap applied, projected_date pushed if needed.
    """
    today = date.today()
    return build_goal_line(
        start_weight=current_trend_weight,
        start_date=today,
        goal_weight=goal_line["goal_weight_kg"],
        target_date=goal_line["target_date"],
        sex=sex,
        height_cm=height_cm,
        mode=goal_line.get("mode", "rate_safe"),
    )


def weeks_to_goal(goal_line: dict) -> int:
    """Number of weeks remaining in the current trajectory."""
    return len(goal_line.get("weekly_targets_json", []))


def unsafe_weeks(goal_line: dict) -> list[dict]:
    """Return weekly targets where planned loss exceeds safe cap (deadline_fixed mode)."""
    return [
        e for e in goal_line.get("weekly_targets_json", [])
        if e.get("exceeds_safe_cap", False)
    ]
