"""
Core logic for detecting and fixing CSV separators.
"""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import List, Tuple

# Encodings tried in order when reading a file. utf-8-sig transparently
# handles both plain UTF-8 and UTF-8-with-BOM (common in Excel exports).
_ENCODING_FALLBACKS = ("utf-8-sig", "cp1252")

# Human-readable labels used when building output file names / messages.
SEPARATOR_LABELS = {"|": "pipe", ";": "semicolon", "\t": "tab", ",": "comma"}


class CsvParsingError(Exception):
    """Raised when a CSV file cannot be parsed safely."""


def _format_row_for_report(row: List[str]) -> str:
    """Render a row with visible markers around the problematic cells."""
    if not row:
        return "<empty>"

    parts = []
    for value in row:
        parts.append(f"[{value}]")
    return " | ".join(parts)


def _looks_numeric(value: str) -> bool:
    """Return True when a value looks like a number."""
    text = value.strip()
    if not text:
        return False
    text = text.replace(",", "").replace(" ", "")
    return bool(re.fullmatch(r"[+-]?\d+(?:\.\d+)?", text))


def _build_column_profile(values: List[str]) -> dict:
    """Summarize the typical shape of a column from the surrounding rows."""
    cleaned = [value.strip() for value in values if value and value.strip()]
    if not cleaned:
        return {
            "dominant_type": "empty",
            "non_empty_ratio": 0.0,
            "comma_ratio": 0.0,
        }

    numeric_count = sum(1 for value in cleaned if _looks_numeric(value))
    text_count = len(cleaned) - numeric_count

    if numeric_count / len(cleaned) >= 0.7:
        dominant_type = "numeric"
    elif text_count / len(cleaned) >= 0.7:
        dominant_type = "text"
    else:
        dominant_type = "mixed"

    comma_count = sum(1 for value in cleaned if "," in value)

    return {
        "dominant_type": dominant_type,
        "non_empty_ratio": len(cleaned) / len(values),
        "comma_ratio": comma_count / len(cleaned),
    }


def separator_label(separator: str) -> str:
    """Return a filesystem/message-safe label for a separator character."""
    if separator in SEPARATOR_LABELS:
        return SEPARATOR_LABELS[separator]
    sanitized = "".join(ch if ch.isalnum() else "_" for ch in separator)
    return sanitized or "custom"


def issues_sidecar_path(output_path: str) -> Path:
    """Path of the issues report written alongside a converted file.

    Deterministic from the output path so callers can predict it (e.g. to
    tell the user where to look) without having to inspect the file system.
    """
    destination = Path(output_path)
    return destination.parent / f"{destination.stem}.issues.json"


