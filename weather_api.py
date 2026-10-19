"""Async client for free aviation weather data.

Primary source: NOAA Aviation Weather Center Data API
    https://aviationweather.gov/api/data/
    Free, no API key required.

Fallback source: VATSIM METAR API (METAR only)
    https://metar.vatsim.net/
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import aiohttp

from .airports import IATA_TO_ICAO

DEFAULT_API_BASE = "https://aviationweather.gov/api/data"
VATSIM_METAR_URL = "https://metar.vatsim.net/{icao}?format=json"
USER_AGENT = (
    "AstrBot-AviationWeather/1.0 "
    "(+https://aviationweather.gov/data/api/; free aviation weather plugin)"
)


class WeatherError(Exception):
    """Base class for plugin weather errors."""


class AirportNotFound(WeatherError):
    """The airport has no METAR/TAF in the selected source."""


class WeatherAPIError(WeatherError):
    """The upstream API could not be reached or returned invalid data."""


@dataclass
class WeatherConfig:
    api_base: str = DEFAULT_API_BASE
    timeout: float = 15.0
    proxy: str = ""
    fallback_vatsim: bool = True
    cache_ttl: int = 60


@dataclass
class WeatherReport:
    icao: str
    metar: dict[str, Any] | None = None
    taf: dict[str, Any] | None = None
    station: dict[str, Any] | None = None
    source: str = "NOAA Aviation Weather Center"
    warnings: list[str] = field(default_factory=list)
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def normalize_airport_code(value: str) -> str:
    """Normalize an ICAO/IATA code and resolve common IATA codes.

    Args:
        value: User supplied airport code, e.g. ``ZBAA`` or ``pek``.

    Returns:
        A four-letter ICAO code.

    Raises:
        ValueError: If the value is empty or not a supported code.
    """

    code = re.sub(r"[^A-Za-z0-9]", "", (value or "").strip()).upper()
    if len(code) == 4 and code.isalpha():
        return code
    if len(code) == 3 and code.isalpha():
        icao = IATA_TO_ICAO.get(code)
        if icao:
            return icao
        raise ValueError(
            f"未收录 3 位 IATA 代码「{code}」。请改用 4 位 ICAO 代码，例如 ZBAA、ZSPD、KJFK。"
        )
    raise ValueError(
        "机场代码格式不正确。请使用 4 位 ICAO 代码（如 ZBAA）"
        "或常见 3 位 IATA 代码（如 PEK）。"
    )


class WeatherClient:
    """Fetch METAR, TAF and station metadata for one airport."""

    def __init__(self, config: WeatherConfig | None = None) -> None:
        self.config = config or WeatherConfig()
        self._cache: dict[str, tuple[float, WeatherReport]] = {}

    async def fetch(self, code: str) -> WeatherReport:
        icao = normalize_airport_code(code)

        cached = self._get_cached(icao)
        if cached is not None:
            return cached

        cfg = self.config
        timeout_seconds = min(60.0, max(3.0, float(cfg.timeout)))
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
        }

        async with aiohttp.ClientSession(
            timeout=timeout,
            headers=headers,
            trust_env=False,
        ) as session:
            metar_result, taf_result, station_result = await asyncio.gather(
                self._get_json(session, "metar", icao),
                self._get_json(session, "taf", icao),
                self._get_json(session, "stationinfo", icao),
                return_exceptions=True,
            )

            warnings: list[str] = []
            metar = _first_dict(metar_result)
            taf = _first_dict(taf_result)
            station = _first_dict(station_result)
            source = "NOAA Aviation Weather Center"

            metar_error = _as_exception(metar_result)
            taf_error = _as_exception(taf_result)
            station_error = _as_exception(station_result)

            if taf_error is not None:
                warnings.append(f"TAF 获取失败：{_short_error(taf_error)}")
            if station_error is not None:
                warnings.append(f"机场信息获取失败：{_short_error(station_error)}")

            if metar is None:
                raw_fallback = None
                if cfg.fallback_vatsim:
                    try:
                        raw_fallback = await self._fetch_vatsim_metar(session, icao)
                    except Exception as exc:  # noqa: BLE001 - fallback must not hide main error
                        warnings.append(f"VATSIM 备用源不可用：{_short_error(exc)}")

                if raw_fallback:
                    metar = {
                        "icaoId": icao,
                        "rawOb": raw_fallback,
                        "source": "VATSIM",
                    }
                    source = "VATSIM METAR（备用源）"
                    if taf is not None:
                        source += " + NOAA TAF"
                        warnings.append(
                            "NOAA 的 METAR 不可用，已切换至 VATSIM 备用 METAR；"
                            "TAF 仍来自 NOAA。"
                        )
                    else:
                        warnings.append(
                            "NOAA 的 METAR 不可用，已切换至 VATSIM 备用源；"
                            "该源通常不提供 TAF 与机场元数据。"
                        )
                elif metar_error is not None and taf is None:
                    raise WeatherAPIError(
                        f"无法获取 {icao} 的 METAR：{_short_error(metar_error)}"
                    )
                elif metar_error is not None:
                    warnings.append(
                        f"METAR 获取失败，仅显示 TAF：{_short_error(metar_error)}"
                    )
                elif taf is None:
                    raise AirportNotFound(
                        f"未找到 {icao} 的 METAR/TAF 报文。"
                        "该机场可能没有航空气象观测，或代码有误。"
                    )

            if metar is None and taf is None:
                raise AirportNotFound(
                    f"未找到 {icao} 的 METAR/TAF 报文。"
                    "该机场可能没有航空气象观测，或代码有误。"
                )

            report = WeatherReport(
                icao=icao,
                metar=metar,
                taf=taf,
                station=station,
                source=source,
                warnings=warnings,
            )
            self._put_cached(icao, report)
            return report

    async def _get_json(
        self,
        session: aiohttp.ClientSession,
        endpoint: str,
        icao: str,
    ) -> Any:
        base = (self.config.api_base or DEFAULT_API_BASE).strip().rstrip("/")
        url = f"{base}/{endpoint}?ids={quote(icao)}&format=json"
        last_error: Exception | None = None

        for attempt in range(2):
            try:
                async with session.get(
                    url, proxy=self.config.proxy or None
                ) as response:
                    if response.status == 204:
                        return []
                    if response.status == 404:
                        return []
                    if response.status >= 500:
                        raise _RetryableStatusError(f"上游接口 HTTP {response.status}")
                    if response.status != 200:
                        text = await response.text()
                        raise WeatherAPIError(
                            f"上游接口 HTTP {response.status}"
                            + (f"：{text[:120]}" if text else "")
                        )

                    text = await response.text()
                    if not text.strip():
                        return []
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError as exc:
                        raise _RetryableDataError(
                            "上游接口返回的不是有效 JSON"
                        ) from exc
            except (_RetryableStatusError, _RetryableDataError) as exc:
                last_error = exc
            except WeatherAPIError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError) as exc:
                last_error = exc
            except Exception as exc:  # noqa: BLE001 - normalize transport errors
                last_error = exc

            if attempt == 0:
                await asyncio.sleep(0.45)

        raise WeatherAPIError(str(last_error) if last_error else "未知网络错误")

    async def _fetch_vatsim_metar(
        self,
        session: aiohttp.ClientSession,
        icao: str,
    ) -> str | None:
        url = VATSIM_METAR_URL.format(icao=quote(icao))
        async with session.get(url, proxy=self.config.proxy or None) as response:
            if response.status == 404 or response.status == 204:
                return None
            if response.status != 200:
                raise WeatherAPIError(f"VATSIM HTTP {response.status}")

            text = (await response.text()).strip()
            if not text:
                return None

            raw: str | None = None
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                payload = None

            if isinstance(payload, list) and payload:
                first = payload[0]
                if isinstance(first, dict):
                    raw = str(first.get("metar") or "").strip() or None
                elif isinstance(first, str):
                    raw = first.strip() or None
            elif isinstance(payload, dict):
                raw = str(payload.get("metar") or "").strip() or None
            elif payload is None:
                raw = text

            if not raw:
                return None
            if not raw.upper().startswith(("METAR ", "SPECI ")):
                raw = f"METAR {icao} {raw}"
            return raw.rstrip("=").strip()

    def _get_cached(self, icao: str) -> WeatherReport | None:
        ttl = max(0, int(self.config.cache_ttl))
        if ttl <= 0:
            return None
        item = self._cache.get(icao)
        if not item:
            return None
        created, report = item
        if time.monotonic() - created > ttl:
            self._cache.pop(icao, None)
            return None
        return report

    def _put_cached(self, icao: str, report: WeatherReport) -> None:
        ttl = max(0, int(self.config.cache_ttl))
        if ttl <= 0:
            return
        if len(self._cache) > 128:
            self._cache.clear()
        self._cache[icao] = (time.monotonic(), report)


class _RetryableStatusError(WeatherAPIError):
    pass


class _RetryableDataError(WeatherAPIError):
    pass


def _first_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                return item
        return None
    if isinstance(value, dict):
        return value
    return None


def _as_exception(value: Any) -> Exception | None:
    if isinstance(value, BaseException):
        return value
    return None


def _short_error(exc: Exception) -> str:
    text = str(exc).strip().replace("\n", " ")
    return text[:180] if text else exc.__class__.__name__
