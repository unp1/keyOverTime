#!/bin/zsh
# One bisect step: build the commit currently checked out in $BISECT_WT and time one proof.
#
#   bisect_probe.sh            exit 0 = good (fast), 1 = bad (regressed), 125 = skip (cannot build)
#
# Used as the `git bisect run` payload while hunting the commit that made model-field proofs slow in
# KeY 3.0. The probe is Modelfield__foo_10, whose .key file is byte-identical across the whole
# bisected range, so every step measures the prover and nothing else. It is loaded from a fixed
# checkout rather than from the commit under test, for the same reason.
#
# 125 rather than 1 on a build failure: a commit that does not compile is not evidence either way,
# and telling git to skip it keeps the search honest.
set -u
HERE=${0:A:h}
WT=${BISECT_WT:-/Users/bubel/claude/keyOverTime-versions/bisect-mf}
PROOF=${BISECT_PROOF:-"/Users/bubel/claude/keyOverTime-versions/t3-3.0/key.ui/examples/performance-test/Modelfield(Modelfield__foo_10()).JML_operation_contract.0.key"}
# 2.13-like is ~2.5 s, 3.0-like ~8.0 s; the midpoint separates them with room for build noise.
THRESH=${BISECT_THRESH:-5000}
JAVA_HOME_B=${BENCH_JAVA_HOME:-/Users/bubel/Library/Java/JavaVirtualMachines/temurin-21.0.11/Contents/Home}

SHA=$(git -C $WT rev-parse --short HEAD)
print -r -- "=== probing $SHA" >&2

# The Java-model timer patch is not applied: this hunt only needs proveMs, and the patch anchor does
# not exist on every commit in range.
EXDIR=$WT/key.core.example/src/main/java/org/key_project
mkdir -p $EXDIR
cp $HERE/../java/BenchMain.java $EXDIR/BenchMain.java

if ! ( cd $WT && JAVA_HOME=$JAVA_HOME_B ./gradlew :key.core.example:installDist \
        --console=plain -q >/dev/null 2>&1 ); then
    print -r -- "  $SHA: BUILD FAILED -> skip" >&2
    exit 125
fi

LIB=$WT/key.core.example/build/install/key.core.example/lib
RULES=$WT/key.core/build/resources/main/de/uka/ilkd/key/proof/rules
KEYHOME=$(mktemp -d)
TO=$(command -v gtimeout || command -v timeout || true)
typeset -a WRAP; [[ -n $TO ]] && WRAP=($TO 300) || WRAP=()

OUT=$($WRAP $JAVA_HOME_B/bin/java -Xmx12g \
    -Dlogback.configurationFile=$HERE/quiet-logback.xml \
    -Dkey.prover.parallel=false -Dkey.disregardSettings=true -Dkey.home=$KEYHOME \
    -Dorg.key_project.stdTacletDirectory=$RULES -Dkey.bench.javaTimer=off \
    -Dkey.bench.fileSettings=1 \
    -cp "$LIB/*" org.key_project.BenchMain "$PROOF" 300000 1 2>/dev/null | grep '^BENCH')
rm -rf $KEYHOME

if [[ -z $OUT ]]; then
    print -r -- "  $SHA: no BENCH line (timeout or load error) -> BAD" >&2
    exit 1
fi
MS=$(print -r -- "$OUT" | sed -n 's/.*proveMs=\([0-9][0-9]*\).*/\1/p')
NODES=$(print -r -- "$OUT" | sed -n 's/.*[^a-zA-Z]nodes=\([0-9][0-9]*\).*/\1/p')
if [[ -z $MS ]]; then
    print -r -- "  $SHA: could not read proveMs -> skip" >&2
    exit 125
fi
print -r -- "  $SHA: proveMs=$MS nodes=$NODES threshold=$THRESH" >&2
[[ $MS -lt $THRESH ]] && exit 0 || exit 1
