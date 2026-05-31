"""
Safety guardrails: speed cap, calorie floor, BMI gate, actual-speed alert.
All guardrails override user input — they are never bypassed.

Thresholds:
  - Speed cap: min(body_weight × 1%, 1.0 kg/week)
  - Calorie floor: max(female 1200 / male 1500, BMR × 1.1)
  - BMI underweight gate: < 18.5 → disable loss mode
  - BMI goal clamp: goal weight that would yield BMI < 18.5 → raise to 18.5 floor
"""
from __future__ import annotations


# ── Speed cap ───────────────────────────────────────────────────────────────

def speed_cap(weight_kg: float) -> float:
    """Max safe weekly weight loss: 1% of body weight, ceiling 1.0 kg."""
    return min(weight_kg * 0.01, 1.0)


# ── Calorie floor ───────────────────────────────────────────────────────────

def calorie_floor(sex: str, bmr_val: float) -> float:
    """Minimum safe daily intake: the larger of absolute minimum and BMR × 1.1."""
    absolute_min = 1500.0 if sex == "male" else 1200.0
    return max(absolute_min, bmr_val * 1.1)


# ── BMI helpers ─────────────────────────────────────────────────────────────

def bmi(weight_kg: float, height_cm: float) -> float:
    h = height_cm / 100.0
    return weight_kg / (h * h)


def min_weight_for_bmi(height_cm: float, bmi_threshold: float = 18.5) -> float:
    """Minimum weight that keeps BMI at or above the threshold."""
    h = height_cm / 100.0
    return bmi_threshold * h * h


# ── BMI gate ────────────────────────────────────────────────────────────────

def check_bmi_gate(
    weight_kg: float,
    height_cm: float,
    goal_weight_kg: float | None = None,
) -> dict:
    """
    Returns:
        current_bmi        – computed BMI
        loss_mode_active   – False if currently underweight
        clamped_goal_weight – goal adjusted to BMI 18.5 minimum (or None if no clamp needed)
        flags              – list of flag dicts
    """
    current_bmi = bmi(weight_kg, height_cm)
    min_w = min_weight_for_bmi(height_cm)
    flags: list[dict] = []
    loss_mode_active = True

    if current_bmi < 18.5:
        flags.append({
            "code": "bmi_underweight",
            "message": f"현재 BMI {current_bmi:.1f}로 저체중입니다. 감량 모드를 권장하지 않습니다. 유지 또는 근성장 모드를 고려해 주세요.",
            "severity": "error",
        })
        loss_mode_active = False

    clamped_goal = goal_weight_kg
    if goal_weight_kg is not None and goal_weight_kg < min_w:
        clamped_goal = round(min_w + 0.05, 1)
        flags.append({
            "code": "goal_bmi_clamp",
            "message": (
                f"목표 체중 {goal_weight_kg:.1f}kg은 BMI 18.5 미만입니다. "
                f"안전 하한선 {clamped_goal:.1f}kg으로 조정합니다."
            ),
            "severity": "warning",
        })

    return {
        "current_bmi": round(current_bmi, 1),
        "loss_mode_active": loss_mode_active,
        "clamped_goal_weight": clamped_goal,
        "flags": flags,
    }


# ── Actual speed alert ──────────────────────────────────────────────────────

def check_actual_speed(
    trend_weight_start: float,
    trend_weight_end: float,
    days: int,
) -> dict:
    """
    Alert if actual weight loss rate over the window exceeds the safe cap.
    Uses the average of start/end weight for the cap calculation.
    """
    if days <= 0:
        return {"alert": False, "actual_weekly_loss": 0.0}

    weekly_loss = (trend_weight_start - trend_weight_end) / days * 7.0
    avg_weight = (trend_weight_start + trend_weight_end) / 2.0
    cap = speed_cap(avg_weight)

    alert = weekly_loss > cap * 1.05  # 5% tolerance for floating-point noise
    return {
        "alert": alert,
        "actual_weekly_loss": round(weekly_loss, 3),
        "safe_cap": round(cap, 3),
        "message": (
            f"실제 주간 감량({weekly_loss:.2f}kg)이 안전 속도({cap:.2f}kg/주)를 초과합니다. "
            "다음 주 적자를 자동 축소합니다."
        ) if alert else "",
    }


# ── Combined guardrail application ─────────────────────────────────────────

def apply_guardrails(
    profile: dict,
    current_weight: float,
    raw_targets: dict,
    health_check: dict | None = None,
) -> tuple[dict, list[dict]]:
    """
    Apply all safety rules to raw targets computed by energy.plan_targets().

    Returns (adjusted_targets, active_flags).
    Flags are dicts with keys: code, message, severity.
    """
    from core.health_flags import check_health_flags

    flags: list[dict] = []
    targets = dict(raw_targets)

    # 1. BMI gate
    gate = check_bmi_gate(current_weight, profile["height_cm"], profile.get("goal_weight_kg"))
    flags.extend(gate["flags"])

    if not gate["loss_mode_active"]:
        # Currently underweight: no deficit allowed
        targets["planned_loss_kg"] = 0.0
        targets["daily_deficit"] = 0.0
        targets["target_intake_kcal"] = targets["tdee"]
        targets["target_exercise_kcal"] = 0.0
        return targets, flags

    # 2. Speed cap (re-enforce in case external code bypassed it)
    cap = speed_cap(current_weight)
    if targets["planned_loss_kg"] > cap + 1e-6:
        flags.append({
            "code": "speed_cap_applied",
            "message": (
                f"주간 감량 목표를 {targets['planned_loss_kg']:.2f}kg에서 "
                f"안전 속도({cap:.2f}kg/주)로 조정했습니다."
            ),
            "severity": "info",
        })
        targets["planned_loss_kg"] = cap
        daily_deficit = cap * 7700.0 / 7.0
        targets["daily_deficit"] = round(daily_deficit, 1)
        floor = calorie_floor(profile["sex"], targets["bmr"])
        targets["target_intake_kcal"] = round(max(floor, targets["tdee"] - daily_deficit), 1)

    # 3. Calorie floor
    floor = calorie_floor(profile["sex"], targets["bmr"])
    if targets["target_intake_kcal"] < floor - 1e-6:
        flags.append({
            "code": "calorie_floor_applied",
            "message": f"일일 섭취 목표를 최소 안전 기준({floor:.0f}kcal)으로 상향 조정했습니다.",
            "severity": "warning",
        })
        targets["target_intake_kcal"] = round(floor, 1)

    # 4. Health check flags
    if health_check:
        hflags = check_health_flags(health_check)
        for hf in hflags:
            flags.append(hf)
            modifier = hf.get("modifier")
            if modifier == "conservative_exercise":
                # Cap weekly exercise at 150 kcal for diabetes risk
                targets["target_exercise_kcal"] = min(targets.get("target_exercise_kcal", 0), 150.0)
            elif modifier == "conservative_deficit":
                # Widen intake by 10% of current deficit (reduce stress)
                current_deficit = targets["tdee"] - targets["target_intake_kcal"]
                reduction = current_deficit * 0.10
                targets["target_intake_kcal"] = round(
                    min(targets["target_intake_kcal"] + reduction, targets["tdee"]), 1
                )

    return targets, flags
