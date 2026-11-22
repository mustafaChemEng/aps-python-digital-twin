"""
Stage 5b - browser dashboard for the APS tank digital twin (Streamlit)
----------------------------------------------------------------------
Reads twin.db (written by twin_loop.py) and refreshes every second in your browser.
Read-only: it never talks to APS, so it cannot disturb the control loop.

Setup (once, in the (twin) environment):
    pip install streamlit
Run (in the twin_project folder):
    streamlit run dashboard_web.py
It opens http://localhost:8501 in your browser. Stop with Ctrl+C in the window.
"""
import json
import sqlite3

import pandas as pd
import streamlit as st

DB = "twin.db"
TANK_H = 2.0          # m, Tank.L - converts level fraction errors to cm

st.set_page_config(page_title="APS Digital Twin", layout="wide")


def query(sql, params=()):
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        try:
            return pd.read_sql_query(sql, con, params=params)
        finally:
            con.close()
    except Exception:
        return pd.DataFrame()


# ---------------- header and sidebar ----------------
st.title("APS tank digital twin")
st.caption("AVEVA Process Simulation 2025 dynamic model  →  Python PI control  →  SQLite  →  this dashboard")

runs = query("SELECT run_id, mode, started, ended, kc, taui, t_dist, t_sp, kpi FROM runs ORDER BY started DESC")
with st.sidebar:
    st.header("Run")
    follow = st.toggle("Follow newest run", value=True)
    chosen = None
    if not follow and not runs.empty:
        chosen = st.selectbox("Stored run", runs["run_id"].tolist())
    st.divider()
    st.markdown("**Loop**  \nEvery 2 s: APS runs → Python reads `Tank.Level` → PI decides → writes `V2.ManPos`")
    st.markdown("**Test**  \n+12 % inflow at t = 120 s  \nSetpoint 0.50 → 0.60 at t = 600 s")


@st.fragment(run_every="1s")
def live():
    all_runs = query("SELECT run_id, mode, started, ended, kc, taui, t_dist, t_sp, kpi FROM runs ORDER BY started DESC")
    if all_runs.empty:
        st.info("Waiting for twin.db - start  python twin_loop.py closed  in a Command Prompt.")
        return
    run_id = chosen if chosen else all_runs.iloc[0]["run_id"]
    info = all_runs[all_runs["run_id"] == run_id].iloc[0]
    data = query("SELECT t, level, sp, u_cmd, u_pos, fin, fout, err, iae, wall FROM readings "
                 "WHERE run_id = ? ORDER BY t", (run_id,))

    status = "FINISHED" if info["ended"] else "RUNNING"
    st.subheader(f"{run_id}  ·  {str(info['mode']).upper()}  ·  {status}")
    if data.empty:
        st.write("Waiting for the first step ...")
        return

    r = data.iloc[-1]
    c = st.columns(8)
    c[0].metric("Sim time", f"{r.t:.0f} s")
    c[1].metric("Level", f"{r.level:.4f}")
    c[2].metric("Setpoint", f"{r.sp:.2f}")
    c[3].metric("Error", f"{r.err * TANK_H * 100:+.1f} cm")
    c[4].metric("V2 command", f"{r.u_cmd:.3f}")
    c[5].metric("Inflow", f"{r.fin:.3f} kg/s")
    c[6].metric("Outflow", f"{r.fout:.3f} kg/s")
    c[7].metric("Speed", f"{r.t / max(r.wall, 1e-6):.1f}x real")

    d = data.set_index("t")
    left, right = st.columns(2)
    with left:
        st.markdown("**Level vs setpoint** (fraction of 2 m)")
        st.line_chart(d[["level", "sp"]].rename(columns={"level": "level", "sp": "setpoint"}),
                      x_label="simulated time [s]", height=260)
        st.markdown("**Flows** (kg/s)")
        st.line_chart(d[["fin", "fout"]].rename(columns={"fin": "inflow", "fout": "outflow"}),
                      x_label="simulated time [s]", height=260)
    with right:
        st.markdown("**Outlet valve V2** (opening, 0-1)")
        st.line_chart(d[["u_cmd", "u_pos"]].rename(columns={"u_cmd": "command", "u_pos": "actual"}),
                      x_label="simulated time [s]", height=260)
        st.markdown("**Level error** (cm)")
        st.line_chart((d["err"] * TANK_H * 100).rename("error"), x_label="simulated time [s]", height=260)

    if info["kc"] is not None and pd.notna(info["kc"]):
        st.caption(f"PI tuning (SIMC, from the plant's own values): Kc = {info['kc']:.2f}, tauI = {info['taui']:.0f} s")
    if info["ended"] and info["kpi"]:
        st.markdown("**Final KPIs**")
        st.table(pd.DataFrame(json.loads(info["kpi"]).items(), columns=["KPI", "value"]))

    done = all_runs[all_runs["kpi"].notna() & (all_runs["mode"] != "init")]
    if not done.empty:
        rows = []
        for _, x in done.iterrows():
            k = json.loads(x["kpi"])
            rows.append({"run": x["run_id"], "mode": x["mode"], "max error [cm]": k.get("max_dev_cm"),
                         "IAE disturbance": k.get("iae_dist"), "settling": k.get("settling"),
                         "valve travel": k.get("valve_travel")})
        with st.expander("Compare all finished runs"):
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


live()
