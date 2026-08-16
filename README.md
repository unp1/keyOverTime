# KeY over time

A performance comparison of several [KeY](https://key-project.org) versions on one fixed corpus,
from a single command. Per version and per proof it reports proof size (nodes, branches), automode
time, end-to-end wall time, the Java parsing and conversion phase, total load time, and where the
version supports it the same figures under the goal-parallel prover.

## Run it

```zsh
./keyovertime --key-repo ~/src/key --workdir ~/keyovertime
```

Those two paths are the whole required input:

- **`--key-repo`** a clone of the KeY repository. It is only ever read: the versions are checked out
  from it with `git worktree`.
- **`--workdir`** where everything generated goes. Nothing is written next to this script.

Everything else is found or derived. The JDK is detected: 21 or newer, and matching the machine's
architecture, since one JDK has to build and run every version for the timings to be comparable. The
Python environment is created and its two dependencies installed on first use. The checkouts are
created, patched, built and registered. Both measurement phases run, and the report is rendered.

Start with `--dry-run` to see what would run, then `--smoke` (one rep per proof) to see it run, then
the real thing. Expect hours: the corpus is real proofs on six versions, and every version is built
from source first.

When it finishes:

```
<workdir>/out/tidy.csv          one row per version, proof and phase
<workdir>/out/report.html       the page to explore, with hover
<workdir>/out/report.pdf        the figures, as vectors
<workdir>/out/keyOverTime.xlsx  the workbook
<workdir>/out/settings_sc.txt   which effective settings differ across versions, per proof
```

Every step is idempotent, so a rerun after an interruption picks up where it left off: an existing
checkout at the right commit is reused, and Gradle skips a build whose inputs did not change.

## What to edit

Two CSV files, both shipped with a working default, both with a header row and `#` comments, so they
open in a spreadsheet and explain themselves.

**`config/versions.csv`** is the versions to compare, oldest first, one row per version. This is the
only place a version is declared; the row order here is the column order in every chart and table.

```csv
name,commit,display,description,workers
t3-3.0,89522a5a40,KeY-3.0,KeY 3.0 release candidate,
```

`commit` is anything git resolves in your clone: a sha, a tag, or a remote branch. `display` is the
label on chart axes, so keep it short. Add a row and rerun; nothing else changes.

`workers` is blank for the run's default, set with `--workers`. A row that names a count is measured
in the parallel phase only, at that count, which is how one version appears at more than one worker
count: give it a second row on the same commit.

```csv
t6-main,origin/main,KeY-main,Current tip of main,
t6-main-8w,origin/main,KeY-main,The same commit at eight workers,8
```

That yields three columns, `SC`, `MT 4x` and `MT 8x`, and costs one build: rows naming the same
commit share a checkout.

**`config/corpus.csv`** is the proofs. The shipped set is chosen from the examples KeY itself ships,
so it works against any clone with no further downloads.

```csv
group,label,path,maxSteps,fileSettings,onlyVersions
examples,Saddleback,heap/saddleback_search/Saddleback_search.key,300000,0,
```

`path` is relative to the corpus directory, or absolute for a one-off proof elsewhere. `maxSteps`
caps the automatic rule applications, `0` means no cap.
`fileSettings=1` proves on the settings stored in the file rather than the version's defaults.
`onlyVersions` is blank for every version, or the names from `versions.csv` that this proof applies
to; use it where a proof cannot close on the older versions, so the report shows a stated gap
instead of a number that means something different from its neighbours.

### Your own corpus

The shipped corpus names proofs that ship with KeY, so its paths resolve against the examples tree of
whichever version is being measured. To benchmark proofs of your own, put them in a directory and
say where it is:

```bash
./keyovertime --key-repo ~/src/key --workdir ~/keyovertime \
              --corpus ~/mycorpus/corpus.csv --corpus-dir ~/mycorpus
```

Relative paths then resolve under `~/mycorpus`. If one version needs its own variant of a proof, for
instance because a syntax change means an older prover cannot parse what the newer one wants, put
that variant under a directory named after the version. It is used for that version alone, and every
other file still comes from the corpus directory, so varying one proof costs one file rather than a
second copy of the corpus:

```
mycorpus/
  corpus.csv
  arith/add.key
  list/remove.key
  t1-2.12.4/arith/add.key      the 2.12.4 variant, used only by that version
```

Before anything is built, every proof is checked to exist for every version measured on it, and all
the missing ones are reported at once. The run also says which proofs were taken from a version's own
directory rather than the shared one. Without `--corpus-dir` the absolute paths are still checked,
since they need no checkout; only paths relative to a version's own examples tree have to wait.

## Options

| | |
|---|---|
| `--dry-run`, `-n` | print what would run, touch nothing |
| `--smoke` | one rep per proof, into a separate output directory |
| `--only sc` \| `--only mt` | one measurement phase instead of both |
| `--skip-build` | measure with what is already built |
| `--build-only` | stop after the checkouts and builds |
| `--report-only` | re-render from results already in the output directory |
| `--load-only` | measure parsing and loading only (automode capped at one rule) |
| `--reps <n>` | reps per JVM (default 3; rep 1 warms the JIT) |
| `--workers <n>` | parallel prover threads for the mt phase (default 4) |
| `--versions`, `--corpus` | use other input files |
| `--corpus-dir <dir>` | directory relative corpus paths resolve against (default: the measured version's examples tree) |
| `--java-home <path>` | build and run with this JDK instead of the detected one |
| `--outdir <dir>` | results and report elsewhere than `<workdir>/out` |
| `--jobs <n>` | Gradle workers during the build |
| `--no-java-timer` | leave every checkout unpatched; `javaMs` is then not collected |
| `--title <text>` | title for the report and the PDF cover |

`--key-repo` and `--workdir` can also come from the environment, as `KEY_REPO` and
`KEYOVERTIME_WORKDIR`. The measurement step additionally reads `XMX`, `TMO`, `JAVA_TIMER` and
`BASELINE_SETTINGS`; see the header of `lib/run_bench.sh`.

## Layout

```
keyovertime          the entry point: checkouts, builds, both phases, report
config/              the two files you edit: versions.csv, corpus.csv
java/                BenchMain (the harness) and MatchBench, copied into each checkout
lib/                 build_config.sh, run_bench.sh, quiet-logback.xml
python/              aggregation, the three report generators, the phase-timer patch, csvio
tools/               bisect_probe.sh, for finding the commit a regression came in on
docs/METHOD.md       why it is built this way, and what the numbers do and do not support
```

`<workdir>/checkouts.csv` is written by the build step, one row per built checkout. It is generated;
edit `versions.csv` instead.

## Requirements

zsh, git, Python 3.9 or newer, and a JDK 21 or newer that matches the machine's architecture. The
KeY clone brings its own Gradle wrapper. On macOS, `gtimeout` from coreutils gives each run a
wall-clock cap; without it runs are uncapped.

## Licence

MIT, except the two sources under `java/`, which carry KeY's GPL-2.0-only header because they are
compiled inside a KeY checkout and against the KeY API. See [LICENSE](LICENSE). KeY itself is not
distributed here.
