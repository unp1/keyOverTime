#!/usr/bin/env python3
"""The one place that knows how this project's CSV files are read and written.

Every table here is CSV: comma separated, quoted where a field needs it, with `#` comment lines and
blank lines allowed so the shipped files can document themselves. Python reads them through
`read_rows`; the shell scripts cannot parse quoted CSV, so they read them through this module's
`rows` command, which re-emits the parsed records tab separated.

Commands:

  csvio.py rows <file> [--min-fields N] [--pad N] [--no-header]
      Parsed records, one per line, tab separated. Comments, blank lines and trailing empty
      columns are gone, so a shell `while IFS=$'\\t' read -r a b c` sees exactly the data.

  csvio.py upsert <file> --key <value> --row <f1> <f2> ...
      Replace the row whose first field is <value>, or append it. Comments are preserved. Used by
      the build step to register a checkout without an awk pipeline that cannot quote.

  csvio.py bench-rows <file> --append <results.csv> --phase P --config C --group G --label L
                             --wall MS [--error EXIT]
      Turn the BENCH lines a measured run printed into result rows. Each tab separated key=value
      token of a BENCH line becomes its own CSV column, which is what makes the results file
      readable by anything without a second parser.
"""
import argparse
import csv
import sys
from pathlib import Path

DELIMITER = ","


def read_rows(path, min_fields=1, pad=0, has_header=True):
    """Data rows of a CSV file: comments, blank lines and the header dropped, fields stripped.

    Every table here carries a header row, so that the files open sensibly in a spreadsheet and say
    what their columns are. `has_header` drops the first record that is not a comment.

    Rows shorter than `pad` are padded with empty strings, so a caller can unpack a fixed number of
    columns whether or not the file bothered to write the trailing empty ones.
    """
    out = []
    skipped_header = not has_header
    with open(path, newline="", encoding="utf-8") as fh:
        for record in csv.reader(fh, delimiter=DELIMITER):
            if not record:
                continue
            first = record[0].strip()
            if not first or first.startswith("#"):
                continue
            if not skipped_header:
                skipped_header = True
                continue
            fields = [f.strip() for f in record]
            if len(fields) < min_fields:
                raise SystemExit(
                    f"{path}: row {fields!r} has {len(fields)} fields, {min_fields} are required")
            if len(fields) < pad:
                fields += [""] * (pad - len(fields))
            out.append(fields)
    return out


def write_rows(path, header, rows, comments=()):
    """Write a CSV file with an optional block of `#` comment lines above the header."""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        for line in comments:
            fh.write(f"# {line}\n" if line else "#\n")
        writer = csv.writer(fh, delimiter=DELIMITER, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def cmd_rows(args):
    for fields in read_rows(args.file, args.min_fields, args.pad, not args.no_header):
        for f in fields:
            if "\t" in f:
                raise SystemExit(f"{args.file}: field {f!r} contains a tab, which the shell readers "
                                 f"use as their separator")
        print("\t".join(fields))


def cmd_upsert(args):
    """Replace the row with this key where it already is, or append it.

    Replacing in place rather than appending keeps the file's order stable, and that order is the
    order the versions are measured in.
    """
    path = Path(args.file)
    records, replaced = [], False
    if path.exists():
        with path.open(newline="", encoding="utf-8") as fh:
            for record in csv.reader(fh, delimiter=DELIMITER):
                if record and record[0].strip() == args.key:
                    records.append(args.row)
                    replaced = True
                else:
                    records.append(record)
    if not replaced:
        records.append(args.row)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, delimiter=DELIMITER, lineterminator="\n")
        for record in records:
            if record:
                writer.writerow(record)
            else:
                fh.write("\n")


def cmd_bench_rows(args):
    """One result row per BENCH line, plus one ERR row when the run itself failed."""
    fixed = [args.phase, args.config, args.group, args.label, args.wall]
    written = 0
    with open(args.append, "a", newline="", encoding="utf-8") as out:
        writer = csv.writer(out, delimiter=DELIMITER, lineterminator="\n")
        if args.error:
            writer.writerow(fixed + ["ERR", f"exit={args.error}"])
        if args.file and Path(args.file).exists():
            with open(args.file, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if line.startswith("BENCH"):
                        writer.writerow(fixed + line.rstrip("\n").split("\t"))
                        written += 1
    print(written)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("rows", help="parsed records, tab separated, for the shell")
    p.add_argument("file")
    p.add_argument("--min-fields", type=int, default=1)
    p.add_argument("--pad", type=int, default=0)
    p.add_argument("--no-header", action="store_true", help="the file has no header row")
    p.set_defaults(func=cmd_rows)

    p = sub.add_parser("upsert", help="replace or append the row with this key")
    p.add_argument("file")
    p.add_argument("--key", required=True)
    p.add_argument("--row", nargs="+", required=True)
    p.set_defaults(func=cmd_upsert)

    p = sub.add_parser("bench-rows", help="BENCH lines of one run to result rows")
    p.add_argument("file", nargs="?")
    p.add_argument("--append", required=True)
    p.add_argument("--phase", required=True)
    p.add_argument("--config", required=True)
    p.add_argument("--group", required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--wall", required=True)
    p.add_argument("--error", default="")
    p.set_defaults(func=cmd_bench_rows)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
