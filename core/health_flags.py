"""
Health check flag detector.
Flags abnormal lab values and returns structured warnings with behavioral modifiers.

Thresholds (Korean clinical guidelines):
  - Fasting glucose ≥ 126 mg/dL  → diabetes suspected
  - Fasting glucose 100–125      → pre-diabetes
  - SBP ≥ 140 OR DBP ≥ 90       → hypertension
  - LDL ≥ 160 mg/dL              → dyslipidemia
  - Triglycerides ≥ 200 mg/dL   → dyslipidemia
"""
from __future__ import annotations


def check_health_flags(health_check: dict) -> list[dict]:
    """
    Evaluate health check values and return a list of active flag dicts.

    Each flag has:
      code      – machine-readable identifier
      message   – user-facing Korean explanation
      severity  – 'error' | 'warning'
      modifier  – optional behavioral adjustment for the planner:
                  'conservative_exercise' | 'limit_high_intensity' | 'conservative_deficit'
    """
    if not health_check:
        return []

    flags: list[dict] = []

    glucose = _safe_float(health_check.get("fasting_glucose"))
    sbp = _safe_float(health_check.get("sbp"))
    dbp = _safe_float(health_check.get("dbp"))
    ldl = _safe_float(health_check.get("ldl"))
    trig = _safe_float(health_check.get("triglyceride"))

    if glucose is not None:
        if glucose >= 126:
            flags.append({
                "code": "diabetes_risk",
                "message": f"공복혈당 {glucose:.0f} mg/dL — 당뇨 의심. 반드시 전문의 상담을 받으세요.",
                "severity": "error",
                "modifier": "conservative_exercise",
            })
        elif glucose >= 100:
            flags.append({
                "code": "prediabetes_risk",
                "message": f"공복혈당 {glucose:.0f} mg/dL — 당뇨 전단계 범위입니다. 주의가 필요합니다.",
                "severity": "warning",
                "modifier": None,
            })

    if sbp is not None and dbp is not None:
        if sbp >= 140 or dbp >= 90:
            flags.append({
                "code": "hypertension",
                "message": f"혈압 {sbp:.0f}/{dbp:.0f} mmHg — 고혈압 범위. 고강도 운동을 제한하고 전문의 상담을 받으세요.",
                "severity": "error",
                "modifier": "limit_high_intensity",
            })

    if ldl is not None and ldl >= 160:
        flags.append({
            "code": "high_ldl",
            "message": f"LDL 콜레스테롤 {ldl:.0f} mg/dL — 이상지질혈증 주의. 전문의 상담을 권고합니다.",
            "severity": "warning",
            "modifier": "conservative_deficit",
        })

    if trig is not None and trig >= 200:
        flags.append({
            "code": "high_triglyceride",
            "message": f"중성지방 {trig:.0f} mg/dL — 이상지질혈증 주의. 전문의 상담을 권고합니다.",
            "severity": "warning",
            "modifier": "conservative_deficit",
        })

    return flags


def _safe_float(value) -> float | None:
    try:
        v = float(value)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None
