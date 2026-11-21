"""
List, create or rename snapshots for the tank model.
  python make_snapshot.py                 -> list snapshots + current state
  python make_snapshot.py create          -> snapshot the CURRENT state and name it 'start_dyn'
  python make_snapshot.py rename "Dyn 1"  -> rename an existing snapshot to 'start_dyn'
Use 'create' only when the model is in Dynamics mode, not running, level steady near the setpoint.
"""
import sys
import json
import traceback

import simcentralconnect
from System import Array, String

SIM = "The_Tank_Model_Test"
TARGET = "start_dyn"


def field(obj, name):
    for n in (name, name.lower(), name[0].upper() + name[1:]):
        try:
            return str(getattr(obj, n))
        except Exception:
            pass
        try:
            return str(obj[n])
        except Exception:
            pass
    return "?"


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "list"
    sc = simcentralconnect.connect({'ClientApp': 'make_snapshot.py'}).Result
    sc.SetOptions(json.dumps({'EnableApiLogging': 'false'}))
    sm = sc.GetService("ISimulationManager")
    vm = sc.GetService("IVariableManager")
    ssm = sc.GetService("ISnapshotManager")

    if SIM not in [str(s) for s in sm.GetOpenSimulations().Result]:
        sm.OpenSimulation(SIM).Result

    mode = str(sm.GetSimulationMode(SIM).Result)
    lvl, u = [float(str(v)) for v in vm.GetVariableValue(SIM, Array[String](["Tank.Level", "V2.ManPos"])).Result]
    print(f"Mode: {mode} | Tank.Level = {lvl:.4f} | V2.ManPos = {u:.3f}")

    snaps = list(ssm.GetAllSnapshots(SIM).Result)
    names = [field(s, "name") for s in snaps]
    print("Snapshots:")
    for s in snaps:
        print(f"   {field(s, 'name'):<20} mode: {field(s, 'mode')}")

    if action == "create":
        if mode != "Dynamics":
            print("Not in Dynamics mode - switch to Dynamics (do not run) and retry.")
            return
        if TARGET in names:
            print(f"'{TARGET}' already exists - nothing to do.")
            return
        new = str(ssm.CreateSnapshot(SIM).Result)
        ok = ssm.RenameSnapshot(SIM, new, TARGET).Result
        print(f"Created snapshot '{new}' and renamed it to '{TARGET}': {ok}")
    elif action == "rename":
        old = sys.argv[2]
        ok = ssm.RenameSnapshot(SIM, old, TARGET).Result
        print(f"Renamed '{old}' -> '{TARGET}': {ok}")


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
