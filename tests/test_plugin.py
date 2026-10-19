from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

PLUGIN_PARENT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN_PARENT))

ASTRBOT_AVAILABLE = True
ASTRBOT_IMPORT_ERROR = ""
try:
    from astrbot.core.star.star_handler import star_handlers_registry

    from astrbot_plugin_aviation_weather.main import (
        USAGE,
        AviationWeatherPlugin,
    )
    from astrbot_plugin_aviation_weather.weather_api import WeatherReport
except Exception as exc:  # pragma: no cover - only runs without AstrBot installed
    ASTRBOT_AVAILABLE = False
    ASTRBOT_IMPORT_ERROR = str(exc)
    star_handlers_registry = None  # type: ignore[assignment]
    USAGE = ""
    AviationWeatherPlugin = None  # type: ignore[assignment,misc]
    WeatherReport = None  # type: ignore[assignment,misc]


class FakeResult:
    def __init__(self, text: str) -> None:
        self.text = text
        self.t2i = None
        self.markdown = None

    def use_t2i(self, value):
        self.t2i = value
        return self

    def use_markdown(self, value):
        self.markdown = value
        return self


class FakeEvent:
    def __init__(self) -> None:
        self.results: list[FakeResult] = []
        self.message_str = "/天气 ZBAA"
        self.is_at_or_wake_command = True
        self.call_llm = False
        self._has_send_oper = False
        self.stopped = False
        self._result = None

    def plain_result(self, text: str) -> FakeResult:
        result = FakeResult(text)
        self.results.append(result)
        # Simulate AstrBot's RespondStage calling event.send() for the yielded
        # result. The plugin is expected to reset this before the default LLM
        # stage runs.
        self._has_send_oper = True
        self._result = result
        return result

    def clear_result(self) -> None:
        self._result = None

    def stop_event(self) -> None:
        self.stopped = True


@unittest.skipUnless(
    ASTRBOT_AVAILABLE,
    f"AstrBot is not importable: {ASTRBOT_IMPORT_ERROR}",
)
class PluginTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.plugin = AviationWeatherPlugin(context=None, config={})

    def test_command_registration(self):
        module = AviationWeatherPlugin.__module__
        handler = next(
            item
            for item in star_handlers_registry
            if item.handler_module_path == module
            and item.handler_name == "weather_command"
        )
        command_filter = next(
            item for item in handler.event_filters if hasattr(item, "command_name")
        )
        self.assertEqual(command_filter.command_name, "天气")
        self.assertIn("机场天气", command_filter.alias)
        self.assertIn("airport", command_filter.handler_params)
        self.assertEqual(command_filter.handler_params["airport"], "")

    async def test_no_argument_usage_and_stop(self):
        event = FakeEvent()
        async for _ in self.plugin.weather_command(event, ""):
            pass
        self.assertTrue(event.stopped)
        self.assertEqual(len(event.results), 1)
        self.assertEqual(event.results[0].text, USAGE)
        self.assertFalse(event.results[0].t2i)
        self.assertFalse(event.results[0].markdown)

    async def test_invalid_code_is_handled_without_network(self):
        event = FakeEvent()
        async for _ in self.plugin.weather_command(event, "ZZZZZ"):
            pass
        self.assertTrue(event.stopped)
        self.assertEqual(len(event.results), 1)
        self.assertIn("机场代码格式不正确", event.results[0].text)

    async def test_unknown_iata_is_handled_without_network(self):
        event = FakeEvent()
        async for _ in self.plugin.weather_command(event, "XYZ"):
            pass
        self.assertTrue(event.stopped)
        self.assertIn("未收录 3 位 IATA", event.results[0].text)

    async def test_raw_report_then_normal_llm_trigger(self):
        raw_metar = "METAR ZBAA 190330Z 17002MPS CAVOK 16/03 Q1020 NOSIG"
        raw_taf = "TAF ZBAA 182107Z 1900/2006 36004MPS CAVOK"
        expected_raw = f"{raw_metar}\n{raw_taf}"
        self.plugin.client.fetch = AsyncMock(
            return_value=WeatherReport(
                icao="ZBAA",
                metar={"rawOb": raw_metar},
                taf={"rawTAF": raw_taf},
            )
        )

        event = FakeEvent()
        items = [item async for item in self.plugin.weather_command(event, "PEK")]

        self.assertEqual(len(items), 1)
        self.assertEqual(len(event.results), 1)
        self.assertEqual(event.results[0].text, expected_raw)
        self.assertNotIn("【", event.results[0].text)

        # The handler must leave the event in the same state as a normal user
        # message so AstrBot's default LLM stage runs exactly once afterwards.
        self.assertEqual(event.message_str, expected_raw)
        self.assertTrue(event.is_at_or_wake_command)
        self.assertFalse(event.call_llm)
        self.assertFalse(event._has_send_oper)
        self.assertIsNone(event._result)
        self.assertFalse(event.stopped)

    async def test_empty_raw_report_stops_without_llm(self):
        self.plugin.client.fetch = AsyncMock(
            return_value=WeatherReport(icao="ZBAA", metar={}, taf={})
        )
        event = FakeEvent()
        async for _ in self.plugin.weather_command(event, "ZBAA"):
            pass
        self.assertTrue(event.stopped)
        self.assertIn("未获取到", event.results[0].text)
        self.assertEqual(event.message_str, "/天气 ZBAA")


if __name__ == "__main__":
    unittest.main()
