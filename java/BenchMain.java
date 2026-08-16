/* This file is part of KeY - https://key-project.org
 * KeY is licensed under the GNU General Public License Version 2
 * SPDX-License-Identifier: GPL-2.0-only */
package org.key_project;

import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;

import java.util.concurrent.atomic.AtomicLong;

import de.uka.ilkd.key.control.KeYEnvironment;
import de.uka.ilkd.key.proof.Proof;
import de.uka.ilkd.key.settings.ProofSettings;
import de.uka.ilkd.key.speclang.Contract;
import de.uka.ilkd.key.util.KeYTypeUtil;

/**
 * Minimal headless benchmark harness. Loads a .key problem, caps the number of automatic rule
 * applications, runs the automatic proof search once and prints a single machine-readable line with
 * the node count, closure state and timings.
 *
 * Restricted to APIs that are stable from the KeY 2.12.4 release candidate to current main, so the
 * very same source compiles unchanged on every compared version and all of them are measured by
 * identical harness logic. Two API details drove that restriction:
 * <ul>
 * <li>{@code KeYJavaType} moved from {@code de.uka.ilkd.key.java.abstraction} to
 * {@code de.uka.ilkd.key.java.ast.abstraction} when Recoder was removed, so the contract fallback
 * below binds those values with {@code var} rather than naming the type.</li>
 * <li>ncore {@code org.key_project.logic} types, proof-script replay and tree-walking helpers are
 * avoided entirely; they exist only on the newer versions.</li>
 * </ul>
 *
 * Every measurement here uses only stock KeY API except one: the Java-model phase, which needs the
 * counter {@code ProblemInitializer.JAVA_READ_NANOS} that {@code patch_readjava.py} adds to a
 * checkout. That counter is read by reflection rather than by a compile-time reference, so this
 * class also compiles and runs against an unpatched checkout. {@code -Dkey.bench.javaTimer} selects
 * what happens then: {@code auto} (the default) reports {@code javaMs=-1} and flags the run,
 * {@code on} fails immediately rather than let a botched patch pass as a measurement, and
 * {@code off} skips the metric deliberately. See {@link #resolveJavaTimer()}.
 *
 * Usage: BenchMain &lt;file.key&gt; [maxSteps] [reps]
 *
 * Each rep reloads the problem from scratch and runs automode once in the same JVM, so the first
 * rep warms up the JIT and the later reps are the warm measurement. The node count is expected to
 * be identical across reps; a divergence is reported as a determinism warning.
 */
public final class BenchMain {
    /** {@code on} if the counter was found and is being read, {@code off} or {@code absent}. */
    private static String javaTimerState;

    /** The patched-in counter, or {@code null} when the metric is unavailable or switched off. */
    private static final AtomicLong JAVA_TIMER = resolveJavaTimer();

    private BenchMain() {
    }

    /**
     * Binds the Java-model counter that {@code patch_readjava.py} adds to a checkout.
     *
     * Reflection rather than a direct reference, so the harness stays usable on a checkout nobody
     * wants to modify: an unpatched build still delivers every other metric. The three modes exist
     * because "the counter is missing" means different things in different situations. Under
     * {@code on} a missing counter is a failed patch and the run dies rather than quietly reporting
     * a metric it never measured; under {@code off} the metric was not wanted; {@code auto} is the
     * middle case, where the run continues and marks itself so the gap is visible in the output.
     *
     * @return the counter, or {@code null} if it is unavailable or switched off
     */
    private static AtomicLong resolveJavaTimer() {
        final String mode = System.getProperty("key.bench.javaTimer", "auto");
        if ("off".equals(mode)) {
            javaTimerState = "off";
            return null;
        }
        try {
            final var field = Class.forName("de.uka.ilkd.key.proof.init.ProblemInitializer")
                    .getField("JAVA_READ_NANOS");
            javaTimerState = "on";
            return (AtomicLong) field.get(null);
        } catch (ReflectiveOperationException | ClassCastException e) {
            if ("on".equals(mode)) {
                throw new IllegalStateException(
                    "key.bench.javaTimer=on but ProblemInitializer.JAVA_READ_NANOS is not there:"
                        + " this checkout was not patched by patch_readjava.py",
                    e);
            }
            javaTimerState = "absent";
            return null;
        }
    }

