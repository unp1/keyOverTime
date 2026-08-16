/* This file is part of KeY - https://key-project.org
 * KeY is licensed under the GNU General Public License Version 2
 * SPDX-License-Identifier: GPL-2.0-only */
package org.key_project;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

import de.uka.ilkd.key.control.KeYEnvironment;
import de.uka.ilkd.key.rule.FindTaclet;
import de.uka.ilkd.key.rule.MatchConditions;

import org.key_project.logic.Term;
import org.key_project.prover.rules.instantiation.MatchResultInfo;

/**
 * Find-matcher throughput of whatever matcher the checkout actually ships.
 *
 * <p>
 * The point of measuring it this way is that it runs on every version. KeY's own
 * {@code CompiledMatchProgramBenchmark} compares the two back-ends of the match-plan framework, so
 * it can only exist where that framework does: it cannot be built on 2.12 at all, and using its
 * interpreter column as a stand-in for 2.12 measures a later version's interpreter, not 2.12's
 * matcher. Here nothing is constructed by hand. Each version's own
 * {@code Taclet.getMatcher().matchFind(...)} is called, so 2.12 is timed on its instruction
 * interpreter and 3.0 and main on their compiled matchers, which is the comparison the slide wants.
 *
 * <p>
 * Restricted to API that is identical from the 2.12.4 release candidate to current main, the same
 * discipline {@code BenchMain} follows, so one source file compiles unchanged on all of them.
 *
 * <pre>
 *   MatchBench &lt;problem.key&gt;... [-Dmatch.bench.passes=30]
 * </pre>
 *
 * Every find-taclet in the activated taclet base is matched against every subterm of the problem's
 * initial sequent. The great majority of those attempts fail, which is what proof search does too:
 * the matcher's job is mostly to reject quickly.
 */
public final class MatchBench {

    private static final MatchResultInfo EMPTY = MatchConditions.EMPTY_MATCHCONDITIONS;

    private MatchBench() {}

    public static void main(String[] args) throws Exception {
        final int passes = Integer.getInteger("match.bench.passes", 30);
        final int warmup = Integer.getInteger("match.bench.warmup", 5);

        long totalAttempts = 0, totalNanos = 0, totalMatches = 0;

        for (String arg : args) {
            final Path file = Path.of(arg.trim());
            if (!Files.exists(file)) {
                System.err.println("MATCHBENCH skip (not found) " + file);
                continue;
            }
            final var env = KeYEnvironment.load(file);
            try {
                final var proof = env.getLoadedProof();
                final var services = proof.getServices();

                final List<Term> corpus = new ArrayList<>();
                for (var sf : proof.root().sequent()) {
                    collect(sf.formula(), corpus);
                }

                // the matchers, resolved once: building them is compilation on 3.0 and main, and
                // that cost belongs to loading a taclet base, not to matching a term
                final var matchers = new ArrayList<Object>();
                for (var t : proof.getInitConfig().activatedTaclets()) {
                    if (t instanceof FindTaclet && t.getMatcher() != null) {
                        matchers.add(t.getMatcher());
                    }
                }

                for (int p = 0; p < warmup; p++) {
                    sweep(matchers, corpus, services);
                }
                final long t0 = System.nanoTime();
                long matches = 0;
                for (int p = 0; p < passes; p++) {
                    matches += sweep(matchers, corpus, services);
                }
                final long ns = System.nanoTime() - t0;
                final long attempts = (long) matchers.size() * corpus.size() * passes;

                System.out.printf(
                    "MATCHBENCH file=%s findTaclets=%d corpus=%d attempts=%d ns=%d "
                        + "nsPerAttempt=%.2f attemptsPerSec=%.0f matches=%d%n",
                    file.getFileName(), matchers.size(), corpus.size(), attempts, ns,
                    (double) ns / attempts, attempts / (ns / 1e9), matches / passes);

                totalAttempts += attempts;
                totalNanos += ns;
                totalMatches += matches / passes;
            } finally {
                env.dispose();
            }
        }

        if (totalAttempts > 0) {
            System.out.printf(
                "MATCHBENCH TOTAL attempts=%d ns=%d nsPerAttempt=%.2f attemptsPerSec=%.0f "
                    + "matches=%d%n",
                totalAttempts, totalNanos, (double) totalNanos / totalAttempts,
                totalAttempts / (totalNanos / 1e9), totalMatches);
        }
    }

    /** One full pass: every matcher against every corpus term. Returns the successful matches. */
    private static long sweep(List<Object> matchers, List<Term> corpus, Object services) {
        long matches = 0;
        for (int m = 0, nm = matchers.size(); m < nm; m++) {
            final var matcher = (org.key_project.prover.rules.TacletMatcher) matchers.get(m);
            for (int i = 0, n = corpus.size(); i < n; i++) {
                if (matcher.matchFind(corpus.get(i), EMPTY,
                    (org.key_project.logic.LogicServices) services) != null) {
                    matches++;
                }
            }
        }
        return matches;
    }

    private static void collect(Term t, List<Term> out) {
        out.add(t);
        for (int i = 0, n = t.arity(); i < n; i++) {
            collect(t.sub(i), out);
        }
    }
}
