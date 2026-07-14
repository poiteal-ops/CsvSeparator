# CsvSeparator

A Python utility to detect and fix separators in CSV files that contain text fields with embedded commas.

## Problem

CSV files traditionally use commas as separators. However, when data contains text fields with commas (e.g., addresses, descriptions), the file parsing breaks because it's ambiguous whether a comma is a field separator or part of the data.

## Solution

CsvSeparator detects this issue and converts the CSV file to use an alternative separator (pipe character `|` by default) for the entire file, ensuring proper parsing of fields containing commas. It can also repair broken rows in place by quoting the offending field (`--quote-fix`) instead of, or in addition to, changing the separator.

It can be used two ways: interactively at a terminal, or non-interactively from a script/runbook.

## Prerequisites

**The first row of every input file must be a header row (field names).** This is mandatory, not optional — every detection and repair heuristic (column counts, per-column type profiles, quote repair) is built relative to the header, and a headerless file will be misread as data.

## Interactive usage (terminal)

```bash
python -m csvseparator.main
```

The tool will:
1. Prompt you for the path to the CSV file (relative to the `in/` folder)
2. Analyze the file to detect if commas in text fields are problematic
3. Ask if you want to proceed with conversion
4. Suggest `|` as the default separator
5. Allow you to choose a different separator if desired
6. Create a new CSV file with the updated separator in the `out/` folder

## Non-interactive usage (scripts / runbooks)

Passing `--input` switches to a fully flag-driven mode with no prompts, so it can run unattended in a runbook, CI job, or scheduled task.

```bash
# Report only — never writes a file. Exits 2 if issues were found, 0 if clean.
python -m csvseparator.main --input path/to/file.csv --analyze-only

# Same, but machine-readable output for a runbook step that parses the result.
python -m csvseparator.main --input path/to/file.csv --analyze-only --format json

# Actually write the converted file. --yes is required to write anything.
python -m csvseparator.main --input path/to/file.csv --separator pipe --yes --output-dir out/

# Repair rows broken by unescaped commas by quoting the offending field,
# keeping the comma separator (see "Quote-fix repair" below).
python -m csvseparator.main --input path/to/file.csv --separator comma --quote-fix --yes --output-dir out/
```

### Flags

| Flag | Description |
| --- | --- |
| `--input`, `-i` | Path to the CSV file. Required to enable non-interactive mode. If not found as given, it is also looked up inside `./in`. |
| `--separator`, `-s` | `pipe` (default), `semicolon`, `tab`, `comma`, or `custom`. |
| `--custom-separator` | The single character to use when `--separator custom` is given. |
| `--output`, `-o` | Exact output file path. Overrides `--output-dir`. |
| `--output-dir` | Directory for the converted file (default: `./out`). |
| `--analyze-only` | Only report structural issues; never writes a file. |
| `--quote-fix` | Attempt to repair rows with extra columns by quoting the field that contains the unescaped comma, before any separator conversion. See "Quote-fix repair" below. |
| `--yes`, `-y` | Required to actually write the converted file. Without it, the run only analyzes and reports what it would do. |
| `--format` | `text` (default) or `json`. Use `json` for runbooks/automation that parse the result. |

## Quote-fix repair

By default, a row with extra columns (caused by an unescaped comma in a text field) is only *flagged* — the raw, mis-split values are carried through to the output as-is, annotated with a `row_issues` column. Passing `--quote-fix` instead attempts to actually repair the row: it figures out which field absorbed the stray comma(s) and re-quotes it, merging the split values back together.

```
# before (id,customer_name,shipping_address,total,notes)
2,Bob Smith,456 Oak Ave, Suite 200, Metropolis,54.50,Prefers evening drop-off

# after --quote-fix (with --separator comma)
2,Bob Smith,"456 Oak Ave, Suite 200, Metropolis",54.50,Prefers evening drop-off
```

This works by comparing candidate repairs against the typical shape of each column (does it normally hold numbers or text, does it normally contain commas at all). It only repairs a row when exactly one field can be identified with confidence. If a row's extra columns can't be attributed to a single field unambiguously (e.g. two different fields each contain unescaped commas), it's left exactly as detection reports it today — flagged, unrepaired. Quote-fix also can't recover a row that's *missing* a field (too few columns); that's data loss, not a quoting problem.

`--quote-fix` works with any `--separator` — combine it with `--separator comma` to fix the file in place without changing separators, as in the example above, or with `--separator pipe`/`semicolon`/`tab` to both repair rows and switch delimiters in one pass.

### Exit codes

Runbooks should branch on the exit code rather than scraping text output:

| Code | Meaning |
| --- | --- |
| `0` | Success. No structural issues were detected. |
| `2` | Structural issues were detected in the source file (this is reported whether or not a conversion also ran — check the report for details). |
| `1` | Unexpected error (file not found, unreadable, invalid arguments, etc.). |

### JSON report shape

```json
{
  "file": "in/example.csv",
  "info": {"total_rows": 10, "header_row": ["id", "name"], "column_count": 2, "file_size": 512},
  "warnings": ["File is not valid UTF-8; read using 'cp1252' fallback encoding"],
  "issues_found": true,
  "issue_count": 1,
  "report": [
    {
      "row_number": 4,
      "expected_columns": 2,
      "actual_columns": 3,
      "reasons": ["expected 2 columns but found 3"],
      "highlighted_row": "❗ Row 4: expected 2 columns but found 3 -> [1] | [Smith] | [Jr]"
    }
  ],
  "converted": true,
  "output_path": "out/example_converted_pipe.csv",
  "separator_collisions": 0
}
```

`warnings` covers non-fatal degradations: encoding fallback (BOM / non-UTF-8 files) and lenient re-parsing after a localized quoting error. `separator_collisions` is the number of existing data values that already contain the chosen separator — the output is still valid quoted CSV, but a downstream tool that doesn't respect CSV quoting could hit the same ambiguity again.

When `--quote-fix` is passed, the payload also includes a `quote_repairs` list, one entry per row that had extra columns: `{"row_number": 4, "status": "repaired", "quoted_column": "shipping_address"}` (or `"status": "unresolved", "quoted_column": null` when the row couldn't be confidently repaired). `report`/`issues_found` reflect the state *after* the repair attempt — successfully repaired rows drop out of the issue report.

## Project Structure

- `csvseparator/` - Main package
  - `separator.py` - Core CSV separator detection and conversion logic
  - `main.py` - Command-line interface (interactive and non-interactive modes)
  - `__init__.py` - Package initialization
- `tests/` - Unit tests covering both the detection logic and the non-interactive CLI
- `notebooks/` - Optional Jupyter notebook for interactively exploring `CsvSeparatorDetector` (see below)

## Notebook (exploration)

`notebooks/explore_csvseparator.ipynb` walks through `CsvSeparatorDetector` step by step against a sample file with a deliberately unescaped comma (`notebooks/data/messy_sample.csv`): file info, parse warnings, structural issue detection, quote-fix repair, and conversion — each in its own cell so you can inspect the intermediate results.

This is a dev/exploration aid, **not** a third entry point — the CLI (`run_interactive`/`run_noninteractive` in `main.py`) remains the only supported way to run the tool end-to-end.

```bash
pip install -e ".[notebook]"
jupyter notebook notebooks/explore_csvseparator.ipynb
```
