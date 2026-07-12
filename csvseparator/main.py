"""
Command-line interface for CsvSeparator.

Supports two modes:
  * Interactive (default, no arguments): prompts for a file and separator.
  * Non-interactive (--input given): suitable for scripts and runbooks.
    See README.md for the full flag reference and exit code meanings.
"""

import argparse
import json
import sys
from pathlib import Path

from csvseparator.separator import CsvSeparatorDetector, issues_sidecar_path, separator_label

SEPARATOR_CHOICES = {"pipe": "|", "semicolon": ";", "tab": "\t", "comma": ","}

EXIT_OK = 0
EXIT_ISSUES_FOUND = 2
EXIT_ERROR = 1


def get_project_directories(project_root: Path | None = None) -> tuple[Path, Path]:
    """Ensure the project has in/ and out/ folders and return their paths."""
    root = Path(project_root or Path.cwd())
    input_dir = root / "in"
    output_dir = root / "out"

    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    return input_dir, output_dir


def prompt_for_file_path(input_dir: Path) -> str:
    """Prompt user for a CSV file name inside the in folder."""
    while True:
        file_name = input("\nEnter the CSV file name in the 'in' folder: ").strip()
        if not file_name:
            print("Please provide a valid file name.")
            continue

        file_path = input_dir / file_name
        if not file_path.exists():
            print(f"File not found: {file_path}")
            continue
        return str(file_path)


def prompt_for_separator() -> str:
    """Prompt user for separator character."""
    print("\nAvailable separator options:")
    print("1. Pipe (|)  - Recommended (default)")
    print("2. Semicolon (;)")
    print("3. Tab (\\t)")
    print("4. Custom")

    while True:
        choice = input("\nSelect separator (1-4) [default: 1]: ").strip() or "1"

        if choice == "1":
            return "|"
        elif choice == "2":
            return ";"
        elif choice == "3":
            return "\t"
        elif choice == "4":
            custom = input("Enter custom separator character: ").strip()
            if len(custom) == 1:
                return custom
            else:
                print("Please enter a single character.")
                continue
        else:
            print("Invalid choice. Please select 1-4.")


def _print_file_info(info: dict) -> None:
    print("\n--- File Information ---")
    print(f"Total rows: {info['total_rows']}")
    print(f"Columns: {info['column_count']}")
    print(f"File size: {info['file_size']} bytes")
    print(f"Headers: {', '.join(info['header_row'][:5])}")
    if len(info['header_row']) > 5:
        print(f"  ... and {len(info['header_row']) - 5} more columns")


def _warn_about_collisions(detector: CsvSeparatorDetector, separator: str) -> None:
    collisions = detector.count_separator_collisions(separator)
    if collisions:
        print(
            f"⚠️  {collisions} value(s) already contain '{separator}'. The output "
            "will still be valid quoted CSV, but downstream tools that don't "
            "respect CSV quoting may re-encounter this same ambiguity."
        )


def run_interactive() -> int:
    """Original interactive flow: prompts the user at each step."""
    print("=" * 60)
    print("CSV Separator Detector and Converter")
    print("=" * 60)

    project_root = Path.cwd()
    input_dir, output_dir = get_project_directories(project_root)
    print(f"Reading from: {input_dir}")
    print(f"Writing to: {output_dir}")

    file_path = prompt_for_file_path(input_dir)
    detector = CsvSeparatorDetector(file_path)

    info = detector.get_file_info()
    if 'error' not in info:
        _print_file_info(info)

    for warning in detector.get_parse_warnings():
        print(f"⚠️  {warning}")

    print("\n--- Analyzing for separator issues ---")
    has_issues, problem_count = detector.analyze_structural_issues()

    if has_issues:
        print(f"⚠️  Detected {problem_count} rows with potential comma issues")
        print("Some fields may contain commas that interfere with CSV parsing.")
        print("\n--- Error Report ---")
        for issue in detector.get_column_mismatch_report():
            print(issue["highlighted_row"])
    else:
        print("✓ No obvious separator issues detected in the file.")

    proceed = input("\nDo you want to convert the separator? (y/n): ").strip().lower()
    if proceed != 'y':
        print("Conversion cancelled.")
        return EXIT_OK

    new_separator = prompt_for_separator()
    _warn_about_collisions(detector, new_separator)

    print(f"\nConverting to {separator_label(new_separator)} separator...")
    output_path = detector.convert_separator(new_separator, output_dir=str(output_dir))
    print("✓ Conversion complete!")
    print(f"Output file: {output_path}")
    if has_issues:
        print(f"⚠️  Flagged rows are marked with a 'row_issues' column in the output.")
        print(f"Full issue report: {issues_sidecar_path(output_path)}")
    return EXIT_OK


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="csvseparator",
        description="Detect and fix separators in CSV files with text fields containing commas.",
    )
    parser.add_argument(
        "--input", "-i",
        help="Path to the CSV file to analyze. Enables non-interactive mode. "
             "If not absolute and not found relative to the current directory, "
             "it is also looked up inside ./in.",
    )
    parser.add_argument(
        "--separator", "-s",
        choices=sorted(SEPARATOR_CHOICES) + ["custom"],
        default="pipe",
        help="Separator to convert to (default: pipe).",
    )
    parser.add_argument(
        "--custom-separator",
        help="Single character to use when --separator custom is given.",
    )
    parser.add_argument(
        "--output", "-o",
        help="Exact output file path. Overrides --output-dir.",
    )
    parser.add_argument(
        "--output-dir",
        help="Directory for the converted file (default: ./out). Ignored if --output is given.",
    )
    parser.add_argument(
        "--analyze-only",
        action="store_true",
        help="Only report structural issues; do not write a converted file.",
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Actually write the converted file. Without this flag, non-interactive "
             "runs only analyze and report what they would do.",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Report output format (default: text). Use 'json' for runbooks/automation.",
    )
    return parser


