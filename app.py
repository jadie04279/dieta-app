"""
Diet Loop — Adaptive TDEE closed-loop diet coach
Main Streamlit entry point: handles routing and shared setup.
"""
import os
import streamlit as st
from pathlib import Path

# Inject Streamlit secrets into os.environ so non-Streamlit modules (db, llm)
# can read DATABASE_URL, GEMINI_API_KEY, ANTHROPIC_API_KEY via os.environ.
try:
    for _k, _v in st.secrets.items():
        if isinstance(_v, str):
            os.environ.setdefault(_k, _v)
except Exception:
    pass  # secrets not available in local dev without secrets.toml

from db.repo import init_db, get_profile

# ── Page config (must be first Streamlit call) ─────────────────────────────
st.set_page_config(
    page_title="Diet Loop",
    page_icon="🔄",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# ── Inject custom CSS ───────────────────────────────────────────────────────
def _inject_css() -> None:
    css_path = Path(__file__).parent / "assets" / "theme.css"
    if css_path.exists():
        css = css_path.read_text(encoding="utf-8")
        st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)

_inject_css()

# ── DB init (idempotent) ────────────────────────────────────────────────────
init_db()

# ── Navigation ──────────────────────────────────────────────────────────────
PAGES = {
    "오늘":    "pages/today.py",
    "추세":    "pages/trends.py",
    "주간 계획": "pages/weekly.py",
    "리포트":  "pages/report.py",
    "설정":    "pages/settings.py",
}

profile = get_profile()

# If no profile yet, redirect to onboarding
if not profile:
    import importlib.util, sys

    onboarding_path = Path(__file__).parent / "pages" / "onboarding.py"
    spec = importlib.util.spec_from_file_location("onboarding", onboarding_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
else:
    # Bottom tab navigation
    tab_labels = list(PAGES.keys())
    default_idx = 0

    if "active_tab" not in st.session_state:
        st.session_state.active_tab = tab_labels[0]

    cols = st.columns(len(tab_labels))
    for i, (label, col) in enumerate(zip(tab_labels, cols)):
        with col:
            if st.button(
                label,
                key=f"nav_{label}",
                use_container_width=True,
                type="primary" if st.session_state.active_tab == label else "secondary",
            ):
                st.session_state.active_tab = label
                st.rerun()

    st.divider()

    active = st.session_state.active_tab
    page_path = Path(__file__).parent / PAGES[active]

    import importlib.util
    spec = importlib.util.spec_from_file_location(active, page_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

# ── Disclaimer ─────────────────────────────────────────────────────────────
st.markdown(
    '<div class="disclaimer">본 앱은 참고용이며 전문의 상담을 대체하지 않습니다.</div>',
    unsafe_allow_html=True,
)
