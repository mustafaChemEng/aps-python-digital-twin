"""
Stage 3-4: closed-loop level control of an APS dynamic tank from Python
=======================================================================
Plant  : The_Tank_Model_Test  (Source -> V1 -> Tank -> V2 -> SNK1), APS 2025, Dynamics mode
Python : reads Tank.Level, writes V2.ManPos (PI controller, velocity form), logs to CSV, plots, KPIs

Usage (APS open, (twin) env active, snapshot 'start_dyn' saved in Dynamics mode):
    python twin_loop.py closed     -> Python controls the level
    python twin_loop.py open       -> valve held fixed (baseline, to compare)
    python twin_loop.py init       -> Python holds level at 0.50 for 1500 s, no disturbance, then, if steady,
                                      OVERWRITES snapshot 'start_dyn' with that true steady state
PI form: proportional action on the MEASUREMENT (not the error), so a setpoint step gives no valve kick;
the setpoint reaches the valve only through the integral term.

Data store: every step is also written to SQLite file twin.db (tables runs, readings);
run  python live_dashboard.py  in a second window to watch it live.
Units: APS internal SI. Level = fraction of tank height (Tank.L = 2 m). Flow kg/s. Pressure kPa. Time s.
Confirmed on this machine (21 Nov 2026): RunDynamics(sim, dt) blocks until done, and dt is a DURATION.
"""
import sys
import csv
import sqlite3
import json
import math
import time
import traceback
from datetime import datetime

import simcentralconnect
from System import Array, String

# ---------------- settings ----------------
SIM = "The_Tank_Model_Test"
SNAPSHOT = "start_dyn"
DT = 2.0                  # s of simulated time per loop step
T_END = 1200.0            # s total simulated time (20 min)
SP0, SP1, T_SP = 0.50, 0.60, 600.0   # level setpoint [fraction]; steps 0.50 -> 0.60 at t = 600 s
T_DIST, P_DIST = 120.0, 350.0        # at t = 120 s, Source.P 300 -> 350 kPa (about +12 % inflow)
TAU_C = 60.0              # s, chosen closed-loop time constant (SIMC rule for an integrating process)
THETA = 6.0               # s, effective dead time = valve actuator lag (Tc = 5 s) + DT/2
U_MIN, U_MAX = 0.05, 1.0  # valve command limits [fraction]
BAND = 0.01               # settling band: |error| < 0.01 (= 2 cm on a 2 m tank)

READ = ["Tank.Level", "V2.ManPos", "V2.Pos", "V1.W", "V2.W", "Source.P"]


def num(x):
    return float(str(x))


