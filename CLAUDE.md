# CsvSeparator

Detects and fixes CSV files where text fields contain unescaped commas, by
converting the whole file to a safer separator (pipe by default).

## Commands

```bash
python -m unittest discover tests    # run tests
python -m csvseparator.main          # interactive mode
```

## Two entry points, one behavior contract

`main.py` branches on whether `--input` was passed: no `--input` → interactive
prompts (`run_interactive`); `--input` given → scriptable mode (`run_noninteractive`).
Keep both paths delegating to the same `CsvSeparatorDetector` methods in
`separator.py` — don't let detection/conversion logic duplicate between them.

## Exit codes are a contract, not incidental

`0` = clean, `2` = structural issues detected (even if a conversion still ran),
`1` = unexpected error. Runbooks branch on these, so don't repurpose them.

## `--yes` gates writes, not analysis

Non-interactive mode always analyzes and reports; it only writes an output file
if `--yes` is passed. Never make analysis-only runs write files, and never make
`--yes` change what's analyzed.

## Encoding and parsing fallbacks are deliberately narrow

Read attempts `utf-8-sig` then `cp1252` only — that's it. CSV parsing tries
`strict=True` first; on a `csv.Error` it falls back to `strict=False`, *except*
when the error is "unexpected end of data" (unterminated quote), which is
treated as fatal rather than silently swallowing the rest of the file into one
cell. Column "normal shape" profiles are built only from rows whose column
count matches the header, so malformed rows can't skew what counts as normal.

## Separator collisions are a warning, not a failure

`count_separator_collisions` finds data values that already contain the target
separator. The output CSV is still valid (values get quoted), so this is
surfaced as a warning/count, never as a reason to block conversion.
