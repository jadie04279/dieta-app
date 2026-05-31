"""
Onboarding: profile setup → goal feasibility → mode selection → save.
Two-step flow using session_state:
  step 1 – enter body metrics + goal
  step 2 – show feasibility analysis, pick mode, confirm
"""
import streamlit as st
import json
from datetime import date, timedelta
from core.utils import today as _today
from db.repo import upsert_profile, add_health_check, upsert_daily_log, save_goal_schedule


def _save_and_go(d: dict, goal_line: dict, mode: str):
    upsert_profile({
        "sex": d["sex"], "birth_date": d["birth_date"],
        "height_cm": d["height_cm"], "activity_factor": d["activity_factor"],
        "goal_weight_kg": d["goal_weight"], "target_date": d["target_date"],
        "macro_carb_pct": 50, "macro_prot_pct": 30, "macro_fat_pct": 20,
        "food_prefs_json": {"allergies": [], "dislikes": [], "cuisine": "korean"},
    })
    upsert_daily_log({"date": str(_today()), "weight_kg": d["current_weight"]})

    hd = d.get("health_data", {})
    if any((hd.get(k) or 0) > 0 for k in ["fasting_glucose", "sbp", "ldl", "triglyceride"]):
        hd["date"] = str(_today())
        add_health_check(hd)

    save_goal_schedule(goal_line)

    st.session_state.onboard_step = 1
    st.session_state.onboard_data = {}
    st.success("시작합니다!")
    st.balloons()
    st.switch_page("pages/today.py")


st.markdown("## 프로필 설정")
st.markdown("처음 한 번만 입력하시면 됩니다.")

if "onboard_step" not in st.session_state:
    st.session_state.onboard_step = 1
if "onboard_data" not in st.session_state:
    st.session_state.onboard_data = {}

# ── Step 1: Body metrics + goal ──────────────────────────────────────────────
if st.session_state.onboard_step == 1:
    with st.form("onboard_step1"):
        st.markdown("### 기본 정보")
        c1, c2 = st.columns(2)
        with c1:
            sex = st.selectbox("성별", ["female", "male"],
                               format_func=lambda x: "여성" if x == "female" else "남성")
            birth_date = st.date_input("생년월일",
                value=date(1992, 1, 1),
                min_value=date(1924, 1, 1),
                max_value=_today() - timedelta(days=365 * 16))
        with c2:
            height_cm = st.number_input("키 (cm)", 100.0, 250.0, 163.0, 0.5)
            current_weight = st.number_input("현재 체중 (kg)", 30.0, 300.0, 65.0, 0.5)

        activity_map = {
            1.2:   "거의 안 움직임 (사무직, 운동 없음)",
            1.375: "가볍게 활동 (주 1~3회 운동)",
            1.55:  "보통 활동 (주 3~5회 운동)",
            1.725: "활발히 활동 (주 6~7회 운동)",
            1.9:   "매우 활발 (하루 2회 운동 / 육체 노동)",
        }
        activity_factor = st.selectbox("활동 수준", list(activity_map.keys()),
                                       format_func=lambda x: activity_map[x], index=2)

        st.markdown("### 목표")
        c3, c4 = st.columns(2)
        with c3:
            goal_weight = st.number_input("목표 체중 (kg)", 30.0, 300.0, 58.0, 0.5)
        with c4:
            target_date = st.date_input("목표 날짜",
                value=_today() + timedelta(weeks=16),
                min_value=_today() + timedelta(weeks=4))

        st.markdown("### 건강검진 수치 (선택)")
        with st.expander("건강검진 수치 입력"):
            h1, h2 = st.columns(2)
            with h1:
                glucose  = st.number_input("공복혈당 (mg/dL)", 0.0, 500.0, 0.0)
                sbp      = st.number_input("수축기 혈압", 0, 300, 0)
                dbp      = st.number_input("이완기 혈압", 0, 200, 0)
                chol     = st.number_input("총 콜레스테롤", 0.0, 1000.0, 0.0)
            with h2:
                ldl      = st.number_input("LDL", 0.0, 500.0, 0.0)
                hdl      = st.number_input("HDL", 0.0, 200.0, 0.0)
                trig     = st.number_input("중성지방", 0.0, 2000.0, 0.0)

        submitted = st.form_submit_button("목표 분석하기 →", use_container_width=True, type="primary")

    if submitted:
        from core.safety import check_bmi_gate, bmi as calc_bmi
        from core.health_flags import check_health_flags

        # Validate
        errors = []
        if goal_weight >= current_weight:
            errors.append("목표 체중이 현재 체중 이상입니다. 감량 앱이므로 목표 체중은 낮아야 합니다.")
        gate = check_bmi_gate(current_weight, height_cm, goal_weight)
        if not gate["loss_mode_active"]:
            errors.append(f"현재 BMI {gate['current_bmi']}로 저체중입니다. 감량을 권장하지 않습니다.")

        if errors:
            for e in errors:
                st.error(e)
        else:
            health_data = {
                "fasting_glucose": glucose, "sbp": sbp, "dbp": dbp,
                "total_chol": chol, "ldl": ldl, "hdl": hdl, "triglyceride": trig,
            }
            st.session_state.onboard_data = {
                "sex": sex, "birth_date": str(birth_date),
                "height_cm": height_cm, "current_weight": current_weight,
                "activity_factor": activity_factor,
                "goal_weight": float(gate["clamped_goal_weight"] or goal_weight),
                "original_goal_weight": goal_weight,
                "target_date": str(target_date),
                "health_data": health_data,
                "bmi_flags": gate["flags"],
            }
            st.session_state.onboard_step = 2
            st.rerun()

