#!/usr/bin/env python3
"""Build the self-contained HTML report from aggregate.py's tidy CSV.

  gen_report.py out/tidy.csv -o out/report.html

One page, one chart per metric. Each chart plots every proof across every column, which is what the
comparison is about: whether a version moved the whole corpus or only part of it.

A column is a (version, prover mode) pair rather than a version, so the goal-parallel runs sit on the
axis beside the single-threaded run of the same build instead of hiding behind a toggle. That also
puts the determinism property in plain sight on the proof-size charts: the SC and MT columns of one
version are identical, so the line between them is flat.

Three views share every chart. Relative to the oldest column and absolute both put all proofs on one
axis, which is what makes them comparable with each other -- and also what squeezes a proof that
improved a little when another improved enormously. Per proof gives each its own panel and its own scale, so a
small improvement reads as clearly as a large one. This module owns the column list, the proof list
and the metric list; gen_pdf.py and gen_xlsx.py import them so all three artefacts stay in step.
"""
import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from csvio import read_rows  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
VERSIONS_CSV = os.environ.get("BENCH_VERSIONS",
                              os.path.join(HERE, os.pardir, "config", "versions.csv"))
CONFIGS_CSV = os.environ.get("BENCH_CONFIGS", "")
# Worker count the mt phase ran at. keyovertime exports it from the value it used, so the
# label and the method note cannot drift from the measurement; 4 is run_bench.sh's default.
WORKERS = os.environ.get("BENCH_WORKERS", "4")
# Comma-separated metric ids to report, in the order given; empty means all of them.
# A focused investigation (say, only the two loading metrics) sets this rather than
# editing METRICS, so the module still describes the full set it can produce.
SELECTED_METRICS = [m for m in os.environ.get("BENCH_METRICS", "").split(",") if m]
# Set when the run capped automode at a single rule to measure loading only. Closure is
# then meaningless by construction -- no proof was given the budget to close -- so the
# report must not flag every cell as OPEN, which would read as something having failed.
LOAD_ONLY = os.environ.get("BENCH_LOAD_ONLY", "") == "1"


def _read_tsv(path):
    """Data rows of one of the project's CSV files, comments, blanks and the header dropped."""
    if not path:
        return []
    try:
        return read_rows(path)
    except OSError as e:
        print(f"warning: {e}", file=sys.stderr)
        return []


def read_versions(path=VERSIONS_CSV):
    """The declared versions, in file order: name, commitish, display label, description."""
    versions = []
    for f in _read_tsv(path):
        if len(f) < 2:
            continue
        versions.append({
            "name": f[0],
            "commitish": f[1],
            "display": f[2] if len(f) > 2 and f[2].strip() else f[0],
            "description": f[3] if len(f) > 3 else "",
            # A row that names its own worker count is a parallel-only row: it exists to put one
            # more worker count on the axis, so it contributes an mt column and no sc column.
            "workers": f[4].strip() if len(f) > 4 else "",
        })
    return versions


def read_mt_capable(path=CONFIGS_CSV):
    """{version name: has the goal-parallel prover}, as build_config.sh detected it."""
    return {f[0]: (len(f) > 4 and f[4].strip() == "1") for f in _read_tsv(path) if f}


def make_columns(versions, mt_capable):
    """(config, prover mode, label, qualifier, note) per chart column, versions in file order.

    A version without the parallel prover contributes one column; a version with it contributes two,
    single-threaded and parallel, so the pair sits together on the axis. A row carrying its own
    worker count contributes only the parallel one, which is how one commit appears at more than one
    worker count: a second row on the same commit, measured in the mt phase alone. Every column states its
    mode, including the versions that only ever ran sequentially: a blank under those names reads as
    "unknown" rather than "SC", and next to a neighbour explicitly marked SC it invites the guess
    that the blank one is something else.
    """
    columns = []
    for v in versions:
        workers = v.get("workers") or WORKERS
        if v.get("workers"):
            modes = ["mt"]
        else:
            modes = ["sc", "mt"] if mt_capable.get(v["name"]) else ["sc"]
        for mode in modes:
            qual = "SC" if mode == "sc" else f"MT {workers}x"
            note = v["description"] or v["display"]
            note += "  --  " + ("single-threaded" if mode == "sc" else f"{workers} workers")
            columns.append((v["name"], mode, v["display"], qual, note))
    return columns


# Loaded once at import so gen_pdf.py and gen_xlsx.py get the same axis by importing COLUMNS. The
# axis is a release lineage, not a linear main history: 3.0-rc is a sibling of the MT commit,
# branched off main the day before MT landed.
VERSIONS = read_versions()
COLUMNS = make_columns(VERSIONS, read_mt_capable())

# Proof order, case studies last. Colors are slots 1-7 of the validated categorical palette, in the
# documented order; the order is the CVD-safety mechanism, so it is not rearranged to taste.
PROOFS = [
    ("SLL_remove", "SimpleLL.remove", "#2a78d6", "#3987e5"),
    ("symmArray", "symmArray", "#eb6834", "#d95926"),
    ("Saddleback", "SaddleBack.search", "#1baf7a", "#199e70"),
    ("jml_infoflow", "jml-information-flow", "#eda100", "#c98500"),
    ("gemplus_add", "gemplus.add", "#e87ba4", "#d55181"),
    ("bike", "bike", "#008300", "#008300"),
    ("bet", "bet", "#4a3aa7", "#9085e9"),
]

# (id, chart title, unit, caption). An empty caption renders no caption line at all, in the page and
# in the PDF alike.
METRICS = [
    ("javaMs", "Java parsing and conversion", "ms",
     "Parsing of the Java sources and conversion into KeY data structures"),
    ("loadMs", "Total load time", "ms",
     "Everything KeYEnvironment.load does: taclet base, Java model, proof obligation. The upper "
     "bound on the Java-model figure above."),
    ("nodes", "Proof size (nodes)", "nodes", ""),
    ("branches", "Proof size (branches)", "branches", ""),
    ("proveMs", "Automode time", "ms",
     "Proof search alone, without loading time."),
    ("msPerNode", "Cost per rule application", "ms/node",
     "Automode time per node."),
    ("coldWallMs", "End-to-end wall time (cold)", "ms",
     "JVM start-up plus loading plus proof search"),
]


def _select(metrics):
    """METRICS filtered and ordered by BENCH_METRICS, or unchanged when it is not set."""
    if not SELECTED_METRICS:
        return metrics
    by_id = {m[0]: m for m in metrics}
    missing = [m for m in SELECTED_METRICS if m not in by_id]
    if missing:
        print(f"warning: BENCH_METRICS names unknown metrics: {', '.join(missing)}", file=sys.stderr)
    return [by_id[m] for m in SELECTED_METRICS if m in by_id]


