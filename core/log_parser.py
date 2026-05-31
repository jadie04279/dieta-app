"""
Natural language log parser: Korean meal/exercise text → structured records.

LLM contract:
  Meal:     input text → [{food_id, grams, meal, confidence}]  (NO kcal)
  Exercise: input text → [{activity_key, minutes}]             (NO kcal)
  Code computes all kcal values from DB/MET tables.

When LLM is unavailable or fails, keyword fallback is used.
All results are marked with `source`: "llm" | "fallback".
"""
from __future__ import annotations

import re
from typing import Optional

# Korean portion-size hints (for LLM prompt and fallback)
PORTION_HINTS = """한국어 분량 표현 참고:
- 한 공기 / 1공기 = 밥류 210g
- 한 그릇 = 국물류 300g, 면류 250g
- 한 개 / 1개 = 달걀 50g, 바나나 120g, 사과 200g
- 한 조각 = 빵 30g
- 한 줌 = 견과류 30g
- 1인분 = 고기류 150g, 찌개류 200g
- 한 캔 = 참치캔 100g
- 한 팩 = 요거트 100g
- 적당히 / 조금 = 50g 기본값"""

MEAL_KEYWORDS = {
    "breakfast": ["아침", "조식", "아침밥", "아침식사"],
    "lunch":     ["점심", "중식", "점심밥", "점심식사"],
    "dinner":    ["저녁", "석식", "저녁밥", "저녁식사"],
    "snack":     ["간식", "야식", "디저트", "후식"],
}


# ── Meal parsing ─────────────────────────────────────────────────────────────

def _build_meal_parse_prompt(user_text: str, candidate_foods: list[dict]) -> str:
    foods_txt = "\n".join(
        f"ID={f['id']}|{f['name']}|{f['category']}|{f['kcal_per_100g']:.0f}kcal/100g|{f.get('serving_desc','')}"
        for f in candidate_foods[:60]
    )
    return f"""당신은 한국어 식사 기록 파서입니다. 사용자의 식사 텍스트를 분석해 음식 DB 항목으로 매핑하세요.

규칙:
- 아래 foods 목록의 ID만 사용하세요. 목록에 없는 ID 생성 금지.
- 칼로리 숫자를 절대 생성하지 마세요 (코드가 계산합니다).
- 끼니(meal)는 breakfast / lunch / dinner / snack 중 하나.
- grams를 그램으로 추정하세요 (아래 분량 힌트 참고).
- DB에 없는 음식 → 가장 유사한 항목으로 매핑하고 confidence 0.5 이하.
- 순수 JSON만 반환하세요. 마크다운 없이.

{PORTION_HINTS}

음식 DB (ID|이름|카테고리|100g당kcal|1회제공량):
{foods_txt}

사용자 식사 기록:
{user_text}

반환 형식:
{{"items": [{{"food_id": 123, "name": "음식명", "grams": 210, "meal": "breakfast", "confidence": 0.9}}]}}"""


def _keyword_meal_match(user_text: str, all_foods: list[dict]) -> list[dict]:
    """Keyword fallback: search foods by name tokens in user text."""
    results: list[dict] = []
    seen_ids: set[int] = set()
    food_by_name = {f["name"]: f for f in all_foods}

    # Detect meal from text
    meal = "breakfast"
    for m, keywords in MEAL_KEYWORDS.items():
        if any(k in user_text for k in keywords):
            meal = m
            break

    # Try to match food names
    for food in all_foods:
        if food["id"] in seen_ids:
            continue
        name = food["name"]
        # Remove parentheses for matching
        clean = re.sub(r"[\(（].*?[\)）]", "", name).strip()
        if clean and clean in user_text:
            grams = _default_grams(food)
            results.append({
                "food_id":    food["id"],
                "name":       food["name"],
                "grams":      grams,
                "meal":       meal,
                "confidence": 0.6,
                "source":     "fallback",
            })
            seen_ids.add(food["id"])
            if len(results) >= 8:
                break

    return results


