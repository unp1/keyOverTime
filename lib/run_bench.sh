#!/bin/zsh
# Run every registered checkout over every proof in the corpus, in a clean JVM per (version, proof)
# with REPS reps inside each JVM (rep 1 warms the JIT, reps >= 2 are the warm measurement). One JVM
# at a time, so wall-clock timings are not perturbed by contention.
#
#   run_bench.sh <sc|mt> [tag]
#
# Each version is measured on its own examples tree, because the surface syntax of .key files was
# renamed over the compared span and a single canonical tree would not parse on the older versions.
# The renames are semantics-preserving, so the proof obligation is the same problem in every tree.
#
# Settings are controlled explicitly so results never depend on, or contaminate, the persisted
# ~/.key directory:
#   - key.disregardSettings=true plus a throwaway key.home per run: KeY neither reads nor writes it.
#   - key.prover.parallel is forced (false for sc, true for mt); leaving it unset resolves to the
#     persisted value, which silently makes an "sc" run parallel.
#   - proofs run on each version's own shipped defaults, which is the "KeY as released over time"
#     reading, unless the corpus row sets fileSettings=1 or BASELINE_SETTINGS is given.
#
# sc : single-threaded automode across every version.
# mt : goal-parallel prover at WORKERS threads. Versions without it are skipped rather than
#      contributing a second single-threaded number under a parallel label.
#
# Environment:
#   BENCH_JAVA_HOME    JDK used for every measured run (required)
#   CONFIGS            registry CSV written by build_config.sh (required)
#   CORPUS             corpus CSV (required)
#   CORPUS_DIR         directory relative corpus paths resolve against; empty means each version's
#                      own examples tree
#   OUTDIR             output directory (required)
#   KEYOVERTIME_HOME   this project's root (default: the parent of this script)
#   BASELINE_SETTINGS  optional proof-settings JSON forced onto every non-fileSettings proof
#   REPS               reps per JVM (default 3)
#   XMX                heap for each JVM (default 12g)
#   TMO                per-run wall-clock cap in seconds via gtimeout/timeout (default 1800)
#   WORKERS            mt worker threads (default 4)
#   JAVA_TIMER         auto|on|off for the Java-model counter (default auto)
set -u
MODE=${1:-sc}
TAG=${2:-$MODE}
HOME_DIR=${KEYOVERTIME_HOME:-${0:A:h:h}}
OUTDIR=${OUTDIR:?OUTDIR is not set}
CONFIGS=${CONFIGS:?CONFIGS is not set}
CORPUS=${CORPUS:?CORPUS is not set}
CORPUS_DIR=${CORPUS_DIR:-}
REPS=${REPS:-3}
XMX=${XMX:-12g}
TMO=${TMO:-1800}
WORKERS=${WORKERS:-4}
BASELINE_SETTINGS=${BASELINE_SETTINGS:-}
# auto : use the Java-model counter where the checkout was patched, report javaMs=-1 where not
# on   : require it, so a failed patch is an error rather than a silently missing metric
# off  : do not collect the metric at all
JAVA_TIMER=${JAVA_TIMER:-auto}
CSVIO=$HOME_DIR/python/csvio.py
mkdir -p $OUTDIR/err

TO=$(command -v gtimeout || command -v timeout || true)
[[ -z $TO ]] && print -r -- "warning: no gtimeout/timeout found; running without a per-run cap"
# Resolve java to an absolute path: a timeout wrapper execs its argument directly and does not
# always inherit a PATH that resolves a bare "java". Pinning it here also keeps every run on the
# JDK the builds used, rather than the machine's default.
JAVA=${BENCH_JAVA_HOME:?BENCH_JAVA_HOME is not set}/bin/java
[[ -x $JAVA ]] || { print -ru2 -- "error: no java at $JAVA"; exit 1 }
# Quiet the default DEBUG logging (a load-phase flood that bloats output and perturbs timing); the
# config routes WARN and above to stderr so stdout carries only the lines the harness parses.
typeset -a LOGARG
[[ -f $HOME_DIR/lib/quiet-logback.xml ]] && LOGARG=(-Dlogback.configurationFile=$HOME_DIR/lib/quiet-logback.xml)
print -r -- "java: $JAVA   timeout: ${TO:-<none>}   mode: $MODE"

