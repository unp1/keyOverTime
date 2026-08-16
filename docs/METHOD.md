# Method

Why the harness is built this way, how to extend it, and what the numbers do and do not support.

## Design

- **One harness class, `java/BenchMain.java`.** It loads a `.key` problem, caps the automatic rule
  applications, runs automode once and prints one machine-readable line per rep. It deliberately
  uses only API that is stable across the whole compared span, so the same source compiles unchanged
  on every version and all of them are measured by identical logic. Two API moves forced that
  discipline: `KeYJavaType` moved package when Recoder was removed (so the contract fallback binds it
  with `var` instead of naming it), and the ncore `org.key_project.logic` types exist only on the
  newer versions (so nothing here touches them).
- **`installDist`, then a clean JVM per run.** Each checkout is assembled into a self-contained
  `lib/` directory, and each proof runs as `java -cp lib/* org.key_project.BenchMain`. No Gradle in
  the measured path, so per-proof timing carries no daemon or task overhead.
- **Reps inside the JVM.** `BenchMain <file> <maxSteps> <reps>` reloads and reproves the problem
  `reps` times in one JVM. Rep 1 warms the JIT; reps 2 and later are the warm measurement. Node
  counts must be identical across reps; a divergence is flagged `nondet` rather than averaged.
- **Nodes are the decisive signal.** The node count is exact and deterministic, so it carries the
  comparison. Times are reported too but are inherently noisier; the driver runs one JVM at a time,
  and the aggregator takes the minimum warm rep.
- **Each version on its own examples.** The surface syntax of `.key` files was renamed over the
  compared span (`<created>` to `#$created`, `int[]::instance(x)` to `instance<[int[]]>(x)`, and the
  position of the JML `helper` modifier in a Java source file), so one canonical tree simply would
  not parse on the older versions. The renames are semantics-preserving, so the proof obligation is
  the same problem in every tree.
- **Settings are supplied explicitly, never inherited.** KeY persists settings under a per-version,
  mutable `~/.key/<version>/` and a headless run both reads it at startup and writes it back on exit.
  Without isolation, settings drift between runs and different versions read different directories.
  Every run therefore gets `-Dkey.disregardSettings=true` plus a throwaway `-Dkey.home`, and
  `key.prover.parallel` is always set explicitly: leaving it unset resolves it to the persisted
  value, which silently turns an `sc` run parallel.
- **The taclet base is read from a directory, not a jar.** Every run gets
  `-Dorg.key_project.stdTacletDirectory` pointing at that version's unpacked build resources. KeY
  2.12.4 cannot follow the `\include` chain out of a jar at all, and reading it the same way on
  every version keeps the taclet-reading part of `loadMs` comparable.
- **The configuration is checked, not assumed.** Every run prints the strategy, the strategy
  properties and the taclet choices it actually used, and `python/settings_diff.py` diffs them across
  versions per proof. Where a proof shows no differing keys, a change in its numbers is the prover
  changing; where keys differ, the report says so instead of attributing the change to the engine.

## Adding a version

Add a row to `config/versions.csv` and rerun. The build step detects from the source whether the
version has the goal-parallel prover and records that in `<workdir>/checkouts.csv`, so a version
without it is skipped in the `mt` phase rather than contributing a second single-threaded number
under a parallel label.

Two things to expect when reaching further back than the versions already listed:

- **`BenchMain` may not compile.** Its API surface is small and chosen for stability, but a package
  move like `de.uka.ilkd.key.java.abstraction` to `de.uka.ilkd.key.java.ast.abstraction` will still
  break a build. The fix is to widen `BenchMain` (usually to `var`, occasionally to reflection), not
  to fork it per version: one source measured by identical logic is the point.
- **`patch_readjava.py` may not find its anchor.** It expects exactly one
  `private void readJava(EnvInput, InitConfig)` and exactly one call site, and refuses to patch
  otherwise rather than time the wrong thing. Adjust the anchor, or build that version with
  `--no-java-timer`.

## Comparing worker counts

A column exists because a row in `config/versions.csv` says so, so a version at two worker counts is
two rows on one commit, the second naming its `workers`. The driver resolves both rows to the same
sha and checks the commit out once, and the build step is idempotent, so the second row costs a
no-op Gradle run rather than a second build of KeY. A row naming a worker count is measured in the
`mt` phase alone; measuring it single-threaded as well would only repeat its neighbour's `sc` number
under a second name.

