import unittest

from tools import verify_inventory_contract


class VerifyInventoryContractTests(unittest.TestCase):
    def test_normalized_compare_allows_crlf(self) -> None:
        expected = "line-one\nline-two\n"
        actual = "line-one\r\nline-two\r\n"
        self.assertTrue(verify_inventory_contract._normalized_equal(actual, expected))


if __name__ == "__main__":
    unittest.main()
