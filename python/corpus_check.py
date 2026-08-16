#!/usr/bin/env python3
"""Check that every proof in the corpus exists, for every version that is measured on it.

  corpus_check.py <corpus.csv> <corpus-dir> <version name>...

Run before anything is built. A mistyped path would otherwise surface only when that proof's turn
came, after the builds, as one MISSING row per version among the results.

<corpus-dir> may be empty, which is the case where relative paths resolve against each version's own
examples tree. Those cannot be checked before the checkouts exist, so they are left alone and only
the absolute paths are checked. Absolute paths need nothing to exist, and a mistyped one is the
mistake this catches cheapest.

The lookup is the one the measurement uses: an absolute path is taken as it stands, and a relative
one is looked for first under <corpus-dir>/<version>/, then under <corpus-dir>/. That first location
is how one version gets its own variant of a file, for instance where a syntax change means the
older prover cannot parse what the newer one wants.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from csvio import read_rows  # noqa: E402


def resolve(corpus_dir, version, path):
    """Where this proof is for this version, and whether it is the version's own variant.

    Returns None for a path that cannot be located yet, meaning a relative one with no corpus
    directory: it lives in a checkout that does not exist at this point in the run.
    """
    if Path(path).is_absolute():
        return Path(path), False
    if not corpus_dir:
        return None, False
    overlay = Path(corpus_dir) / version / path
    if overlay.is_file():
        return overlay, True
    return Path(corpus_dir) / path, False


def main(corpus, corpus_dir, versions):
    # missing is keyed by (label, where it was looked for), because a corpus of n proofs missing for
    # all m versions is one mistake, not n*m of them, and listing it m times buries it
    missing, overlaid = {}, []
    for _group, label, path, _steps, _fs, only in read_rows(corpus, min_fields=3, pad=6):
        applies = only.replace(",", " ").split() or versions
        for version in versions:
            if version not in applies:
                continue
            found, is_overlay = resolve(corpus_dir, version, path)
            if found is None:
                continue
            if not found.is_file():
                missing.setdefault((label, str(found)), []).append(version)
            elif is_overlay:
                overlaid.append(f"{version}: {label}")

    for line in overlaid:
        print(f"  corpus: own variant for {line}")
    sys.stdout.flush()

    if missing:
        print("corpus files not found:", file=sys.stderr)
        for (label, found), affected in missing.items():
            who = "every version" if len(affected) == len(versions) else ", ".join(affected)
            print(f"         {label}: {found}  ({who})", file=sys.stderr)
        print("", file=sys.stderr)
        if corpus_dir:
            print(f"       Relative paths are looked for under {corpus_dir}/<version>/ first, then",
                  file=sys.stderr)
            print(f"       under {corpus_dir}/ itself. Correct the corpus row, or place the file.",
                  file=sys.stderr)
        else:
            print("       Correct the corpus row, or place the file.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 4:
        raise SystemExit(__doc__)
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3:]))