The alternative, a `--workers 1,2,4,8` sweep, was not taken. The measurement layer would support it,
since `run_bench.sh` already separates the prover mode from the output tag, but the report keys its
data, its column styling and its filter buttons off the literal mode `mt` in about a dozen places,
most of them in the embedded JavaScript. Rows cost nothing there.

## Adding a measurement

There are two kinds, and only the second needs a source patch.

**A metric `BenchMain` can compute on its own**, meaning anything reachable from the loaded `Proof`,
the `Services`, or a JVM MXBean. Add it to the `BENCH` line in `java/BenchMain.java`, add its name to
`METRIC_FIELDS` in `python/aggregate.py`, add a row to `OUT_FIELDS`, and add an entry to `METRICS` in
`python/gen_report.py` to give it a chart. Nothing else is involved: each `key=value` token of a
`BENCH` line becomes its own column in the results file, so a new token needs no driver change.

**A phase timer inside KeY.** The Java-model phase is the worked example. `python/patch_readjava.py`
renames `ProblemInitializer.readJava` to `readJava0` and puts a timing wrapper in its place that
accumulates into a `public static final AtomicLong JAVA_READ_NANOS`. To time a different phase, copy
that script, point `ANCHOR` at the method you want to bracket, and give the counter a new name;
`BenchMain` then reads it the same way `resolveJavaTimer()` reads this one. Two rules make such a
patch trustworthy: pick a method whose signature and call sites are identical on every compared
version, or the versions are not measuring the same window, and have the script refuse to patch when
it cannot verify that, which is why this one counts its anchor and its call sites and fails instead
of guessing.

## Disabling a measurement

`--no-java-timer` leaves every `ProblemInitializer` untouched, so the checkouts are built unmodified
apart from the added `BenchMain`. The Java-model metric is then unavailable and reports `javaMs=-1`;
everything else is unaffected. Reach for it when a checkout must not be modified, or when a new
version's `readJava` does not match the patch anchor and you would rather have the other metrics now
than fix the anchor first.

The mechanism underneath is `-Dkey.bench.javaTimer`, which the driver passes on every run and which
`BenchMain.resolveJavaTimer()` reads:

| value | behaviour |
|---|---|
| `auto` (default) | use the counter where it is present; report `javaMs=-1` where it is not |
| `on` | require it, so a botched patch is never mistaken for a measurement |
| `off` | do not collect the metric at all |

Each `BENCH` line carries `javaTimer=on\|off\|absent`, so which of the three applied is visible per
run rather than inferred.

To drop a measurement from the report without changing how it is collected, remove its entry from
`METRICS` in `python/gen_report.py`, or set `BENCH_METRICS` to the ids you want.

## Caveats

- **JavaParser is lazier than Recoder.** Recoder resolved cross-references eagerly inside
  `readJava`; JavaParser defers part of the symbol solving to on-demand work during the proof. The
  post-3.0 `javaMs` therefore understates the Java handling relative to 2.12.4, which is why the
  report shows total `loadMs` beside it as the upper bound.
- **`loadMs` is the warm figure.** The aggregator takes the minimum over the warm reps. The first
  load in a fresh JVM is a different measurement and a much closer race; do not quote the warm
  speed-up as what a user sees on the first file they open after starting KeY.
- **Not every proof closes on every version.** A version that leaves goals open did less work than
  one that closed, so its time is not comparable on equal terms. The aggregator marks those rows
  `OPEN` and the report flags them; read the node count first in those cases. Where the mismatch is
  known in advance, exclude the proof from those versions with `onlyVersions`.
- **The x axis is a release lineage, not a linear history.** A release-candidate entry can be a
  sibling of a main-line commit rather than its ancestor. It sits where it does because that is
  where its content sits.
- **Timing is noisy, nodes are not.** Lead with node counts. Treat warm-time deltas under a few
  percent as noise, and prefer the larger proofs for the timing story, since JVM warm-up weighs less
  on them.
- **macOS.** `timeout` is not present by default; the driver uses `gtimeout` (Homebrew or MacPorts
  coreutils) and falls back to no cap if neither is found.