METRICS = _select(METRICS)


def load(path):
    with open(path) as fh:
        return list(csv.DictReader(fh))


def build(rows):
    """(values, status): values[metric][proof] and status[proof] are lists aligned to COLUMNS.

    Aligning to the column list here rather than in each consumer means a missing measurement is a
    None in a known position, so a proof that was not run on some version leaves a visible gap
    instead of silently shifting the rest of its line along the axis.
    """
    by = {(r["mode"], r["label"], r["config"]): r for r in rows}
    values, status = {}, {}
    for pid, *_ in PROOFS:
        status[pid] = []
        for cfg, mode, *_rest in COLUMNS:
            r = by.get((mode, pid, cfg))
            status[pid].append(None if r is None else {
                "status": r["status"], "openGoals": r["openGoals"], "nondet": r["nondet"]})
    for metric, *_ in METRICS:
        per_proof = {}
        for pid, *_ in PROOFS:
            series = []
            for cfg, mode, *_rest in COLUMNS:
                r = by.get((mode, pid, cfg))
                v = r.get(metric, "") if r else ""
                series.append(float(v) if v not in ("", None) else None)
            if any(v is not None for v in series):
                per_proof[pid] = series
        if per_proof:
            values[metric] = per_proof
    return values, status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tidy")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--title", default="KeY performance over time")
    args = ap.parse_args()

    rows = load(args.tidy)
    values, status = build(rows)
    if not values:
        print("no usable rows", file=sys.stderr)
        return 1

    mt_capable = read_mt_capable()
    payload = {
        "versions": [{**v, "mt": bool(mt_capable.get(v["name"]))} for v in VERSIONS],
        "workers": WORKERS,
        "loadOnly": LOAD_ONLY,
        "columns": [{"cfg": c, "mode": m, "name": n, "qual": q, "note": d}
                    for c, m, n, q, d in COLUMNS],
        "proofs": [{"id": i, "name": n, "light": cl, "dark": cd} for i, n, cl, cd in PROOFS],
        "metrics": [{"id": i, "title": t, "unit": u, "blurb": b} for i, t, u, b in METRICS],
        "data": values,
        "status": status,
    }

    with open(args.out, "w") as fh:
        fh.write(PAGE.replace("__TITLE__", args.title)
                 .replace("__PAYLOAD__", json.dumps(payload)))
    print(f"wrote {args.out}", file=sys.stderr)
    return 0


