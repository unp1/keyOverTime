#!/usr/bin/env python3
"""Report, per proof, which effective settings differ between the compared versions.

  settings_diff.py <outdir> [tag]

Reads the SETTINGS line each run prints and diffs the active strategy, its strategy properties and
the taclet choices across versions. This is the check that decides how the whole comparison may be
read: where a proof shows no differing keys, the versions ran the same configuration on the same
problem and a change in node count is the prover changing. Where keys differ, the change is at least
partly the shipped configuration, and the report has to say so rather than attribute it to the
engine.
"""
import glob
import os
import re
import sys


def load(outdir, tag):
    """SETTINGS of rep 1 per (label, config), from the driver's per-run stdout captures."""
    runs = {}
    for path in sorted(glob.glob(os.path.join(outdir, "err", f"{tag}_*.out"))):
        base = os.path.basename(path)[len(tag) + 1:-len(".out")]
        # "<config>_<label>": configs are registered as t<n>-<name>, labels never start with "t<n>-"
        m = re.match(r"(t\d+-[^_]+)_(.+)$", base)
        if not m:
            continue
        config, label = m.group(1), m.group(2)
        with open(path) as fh:
            for line in fh:
                if not line.startswith("SETTINGS"):
                    continue
                props = re.search(r"props=\{(.*?)\}\t", line)
                choices = re.search(r"choices=\{(.*?)\}\s*$", line)
                strategy = re.search(r"strategy=([^\t]*)", line)

                def to_map(m_):
                    if not m_ or not m_.group(1).strip():
                        return {}
                    return dict(x.split("=", 1) for x in m_.group(1).split(", ") if "=" in x)

                runs[(label, config)] = {
                    "strategy": strategy.group(1) if strategy else "?",
                    "props": to_map(props),
                    "choices": to_map(choices),
                }
                break
    return runs


def main():
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    outdir = sys.argv[1]
    tag = sys.argv[2] if len(sys.argv) > 2 else "sc"
    runs = load(outdir, tag)
    if not runs:
        print(f"no SETTINGS lines found under {outdir}/err for tag {tag}", file=sys.stderr)
        return 1

    labels = sorted({label for label, _ in runs})
    configs = sorted({config for _, config in runs})
    print(f"configs: {', '.join(configs)}\n")

    clean = []
    for label in labels:
        present = [c for c in configs if (label, c) in runs]
        if len(present) < 2:
            print(f"{label}: only {present} produced settings, cannot diff")
            continue
        strategies = {runs[(label, c)]["strategy"] for c in present}
        lines = []
        if len(strategies) > 1:
            lines.append("  strategy      " + " | ".join(
                f"{c}={runs[(label, c)]['strategy']}" for c in present))
        for kind in ("props", "choices"):
            keys = sorted(set().union(*[set(runs[(label, c)][kind]) for c in present]))
            for k in keys:
                vals = {c: runs[(label, c)][kind].get(k) for c in present}
                if len(set(vals.values())) > 1:
                    lines.append(f"  {kind:<8}{k:<50}"
                                 + " | ".join(f"{c}={vals[c]}" for c in present))
        missing = [c for c in configs if c not in present]
        head = f"{label}: {len(lines)} differing key(s)"
        if missing:
            head += f"  [no run: {', '.join(missing)}]"
        print(head)
        for line in lines:
            print(line)
        if not lines and not missing:
            clean.append(label)
        print()

    print(f"identical configuration across all versions: "
          f"{', '.join(clean) if clean else '(none)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
