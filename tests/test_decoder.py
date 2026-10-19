from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_aviation_weather.decoder import (  # noqa: E402
    build_metar,
    build_taf,
    decode_weather_token,
    derive_flight_category,
    format_taf_group,
    format_temp_extremes,
    relative_humidity,
)
from astrbot_plugin_aviation_weather.formatter import (  # noqa: E402
    format_raw_reports,
    format_report,
    split_message,
)
from astrbot_plugin_aviation_weather.weather_api import (  # noqa: E402
    WeatherReport,
    normalize_airport_code,
)

TZ = timezone(timedelta(hours=8))
UTC = timezone.utc


def metar_payload(
    icao: str,
    raw: str,
    **extra,
):
    payload = {
        "icaoId": icao,
        "rawOb": raw,
        "obsTime": 1792380600,
        "reportTime": "2026-10-19T03:30:00.000Z",
        "lat": 40.082,
        "lon": 116.603,
        "elev": 31,
        "name": "Test Intl",
    }
    payload.update(extra)
    return payload


class AirportCodeTests(unittest.TestCase):
    def test_icao_and_iata(self):
        self.assertEqual(normalize_airport_code("zbaa"), "ZBAA")
        self.assertEqual(normalize_airport_code("PEK"), "ZBAA")
        self.assertEqual(normalize_airport_code(" pek, "), "ZBAA")
        self.assertEqual(normalize_airport_code("JFK"), "KJFK")

    def test_invalid(self):
        with self.assertRaises(ValueError):
            normalize_airport_code("")
        with self.assertRaises(ValueError):
            normalize_airport_code("ZZZZZ")
        with self.assertRaises(ValueError):
            normalize_airport_code("XYZ")


class MetarDecodeTests(unittest.TestCase):
    def test_metric_cavok(self):
        payload = metar_payload(
            "ZBAA",
            "METAR ZBAA 190330Z 17002MPS 110V270 CAVOK 16/03 Q1020 NOSIG",
            temp=16,
            dewp=3,
            altim=1020,
            fltCat="VFR",
        )
        data = build_metar(payload)
        self.assertEqual(data.station, "ZBAA")
        self.assertEqual(data.observed_at, datetime(2026, 10, 19, 3, 30, tzinfo=UTC))
        self.assertTrue(data.cavok)
        self.assertEqual(data.wind.direction, 170)
        self.assertEqual(data.wind.speed, 2.0)
        self.assertEqual(data.wind.unit, "MPS")
        self.assertEqual((data.wind.var_from, data.wind.var_to), (110, 270))
        self.assertEqual(data.temp_c, 16)
        self.assertEqual(data.dew_c, 3)
        self.assertEqual(data.pressure_hpa, 1020)
        self.assertEqual(data.flight_category, "VFR")
        self.assertEqual(data.trend_raw, "NOSIG")

    def test_statute_miles_and_remarks(self):
        payload = metar_payload(
            "KJFK",
            "METAR KJFK 190251Z 07004KT 10SM FEW250 09/03 A3054 RMK AO2 SLP342 T00940033 58004 $",
            temp=9.4,
            dewp=3.3,
            altim=1034.3,
            fltCat="VFR",
            clouds=[{"cover": "FEW", "base": 25000}],
        )
        data = build_metar(payload)
        self.assertEqual(data.wind.unit, "KT")
        self.assertIn("16.1 km", data.visibility_text)
        self.assertEqual(data.clouds[0].base_ft, 25000)
        self.assertAlmostEqual(data.pressure_hpa, 1034.2, places=1)
        self.assertIn("AO2", data.remarks)
        self.assertIn("SLP", data.remarks)

    def test_weather_and_cloud_layers(self):
        payload = metar_payload(
            "ZSPD",
            "METAR ZSPD 190330Z 04003MPS 360V080 9999 -RA BKN013 OVC033 24/21 Q1018",
            temp=24,
            dewp=21,
            altim=1018,
            fltCat="MVFR",
            clouds=[
                {"cover": "BKN", "base": 1300},
                {"cover": "OVC", "base": 3300},
            ],
        )
        data = build_metar(payload)
        self.assertEqual(data.weather, ["小雨"])
        self.assertEqual(len(data.clouds), 2)
        self.assertIn("1300 ft", format_clouds_text(data))
        self.assertEqual(data.visibility_text, "≥10 km")

    def test_auto_and_unknown_cloud_type(self):
        payload = metar_payload(
            "EGLL",
            "METAR EGLL 190350Z AUTO 09006KT 9999 -RA FEW008/// 10/09 Q1025",
            temp=10,
            dewp=9,
            altim=1025,
            fltCat="VFR",
        )
        data = build_metar(payload)
        self.assertTrue(data.auto)
        self.assertEqual(data.weather, ["小雨"])
        self.assertEqual(data.clouds[0].base_ft, 800)
        self.assertEqual(data.clouds[0].cloud_type, "")

    def test_json_fallback_without_raw(self):
        payload = {
            "icaoId": "OMDB",
            "obsTime": 1792380600,
            "temp": 30,
            "dewp": 16,
            "wdir": "VRB",
            "wspd": 5,
            "visib": "6+",
            "altim": 1013,
            "fltCat": "VFR",
            "clouds": [],
        }
        data = build_metar(payload)
        self.assertTrue(data.wind.variable)
        self.assertEqual(data.wind.speed_kt, 5)
        self.assertTrue(data.visibility_text.startswith("≥"))
        self.assertEqual(data.flight_category, "VFR")

    def test_missing_wind_direction_does_not_crash(self):
        payload = metar_payload(
            "ZBAA",
            "",
            wdir=None,
            wspd=5,
            temp=16,
            dewp=3,
            altim=1020,
        )
        data = build_metar(payload)
        from astrbot_plugin_aviation_weather.decoder import format_wind

        self.assertIn("风向不定", format_wind(data.wind))


