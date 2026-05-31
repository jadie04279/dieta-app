"""
Weekly report: goal progress, TDEE estimation, plan history, safety flags, health data.
"""
import streamlit as st
from datetime import date
from core.utils import today as _today
import pandas as pd

from db.repo import (
    get_profile, get_recent_logs, get_latest_goal_schedule,
    get_latest_weekly_plan, get_latest_health_check, get_connection,
)
from core.adaptive import ewma_trend_weights, estimate_tdee
from core.health_flags import check_health_flags
from core.schedule import daily_target as goal_daily_target

profile = get_profile()
if not profile:
    st.warning("프로필을 먼저 설정해 주세요.")
    st.stop()

st.markdown("## 주간 리포트")

logs       = get_recent_logs(90)
goal_line  = get_latest_goal_schedule()
plan       = get_latest_weekly_plan()
health     = get_latest_health_check()

enriched    = ewma_trend_weights(logs) if logs else []
with_weight = [l for l in enriched if (l.get("weight_kg") or 0) > 0]
with_trend  = [l for l in enriched if l.get("trend_weight")]

latest_trend   = with_trend[-1]["trend_weight"]  if with_trend  else None
start_weight   = goal_line["start_weight_kg"]    if goal_line   else (with_weight[0]["weight_kg"] if with_weight else None)
goal_weight    = goal_line["goal_weight_kg"]     if goal_line   else profile.get("goal_weight_kg")
projected_date = goal_line["projected_date"]     if goal_line   else None
target_date    = goal_line["target_date"]        if goal_line   else profile.get("target_date")

# ── Goal progress ─────────────────────────────────────────────────────────────
st.markdown("### 목표 진행 현황")

cumulative_loss = (start_weight - latest_trend) if (start_weight and latest_trend) else None
total_to_lose   = (start_weight - goal_weight)  if (start_weight and goal_weight and start_weight != goal_weight) else None
pct_done        = (cumulative_loss / total_to_lose * 100) if (cumulative_loss and total_to_lose and total_to_lose > 0) else None
remaining       = (total_to_lose - cumulative_loss)       if (total_to_lose and cumulative_loss is not None)        else None

days_left = None
if projected_date:
    try:
        days_left = (date.fromisoformat(projected_date) - _today()).days
    except Exception:
        pass

g1, g2, g3, g4 = st.columns(4)
g1.metric("누적 감량",   f"{cumulative_loss:.2f} kg" if cumulative_loss is not None else "—")
g2.metric("목표 달성률", f"{pct_done:.0f}%"           if pct_done        is not None else "—")
g3.metric("남은 감량",   f"{remaining:.2f} kg"        if remaining       is not None else "—")
g4.metric("도달 예상일", projected_date if projected_date else "—",
          delta=f"목표 {target_date}" if target_date else None, delta_color="off")

if pct_done is not None:
    st.progress(min(1.0, pct_done / 100), text=f"목표 달성 {pct_done:.0f}%")

if goal_line and latest_trend:
    gt = goal_daily_target(goal_line, _today())
    tw = gt.get("target_weight") if gt else None
    if tw:
        diff = latest_trend - tw
        if abs(diff) < 0.3:
            st.success(f"추세 체중 **{latest_trend:.2f} kg** — 목표선과 거의 일치합니다.")
        elif diff > 0:
            st.warning(f"추세 체중 **{latest_trend:.2f} kg** — 목표선보다 +{diff:.2f} kg 위입니다.")
        else:
            st.info(f"추세 체중 **{latest_trend:.2f} kg** — 목표선을 {abs(diff):.2f} kg 앞서가고 있습니다.")

st.divider()

# ── TDEE estimation ───────────────────────────────────────────────────────────
st.markdown("### TDEE 역산 결과 (최근 14일)")

tdee_result = estimate_tdee(logs) if logs else {"enough_data": False, "reason": "기록 없음"}

if tdee_result["enough_data"]:
    t1, t2, t3, t4 = st.columns(4)
    t1.metric("추정 TDEE",     f"{tdee_result['estimated_tdee']:.0f} kcal/일")
    t2.metric("분석 일수",      f"{tdee_result['n_days']}일")
    t3.metric("추세 체중 변화", f"{tdee_result['delta_trend_kg']:+.2f} kg")
    t4.metric("기간 총 섭취",   f"{tdee_result['total_intake']:.0f} kcal")

    if plan:
        delta     = tdee_result["estimated_tdee"] - plan["est_tdee"]
        direction = "높게" if delta > 0 else "낮게"
        note      = (
            f"현재 계획 TDEE **{plan['est_tdee']:.0f} kcal**보다 역산값이 "
            f"**{abs(delta):.0f} kcal {direction}** 추정됩니다."
        )
        if abs(delta) > 150:
            st.caption(note + " '추세' 탭에서 재조정을 실행하세요.")
        else:
            st.caption(note + " 현재 계획이 적절합니다.")
else:
    st.info(f"TDEE 역산 데이터 부족 — {tdee_result.get('reason', '기록을 더 쌓아주세요.')}")

st.divider()

# ── Plan history ──────────────────────────────────────────────────────────────
st.markdown("### 주간 계획 이력")

conn = get_connection()
plan_rows = conn.execute(
    "SELECT week_start, est_tdee, target_intake_kcal, target_exercise_kcal, planned_loss_kg "
    "FROM weekly_plan ORDER BY week_start DESC LIMIT 6"
).fetchall()
conn.close()