# Names declared in the versions file, if it is there. A registry row whose name is not declared is
# stale, typically a version that was renamed, and measuring it wastes a full pass on a build nobody
# asked for, under a label the report will not show.
typeset -A DECLARED
VERSIONS=${BENCH_VERSIONS:-}
if [[ -n $VERSIONS && -f $VERSIONS ]]; then
    while IFS=$'\t' read -r vn rest; do
        DECLARED[$vn]=1
    done < <(python3 $CSVIO rows $VERSIONS --pad 4)
fi

typeset -a CNAMES CLIBS CEXAMPLES CRULES CWORKERS
while IFS=$'\t' read -r n lib ex ru mt w; do
    # A row that names its own worker count exists to add a parallel column, so it is measured in
    # the mt phase only; running it single-threaded too would repeat its neighbour's sc number.
    if [[ $MODE == sc && -n $w ]]; then
        print -r -- "skipping $n in sc mode: it is a parallel-only row ($w workers)"
        continue
    fi
    if (( ${#DECLARED} )) && [[ -z ${DECLARED[$n]:-} ]]; then
        print -r -- "skipping $n: registered but not declared in the versions file (stale row?)"
        continue
    fi
    # In mt mode, skip versions without the goal-parallel prover. Running them anyway would produce
    # a second single-threaded number under an "mt" label, which is worse than a gap in the chart.
    if [[ $MODE == mt && ${mt:-0} != 1 ]]; then
        print -r -- "skipping $n in mt mode: no parallel prover"
        continue
    fi
    CNAMES+=$n; CLIBS+=$lib; CEXAMPLES+=$ex; CRULES+=$ru; CWORKERS+=${w:-$WORKERS}
done < <(python3 $CSVIO rows $CONFIGS --pad 6)
(( ${#CNAMES} )) || { print -ru2 -- "error: no versions registered in $CONFIGS"; exit 1 }

# Version names as a set, so a corpus row naming an unknown one can be reported rather than
# silently excluding everything.
typeset -A VERSIONSET
for n in $CNAMES; do VERSIONSET[$n]=1; done

OUT=$OUTDIR/results_$TAG.csv
PROG=$OUTDIR/$TAG.progress
print -r -- "phase,config,group,label,wallMs,bench" > $OUT
: > $PROG
print -r -- "versions: ${CNAMES}  mode=$MODE reps=$REPS workers=$WORKERS" >> $PROG

t0=$SECONDS
while IFS=$'\t' read -r g l p m s only; do
    # settings mode: the proof's own \settings, or a forced baseline, or the version's own defaults
    if [[ ${s:-0} == 1 ]]; then
        SET=(-Dkey.bench.fileSettings=1)
    elif [[ -n $BASELINE_SETTINGS ]]; then
        SET=(-Dkey.bench.settingsFile=$BASELINE_SETTINGS)
    else
        SET=()
    fi
    # A corpus row may name the versions it applies to, separated by commas or spaces: both, so a
    # natural "a b" is not read as one name that matches nothing and skips every version.
    typeset -a ONLY
    ONLY=(${(s: :)${only//,/ }})
    if (( ${#ONLY} )); then
        for o in $ONLY; do
            if [[ -z ${VERSIONSET[$o]:-} ]]; then
                print -r -- "warning: corpus row '$l' names onlyVersions=$o, which is not a" \
                            "registered version; that name matches nothing and excludes every version"
            fi
        done
    fi
    for i in {1..${#CNAMES}}; do
        c=$CNAMES[$i]; LIB=$CLIBS[$i]
        # force the parallel flag explicitly, never leave it unset: unset resolves to whatever was
        # persisted, which silently makes an sc run parallel
        if [[ $MODE == mt ]]; then
            PAR=(-Dkey.prover.parallel=true -Dkey.prover.parallel.threads=$CWORKERS[$i])
        else
            PAR=(-Dkey.prover.parallel=false)
        fi
        # Skipping here leaves no row at all for the excluded versions, which is what the report
        # should show: a stated exclusion, not an ERR that looks like something went wrong.
        if (( ${#ONLY} )) && (( ! ${ONLY[(I)$c]} )); then
            printf '[%ss] skip %s %s (not in onlyVersions=%s)\n' $((SECONDS-t0)) $c $l "$only" >> $PROG
            continue
        fi
        # An absolute path stands on its own. A relative one resolves under the corpus directory,
        # where <corpus-dir>/<version>/ is looked at first: that is how one version gets its own
        # variant of a proof, without copying the whole corpus to vary one file. With no corpus
        # directory it resolves against the version's own examples tree, which is what the shipped
        # corpus of KeY's own examples wants.
        case $p in
            /*) pfile=$p ;;
            *)  if [[ -n $CORPUS_DIR ]]; then
                    if [[ -f $CORPUS_DIR/$c/$p ]]; then
                        pfile=$CORPUS_DIR/$c/$p
                    else
                        pfile=$CORPUS_DIR/$p
                    fi
                else
                    pfile=$CEXAMPLES[$i]/$p
                fi ;;
        esac
        if [[ ! -f $pfile ]]; then
            python3 $CSVIO bench-rows --append $OUT --phase $TAG --config $c --group $g \
                --label $l --wall -1 --error "missing=$pfile" >/dev/null
            printf '[%ss] MISSING %s %s (%s)\n' $((SECONDS-t0)) $c $l "$pfile" >> $PROG
            continue
        fi
        KEYHOME=$OUTDIR/keyhome/${TAG}_${c}; mkdir -p $KEYHOME
        # stdTacletDirectory: read the taclet base from the version's unpacked build resources
        # rather than from inside its jar. 2.12.4 cannot follow the \include chain out of a jar at
        # all, and doing it the same way on every version keeps the taclet-reading part of loadMs
        # comparable across them.
        ISO=(-Dkey.disregardSettings=true -Dkey.home=$KEYHOME
             -Dorg.key_project.stdTacletDirectory=$CRULES[$i]
             -Dkey.bench.javaTimer=$JAVA_TIMER)
        printf '[%ss] %s %s %s ...\n' $((SECONDS-t0)) $TAG $c $l >> $PROG
        typeset -a WRAP; [[ -n $TO ]] && WRAP=($TO $TMO) || WRAP=()
        # Wall time of the whole process: JVM start-up, load, REPS proof runs and teardown. Unlike
        # proveMs and loadMs, which are measured inside the JVM, this is what a user waits for.
        w0=$(python3 -c 'import time;print(int(time.time()*1000))')
        $WRAP $JAVA -Xmx$XMX $LOGARG $PAR $ISO $SET -cp "$LIB/*" \
            org.key_project.BenchMain "$pfile" "$m" $REPS \
            > $OUTDIR/err/${TAG}_${c}_${l}.out 2> $OUTDIR/err/${TAG}_${c}_${l}.err
        ex=$?
        w1=$(python3 -c 'import time;print(int(time.time()*1000))')
        WALL=$((w1 - w0))
        [[ $ex -ne 0 ]] && printf '  EXIT=%s (%s %s)\n' $ex $l $c >> $PROG
        python3 $CSVIO bench-rows $OUTDIR/err/${TAG}_${c}_${l}.out --append $OUT \
            --phase $TAG --config $c --group $g --label $l --wall $WALL \
            --error "$([[ $ex -ne 0 ]] && print -- $ex)" >/dev/null
    done
done < <(python3 $CSVIO rows $CORPUS --min-fields 3 --pad 6)
printf '[%ss] %s DONE\n' $((SECONDS-t0)) $TAG >> $PROG
printf '%s done wall=%ss\n' $TAG $((SECONDS-t0)) > $OUTDIR/$TAG.DONE
print -r -- "done: $OUT"
