#!/usr/bin/env python3
"""Render the comparison as a vector PDF: a cover naming the versions, then two pages per metric.

  gen_pdf.py out/tidy.csv -o out/report.pdf

The cover comes from versions.csv. A PDF is the artefact that gets detached from its context and
mailed on, and a chart axis reading "3.0-rc" means nothing to someone who was never told which
commit that is.

The first page of each metric carries two panels of the same data: on the left every proof indexed
to its own value on the oldest version, on the right the absolute values. The comparison needs both.
Indexed, every proof starts at the same point and the shape of the change is readable; absolute, the fact
that they span three orders of magnitude stays visible instead of being normalised away. The second
page is small multiples, one self-scaled panel per proof.

Both panels use a log y axis, for different reasons. On the indexed panel it makes halving and
doubling the same distance from the baseline, so an improvement and a regression of equal size look
equal. On the absolute panel it is the only way proofs of that spread share an axis at all.

Colours are slots 1-7 of the validated categorical palette in its documented order; the order is the
colour-blindness safety mechanism, so it is not rearranged. Every line is also labelled at its right
end, which is what discharges the palette's contrast relief for the lighter slots.
"""
import argparse
import csv
import math
import sys
import textwrap

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.ticker import FixedLocator, FuncFormatter, NullFormatter  # noqa: E402

from gen_report import (COLUMNS, METRICS, PROOFS, VERSIONS, WORKERS,  # noqa: E402
                        build, read_mt_capable)

INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#7c7a74"
GRID = "#dbdad4"
BAND = "#00000008"   # shading behind the goal-parallel columns

# Minimum vertical separation between two rows of the label column, in axes fractions.
LABEL_GAP = 0.055
# Clear air between the last marker and its label, as a fraction of the axes width.
LABEL_OFFSET = 0.055


def load(path):
    """The tidy rows, reduced to the per-column value lists gen_report defines."""
    with open(path) as fh:
        values, _status = build(list(csv.DictReader(fh)))
    return values


def series(values, metric):
    """[(proof name, colour, [value per column or None])] for the proofs that produced anything."""
    per_proof = values.get(metric, {})
    return [(name, light, per_proof[pid])
            for pid, name, light, _dark in PROOFS if pid in per_proof]


def ratio_fmt(v):
    if v >= 1:
        return f"{v:g}×" if v != 1 else "1×"
    return f"÷{1 / v:g}"


def si(v):
    if v >= 1000:
        return f"{v:,.0f}".replace(",", " ")
    if v >= 10:
        return f"{v:.0f}"
    return f"{v:g}"


def panel(ax, data, indexed, unit):
    xs = list(range(len(COLUMNS)))
    plotted = []
    for name, colour, vals in data:
        if indexed:
            base = vals[0]
            if base in (None, 0):
                continue  # nothing to index against: this proof was not measured on the baseline
            ys = [None if v is None else v / base for v in vals]
        else:
            ys = list(vals)
        pts = [(x, y) for x, y in zip(xs, ys) if y is not None and y > 0]
        if not pts:
            continue
        ax.plot([p[0] for p in pts], [p[1] for p in pts], color=colour, lw=2,
                marker="o", ms=6, mec="white", mew=1.4, zorder=3, solid_capstyle="round")
        plotted.append((name, colour, pts[-1]))

    # a shaded band behind each parallel column, so the SC/MT pairs are legible as pairs
    for i, (_c, mode, *_r) in enumerate(COLUMNS):
        if mode == "mt":
            ax.axvspan(i - 0.5, i + 0.5, color=BAND, lw=0, zorder=0)

    ax.set_yscale("log")
    ax.set_xlim(-0.4, len(COLUMNS) - 1 + 0.4)
    ax.set_xticks(xs)
    ax.set_xticklabels([n + ("\n" + q if q else "") for _c, _m, n, q, _d in COLUMNS],
                       fontsize=7.5, color=INK2)
    ax.tick_params(axis="y", labelsize=8.5, colors=MUTED, which="major")
    ax.tick_params(axis="y", which="minor", length=0)
    ax.tick_params(axis="x", length=0)

    if indexed:
        # Include the 1x baseline but do not mirror the range around it: when every proof improved,
        # a symmetric axis spends half its height above the baseline on nothing.
        lo, hi = ax.get_ylim()
        vals = [y for _n, _c, pts in [(0, 0, ax.get_lines())] for ln in pts
                for y in ln.get_ydata() if y is not None and y > 0]
        if vals:
            lo, hi = min(min(vals), 1.0), max(max(vals), 1.0)
            pad = max((math.log(hi) - math.log(lo)) * 0.09, 0.05)
            ax.set_ylim(math.exp(math.log(lo) - pad), math.exp(math.log(hi) + pad))
        ax.axhline(1.0, color=GRID, lw=1.4, zorder=1)
        ticks = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50]
        lo, hi = ax.get_ylim()
        keep = [t for t in ticks if lo <= t <= hi]
        ax.yaxis.set_major_locator(FixedLocator(keep))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _p: ratio_fmt(v)))
        ax.set_ylabel(f"relative to {COLUMNS[0][2]}", fontsize=9, color=INK2)
    else:
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _p: si(v)))
        ax.set_ylabel(unit, fontsize=9, color=INK2)
    ax.yaxis.set_minor_formatter(NullFormatter())

    ax.grid(axis="y", color=GRID, lw=0.8, ls=(0, (2, 4)), zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)

    # A label column in the right margin: one row per series, at the height of that series' last
    # point, pushed apart where endpoints coincide. Each row is a coloured marker plus the name in
    # ink, so identity never rests on colour alone and the label column doubles as the legend.
    lo, hi = ax.get_ylim()
    span = math.log10(hi) - math.log10(lo)

    def frac(y):
        return (math.log10(y) - math.log10(lo)) / span

    plotted.sort(key=lambda t: t[2][1])
    placed, floor = [], -1.0
    for name, colour, (_x, y) in plotted:
        f = max(frac(y), floor + LABEL_GAP)
        placed.append([name, colour, f])
        floor = f
    # Pushing rows apart can run the column off the top; slide it back down, then off the bottom if
    # the column is taller than the axes, in which case it is centred and the gap simply shrinks.
    if placed:
        shift = max(0.0, placed[-1][2] - 1.0)
        low = placed[0][2] - shift
        if low < 0.0:
            shift += low / 2
        for row in placed:
            row[2] -= shift
    for name, colour, f in placed:
        ax.plot([1 + LABEL_OFFSET], [f], transform=ax.transAxes, marker="s", ms=4,
                color=colour,
                clip_on=False, zorder=5)
        ax.text(1 + LABEL_OFFSET + 0.03, f, name, transform=ax.transAxes, fontsize=7,
                color=INK2,
                va="center", ha="left", clip_on=False)


