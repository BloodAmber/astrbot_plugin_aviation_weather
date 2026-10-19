from __future__ import annotations

import os
import sys
import unittest
from datetime import timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_aviation_weather.formatter import (  # noqa: E402
    format_raw_reports,
    format_report,
)
from astrbot_plugin_aviation_weather.weather_api import WeatherClient  # noqa: E402

LIVE = os.environ.get("AVWX_LIVE") == "1"


@unittest.skipUnless(LIVE, "set AVWX_LIVE=1 to run live API tests")
class LiveApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_icao_resolved_from_iata(self):
        report = await WeatherClient().fetch("PEK")
        self.assertEqual(report.icao, "ZBAA")
        self.assertIsNotNone(report.metar)
        text = format_report(report, timezone(timedelta(hours=8)))
        self.assertIn("ZBAA", text)
        self.assertIn("METAR", text)
        raw = format_raw_reports(report)
        self.assertIn("METAR", raw)
        self.assertNotIn("【", raw)

    async def test_multiple_airports(self):
        client = WeatherClient()
        for code in ("ZBAA", "ZSPD", "KJFK", "EGLL"):
            with self.subTest(code=code):
                report = await client.fetch(code)
                self.assertTrue(report.metar or report.taf)


if __name__ == "__main__":
    unittest.main()
