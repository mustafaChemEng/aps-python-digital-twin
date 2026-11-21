"""
List every variable and parameter of each model in a simulation.
Usage:  python list_vars.py "The_Tank_Model_Test"
Writes: vars_<simulation>.txt in this folder (and prints a short summary).
Based on the Query pattern in AVEVA's Scripting_Ex_2.py sample.
"""
import sys
import json
import traceback

import simcentralconnect
from System import Array, String, Object
from System.Collections.Generic import Dictionary


def convert(d):
    nd = Dictionary[String, Object]()
    for k, v in d.items():
        nd[k] = v
    return nd


def query(sm, resource, kind, fields):
    child = Array[Object]([convert({'type': kind, 'fields': Array[String](fields)})])
    res = sm.Query('simulation', Array[String](resource), child).Result
    items = getattr(res, kind, None)
    return list(items) if items is not None else []


def main():
    sim = sys.argv[1] if len(sys.argv) > 1 else "The_Tank_Model_Test"
    sc = simcentralconnect.connect({'ClientApp': 'list_vars.py'}).Result
    sc.SetOptions(json.dumps({'EnableApiLogging': 'false'}))
    sm = sc.GetService("ISimulationManager")

    if sim not in [str(s) for s in sm.GetOpenSimulations().Result]:
        sm.OpenSimulation(sim).Result

    lines = [f"Simulation: {sim}",
             f"Mode: {sm.GetSimulationMode(sim).Result}",
             f"Status (input, spec, solved): {list(sm.GetSimulationStatus(sim).Result)}", ""]

    for m in query(sm, [sim], 'model', ['name', 'modelType']):
        name = str(m.name)
        lines.append(f"=== MODEL {name}  ({m.modeltype})")
        for v in query(sm, [sim, name], 'variable', ['name', 'specified', 'value', 'uom']):
            spec = "SPEC" if str(v.specified).lower() == "true" else "calc"
            lines.append(f"  VAR   {name}.{v.name:<22} {spec}  {v.value}  [{v.uom}]")
        for p in query(sm, [sim, name], 'parameter', ['name', 'value', 'uom']):
            lines.append(f"  PARAM {name}.{p.name:<22}       {p.value}  [{p.uom}]")
        lines.append("")

    out = f"vars_{sim}.txt"
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines[:3]))
    print(f"\nWrote {len(lines)} lines to {out}")


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