def _resolve_separator(args: argparse.Namespace) -> str:
    if args.separator == "custom":
        if not args.custom_separator or len(args.custom_separator) != 1:
            raise ValueError("--custom-separator must be a single character when --separator custom is given")
        return args.custom_separator
    return SEPARATOR_CHOICES[args.separator]


def _resolve_input_path(raw_path: str) -> Path:
    candidate = Path(raw_path)
    if candidate.exists():
        return candidate
    fallback = Path.cwd() / "in" / raw_path
    if fallback.exists():
        return fallback
    return candidate  # let CsvSeparatorDetector raise a clear FileNotFoundError


def run_noninteractive(args: argparse.Namespace) -> int:
    """Scriptable flow driven entirely by CLI flags: for CI jobs and runbooks."""
    file_path = _resolve_input_path(args.input)
    detector = CsvSeparatorDetector(str(file_path))

    info = detector.get_file_info()
    warnings = detector.get_parse_warnings()
    report = detector.get_column_mismatch_report()
    has_issues = bool(report)

    if args.format == "json":
        payload = {
            "file": str(detector.file_path),
            "info": info,
            "warnings": warnings,
            "issues_found": has_issues,
            "issue_count": len(report),
            "report": report,
        }
    else:
        if 'error' not in info:
            _print_file_info(info)
        for warning in warnings:
            print(f"⚠️  {warning}")
        if has_issues:
            print(f"⚠️  Detected {len(report)} rows with potential comma issues")
            for issue in report:
                print(issue["highlighted_row"])
        else:
            print("✓ No obvious separator issues detected in the file.")

    if args.analyze_only:
        if args.format == "json":
            payload["converted"] = False
            print(json.dumps(payload, indent=2))
        return EXIT_ISSUES_FOUND if has_issues else EXIT_OK

    if not args.yes:
        message = "Pass --yes to write the converted file (no changes made)."
        if args.format == "json":
            payload["converted"] = False
            payload["message"] = message
            print(json.dumps(payload, indent=2))
        else:
            print(message)
        return EXIT_ISSUES_FOUND if has_issues else EXIT_OK

    new_separator = _resolve_separator(args)
    collisions = detector.count_separator_collisions(new_separator)
    output_dir = args.output_dir or str(Path.cwd() / "out")
    output_path = detector.convert_separator(new_separator, output_path=args.output, output_dir=output_dir)

    if args.format == "json":
        payload["converted"] = True
        payload["output_path"] = output_path
        payload["separator_collisions"] = collisions
        if has_issues:
            payload["issues_report_path"] = str(issues_sidecar_path(output_path))
        print(json.dumps(payload, indent=2))
    else:
        _warn_about_collisions(detector, new_separator)
        print(f"✓ Conversion complete: {output_path}")
        if has_issues:
            print("⚠️  Flagged rows are marked with a 'row_issues' column in the output.")
            print(f"Full issue report: {issues_sidecar_path(output_path)}")

    return EXIT_ISSUES_FOUND if has_issues else EXIT_OK


def main():
    """Main entry point."""
    # Non-UTF-8 terminals/log collectors (common for scheduled/runbook
    # execution on Windows) would otherwise crash on the emoji below.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    args = build_arg_parser().parse_args()

    try:
        if args.input is None:
            sys.exit(run_interactive())
        else:
            sys.exit(run_noninteractive(args))
    except KeyboardInterrupt:
        print("\n\nOperation cancelled by user.")
        sys.exit(EXIT_ERROR)
    except Exception as e:
        print(f"\n❌ Error: {str(e)}")
        sys.exit(EXIT_ERROR)


if __name__ == "__main__":
    main()