class TafDecodeTests(unittest.TestCase):
    def _taf(self, icao, raw, fcsts, **extra):
        payload = {
            "icaoId": icao,
            "rawTAF": raw,
            "issueTime": "2026-10-18T21:07:00.000Z",
            "validTimeFrom": 1792368000,
            "validTimeTo": 1792476000,
            "fcsts": fcsts,
        }
        payload.update(extra)
        return build_taf(payload)

    def test_initial_and_temperature_extremes(self):
        taf = self._taf(
            "ZBAA",
            "TAF ZBAA 182107Z 1900/2006 36004MPS CAVOK "
            "TX18/1906Z TX13/2006Z TN06/1921Z",
            [
                {
                    "timeFrom": 1792368000,
                    "timeTo": 1792476000,
                    "wdir": 360,
                    "wspd": 8,
                    "visib": "6+",
                    "clouds": [{"cover": "NSC", "base": None}],
                }
            ],
        )
        self.assertEqual(len(taf.groups), 1)
        self.assertTrue(taf.groups[0].cavok)
        self.assertIn("CAVOK", taf.groups[0].visibility_text)
        self.assertEqual(len(taf.temp_extremes), 3)
        self.assertEqual(
            [item["type"] for item in taf.temp_extremes],
            ["MAX", "MAX", "MIN"],
        )
        text = format_temp_extremes(taf.temp_extremes, TZ)
        self.assertIn("最高 18°C", text)
        self.assertIn("最低 6°C", text)

    def test_becmg_time_uses_transition_window(self):
        taf = self._taf(
            "ZSPD",
            "TAF ZSPD 182110Z 1900/2006 04004MPS 8000 BKN015 "
            "TX25/1906Z TX30/2006Z TN21/1921Z BECMG 1909/1910 12004MPS",
            [
                {
                    "timeFrom": 1792368000,
                    "timeTo": 1792400400,
                    "wdir": 40,
                    "wspd": 8,
                    "visib": 4.97,
                    "clouds": [{"cover": "BKN", "base": 1500}],
                },
                {
                    "timeFrom": 1792400400,
                    "timeTo": 1792476000,
                    "timeBec": 1792404000,
                    "fcstChange": "BECMG",
                    "wdir": 120,
                    "wspd": 8,
                    "visib": 4.97,
                    "clouds": [{"cover": "BKN", "base": 1500}],
                },
            ],
        )
        self.assertEqual(len(taf.groups), 2)
        text = format_taf_group(taf.groups[1], TZ)
        self.assertIn("逐渐转变", text)
        self.assertIn("10-19 17:00", text)
        self.assertIn("10-19 18:00", text)

    def test_fm_and_probability(self):
        taf = self._taf(
            "EGLL",
            "TAF EGLL 182305Z 1823/1924 03008KT 9999 SCT014 BKN020 "
            "TEMPO 1823/1906 8000 -RA BKN012 "
            "PROB30 TEMPO 1823/1902 4000 DZRA BKN008 "
            "BECMG 1902/1904 09010KT",
            [
                {
                    "timeFrom": 1792364400,
                    "timeTo": 1792375200,
                    "wdir": 30,
                    "wspd": 8,
                    "visib": "6+",
                    "clouds": [
                        {"cover": "SCT", "base": 1400},
                        {"cover": "BKN", "base": 2000},
                    ],
                },
                {
                    "timeFrom": 1792364400,
                    "timeTo": 1792389600,
                    "fcstChange": "TEMPO",
                    "visib": 4.97,
                    "wxString": "-RA",
                    "clouds": [{"cover": "BKN", "base": 1200}],
                },
                {
                    "timeFrom": 1792364400,
                    "timeTo": 1792375200,
                    "fcstChange": "TEMPO",
                    "probability": 30,
                    "visib": 2.49,
                    "wxString": "DZ RA",
                    "clouds": [{"cover": "BKN", "base": 800}],
                },
                {
                    "timeFrom": 1792375200,
                    "timeTo": 1792443600,
                    "timeBec": 1792382400,
                    "fcstChange": "BECMG",
                    "wdir": 90,
                    "wspd": 10,
                    "visib": "6+",
                    "clouds": [
                        {"cover": "SCT", "base": 1400},
                        {"cover": "BKN", "base": 2000},
                    ],
                },
            ],
        )
        self.assertEqual(len(taf.groups), 4)
        self.assertIn("30% 概率短时", format_taf_group(taf.groups[2], TZ))
        self.assertIn("逐渐转变", format_taf_group(taf.groups[3], TZ))

    def test_fm_group(self):
        taf = self._taf(
            "KJFK",
            "TAF KJFK 190341Z 1904/2006 07005KT P6SM SKC "
            "FM190900 03007KT P6SM SKC FM191500 04013G19KT P6SM FEW250",
            [
                {
                    "timeFrom": 1792382400,
                    "timeTo": 1792400400,
                    "wdir": 70,
                    "wspd": 5,
                    "visib": "6+",
                    "clouds": [{"cover": "SKC", "base": None}],
                },
                {
                    "timeFrom": 1792400400,
                    "timeTo": 1792422000,
                    "fcstChange": "FM",
                    "wdir": 30,
                    "wspd": 7,
                    "visib": "6+",
                    "clouds": [{"cover": "SKC", "base": None}],
                },
            ],
        )
        text = format_taf_group(taf.groups[1], TZ)
        self.assertIn("从", text)
        self.assertIn("10-19 17:00", text)


