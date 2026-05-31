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
- 한 그릇 = 국물류 800g, 면류 600g
- 한 개 / 1개 = 달걀 50g, 바나나 120g, 사과 200g
- 한 조각 = 빵 30g
- 한 줌 = 견과류 30g
- 1인분 = 한식메뉴 기본 제공량 (음식별 serving_desc 참고)
- 2인분 = 1인분 × 2
- 3인분 = 1인분 × 3
- 곱배기 = 기본 1인분 × 1.7 (면류/국물류)
- 대자 / 큰 것 = 기본 × 1.5
- 소자 / 작은 것 = 기본 × 0.7
- 반 / 절반 = 기본 × 0.5
- 한 캔 = 참치캔 100g
- 한 팩 = 요거트 100g
- 적당히 / 조금 = 50g 기본값
- 한식 메뉴 1인분 기준: 짬뽕 800g, 짜장면 600g, 물회 700g, 비빔밥 600g, 냉면 600g, 김밥 1줄 250g, 떡볶이 300g, 돈까스 200g"""

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
- 곱배기·2인분·대자 등 분량 수식어를 반드시 grams에 반영하세요.
- 음식명이 DB에 있는 항목과 정확히 일치하면 confidence 0.9 이상.
- DB에 없는 음식 → 가장 유사한 카테고리 항목으로 매핑하고 confidence 0.5 이하.
- 음식 이름이 DB 항목명과 유사하면 적극적으로 매핑하세요 (예: 짬뽕→짬뽕, 물회→물회).
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

    # Tokenize user text to prevent partial matches (e.g. "배" inside "곱배기")
    text_tokens = set(re.split(r"[,，、\s\d]+", user_text))

    # Detect meal per-sentence (아침→breakfast, 저녁→dinner, etc.)
    def _detect_meal(segment: str) -> str:
        for m, keywords in MEAL_KEYWORDS.items():
            if any(k in segment for k in keywords):
                return m
        return "breakfast"

    # Split text into meal segments by sentence delimiters
    segments = re.split(r"[,，.。]+", user_text)

    # Build meal map: token → meal
    token_meal: dict[str, str] = {}
    for seg in segments:
        meal = _detect_meal(seg)
        for tok in re.split(r"[\s\d]+", seg):
            if tok:
                token_meal[tok] = meal

    # Try to match food names as whole tokens only
    for food in all_foods:
        if food["id"] in seen_ids:
            continue
        name = food["name"]
        clean = re.sub(r"[\(（].*?[\)）]", "", name).strip()
        if clean and clean in text_tokens:
            grams = _default_grams(food)
            # Apply portion multipliers from user text
            if any(w in user_text for w in ["곱배기", "곱빼기"]):
                grams = round(grams * 1.7, 0)
            elif "2인분" in user_text:
                grams = round(grams * 2.0, 0)
            elif "3인분" in user_text:
                grams = round(grams * 3.0, 0)
            elif any(w in user_text for w in ["대자", "큰 것", "큰것"]):
                grams = round(grams * 1.5, 0)
            # Detect meal from surrounding sentence
            meal = token_meal.get(clean, "breakfast")
            results.append({
                "food_id":    food["id"],
                "name":       food["name"],
                "grams":      grams,
                "meal":       meal,
                "confidence": 0.7,
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
    text_tokens = set(re.split(r"[,，、\s]+", user_text))

    # ── Step 1: 음식명 정확 매칭 (LLM 오매핑 방지) ──────────────────────────
    # 사용자 텍스트에 DB 음식명이 정확히 포함되면 바로 매핑
    def _portion_mult(ctx: str) -> float:
        if any(w in ctx for w in ["곱배기", "곱빼기"]):
            return 1.7
        if "2인분" in ctx:
            return 2.0
        if "3인분" in ctx:
            return 3.0
        if any(w in ctx for w in ["대자", "큰것", "큰 것"]):
            return 1.5
        if any(w in ctx for w in ["소자", "반", "작은"]):
            return 0.5
        return 1.0

    def _detect_meal_ctx(token: str) -> str:
        idx = user_text.find(token)
        ctx = user_text[max(0, idx - 20):idx + 20]
        for m, kws in MEAL_KEYWORDS.items():
            if any(k in ctx for k in kws):
                return m
        return "breakfast"

    exact_items: list[dict] = []
    exact_ids: set[int] = set()
    for food in all_foods:
        clean = re.sub(r"[\(（].*?[\)）]", "", food["name"]).strip()
        if len(clean) < 2 or food["id"] in exact_ids:
            continue
        if clean in text_tokens:
            base = _default_grams(food)
            # 해당 음식 전후 문맥에서 분량 배수 추출
            idx = user_text.find(clean)
            ctx = user_text[max(0, idx - 10):idx + len(clean) + 10]
            grams = round(base * _portion_mult(ctx), 0)
            nutr = calc_item_nutrition(food, grams)
            nutr["meal"]       = _detect_meal_ctx(clean)
            nutr["confidence"] = 0.95
            nutr["source"]     = "exact"
            exact_items.append(nutr)
            exact_ids.add(food["id"])

    if exact_items:
        return exact_items

    # ── Step 2: LLM 분석 (정확 매칭 실패 시) ────────────────────────────────
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
    for food in all_foods:
        if food["id"] not in cand_ids and food["category"] in (
            "한식 메뉴", "국/찌개", "곡류", "달걀/유제품", "채소"
        ):
            candidates.append(food)
            cand_ids.add(food["id"])
        if len(candidates) >= 80:
            break

    provider = get_provider()
    raw_items: list[dict] = []

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