def small_multiples(fig, data, title, caption):
    """Fill fig with one mini panel per proof, each indexed to the first column and self-scaled.

    The two-panel page shares one y axis across every proof, which is what makes the proofs
    comparable with each other and also what flattens a 1.7x improvement sitting beside a 19x one.
    Here every proof owns its panel, so the shape of each trajectory is legible on its own terms.
    """
    keep = [(n, c, v) for n, c, v in data if v[0] not in (None, 0)]
    cols = 4
    rows = math.ceil(len(keep) / cols)
    axes = fig.subplots(rows, cols, squeeze=False)
    xs = list(range(len(COLUMNS)))
    for k, ax in enumerate([a for r in axes for a in r]):
        if k >= len(keep):
            ax.axis("off")
            continue
        name, colour, vals = keep[k]
        rel = [None if v is None else v / vals[0] for v in vals]
        for i, (_c, mode, *_r) in enumerate(COLUMNS):
            if mode == "mt":
                ax.axvspan(i - 0.5, i + 0.5, color=BAND, lw=0, zorder=0)
        pts = [(x, y) for x, y in zip(xs, rel) if y is not None and y > 0]
        ax.plot([p[0] for p in pts], [p[1] for p in pts], color=colour, lw=1.8,
                marker="o", ms=3.6, mec="white", mew=1, zorder=3)
        ax.axhline(1.0, color=GRID, lw=1, zorder=1)
        ax.set_yscale("log")
        ax.set_xlim(-0.5, len(COLUMNS) - 0.5)
        ax.set_xticks(xs)
        ax.set_xticklabels([n + (" " + q if q else "") for _c, _m, n, q, _d in COLUMNS],
                           fontsize=5.4, color=MUTED, rotation=38, ha="right")
        ax.tick_params(axis="both", length=0, labelsize=5.4, colors=MUTED)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _p: ratio_fmt(v)))
        ax.yaxis.set_minor_formatter(NullFormatter())
        last = next((v for v in reversed(rel) if v is not None), None)
        ax.set_title(name + ("   " + ratio_fmt(last) if last is not None else ""),
                     fontsize=7.5, color=INK, loc="left", pad=4)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(GRID)
    fig.suptitle(title + "  -  per proof, each on its own scale",
                 x=0.045, y=0.965, ha="left", fontsize=13, color=INK, weight=600)
    if caption:
        fig.text(0.045, 0.925, "\n".join(textwrap.wrap(caption, 130)),
                 ha="left", va="top", fontsize=8, color=INK2, linespacing=1.5)