    public static void main(String[] args) throws Exception {
        final Path keyFile = Paths.get(args[0]);
        final int maxSteps = args.length > 1 ? Integer.parseInt(args[1]) : 100000;
        final int reps = args.length > 2 ? Integer.parseInt(args[2]) : 1;

        // Everything the JVM did before reaching this point: process start, VM boot and the class
        // loading of the harness. It is the part of the end-to-end wait that neither loadMs nor
        // proveMs sees, and the driver cannot separate it out because one process serves all reps.
        // With it, a cold end-to-end wall is jvmMs + loadMs(rep 1) + proveMs(rep 1), and a warm one
        // is loadMs + proveMs of a later rep.
        final long jvmMs =
            java.lang.management.ManagementFactory.getRuntimeMXBean().getUptime();

        int firstNodes = -1;
        for (int rep = 1; rep <= reps; rep++) {
            // Establish the strategy settings deterministically before loading, so the run does not
            // depend on the persisted ~/.key defaults (the driver disregards those). Three modes:
            //   key.bench.fileSettings : take the problem file's own \settings (the case studies).
            //   key.bench.settingsFile : load one supplied baseline settings file for every config.
            //   neither                : the version's own shipped defaults, which is what the
            //                            over-time comparison uses for the example proofs.
            if (System.getProperty("key.bench.fileSettings") != null) {
                final var fromFile =
                    de.uka.ilkd.key.nparser.ParsingFacade.parseFile(keyFile).findProofSettings();
                if (fromFile != null) {
                    ProofSettings.DEFAULT_SETTINGS.loadSettingsFromJSONStream(
                        new java.io.StringReader(fromFile.settingsToString()));
                }
            } else {
                final String settingsFile = System.getProperty("key.bench.settingsFile");
                if (settingsFile != null) {
                    try (var r = java.nio.file.Files
                            .newBufferedReader(java.nio.file.Paths.get(settingsFile))) {
                        ProofSettings.DEFAULT_SETTINGS.loadSettingsFromJSONStream(r);
                    }
                }
            }

            // Java-model phase of this rep only: the counter accumulates across every readJava call
            // the load performs, so it is zeroed immediately before the load rather than read as a
            // difference.
            if (JAVA_TIMER != null) {
                JAVA_TIMER.set(0);
            }
            final long tLoad0 = System.nanoTime();
            final KeYEnvironment<?> env = KeYEnvironment.load(keyFile, null, null, null);
            final long tLoad1 = System.nanoTime();
            final long javaNanos = JAVA_TIMER != null ? JAVA_TIMER.get() : -1_000_000L;

            Proof proof = env.getLoadedProof();
            if (proof == null) {
                // a \chooseContract project without a contract name: take its single contract
                final List<Contract> contracts = new ArrayList<>();
                for (final var type : env.getJavaInfo().getAllKeYJavaTypes()) {
                    if (KeYTypeUtil.isLibraryClass(type)) {
                        continue;
                    }
                    for (final var target : env.getSpecificationRepository()
                            .getContractTargets(type)) {
                        for (final Contract c : env.getSpecificationRepository().getContracts(type,
                            target)) {
                            contracts.add(c);
                        }
                    }
                }
                if (contracts.size() != 1) {
                    System.out.println("BENCHERR\tfile=" + keyFile.getFileName() + "\tcontracts="
                        + contracts.size());
                    System.exit(2);
                }
                proof = env.createProof(
                    contracts.get(0).createProofObl(env.getInitConfig(), contracts.get(0)));
            }

            // maxSteps <= 0 keeps the step budget the problem file configured (used for the case
            // studies that are measured exactly as set up); a positive value overrides it.
            if (maxSteps > 0) {
                ProofSettings.DEFAULT_SETTINGS.getStrategySettings().setMaxSteps(maxSteps);
                proof.getSettings().getStrategySettings().setMaxSteps(maxSteps);
            }
            final int effMaxSteps = proof.getSettings().getStrategySettings().getMaxSteps();

            // Explicit strategy-property overrides (-Dkey.bench.prop.<KEY>=<VALUE>), applied to
            // the loaded proof itself. The global settings seeding above can be lost silently;
            // these cannot, and the SETTINGS line below shows the values actually active, so a
            // driver can verify them per run.
            {
                final var sprops =
                    proof.getSettings().getStrategySettings().getActiveStrategyProperties();
                boolean touched = false;
                for (final var e : System.getProperties().entrySet()) {
                    final String k = String.valueOf(e.getKey());
                    if (k.startsWith("key.bench.prop.")) {
                        sprops.setProperty(k.substring("key.bench.prop.".length()),
                            String.valueOf(e.getValue()));
                        touched = true;
                    }
                }
                if (touched) {
                    proof.getSettings().getStrategySettings()
                            .setActiveStrategyProperties(sprops);
                    // The active strategy snapshots its options at construction, which happened
                    // during load. Rebuild it so the overrides actually steer the run.
                    final var profile = proof.getServices().getProfile();
                    final var stratName =
                        proof.getSettings().getStrategySettings().getStrategy();
                    final var factory = profile.supportsStrategyFactory(stratName)
                            ? profile.getStrategyFactory(stratName)
                            : profile.getDefaultStrategyFactory();
                    proof.setActiveStrategy(factory.create(proof, sprops));
                }
            }

            // Dump the effective settings this proof is about to run with. Each version runs on its
            // own shipped defaults here, so this line is what makes a node-count jump attributable:
            // identical settings across two versions means the prover changed, differing settings
            // means the shipped configuration did.
            System.out.println("SETTINGS"
                + "\tfile=" + keyFile.getFileName()
                + "\trep=" + rep
                + "\tparallel=" + System.getProperty("key.prover.parallel")
                + "\tthreads=" + System.getProperty("key.prover.parallel.threads")
                + "\tstrategy=" + proof.getSettings().getStrategySettings().getStrategy()
                + "\tmaxSteps=" + effMaxSteps
                + "\tprops=" + new java.util.TreeMap<Object, Object>(
                    proof.getSettings().getStrategySettings().getActiveStrategyProperties())
                + "\tchoices=" + new java.util.TreeMap<>(
                    proof.getSettings().getChoiceSettings().getDefaultChoices()));

            // Reset the heap high-water mark right before proving, so peakHeapMB reflects the peak
            // reached while this proof runs (load and JIT warm-up from earlier reps excluded).
            resetHeapPeak();
            final long gc0 = gcMillis();
            final long tRun0 = System.nanoTime();
            env.getUi().getProofControl().startAndWaitForAutoMode(proof);
            final long tRun1 = System.nanoTime();
            final long gcRun = gcMillis() - gc0;
            final long peakHeapBytes = heapPeak();

            final int nodes = proof.getStatistics().nodes;
            if (firstNodes < 0) {
                firstNodes = nodes;
            }
            // Live set retained by the finished proof (proof tree + interned terms + the OSS
            // caches), measured after a collection so it is independent of the -Xmx the run was
            // given, unlike peakHeapMB. The proof is still referenced here, so its caches are still
            // populated.
            System.gc();
            final long liveHeapBytes = heapUsed();
            System.out.println("BENCH"
                + "\tfile=" + keyFile.getFileName()
                + "\trep=" + rep
                + "\tmaxSteps=" + effMaxSteps
                + "\tnodes=" + nodes
                + "\tbranches=" + proof.getStatistics().branches
                + "\topenGoals=" + proof.openGoals().size()
                + "\tclosed=" + proof.closed()
                + "\tjvmMs=" + jvmMs
                + "\tloadMs=" + (tLoad1 - tLoad0) / 1_000_000
                + "\tjavaMs=" + javaNanos / 1_000_000
                + "\tjavaTimer=" + javaTimerState
                + "\tproveMs=" + (tRun1 - tRun0) / 1_000_000
                + "\tgcMs=" + gcRun
                + "\tpeakHeapMB=" + (peakHeapBytes >> 20)
                + "\tliveHeapMB=" + (liveHeapBytes >> 20)
                + (nodes == firstNodes ? "" : "\tNONDET_vs_rep1=" + firstNodes));

            proof.dispose();
            env.dispose();
        }
        System.exit(0);
    }

