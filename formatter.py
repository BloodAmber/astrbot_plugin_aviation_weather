"""Format raw or decoded aviation weather for chat replies."""

from __future__ import annotations

from datetime import datetime, timezone

from .airports import AIRPORT_NAME_ZH
from .decoder import (
    AirportInfo,
    MetarData,
    TafData,
    build_airport,
    build_metar,
    build_taf,
    coordinate_text,
    format_clouds,
    format_flight_category,
    format_local_time,
    format_obs_time,
    format_pressure,
    format_taf_group,
    format_temp_extremes,
    format_temperature,
    format_trend,
    format_visibility,
    format_weather,
    format_wind,
    timezone_label,
)
from .weather_api import WeatherReport


def format_raw_reports(report: WeatherReport) -> str:
    """Return only the original METAR/TAF text, one report per line.

    The direct reply intentionally contains no decoded fields, airport title,
    timestamps, source footer or disclaimer. Those are left to the model stage.
    """

    metar = report.metar or {}
    taf = report.taf or {}

    raw_metar = str(
        metar.get("rawOb") or metar.get("raw_text") or metar.get("raw") or ""
    ).strip()
    raw_taf = str(
        taf.get("rawTAF") or taf.get("rawTaf") or taf.get("raw") or ""
    ).strip()
    if not raw_taf:
        # Some NOAA responses (metar?taf=true) put the raw TAF inside the METAR
        # object. This is a harmless fallback when the dedicated TAF call failed.
        raw_taf = str(metar.get("rawTaf") or metar.get("rawTAF") or "").strip()

    parts = [item for item in (raw_metar, raw_taf) if item]
    return "\n".join(parts)


COUNTRY_NAMES = {
    "CN": "中国",
    "HK": "中国香港",
    "MO": "中国澳门",
    "TW": "中国台湾",
    "US": "美国",
    "CA": "加拿大",
    "GB": "英国",
    "JP": "日本",
    "KR": "韩国",
    "SG": "新加坡",
    "MY": "马来西亚",
    "TH": "泰国",
    "VN": "越南",
    "PH": "菲律宾",
    "ID": "印度尼西亚",
    "IN": "印度",
    "AU": "澳大利亚",
    "NZ": "新西兰",
    "AE": "阿联酋",
    "QA": "卡塔尔",
    "SA": "沙特阿拉伯",
    "TR": "土耳其",
    "DE": "德国",
    "FR": "法国",
    "NL": "荷兰",
    "BE": "比利时",
    "CH": "瑞士",
    "AT": "奥地利",
    "IT": "意大利",
    "ES": "西班牙",
    "PT": "葡萄牙",
    "GR": "希腊",
    "SE": "瑞典",
    "NO": "挪威",
    "DK": "丹麦",
    "FI": "芬兰",
    "IS": "冰岛",
    "IE": "爱尔兰",
    "PL": "波兰",
    "CZ": "捷克",
    "HU": "匈牙利",
    "RO": "罗马尼亚",
    "RU": "俄罗斯",
    "UA": "乌克兰",
    "BR": "巴西",
    "AR": "阿根廷",
    "CL": "智利",
    "ZA": "南非",
    "EG": "埃及",
    "IL": "以色列",
    "PK": "巴基斯坦",
    "BD": "孟加拉国",
    "LK": "斯里兰卡",
    "NP": "尼泊尔",
    "KZ": "哈萨克斯坦",
    "MN": "蒙古",
}


def format_report(
    report: WeatherReport,
    tz: timezone,
    *,
    show_raw: bool = True,
    show_remarks: bool = True,
) -> str:
    """Return a plain-text report suitable for QQ/Telegram/etc."""

    metar = build_metar(report.metar) if report.metar else None
    taf = build_taf(report.taf) if report.taf else None
    airport = build_airport(report.metar, report.station, report.icao)

    lines: list[str] = [f"✈️ 航空气象 · {report.icao}"]
    lines.append(_airport_title(airport, report.icao))

    meta = _airport_meta(airport)
    if meta:
        lines.append(" ｜ ".join(meta))

    if metar and metar.observed_at:
        lines.append(f"🕒 观测：{format_obs_time(metar.observed_at, tz)}")
    elif taf and taf.issue_time:
        lines.append(
            "🕒 TAF 发布："
            f"{format_local_time(taf.issue_time, tz, date=True)}"
            f"（{timezone_label(tz)}）"
        )

    if taf and taf.valid_from and taf.valid_to:
        lines.append(
            "📅 TAF 有效期："
            f"{format_local_time(taf.valid_from, tz, date=True)} → "
            f"{format_local_time(taf.valid_to, tz, date=True)}"
            f"（{timezone_label(tz)}）"
        )

    lines.append("")

    if metar is not None:
        lines.extend(_render_metar(metar, show_raw=show_raw, show_remarks=show_remarks))
    else:
        lines.append("【METAR 实况】")
        lines.append("暂无 METAR 实况报文，仅显示 TAF 预报。")

    if taf is not None:
        lines.append("")
        lines.extend(_render_taf(taf, tz, show_raw=show_raw, show_remarks=show_remarks))
    elif metar is not None:
        lines.append("")
        lines.append("【TAF 预报】")
        lines.append("暂无 TAF 预报（该机场可能不发布 TAF，或上游数据暂缺）。")

    warnings = [item for item in report.warnings if item]
    if warnings:
        lines.append("")
        lines.append("【提示】")
        for warning in warnings:
            lines.append(f"⚠️ {warning}")

    lines.append("")
    lines.extend(_render_source(report, tz))
    return "\n".join(lines).strip()