def member(obj, name):
    for n in (name, name[0].upper() + name[1:]):
        if hasattr(obj, n):
            return num(getattr(obj, n))
    t = obj.GetType()
    for m in list(t.GetProperties()) + list(t.GetFields()):
        if m.Name.lower() == name.lower():
            return num(m.GetValue(obj))
    raise AttributeError(name)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "closed"
    t_end = 1500.0 if mode == "init" else T_END
    sc = simcentralconnect.connect({'ClientApp': 'twin_loop.py'}).Result
    sc.SetOptions(json.dumps({'EnableApiLogging': 'false'}))
    sm = sc.GetService("ISimulationManager")
    vm = sc.GetService("IVariableManager")
    ssm = sc.GetService("ISnapshotManager")

    def read(names):
        return [num(v) for v in vm.GetVariableValue(SIM, Array[String](names)).Result]

    def write(name, value):
        vm.SetVariableValue(SIM, name, float(value)).Result

    def sim_time():
        return member(sm.GetSimulationTimeDetails(SIM).Result, "timeVariableValue")

    # ---- open, reset, check mode ----
    if SIM not in [str(s) for s in sm.GetOpenSimulations().Result]:
        sm.OpenSimulation(SIM).Result
    try:
        ok = ssm.RevertSnapshot(SIM, SNAPSHOT).Result
        print(f"Reverted to snapshot '{SNAPSHOT}': {ok}")
    except Exception:
        print(f"Snapshot '{SNAPSHOT}' not found. Save it in APS (Dynamics mode, not running) and retry.")
        return
    if str(sm.GetSimulationMode(SIM).Result) != "Dynamics":
        sm.UpdateSimulationMode(SIM, "Dynamics").Result

    # ---- model-based tuning from the plant's own numbers ----
    rho, D, Ltank = read(["Tank.rhol", "Tank.D", "Tank.L"])
    L0, u0, pos0, Fin0, Fout0, P0 = read(READ)
    area = math.pi / 4 * D ** 2                      # m2
    m_per_frac = rho * area * Ltank                  # kg of liquid per 1.0 of level fraction
    dF_du = Fout0 / max(u0, 1e-6)                    # kg/s per unit valve opening (linear valve)
    k = dF_du / m_per_frac                           # 1/s : level fraction per second per unit opening
    Kc = 1.0 / (k * (TAU_C + THETA))                 # SIMC, integrating process
    tauI = 4.0 * (TAU_C + THETA)                     # s
    print(f"Start t = {sim_time():.1f} s | level {L0:.4f} | V2 cmd {u0:.3f} | Fin {Fin0:.4f} kg/s | Fout {Fout0:.4f} kg/s")
    print(f"Plant: area {area:.4f} m2, {m_per_frac:.0f} kg per unit level, dF/du {dF_du:.3f} kg/s, k {k:.3e} 1/s")
    print(f"PI   : Kc {Kc:.2f} (opening per unit level error), tauI {tauI:.0f} s, mode = {mode.upper()}")
    if u0 > 0.9 and mode == "closed":
        print("WARNING: V2 starts above 90 % open - little room to open further. Set V2.ManPos = 0.5 in Process mode.")

    # ---- loop ----
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = f"run_{mode}_{stamp[:-2]}.csv"
    run_id = f"{mode}_{stamp}"
    db = sqlite3.connect("twin.db")
    db.execute("PRAGMA journal_mode=WAL")          # lets the dashboard read while we write
    db.execute("""CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, mode TEXT, started TEXT,
                  ended TEXT, dt REAL, kc REAL, taui REAL, t_dist REAL, t_sp REAL, kpi TEXT)""")
    db.execute("""CREATE TABLE IF NOT EXISTS readings (run_id TEXT, t REAL, level REAL, sp REAL,
                  u_cmd REAL, u_pos REAL, fin REAL, fout REAL, psrc REAL, err REAL, iae REAL, wall REAL)""")
    db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?)",
               (run_id, mode, datetime.now().isoformat(timespec="seconds"), None, DT,
                Kc if mode != "open" else None, tauI if mode != "open" else None,
                None if mode == "init" else T_DIST, None if mode == "init" else T_SP, None))
    db.commit()
    print(f"Logging to twin.db as run '{run_id}'")
    rows, u, e_prev = [], u0, None
    iae, travel, dist_done = 0.0, 0.0, False
    wall0 = time.time()
    t = 0.0
    L_prev = L0
    while t < t_end - 1e-9:
        if mode != "init" and not dist_done and t >= T_DIST:
            write("Source.P", P_DIST)
            dist_done = True
            print(f"t = {t:6.0f} s  DISTURBANCE: Source.P -> {P_DIST} kPa")
        sp = SP1 if (t >= T_SP and mode != "init") else SP0

        sm.RunDynamics(SIM, DT, None).Result          # plant runs DT seconds, then waits
        t += DT
        L, ucmd, pos, Fin, Fout, Psrc = read(READ)
        e = L - sp                                     # direct acting: level high -> open valve

        if mode in ("closed", "init"):
            dL = L - L_prev                            # P on measurement: no kick on setpoint steps
            u_new = min(U_MAX, max(U_MIN, u + Kc * (dL + DT / tauI * e)))
            travel += abs(u_new - u)
            u = u_new
            write("V2.ManPos", u)
        L_prev = L
        iae += abs(e) * DT
        rows.append([t, L, sp, u, pos, Fin, Fout, Psrc, e, iae])
        db.execute("INSERT INTO readings VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                   (run_id, t, L, sp, u, pos, Fin, Fout, Psrc, e, iae, time.time() - wall0))
        db.commit()
        if int(round(t / DT)) % 15 == 0:
            print(f"t = {t:6.0f} s | level {L:.4f} sp {sp:.2f} | V2 cmd {u:.3f} pos {pos:.3f} | "
                  f"Fin {Fin:.3f} Fout {Fout:.3f} kg/s | wall {time.time() - wall0:5.0f} s")

    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_s", "level_frac", "sp_frac", "v2_cmd", "v2_pos", "fin_kgps", "fout_kgps",
                    "psource_kPa", "error_frac", "iae_frac_s"])
        w.writerows(rows)

    # ---- KPIs ----
    wall = time.time() - wall0

    def close_run(kpi):
        db.execute("UPDATE runs SET ended=?, kpi=? WHERE run_id=?",
                   (datetime.now().isoformat(timespec="seconds"), json.dumps(kpi), run_id))
        db.commit()
        db.close()

    if mode == "init":
        close_run({"note": "init"})
        L, ucmd, pos, Fin, Fout, Psrc = read(READ)
        print("\n================ INIT DONE ================")
        print(f"level {L:.4f} | V2 {ucmd:.3f} | Fin {Fin:.4f} | Fout {Fout:.4f} kg/s | imbalance {Fin - Fout:+.4f} kg/s")
        if abs(Fin - Fout) < 0.002 and abs(L - SP0) < 0.002:
            ok = ssm.OverwriteSnapshot(SIM, SNAPSHOT, 60000, False, True).Result
            print(f"Steady. Snapshot '{SNAPSHOT}' overwritten with this state: {ok}")
        else:
            print("NOT steady enough (need |imbalance| < 0.002 kg/s and |level - 0.50| < 0.002).")
            print("Snapshot NOT changed. Run init again - it starts from the old snapshot.")
        return
    dist = [r for r in rows if T_DIST <= r[0] < T_SP]
    max_dev = max(abs(r[8]) for r in dist) if dist else float("nan")
    outside = [r[0] for r in dist if abs(r[8]) > BAND]
    if not outside:
        settle_txt = "0 s (never left the band)"
    elif outside[-1] >= dist[-1][0]:
        settle_txt = "did NOT settle before the setpoint step"
    else:
        settle_txt = f"{outside[-1] - T_DIST:.0f} s after the disturbance"
    print("\n================ KPIs ================")
    print(f"IAE (whole run)              : {iae:.2f} fraction.s")
    print(f"Max |error| after disturbance: {max_dev:.4f}  (= {max_dev * Ltank * 100:.1f} cm)")
    print(f"Settling time (|e| < {BAND})   : {settle_txt}")
    print(f"Total valve travel           : {travel:.3f}")
    print(f"Speed: {t_end:.0f} s simulated in {wall:.0f} s wall time ({t_end / wall:.2f}x real time)")
    print(f"Data saved to {out} and twin.db (run '{run_id}')")
    iae_d = sum(abs(r[8]) * DT for r in dist)
    close_run({"iae_dist": round(iae_d, 3), "max_dev_cm": round(max_dev * Ltank * 100, 2),
               "settling": settle_txt, "valve_travel": round(travel, 3), "speed_x": round(t_end / wall, 2)})

    # ---- plot ----
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        ts = [r[0] for r in rows]
        fig, ax = plt.subplots(3, 1, figsize=(9, 8), sharex=True)
        ax[0].plot(ts, [r[1] for r in rows], label="level")
        ax[0].plot(ts, [r[2] for r in rows], "--", label="setpoint")
        ax[0].set_ylabel("level [fraction of 2 m]")
        ax[1].plot(ts, [r[3] for r in rows], label="V2 command")
        ax[1].plot(ts, [r[4] for r in rows], label="V2 actual")
        ax[1].set_ylabel("valve opening [-]")
        ax[2].plot(ts, [r[5] for r in rows], label="inflow")
        ax[2].plot(ts, [r[6] for r in rows], label="outflow")
        ax[2].set_ylabel("flow [kg/s]")
        ax[2].set_xlabel("time [s]")
        for a in ax:
            a.axvline(T_DIST, color="grey", lw=0.8)
            a.axvline(T_SP, color="grey", lw=0.8)
            a.legend(loc="best")
            a.grid(alpha=0.3)
        fig.suptitle(f"APS tank, {mode} loop: disturbance at {T_DIST:.0f} s, setpoint step at {T_SP:.0f} s")
        fig.tight_layout()
        fig.savefig(out.replace(".csv", ".png"), dpi=120)
        print(f"Plot saved to {out.replace('.csv', '.png')}")
    except Exception as ex:
        print("Plot skipped:", ex)

    ssm.RevertSnapshot(SIM, SNAPSHOT).Result           # leave the model clean for the next run
    print(f"Model reverted to '{SNAPSHOT}'.")


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
