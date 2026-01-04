import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from tools import scaffold_edits_payload, verify_edits_payload
from tools.verify_edits_contract import REQUIRED_KEYS


class ScaffoldEditsPayloadTests(unittest.TestCase):
    def _write_text(self, path: Path, text: str) -> None:
        with path.open("wb") as handle:
            handle.write(text.encode("utf-8"))

    def _load_json(self, path: Path) -> dict[str, object]:
        return json.loads(path.read_text(encoding="utf-8"))

    def test_scaffold_from_edits_array(self) -> None:
        edits = [{"op": "FILE_WRITE", "path": "docs/sample.txt", "content": "ok"}]
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            input_path = temp_path / "edits.json"
            input_path.write_text(json.dumps(edits), encoding="utf-8")

            exit_code = scaffold_edits_payload.main(
                ["--edits-json", str(input_path), "--artifacts-dir", tempdir]
            )

            self.assertEqual(exit_code, 0)
            output_path = temp_path / "scaffold_edits_payload.json"
            payload = self._load_json(output_path)
            for key in REQUIRED_KEYS:
                self.assertIn(key, payload)
            self.assertEqual(payload["edits"], edits)
            created_at = payload["created_at"]
            self.assertIsInstance(created_at, str)
            self.assertRegex(created_at, r"[+-]\d{2}:\d{2}$")
            parsed = datetime.fromisoformat(created_at)
            self.assertIsNotNone(parsed.tzinfo)

    def test_scaffold_from_edits_object_missing_keys(self) -> None:
        input_payload = {"edits": [{"op": "FILE_WRITE", "path": "docs/ok.txt", "content": "y"}]}
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            input_path = temp_path / "edits.json"
            input_path.write_text(json.dumps(input_payload), encoding="utf-8")

            exit_code = scaffold_edits_payload.main(
                ["--edits-json", str(input_path), "--artifacts-dir", tempdir]
            )

            self.assertEqual(exit_code, 0)
            payload = self._load_json(temp_path / "scaffold_edits_payload.json")
            self.assertEqual(payload["edits"], input_payload["edits"])
            self.assertEqual(payload["assumptions"], [])
            self.assertEqual(payload["risks"], [])
            self.assertEqual(payload["gates"], [])
            self.assertEqual(payload["rollback"], [])

    def test_scaffold_passthrough_full_payload(self) -> None:
        input_payload = {
            "version": "v1",
            "created_at": "2024-01-01T00:00:00Z",
            "edits": [],
            "assumptions": ["a"],
            "risks": [],
            "gates": [],
            "rollback": [],
        }
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            input_path = temp_path / "edits.json"
            input_path.write_text(json.dumps(input_payload), encoding="utf-8")

            exit_code = scaffold_edits_payload.main(
                ["--edits-json", str(input_path), "--artifacts-dir", tempdir]
            )

            self.assertEqual(exit_code, 0)
            payload = self._load_json(temp_path / "scaffold_edits_payload.json")
            self.assertEqual(payload, input_payload)

    def test_verify_edits_payload_accepts_scaffolded_payloads(self) -> None:
        edits = [{"op": "FILE_WRITE", "path": "docs/ok.txt", "content": "x"}]
        full_payload = {
            "version": "v1",
            "created_at": "2024-01-01T00:00:00Z",
            "edits": edits,
            "assumptions": [],
            "risks": [],
            "gates": [],
            "rollback": [],
        }
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)

            edits_path = temp_path / "edits_only.json"
            edits_path.write_text(json.dumps(edits), encoding="utf-8")
            scaffold_edits_payload.main(["--edits-json", str(edits_path), "--artifacts-dir", tempdir])

            scaffold_path = temp_path / "scaffold_edits_payload.json"
            with mock.patch.object(
                sys,
                "argv",
                [
                    "verify_edits_payload",
                    "--edits-path",
                    str(scaffold_path),
                    "--artifacts-dir",
                    tempdir,
                ],
            ):
                self.assertEqual(verify_edits_payload.main(), 0)

            full_path = temp_path / "full_payload.json"
            full_path.write_text(json.dumps(full_payload), encoding="utf-8")
            scaffold_edits_payload.main(["--edits-json", str(full_path), "--artifacts-dir", tempdir])
            with mock.patch.object(
                sys,
                "argv",
                [
                    "verify_edits_payload",
                    "--edits-path",
                    str(scaffold_path),
                    "--artifacts-dir",
                    tempdir,
                ],
            ):
                self.assertEqual(verify_edits_payload.main(), 0)

    def test_crlf_bom_normalization(self) -> None:
        edits = [{"op": "FILE_WRITE", "path": "docs/ok.txt", "content": "x"}]
        raw = json.dumps(edits, indent=2).replace("\n", "\r\n")
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            input_path = temp_path / "edits.json"
            self._write_text(input_path, "\ufeff" + raw)

            exit_code = scaffold_edits_payload.main(
                ["--edits-json", str(input_path), "--artifacts-dir", tempdir]
            )

            self.assertEqual(exit_code, 0)
            output_text = (temp_path / "scaffold_edits_payload.json").read_text(encoding="utf-8")
            summary_text = (temp_path / "scaffold_edits_payload.txt").read_text(encoding="utf-8")
            self.assertNotIn("\r", output_text)
            self.assertNotIn("\r", summary_text)
            self.assertTrue(summary_text.endswith("\n"))


if __name__ == "__main__":
    unittest.main()
