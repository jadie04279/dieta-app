"""
Energy model: BMR (Mifflin-St Jeor), TDEE, and daily/weekly calorie targets.

References:
  - Mifflin MD et al. (1990). JADA 90(3):375-381
  - 1 kg body fat ≈ 7,700 kcal stored energy (commonly accepted approximation)
"""
from datetime import date


def age_from_birth(birth_date: str | date) -> int:
    if isinstance(birth_date, str):
        birth_date = date.fromisoformat(birth_date)
    today = date.today()
    age = today.year - birth_date.year
    if (today.month, today.day) < (birth_date.month, birth_date.day):
        age -= 1
    return age


def bmr(sex: str, weight_kg: float, height_cm: float, age_years: int) -> float:
    """
    Mifflin-St Jeor BMR (kcal/day).
      male:   10·kg + 6.25·cm − 5·age + 5
      female: 10·kg + 6.25·cm − 5·age − 161
    """
    base = 10.0 * weight_kg + 6.25 * height_cm - 5.0 * age_years
    return base + 5.0 if sex == "male" else base - 161.0


def tdee(bmr_val: float, activity_factor: float) -> float:
    """TDEE = BMR × activity factor (1.2 – 1.9)."""
    return bmr_val * activity_factor


def plan_targets(profile: dict, current_weight: float) -> dict:
    """
    Compute one week's calorie/exercise targets from profile and current weight.

    Returns:
        bmr               – daily BMR (kcal)
        tdee              – daily TDEE (kcal)
        planned_loss_kg   – target weekly loss after safety cap (kg)
        daily_deficit     – required daily energy deficit (kcal)
        target_intake_kcal – target daily intake (kcal), floored by safety
        target_exercise_kcal – weekly exercise energy expenditure target (kcal)
    """
    from core.safety import speed_cap, calorie_floor

    age = age_from_birth(profile["birth_date"])
    bmr_val = bmr(profile["sex"], current_weight, profile["height_cm"], age)
    tdee_val = tdee(bmr_val, profile["activity_factor"])

    planned_loss_kg = speed_cap(current_weight)
    daily_deficit = planned_loss_kg * 7700.0 / 7.0

    floor = calorie_floor(profile["sex"], bmr_val)
    raw_intake = tdee_val - daily_deficit
    target_intake = max(floor, raw_intake)

    # If diet intake is floored, push remainder into exercise target
    diet_deficit = tdee_val - target_intake
    leftover_deficit = max(0.0, daily_deficit - diet_deficit)
    target_exercise_kcal_week = leftover_deficit * 7.0

    return {
        "bmr": round(bmr_val, 1),
        "tdee": round(tdee_val, 1),
        "planned_loss_kg": round(planned_loss_kg, 3),
        "daily_deficit": round(daily_deficit, 1),
        "target_intake_kcal": round(target_intake, 1),
        "target_exercise_kcal": round(target_exercise_kcal_week, 1),
    }
