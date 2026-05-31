"""
Meal and exercise plan generator: DB + LLM hybrid.

LLM contract:
  INPUT  — candidate food list (id, name, kcal/100g) + macro targets
  OUTPUT — food_id + grams only (NO kcal, NO macros — code computes from DB)

Exercise planning is code-only (no LLM):
  MET × weight_kg × (minutes/60) = kcal
"""
from __future__ import annotations

import json
from typing import Optional

MEAL_SPLIT   = {"breakfast": 0.25, "lunch": 0.35, "dinner": 0.30, "snack": 0.10}
SESSIONS_PER_WEEK = 4          # default exercise sessions
AEROBIC_RATIO     = 0.65       # 65% aerobic, 35% strength
TOLERANCE         = 0.12       # ±12% kcal tolerance before auto-adjust


# ── Nutrition calculation helpers ────────────────────────────────────────────

def calc_item_nutrition(food: dict, grams: float) -> dict:
    """Compute kcal and macros for a given food + gram amount using DB values."""
    ratio = grams / 100.0
    return {
        "food_id":   food["id"],
        "name":      food["name"],
        "grams":     round(grams, 1),
        "kcal":      round(food["kcal_per_100g"] * ratio, 1),
        "carb_g":    round(food["carb_g"]    * ratio, 1),
        "protein_g": round(food["protein_g"] * ratio, 1),
        "fat_g":     round(food["fat_g"]     * ratio, 1),
    }


def sum_nutrition(items: list[dict]) -> dict:
    return {
        "kcal":      round(sum(i["kcal"]      for i in items), 1),
        "carb_g":    round(sum(i["carb_g"]    for i in items), 1),
        "protein_g": round(sum(i["protein_g"] for i in items), 1),
        "fat_g":     round(sum(i["fat_g"]     for i in items), 1),
    }


def _scale_to_target(items: list[dict], target_kcal: float, food_db: dict) -> list[dict]:
    """Proportionally scale all grams so total kcal matches target."""
    total = sum(i["kcal"] for i in items)
    if total <= 0:
        return items
    scale = target_kcal / total
    result = []
    for item in items:
        food = food_db.get(item["food_id"])
        if food:
            new_grams = max(5.0, item["grams"] * scale)
            result.append(calc_item_nutrition(food, new_grams))
    return result


# ── LLM meal prompt ──────────────────────────────────────────────────────────

def _build_meal_prompt(
    foods: list[dict],
    meal_name: str,
    target_kcal: float,
    target_carb: float,
    target_prot: float,
    target_fat:  float,
    cuisine: str = "korean",
    dislikes: list[str] | None = None,
) -> str:
    foods_txt = "\n".join(
        f"ID={f['id']}|{f['name']}|{f['category']}|{f['kcal_per_100g']:.0f}kcal/100g"
        for f in foods[:80]
    )
    dislike_txt = f"\n기피 음식: {', '.join(dislikes)}" if dislikes else ""

    return f"""당신은 한국식 식단 플래너입니다. {meal_name}을 구성하세요.

규칙:
- 아래 foods 목록의 ID만 사용하세요. 목록에 없는 ID 생성 금지.
- 칼로리·영양소 숫자를 직접 출력하지 마세요 (코드가 계산합니다).
- grams(그램수)만 지정하세요.
- {cuisine} 음식 위주로 구성하세요.{dislike_txt}
- 순수 JSON만 반환하세요. 설명·마크다운 없이.

영양 목표:
- 칼로리: {target_kcal:.0f} kcal
- 탄수화물: {target_carb:.0f}g / 단백질: {target_prot:.0f}g / 지방: {target_fat:.0f}g

선택 가능한 음식 (ID|이름|카테고리|100g당 kcal):
{foods_txt}

반환 형식:
{{"items": [{{"food_id": 123, "grams": 150}}, ...]}}"""