def cover(fig, title, mt_capable):
    """A first page naming the versions, so the PDF explains its own axis.

    A figure captioned "3.0-rc" is unreadable to anyone who has not been told which commit that is,
    and unlike the HTML report a PDF is what gets detached from its context and mailed around.
    """
    L, R = 0.055, 0.955
    fig.text(L, 0.94, title, ha="left", va="top", fontsize=20, color=INK, weight=600)
    fig.text(L, 0.855, "Versions compared", ha="left", va="top", fontsize=12, color=INK,
             weight=600)

    def rule(y):
        # A real line rather than a run of underscores, which only spans as far as its own glyphs.
        fig.add_artist(Line2D([L, R], [y, y], color=GRID, lw=0.8,
                              transform=fig.transFigure, zorder=0))

    y, row_gap = 0.775, 0.132
    rule(y + 0.028)
    for v in VERSIONS:
        fig.text(L, y, v["display"], ha="left", va="top", fontsize=11.5, color=INK, weight=600)
        rev = v["commitish"]
        short = rev[:10] if len(rev) >= 12 and all(c in "0123456789abcdef" for c in rev) else rev
        fig.text(L, y - 0.030, short, ha="left", va="top", fontsize=8, color=MUTED,
                 family="monospace")
        fig.text(0.29, y, "SC + MT 4x" if mt_capable.get(v["name"]) else "SC",
                 ha="left", va="top", fontsize=9, color=INK2)
        fig.text(0.40, y, "\n".join(textwrap.wrap(v["description"], 88)),
                 ha="left", va="top", fontsize=9, color=INK2, linespacing=1.55)
        y -= row_gap
        rule(y + 0.028)

    # Assembled rather than written out: the counts change with versions.csv, and a fixed sentence
    # naming them goes quietly wrong the moment a row is added or removed.
    n = len(VERSIONS)
    n_mt = sum(1 for v in VERSIONS if mt_capable.get(v["name"]))
    if n_mt == 0:
        modes = "No version here has the goal-parallel prover, so each contributes one column."
    elif n_mt == n:
        modes = (f"Every version has the goal-parallel prover, so each contributes two columns: "
                 f"single-threaded, and at {WORKERS} workers.")
    else:
        modes = (f"{n_mt} of the {n} versions have the goal-parallel prover and contribute two "
                 f"columns each, single-threaded and at {WORKERS} workers; the other {n - n_mt} "
                 f"contribute one.")
    body = (f"Each of the {n} versions is built from its own detached worktree with the same JDK "
            f"that runs the measurements, and measured on its own examples tree so that no version "
            f"is asked to parse a .key file written for another. Every run is a fresh JVM that "
            f"neither reads nor writes the persisted settings. Timings are the minimum over the "
            f"warm reps; node and branch counts are exact. {modes}")
    fig.text(L, 0.105, "\n".join(textwrap.wrap(body, 128)),
             ha="left", va="top", fontsize=8, color=MUTED, linespacing=1.6)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tidy")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--title", default="KeY performance over time")
    args = ap.parse_args()

    values = load(args.tidy)
    if not values:
        print("no usable rows in tidy csv", file=sys.stderr)
        return 1

    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"],
        "pdf.fonttype": 42,   # embed as TrueType, so the text stays selectable and searchable
        "figure.facecolor": "white",
        "axes.facecolor": "white",
    })

    with PdfPages(args.out) as pdf:
        pages = 0
        fig = plt.figure(figsize=(12.6, 6.4))
        cover(fig, args.title, read_mt_capable())
        pdf.savefig(fig)
        plt.close(fig)
        pages += 1

        for mid, title, unit, blurb in METRICS:
            data = series(values, mid)
            if not data:
                continue
            fig, axes = plt.subplots(1, 2, figsize=(12.6, 4.9))
            # right and wspace leave room for each panel's label column without starving the plots
            fig.subplots_adjust(left=0.062, right=0.83, top=0.75, bottom=0.12, wspace=0.46)
            panel(axes[0], data, True, unit)
            panel(axes[1], data, False, unit)
            fig.suptitle(title, x=0.062, y=0.945, ha="left", fontsize=15, color=INK, weight=600)
            # Wrapped by hand: matplotlib's wrap=True measures against the figure edge, not the
            # margins set above, so a long blurb runs off the page.
            fig.text(0.062, 0.865, "\n".join(textwrap.wrap(blurb, 122)),
                     ha="left", va="top", fontsize=8.5, color=INK2, linespacing=1.5)
            fig.text(0.062, 0.028,
                     "single-threaded  ·  warm rep, minimum over reps  ·  log axes on both panels",
                     ha="left", fontsize=7.5, color=MUTED)
            pdf.savefig(fig)
            plt.close(fig)
            pages += 1

            fig = plt.figure(figsize=(12.6, 6.4))
            fig.subplots_adjust(left=0.045, right=0.985, top=0.84, bottom=0.09,
                                wspace=0.34, hspace=0.72)
            small_multiples(fig, data, title, blurb)
            pdf.savefig(fig)
            plt.close(fig)
            pages += 1

        meta = pdf.infodict()
        meta["Title"] = args.title
        meta["Subject"] = "  ".join(f"{n} ({d})" for _c, _m, n, _q, d in COLUMNS)

    print(f"wrote {args.out} ({pages} pages)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
