import json
import tempfile
import unittest
from pathlib import Path

from tools import verify_consistency


def _write_event(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def _valid_event_payload() -> dict:
    return {
        "schema_version": "v1",
        "ts_utc": "2024-01-01T00:00:00Z",
        "event_type": "test",
        "symbol": "TEST",
        "severity": "info",
        "message": "ok",
    }


class VerifyConsistencyArchivesTests(unittest.TestCase):
    def test_archives_skipped_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            logs_dir = root / "Logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            _write_event(logs_dir / "events.jsonl", _valid_event_payload())
            archive_path = root / "Data" / "events_2024-01-02.jsonl"
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            archive_path.write_text("{}\n", encoding="utf-8")

            results = verify_consistency.check_events_schema(
                include_archives=False,
                root=root,
                logs_dir=logs_dir,
            )

            self.assertTrue(
                any(
                    res.name == "events archives"
                    and res.status == "SKIP"
                    and res.details == "1"
                    for res in results
                )
            )
            self.assertTrue(
                any(res.name == "events schema" and res.status == "OK" for res in results)
            )

    def test_archives_validated_when_opted_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            logs_dir = root / "Logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            _write_event(logs_dir / "events.jsonl", _valid_event_payload())
            archive_path = root / "Data" / "events_2024-01-02.jsonl"
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            archive_path.write_text("{}\n", encoding="utf-8")

            results = verify_consistency.check_events_schema(
                include_archives=True,
                root=root,
                logs_dir=logs_dir,
            )

            failures = [res for res in results if res.name == "events schema" and res.status == "FAIL"]
            self.assertTrue(failures)
            self.assertIn("events_2024-01-02.jsonl", failures[0].details)
            self.assertIn("missing keys", failures[0].details)

    def test_pr20_skipped_by_default(self) -> None:
        results = verify_consistency._legacy_gate_checks(include_legacy_gates=False)
        self.assertTrue(results)
        self.assertEqual(results[0].status, "SKIP")
        self.assertIn("verify_pr20_gate.py", results[0].name)


if __name__ == "__main__":
    unittest.main()