def _parse_llm_meal(raw: dict | None, food_db: dict) -> list[dict]:
    """Validate LLM output: keep only items with valid food_ids."""
    if not raw or "items" not in raw:
        return []
    result = []
    for item in raw["items"]:
        fid = item.get("food_id")
        grams = item.get("grams", 0)
        if fid and fid in food_db and grams > 0:
            result.append(calc_item_nutrition(food_db[fid], float(grams)))
    return result


def _validate_meal(
    items: list[dict],
    target_kcal: float,
    food_db: dict,
) -> tuple[list[dict], bool]:
    """
    Return (items, within_tolerance).
    If out of tolerance, scale grams proportionally.
    """
    total = sum(i["kcal"] for i in items)
    if total <= 0:
        return items, False
    ratio = abs(total - target_kcal) / target_kcal
    if ratio > TOLERANCE:
        items = _scale_to_target(items, target_kcal, food_db)
    return items, True


# ── Greedy fallback ──────────────────────────────────────────────────────────

def _greedy_meal(
    foods: list[dict],
    target_kcal: float,
    categories: list[str] | None = None,
) -> list[dict]:
    """Rule-based fallback: pick a staple + protein + vegetable up to target kcal."""
    CAT_PREF = ["곡류", "달걀/유제품", "육류", "어패류", "채소", "콩류"]
    pool = sorted(foods, key=lambda f: CAT_PREF.index(f["category"])
                  if f["category"] in CAT_PREF else 99)

    items: list[dict] = []
    remaining = target_kcal
    for food in pool:
        if remaining <= 0:
            break
        # Use standard 100g portion, scale to fit remaining budget
        target_g = min(300.0, remaining / food["kcal_per_100g"] * 100.0)
        if target_g < 20:
            continue
        items.append(calc_item_nutrition(food, target_g))
        remaining -= items[-1]["kcal"]
        if len(items) >= 5:
            break
    return items


# ── Public: generate_meal_plan ───────────────────────────────────────────────

def generate_meal_plan(
    profile: dict,
    targets: dict,
    current_weight: float = 65.0,
) -> dict:
    """
    Generate a full one-day meal plan.

    Returns:
        {breakfast: [{food_id, name, grams, kcal, carb_g, protein_g, fat_g}], ...}
        plus a "totals" summary and "used_llm" flag.
    """
    from db.repo import get_foods_for_planner
    from llm.provider import get_provider

    provider = get_provider()
    foods = get_foods_for_planner(max_kcal_per_100g=500)
    food_db = {f["id"]: f for f in foods}

    total_kcal = targets["target_intake_kcal"]
    carb_pct   = profile.get("macro_carb_pct", 50)
    prot_pct   = profile.get("macro_prot_pct", 30)
    fat_pct    = profile.get("macro_fat_pct",  20)

    prefs = profile.get("food_prefs_json") or {}
    if isinstance(prefs, str):
        import json as _j
        try:
            prefs = _j.loads(prefs)
        except Exception:
            prefs = {}
    dislikes = prefs.get("dislikes", [])
    cuisine  = prefs.get("cuisine", "korean")

    plan = {}
    used_llm = provider.available

    for meal, frac in MEAL_SPLIT.items():
        meal_kcal  = total_kcal * frac
        meal_carb  = total_kcal * carb_pct / 100 / 4 * frac
        meal_prot  = total_kcal * prot_pct / 100 / 4 * frac
        meal_fat   = total_kcal * fat_pct  / 100 / 9 * frac

        items: list[dict] = []

        if provider.available:
            meal_label = {"breakfast": "아침", "lunch": "점심",
                          "dinner": "저녁", "snack": "간식"}[meal]
            prompt = _build_meal_prompt(
                foods, meal_label, meal_kcal,
                meal_carb, meal_prot, meal_fat,
                cuisine, dislikes,
            )
            for _attempt in range(2):
                raw = provider.generate_json(prompt, retries=1)
                parsed = _parse_llm_meal(raw, food_db)
                if parsed:
                    items, ok = _validate_meal(parsed, meal_kcal, food_db)
                    if ok:
                        break

        if not items:
            items = _greedy_meal(foods, meal_kcal)
            used_llm = False

        plan[meal] = items

    # Overall totals
    all_items = [i for meal_items in plan.values() for i in meal_items]
    plan["totals"] = sum_nutrition(all_items)
    plan["used_llm"] = used_llm
    return plan


