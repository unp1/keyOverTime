#!/usr/bin/env python3
"""Derive the load-only corpus from the real one.

  corpus_load.py <corpus.csv> <out.csv>

Same proofs and same settings, but a one-rule budget and no version pins. Deriving it beats keeping
a second corpus file, which would drift from the first. The pins are dropped because they exist
where a proof cannot close on an older prover, which is a fact about proof search; loading works
everywhere, so a loading comparison wants every version measured on every proof.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from csvio import read_rows, write_rows  # noqa: E402

HEADER = ["group", "label", "path", "maxSteps", "fileSettings", "onlyVersions"]


def main(src, dst):
    rows = [[group, label, path, "1", file_settings, ""]
            for group, label, path, _steps, file_settings, *_
            in read_rows(src, min_fields=5, pad=6)]
    write_rows(dst, HEADER, rows,
               comments=[f"Derived from {src} for a load-only run: one rule, no version pins.",
                         "Generated; edit the corpus it came from instead."])


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    main(sys.argv[1], sys.argv[2])
