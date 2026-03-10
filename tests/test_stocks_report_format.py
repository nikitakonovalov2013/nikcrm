import unittest
from datetime import datetime
from decimal import Decimal

from bot.app.services.stocks_reports import MaterialAgg, ReportData, StockEvent, UserOutgoingAgg
from bot.app.services.stocks_reports_format import RUB_PER_KG, format_report_html


class TestStocksReportFormat(unittest.TestCase):
    def _base_data(self, *, silicone_out: Decimal, silicone_in: Decimal) -> ReportData:
        return ReportData(
            start=datetime(2026, 1, 28, 10, 0, 0),
            end=datetime(2026, 1, 28, 23, 59, 59),
            materials=[],
            total_in=Decimal("0"),
            total_out=Decimal("0"),
            top_out=None,
            events=[],
            silicone_in=silicone_in,
            silicone_out=silicone_out,
            silicone_out_by_user=[],
            outgoing_by_user=[],
            total_remains_kg=Decimal("0"),
            warehouse_price_rub=0,
        )

    def test_money_uses_int_kg(self):
        data = self._base_data(silicone_out=Decimal("0"), silicone_in=Decimal("0"))
        data.total_out = Decimal("69.81")
        text = format_report_html("ignored", data)
        self.assertIn("— Расход: 69,81 кг", text)
        self.assertIn(f"💰 В сумме: {69 * RUB_PER_KG} руб", text)

    def test_incoming_line_always_shown(self):
        data = self._base_data(silicone_out=Decimal("0"), silicone_in=Decimal("0"))
        data.total_in = Decimal("0.5")
        text = format_report_html("ignored", data)
        self.assertIn("+ Приход: 0,5 кг", text)

    def test_warehouse_price_line_between_sum_and_incoming(self):
        data = self._base_data(silicone_out=Decimal("0"), silicone_in=Decimal("0"))
        data.total_out = Decimal("93.98")
        data.total_in = Decimal("0")
        data.warehouse_price_rub = 20_000_000
        text = format_report_html("ignored", data)
        self.assertIn("💵 Цена склада: 20 000 000 руб", text)
        self.assertTrue(text.index("💰 В сумме:") < text.index("💵 Цена склада:") < text.index("+ Приход:"))

    def test_per_user_block(self):
        data = self._base_data(silicone_out=Decimal("0"), silicone_in=Decimal("0"))
        data.outgoing_by_user = [
            UserOutgoingAgg(user_id=1, fio="Иван Петров", outgoing=Decimal("3.4")),
        ]
        text = format_report_html("ignored", data)
        self.assertIn("👥 <b>По работникам</b>", text)
        self.assertIn("Иван Петров: 3,4 кг", text)
        self.assertNotIn("руб", text)

    def test_materials_block_line_format(self):
        data = self._base_data(silicone_out=Decimal("0"), silicone_in=Decimal("0"))
        data.materials = [
            MaterialAgg(material_id=1, name="Силикон A", unit="кг", incoming=Decimal("10"), outgoing=Decimal("2.5")),
            MaterialAgg(material_id=2, name="Пигмент", unit="кг", incoming=Decimal("1"), outgoing=Decimal("0")),
        ]
        text = format_report_html("ignored", data)
        self.assertIn("📦 <b>По материалам</b>", text)
        self.assertIn("Силикон A: +10 кг | -2,5 кг | Δ7,5 кг", text)

    def test_last_events_not_included(self):
        data = self._base_data(silicone_out=Decimal("0"), silicone_in=Decimal("0"))
        data.events = [
            StockEvent(
                dt=datetime(2026, 1, 28, 12, 30, 0),
                kind="out",
                user_fio="Иван Петров",
                material_name="Силикон A",
                amount=Decimal("1"),
                unit="кг",
            )
        ]
        text = format_report_html("ignored", data)
        self.assertNotIn("Последние события", text)


if __name__ == "__main__":
    unittest.main()
