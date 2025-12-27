import unittest
from decimal import Decimal

from shared.utils import format_number


class TestFormatNumber(unittest.TestCase):
    def test_no_scientific(self):
        self.assertEqual(format_number(Decimal("200")), "200")
        self.assertEqual(format_number(Decimal("1.04E+3")), "1 040")
        self.assertEqual(format_number(Decimal("2.44E+3")), "2 440")

    def test_decimals(self):
        self.assertEqual(format_number(Decimal("12.0")), "12")
        self.assertEqual(format_number(Decimal("12.50")), "12,5")
        self.assertEqual(format_number(Decimal("12.345")), "12,35")

    def test_float_and_int(self):
        self.assertEqual(format_number(1439), "1 439")
        self.assertEqual(format_number(12.34), "12,34")

    def test_none(self):
        self.assertEqual(format_number(None), "0")
        self.assertEqual(format_number(None, none_as_zero=False), "—")

    def test_negative(self):
        self.assertEqual(format_number(Decimal("-1040")), "-1 040")
        self.assertEqual(format_number(Decimal("-12.5")), "-12,5")


if __name__ == "__main__":
    unittest.main()
