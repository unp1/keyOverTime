#!/usr/bin/env python3
"""Add a Java-model phase timer to one checkout's ProblemInitializer.

`ProblemInitializer.readJava` is the single place where every compared KeY version reads the Java
source of a problem and converts it into KeY data structures: under Recoder through
`Recoder2KeY.readCompilationUnitsAsFiles`, from the JavaParser merge onward through
`JavaService.parseSpecialClasses` and `JavaService.readCompilationUnits`. Its signature and its
single call site in `prepare(...)` are identical on all five versions, which is what makes the
phase comparable across them.

The patch renames the method to `readJava0` and puts a timing wrapper in its place, so the measured
window is exactly the method body and nothing textual inside it has to be matched. The counter is an
AtomicLong because the goal-parallel prover exists on the later versions; loading is single-threaded
on all of them, but a plain long would be an accident waiting to happen.

Caveat carried into the report: Recoder resolved cross-references eagerly inside this window, while
JavaParser defers part of the symbol solving to on-demand work later in the proof. The post-3.0
figures therefore understate the Java handling relative to 2.12.4, which is why the harness also
reports total load time as the upper bound.

  patch_readjava.py <path-to-key-worktree>
"""
import re
import sys
from pathlib import Path

ANCHOR = ("    private void readJava(EnvInput envInput, InitConfig initConfig)"
          " throws ProofInputException {")

WRAPPER = '''    /**
     * Accumulated nanoseconds spent reading the Java model, summed over every {@link #readJava}
     * call. Benchmark instrumentation: the driver zeroes it before a load and reads it afterwards.
     */
    public static final java.util.concurrent.atomic.AtomicLong JAVA_READ_NANOS =
        new java.util.concurrent.atomic.AtomicLong();

    private void readJava(EnvInput envInput, InitConfig initConfig) throws ProofInputException {
        final long benchT0 = System.nanoTime();
        try {
            readJava0(envInput, initConfig);
        } finally {
            JAVA_READ_NANOS.addAndGet(System.nanoTime() - benchT0);
        }
    }

    private void readJava0(EnvInput envInput, InitConfig initConfig) throws ProofInputException {'''


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    wt = Path(sys.argv[1])
    src = wt / "key.core/src/main/java/de/uka/ilkd/key/proof/init/ProblemInitializer.java"
    if not src.is_file():
        print(f"error: {src} not found", file=sys.stderr)
        return 1

    text = src.read_text()
    if "JAVA_READ_NANOS" in text:
        print(f"already patched: {src}")
        return 0
    if text.count(ANCHOR) != 1:
        print(f"error: expected exactly one readJava definition in {src},"
              f" found {text.count(ANCHOR)}", file=sys.stderr)
        return 1
    # The wrapper must be the only caller of the renamed body, so guard against a version that
    # calls readJava more than once: the single call site in prepare(...) is what the timing
    # assumes, and an extra one would silently double-count nested loads.
    calls = len(re.findall(r"(?<!private void )readJava\(envInput, initConfig\)", text))
    if calls != 1:
        print(f"error: expected exactly one readJava call site, found {calls}", file=sys.stderr)
        return 1

    src.write_text(text.replace(ANCHOR, WRAPPER))
    print(f"patched: {src}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
