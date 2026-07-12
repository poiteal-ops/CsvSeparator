# CsvSeparator

A Python utility to detect and fix separators in CSV files that contain text fields with embedded commas.

## Problem

CSV files traditionally use commas as separators. However, when data contains text fields with commas (e.g., addresses, descriptions), the file parsing breaks because it's ambiguous whether a comma is a field separator or part of the data.

## Solution

CsvSeparator detects this issue and converts the CSV file to use an alternative separator (pipe character `|` by default) for the entire file, ensuring proper parsing of fields containing commas.

It can be used two ways: interactively at a terminal, or non-interactively from a script/runbook.

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
| `--yes`, `-y` | Required to actually write the converted file. Without it, the run only analyzes and reports what it would do. |
| `--format` | `text` (default) or `json`. Use `json` for runbooks/automation that parse the result. |

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

## Project Structure

- `csvseparator/` - Main package
  - `separator.py` - Core CSV separator detection and conversion logic
  - `main.py` - Command-line interface (interactive and non-interactive modes)
  - `__init__.py` - Package initialization
- `tests/` - Unit tests covering both the detection logic and the non-interactive CLI