# ── Step 2: Feasibility analysis + mode selection ────────────────────────────
elif st.session_state.onboard_step == 2:
    d = st.session_state.onboard_data
    from core.schedule import build_goal_line, unsafe_weeks
    from core.safety import bmi as calc_bmi

    # Build both modes for comparison
    gl_safe  = build_goal_line(d["current_weight"], _today(),
                               d["goal_weight"], d["target_date"],
                               d["sex"], d["height_cm"], mode="rate_safe")
    gl_fixed = build_goal_line(d["current_weight"], _today(),
                               d["goal_weight"], d["target_date"],
                               d["sex"], d["height_cm"], mode="deadline_fixed")

    total_loss = d["current_weight"] - d["goal_weight"]
    available_weeks = (date.fromisoformat(d["target_date"]) - _today()).days / 7
    feasible = gl_safe["feasible"]

    st.markdown("### 목표 분석 결과")

    # BMI clamp notice
    if d["goal_weight"] != d["original_goal_weight"]:
        st.warning(f"목표 체중을 BMI 18.5 안전 하한선인 {d['goal_weight']:.1f}kg으로 조정했습니다.")

    current_bmi = calc_bmi(d["current_weight"], d["height_cm"])
    goal_bmi    = calc_bmi(d["goal_weight"],    d["height_cm"])

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("현재 체중", f"{d['current_weight']:.1f} kg")
    m2.metric("목표 체중", f"{d['goal_weight']:.1f} kg",
              delta=f"−{total_loss:.1f} kg")
    m3.metric("현재 BMI", f"{current_bmi:.1f}")
    m4.metric("목표 BMI", f"{goal_bmi:.1f}")

    st.divider()

    if feasible:
        st.success(
            f"✓ 요청하신 기간({available_weeks:.0f}주) 내에 안전하게 달성 가능합니다. "
            f"안전 속도 기준 도달 예상일: **{gl_safe['projected_date']}**"
        )
    else:
        needed_weeks = len(gl_safe["weekly_targets_json"])
        st.warning(
            f"요청하신 기간({available_weeks:.0f}주)은 안전 속도로 달성하기 어렵습니다. "
            f"안전 속도로는 약 **{needed_weeks}주** 소요 예상입니다."
        )

    # Mode cards
    st.markdown("### 진행 방식 선택")

    tab1, tab2 = st.tabs(["안전 속도 모드 (권장)", "기한 고수 모드"])

    with tab1:
        st.markdown(f"""
<div class="diet-card">
<strong>안전 속도로 감량</strong> — 주당 체중의 1% 이하<br>
예상 도달일: <strong>{gl_safe['projected_date']}</strong> &nbsp;
({len(gl_safe['weekly_targets_json'])}주 소요)<br><br>
• 칼로리 하한·안전 속도를 절대 위반하지 않습니다<br>
• 정체기가 와도 TDEE 재추정으로 자동 보정됩니다<br>
• 근손실 최소화, 요요 방지에 유리합니다
</div>
""", unsafe_allow_html=True)
        if st.button("안전 속도로 시작", use_container_width=True, type="primary"):
            _save_and_go(d, gl_safe, "rate_safe")

    with tab2:
        unsafe = unsafe_weeks(gl_fixed)
        st.markdown(f"""
<div class="diet-card">
<strong>기한 고수</strong> — 목표일 {d['target_date']} 내 달성 시도<br>
{'⚠️ ' + str(len(unsafe)) + '개 주차가 안전 속도를 초과합니다.' if unsafe else '이 기간은 안전 범위 내입니다.'}<br><br>
• 칼로리 하한과 BMI 게이트는 어떤 경우에도 지킵니다<br>
• 초과 주차는 빨간색으로 표시됩니다<br>
• 물리적으로 달성 불가한 경우 가능한 최선까지만 진행합니다
</div>
""", unsafe_allow_html=True)
        if unsafe:
            st.error(f"{len(unsafe)}개 주차가 안전 속도(주당 체중 1%)를 초과합니다.")
        if st.button("기한 고수로 시작", use_container_width=True):
            _save_and_go(d, gl_fixed, "deadline_fixed")

    if st.button("← 다시 입력", use_container_width=True):
        st.session_state.onboard_step = 1
        st.rerun()