def _default_grams(food: dict) -> float:
    """Estimate standard serving grams from serving_desc or category defaults."""
    desc = food.get("serving_desc", "")
    # Extract grams from desc like "1공기(210g)"
    m = re.search(r"\((\d+(?:\.\d+)?)g\)", desc)
    if m:
        return float(m.group(1))
    # Category defaults
    cat = food.get("category", "")
    defaults = {
        "곡류": 210.0, "빵/면": 100.0, "달걀/유제품": 100.0,
        "육류": 150.0, "어패류": 100.0, "채소": 100.0,
        "채소/김치": 70.0, "과일": 150.0, "견과류": 30.0,
        "한식 메뉴": 300.0, "국/찌개": 200.0, "음료": 200.0,
    }
    return defaults.get(cat, 100.0)


def parse_meals(
    user_text: str,
    all_foods: list[dict],
    current_weight: float = 65.0,
) -> list[dict]:
    """
    Parse Korean meal text → list of food items with kcal computed from DB.

    Each item: {food_id, name, grams, meal, kcal, carb_g, protein_g, fat_g, confidence, source}
    """
    from llm.provider import get_provider
    from core.planner import calc_item_nutrition

    food_db = {f["id"]: f for f in all_foods}

    # Build candidate subset: keyword pre-filter
    tokens = re.split(r"[,，、\s]+", user_text)
    candidates = []
    cand_ids: set[int] = set()
    for tok in tokens:
        tok = tok.strip()
        if len(tok) < 2:
            continue
        for food in all_foods:
            if food["id"] not in cand_ids and tok in food["name"]:
                candidates.append(food)
                cand_ids.add(food["id"])
    # Always include common staples
    for food in all_foods:
        if food["id"] not in cand_ids and food["category"] in ("곡류", "달걀/유제품", "채소"):
            candidates.append(food)
            cand_ids.add(food["id"])
        if len(candidates) >= 60:
            break

    provider = get_provider()
    raw_items: list[dict] = []
    source = "fallback"

    if provider.available and candidates:
        prompt = _build_meal_parse_prompt(user_text, candidates)
        raw = provider.generate_json(prompt, retries=2)
        if raw and "items" in raw:
            for item in raw["items"]:
                fid   = item.get("food_id")
                grams = float(item.get("grams", 0) or 0)
                if fid and fid in food_db and grams > 0:
                    nutr = calc_item_nutrition(food_db[fid], grams)
                    nutr["meal"]       = item.get("meal", "breakfast")
                    nutr["confidence"] = float(item.get("confidence", 0.8))
                    nutr["source"]     = "llm"
                    raw_items.append(nutr)
            source = "llm"

    if not raw_items:
        for item in _keyword_meal_match(user_text, all_foods):
            fid   = item["food_id"]
            grams = item["grams"]
            if fid in food_db:
                nutr = calc_item_nutrition(food_db[fid], grams)
                nutr["meal"]       = item["meal"]
                nutr["confidence"] = item["confidence"]
                nutr["source"]     = "fallback"
                raw_items.append(nutr)

    return raw_items


def compute_intake_totals(items: list[dict]) -> dict:
    """Sum nutrition across all parsed meal items."""
    from core.planner import sum_nutrition
    return sum_nutrition(items)


# ── Exercise parsing ──────────────────────────────────────────────────────────

def _build_exercise_parse_prompt(user_text: str, met_values: list[dict]) -> str:
    acts_txt = "\n".join(
        f"{m['activity_key']}|{m['name']}|{m['category']}|MET={m['met']}"
        for m in met_values
    )
    return f"""당신은 한국어 운동 기록 파서입니다. 사용자의 운동 텍스트를 분석해 운동 DB 활동으로 매핑하세요.

규칙:
- 아래 activities 목록의 activity_key만 사용하세요.
- 칼로리 숫자를 절대 생성하지 마세요 (코드가 계산합니다).
- 시간은 분(minutes) 단위로 변환하세요 ("1시간" = 60, "30분" = 30).
- 강도가 모호하면 보수적(낮은 MET) 기본값 사용.
- 순수 JSON만 반환하세요. 마크다운 없이.

운동 DB (activity_key|이름|카테고리|MET):
{acts_txt}

사용자 운동 기록:
{user_text}

반환 형식:
{{"items": [{{"activity_key": "walk_brisk", "minutes": 40}}]}}"""


