#!/usr/bin/env python3
"""Turn one or more run_bench.sh result files into a tidy per-(version, proof, mode) table.

  aggregate.py out/results_sc.csv [out/results_mt.csv ...] [-o out/tidy.csv]

Input rows are `phase, config, group, label, wallMs, <BENCH tokens>`, one per rep. Output is one row
per (mode, config, group, label) with the metrics the over-time comparison reports:

  nodes, branches   proof size. Exact and deterministic, so they carry the comparison. Reps must
                    agree; a disagreement is kept as a `nondet` flag rather than averaged away.
  proveMs           automode time, minimum over the warm reps (rep >= 2), or over all reps when
                    only one was run. The minimum, not the mean: it is the least contaminated by
                    scheduling noise, and the runs are already serialised.
  loadMs            everything KeYEnvironment.load does -- taclet base, Java model, proof obligation.
  javaMs            the Java-model part of that load: ProblemInitializer.readJava, which is Recoder's
                    readCompilationUnitsAsFiles before the JavaParser merge and JavaService's
                    parseSpecialClasses plus readCompilationUnits after it.
  msPerNode         proveMs / nodes: what one rule application costs. It separates the two ways a
                    version can get slower, which the raw time conflates -- a constant overhead per
                    step moves this and leaves the node count alone, while a worse proof search
                    moves the node count and leaves this alone.
  coldWallMs        jvmMs + loadMs + proveMs of rep 1: the end-to-end wait on a fresh start.
  warmWallMs        loadMs + proveMs of the warm rep: the same wait with the JIT already warm.
  procWallMs        wall clock of the whole process, covering every rep. Reported as a cross-check
                    on the two derived walls, not as a per-proof figure.
  closed, openGoals whether the proof actually closed. A version that leaves goals open is not
                    comparable on time to one that closed, so this gates how a row may be read.
"""
import argparse
import csv
import sys
from collections import defaultdict

METRIC_FIELDS = ("nodes", "branches", "openGoals", "jvmMs", "loadMs", "javaMs", "proveMs",
                 "gcMs", "peakHeapMB", "liveHeapMB")

OUT_FIELDS = ["mode", "config", "group", "label", "status", "nodes", "branches", "openGoals",
              "closed", "proveMs", "msPerNode", "loadMs", "javaMs", "coldWallMs", "warmWallMs",
              "procWallMs", "gcMs", "peakHeapMB", "liveHeapMB", "reps", "nondet"]


def parse(path, rows):
    with open(path, newline="", encoding="utf-8") as fh:
        for parts in csv.reader(fh):
            if len(parts) < 5 or parts[0] == "phase" or parts[0].startswith("#"):
                continue
            mode, config, group, label, wall = parts[:5]
            rest = parts[5:]
            if not rest:
                continue
            if rest[0] == "ERR":
                rows[(mode, config, group, label)].append({"ERR": rest[1] if len(rest) > 1 else "?",
                                                           "procWallMs": wall})
                continue
            if rest[0] != "BENCH":
                continue
            rec = {"procWallMs": wall}
            for tok in rest[1:]:
                if "=" in tok:
                    k, v = tok.split("=", 1)
                    rec[k] = v
                elif tok.startswith("NONDET"):
                    rec["nondet"] = "1"
            rows[(mode, config, group, label)].append(rec)


def summarise(recs):
    errs = [r for r in recs if "ERR" in r]
    good = [r for r in recs if "ERR" not in r]
    if not good:
        reason = errs[0]["ERR"] if errs else "no output"
        return {"status": f"ERR:{reason}", "procWallMs": errs[0]["procWallMs"] if errs else ""}

    def num(rec, key, default=None):
        v = rec.get(key)
        return int(v) if v not in (None, "") else default

    reps = sorted(good, key=lambda r: num(r, "rep", 0))
    rep1 = reps[0]
    warm = [r for r in reps if num(r, "rep", 1) >= 2] or reps

    node_set = {num(r, "nodes") for r in reps}
    nondet = "1" if len(node_set) > 1 or any(r.get("nondet") for r in reps) else ""
    closed = rep1.get("closed", "?")
    open_goals = num(rep1, "openGoals", 0)
    status = "ok" if closed == "true" else "OPEN"

    def best(key):
        vals = [num(r, key) for r in warm if num(r, key) is not None]
        return min(vals) if vals else ""

    prove, load, java = best("proveMs"), best("loadMs"), best("javaMs")
    cold = (num(rep1, "jvmMs", 0) + num(rep1, "loadMs", 0) + num(rep1, "proveMs", 0))
    nodes = num(rep1, "nodes", "")
    return {
        "status": status,
        "nodes": nodes,
        "branches": num(rep1, "branches", ""),
        "openGoals": open_goals,
        "closed": closed,
        "proveMs": prove,
        "msPerNode": round(prove / nodes, 4) if prove != "" and nodes not in ("", 0) else "",
        "loadMs": load,
        "javaMs": java,
        "coldWallMs": cold,
        "warmWallMs": (load + prove) if load != "" and prove != "" else "",
        "procWallMs": rep1.get("procWallMs", ""),
        "gcMs": best("gcMs"),
        "peakHeapMB": best("peakHeapMB"),
        "liveHeapMB": best("liveHeapMB"),
        "reps": len(reps),
        "nondet": nondet,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results", nargs="+")
    ap.add_argument("-o", "--out", default=None)
    args = ap.parse_args()

    rows = defaultdict(list)
    for p in args.results:
        parse(p, rows)
    if not rows:
        print("no rows parsed", file=sys.stderr)
        return 1

    out = []
    for (mode, config, group, label), recs in rows.items():
        rec = {"mode": mode, "config": config, "group": group, "label": label}
        rec.update(summarise(recs))
        out.append({k: rec.get(k, "") for k in OUT_FIELDS})
    out.sort(key=lambda r: (r["mode"], r["group"], r["label"], r["config"]))

    sink = open(args.out, "w", newline="") if args.out else sys.stdout
    w = csv.DictWriter(sink, fieldnames=OUT_FIELDS)
    w.writeheader()
    w.writerows(out)
    if args.out:
        sink.close()
        print(f"wrote {args.out} ({len(out)} rows)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
