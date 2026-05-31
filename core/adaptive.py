"""
Adaptive TDEE estimator: closed-loop feedback engine.

Each week, we reverse-engineer the user's TRUE metabolic rate from observed
intake and weight change, then re-calibrate the next week's targets.

Key formula (energy balance rearranged):
    estimated_TDEE = (Σintake_kcal − ΔW_trend × 7700) / N

Where:
    ΔW_trend  = EWMA trend weight at end − EWMA trend weight at start (kg)
                Negative value = net fat burned.
    7700      = kcal per kg body fat (accepted approximation)
    N         = number of valid logged days in the window

EWMA smoothing (α ≈ 0.1) removes day-to-day scale noise (water, gut content)
that would otherwise dominate the small ΔW signal.

References:
    - Hall KD et al. (2012). Quantification of the effect of energy imbalance
      on bodyweight. Lancet 378(9793):826-837.
    - Thomas DM et al. (2014). Time to correctly predict the amount of weight
      loss with dieting. JADA 114(6):857-861.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

ALPHA = 0.1          # EWMA smoothing factor; lower = smoother, more lag
WINDOW_DAYS = 14     # sliding window for TDEE estimation
MIN_VALID_DAYS = 7   # minimum logged days to attempt estimation


# ── EWMA ────────────────────────────────────────────────────────────────────

def ewma(series: list[float], alpha: float = ALPHA) -> list[float]:
    """
    Exponentially Weighted Moving Average on a list of values.
    First output equals the first input (no look-back required).
    """
    if not series:
        return []
    result = [series[0]]
    for val in series[1:]:
        result.append(alpha * val + (1.0 - alpha) * result[-1])
    return result


def ewma_trend_weights(
    logs: list[dict],
    alpha: float = ALPHA,
) -> list[dict]:
    """
    Given a list of daily_log dicts (must include 'date' and 'weight_kg'),
    return the same list enriched with 'trend_weight' (EWMA).

    Days without weight are carried forward from the previous trend value
    (they do not reset the EWMA but are not included in TDEE estimation).
    """
    if not logs:
        return []

    sorted_logs = sorted(logs, key=lambda l: l["date"])
    result = []
    prev_trend: Optional[float] = None

    for log in sorted_logs:
        actual = log.get("weight_kg")
        if actual is not None and actual > 0:
            if prev_trend is None:
                prev_trend = actual  # bootstrap
            trend = alpha * actual + (1.0 - alpha) * prev_trend
        else:
            # Missing weight: carry previous trend (no new info)
            trend = prev_trend

        prev_trend = trend
        result.append({**log, "trend_weight": round(trend, 4) if trend is not None else None})

    return result


# ── TDEE estimation ──────────────────────────────────────────────────────────

def estimate_tdee(
    logs: list[dict],
    alpha: float = ALPHA,
    window: int = WINDOW_DAYS,
) -> dict:
    """
    Estimate TDEE from a window of daily logs.

    Returns a result dict:
        enough_data      – bool: False if fewer than MIN_VALID_DAYS valid entries
        estimated_tdee   – float (kcal/day) if enough_data
        n_days           – number of valid days used
        trend_start      – EWMA weight at window start
        trend_end        – EWMA weight at window end
        delta_trend_kg   – trend_end - trend_start (negative = loss)
        total_intake     – sum of intake_kcal across valid days
        stored_energy_delta – ΔW_trend × 7700 (negative = energy released)
        reason           – explanation if not enough_data
    """
    # Use only logs within the window, sorted chronologically
    sorted_logs = sorted(logs, key=lambda l: l["date"])
    if window and len(sorted_logs) > window:
        sorted_logs = sorted_logs[-window:]

    # Valid = has both weight and intake
    valid = [l for l in sorted_logs if (l.get("weight_kg") or 0) > 0 and (l.get("intake_kcal") or 0) > 0]

    if len(valid) < MIN_VALID_DAYS:
        return {
            "enough_data": False,
            "reason": f"유효 기록 {len(valid)}일 (최소 {MIN_VALID_DAYS}일 필요)",
            "n_days": len(valid),
        }

    # Compute EWMA on the valid subset only (avoids gap carry-forward skewing ΔW)
    weight_series = [l["weight_kg"] for l in valid]
    trend_series = ewma(weight_series, alpha)

    trend_start = trend_series[0]
    trend_end = trend_series[-1]
    delta_trend = trend_end - trend_start         # kg (negative = loss)
    stored_delta = delta_trend * 7700.0           # kcal (negative = released)

    total_intake = sum(l["intake_kcal"] for l in valid)
    n = len(valid)

    # TDEE = (calories in − calories released from/stored in body) / days
    estimated_tdee = (total_intake - stored_delta) / n

    return {
        "enough_data": True,
        "n_days": n,
        "total_intake": round(total_intake, 1),
        "trend_start": round(trend_start, 3),
        "trend_end": round(trend_end, 3),
        "delta_trend_kg": round(delta_trend, 3),
        "stored_energy_delta": round(stored_delta, 1),
        "estimated_tdee": round(estimated_tdee, 1),
    }


def validate_tdee(
    estimated: float,
    bmr_val: float,
    prev_estimate: Optional[float] = None,
) -> tuple[float, list[str]]:
    """
    Sanity-check the estimated TDEE and return a (validated_tdee, notes) pair.

    Rules:
      - Below BMR: biologically implausible → blend toward BMR×1.1 or prev estimate
      - Above BMR×3.5: implausibly high → cap and blend
      - If prev_estimate exists: blend 70/30 (new/old) to prevent large jumps
    """
    notes: list[str] = []
    result = estimated
    lower_bound = bmr_val * 1.0   # true lower: can't sustain below BMR long-term
    upper_bound = bmr_val * 3.5   # extreme athlete ceiling

    if estimated < lower_bound:
        anchor = prev_estimate if (prev_estimate and prev_estimate >= lower_bound) else bmr_val * 1.1
        result = 0.5 * estimated + 0.5 * anchor
        notes.append(f"추정 TDEE({estimated:.0f})가 BMR({bmr_val:.0f})보다 낮아 보정했습니다.")

    elif estimated > upper_bound:
        result = upper_bound
        notes.append(f"추정 TDEE({estimated:.0f})가 상한선({upper_bound:.0f})을 초과해 보정했습니다.")

    # Blend with previous to prevent week-over-week jumps > ~200 kcal
    if prev_estimate and abs(result - prev_estimate) > 200:
        result = 0.7 * result + 0.3 * prev_estimate
        notes.append("주간 변동 완화를 위해 이전 추정값과 블렌딩했습니다.")

    return round(result, 1), notes


# ── Fallback TDEE ────────────────────────────────────────────────────────────

def fallback_tdee(profile: dict, current_weight: float) -> float:
    """
    When insufficient data exists, compute TDEE from BMR + activity factor.
    Used as a cold-start or data-gap fallback.
    """
    from core.energy import bmr, age_from_birth, tdee
    age = age_from_birth(profile["birth_date"])
    b = bmr(profile["sex"], current_weight, profile["height_cm"], age)
    return tdee(b, profile["activity_factor"])


# ── Next-week target computation ─────────────────────────────────────────────

def compute_next_week(
    profile: dict,
    current_trend_weight: float,
    est_tdee: float,
    goal_line: dict,
    health_check: Optional[dict] = None,
) -> dict:
    """
    Given the freshly estimated TDEE and current trend weight, compute
    next week's intake/exercise targets and reproject the goal line.

    Steps:
      1. Reproject goal line from current trend weight.
      2. Read this week's planned_loss from the new goal line.
      3. Apply safety guardrails (calorie floor, health flags).
      4. Return structured next-week plan.
    """
    from core.energy import bmr as compute_bmr, age_from_birth
    from core.safety import calorie_floor, apply_guardrails
    from core.schedule import reproject

    age = age_from_birth(profile["birth_date"])
    bmr_val = compute_bmr(profile["sex"], current_trend_weight, profile["height_cm"], age)

    # Reproject goal line from today's trend weight
    new_goal_line = reproject(
        goal_line,
        current_trend_weight,
        profile["sex"],
        profile["height_cm"],
    )

    # Planned weekly loss from the freshest goal line trajectory
    weekly_entries = new_goal_line.get("weekly_targets_json", [])
    planned_loss_kg = weekly_entries[0]["planned_loss_kg"] if weekly_entries else 0.0

    daily_deficit = planned_loss_kg * 7700.0 / 7.0
    floor = calorie_floor(profile["sex"], bmr_val)
    target_intake = max(floor, est_tdee - daily_deficit)

    # Remaining deficit after diet → push to exercise
    diet_deficit = est_tdee - target_intake
    leftover_deficit = max(0.0, daily_deficit - diet_deficit)
    target_exercise_kcal_week = leftover_deficit * 7.0

    # Build a raw_targets dict so apply_guardrails can re-verify
    raw = {
        "bmr": round(bmr_val, 1),
        "tdee": round(est_tdee, 1),
        "planned_loss_kg": round(planned_loss_kg, 3),
        "daily_deficit": round(daily_deficit, 1),
        "target_intake_kcal": round(target_intake, 1),
        "target_exercise_kcal": round(target_exercise_kcal_week, 1),
    }
    adjusted, flags = apply_guardrails(profile, current_trend_weight, raw, health_check)

    return {
        "est_tdee": round(est_tdee, 1),
        "bmr": round(bmr_val, 1),
        "planned_loss_kg": round(adjusted["planned_loss_kg"], 3),
        "target_intake_kcal": round(adjusted["target_intake_kcal"], 1),
        "target_exercise_kcal": round(adjusted["target_exercise_kcal"], 1),
        "goal_line": new_goal_line,
        "flags": flags,
    }


# ── Weekly adjustment report ─────────────────────────────────────────────────

def weekly_adjustment_report(
    prev_plan: dict,
    tdee_result: dict,
    next_plan: dict,
    prev_trend_weight: float,
    current_trend_weight: float,
) -> dict:
    """
    Summarise what changed between last week's plan and the new estimate.

    Returns a structured dict suitable for the Readjustment Report page (Phase 6).
    """
    actual_weekly_loss = prev_trend_weight - current_trend_weight

    prev_planned_loss = prev_plan.get("planned_loss_kg", 0.0)
    prev_intake = prev_plan.get("target_intake_kcal", 0.0)
    prev_tdee = prev_plan.get("est_tdee", 0.0)

    new_tdee = next_plan["est_tdee"]
    new_intake = next_plan["target_intake_kcal"]
    new_loss = next_plan["planned_loss_kg"]

    old_projected = prev_plan.get("goal_line", {}).get("projected_date", "—")
    new_projected = next_plan["goal_line"].get("projected_date", "—")

    return {
        "prev_planned_loss_kg": round(prev_planned_loss, 2),
        "actual_weekly_loss_kg": round(actual_weekly_loss, 2),
        "loss_delta_kg": round(actual_weekly_loss - prev_planned_loss, 2),
        "prev_est_tdee": round(prev_tdee, 0),
        "new_est_tdee": round(new_tdee, 0),
        "tdee_delta": round(new_tdee - prev_tdee, 0),
        "prev_target_intake": round(prev_intake, 0),
        "new_target_intake": round(new_intake, 0),
        "intake_delta": round(new_intake - prev_intake, 0),
        "new_planned_loss_kg": round(new_loss, 2),
        "prev_projected_date": old_projected,
        "new_projected_date": new_projected,
        "n_valid_days": tdee_result.get("n_days", 0),
        "flags": next_plan.get("flags", []),
    }


# ── Full weekly cycle (entry point) ─────────────────────────────────────────

def run_weekly_cycle(
    profile: dict,
    recent_logs: list[dict],
    prev_plan: dict,
    goal_line: dict,
    prev_tdee_estimate: Optional[float] = None,
    health_check: Optional[dict] = None,
) -> dict:
    """
    End-to-end weekly re-calibration.

    1. Estimate TDEE from recent logs (or fall back to BMR-based).
    2. Validate / blend with previous estimate.
    3. Compute next week's targets.
    4. Build the adjustment report.

    Returns:
        tdee_result   – raw estimation output
        validated_tdee – post-validation TDEE value
        validation_notes – list of any correction messages
        next_week     – compute_next_week() output
        report        – weekly_adjustment_report() output
        used_fallback  – True if insufficient data forced BMR fallback
    """
    # Get latest trend weight
    enriched = ewma_trend_weights(recent_logs)
    with_trend = [l for l in enriched if l.get("trend_weight") is not None]
    if not with_trend:
        # No weight data at all: use profile's last known weight as fallback
        current_trend = profile.get("goal_weight_kg", 65.0)
        prev_trend = current_trend
    else:
        current_trend = with_trend[-1]["trend_weight"]
        prev_trend = with_trend[0]["trend_weight"]

    # Attempt TDEE estimation
    from core.energy import bmr as compute_bmr, age_from_birth
    age = age_from_birth(profile["birth_date"])
    bmr_val = compute_bmr(profile["sex"], current_trend, profile["height_cm"], age)

    tdee_result = estimate_tdee(recent_logs)
    used_fallback = False

    if not tdee_result["enough_data"]:
        raw_tdee = fallback_tdee(profile, current_trend)
        used_fallback = True
        validation_notes = [f"데이터 부족({tdee_result['reason']}) — BMR 기반 TDEE로 대체합니다."]
    else:
        raw_tdee = tdee_result["estimated_tdee"]
        validation_notes = []

    validated_tdee, extra_notes = validate_tdee(raw_tdee, bmr_val, prev_tdee_estimate)
    validation_notes.extend(extra_notes)

    next_week = compute_next_week(
        profile, current_trend, validated_tdee, goal_line, health_check
    )

    report = weekly_adjustment_report(
        prev_plan=prev_plan,
        tdee_result=tdee_result,
        next_plan=next_week,
        prev_trend_weight=prev_trend,
        current_trend_weight=current_trend,
    )

    return {
        "tdee_result": tdee_result,
        "validated_tdee": validated_tdee,
        "validation_notes": validation_notes,
        "current_trend_weight": round(current_trend, 3),
        "next_week": next_week,
        "report": report,
        "used_fallback": used_fallback,
    }
