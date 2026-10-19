"""AstrBot plugin: send raw METAR/TAF, then trigger a normal LLM reply to it."""

from __future__ import annotations

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from .formatter import format_raw_reports
from .weather_api import (
    AirportNotFound,
    WeatherAPIError,
    WeatherClient,
    WeatherConfig,
)

USAGE = (
    "✈️ 航空气象查询\n"
    "用法：/天气 <机场代码>\n"
    "示例：/天气 ZBAA　/天气 PEK　/天气 KJFK\n"
    "支持 4 位 ICAO 代码和常见 3 位 IATA 代码。\n"
    "先直接回复 METAR/TAF 原文，再把原文作为消息触发一次正常 LLM 回复。"
)


class AviationWeatherPlugin(Star):
    """查询机场 METAR/TAF：先发原文，再让模型像正常聊天一样回复它。"""

    def __init__(
        self,
        context: Context,
        config: AstrBotConfig | dict | None = None,
    ) -> None:
        super().__init__(context)
        if not hasattr(self, "logger"):
            # Older AstrBot releases did not expose Star.logger.
            self.logger = logger
        self.config = config or {}

        self.client = WeatherClient(
            WeatherConfig(
                api_base=str(
                    self.config.get("api_base")
                    or "https://aviationweather.gov/api/data"
                ).strip(),
                timeout=self._as_float(self.config.get("timeout"), 15.0),
                proxy=str(self.config.get("proxy") or "").strip(),
                fallback_vatsim=self._as_bool(self.config.get("fallback_vatsim"), True),
                cache_ttl=self._as_int(self.config.get("cache_ttl"), 60),
            )
        )

    @filter.command(
        "天气",
        alias={"机场天气", "航空气象", "metar", "METAR", "taf", "TAF"},
    )
    async def weather_command(
        self,
        event: AstrMessageEvent,
        airport: str = "",
    ):
        """查询机场航空气象：先回复原文，再触发一次正常 LLM 回复。"""

        if not airport.strip():
            yield self._text_result(event, USAGE)
            event.stop_event()
            return

        try:
            report = await self.client.fetch(airport)
            raw_report = format_raw_reports(report)
            if not raw_report:
                yield self._text_result(
                    event,
                    "❌ 未获取到该机场的 METAR/TAF 报文原文，请稍后重试。",
                )
                event.stop_event()
                return

            # 1. Directly send only the original report text.
            yield self._text_result(event, raw_report)

            # 2. Turn the just-sent report into the current message and let
            # AstrBot's normal LLM stage reply to it. The raw reply above set
            # _has_send_oper, so reset it and clear the already-sent result;
            # otherwise the default LLM stage is skipped.
            event.message_str = raw_report
            event.is_at_or_wake_command = True
            event._has_send_oper = False
            event.clear_result()
        except ValueError as exc:
            yield self._text_result(event, f"❌ {exc}")
            event.stop_event()
        except AirportNotFound as exc:
            yield self._text_result(event, f"❌ {exc}")
            event.stop_event()
        except WeatherAPIError as exc:
            yield self._text_result(
                event,
                "⚠️ 获取航空气象数据失败。\n"
                f"原因：{exc}\n"
                "请稍后重试；若持续失败，请检查插件配置中的 api_base / proxy / timeout。",
            )
            event.stop_event()
        except Exception:
            self.logger.exception("Unexpected error while handling /天气")
            yield self._text_result(
                event,
                "⚠️ 插件处理航空气象请求时发生内部错误，详情已写入 AstrBot 日志。",
            )
            event.stop_event()

    @staticmethod
    def _text_result(event: AstrMessageEvent, text: str):
        result = event.plain_result(text)
        # Keep the raw aviation report copyable as text, not a T2I image.
        result.use_t2i(False)
        result.use_markdown(False)
        return result

    @staticmethod
    def _as_bool(value, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            text = value.strip().lower()
            if text in {"1", "true", "yes", "on", "启用", "开启"}:
                return True
            if text in {"0", "false", "no", "off", "禁用", "关闭"}:
                return False
        if value is None:
            return default
        return bool(value)

    @staticmethod
    def _as_int(value, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _as_float(value, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
