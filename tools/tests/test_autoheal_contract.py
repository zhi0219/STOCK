import tempfile
import unittest
from pathlib import Path

from tools import autoheal_collect, verify_autoheal_contract


class AutohealContractTests(unittest.TestCase):
    def test_collect_and_verify(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            artifacts_root = Path(tmpdir) / "autoheal"
            rc_collect = autoheal_collect.main(["--artifacts-dir", str(artifacts_root)])
            self.assertEqual(rc_collect, 0)
            latest_path = artifacts_root / "_latest.txt"
            self.assertTrue(latest_path.exists())
            latest_dir = Path(latest_path.read_text(encoding="utf-8").strip())
            self.assertTrue(latest_dir.exists())
            rc_verify = verify_autoheal_contract.main(
                ["--artifacts-dir", str(artifacts_root)]
            )
            self.assertEqual(rc_verify, 0)
            index_json = latest_dir / "EVIDENCE_INDEX.json"
            index_txt = latest_dir / "EVIDENCE_INDEX.txt"
            self.assertTrue(index_json.exists())
            self.assertTrue(index_txt.exists())
            self.assertFalse(index_json.read_bytes().startswith(b"\xef\xbb\xbf"))
            self.assertFalse(index_txt.read_bytes().startswith(b"\xef\xbb\xbf"))


if __name__ == "__main__":
    unittest.main()
