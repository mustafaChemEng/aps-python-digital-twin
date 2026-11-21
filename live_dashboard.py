"""
Stage 5 - live dashboard for the APS tank digital twin
------------------------------------------------------
Reads twin.db (written by twin_loop.py) once per second and redraws. It never talks to APS,
so it cannot disturb the control loop.

Usage (second Command Prompt, (twin) env active, in the twin_project folder):
    python live_dashboard.py            -> follows the newest run, switches when a new run starts
    python live_dashboard.py RUN_ID     -> shows one stored run (e.g. closed_20261121_031500)
Close the window to stop.
"""
import sys
import json
import time
import sqlite3

import matplotlib.pyplot as plt

DB = "twin.db"
REFRESH = 1.0      # s between redraws
TANK_H = 2.0       # m, Tank.L, to show errors in cm


def latest_run(db):
    row = db.execute("SELECT run_id FROM runs ORDER BY started DESC, rowid DESC LIMIT 1").fetchone()
    return row[0] if row else None


def run_info(db, run_id):
    return db.execute("SELECT mode, dt, kc, taui, t_dist, t_sp, ended, kpi FROM runs WHERE run_id=?",
                      (run_id,)).fetchone()


def main():
    fixed = sys.argv[1] if len(sys.argv) > 1 else None
    db = None
    while db is None:
        try:
            db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
            db.execute("SELECT 1 FROM runs LIMIT 1")
        except sqlite3.Error:
            db = None
            print("Waiting for twin.db - start twin_loop.py ...")
            time.sleep(2)

    plt.ion()
    fig = plt.figure(figsize=(12, 8))
    gs = fig.add_gridspec(3, 4)
    ax_l = fig.add_subplot(gs[0, :3])
    ax_u = fig.add_subplot(gs[1, :3], sharex=ax_l)
    ax_f = fig.add_subplot(gs[2, :3], sharex=ax_l)
    ax_k = fig.add_subplot(gs[:, 3])
    ax_k.axis("off")

    l_lvl, = ax_l.plot([], [], label="level")
    l_sp, = ax_l.plot([], [], "--", label="setpoint")
    l_uc, = ax_u.plot([], [], label="V2 command")
    l_up, = ax_u.plot([], [], label="V2 actual")
    l_fi, = ax_f.plot([], [], label="inflow")
    l_fo, = ax_f.plot([], [], label="outflow")
    ax_l.set_ylabel("level [fraction of 2 m]")
    ax_u.set_ylabel("valve opening [-]")
    ax_f.set_ylabel("flow [kg/s]")
    ax_f.set_xlabel("simulated time [s]")
    for a in (ax_l, ax_u, ax_f):
        a.grid(alpha=0.3)
        a.legend(loc="upper left")
    kpi_text = ax_k.text(0.0, 1.0, "", va="top", family="monospace", fontsize=10)
    markers = []

    run_id, data, last_t = None, [], -1.0
    while plt.fignum_exists(fig.number):
        target = fixed or latest_run(db)
        if target != run_id:                       # new run started (or first start): reset
            run_id, data, last_t = target, [], -1.0
            for m in markers:
                m.remove()
            markers = []
            info = run_info(db, run_id)
            if info:
                for tm in (info[4], info[5]):
                    if tm is not None:
                        markers += [a.axvline(tm, color="grey", lw=0.8) for a in (ax_l, ax_u, ax_f)]
            fig.suptitle(f"APS tank digital twin - run {run_id}")

        new = db.execute("SELECT t, level, sp, u_cmd, u_pos, fin, fout, err, iae, wall FROM readings "
                         "WHERE run_id=? AND t>? ORDER BY t", (run_id, last_t)).fetchall()
        if new:
            data += new
            last_t = new[-1][0]
            cols = list(zip(*data))
            t = cols[0]
            l_lvl.set_data(t, cols[1]); l_sp.set_data(t, cols[2])
            l_uc.set_data(t, cols[3]); l_up.set_data(t, cols[4])
            l_fi.set_data(t, cols[5]); l_fo.set_data(t, cols[6])
            for a in (ax_l, ax_u, ax_f):
                a.relim(); a.autoscale_view()

        info = run_info(db, run_id)
        if data and info:
            r = data[-1]
            mode, dt, kc, taui, t_dist, t_sp, ended, kpi = info
            lines = [f"Run     {run_id}", f"Mode    {mode.upper()}", f"Status  {'FINISHED' if ended else 'RUNNING'}", "",
                     f"t       {r[0]:7.0f} s", f"Level   {r[1]:7.4f}", f"SP      {r[2]:7.2f}",
                     f"Error   {r[7] * TANK_H * 100:+7.1f} cm", f"V2 cmd  {r[3]:7.3f}",
                     f"Fin     {r[5]:7.3f} kg/s", f"Fout    {r[6]:7.3f} kg/s", f"IAE     {r[8]:7.2f}",
                     f"Speed   {r[0] / max(r[9], 1e-6):7.2f}x real", ""]
            if kc:
                lines += [f"PI Kc   {kc:7.2f}", f"PI tauI {taui:7.0f} s", ""]
            if ended and kpi:
                lines += ["FINAL KPIs"] + [f"{k:<13}{v}" for k, v in json.loads(kpi).items()]
            kpi_text.set_text("\n".join(lines))
        elif not data:
            kpi_text.set_text(f"Run {run_id}\nwaiting for data ...")

        plt.pause(REFRESH)


if __name__ == "__main__":
    main()
