import datetime
import html
import os

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
API_KEY = os.getenv("API_KEY")

TABLEAU_EMBED_URL = (
    "https://public.tableau.com/views/PatientCohortIndividualDashboards/"
    "CohortOverview?:showVizHome=no&:embed=true&:tabs=yes&:toolbar=yes"
)
TABLEAU_PUBLIC_LINK = (
    "https://public.tableau.com/views/PatientCohortIndividualDashboards/"
    "CohortOverview?:language=en-US&:display_count=n&:origin=viz_share_link"
)

ACCENT = "#0F766E"
ACCENT_SOFT = "#7FB3AC"
GRID = "#EEF2F2"

st.set_page_config(page_title="Clinical Data Platform", page_icon="\U0001fa7a", layout="wide")

# ---------------------------------------------------------------------------
# Styling. Streamlit's default components (st.metric, st.line_chart) read as
# "a script that shows data" rather than a tool someone would want to open.
# This CSS + a handful of small HTML helpers below borrow a few patterns from
# real clinical software (colored status/alert banners, dense card-based
# sections) at a scale this project's actual data can support.
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
    html, body, [class*="css"] { font-family: 'Inter', -apple-system, sans-serif; }

    .brand-row {
        display: flex; align-items: center; gap: 0.5rem;
        padding: 0.15rem 0 1rem 0; margin-bottom: 0.5rem;
        border-bottom: 1px solid rgba(15, 118, 110, 0.15);
    }
    .brand-title { font-weight: 700; font-size: 1.05rem; color: #0F766E; letter-spacing: -0.01em; }

    .hero-title { font-size: 1.9rem; font-weight: 700; color: #1A202C; letter-spacing: -0.02em; margin-bottom: 0.1rem; }
    .hero-pitch { font-size: 1.05rem; color: #4B5563; max-width: 640px; margin: 0.5rem 0 0.25rem 0; line-height: 1.5; }

    .metric-row { display: flex; gap: 0.75rem; flex-wrap: wrap; margin: 0.75rem 0; }
    .metric-card {
        flex: 1 1 140px; background: #F8FAFA; border: 1px solid #E3EAEA;
        border-radius: 12px; padding: 0.85rem 1rem;
    }
    .metric-label {
        font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em;
        color: #7C8896; margin-bottom: 0.3rem; font-weight: 600;
    }
    .metric-value { font-size: 1.5rem; font-weight: 700; color: #1A202C; }

    .badge {
        display: inline-block; font-size: 0.68rem; font-weight: 700;
        padding: 0.14rem 0.55rem; border-radius: 999px; margin-left: 0.45rem;
        vertical-align: middle; text-transform: uppercase; letter-spacing: 0.02em;
    }
    .badge-success { background: #DCFCE7; color: #15803D; }
    .badge-warning { background: #FEF3C7; color: #B45309; }
    .badge-danger  { background: #FEE2E2; color: #B91C1C; }
    .badge-neutral { background: #F3F4F6; color: #4B5563; }

    .patient-name { font-size: 1.35rem; font-weight: 700; color: #1A202C; margin-bottom: 0.4rem; }

    .alert-banner {
        display: flex; align-items: flex-start; gap: 0.6rem;
        border-radius: 10px; padding: 0.7rem 1rem; margin: 0 0 0.6rem 0;
        font-size: 0.88rem; font-weight: 500; line-height: 1.4;
    }
    .alert-banner svg { flex-shrink: 0; margin-top: 0.15rem; }
    .alert-warning { background: #FEF3C7; border: 1px solid #FDE68A; color: #92400E; }
    .alert-danger  { background: #FEE2E2; border: 1px solid #FECACA; color: #991B1B; }

    .hero-stat-row { display: flex; gap: 1rem; flex-wrap: wrap; margin: 1.4rem 0; }
    .hero-stat {
        flex: 1 1 160px; background: #F8FAFA; border: 1px solid #E3EAEA;
        border-radius: 14px; padding: 1.15rem 1.35rem;
    }
    .hero-stat-value { font-size: 2rem; font-weight: 700; color: #0F766E; }
    .hero-stat-label {
        font-size: 0.78rem; color: #7C8896; margin-top: 0.2rem;
        text-transform: uppercase; letter-spacing: 0.04em; font-weight: 600;
    }

    .feature-grid { display: flex; gap: 1rem; flex-wrap: wrap; margin: 0.5rem 0 1.5rem 0; }
    .feature-card {
        flex: 1 1 220px; border: 1px solid #E3EAEA; border-radius: 14px;
        padding: 1.1rem 1.25rem; background: #FFFFFF;
    }
    .feature-card-title { font-weight: 700; font-size: 0.95rem; color: #1A202C; margin: 0.55rem 0 0.3rem 0; }
    .feature-card-desc { font-size: 0.83rem; color: #5b6472; line-height: 1.45; }

    .patient-rail {
        border: 1px solid rgba(15, 118, 110, 0.25); background: #F0F9F8;
        border-radius: 10px; padding: 0.6rem 0.8rem; margin: 0.6rem 0 1rem 0;
    }
    .patient-rail-label {
        font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.05em;
        color: #0F766E; font-weight: 700; margin-bottom: 0.15rem;
    }
    .patient-rail-name { font-size: 0.95rem; font-weight: 700; color: #1A202C; }
    .patient-rail-meta { font-size: 0.75rem; color: #5b6472; margin-top: 0.1rem; }

    .activity-row {
        display: flex; align-items: baseline; gap: 0.6rem; flex-wrap: wrap;
        padding: 0.55rem 0; border-bottom: 1px solid #EEF2F2; font-size: 0.85rem;
    }
    .activity-row:last-child { border-bottom: none; }
    .activity-name { font-weight: 600; color: #1A202C; min-width: 140px; }
    .activity-desc { color: #4B5563; flex: 1; }
    .activity-date { color: #9CA3AF; font-size: 0.78rem; white-space: nowrap; }
    </style>
    """,
    unsafe_allow_html=True,
)

BRAND_ICON_SVG = (
    '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" '
    'stroke="#0F766E" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M2 12h3.5l1.8-4.5 3 9 2.4-7.5 1.8 3h7.5"/></svg>'
)
ALERT_ICON_SVG = (
    '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M12 4 2 20h20L12 4z"/><line x1="12" y1="10" x2="12" y2="14"/>'
    '<circle cx="12" cy="17" r="0.6" fill="currentColor" stroke="none"/></svg>'
)
SHIELD_ICON_SVG = (
    '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" '
    'stroke="#0F766E" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6l7-3z"/><path d="M9 12l2 2 4-4"/></svg>'
)
TREND_ICON_SVG = (
    '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" '
    'stroke="#0F766E" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M3 17l5-5 4 4 8-8"/><path d="M15 8h5v5"/></svg>'
)
GRID_ICON_SVG = (
    '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" '
    'stroke="#0F766E" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/>'
    '<rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>'
)


def compute_age(birth_date_str: str) -> int:
    bd = datetime.date.fromisoformat(birth_date_str)
    today = datetime.date.today()
    return today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))


def e(value) -> str:
    """Escapes a value for safe interpolation into raw HTML strings below.
    Needed specifically for free-text fields (patient names, gender) that
    ultimately come from user input via ManualPatientIntake -- everything
    else interpolated in this file (BMI categories, formatted numbers, ages)
    comes from a fixed set of values this code computes itself."""
    return html.escape(str(value)) if value is not None else ""


def bmi_badge(category: str | None) -> str:
    cls = {
        "Normal": "badge-success",
        "Underweight": "badge-warning",
        "Overweight": "badge-warning",
        "Obese": "badge-danger",
    }.get(category, "badge-neutral")
    return f'<span class="badge {cls}">{category or "Unknown"}</span>'


def bp_badge(systolic: int | None, diastolic: int | None) -> str:
    if systolic is None or diastolic is None:
        return '<span class="badge badge-neutral">No data</span>'
    if systolic < 120 and diastolic < 80:
        return '<span class="badge badge-success">Normal</span>'
    if systolic < 130 and diastolic < 80:
        return '<span class="badge badge-warning">Elevated</span>'
    return '<span class="badge badge-danger">High</span>'


def clinical_alerts(bmi_category: str | None, systolic: int | None, diastolic: int | None) -> list[tuple[str, str]]:
    """Returns [(message, level)] for anything worth flagging, worst-first."""
    alerts = []
    if bmi_category == "Obese":
        alerts.append(("BMI is in the Obese range.", "danger"))
    elif bmi_category in ("Underweight", "Overweight"):
        alerts.append((f"BMI is in the {bmi_category} range.", "warning"))
    if systolic is not None and diastolic is not None:
        if systolic >= 140 or diastolic >= 90:
            alerts.append((f"Blood pressure is high ({systolic}/{diastolic} mmHg).", "danger"))
        elif systolic >= 130 or diastolic >= 80:
            alerts.append((f"Blood pressure is elevated ({systolic}/{diastolic} mmHg).", "warning"))
    return alerts


def render_alert(message: str, level: str) -> None:
    cls = "alert-danger" if level == "danger" else "alert-warning"
    st.markdown(f'<div class="alert-banner {cls}">{ALERT_ICON_SVG}<div>{message}</div></div>', unsafe_allow_html=True)


def metric_card(label: str, value: str, badge_html: str = "") -> str:
    return (
        f'<div class="metric-card"><div class="metric-label">{label}</div>'
        f'<div class="metric-value">{value}{badge_html}</div></div>'
    )


def render_metric_row(cards: list[str]) -> None:
    st.markdown(f'<div class="metric-row">{"".join(cards)}</div>', unsafe_allow_html=True)


def hero_stat(label: str, value: str) -> str:
    return (
        f'<div class="hero-stat"><div class="hero-stat-value">{value}</div>'
        f'<div class="hero-stat-label">{label}</div></div>'
    )


def feature_card(icon_svg: str, title: str, description: str) -> str:
    return (
        f'<div class="feature-card">{icon_svg}<div class="feature-card-title">{title}</div>'
        f'<div class="feature-card-desc">{description}</div></div>'
    )


def styled_line_chart(df: pd.DataFrame, y_columns: dict[str, str]) -> go.Figure:
    """
    y_columns maps {column_name: display_color}. Used for both the
    single-series weight trend and the two-series BP trend so the two charts
    share one consistent look (transparent background, light gridlines,
    accent-colored lines with markers) instead of default st.line_chart bars.
    """
    fig = go.Figure()
    for column, color in y_columns.items():
        fig.add_trace(
            go.Scatter(
                x=df.index, y=df[column], mode="lines+markers", name=column,
                line=dict(color=color, width=2.5), marker=dict(size=6, color=color),
            )
        )
    fig.update_layout(
        height=270,
        margin=dict(l=10, r=10, t=10, b=10),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(showgrid=False),
        yaxis=dict(showgrid=True, gridcolor=GRID, zeroline=False),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0)
        if len(y_columns) > 1 else dict(visible=False),
        showlegend=len(y_columns) > 1,
    )
    return fig


def fetch_patient_by_id(patient_id: str) -> dict | None:
    try:
        response = requests.get(f"{API_BASE_URL}/api/patients/{patient_id}", timeout=10)
    except requests.ConnectionError:
        st.error(f"Couldn't reach the API at {API_BASE_URL}.")
        return None
    return response.json() if response.status_code == 200 else None


def submit_visit(identity: dict, clinical: dict, force_new: bool) -> dict | None:
    """
    POST one visit to the manual-entry endpoint, render the outcome, and
    return the parsed response on success (or None on any failure) so the
    caller can decide how to refresh the on-screen patient state.
    """
    payload = {**identity, **clinical, "force_new": force_new}
    try:
        response = requests.post(
            f"{API_BASE_URL}/api/patients/manual-entry",
            json=payload,
            headers={"X-API-Key": API_KEY} if API_KEY else {},
            timeout=10,
        )
    except requests.ConnectionError:
        st.error(
            f"Couldn't reach the API at {API_BASE_URL}. "
            "Is `uvicorn api.main:app --reload` running?"
        )
        return None

    if response.status_code == 201:
        data = response.json()
        verb = "Updated existing" if data["matched_existing_patient"] else "Created new"
        st.success(
            f"{verb} patient record. BMI: {data['bmi']} ({data['bmi_category']}). "
            f"{data['observations_recorded']} observation(s) recorded."
        )
        return data
    elif response.status_code == 401:
        st.error(
            "The API rejected this write (401 -- missing or invalid API key). "
            "Set API_KEY in your .env to match the API's."
        )
    elif response.status_code == 422:
        for err in response.json().get("detail", []):
            st.error(err.get("msg", "Validation error."))
    else:
        st.error(f"Unexpected error ({response.status_code}): {response.text}")
    return None


with st.sidebar:
    st.markdown(
        f'<div class="brand-row">{BRAND_ICON_SVG}<span class="brand-title">Clinical Data Platform</span></div>',
        unsafe_allow_html=True,
    )
    # A key here (rather than relying on the bare return value) is what lets
    # the patient-rail button below programmatically jump the nav to
    # "Patient Chart" -- it just writes this same session_state key and reruns.
    view = st.radio(
        "View", ["Home", "Patient Chart", "Cohort Dashboards", "Recent Patients"],
        label_visibility="collapsed",
        key="nav_view",
    )

    _rail_patient = st.session_state.get("active_patient")
    if _rail_patient:
        _rail_age = compute_age(_rail_patient["birth_date"])
        _rail_is_new = not (_rail_patient["found"] and not _rail_patient["treat_as_new"])
        _rail_label = "New chart (unsaved)" if _rail_is_new else "Active patient"
        st.markdown(
            f'<div class="patient-rail">'
            f'<div class="patient-rail-label">{_rail_label}</div>'
            f'<div class="patient-rail-name">{e(_rail_patient["first_name"])} {e(_rail_patient["last_name"])}</div>'
            f'<div class="patient-rail-meta">{_rail_age} yr &middot; born {_rail_patient["birth_date"]}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        # Clicking this jumps straight to Patient Chart. It always lands on
        # the Overview tab too: st.tabs() resets to its first tab whenever the
        # Patient Chart branch is freshly mounted, which is exactly what
        # happens when nav_view flips from some other view to this one.
        if view != "Patient Chart":
            if st.button("Open chart →", key="rail_open_chart", use_container_width=True):
                st.session_state.nav_view = "Patient Chart"
                st.rerun()

if "active_patient" not in st.session_state:
    # None until a lookup runs; once set: first_name/last_name/birth_date (what
    # was searched), found (bool), snapshot (PatientSnapshot dict or None),
    # and treat_as_new (the doctor explicitly said "different person" despite
    # a name+DOB match).
    st.session_state.active_patient = None

st.markdown(f'<div class="hero-title">{view}</div>', unsafe_allow_html=True)


# Home: what the tool is and a live pulse of what's tracked, before diving
# into any one workflow.
if view == "Home":
    st.markdown(
        '<div class="hero-pitch">A FHIR-based clinical data platform: look up a patient, '
        'confirm their identity before merging any records, chart their vitals over time, '
        'and see the whole cohort at a glance.</div>',
        unsafe_allow_html=True,
    )
    # /api/stats aggregates in SQL, so this reflects every row in the table --
    # not just whatever page of patients happened to be fetched client-side.
    try:
        with st.spinner("Loading stats..."):
            stats_response = requests.get(f"{API_BASE_URL}/api/stats", timeout=10)
        home_stats = stats_response.json() if stats_response.status_code == 200 else None
    except requests.ConnectionError:
        home_stats = None
        st.error(f"Couldn't reach the API at {API_BASE_URL}.")

    total_patients = home_stats["total_patients"] if home_stats else 0
    avg_bmi_home = home_stats["avg_bmi"] if home_stats else None

    st.markdown(
        '<div class="hero-stat-row">'
        + hero_stat("Patients tracked", str(total_patients))
        + hero_stat("Average BMI", f"{avg_bmi_home:.1f}" if avg_bmi_home else "—")
        + hero_stat("Data sources", "FHIR + manual intake")
        + "</div>",
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="feature-grid">'
        + feature_card(
            SHIELD_ICON_SVG, "Identity-safe intake",
            "Matches patients by name and birth date, but a person confirms before two "
            "different people are ever merged into one chart.",
        )
        + feature_card(
            TREND_ICON_SVG, "Vitals trending",
            "Weight and blood pressure charted over time for every patient, pulled "
            "straight from their observation history.",
        )
        + feature_card(
            GRID_ICON_SVG, "Cohort analytics",
            "A Tableau-backed view of the full patient population: BMI distribution, "
            "age spread, and blood pressure clustering.",
        )
        + "</div>",
        unsafe_allow_html=True,
    )

    st.markdown("##### Recent activity")
    try:
        activity_response = requests.get(
            f"{API_BASE_URL}/api/patients/recent-activity", params={"limit": 6}, timeout=10
        )
        recent_activity = activity_response.json() if activity_response.status_code == 200 else []
    except requests.ConnectionError:
        recent_activity = []

    if recent_activity:
        with st.container(border=True):
            for entry in recent_activity:
                name = (
                    f"{entry['first_name']} {entry['last_name']}"
                    if entry.get("first_name") else "Unnamed patient"
                )
                desc = entry.get("observation_description") or "Observation"
                value = entry.get("observation_value")
                unit = entry.get("observation_unit") or ""
                value_str = f"{value} {unit}".strip() if value is not None else "recorded"
                date_str = entry.get("observation_date") or ""
                st.markdown(
                    f'<div class="activity-row">'
                    f'<span class="activity-name">{e(name)}</span>'
                    f'<span class="activity-desc">{e(desc)}: {e(value_str)}</span>'
                    f'<span class="activity-date">{e(date_str)}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
    else:
        st.caption("No visits recorded yet -- log one from Patient Chart to see it here.")


# Patient Chart: the primary, doctor-facing view: look a patient up, see
# who they are and their vitals trend, then chart today's visit against that
# context.
elif view == "Patient Chart":
    st.caption(
        "Look up a patient by name and birth date to open their chart, "
        "or search for someone new to start one."
    )
    with st.form("patient_lookup_form"):
        col1, col2, col3 = st.columns(3)
        with col1:
            lookup_first = st.text_input("First name")
        with col2:
            lookup_last = st.text_input("Last name")
        with col3:
            lookup_dob = st.date_input(
                "Birth date",
                value=datetime.date(1990, 1, 1),
                min_value=datetime.date(1900, 1, 1),
                max_value=datetime.date.today(),
            )
        look_up_submitted = st.form_submit_button("Look Up")

    if look_up_submitted:
        if not lookup_first.strip() or not lookup_last.strip():
            st.warning("Enter a first and last name to look up a patient.")
        else:
            try:
                with st.spinner("Looking up patient..."):
                    lookup_response = requests.get(
                        f"{API_BASE_URL}/api/patients/lookup",
                        params={
                            "first_name": lookup_first,
                            "last_name": lookup_last,
                            "birth_date": lookup_dob.isoformat(),
                        },
                        timeout=10,
                    )
            except requests.ConnectionError:
                st.error(
                    f"Couldn't reach the API at {API_BASE_URL}. "
                    "Is `uvicorn api.main:app --reload` running?"
                )
            else:
                if lookup_response.status_code == 200:
                    data = lookup_response.json()
                    st.session_state.active_patient = {
                        "first_name": lookup_first.strip(),
                        "last_name": lookup_last.strip(),
                        "birth_date": lookup_dob.isoformat(),
                        "found": data["match_found"],
                        "snapshot": data["patient"],
                        "treat_as_new": False,
                    }
                else:
                    st.error(f"Unexpected error ({lookup_response.status_code}).")

    active = st.session_state.active_patient

    if active is None:
        st.info("Look up a patient above to open their chart.")
    else:
        st.divider()
        showing_existing = active["found"] and not active["treat_as_new"]

        if showing_existing:
            snap = active["snapshot"]
            age = compute_age(active["birth_date"])

            tab_overview, tab_vitals, tab_visit = st.tabs(["Overview", "Vitals Trends", "Record Visit"])
            visit_container = tab_visit

            with tab_overview:
                with st.container(border=True):
                    st.markdown(
                        f'<div class="patient-name">{e(snap["first_name"])} {e(snap["last_name"])}</div>',
                        unsafe_allow_html=True,
                    )
                    bmi_value = f"{snap['bmi']}" if snap["bmi"] is not None else "—"
                    bp_value = (
                        f"{snap['latest_systolic_bp']}/{snap['latest_diastolic_bp']}"
                        if snap["latest_systolic_bp"] is not None else "—"
                    )
                    render_metric_row([
                        metric_card("Age", str(age)),
                        metric_card("Gender", e((snap["gender"] or "—").title())),
                        metric_card("Latest BMI", bmi_value, bmi_badge(snap["bmi_category"]) if snap["bmi"] is not None else ""),
                        metric_card("Latest BP", bp_value, bp_badge(snap["latest_systolic_bp"], snap["latest_diastolic_bp"])),
                    ])
                    for message, level in clinical_alerts(
                        snap["bmi_category"], snap["latest_systolic_bp"], snap["latest_diastolic_bp"]
                    ):
                        render_alert(message, level)
                    if st.button("Not the same person — start a new chart instead"):
                        st.session_state.active_patient["treat_as_new"] = True
                        st.rerun()

            try:
                with st.spinner("Loading vitals history..."):
                    obs_response = requests.get(
                        f"{API_BASE_URL}/api/patients/{snap['id']}/observations", timeout=10
                    )
            except requests.ConnectionError:
                obs_response = None

            with tab_vitals:
                if obs_response is not None and obs_response.status_code == 200:
                    history = obs_response.json()
                    if history:
                        hist_df = pd.DataFrame(history)
                        hist_df["observation_date"] = pd.to_datetime(hist_df["observation_date"])

                        chart_col1, chart_col2 = st.columns(2)
                        with chart_col1:
                            st.caption("Weight over time (kg)")
                            weight_df = hist_df[hist_df["observation_code"] == "29463-7"]
                            if not weight_df.empty:
                                weight_series = weight_df.set_index("observation_date")[["observation_value"]]
                                weight_series.columns = ["Weight (kg)"]
                                st.plotly_chart(
                                    styled_line_chart(weight_series, {"Weight (kg)": ACCENT}),
                                    use_container_width=True,
                                )
                            else:
                                st.info("No weight history on file yet.")
                        with chart_col2:
                            st.caption("Blood pressure over time (mmHg)")
                            bp_df = hist_df[hist_df["observation_code"].isin(["8480-6", "8462-4"])]
                            if not bp_df.empty:
                                bp_pivot = bp_df.pivot_table(
                                    index="observation_date",
                                    columns="observation_description",
                                    values="observation_value",
                                )
                                colors = {
                                    "Systolic Blood Pressure": ACCENT,
                                    "Diastolic Blood Pressure": ACCENT_SOFT,
                                }
                                st.plotly_chart(
                                    styled_line_chart(
                                        bp_pivot,
                                        {c: colors.get(c, ACCENT) for c in bp_pivot.columns},
                                    ),
                                    use_container_width=True,
                                )
                            else:
                                st.info("No blood pressure history on file yet.")
                    else:
                        st.info("No observation history on file yet for this patient.")
                else:
                    st.info("Vitals history unavailable right now.")
        else:
            st.info(
                f"Starting a new chart for **{active['first_name']} {active['last_name']}** "
                f"(born {active['birth_date']})."
            )
            if active["found"] and active["treat_as_new"]:
                if st.button("Actually, that was the same person"):
                    st.session_state.active_patient["treat_as_new"] = False
                    st.rerun()
            visit_container = st.container()

        with visit_container:
            st.markdown("##### Record a visit")
            gender_options = ["", "male", "female", "other"]
            default_gender = ""
            if showing_existing and active["snapshot"]["gender"] in gender_options:
                default_gender = active["snapshot"]["gender"]

            with st.form("visit_form", clear_on_submit=True):
                col1, col2 = st.columns(2)
                with col1:
                    gender = st.selectbox(
                        "Gender", gender_options, index=gender_options.index(default_gender)
                    )
                    height_cm = st.number_input(
                        "Height (cm)", min_value=0.0, max_value=300.0, value=0.0, step=0.1
                    )
                    systolic_bp = st.number_input("Systolic BP", min_value=0, max_value=300, value=0)
                with col2:
                    weight_kg = st.number_input(
                        "Weight (kg)", min_value=0.0, max_value=500.0, value=0.0, step=0.1
                    )
                    diastolic_bp = st.number_input("Diastolic BP", min_value=0, max_value=200, value=0)
                observation_date = st.date_input("Visit date", value=datetime.date.today())
                visit_submitted = st.form_submit_button("Record Visit")

            if visit_submitted:
                identity = {
                    "first_name": active["first_name"],
                    "last_name": active["last_name"],
                    "birth_date": active["birth_date"],
                }
                clinical = {
                    "gender": gender or None,
                    "height_cm": height_cm if height_cm > 0 else None,
                    "weight_kg": weight_kg if weight_kg > 0 else None,
                    "systolic_bp": int(systolic_bp) if systolic_bp > 0 else None,
                    "diastolic_bp": int(diastolic_bp) if diastolic_bp > 0 else None,
                    "observation_date": observation_date.isoformat(),
                }
                force_new = active["found"] and active["treat_as_new"]
                with st.spinner("Saving visit..."):
                    result = submit_visit(identity, clinical, force_new)
                if result is not None:
                    # Refresh by the exact patient id the write just returned,
                    # never by re-running the name+DOB lookup.
                    refreshed_snapshot = fetch_patient_by_id(result["patient_id"])
                    st.session_state.active_patient = {
                        "first_name": active["first_name"],
                        "last_name": active["last_name"],
                        "birth_date": active["birth_date"],
                        "found": True,
                        "snapshot": refreshed_snapshot,
                        "treat_as_new": False,
                    }
                    st.rerun()


# Cohort Dashboards: the population-level Tableau embed.
elif view == "Cohort Dashboards":
    st.caption(
        "Live Tableau Public dashboards. Use the tabs inside the embedded "
        "view to switch between the cohort-level and per-patient dashboards."
    )
    left, center, right = st.columns([1, 10, 1])
    with center:
        with st.container(border=True):
            st.iframe(TABLEAU_EMBED_URL, height=850)
    st.markdown(f"[Open full dashboard in a new tab]({TABLEAU_PUBLIC_LINK})")


# Recent Patients: a sortable view of everyone on file.
elif view == "Recent Patients":
    st.caption("Live query against the patients table -- reflects new entries immediately.")
    if st.button("Refresh"):
        st.rerun()
    try:
        with st.spinner("Loading patients..."):
            response = requests.get(
                f"{API_BASE_URL}/api/patients",
                params={"limit": 100, "order_by": "created_at"},
                timeout=10,
            )
    except requests.ConnectionError:
        st.error(
            f"Couldn't reach the API at {API_BASE_URL}. "
            "Is `uvicorn api.main:app --reload` running?"
        )
    else:
        if response.status_code == 200:
            patients = response.json()
            if patients:
                df = pd.DataFrame(patients)
                avg_bmi = df["bmi"].dropna().mean()
                most_recent = pd.to_datetime(df["created_at"]).max()
                render_metric_row([
                    metric_card("Patients shown", str(len(df))),
                    metric_card("Average BMI", f"{avg_bmi:.1f}" if pd.notna(avg_bmi) else "—"),
                    metric_card("Most recent entry", most_recent.strftime("%b %d, %Y")),
                ])

                cat_order = ["Underweight", "Normal", "Overweight", "Obese"]
                cat_colors = {
                    "Underweight": "#D97706", "Normal": "#16A34A",
                    "Overweight": "#EA580C", "Obese": "#DC2626",
                }
                counts = df["bmi_category"].value_counts()
                counts = counts.reindex(cat_order).fillna(0)
                st.caption("BMI category breakdown")
                fig = go.Figure(
                    go.Bar(
                        x=counts.index, y=counts.values,
                        marker_color=[cat_colors[c] for c in counts.index],
                    )
                )
                fig.update_layout(
                    height=220, margin=dict(l=10, r=10, t=10, b=10),
                    plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                    yaxis=dict(showgrid=True, gridcolor=GRID, zeroline=False),
                    xaxis=dict(showgrid=False),
                )
                st.plotly_chart(fig, use_container_width=True)

                st.dataframe(
                    df,
                    width="stretch",
                    hide_index=True,
                    column_config={
                        "id": st.column_config.TextColumn("Patient ID", width="small"),
                        "first_name": "First Name",
                        "last_name": "Last Name",
                        "gender": "Gender",
                        "birth_date": st.column_config.DateColumn("Birth Date"),
                        "height_cm": st.column_config.NumberColumn("Height (cm)", format="%.1f"),
                        "weight_kg": st.column_config.NumberColumn("Weight (kg)", format="%.1f"),
                        "bmi": st.column_config.NumberColumn("BMI", format="%.1f"),
                        "bmi_category": "BMI Category",
                        "latest_systolic_bp": "Systolic BP",
                        "latest_diastolic_bp": "Diastolic BP",
                        "created_at": st.column_config.DatetimeColumn(
                            "Added", format="MMM D, YYYY h:mm a"
                        ),
                    },
                )
            else:
                st.info("No patients found.")
        else:
            st.error(f"Unexpected error ({response.status_code}): {response.text}")