# ── Exercise plan (code-only, no LLM) ────────────────────────────────────────

def generate_exercise_plan(
    targets: dict,
    current_weight: float,
    health_flags: list[dict] | None = None,
    sessions: int = SESSIONS_PER_WEEK,
) -> dict:
    """
    Distribute the weekly exercise target across N sessions.
    Returns structured exercise_json ready for weekly_plan.
    kcal = MET × weight_kg × hours  (code, not LLM)
    """
    from db.repo import get_all_met_values

    weekly_kcal = targets.get("target_exercise_kcal", 0.0)
    if weekly_kcal <= 0:
        return {"sessions": [], "weekly_total_kcal": 0.0}

    # Check health flags for intensity limits
    high_intensity_ok = True
    if health_flags:
        for f in health_flags:
            if f.get("modifier") in ("limit_high_intensity", "conservative_exercise"):
                high_intensity_ok = False

    all_mets = get_all_met_values()
    aerobic  = [m for m in all_mets if m["category"] == "유산소" and
                (high_intensity_ok or m["met"] <= 5.0)]
    strength = [m for m in all_mets if m["category"] == "근력" and
                (high_intensity_ok or m["met"] <= 4.0)]

    # Default activity mix
    if not aerobic:
        aerobic = [{"activity_key": "walk_moderate", "name": "걷기", "met": 3.5}]
    if not strength:
        strength = [{"activity_key": "resistance_light", "name": "근력운동(가벼운)", "met": 3.0}]

    # Pick representative activities
    aero_act   = next((a for a in aerobic if "walk_brisk" in a["activity_key"]),  aerobic[0])
    strength_act = next((a for a in strength if "moderate" in a["activity_key"]), strength[0])

    session_kcal = weekly_kcal / sessions
    aero_kcal    = session_kcal * AEROBIC_RATIO
    str_kcal     = session_kcal * (1 - AEROBIC_RATIO)

    # Duration (minutes) = kcal / (MET × weight_kg / 60)
    def _minutes(kcal: float, met: float) -> int:
        mins = kcal / (met * current_weight / 60.0)
        return max(10, min(90, round(mins / 5) * 5))

    aero_min   = _minutes(aero_kcal,   aero_act["met"])
    str_min    = _minutes(str_kcal,    strength_act["met"])

    def _kcal(met: float, mins: int) -> float:
        return round(met * current_weight * (mins / 60.0), 1)

    DAYS = ["monday", "tuesday", "wednesday", "thursday",
            "friday", "saturday", "sunday"]
    day_indices = list(range(0, 7, 7 // sessions))[:sessions]

    sessions_list = []
    actual_total = 0.0
    for day_idx in day_indices:
        aero_k = _kcal(aero_act["met"],   aero_min)
        str_k  = _kcal(strength_act["met"], str_min)
        actual_total += aero_k + str_k
        sessions_list.append({
            "day": DAYS[day_idx],
            "activities": [
                {
                    "activity_key": aero_act["activity_key"],
                    "name":         aero_act["name"],
                    "minutes":      aero_min,
                    "met":          aero_act["met"],
                    "kcal":         aero_k,
                },
                {
                    "activity_key": strength_act["activity_key"],
                    "name":         strength_act["name"],
                    "minutes":      str_min,
                    "met":          strength_act["met"],
                    "kcal":         str_k,
                },
            ],
        })

    return {
        "sessions": sessions_list,
        "weekly_total_kcal": round(actual_total, 1),
        "used_llm": False,
    }
