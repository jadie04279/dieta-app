"""공통 유틸리티 — KST 날짜, 걸음수 추정 등."""
from datetime import datetime, timezone, timedelta, date as _date
from typing import Optional

_KST = timezone(timedelta(hours=9))


def today() -> _date:
    return datetime.now(_KST).date()


# MET 기반 걸음수/분 매핑 (걷기 계열 활동만)
_WALK_STEPS_PER_MIN: dict[str, int] = {
    "walk_slow":     65,   # MET ~2.5
    "walk_moderate": 85,   # MET 3.5
    "walk_brisk":   100,   # MET 4.3  ← 빠르게 걷기
    "walk_fast":    120,   # MET 5.0
    "walk_incline": 110,   # 오르막 걷기
    "treadmill":    100,   # 러닝머신
}
_WALK_NAME_KEYWORDS = ["걷기", "워킹", "산책", "treadmill", "러닝머신"]


def estimate_steps(
    minutes: int,
    activity_key: Optional[str] = None,
    name: Optional[str] = None,
) -> Optional[int]:
    """걷기 계열 활동의 예상 걸음수를 반환. 걷기가 아니면 None."""
    key = (activity_key or "").lower()
    nm  = (name or "").lower()

    # activity_key 직접 매핑
    for k, spm in _WALK_STEPS_PER_MIN.items():
        if k in key:
            return round(minutes * spm, -2)   # 백 단위 반올림

    # 이름에 걷기 키워드 포함 → 기본 100 steps/min
    if any(kw in nm for kw in _WALK_NAME_KEYWORDS):
        return round(minutes * 100, -2)

    return None


def steps_label(steps: Optional[int]) -> str:
    """걸음수를 한국어 표기 문자열로 변환."""
    if steps is None:
        return ""
    if steps >= 10000:
        return f"🦶 {steps:,}보 (만보 달성!)"
    return f"🦶 {steps:,}보"


def steps_to_kcal(steps: int, weight_kg: float) -> float:
    """걸음수 → 소모 칼로리 변환.
    공식: kcal = steps × 0.04 × (weight_kg / 70)
    """
    return round(steps * 0.04 * (weight_kg / 70.0), 1)


def steps_to_minutes(steps: int, steps_per_min: int = 100) -> int:
    """걸음수 → 분 변환 (빠르게 걷기 기준 100보/분)."""
    return max(1, round(steps / steps_per_min))