    private static long gcMillis() {
        long ms = 0;
        for (final var gc : java.lang.management.ManagementFactory.getGarbageCollectorMXBeans()) {
            ms += gc.getCollectionTime();
        }
        return ms;
    }

    /** Bytes currently used across all heap memory pools. */
    private static long heapUsed() {
        long b = 0;
        for (final var p : java.lang.management.ManagementFactory.getMemoryPoolMXBeans()) {
            if (p.getType() == java.lang.management.MemoryType.HEAP) {
                b += p.getUsage().getUsed();
            }
        }
        return b;
    }

    /** Peak bytes used across all heap memory pools since the last {@link #resetHeapPeak()}. */
    private static long heapPeak() {
        long b = 0;
        for (final var p : java.lang.management.ManagementFactory.getMemoryPoolMXBeans()) {
            if (p.getType() == java.lang.management.MemoryType.HEAP) {
                b += p.getPeakUsage().getUsed();
            }
        }
        return b;
    }

    /** Resets the per-pool heap high-water mark to the current usage. */
    private static void resetHeapPeak() {
        for (final var p : java.lang.management.ManagementFactory.getMemoryPoolMXBeans()) {
            if (p.getType() == java.lang.management.MemoryType.HEAP) {
                p.resetPeakUsage();
            }
        }
    }
}