def _render_metar(
    metar: MetarData,
    *,
    show_raw: bool,
    show_remarks: bool,
) -> list[str]:
    lines = ["【METAR 实况】"]
    if show_raw and metar.raw:
        lines.append(metar.raw)

    flags: list[str] = []
    if metar.auto:
        flags.append("自动观测 AUTO")
    if metar.corrected:
        flags.append("更正报 COR")
    if metar.nil:
        flags.append("无数据 NIL")
    if flags:
        lines.append("🏷 " + "；".join(flags))

    if metar.nil:
        lines.append("该时段观测报文为 NIL，未包含气象要素。")
        return lines

    lines.append(f"🌬 风：{format_wind(metar.wind)}")
    lines.append(f"👁 能见度：{format_visibility(metar)}")
    lines.append(f"🌦 天气：{format_weather(metar.weather, metar.weather_raw)}")
    lines.append(f"☁️ 云：{format_clouds(metar.clouds, metar.cavok)}")
    lines.append(f"🌡 温度/露点：{format_temperature(metar)}")
    lines.append(f"🧭 气压：{format_pressure(metar.pressure_hpa)}")
    if metar.flight_category:
        lines.append(f"🚦 飞行条件：{format_flight_category(metar.flight_category)}")
    if metar.trend_raw:
        lines.append(f"📈 趋势：{format_trend(metar.trend_raw)}")
    if metar.rvr:
        lines.append("🛣 " + "；".join(metar.rvr))
    if show_remarks and metar.remarks:
        lines.append(f"📝 备注（原始）：{metar.remarks}")
    return lines


def _render_taf(
    taf: TafData,
    tz: timezone,
    *,
    show_raw: bool,
    show_remarks: bool,
) -> list[str]:
    lines = ["【TAF 预报】"]
    if show_raw and taf.raw:
        lines.append(taf.raw)

    if taf.valid_to is not None and taf.valid_to < datetime.now(timezone.utc):
        lines.append(
            "⚠️ 该 TAF 已过期（有效期结束于 "
            f"{format_local_time(taf.valid_to, tz, date=True)}）。"
        )

    if not taf.groups:
        lines.append("暂无可解析的分段预报，以下仅保留原始 TAF 报文。")
    else:
        for group in taf.groups:
            lines.append(format_taf_group(group, tz))
        lines.append("ℹ️ 未列出的要素表示维持前一阶段；TEMPO 表示短时波动。")

    temp_text = format_temp_extremes(taf.temp_extremes, tz)
    if temp_text:
        lines.append(f"🌡 温度极值：{temp_text}")

    if show_remarks and taf.remarks:
        lines.append(f"📝 备注（原始）：{taf.remarks}")
    return lines


def _render_source(report: WeatherReport, tz: timezone) -> list[str]:
    lines = ["【数据来源】"]
    if "VATSIM" in report.source and "NOAA" in report.source:
        lines.append(
            "METAR：VATSIM METAR API（备用源，metar.vatsim.net）· "
            "TAF：NOAA Aviation Weather Center（aviationweather.gov）"
        )
    elif "VATSIM" in report.source:
        lines.append("VATSIM METAR API（备用源，metar.vatsim.net）· 免费、无需 API Key")
    else:
        lines.append(
            "NOAA Aviation Weather Center（aviationweather.gov）· 免费、无需 API Key"
        )
    lines.append(
        "拉取时间："
        + format_local_time(report.fetched_at, tz, date=True)
        + f"（{timezone_label(tz)}）"
    )
    lines.append(
        "⚠️ 报文仅供信息参考，不替代官方飞行气象简报；飞行决策请以民航气象部门发布为准。"
    )
    return lines


def _airport_title(airport: AirportInfo, icao: str) -> str:
    name_zh = AIRPORT_NAME_ZH.get(icao)
    codes: list[str] = []
    if airport.iata:
        codes.append(airport.iata)
    codes.append(icao)
    suffix = f"（{' / '.join(codes)}）"

    if name_zh:
        return f"{name_zh}{suffix}"

    if airport.name:
        return f"{airport.name}{suffix}"
    return f"机场 {icao}"


def _airport_meta(airport: AirportInfo) -> list[str]:
    meta: list[str] = []
    country = COUNTRY_NAMES.get(airport.country, airport.country)
    if country:
        if airport.state and airport.country not in {"CN", "HK", "MO"}:
            meta.append(f"{country} · {airport.state}")
        else:
            meta.append(country)
    coordinates = coordinate_text(airport.lat, airport.lon)
    if coordinates:
        meta.append(coordinates)
    if airport.elevation_m is not None:
        meta.append(f"海拔 {airport.elevation_m:.0f} m")
    if airport.wmo:
        meta.append(f"WMO {airport.wmo}")
    if airport.site_types:
        meta.append("/".join(airport.site_types))
    return meta


def split_message(text: str, limit: int) -> list[str]:
    """Split a long message on line boundaries without breaking UTF-8."""

    if limit <= 0 or len(text) <= limit:
        return [text]

    # Reserve room for a "（1/2）\n" prefix so the final message stays within
    # the requested limit.
    body_limit = max(100, limit - 12)
    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(line) > body_limit:
            if current.strip():
                chunks.append(current.strip())
                current = ""
            for offset in range(0, len(line), body_limit):
                part = line[offset : offset + body_limit].strip()
                if part:
                    chunks.append(part)
            continue
        if len(current) + len(line) > body_limit:
            if current.strip():
                chunks.append(current.strip())
            current = line
        else:
            current += line
    if current.strip():
        chunks.append(current.strip())

    if len(chunks) <= 1:
        return chunks or [text]

    total = len(chunks)
    return [f"（{index}/{total}）\n{chunk}" for index, chunk in enumerate(chunks, 1)]
