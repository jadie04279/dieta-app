"""공통 유틸리티 — 서버 UTC와 무관하게 KST(UTC+9) 기준 오늘 날짜 반환."""
from datetime import datetime, timezone, timedelta, date as _date

_KST = timezone(timedelta(hours=9))


def today() -> _date:
    return datetime.now(_KST).date()