if plan_rows:
    df_plans = pd.DataFrame([{
        "주 시작":   r["week_start"],
        "추정 TDEE": f"{r['est_tdee']:.0f} kcal",
        "목표 섭취": f"{r['target_intake_kcal']:.0f} kcal/일",
        "운동 목표": f"{r['target_exercise_kcal']:.0f} kcal/주",
        "목표 감량": f"{r['planned_loss_kg']:.2f} kg/주",
    } for r in plan_rows])
    st.dataframe(df_plans, use_container_width=True, hide_index=True)
else:
    st.info("저장된 주간 계획이 없습니다. 추세 탭에서 재조정을 실행하세요.")

st.divider()

# ── Safety & health flags ─────────────────────────────────────────────────────
st.markdown("### 안전 및 건강 알림")

plan_flags   = (plan.get("flags_json") or []) if plan else []
health_flags = check_health_flags(health) if health else []
all_flags    = plan_flags + health_flags

if all_flags:
    for f in all_flags:
        sev = f.get("severity", "info")
        msg = f.get("message", "")
        if sev == "error":
            st.error(f"🔴 {msg}")
        elif sev == "warning":
            st.warning(f"🟡 {msg}")
        else:
            st.info(f"🔵 {msg}")
else:
    st.success("현재 활성 안전 알림이 없습니다.")

st.divider()

# ── Health check summary ──────────────────────────────────────────────────────
st.markdown("### 최근 건강 검사")

if health:
    st.caption(f"검사 날짜: {health.get('date', '—')}")
    hc1, hc2, hc3, hc4 = st.columns(4)

    glucose = health.get("fasting_glucose")
    sbp     = health.get("sbp")
    dbp     = health.get("dbp")
    ldl     = health.get("ldl")
    tg      = health.get("triglyceride")
    hdl     = health.get("hdl")
    tc      = health.get("total_chol")

    if glucose:
        s = "🔴" if glucose >= 126 else ("🟡" if glucose >= 100 else "🟢")
        hc1.metric("공복혈당", f"{glucose:.0f} mg/dL", delta=s, delta_color="off")
    if sbp and dbp:
        s = "🔴" if (sbp >= 140 or dbp >= 90) else "🟢"
        hc2.metric("혈압", f"{sbp:.0f}/{dbp:.0f} mmHg", delta=s, delta_color="off")
    if ldl:
        s = "🔴" if ldl >= 160 else "🟢"
        hc3.metric("LDL", f"{ldl:.0f} mg/dL", delta=s, delta_color="off")
    if tg:
        s = "🔴" if tg >= 200 else "🟢"
        hc4.metric("중성지방", f"{tg:.0f} mg/dL", delta=s, delta_color="off")

    extra_cols = [c for c in [hdl, tc] if c]
    if extra_cols:
        ec1, ec2, _, _ = st.columns(4)
        if hdl:
            ec1.metric("HDL", f"{hdl:.0f} mg/dL")
        if tc:
            ec2.metric("총 콜레스테롤", f"{tc:.0f} mg/dL")
else:
    st.info("건강 검사 기록이 없습니다. '설정' 탭에서 입력할 수 있습니다.")

st.divider()

# ── Recommendations ───────────────────────────────────────────────────────────
st.markdown("### 이번 주 권고사항")

recs: list[str] = []

if cumulative_loss is not None and total_to_lose and total_to_lose > 0 and goal_line and logs:
    try:
        total_days   = (date.fromisoformat(target_date) - date.fromisoformat(goal_line["start_date"])).days
        elapsed_days = (_today() - date.fromisoformat(goal_line["start_date"])).days
        expected_frac = min(1.0, elapsed_days / total_days) if total_days > 0 else 0
        actual_frac   = cumulative_loss / total_to_lose
        if actual_frac < expected_frac * 0.6 and expected_frac > 0.1:
            recs.append("감량 속도가 목표보다 느립니다. 식사 기록을 꼼꼼히 작성하고 섭취량을 점검하세요.")
        elif actual_frac > expected_frac * 1.4 and expected_frac > 0.1:
            recs.append("감량이 빠릅니다. 단백질(체중 1 g/kg)을 충분히 섭취해 근손실을 방지하세요.")
    except Exception:
        pass

if tdee_result.get("n_days", 0) < 7:
    recs.append("체중·식사 기록일이 7일 미만입니다. 매일 기록하면 TDEE 역산 정확도가 높아집니다.")

if plan and plan.get("target_exercise_kcal", 0) > 0:
    recs.append(f"주간 운동 목표 {plan['target_exercise_kcal']:.0f} kcal — 주 4회 이상 30분씩 활동을 권장합니다.")

if not health:
    recs.append("건강 검사 결과를 입력하면 맞춤 안전 알림이 제공됩니다.")

recs.append("매주 일요일 저녁 '추세' 탭에서 주간 재조정을 실행하세요.")

for i, rec in enumerate(recs, 1):
    st.markdown(f"**{i}.** {rec}")

# ── Disclaimer ────────────────────────────────────────────────────────────────
st.divider()
st.markdown(
    """
    <div style="
        background:rgba(244,184,74,0.07);
        border:1px solid rgba(244,184,74,0.25);
        border-radius:12px;
        padding:16px 20px;
        font-size:13px;
        color:#9AA7B4;
        line-height:1.7;
    ">
    ⚠️ <strong>의학적 고지</strong><br>
    본 앱은 체중 관리를 위한 참고 정보를 제공하며 의료 진단·치료·처방을 대체하지 않습니다.
    당뇨, 고혈압, 심혈관 질환, 신장 질환, 임신·수유 중인 경우 반드시 <strong>전문 의료인과 상담</strong> 후 계획을 조정하세요.
    급격한 체중 변화, 극심한 피로, 어지러움, 심계항진이 나타나면 즉시 의료 도움을 받으세요.
    </div>
    """,
    unsafe_allow_html=True,
)