class CsvSeparatorDetector:
    """Detect and fix separators in CSV files."""

    def __init__(self, file_path: str):
        """Initialize with CSV file path."""
        self.file_path = Path(file_path)
        if not self.file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

    def _read_csv_rows(self) -> Tuple[List[List[str]], str | None, List[str]]:
        """
        Read and parse CSV rows, surfacing any parse or encoding problems.

        Returns a (rows, parse_error, warnings) tuple. `parse_error` is set
        only when the file could not be read at all. `warnings` reports
        non-fatal degradations (encoding fallback, lenient re-parse).
        """
        warnings: List[str] = []
        content = None
        used_encoding = None
        for encoding in _ENCODING_FALLBACKS:
            try:
                with open(self.file_path, 'r', encoding=encoding, newline='') as handle:
                    content = handle.read()
                used_encoding = encoding
                break
            except UnicodeDecodeError:
                continue

        if content is None:
            tried = ", ".join(_ENCODING_FALLBACKS)
            return [], f"Unable to read file with any of the supported encodings ({tried})", warnings

        if used_encoding != "utf-8-sig":
            warnings.append(f"File is not valid UTF-8; read using '{used_encoding}' fallback encoding")

        try:
            rows = list(csv.reader(io.StringIO(content), strict=True))
        except csv.Error as exc:
            if "unexpected end of data" in str(exc):
                # An unterminated quoted field: lenient parsing would silently
                # swallow the rest of the file into one cell, which is worse
                # than reporting the failure.
                return [], f"CSV parsing error: {exc}", warnings

            try:
                rows = list(csv.reader(io.StringIO(content), strict=False))
            except csv.Error as exc2:
                return [], f"CSV parsing error: {exc2}", warnings

            warnings.append(
                f"Strict CSV parsing failed ({exc}); re-parsed leniently, "
                "so the flagged rows below may not be split exactly as intended"
            )

        return rows, None, warnings

    def get_parse_warnings(self) -> List[str]:
        """Return non-fatal issues encountered while reading the file."""
        _, _, warnings = self._read_csv_rows()
        return warnings

    def analyze_structural_issues(self) -> Tuple[bool, int]:
        """
        Analyze whether the file contains rows that are structurally
        inconsistent with the rest of the file (wrong column count,
        columns that don't match the type of surrounding data, etc.),
        which is typically caused by unescaped separators in the data.

        Returns:
            Tuple of (has_issues, problem_count)
        """
        report = self.get_column_mismatch_report()
        return bool(report), len(report)

    def _build_column_profiles(self, header_cols: int, data_rows: List[List[str]]) -> List[dict]:
        """Build a per-column profile from structurally-clean rows.

        Only rows whose column count already matches the header are used, so
        malformed rows can't skew what "normal" looks like for a column.
        """
        clean_rows = [row for row in data_rows if len(row) == header_cols]
        profile_source = clean_rows if clean_rows else data_rows

        profiles = []
        for col_idx in range(header_cols):
            column_values = [row[col_idx] if col_idx < len(row) else "" for row in profile_source]
            profiles.append(_build_column_profile(column_values))
        return profiles

    @staticmethod
    def _type_mismatch_reasons(row: List[str], header_cols: int, profiles: List[dict]) -> List[str]:
        """Check a row's values against expected per-column profiles.

        Only looks at the overlapping columns (min(header_cols, len(row))) -
        column-count and extra-value reasons are the caller's responsibility.
        """
        reasons = []
        for col_idx in range(min(header_cols, len(row))):
            value = row[col_idx].strip()
            profile = profiles[col_idx]

            if not value and profile["non_empty_ratio"] >= 0.8:
                reasons.append(f"column {col_idx + 1} is empty even though similar rows usually contain data")
                continue

            if profile["dominant_type"] == "numeric" and value and not _looks_numeric(value):
                reasons.append(f"column {col_idx + 1} has text-like value '{value}' but this column is usually numeric")
            elif profile["dominant_type"] == "text" and value and _looks_numeric(value) and len(value) <= 10:
                reasons.append(f"column {col_idx + 1} has numeric-looking value '{value}' but this column usually contains text")

        return reasons

    def get_column_mismatch_report(self, rows: List[List[str]] | None = None) -> List[dict]:
        """Return rows that look structurally inconsistent with the rest of the file.

        By default reads and parses the file from disk. Pass `rows` (e.g.
        after `repair_quoting()`) to re-evaluate an already-parsed, possibly
        repaired, row set instead.
        """
        if rows is None:
            rows, parse_error, _ = self._read_csv_rows()
            if parse_error:
                return [{
                    "row_number": 1,
                    "expected_columns": 0,
                    "actual_columns": 0,
                    "reasons": [parse_error],
                    "highlighted_row": f"❗ Row 1: {parse_error}",
                }]

        lines = rows
        if not lines:
            return []

        header_cols = len(lines[0])
        data_rows = lines[1:]
        profiles = self._build_column_profiles(header_cols, data_rows)

        mismatches = []
        for row_number, row in enumerate(data_rows, start=2):
            actual_columns = len(row)
            reasons = []

            if actual_columns != header_cols:
                reasons.append(f"expected {header_cols} columns but found {actual_columns}")

            reasons.extend(self._type_mismatch_reasons(row, header_cols, profiles))

            if actual_columns > header_cols:
                extra_values = row[header_cols:]
                reasons.append(f"extra values detected: {', '.join(extra_values[:3])}")

            if reasons:
                mismatches.append({
                    "row_number": row_number,
                    "expected_columns": header_cols,
                    "actual_columns": actual_columns,
                    "reasons": reasons,
                    "highlighted_row": f"❗ Row {row_number}: {'; '.join(reasons)} -> {_format_row_for_report(row)}",
                })

        return mismatches

    def _find_quote_repair(self, row: List[str], header_cols: int, profiles: List[dict]) -> Tuple[List[str], int] | None:
        """Try to find the single column whose unescaped comma(s) produced the extra tokens in `row`.

        Returns (repaired_row, quoted_column_index) if exactly one candidate
        split point produces a row with no remaining type/empty mismatches,
        else None (ambiguous or no clean candidate).
        """
        extra = len(row) - header_cols
        if extra <= 0:
            return None

        merge_len = extra + 1
        clean_candidates = []
        for p in range(header_cols):
            left = row[:p]
            merged = ",".join(row[p:p + merge_len])
            right = row[p + merge_len:]
            candidate = left + [merged] + right

            # A merge that reintroduces a comma into a column that never
            # legitimately contains one (per the clean-row profile) is very
            # unlikely to be the real split point, even if it happens to
            # still "look like text" - e.g. a merged "Bob Smith,456 Oak Ave"
            # landing in a name column. Columns that do legitimately contain
            # commas (addresses, notes) allow it.
            if "," in merged and profiles[p]["comma_ratio"] == 0:
                continue

            if not self._type_mismatch_reasons(candidate, header_cols, profiles):
                clean_candidates.append((candidate, p))

        if len(clean_candidates) == 1:
            return clean_candidates[0]
        return None

    def repair_quoting(self) -> Tuple[List[List[str]], List[dict]]:
        """Attempt to fix rows with extra columns by quoting the field with the unescaped comma.

        Returns (rows, summary): `rows` is the full row list (header + data),
        repaired in place where possible. `summary` has one entry per row
        that had extra columns: {"row_number", "status": "repaired" or
        "unresolved", "quoted_column"} (quoted_column is the header name,
        None when unresolved).
        """
        rows, parse_error, _ = self._read_csv_rows()
        if parse_error or not rows:
            return rows, []

        header = rows[0]
        header_cols = len(header)
        data_rows = rows[1:]
        profiles = self._build_column_profiles(header_cols, data_rows)

        repaired_rows = [header]
        summary = []
        for row_number, row in enumerate(data_rows, start=2):
            if len(row) > header_cols:
                fixed = self._find_quote_repair(row, header_cols, profiles)
                if fixed is not None:
                    candidate, quoted_index = fixed
                    repaired_rows.append(candidate)
                    summary.append({
                        "row_number": row_number,
                        "status": "repaired",
                        "quoted_column": header[quoted_index],
                    })
                    continue
                summary.append({
                    "row_number": row_number,
                    "status": "unresolved",
                    "quoted_column": None,
                })
            repaired_rows.append(row)

        return repaired_rows, summary

    def count_separator_collisions(self, separator: str) -> int:
        """Count data cells that already contain the given separator character."""
        rows, parse_error, _ = self._read_csv_rows()
        if parse_error:
            return 0
        return sum(1 for row in rows for value in row if separator in value)

    def _default_output_name(self, separator: str) -> str:
        return f"{self.file_path.stem}_converted_{separator_label(separator)}.csv"

    def convert_separator(
        self,
        new_separator: str,
        output_path: str | None = None,
        output_dir: str | None = None,
        quote_fix: bool = False,
    ) -> str:
        """
        Convert CSV file to use a new separator.

        Args:
            new_separator: The new separator character
            output_path: Optional exact output file path (takes precedence)
            output_dir: Optional directory to place the default-named output file in
            quote_fix: If True, attempt to repair rows with extra columns by
                quoting the field with the unescaped comma before writing

        Returns:
            Path to the output file
        """
        if output_path is not None:
            destination = Path(output_path)
        else:
            directory = Path(output_dir) if output_dir is not None else self.file_path.parent
            destination = directory / self._default_output_name(new_separator)
        destination.parent.mkdir(parents=True, exist_ok=True)

        try:
            rows, parse_error, _ = self._read_csv_rows()
            if parse_error:
                raise CsvParsingError(parse_error)

            if quote_fix:
                rows, _ = self.repair_quoting()

            mismatches = self.get_column_mismatch_report(rows=rows)
            rows_to_write = rows

            if mismatches and rows:
                # Append a trailing column so flagged rows are visible
                # directly in the output file, not just in console/JSON
                # reports that are easy to miss when --yes is used.
                reasons_by_row = {entry["row_number"]: "; ".join(entry["reasons"]) for entry in mismatches}
                rows_to_write = [rows[0] + ["row_issues"]]
                for row_number, row in enumerate(rows[1:], start=2):
                    rows_to_write.append(row + [reasons_by_row.get(row_number, "")])

            with open(destination, 'w', encoding='utf-8', newline='') as f:
                writer = csv.writer(f, delimiter=new_separator)
                writer.writerows(rows_to_write)

            if mismatches:
                report_path = issues_sidecar_path(str(destination))
                report_path.write_text(json.dumps(mismatches, indent=2), encoding='utf-8')

            return str(destination)
        except Exception as e:
            raise Exception(f"Error converting file: {str(e)}")

    def get_file_info(self) -> dict:
        """Get information about the CSV file."""
        try:
            rows, parse_error, _ = self._read_csv_rows()
            if parse_error:
                return {'error': parse_error}

            return {
                'total_rows': len(rows),
                'header_row': rows[0] if rows else [],
                'column_count': len(rows[0]) if rows else 0,
                'file_size': self.file_path.stat().st_size,
            }
        except Exception as e:
            return {'error': str(e)}