PAGE = r"""<title>__TITLE__</title>
<style>
  .viz-root {
    color-scheme: light;
    --surface-1: #fcfcfb; --surface-2: #f4f3f0; --line: #dbdad4; --band: #00000008;
    --sel: #e9f1fc;
    --text-primary: #0b0b0b; --text-secondary: #52514e; --text-muted: #7c7a74;
    --critical: #d03b3b;
  }
  @media (prefers-color-scheme: dark) {
    :root:where(:not([data-theme="light"])) .viz-root {
      color-scheme: dark;
      --surface-1: #1a1a19; --surface-2: #232322; --line: #3a3a37; --band: #ffffff0d;
      --sel: #26334a;
      --text-primary: #ffffff; --text-secondary: #c3c2b7; --text-muted: #92908a;
    }
  }
  :root[data-theme="dark"] .viz-root {
    color-scheme: dark;
    --surface-1: #1a1a19; --surface-2: #232322; --line: #3a3a37; --band: #ffffff0d;
    --sel: #26334a;
    --text-primary: #ffffff; --text-secondary: #c3c2b7; --text-muted: #92908a;
  }
  .viz-root {
    background: var(--surface-1); color: var(--text-primary);
    font: 15px/1.55 ui-sans-serif, -apple-system, "Segoe UI", system-ui, sans-serif;
    max-width: 1120px; margin: 0 auto; padding: 32px 20px 80px;
  }
  h1 { font-size: 27px; letter-spacing: -0.015em; margin: 0 0 6px; font-weight: 620; }
  h2 { font-size: 19px; letter-spacing: -0.01em; margin: 40px 0 4px; font-weight: 600; }
  .sub { color: var(--text-secondary); margin: 0 0 4px; }
  .blurb { color: var(--text-secondary); font-size: 13.5px; margin: 0 0 14px; max-width: 74ch; }
  .card { background: var(--surface-2); border: 1px solid var(--line); border-radius: 12px;
          padding: 14px 14px 8px; margin-top: 12px; }
  .toolbar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 0 0 8px; }
  .toolbar button {
    font: inherit; font-size: 13px; padding: 4px 11px; border-radius: 999px; cursor: pointer;
    border: 1px solid var(--line); background: var(--surface-1); color: var(--text-secondary);
  }
  .toolbar button[aria-pressed="true"] { color: var(--text-primary);
    border-color: var(--text-muted); font-weight: 560; }
  .legend { display: flex; flex-wrap: wrap; gap: 6px 16px; margin: 6px 0 2px; font-size: 12.5px;
            color: var(--text-secondary); }
  .legend span { display: inline-flex; align-items: center; gap: 6px; }
  .legend i { width: 11px; height: 11px; border-radius: 3px; display: inline-block; }
  .scroll { overflow-x: auto; }
  table { border-collapse: collapse; font-size: 12.5px; font-variant-numeric: tabular-nums;
          width: 100%; }
  th, td { text-align: right; padding: 5px 7px; border-bottom: 1px solid var(--line);
           white-space: nowrap; }
  th:first-child, td:first-child { text-align: left; }
  thead th { color: var(--text-secondary); font-weight: 560; }
  th small, td small { display: block; font-size: 10px; color: var(--text-muted); font-weight: 400; }
  td .sw { width: 9px; height: 9px; border-radius: 2px; display: inline-block; margin-right: 7px; }
  .mtcol { background: var(--band); }
  th.selcol, td.selcol { background: var(--sel); }
  th.cmpcol, td.cmpcol { border-left: 2px solid var(--line); padding-left: 14px; }
  th.cmpcol { color: var(--text-secondary); max-width: 15ch; }
  th.cmpcol small { white-space: normal; line-height: 1.3; }
  td.cmpcol { font-weight: 600; color: var(--text-primary); }
  /* The view panel. In the flow by default, so a narrow window simply reads it as a block above
     the charts; floated into the left margin once there is room for it beside the content. */
  #panel { margin: 14px 0 4px; }
  #panel .pbox { background: var(--surface-2); border: 1px solid var(--line);
                 border-radius: 12px; padding: 11px 13px 12px; }
  #panel h3 { font-size: 11px; letter-spacing: 0.07em; text-transform: uppercase; font-weight: 600;
              color: var(--text-muted); margin: 0 0 7px; }
  #panel h3.next { margin-top: 15px; }
  #panel label { display: flex; align-items: baseline; gap: 7px; font-size: 12.5px; padding: 2px 0;
                 cursor: pointer; color: var(--text-secondary); }
  #panel label input { cursor: pointer; margin: 0; }
  #panel label small { color: var(--text-muted); font-size: 10.5px; }
  #panel label.off { opacity: 0.55; }
  #panel select { width: 100%; font: inherit; font-size: 12.5px; padding: 3px 6px;
                  border-radius: 8px; border: 1px solid var(--line); background: var(--surface-1);
                  color: var(--text-primary); cursor: pointer; }
  #panel .arrow { display: block; text-align: center; color: var(--text-muted); font-size: 11px;
                  line-height: 1.5; }
  #panel .row { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; }
  #panel button { font: inherit; font-size: 11.5px; padding: 3px 9px; border-radius: 999px;
                  cursor: pointer; border: 1px solid var(--line);
                  background: var(--surface-1); color: var(--text-secondary); }
  #panel .cmpnote { display: block; color: var(--text-muted); font-size: 11px; margin-top: 9px;
                    line-height: 1.45; }
  #panel .cmpname { font-weight: 600; color: var(--text-primary); }
  @media (min-width: 1180px) {
    #panel { position: fixed; top: 66px; left: 14px; width: 206px; margin: 0; z-index: 5;
             max-height: calc(100vh - 84px); overflow-y: auto; }
    .viz-root { margin-left: 242px; margin-right: auto; }
  }
  #versions td { vertical-align: top; }
  #versions code { font-size: 11.5px; color: var(--text-muted); }
  #versions td.desc { text-align: left; white-space: normal; width: 100%;
                      color: var(--text-secondary); }
  #versions th.desc { text-align: left; }
  #versions th:nth-child(-n+3), #versions td:nth-child(-n+3) { width: 1%; }
  #versions code { white-space: nowrap; }
  .flag { color: var(--critical); font-weight: 600; }
  .note { color: var(--text-secondary); font-size: 13.5px; max-width: 78ch; }
  .tip { position: fixed; pointer-events: none; z-index: 10; background: var(--surface-1);
         border: 1px solid var(--line); border-radius: 9px; padding: 8px 10px; font-size: 12.5px;
         box-shadow: 0 6px 22px rgba(0,0,0,.16); display: none; }
  .tip b { font-weight: 600; }
  .tip div { display: flex; justify-content: space-between; gap: 14px; }
  svg { display: block; max-width: 100%; }
</style>
<div class="viz-root">
<h1>__TITLE__</h1>
<p class="sub" id="sub"></p>
<div id="versions"></div>
<div id="panel"></div>
<div id="charts"></div>

<h2>How this was measured</h2>
<p class="note" id="method"></p>
</div>
<div class="tip" id="tip"></div>
<script>
const P = __PAYLOAD__;
const tip = document.getElementById('tip');
const isDark = () => document.documentElement.dataset.theme === 'dark' ||
  (document.documentElement.dataset.theme !== 'light' &&
   matchMedia('(prefers-color-scheme: dark)').matches);
const color = p => isDark() ? p.dark : p.light;
const NC = P.columns.length;
const fmt = v => v == null ? '\u2014'
  : v === 0 ? '0'
  : v >= 100 ? Math.round(v).toLocaleString('en-US')
  : v >= 10 ? v.toFixed(0) : v.toFixed(v >= 1 ? 1 : 3);
const ratio = v => v == null ? '\u2014'
  : (v >= 1 ? v.toFixed(2) + '\u00d7' : '\u00f7' + (1 / v).toFixed(2));
const colLabel = c => c.name + (c.qual ? ' (' + c.qual + ')' : '');

document.getElementById('sub').textContent =
  P.columns.filter(c => c.mode === 'sc').map(c => c.name).join('  \u2192  ') +
  '   \u00b7   ' + P.proofs.length + ' proofs' +
  (P.columns.some(c => c.mode === 'mt')
     ? '   \u00b7   MT ' + P.workers + 'x where available' : '');

// [{proof, values[]}] aligned to P.columns, for the proofs that produced anything
function series(metric, exclude) {
  const d = P.data[metric] || {};
  return P.proofs.filter(p => d[p.id] && p.id !== exclude)
                 .map(p => ({ proof: p, values: d[p.id] }));
}

// Which columns are on the axis at all, one flag per column of P.columns. Hiding a column is a view
// choice, not a data one, so everything downstream works off visibleCols() rather than off P.columns
// directly and the underlying values are never rewritten. Page-level rather than per-chart: a
// version hidden in one chart and shown in the next would make two charts silently disagree about
// what is being compared.
const SHOWN = P.columns.map(() => true);

// Never fewer than two: a ratio needs a baseline and a target, and a single-column chart says
// nothing this report is for.
const MIN_SHOWN = 2;

function visibleCols() {
  return P.columns.map((c, i) => i).filter(i => SHOWN[i]);
}

// The pair every ratio is taken over: baseline and target, as indices into P.columns. Defaults to
// first versus last, which is the "what changed over the whole span" reading the report opens on.
// It is page-level rather than per-chart state: comparing nodes over one pair and time over another
// invites reading a speedup against a proof-size change that was never measured against it.
const CMP = { from: 0, to: P.columns.length - 1 };

// The pair as positions within a visible-column list, since hiding the parallel columns renumbers
// everything. A selected column that is currently hidden falls back to the nearest visible one, so
// the ratio always names two columns that are actually on the axis.
function cmpPos(vis) {
  const near = (want) => {
    if (vis.includes(want)) return vis.indexOf(want);
    let best = 0, bestD = Infinity;
    vis.forEach((c, j) => { const d = Math.abs(c - want); if (d < bestD) { bestD = d; best = j; } });
    return best;
  };
  const from = near(CMP.from), to = near(CMP.to);
  return { from, to };
}

// value[to] / value[from] for one proof's series, or null where either end was not measured.
function cmpRatio(raw, pos) {
  const a = raw[pos.from], b = raw[pos.to];
  return (a == null || b == null || a === 0) ? null : b / a;
}

// A proof counts as an outlier only if it is detached from the field, which takes two tests. It
// must lead the runner-up by at least OUTLIER_MIN_LEAD, and that lead must also be a substantial
// share of how far the remaining proofs are spread among themselves. The second test is what stops
// a leader being flagged merely because the whole corpus is spread out: leading by 2x means little
// when the others already span 11x, and a great deal when they span 3x.
const OUTLIER_MIN_LEAD = 1.35;
const OUTLIER_DETACH = 0.5;

// The outlier, or null if there is not one. Every metric here is lower-is-better, so the biggest
// winner is the smallest last-versus-first ratio. Comparisons are done on log improvements, where
// "twice as much again" is the same distance wherever it sits on the scale.
function outlier(metric, vis) {
  const d = P.data[metric] || {};
  const pos = cmpPos(vis);
  const scored = [];
  for (const p of P.proofs) {
    const v = d[p.id];
    if (!v) continue;
    // Scored over the selected pair, so the outlier is the one distorting the comparison actually
    // on screen rather than one that happens to lead over the full span.
    const r = cmpRatio(vis.map(i => v[i]), pos);
    if (r == null || r <= 0) continue;
    scored.push({ id: p.id, name: p.name, rel: r });
  }
  // Fewer than three proofs leaves no field to be detached from.
  if (scored.length < 3) return null;
  scored.sort((a, b) => a.rel - b.rel);
  const imp = scored.map(s => Math.log(1 / s.rel));   // improvement, largest first
  const lead = imp[0] - imp[1];
  const restSpread = imp[1] - imp[imp.length - 1];
  if (Math.exp(lead) < OUTLIER_MIN_LEAD) return null;
  if (restSpread > 0 && lead < OUTLIER_DETACH * restSpread) return null;
  return scored[0];
}

// Tick values for a linear axis: about `want` of them, on a 1/2/5 x 10^k step so the numbers
// themselves stay round. A log axis has decades to fall back on; a linear one has to be given them.
function niceTicks(lo, hi, want) {
  const span = hi - lo;
  if (!(span > 0)) return [lo];
  // Try every round step and keep the one landing closest to `want` ticks, rather than deriving a
  // step from span/want directly: that rounds up through a fixed ladder, so a span a hair over a
  // breakpoint jumps to the next coarser step and leaves an axis with two labels on it.
  const mag = Math.pow(10, Math.floor(Math.log10(span)));
  const count = (step) =>
    Math.floor((hi - Math.ceil(lo / step - 1e-9) * step) / step + 1e-9) + 1;
  let step = mag, best = Infinity;
  for (const m of [0.1, 0.2, 0.25, 0.5, 1, 2, 2.5, 5, 10]) {
    const n = count(m * mag);
    if (n < 2) continue;
    const d = Math.abs(n - want);
    if (d < best) { best = d; step = m * mag; }
  }
  const out = [];
  for (let v = Math.ceil(lo / step - 1e-9) * step; v <= hi + step * 1e-9; v += step) {
    // rounding kills the accumulated float drift that would otherwise print 0.30000000000000004
    out.push(+v.toPrecision(12));
  }
  return out;
}

// Exact label for a linear tick. fmt() rounds for readability, which is right for a measured
// value in a table but wrong on an axis: it printed the 1.25 gridline as "1.3", so the label
// disagreed with the line it sat on. niceTicks only ever produces round values, so printing them
// at full precision costs no width.
function tickNum(v) {
  if (v === 0) return '0';
  return Math.abs(v) >= 1000 ? Math.round(v).toLocaleString('en-US') : String(+v.toPrecision(12));
}

function chart(metric, rel, exclude, vis, useLog) {
  const S = series(metric, exclude);
  const N = vis.length;
  const W = 1060, H = 410, ml = 62, mr = 200, mt = 16, mb = 76;
  // Clear air between the last marker and its label, so the name reads as a caption
  // beside the line rather than as part of the series.
  const LABEL_OFFSET = 34;
  const iw = W - ml - mr, ih = H - mt - mb;
  const pos = cmpPos(vis);
  const pts = S.map(s => {
    const raw = vis.map(i => s.values[i]);
    // Indexed against the selected baseline, not against the first column: picking a different
    // baseline is exactly how you read a step that happens mid-chain rather than over the span.
    const base = raw[pos.from];
    return { ...s, vals: rel
      ? raw.map(v => (v == null || base == null || base === 0) ? null : v / base)
      : raw };
  });
  const all = pts.flatMap(s => s.vals).filter(v => v != null && v > 0);
  if (!all.length) return '<p class="note">no data</p>';
  let lo = Math.min(...all), hi = Math.max(...all);
  if (rel) {
    // Include the 1x baseline so improvement and regression stay distinguishable, but do not
    // mirror the range around it: when every proof improved, a symmetric axis spends half its
    // height on empty space above the baseline and halves the resolution of the actual data.
    lo = Math.min(lo, 1); hi = Math.max(hi, 1);
    if (useLog) {
      const pad = Math.max((Math.log(hi) - Math.log(lo)) * 0.09, 0.05);
      lo = Math.exp(Math.log(lo) - pad); hi = Math.exp(Math.log(hi) + pad);
    } else {
      const pad = Math.max((hi - lo) * 0.08, 0.02);
      lo = Math.max(0, lo - pad); hi = hi + pad;
    }
  } else if (useLog) {
    lo /= 1.6; hi *= 1.6;
  } else {
    // A linear axis of magnitudes is anchored at zero: cutting the bottom off would exaggerate
    // every difference between the columns, which is exactly what the log view is there to avoid.
    lo = 0; hi = hi * 1.06;
  }
  const ly = useLog
    ? v => mt + ih - (Math.log(v) - Math.log(lo)) / (Math.log(hi) - Math.log(lo)) * ih
    : v => mt + ih - (v - lo) / (hi - lo) * ih;
  const lx = j => ml + (N === 1 ? iw / 2 : j * iw / (N - 1));

  let g = '';
  // a shaded band behind each parallel column, so the SC/MT pairs are legible as pairs
  vis.forEach((i, j) => {
    if (P.columns[i].mode !== 'mt' || N < 2) return;
    const half = iw / (N - 1) / 2;
    g += '<rect x="' + (lx(j) - half).toFixed(1) + '" y="' + mt + '" width="' + (half * 2) +
         '" height="' + ih + '" fill="var(--band)"/>';
  });
  const ticks = [];
  if (useLog) {
    if (rel) {
      [0.01,0.02,0.05,0.1,0.2,0.5,1,2,5,10,20,50,100].forEach(t => {
        if (t >= lo && t <= hi) ticks.push([t, ratio(t)]); });
    } else {
      for (let e = -3; e <= 9; e++) for (const m of [1,2,5]) {
        const t = m * Math.pow(10, e); if (t >= lo && t <= hi) ticks.push([t, fmt(t)]); }
    }
  } else {
    // On a linear axis the ticks are evenly spaced, so a fold-change label (0.2 shown as "div 5")
    // would make even spacing read as uneven. Plain multiples keep the spacing legible.
    niceTicks(lo, hi, 7).forEach(t => {
      if (t >= lo && t <= hi) ticks.push([t, tickNum(t) + (rel ? '\u00d7' : '')]); });
  }
  for (const [t, lab] of ticks) {
    const y = ly(t), strong = rel && t === 1;
    g += '<line x1="' + ml + '" x2="' + (ml+iw) + '" y1="' + y + '" y2="' + y +
         '" stroke="var(--line)" stroke-width="' + (strong ? 1.5 : 1) + '" ' +
         (strong ? '' : 'stroke-dasharray="2 4"') + '/>';
    g += '<text x="' + (ml-9) + '" y="' + (y+4) + '" text-anchor="end" font-size="11.5" ' +
         'fill="var(--text-muted)">' + lab + '</text>';
  }
  // The two compared columns are marked on the axis so the ratios in the table can be traced back
  // to the chart without consulting the selector above.
  vis.forEach((i, j) => {
    if (j !== pos.from && j !== pos.to) return;
    g += '<line x1="' + lx(j).toFixed(1) + '" x2="' + lx(j).toFixed(1) + '" y1="' + mt +
         '" y2="' + (mt + ih) + '" stroke="var(--text-muted)" stroke-width="1" ' +
         'stroke-dasharray="3 3" opacity="0.55"/>';
  });
  vis.forEach((i, j) => {
    const c = P.columns[i];
    const marked = (j === pos.from || j === pos.to);
    g += '<text x="' + lx(j) + '" y="' + (mt+ih+22) + '" text-anchor="middle" font-size="12" ' +
         'font-weight="' + (marked ? '650' : '400') + '" fill="var(--text-' +
         (marked ? 'primary' : 'secondary') + ')">' + c.name + '</text>';
    if (c.qual) {
      g += '<text x="' + lx(j) + '" y="' + (mt+ih+37) + '" text-anchor="middle" font-size="10.5" ' +
           'fill="var(--text-muted)">' + c.qual + '</text>';
    }
    if (marked) {
      g += '<text x="' + lx(j) + '" y="' + (mt+ih+(c.qual ? 50 : 36)) + '" text-anchor="middle" ' +
           'font-size="9.5" fill="var(--text-muted)">' + (j === pos.from ? 'from' : 'to') + '</text>';
    }
  });

  const ends = [];
  for (const s of pts) {
    const c = color(s.proof);
    let d = '', open = false;
    s.vals.forEach((v, j) => {
      if (v == null || v <= 0) { open = false; return; }
      d += (open ? 'L' : 'M') + lx(j).toFixed(1) + ' ' + ly(v).toFixed(1) + ' ';
      open = true;
    });
    g += '<path d="' + d + '" fill="none" stroke="' + c + '" stroke-width="2" ' +
         'stroke-linejoin="round" stroke-linecap="round"/>';
    s.vals.forEach((v, j) => {
      if (v == null || v <= 0) return;
      g += '<circle cx="' + lx(j).toFixed(1) + '" cy="' + ly(v).toFixed(1) + '" r="4.2" fill="' +
           c + '" stroke="var(--surface-2)" stroke-width="2"/>';
    });
    let last = -1;
    s.vals.forEach((v, j) => { if (v != null && v > 0) last = j; });
    if (last >= 0) ends.push({ name: s.proof.name, c, y: ly(s.vals[last]) });
  }
  // Place the end labels in one column at the right, pushed apart to a minimum spacing and slid
  // back inside the plot if the column overflowed either edge.
  const GAP = 14, colX = ml + iw + LABEL_OFFSET;
  ends.sort((a, b) => a.y - b.y);
  let floorY = -1e9;
  for (const e of ends) { e.ly = Math.max(e.y, floorY + GAP); floorY = e.ly; }
  if (ends.length) {
    let shift = Math.max(0, ends[ends.length - 1].ly - (mt + ih));
    if (ends[0].ly - shift < mt) shift += (ends[0].ly - shift - mt) / 2;
    for (const e of ends) e.ly -= shift;
  }
  // No leader line from the marker to a nudged label: a thin coloured stroke reaching towards the
  // axis reads as another segment of the series, and a reader cannot tell it from data.
  for (const e of ends) {
    g += '<rect x="' + colX + '" y="' + (e.ly-4).toFixed(1) + '" width="8" height="8" rx="2" fill="' + e.c + '"/>';
    g += '<text x="' + (colX+13) + '" y="' + (e.ly+4).toFixed(1) + '" font-size="12" ' +
         'fill="var(--text-secondary)">' + e.name + '</text>';
  }
  vis.forEach((i, j) => {
    const w = N < 2 ? iw : iw / (N - 1), x0 = lx(j) - w / 2;
    const rowsTip = pts.map(s => ({ n: s.proof.name, c: color(s.proof),
      t: s.vals[j] == null ? '\u2014' : (rel ? ratio(s.vals[j]) : fmt(s.vals[j])) }));
    g += '<rect x="' + Math.max(ml, x0).toFixed(1) + '" y="' + mt + '" width="' + w.toFixed(1) +
         '" height="' + ih + '" fill="transparent" data-tip=\'' +
         JSON.stringify({ h: colLabel(P.columns[i]) + ' \u00b7 ' + P.columns[i].note, rows: rowsTip })
           .replace(/'/g, '&apos;') + '\'/>';
  });
  return '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img">' + g + '</svg>';
}

// Small multiples: one mini panel per proof, shared column axis, each panel scaled to its own data.
// The combined chart has to fit every proof on one axis, so a proof that improved 19x leaves the
// ones that improved 1.7x squeezed into a sliver near the baseline. Here each proof gets the full
// panel height, so a small improvement is as readable as a large one -- at the cost of comparing
// proofs against each other, which is what the other two views are for.
function smallMultiples(metric, vis, useLog) {
  const N = vis.length;
  const posPre = cmpPos(vis);
  const S = series(metric).filter(s => {
    const b = s.values[vis[posPre.from]];
    return b != null && b !== 0;
  });
  if (!S.length) return '<p class="note">nothing to show: no baseline value to index against</p>';
  const pos = cmpPos(vis);
  const cols = Math.min(2, S.length), rows = Math.ceil(S.length / cols);
  const slotW = 508, ih = 150, gx = 18, gy = 78, ml = 6, mt = 26, mb = 8;
  const gutterL = 52, padR = 18;
  const iw = slotW - gutterL - padR;
  const W = ml + cols * slotW + (cols - 1) * gx, H = mt + rows * (ih + gy) + mb;
  let g = '';

  S.forEach((s, k) => {
    const col = k % cols, row = Math.floor(k / cols);
    const ox = ml + col * (slotW + gx) + gutterL, oy = mt + row * (ih + gy);
    const raw = vis.map(i => s.values[i]);
    const base = raw[pos.from];
    const vals = raw.map(v => (v == null || base == null || base === 0) ? null : v / base);
    const seen = vals.filter(v => v != null && v > 0);
    let lo = Math.min(...seen, 1), hi = Math.max(...seen, 1);
    if (useLog) {
      const pad = Math.max((Math.log(hi) - Math.log(lo)) * 0.18, 0.06);
      lo = Math.exp(Math.log(lo) - pad); hi = Math.exp(Math.log(hi) + pad);
    } else {
      const pad = Math.max((hi - lo) * 0.16, 0.03);
      lo = Math.max(0, lo - pad); hi = hi + pad;
    }
    const axLab = v => useLog ? ratio(v) : tickNum(+v.toPrecision(3)) + '\u00d7';
    const lx = j => ox + (N === 1 ? iw / 2 : j * iw / (N - 1));
    const ly = useLog
      ? v => oy + ih - (Math.log(v) - Math.log(lo)) / (Math.log(hi) - Math.log(lo)) * ih
      : v => oy + ih - (v - lo) / (hi - lo) * ih;
    const c = color(s.proof);

    g += '<rect x="' + (ox - gutterL + 4) + '" y="' + (oy - 20) + '" width="' + (slotW - 8) +
         '" height="' + (ih + 58) + '" rx="9" fill="var(--surface-1)" stroke="var(--line)"/>';
    vis.forEach((i, j) => {
      if (P.columns[i].mode !== 'mt' || N < 2) return;
      const half = iw / (N - 1) / 2;
      g += '<rect x="' + (lx(j) - half).toFixed(1) + '" y="' + oy + '" width="' + (half * 2) +
           '" height="' + ih + '" fill="var(--band)"/>';
    });
    if (1 >= lo && 1 <= hi) {
      g += '<line x1="' + ox + '" x2="' + (ox + iw) + '" y1="' + ly(1).toFixed(1) + '" y2="' +
           ly(1).toFixed(1) + '" stroke="var(--line)" stroke-width="1.2"/>';
    }
    [pos.from, pos.to].forEach(j => {
      g += '<line x1="' + lx(j).toFixed(1) + '" x2="' + lx(j).toFixed(1) + '" y1="' + oy +
           '" y2="' + (oy + ih) + '" stroke="var(--text-muted)" stroke-width="1" ' +
           'stroke-dasharray="3 3" opacity="0.45"/>';
    });
    let d = '', open = false;
    vals.forEach((v, j) => {
      if (v == null || v <= 0) { open = false; return; }
      d += (open ? 'L' : 'M') + lx(j).toFixed(1) + ' ' + ly(v).toFixed(1) + ' ';
      open = true;
    });
    g += '<path d="' + d + '" fill="none" stroke="' + c + '" stroke-width="2" ' +
         'stroke-linejoin="round" stroke-linecap="round"/>';
    vals.forEach((v, j) => {
      if (v == null || v <= 0) return;
      g += '<circle cx="' + lx(j).toFixed(1) + '" cy="' + ly(v).toFixed(1) + '" r="3.8" fill="' +
           c + '" stroke="var(--surface-1)" stroke-width="1.6"/>';
    });

    const rSel = cmpRatio(raw, pos);
    g += '<rect x="' + (ox - gutterL + 14) + '" y="' + (oy - 15) + '" width="8" height="8" rx="2" fill="' + c + '"/>';
    g += '<text x="' + (ox - gutterL + 26) + '" y="' + (oy - 8) + '" font-size="12" ' +
         'fill="var(--text-primary)">' + s.proof.name + '</text>';
    if (rSel != null) {
      g += '<text x="' + (ox + iw) + '" y="' + (oy - 8) + '" text-anchor="end" font-size="11.5" ' +
           'font-weight="600" fill="var(--text-secondary)">' + ratio(rSel) + '</text>';
    }
    g += '<text x="' + (ox - 7) + '" y="' + (oy + 4).toFixed(1) + '" text-anchor="end" font-size="9.5" ' +
         'fill="var(--text-muted)">' + axLab(hi) + '</text>';
    g += '<text x="' + (ox - 7) + '" y="' + (oy + ih + 3).toFixed(1) + '" text-anchor="end" font-size="9.5" ' +
         'fill="var(--text-muted)">' + axLab(lo) + '</text>';
    vis.forEach((i, j) => {
      const y = oy + ih + 14, marked = (j === pos.from || j === pos.to);
      g += '<text x="' + lx(j) + '" y="' + y + '" text-anchor="end" font-size="9" ' +
           'font-weight="' + (marked ? '650' : '400') + '" fill="var(--text-' +
           (marked ? 'secondary' : 'muted') + ')" transform="rotate(-38 ' + lx(j) + ' ' + y + ')">' +
           colLabel(P.columns[i]) + '</text>';
    });
    const rowsTip = vis.map((i, j) => ({
      n: colLabel(P.columns[i]), c, t: raw[j] == null ? '\u2014'
        : fmt(raw[j]) + '  (' + (vals[j] == null ? '\u2014' : ratio(vals[j])) + ')' }));
    g += '<rect x="' + (ox - gutterL + 4) + '" y="' + (oy - 20) + '" width="' + (slotW - 8) +
         '" height="' + (ih + 58) + '" fill="transparent" data-tip=\'' +
         JSON.stringify({ h: s.proof.name, rows: rowsTip }).replace(/'/g, '&apos;') + '\'/>';
  });
  return '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img">' + g + '</svg>';
}

function table(metric, vis) {
  const S = series(metric);
  const pos = cmpPos(vis);
  const cmpHead = P.columns[vis[pos.to]].name + ' vs ' + P.columns[vis[pos.from]].name;
  // "compare" on the first line and the pair beneath it in the small muted style the version
  // columns already use for their SC/MT qualifier, so the two header rows line up.
  let h = '<div class="scroll"><table><thead><tr><th>proof</th>' +
    vis.map((i, j) => {
      const c = P.columns[i];
      const cls = [c.mode === 'mt' ? 'mtcol' : '', (j === pos.from || j === pos.to) ? 'selcol' : '']
        .filter(Boolean).join(' ');
      return '<th class="' + cls + '">' + c.name +
             (c.qual ? '<small>' + c.qual + '</small>' : '<small>&nbsp;</small>') + '</th>';
    }).join('') +
    '<th class="cmpcol">compare<small>' + cmpHead + '</small></th></tr></thead><tbody>';
  for (const s of S) {
    const raw = vis.map(i => s.values[i]);
    const rel = cmpRatio(raw, pos);
    const st = P.status[s.proof.id] || [];
    h += '<tr><td><i class="sw" style="background:' + color(s.proof) + '"></i>' + s.proof.name + '</td>' +
      vis.map((i, j) => {
        const val = raw[j];
        const flag = (!P.loadOnly && st[i] && st[i].status && st[i].status !== 'ok')
          ? ' <span class="flag">' + st[i].status + '</span>' : '';
        const cls = [P.columns[i].mode === 'mt' ? 'mtcol' : '',
                     (j === pos.from || j === pos.to) ? 'selcol' : ''].filter(Boolean).join(' ');
        return '<td class="' + cls + '">' + (val == null ? '\u2014' : fmt(val)) + flag + '</td>';
      }).join('') +
      '<td class="cmpcol">' + (rel == null ? '\u2014' : ratio(rel)) + '</td></tr>';
  }
  return h + '</tbody></table></div>';
}

// The declared versions, rendered from versions.csv: what each column of every chart actually is.
// It sits before the charts because "3.0-rc" and "main" mean nothing to a reader who has not been
// told which commit they are.
// Full 40-character shas crowd the description out of the table; the first ten identify a commit
// unambiguously in practice, and the full value stays in the cell's title. Branch and tag names are
// left as written, since truncating those would lose the meaning rather than just the digits.
function shortRev(rev) {
  return /^[0-9a-f]{12,}$/i.test(rev) ? rev.slice(0, 10) : rev;
}

// Written out rather than hard-coded, because the counts in it change with versions.csv: a
// sentence naming "five versions, two of them parallel" silently becomes wrong the moment a row is
// added or removed.
function methodText() {
  const n = P.versions.length;
  const nMt = P.versions.filter(v => v.mt).length;
  const nSc = n - nMt;
  let s = n + ' KeY version' + (n === 1 ? '' : 's') + ', each built from its own detached worktree '
    + 'with the same JDK that runs the measurements, and each measured on its own examples tree so '
    + 'that no version is asked to parse a <code>.key</code> file written for another. Every run is '
    + 'a fresh JVM with <code>key.disregardSettings</code> and a throwaway <code>key.home</code>, '
    + 'so nothing reads or writes the persisted settings, and <code>key.prover.parallel</code> is '
    + 'always set explicitly rather than left to resolve to a stored value. Timings are the minimum '
    + 'over the warm reps; node and branch counts are exact.';
  if (nMt === 0) {
    s += ' No version here has the goal-parallel prover, so each contributes one column.';
  } else if (nSc === 0) {
    s += ' Every version has the goal-parallel prover, so each contributes two columns: '
       + 'single-threaded, and at ' + P.workers + ' workers.';
  } else {
    s += ' ' + nMt + ' of them ' + (nMt === 1 ? 'has' : 'have') + ' the goal-parallel prover and '
       + (nMt === 1 ? 'contributes' : 'contribute') + ' two columns, single-threaded and at '
       + P.workers + ' workers; the other ' + nSc + ' contribute one.';
  }
  if (P.loadOnly) {
    s += ' This run measured loading only: automode was capped at a single rule, so parsing and '
       + 'load times are exactly as in a full run, while proof size and search time were never '
       + 'measured and no proof was expected to close.';
  }
  document.getElementById('method').innerHTML = s;
}

function versionTable() {
  const host = document.getElementById('versions');
  if (!P.versions || !P.versions.length) return;
  host.innerHTML = '<h2>Versions compared</h2>' +
    '<div class="card"><div class="scroll"><table><thead><tr>' +
    '<th>shown as</th><th>commit</th><th>measured</th><th class="desc">what it is</th>' +
    '</tr></thead><tbody>' +
    P.versions.map(v =>
      '<tr><td><b>' + v.display + '</b></td>' +
      '<td><code title="' + v.commitish + '">' + shortRev(v.commitish) + '</code></td>' +
      '<td>' + (v.mt ? 'SC + MT 4x' : 'SC') + '</td>' +
      '<td class="desc">' + (v.description || '') + '</td></tr>').join('') +
    '</tbody></table></div></div>';
}

// The view panel: which columns are shown at all, and which two every ratio is taken over. One
// control for the whole page rather than one per chart, because the interesting questions ("what did
// this commit cost?") are asked of several metrics at once, and a per-chart pair would let two
// charts answer the same question over different pairs without saying so.
function panel() {
  const host = document.getElementById('panel');
  if (!host || P.columns.length < 2) return;
  const vis = visibleCols();
  const pos = cmpPos(vis);
  const fromCol = vis[pos.from], toCol = vis[pos.to];
  const hasMt = P.columns.some(c => c.mode === 'mt');
  const atFullSpan = (fromCol === vis[0] && toCol === vis[vis.length - 1]);

  // Only visible columns can be compared, so the selects list exactly those; their values stay
  // absolute indices into P.columns, so re-showing a column restores the pair rather than
  // renumbering it.
  const opts = (sel) => vis.map(i =>
    '<option value="' + i + '"' + (i === sel ? ' selected' : '') + '>' +
    colLabel(P.columns[i]) + '</option>').join('');

  host.innerHTML =
    '<div class="pbox">' +
    '<h3>Columns</h3>' +
    P.columns.map((c, i) =>
      '<label class="' + (SHOWN[i] ? '' : 'off') + '">' +
      '<input type="checkbox" data-col="' + i + '"' + (SHOWN[i] ? ' checked' : '') + '>' +
      '<span>' + c.name + (c.qual ? ' <small>' + c.qual + '</small>' : '') + '</span></label>').join('') +
    '<div class="row"><button data-qa="all">all</button>' +
    (hasMt ? '<button data-qa="sc">SC only</button><button data-qa="mt">MT only</button>' : '') +
    '</div>' +
    '<h3 class="next">Compare</h3>' +
    '<select id="cmpFrom" aria-label="baseline column">' + opts(fromCol) + '</select>' +
    '<span class="arrow">\u2193 against</span>' +
    '<select id="cmpTo" aria-label="target column">' + opts(toCol) + '</select>' +
    '<span class="cmpnote" id="cmpNote"></span>' +
    '<div class="row"><button id="cmpReset" hidden>reset to full span</button></div>' +
    '</div>';

  document.getElementById('cmpNote').innerHTML = atFullSpan
    ? 'every ratio is <span class="cmpname">' + colLabel(P.columns[toCol]) +
      '</span> against <span class="cmpname">' + colLabel(P.columns[fromCol]) + '</span>'
    : 'ratios are over this pair only, not the full span';
  document.getElementById('cmpReset').hidden = atFullSpan;

  const refresh = () => { render(); panel(); };

  host.querySelectorAll('[data-col]').forEach(box => box.addEventListener('change', () => {
    const i = +box.dataset.col;
    // Refuse the change rather than silently ignoring it, so the box never disagrees with the axis.
    if (!box.checked && SHOWN.filter(Boolean).length <= MIN_SHOWN) { box.checked = true; return; }
    SHOWN[i] = box.checked;
    refresh();
  }));
  host.querySelectorAll('[data-qa]').forEach(b => b.addEventListener('click', () => {
    const want = (c) => b.dataset.qa === 'all' ? true
                      : b.dataset.qa === 'sc' ? c.mode !== 'mt' : c.mode === 'mt';
    const next = P.columns.map(want);
    if (next.filter(Boolean).length < MIN_SHOWN) return;
    P.columns.forEach((c, i) => { SHOWN[i] = next[i]; });
    refresh();
  }));
  const onPair = () => {
    CMP.from = +document.getElementById('cmpFrom').value;
    CMP.to = +document.getElementById('cmpTo').value;
    refresh();
  };
  document.getElementById('cmpFrom').addEventListener('change', onPair);
  document.getElementById('cmpTo').addEventListener('change', onPair);
  document.getElementById('cmpReset').addEventListener('click', () => {
    const v = visibleCols();
    CMP.from = v[0]; CMP.to = v[v.length - 1];
    refresh();
  });
}

function render() {
  const host = document.getElementById('charts');
  host.innerHTML = '';
  for (const m of P.metrics) {
    if (!P.data[m.id]) continue;
    const sec = document.createElement('section');
    sec.innerHTML = '<h2>' + m.title + '</h2>' +
      (m.blurb ? '<p class="blurb">' + m.blurb + '</p>' : '') +
      '<div class="card">' +
      '  <div class="toolbar">' +
      '    <button data-view="rel" aria-pressed="true">relative to baseline</button>' +
      '    <button data-view="abs" aria-pressed="false">absolute (' + m.unit + ', log)</button>' +
      '    <button data-view="sm" aria-pressed="false">per proof</button>' +
      '    <button data-excl="1" aria-pressed="false" hidden>exclude outlier</button>' +
      '    <button data-scale="1" aria-pressed="true">log scale</button>' +
      '  </div>' +
      '  <div class="legend">' + P.proofs.map(p =>
           '<span><i style="background:' + color(p) + '"></i>' + p.name + '</span>').join('') +
      '  </div><div class="plot"></div><div class="tbl"></div></div>';
    host.appendChild(sec);

    const state = { view: 'rel', excl: false, log: true };
    const exclBtn = sec.querySelector('[data-excl]');
    const relBtn = sec.querySelector('[data-view="rel"]');
    const draw = () => {
      const vis = visibleCols();
      const win = outlier(m.id, vis);
      // Name the actual baseline: the relative view indexes against the selected "from" column,
      // and a button still reading "relative to <first column>" would misdescribe the chart.
      if (relBtn) {
        relBtn.textContent = 'relative to ' + P.columns[vis[cmpPos(vis).from]].name;
      }
      // Excluding rescales the shared axis, which is the whole point; in the per-proof view every
      // panel already has its own scale, so there is nothing for it to do there.
      const off = state.view === 'sm';
      const drop = (!off && state.excl && win) ? win.id : null;
      if (exclBtn) {
        // Hidden outright when this metric has no proof far enough ahead of the rest to distort
        // the axis, and greyed in the per-proof view where every panel is already self-scaled.
        exclBtn.hidden = !win;
        exclBtn.disabled = off || !win;
        exclBtn.style.opacity = (off || !win) ? 0.4 : '';
        exclBtn.style.cursor = (off || !win) ? 'default' : '';
        // Which proof was dropped stays visible in the dimmed legend entry and in the table, so
        // the button itself does not need to carry the name.
        if (win) {
          exclBtn.textContent = (state.excl && !off) ? 'excluding outlier(s)' : 'exclude outlier(s)';
        }
      }
      const scaleBtn = sec.querySelector('[data-scale]');
      if (scaleBtn) scaleBtn.textContent = state.log ? 'log scale' : 'linear scale';
      sec.querySelector('.plot').innerHTML = off
        ? smallMultiples(m.id, vis, state.log)
        : chart(m.id, state.view === 'rel', drop, vis, state.log);
      // the shared legend is redundant once every panel carries its own name
      sec.querySelector('.legend').style.display = off ? 'none' : '';
      sec.querySelectorAll('.legend span').forEach((el, i) => {
        el.style.opacity = (drop && P.proofs[i] && P.proofs[i].id === drop) ? 0.32 : '';
      });
      // The table always shows every proof, excluded or not: the toggle changes what the axis has
      // to accommodate, not what was measured.
      sec.querySelector('.tbl').innerHTML = table(m.id, vis);
    };
    sec.querySelectorAll('.toolbar button').forEach(b => b.addEventListener('click', () => {
      if (b.dataset.excl) {
        if (b.disabled) return;
        state.excl = !state.excl;
        b.setAttribute('aria-pressed', String(state.excl));
      } else if (b.dataset.scale) {
        state.log = !state.log;
        b.setAttribute('aria-pressed', String(state.log));
      } else {
        state.view = b.dataset.view;
        sec.querySelectorAll('[data-view]').forEach(x =>
          x.setAttribute('aria-pressed', String(x.dataset.view === b.dataset.view)));
      }
      draw();
    }));
    draw();
  }
}

document.addEventListener('mousemove', e => {
  const t = e.target.closest ? e.target.closest('[data-tip]') : null;
  if (!t) { tip.style.display = 'none'; return; }
  const d = JSON.parse(t.getAttribute('data-tip'));
  tip.innerHTML = `<b>${d.h}</b>` + d.rows.map(r =>
    `<div><span><i style="display:inline-block;width:9px;height:9px;border-radius:2px;
      background:${r.c};margin-right:6px"></i>${r.n}</span><span>${r.t}</span></div>`).join('');
  tip.style.display = 'block';
  const r = tip.getBoundingClientRect();
  tip.style.left = Math.min(e.clientX + 14, innerWidth - r.width - 10) + 'px';
  tip.style.top = Math.min(e.clientY + 14, innerHeight - r.height - 10) + 'px';
});
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
  panel(); render(); });
new MutationObserver(() => { panel(); render(); })
  .observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
versionTable();
methodText();
panel();
render();
</script>
"""

if __name__ == "__main__":
    sys.exit(main())
