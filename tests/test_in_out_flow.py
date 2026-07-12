import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from csvseparator.main import build_arg_parser, get_project_directories, run_noninteractive
from csvseparator.separator import CsvSeparatorDetector


class ProjectDirectoriesTests(unittest.TestCase):
    def test_get_project_directories_creates_in_and_out_folders(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "sample-project"
            input_dir, output_dir = get_project_directories(project_root)

            self.assertEqual(input_dir, project_root / "in")
            self.assertEqual(output_dir, project_root / "out")
            self.assertTrue(input_dir.exists())
            self.assertTrue(output_dir.exists())

    def test_reports_malformed_rows_with_extra_columns(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "sample.csv"
            csv_path.write_text(
                "id,name,notes\n"
                "1,Alice,ok\n"
                "2,Bob,broken,extra\n",
                encoding="utf-8",
            )

            detector = CsvSeparatorDetector(str(csv_path))
            report = detector.get_column_mismatch_report()

            self.assertEqual(len(report), 1)
            self.assertEqual(report[0]["row_number"], 3)
            self.assertEqual(report[0]["expected_columns"], 3)
            self.assertEqual(report[0]["actual_columns"], 4)
            self.assertIn("❗", report[0]["highlighted_row"])

    def test_ignores_valid_rows_with_long_text(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "sample.csv"
            csv_path.write_text(
                "id,name,notes\n"
                "1,Alice,This is a normal long note that still fits the expected structure\n"
                "2,Bob,Another long note with enough length to be descriptive but still valid\n",
                encoding="utf-8",
            )

            detector = CsvSeparatorDetector(str(csv_path))
            report = detector.get_column_mismatch_report()

            self.assertEqual(report, [])

    def test_reports_malformed_csv_as_a_data_quality_issue(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "sample.csv"
            csv_path.write_text(
                'id,name,notes\n'
                '1,Alice,"unterminated note\n'
                '2,Bob,ok\n',
                encoding="utf-8",
            )

            detector = CsvSeparatorDetector(str(csv_path))
            report = detector.get_column_mismatch_report()

            self.assertEqual(len(report), 1)
            self.assertIn("CSV parsing error", report[0]["reasons"][0])
            self.assertIn("❗", report[0]["highlighted_row"])

    def test_recovers_from_a_single_bad_row_instead_of_aborting_the_whole_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "sample.csv"
            csv_path.write_text(
                'id,name,notes\n'
                '1,Alice,ok\n'
                '2,Bob,"ab"cd,extra\n'
                '3,Carol,fine\n',
                encoding="utf-8",
            )

            detector = CsvSeparatorDetector(str(csv_path))
            report = detector.get_column_mismatch_report()

            # Row 3 (Carol) parses fine and should not be swallowed by the
            # earlier row's quoting glitch.
            row_numbers = [entry["row_number"] for entry in report]
            self.assertEqual(row_numbers, [3])
            self.assertIn("expected 3 columns but found 4", report[0]["reasons"][0])

            warnings = detector.get_parse_warnings()
            self.assertTrue(any("re-parsed leniently" in w for w in warnings))

    def test_column_profile_ignores_malformed_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "sample.csv"
            lines = ["id,category,amount"]
            lines += [f"{i},Books,{i * 2}" for i in range(1, 8)]
            lines += [f"{i},{100 - i},{i * 2},extra" for i in range(8, 13)]
            lines.append("13,42,25")
            csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

            detector = CsvSeparatorDetector(str(csv_path))
            report = detector.get_column_mismatch_report()

            last_row = next(entry for entry in report if "42" in entry["highlighted_row"] and entry["actual_columns"] == 3)
            self.assertTrue(
                any("usually contains text" in reason for reason in last_row["reasons"]),
                last_row["reasons"],
            )

    def test_reads_utf8_bom_file_without_corrupting_the_header(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "sample.csv"
            csv_path.write_text("id,name,notes\n1,Alice,ok\n", encoding="utf-8-sig")

            detector = CsvSeparatorDetector(str(csv_path))
            info = detector.get_file_info()

            self.assertEqual(info["header_row"][0], "id")
            self.assertEqual(detector.get_parse_warnings(), [])

    def test_falls_back_to_cp1252_for_non_utf8_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "sample.csv"
            csv_path.write_bytes("id,name,notes\n1,Caf\xe9,ok\n".encode("cp1252"))

            detector = CsvSeparatorDetector(str(csv_path))
            info = detector.get_file_info()

            self.assertNotIn("error", info)
            warnings = detector.get_parse_warnings()
            self.assertTrue(any("cp1252" in w for w in warnings))

    def test_convert_annotates_flagged_rows_and_writes_sidecar_report(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "sample.csv"
            csv_path.write_text(
                "id,name,notes\n"
                "1,Alice,ok\n"
                "2,Bob,broken,extra\n",
                encoding="utf-8",
            )
            out_dir = Path(tmpdir) / "out"

            detector = CsvSeparatorDetector(str(csv_path))
            output_path = detector.convert_separator("|", output_dir=str(out_dir))

            lines = Path(output_path).read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[0], "id|name|notes|row_issues")
            self.assertEqual(lines[1], "1|Alice|ok|")
            self.assertIn("expected 3 columns but found 4", lines[2])

            report_path = Path(output_path).parent / "sample_converted_pipe.issues.json"
            self.assertTrue(report_path.exists())
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report[0]["row_number"], 3)

    def test_convert_leaves_clean_files_unchanged(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "clean.csv"
            csv_path.write_text("id,name\n1,Alice\n2,Bob\n", encoding="utf-8")
            out_dir = Path(tmpdir) / "out"

            detector = CsvSeparatorDetector(str(csv_path))
            output_path = detector.convert_separator("|", output_dir=str(out_dir))

            lines = Path(output_path).read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[0], "id|name")
            self.assertEqual(len(list(Path(output_path).parent.glob("*.issues.json"))), 0)

    def test_counts_values_that_already_contain_the_target_separator(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "sample.csv"
            csv_path.write_text(
                'id,name,notes\n'
                '1,Alice,"a|b"\n'
                '2,Bob,ok\n',
                encoding="utf-8",
            )

            detector = CsvSeparatorDetector(str(csv_path))
            self.assertEqual(detector.count_separator_collisions("|"), 1)
            self.assertEqual(detector.count_separator_collisions(";"), 0)


class NonInteractiveCliTests(unittest.TestCase):
    def _make_sample(self, tmpdir):
        csv_path = Path(tmpdir) / "sample.csv"
        csv_path.write_text(
            "id,name,notes\n"
            "1,Alice,ok\n"
            "2,Bob,broken,extra\n",
            encoding="utf-8",
        )
        return csv_path

    def test_analyze_only_exits_with_issues_found_code(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = self._make_sample(tmpdir)
            args = build_arg_parser().parse_args([
                "--input", str(csv_path), "--analyze-only", "--format", "json",
            ])

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                exit_code = run_noninteractive(args)

            self.assertEqual(exit_code, 2)
            payload = json.loads(buffer.getvalue())
            self.assertTrue(payload["issues_found"])
            self.assertEqual(payload["converted"], False)

    def test_requires_yes_flag_to_actually_write_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "clean.csv"
            csv_path.write_text("id,name\n1,Alice\n", encoding="utf-8")
            out_dir = Path(tmpdir) / "out"

            args = build_arg_parser().parse_args([
                "--input", str(csv_path), "--output-dir", str(out_dir),
            ])
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                exit_code = run_noninteractive(args)

            self.assertEqual(exit_code, 0)
            self.assertFalse(out_dir.exists())

            args = build_arg_parser().parse_args([
                "--input", str(csv_path), "--output-dir", str(out_dir), "--yes",
            ])
            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = run_noninteractive(args)

            self.assertEqual(exit_code, 0)
            converted = list(out_dir.glob("*_converted_pipe.csv"))
            self.assertEqual(len(converted), 1)


if __name__ == "__main__":
    unittest.main()
