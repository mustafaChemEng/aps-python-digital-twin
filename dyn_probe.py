"""
Stage 2a - Dynamics probe for AVEVA Process Simulation 2025
------------------------------------------------------------
Goal: find out, on YOUR APS version, how RunDynamics behaves:
  1. Does the Python call wait until the run is finished, or return at once?
  2. Is the time argument a DURATION ("run 5 s more") or an END TIME ("run until t = 5 s")?

How to run (in Command Prompt, with the (twin) environment active):
  1. Open APS and open a simulation that can run in Dynamics mode.
  2. python dyn_probe.py                      -> lists the open simulations
  3. python dyn_probe.py "Sim Name" "TANK1.L"  -> runs the probe
     (the second argument is optional: any variable you want to watch)

Units: APS internal units are SI (s, K, kPa, kg/s). Time below is in seconds.
"""
import sys
import time
import json
import traceback

import simcentralconnect

STEP = 5.0          # s of simulated time per RunDynamics call (a double, as the readme requires)
POLL = 0.25         # s of wall-clock time between checks
QUIET_LIMIT = 2.0   # s of wall-clock time with no change in t = "run has finished"
MAX_WAIT = 60.0     # s of wall-clock time before giving up on one test


def read_time(sm, sim):
    """Return (t, dt) in seconds from GetSimulationTimeDetails."""
    d = sm.GetSimulationTimeDetails(sim).Result
    return _member(d, "timeVariableValue"), _member(d, "dtParameterValue")


def _member(obj, name):
    """Read a field or property from a .NET object, whatever its capitalisation."""
    for n in (name, name[0].upper() + name[1:]):
        if hasattr(obj, n):
            return float(str(getattr(obj, n)))
    t = obj.GetType()
    for m in list(t.GetProperties()) + list(t.GetFields()):
        if m.Name.lower() == name.lower():
            return float(str(m.GetValue(obj)))
    names = [m.Name for m in list(t.GetProperties()) + list(t.GetFields())]
    raise AttributeError(f"{t.FullName} has no '{name}'. Members: {names}")


def read_var(vm, sim, var):
    if not var:
        return None
    return float(str(vm.GetVariableValue(sim, var).Result))


def watch_until_quiet(sm, vm, sim, var):
    """Poll t until it stops changing; print each change. Return final t."""
    start = time.time()
    last_t, _ = read_time(sm, sim)
    last_change = time.time()
    while time.time() - start < MAX_WAIT:
        time.sleep(POLL)
        t, _ = read_time(sm, sim)
        if abs(t - last_t) > 1e-9:
            v = read_var(vm, sim, var)
            extra = f"   {var} = {v:.6g}" if var else ""
            print(f"   wall {time.time() - start:6.2f} s   t = {t:10.4f} s{extra}")
            last_t, last_change = t, time.time()
        elif time.time() - last_change > QUIET_LIMIT:
            return last_t, False
    return last_t, True   # True = still moving after MAX_WAIT (free-running)


def run_test(sm, vm, sim, var, label):
    t_before, _ = read_time(sm, sim)
    print(f"\n{label}: t before = {t_before:.4f} s, calling RunDynamics(sim, {STEP})")
    w0 = time.time()
    ok = sm.RunDynamics(sim, STEP, None).Result
    call_s = time.time() - w0
    t_return, _ = read_time(sm, sim)
    print(f"   call returned {ok} after {call_s:.2f} s wall time; t at return = {t_return:.4f} s")
    t_end, still_moving = watch_until_quiet(sm, vm, sim, var)
    print(f"   t after it settled = {t_end:.4f} s")
    return t_before, t_return, t_end, still_moving


def main():
    sc = simcentralconnect.connect({'ClientApp': 'dyn_probe.py'}).Result
    sc.SetOptions(json.dumps({'EnableApiLogging': 'false'}))
    sm = sc.GetService("ISimulationManager")
    vm = sc.GetService("IVariableManager")

    open_sims = [str(s) for s in sm.GetOpenSimulations().Result]
    if len(sys.argv) < 2:
        print("Open simulations:", open_sims if open_sims else "(none)")
        print("Saved simulations (exact names):")
        for s in sm.GetAvailableSimulations().Result:
            print("   ", str(s))
        print('\nRun again as:  python dyn_probe.py "Exact Name" "MODEL.VAR"')
        return

    sim = sys.argv[1]
    var = sys.argv[2] if len(sys.argv) > 2 else None

    if sim not in open_sims:
        print(f"'{sim}' is not open - opening it ...")
        try:
            sm.OpenSimulation(sim).Result
        except Exception:
            print(f"Could not open '{sim}'. Saved simulations (exact names):")
            for s in sm.GetAvailableSimulations().Result:
                print("   ", str(s))
            return

    mode = str(sm.GetSimulationMode(sim).Result)
    print(f"Simulation '{sim}' mode: {mode}")
    if mode != "Dynamics":
        print("Switching to Dynamics mode ...")
        solved = sm.UpdateSimulationMode(sim, "Dynamics").Result
        print(f"   solved after switch: {solved}")

    t0, dt = read_time(sm, sim)
    print(f"Start: t = {t0:.4f} s, integration step dt = {dt:.4g} s")
    if var:
        print(f"Start: {var} = {read_var(vm, sim, var):.6g} (internal SI units)")

    a = run_test(sm, vm, sim, var, "TEST 1")
    b = run_test(sm, vm, sim, var, "TEST 2")

    print("\n================ VERDICT ================")
    blocking = abs(a[1] - a[2]) < 1e-6
    print("1. Call waits for the run to finish:", "YES" if blocking else "NO - must poll t after each call")
    if a[3] or b[3]:
        print("2. Time kept rising after the call: dynamics may be FREE-RUNNING; pause it in APS.")
    else:
        gain1, gain2 = a[2] - a[0], b[2] - b[0]
        print(f"   Test 1 advanced t by {gain1:.4f} s, Test 2 by {gain2:.4f} s (asked for {STEP} s each)")
        if abs(gain2 - STEP) < 0.5 * STEP:
            print("2. Time argument is a DURATION (run STEP seconds more).")
        elif abs(b[2] - STEP) < 0.5 * STEP or gain2 < 0.1 * STEP:
            print("2. Time argument looks like an END TIME (run until t = STEP).")
        else:
            print("2. Unclear - send this whole output to Claude.")
    print("=========================================")


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