class WeatherAndUtilityTests(unittest.TestCase):
    def test_weather_tokens(self):
        cases = {
            "-RA": "小雨",
            "+RA": "大雨",
            "-SHRA": "小阵雨",
            "+TSRA": "强雷雨",
            "RASN": "雨夹雪",
            "BCFG": "散片雾",
            "VCSH": "附近阵雨",
            "FZRA": "冻雨",
            "SHRA": "阵雨",
            "BR": "轻雾",
            "HZ": "霾",
            "+TSRAGR": "强雷暴伴雨和冰雹",
            "NSW": "无重要天气（NSW）",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(decode_weather_token(raw), expected)

    def test_flight_categories(self):
        self.assertEqual(derive_flight_category(10.0, []), "VFR")
        self.assertEqual(derive_flight_category(5.0, []), "MVFR")
        self.assertEqual(derive_flight_category(2.0, []), "IFR")
        self.assertEqual(derive_flight_category(0.5, []), "LIFR")

    def test_relative_humidity(self):
        self.assertEqual(relative_humidity(16, 3), 42)
        self.assertIsNone(relative_humidity(None, 3))

    def test_split_message(self):
        text = "\n".join(f"第 {index} 行 " + "x" * 80 for index in range(20))
        chunks = split_message(text, 300)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 300 for chunk in chunks))
        self.assertTrue(chunks[0].startswith("（1/"))

    def test_full_report(self):
        metar = metar_payload(
            "ZBAA",
            "METAR ZBAA 190330Z 17002MPS 110V270 CAVOK 16/03 Q1020 NOSIG",
            temp=16,
            dewp=3,
            altim=1020,
            fltCat="VFR",
        )
        taf = {
            "icaoId": "ZBAA",
            "rawTAF": "TAF ZBAA 182107Z 1900/2006 36004MPS CAVOK TX18/1906Z TN06/1921Z",
            "issueTime": "2026-10-18T21:07:00.000Z",
            "validTimeFrom": 1792368000,
            "validTimeTo": 1792476000,
            "fcsts": [
                {
                    "timeFrom": 1792368000,
                    "timeTo": 1792476000,
                    "wdir": 360,
                    "wspd": 8,
                    "visib": "6+",
                    "clouds": [{"cover": "NSC", "base": None}],
                }
            ],
        }
        station = {
            "icaoId": "ZBAA",
            "iataId": "PEK",
            "site": "Beijing Intl",
            "country": "CN",
            "lat": 40.082,
            "lon": 116.603,
            "elev": 31,
            "wmoId": "54511",
            "siteType": ["METAR", "TAF"],
        }
        report = WeatherReport(
            icao="ZBAA",
            metar=metar,
            taf=taf,
            station=station,
        )
        text = format_report(report, TZ)
        self.assertIn("北京首都国际机场", text)
        self.assertIn("METAR ZBAA", text)
        self.assertIn("TAF ZBAA", text)
        self.assertIn("VFR", text)
        self.assertIn("aviationweather.gov", text)
        self.assertIn("最高 18°C", text)

    def test_raw_report_output(self):
        report = WeatherReport(
            "ZBAA",
            metar={"rawOb": "METAR ZBAA 190330Z CAVOK 16/03 Q1020"},
            taf={"rawTAF": "TAF ZBAA 182107Z 1900/2006 CAVOK"},
        )
        text = format_raw_reports(report)
        self.assertEqual(
            text,
            "METAR ZBAA 190330Z CAVOK 16/03 Q1020\nTAF ZBAA 182107Z 1900/2006 CAVOK",
        )
        self.assertNotIn("【", text)
        self.assertNotIn("数据来源", text)

    def test_raw_report_taf_fallback(self):
        report = WeatherReport(
            "ZBAA",
            metar={
                "rawOb": "METAR ZBAA 190330Z CAVOK 16/03 Q1020",
                "rawTaf": "TAF ZBAA 182107Z 1900/2006 CAVOK",
            },
            taf={},
        )
        text = format_raw_reports(report)
        self.assertIn("TAF ZBAA", text)
        self.assertEqual(len(text.splitlines()), 2)

    def test_report_without_taf(self):
        metar = metar_payload(
            "ZBAA",
            "METAR ZBAA 190330Z 17002MPS CAVOK 16/03 Q1020 NOSIG",
        )
        text = format_report(WeatherReport("ZBAA", metar=metar), TZ)
        self.assertIn("暂无 TAF", text)
        self.assertNotIn("【TAF 预报】\nTAF", text)

    def test_report_without_metar(self):
        taf = {
            "icaoId": "ZBAA",
            "rawTAF": "TAF ZBAA 182107Z 1900/2006 36004MPS CAVOK",
            "issueTime": "2026-10-18T21:07:00.000Z",
            "validTimeFrom": 1792368000,
            "validTimeTo": 1792476000,
            "fcsts": [],
        }
        text = format_report(WeatherReport("ZBAA", taf=taf), TZ)
        self.assertIn("暂无 METAR", text)
        self.assertIn("TAF ZBAA", text)

    def test_vatsim_source(self):
        metar = metar_payload(
            "ZBAA",
            "METAR ZBAA 190330Z 17002MPS CAVOK 16/03 Q1020 NOSIG",
        )
        report = WeatherReport("ZBAA", metar=metar, source="VATSIM METAR（备用源）")
        text = format_report(report, TZ)
        self.assertIn("VATSIM METAR API", text)
        self.assertIn("备用源", text)


def format_clouds_text(data):
    from astrbot_plugin_aviation_weather.decoder import format_clouds

    return format_clouds(data.clouds, data.cavok)


if __name__ == "__main__":
    unittest.main()