# Simple keyword → activity_key mapping for fallback
EXERCISE_KEYWORDS: list[tuple[list[str], str]] = [
    (["걷기", "산책", "워킹"],              "walk_moderate"),
    (["빠르게 걷기", "빠른 걷기"],           "walk_brisk"),
    (["달리기", "러닝", "조깅", "뛰기"],     "run_jog"),
    (["자전거", "사이클"],                   "cycling_moderate"),
    (["수영"],                               "swimming_moderate"),
    (["근력", "헬스", "웨이트", "덤벨"],     "resistance_moderate"),
    (["HIIT", "인터벌"],                     "hiit"),
    (["요가", "필라테스"],                   "yoga"),
    (["등산", "하이킹"],                     "hiking"),
    (["계단"],                               "stair_climbing"),
    (["줄넘기"],                             "jump_rope"),
    (["스트레칭"],                           "stretching"),
]


def _extract_minutes(text: str, start: int) -> int:
    """Extract duration in minutes from text near position `start`."""
    local = text[max(0, start - 30):start + 30]
    h = re.search(r"(\d+)\s*시간", local)
    m = re.search(r"(\d+)\s*분",   local)
    total = 0
    if h:
        total += int(h.group(1)) * 60
    if m:
        total += int(m.group(1))
    return total if total > 0 else 30  # default 30 min


def _keyword_exercise_match(user_text: str, met_values: list[dict]) -> list[dict]:
    met_db = {m["activity_key"]: m for m in met_values}
    results: list[dict] = []

    for keywords, key in EXERCISE_KEYWORDS:
        for kw in keywords:
            idx = user_text.find(kw)
            if idx >= 0:
                mins = _extract_minutes(user_text, idx)
                if key in met_db:
                    results.append({
                        "activity_key": key,
                        "name":         met_db[key]["name"],
                        "minutes":      mins,
                        "source":       "fallback",
                    })
                break

    return results


def parse_exercises(
    user_text: str,
    met_values: list[dict],
    current_weight: float = 65.0,
) -> list[dict]:
    """
    Parse Korean exercise text → list of activity items with kcal computed from MET table.

    Each item: {activity_key, name, minutes, met, kcal, source}
    kcal = MET × weight_kg × (minutes / 60)
    """
    from llm.provider import get_provider
    met_db = {m["activity_key"]: m for m in met_values}

    provider = get_provider()
    raw_items: list[dict] = []

    if provider.available:
        prompt = _build_exercise_parse_prompt(user_text, met_values)
        raw = provider.generate_json(prompt, retries=2)
        if raw and "items" in raw:
            for item in raw["items"]:
                key  = item.get("activity_key")
                mins = float(item.get("minutes", 0) or 0)
                if key and key in met_db and mins > 0:
                    met_val = met_db[key]["met"]
                    kcal = round(met_val * current_weight * (mins / 60.0), 1)
                    raw_items.append({
                        "activity_key": key,
                        "name":         met_db[key]["name"],
                        "minutes":      round(mins),
                        "met":          met_val,
                        "kcal":         kcal,
                        "source":       "llm",
                    })

    if not raw_items:
        for item in _keyword_exercise_match(user_text, met_values):
            key  = item["activity_key"]
            mins = item["minutes"]
            if key in met_db:
                met_val = met_db[key]["met"]
                kcal = round(met_val * current_weight * (mins / 60.0), 1)
                raw_items.append({
                    "activity_key": key,
                    "name":         met_db[key]["name"],
                    "minutes":      mins,
                    "met":          met_val,
                    "kcal":         kcal,
                    "source":       "fallback",
                })

    return raw_items


def compute_exercise_totals(items: list[dict]) -> dict:
    total_kcal = sum(i["kcal"] for i in items)
    total_mins = sum(i["minutes"] for i in items)
    return {
        "kcal": round(total_kcal, 1),
        "minutes": total_mins,
    }
