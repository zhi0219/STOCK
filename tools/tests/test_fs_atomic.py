import tempfile
import unittest
from pathlib import Path

from tools.fs_atomic import atomic_write_text


class AtomicWriteTextTests(unittest.TestCase):
    def test_atomic_write_text_emits_lf_only_utf8(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "out.txt"
            atomic_write_text(path, "a\nb\n")
            data = path.read_bytes()
            self.assertFalse(data.startswith(b"\xef\xbb\xbf"))
            self.assertNotIn(b"\r\n", data)
            self.assertNotIn(b"\r", data)
            self.assertTrue(data.endswith(b"\n"))
            self.assertFalse(data.endswith(b"\n\n"))


if __name__ == "__main__":
    unittest.main()
