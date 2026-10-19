"""Decode METAR/TAF JSON and raw reports into structured, readable data."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .airports import ICAO_TO_IATA

UTC = timezone.utc
MILES_TO_KM = 1.609344
KT_TO_MPS = 0.514444
MPS_TO_KT = 1.943844
MPS_TO_KMH = 3.6
INHG_TO_HPA = 33.8639

COMPASS_8 = ("北", "东北", "东", "东南", "南", "西南", "西", "西北")
COMPASS_16 = (
    "北",
    "北东北",
    "东北",
    "东东北",
    "东",
    "东东南",
    "东南",
    "南东南",
    "南",
    "南西南",
    "西南",
    "西西南",
    "西",
    "西西北",
    "西北",
    "北西北",
)
BEAUFORT_LABELS = (
    "无风",
    "软风",
    "轻风",
    "微风",
    "和风",
    "清劲风",
    "强风",
    "疾风",
    "大风",
    "烈风",
    "狂风",
    "暴风",
    "飓风",
)
BEAUFORT_LIMITS = (0.3, 1.6, 3.4, 5.5, 8.0, 10.8, 13.9, 17.2, 20.8, 24.5, 28.5, 32.7)

COVER_LABELS = {
    "FEW": "少云",
    "SCT": "疏云",
    "BKN": "多云",
    "OVC": "阴天",
    "VV": "垂直能见度",
    "SKC": "碧空",
    "CLR": "无云",
    "NSC": "无重要云",
    "NCD": "无云",
}
COVER_OKTAS = {
    "FEW": "1–2 成",
    "SCT": "3–4 成",
    "BKN": "5–7 成",
    "OVC": "8 成",
}

DESCRIPTOR_LABELS = {
    "MI": "浅",
    "PR": "部分",
    "BC": "散片",
    "DR": "低吹",
    "BL": "高吹",
    "SH": "阵",
    "TS": "雷暴",
    "FZ": "冻",
    "SQ": "飑",
    "FC": "漏斗云",
    "SS": "沙暴",
    "DS": "尘暴",
}
PHENOMENA_LABELS = {
    "DZ": "毛毛雨",
    "RA": "雨",
    "SN": "雪",
    "SG": "米雪",
    "IC": "冰晶",
    "PL": "冰粒",
    "GR": "冰雹",
    "GS": "小冰雹",
    "UP": "未知降水",
    "BR": "轻雾",
    "FG": "雾",
    "FU": "烟",
    "VA": "火山灰",
    "DU": "浮尘",
    "SA": "沙",
    "HZ": "霾",
    "PY": "扬尘",
    "PO": "尘卷风",
    "SQ": "飑",
    "FC": "漏斗云",
    "SS": "沙暴",
    "DS": "尘暴",
}
WEATHER_TOKEN_RE = re.compile(
    r"^(?P<intensity>[+-])?(?P<vicinity>VC)?"
    r"(?P<descriptor>MI|PR|BC|DR|BL|SH|TS|FZ|SQ|FC|SS|DS)?"
    r"(?P<phenomena>(?:DZ|RA|SN|SG|IC|PL|GR|GS|UP|BR|FG|FU|VA|DU|SA|HZ|PY|PO|SQ|FC|SS|DS)*)$"
)
WIND_RE = re.compile(
    r"^(?P<dir>\d{3}|VRB)(?P<speed>\d{2,3})(?:G(?P<gust>\d{2,3}))?"
    r"(?P<unit>KT|MPS|KMH)$"
)
VARIABLE_WIND_RE = re.compile(r"^(?P<from>\d{3})V(?P<to>\d{3})$")
CLOUD_RE = re.compile(
    r"^(?P<cover>FEW|SCT|BKN|OVC|VV)(?P<base>\d{3}|///)(?P<type>CB|TCU|///)?$"
)
TEMP_RE = re.compile(r"^(?P<temp>M?\d{2})/(?P<dew>M?\d{2}|//)$")
RVR_RE = re.compile(r"^R(?P<runway>\d{2}[LCR]?)/(?P<rvr>[PM]?\d{4})(?P<trend>[UDN])?$")
TREND_RE = re.compile(r"^(NOSIG|BECMG|TEMPO|INTER|FM\d{6}|PROB\d{2})$")
TIME_RE = re.compile(r"^(\d{2})(\d{2})(\d{2})Z$")
TAF_VALID_RE = re.compile(r"^(\d{2})(\d{2})/(\d{2})(\d{2})$")


@dataclass
class Wind:
    direction: int | None = None
    variable: bool = False
    speed: float = 0.0
    unit: str = "KT"
    gust: float | None = None
    var_from: int | None = None
    var_to: int | None = None
    raw: str = ""

    @property
    def speed_kt(self) -> float:
        if self.unit == "MPS":
            return self.speed * MPS_TO_KT
        if self.unit == "KMH":
            return self.speed / 1.852
        return self.speed

    @property
    def speed_mps(self) -> float:
        if self.unit == "MPS":
            return self.speed
        if self.unit == "KMH":
            return self.speed / 3.6
        return self.speed * KT_TO_MPS

    @property
    def speed_kmh(self) -> float:
        return self.speed_mps * MPS_TO_KMH

    @property
    def gust_mps(self) -> float | None:
        if self.gust is None:
            return None
        if self.unit == "MPS":
            return self.gust
        if self.unit == "KMH":
            return self.gust / 3.6
        return self.gust * KT_TO_MPS

    @property
    def beaufort(self) -> int:
        speed = self.speed_mps
        for index, limit in enumerate(BEAUFORT_LIMITS):
            if speed < limit:
                return index
        return 12


@dataclass
class CloudLayer:
    cover: str
    base_ft: int | None = None
    cloud_type: str = ""
    raw: str = ""


@dataclass
class MetarData:
    raw: str = ""
    report_type: str = "METAR"
    station: str = ""
    observed_at: datetime | None = None
    wind: Wind | None = None
    visibility_text: str = ""
    visibility_km: float | None = None
    cavok: bool = False
    weather: list[str] = field(default_factory=list)
    weather_raw: list[str] = field(default_factory=list)
    clouds: list[CloudLayer] = field(default_factory=list)
    temp_c: float | None = None
    dew_c: float | None = None
    pressure_hpa: float | None = None
    flight_category: str = ""
    trend_raw: str = ""
    remarks: str = ""
    rvr: list[str] = field(default_factory=list)
    auto: bool = False
    corrected: bool = False
    nil: bool = False
    unparsed: list[str] = field(default_factory=list)


@dataclass
class TafGroup:
    change: str = ""
    probability: int | None = None
    time_from: datetime | None = None
    time_to: datetime | None = None
    time_bec: datetime | None = None
    wind: Wind | None = None
    visibility_text: str = ""
    visibility_km: float | None = None
    cavok: bool = False
    weather: list[str] = field(default_factory=list)
    weather_raw: list[str] = field(default_factory=list)
    clouds: list[CloudLayer] = field(default_factory=list)
    vert_vis_ft: int | None = None
    altim_hpa: float | None = None
    wind_shear: str = ""
    temps: list[dict[str, Any]] = field(default_factory=list)
    raw: str = ""


@dataclass
class TafData:
    raw: str = ""
    issue_time: datetime | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    groups: list[TafGroup] = field(default_factory=list)
    temp_extremes: list[dict[str, Any]] = field(default_factory=list)
    remarks: str = ""


@dataclass
class AirportInfo:
    icao: str
    iata: str = ""
    name: str = ""
    country: str = ""
    state: str = ""
    lat: float | None = None
    lon: float | None = None
    elevation_m: float | None = None
    wmo: str = ""
    site_types: list[str] = field(default_factory=list)


def build_metar(payload: dict[str, Any]) -> MetarData:
    """Build a :class:`MetarData` from the NOAA METAR JSON object."""

    raw = str(payload.get("rawOb") or "").strip().rstrip("=")
    data = parse_metar_raw(raw)
    data.raw = raw
    if not data.station:
        data.station = str(payload.get("icaoId") or "").strip().upper()

    # obsTime is the exact observation epoch; reportTime may be rounded to the
    # nearest hour by the upstream API.
    json_time = epoch_to_datetime(payload.get("obsTime"))
    if json_time is None:
        json_time = parse_datetime(payload.get("reportTime"))
    if json_time is not None:
        data.observed_at = json_time

    wind = wind_from_json(payload)
    if data.wind is None and wind is not None:
        data.wind = wind

    if not data.visibility_text:
        text, km = visibility_from_json(payload.get("visib"))
        data.visibility_text = text
        data.visibility_km = km

    if not data.clouds:
        data.clouds = clouds_from_json(payload.get("clouds"))

    if data.temp_c is None:
        data.temp_c = to_float(payload.get("temp"))
    if data.dew_c is None:
        data.dew_c = to_float(payload.get("dewp"))

    if data.pressure_hpa is None:
        data.pressure_hpa = (
            to_float(payload.get("altim"))
            or to_float(payload.get("slp"))
            or _pressure_from_slp(payload.get("slp"))
        )

    if not data.weather_raw:
        wx = payload.get("wxString")
        if isinstance(wx, str) and wx.strip():
            data.weather_raw = wx.replace(",", " ").split()
            data.weather = decode_weather_tokens(data.weather_raw)

    category = str(payload.get("fltCat") or "").strip().upper()
    if category not in {"VFR", "MVFR", "IFR", "LIFR"}:
        category = derive_flight_category(data.visibility_km, data.clouds)
    data.flight_category = category

    if not data.visibility_text:
        data.visibility_text = "无数据"
    return data


def build_taf(payload: dict[str, Any]) -> TafData:
    """Build a :class:`TafData` from the NOAA TAF JSON object."""

    raw = str(payload.get("rawTAF") or payload.get("rawTaf") or "").strip().rstrip("=")
    data = TafData(raw=raw, remarks=str(payload.get("remarks") or "").strip())

    data.issue_time = parse_datetime(payload.get("issueTime"))
    data.valid_from = epoch_to_datetime(payload.get("validTimeFrom"))
    data.valid_to = epoch_to_datetime(payload.get("validTimeTo"))

    if data.issue_time is None:
        data.issue_time = _parse_taf_issue_time(raw)
    reference = data.issue_time or datetime.now(UTC)
    if data.valid_from is None or data.valid_to is None:
        start, end = _parse_taf_validity(raw, reference)
        data.valid_from = data.valid_from or start
        data.valid_to = data.valid_to or end

    for item in payload.get("fcsts") or []:
        if not isinstance(item, dict):
            continue
        group = TafGroup(
            change=str(item.get("fcstChange") or "").strip().upper(),
            probability=_to_int(item.get("probability")),
            time_from=epoch_to_datetime(item.get("timeFrom")),
            time_to=epoch_to_datetime(item.get("timeTo")),
            time_bec=epoch_to_datetime(item.get("timeBec")),
            wind=wind_from_json(item),
            vert_vis_ft=_to_int(item.get("vertVis")),
            altim_hpa=to_float(item.get("altim")),
            temps=[temp for temp in (item.get("temp") or []) if isinstance(temp, dict)],
            raw=str(item.get("notDecoded") or "").strip(),
        )
        group.visibility_text, group.visibility_km = visibility_from_json(
            item.get("visib")
        )
        wx = item.get("wxString")
        if isinstance(wx, str) and wx.strip():
            group.weather_raw = wx.replace(",", " ").split()
            group.weather = decode_weather_tokens(group.weather_raw)
        group.clouds = clouds_from_json(item.get("clouds"))
        group.wind_shear = _format_wind_shear(item)
        data.groups.append(group)

    _apply_raw_taf_base(data)
    data.temp_extremes = _extract_taf_temperatures(raw, data, reference)
    return data


def build_airport(
    metar_payload: dict[str, Any] | None,
    station_payload: dict[str, Any] | None,
    icao: str,
) -> AirportInfo:
    """Merge station metadata with METAR fallback fields."""

    metar = metar_payload or {}
    station = station_payload or {}
    info = AirportInfo(icao=icao.upper())

    info.iata = str(station.get("iataId") or "").strip().upper()
    if not info.iata:
        info.iata = ICAO_TO_IATA.get(info.icao, "")

    info.name = str(station.get("site") or "").strip()
    if not info.name:
        name = str(metar.get("name") or "").strip()
        info.name = re.sub(r",\s*[A-Z0-9]{1,3},\s*[A-Z]{2}$", "", name).strip()

    info.country = str(station.get("country") or "").strip().upper()
    info.state = str(station.get("state") or "").strip()
    info.wmo = str(station.get("wmoId") or "").strip()
    info.lat = to_float(station.get("lat"))
    info.lon = to_float(station.get("lon"))
    info.elevation_m = to_float(station.get("elev"))
    if info.lat is None:
        info.lat = to_float(metar.get("lat"))
    if info.lon is None:
        info.lon = to_float(metar.get("lon"))
    if info.elevation_m is None:
        info.elevation_m = to_float(metar.get("elev"))

    site_types = station.get("siteType")
    if isinstance(site_types, list):
        info.site_types = [str(item) for item in site_types if item]
    return info


def parse_metar_raw(raw: str) -> MetarData:
    """Parse the non-remark part of a raw METAR/SPECI report."""

    data = MetarData(raw=raw)
    tokens = (raw or "").replace("=", " ").split()
    if not tokens:
        return data

    index = 0
    if tokens[index].upper() in {"METAR", "SPECI"}:
        data.report_type = tokens[index].upper()
        index += 1

    # Flags can occur before or after the station identifier.
    while index < len(tokens) and tokens[index].upper() in {
        "AUTO",
        "COR",
        "NIL",
        "BBB",
        "CCA",
    }:
        flag = tokens[index].upper()
        if flag == "AUTO":
            data.auto = True
        elif flag in {"COR", "CCA"}:
            data.corrected = True
        elif flag == "NIL":
            data.nil = True
        index += 1

    if index < len(tokens) and re.fullmatch(r"[A-Z][A-Z0-9]{3}", tokens[index]):
        data.station = tokens[index]
        index += 1

    if index < len(tokens) and TIME_RE.fullmatch(tokens[index]):
        data.observed_at = parse_observation_time(tokens[index], datetime.now(UTC))
        index += 1

    while index < len(tokens) and tokens[index].upper() in {"AUTO", "COR", "NIL"}:
        flag = tokens[index].upper()
        if flag == "AUTO":
            data.auto = True
        elif flag == "COR":
            data.corrected = True
        elif flag == "NIL":
            data.nil = True
        index += 1

    while index < len(tokens):
        token = tokens[index]
        upper = token.upper()

        if upper == "RMK":
            data.remarks = " ".join(tokens[index + 1 :]).strip()
            break

        if upper in {"AUTO", "COR", "NIL"}:
            if upper == "AUTO":
                data.auto = True
            elif upper == "COR":
                data.corrected = True
            else:
                data.nil = True
            index += 1
            continue

        if upper == "CAVOK":
            data.cavok = True
            data.visibility_text = "CAVOK（≥10 km，无重要云/天气）"
            data.visibility_km = 10.0
            index += 1
            continue

        wind = parse_wind_token(upper)
        if wind is not None:
            data.wind = wind
            index += 1
            continue

        variable = VARIABLE_WIND_RE.fullmatch(upper)
        if variable and data.wind is not None:
            data.wind.var_from = int(variable.group("from"))
            data.wind.var_to = int(variable.group("to"))
            index += 1
            continue

        visibility, consumed = parse_visibility_at(tokens, index)
        if visibility is not None:
            data.visibility_text, data.visibility_km = visibility
            index += consumed
            continue

        rvr = RVR_RE.fullmatch(upper)
        if rvr:
            data.rvr.append(
                f"跑道 {rvr.group('runway')}：RVR {int(rvr.group('rvr').lstrip('PM'))} m"
                + _rvr_trend_text(rvr.group("trend"))
            )
            index += 1
            continue

        cloud = parse_cloud_token(upper)
        if cloud is not None:
            data.clouds.append(cloud)
            index += 1
            continue

        temp_match = TEMP_RE.fullmatch(upper)
        if temp_match:
            data.temp_c = _signed_temp(temp_match.group("temp"))
            dew = temp_match.group("dew")
            if dew != "//":
                data.dew_c = _signed_temp(dew)
            index += 1
            continue

        pressure = _parse_pressure_token(upper)
        if pressure is not None:
            data.pressure_hpa = pressure
            index += 1
            continue

        if upper == "NSW":
            data.weather_raw.append(upper)
            data.weather.append("无重要天气（NSW）")
            index += 1
            continue

        weather = decode_weather_token(upper)
        if weather is not None:
            data.weather_raw.append(upper)
            data.weather.append(weather)
            index += 1
            continue

        if TREND_RE.fullmatch(upper):
            data.trend_raw = " ".join(tokens[index:]).strip()
            break

        if upper in {"//", "////", "$"}:
            index += 1
            continue

        data.unparsed.append(token)
        index += 1

    if data.observed_at is None:
        for token in tokens[:6]:
            if TIME_RE.fullmatch(token):
                data.observed_at = parse_observation_time(token, datetime.now(UTC))
                break

    return data


def parse_wind_token(token: str) -> Wind | None:
    match = WIND_RE.fullmatch(token)
    if not match:
        return None
    direction_raw = match.group("dir")
    speed = float(match.group("speed"))
    gust = float(match.group("gust")) if match.group("gust") else None
    unit = match.group("unit")
    variable = direction_raw == "VRB"
    direction = None if variable else int(direction_raw)
    return Wind(
        direction=direction,
        variable=variable,
        speed=speed,
        unit=unit,
        gust=gust,
        raw=token,
    )


def wind_from_json(payload: dict[str, Any]) -> Wind | None:
    wdir = payload.get("wdir")
    wspd = payload.get("wspd")
    wgst = payload.get("wgst")
    if wdir is None and wspd is None:
        return None
    variable = isinstance(wdir, str) and wdir.strip().upper() == "VRB"
    direction = None if variable or wdir is None else _to_int(wdir)
    speed = to_float(wspd) or 0.0
    gust = to_float(wgst)
    return Wind(
        direction=direction,
        variable=variable,
        speed=speed,
        unit="KT",
        gust=gust,
    )


def parse_visibility_at(
    tokens: list[str],
    index: int,
) -> tuple[tuple[str, float | None] | None, int]:
    token = tokens[index].upper()
    if token in {"////", "//"}:
        return ("能见度不明", None), 1

    meters_match = re.fullmatch(r"(\d{4})(?:NDV)?", token)
    if meters_match:
        meters = int(meters_match.group(1))
        if meters == 9999:
            return ("≥10 km", 10.0), 1
        if meters == 0:
            return ("<50 m", 0.05), 1
        return (_meters_text(meters), meters / 1000.0), 1

    simple_sm = re.fullmatch(r"(P)?(\d+)SM", token)
    if simple_sm:
        prefix, value = simple_sm.groups()
        miles = float(value)
        if prefix == "P":
            return (
                f"> {miles * MILES_TO_KM:.1f} km（>{value} SM）",
                miles * MILES_TO_KM,
            ), 1
        return (f"{miles * MILES_TO_KM:.1f} km（{value} SM）", miles * MILES_TO_KM), 1

    fraction_sm = re.fullmatch(r"(M)?(\d+)/(\d+)SM", token)
    if fraction_sm:
        prefix, numerator, denominator = fraction_sm.groups()
        miles = float(numerator) / float(denominator)
        if prefix == "M":
            return (
                f"< {miles * MILES_TO_KM:.1f} km（<{numerator}/{denominator} SM）",
                miles * MILES_TO_KM,
            ), 1
        return (
            f"{miles * MILES_TO_KM:.1f} km（{numerator}/{denominator} SM）",
            miles * MILES_TO_KM,
        ), 1

    if (
        re.fullmatch(r"\d+", token)
        and index + 1 < len(tokens)
        and re.fullmatch(r"\d+/\d+SM", tokens[index + 1].upper())
    ):
        whole = float(token)
        frac = tokens[index + 1].upper()[:-2]
        numerator, denominator = frac.split("/")
        miles = whole + float(numerator) / float(denominator)
        return (
            f"{miles * MILES_TO_KM:.1f} km（{int(whole)} {numerator}/{denominator} SM）",
            miles * MILES_TO_KM,
        ), 2

    return None, 1


def visibility_from_json(value: Any) -> tuple[str, float | None]:
    if value is None:
        return ("", None)
    if isinstance(value, (int, float)):
        miles = float(value)
        return (f"{miles * MILES_TO_KM:.1f} km（{miles:g} SM）", miles * MILES_TO_KM)
    text = str(value).strip()
    if not text:
        return ("", None)
    if text.endswith("+"):
        try:
            miles = float(text[:-1])
        except ValueError:
            return (text, None)
        km = miles * MILES_TO_KM
        return (f"≥{km:.1f} km（>{text[:-1]} SM）", km)
    try:
        miles = float(text)
    except ValueError:
        return (text, None)
    return (f"{miles * MILES_TO_KM:.1f} km（{text} SM）", miles * MILES_TO_KM)


def parse_cloud_token(token: str) -> CloudLayer | None:
    upper = token.upper()
    if upper in {"SKC", "CLR", "NSC", "NCD"}:
        return CloudLayer(cover=upper, raw=upper)
    match = CLOUD_RE.fullmatch(upper)
    if not match:
        return None
    base_raw = match.group("base")
    base_ft = None if base_raw == "///" else int(base_raw) * 100
    cloud_type = match.group("type") or ""
    if cloud_type == "///":
        cloud_type = ""
    return CloudLayer(
        cover=match.group("cover"),
        base_ft=base_ft,
        cloud_type=cloud_type,
        raw=upper,
    )


def clouds_from_json(value: Any) -> list[CloudLayer]:
    result: list[CloudLayer] = []
    if not isinstance(value, list):
        return result
    for item in value:
        if not isinstance(item, dict):
            continue
        cover = str(item.get("cover") or "").strip().upper()
        if not cover:
            continue
        base = _to_int(item.get("base"))
        result.append(
            CloudLayer(
                cover=cover,
                base_ft=base,
                cloud_type=str(item.get("type") or "").strip().upper(),
            )
        )
    return result


def decode_weather_token(token: str) -> str | None:
    upper = token.upper().strip()
    if not upper:
        return None
    if upper == "NSW":
        return "无重要天气（NSW）"
    if upper in {"//", "////"}:
        return "天气不明"

    match = WEATHER_TOKEN_RE.fullmatch(upper)
    if not match:
        return None
    intensity = match.group("intensity")
    vicinity = match.group("vicinity")
    descriptor = match.group("descriptor")
    phenomena = match.group("phenomena")
    if not descriptor and not phenomena:
        return None

    phenomena_labels = [
        PHENOMENA_LABELS.get(phenomena[index : index + 2], phenomena[index : index + 2])
        for index in range(0, len(phenomena), 2)
    ]
    if set(phenomena_labels) == {"雨", "雪"}:
        phenomena_text = "雨夹雪"
    else:
        phenomena_text = "和".join(phenomena_labels)

    if descriptor == "TS":
        if phenomena_text in {"雨", "雪"}:
            body = f"雷{phenomena_text}"
        elif phenomena_text:
            body = f"雷暴伴{phenomena_text}"
        else:
            body = "雷暴"
    elif descriptor == "SH":
        body = f"阵{phenomena_text}" if phenomena_text else "阵雨"
    elif descriptor == "FZ":
        body = f"冻{phenomena_text}" if phenomena_text else "冻"
    elif descriptor:
        body = DESCRIPTOR_LABELS.get(descriptor, descriptor) + phenomena_text
    else:
        body = phenomena_text

    if not body:
        return None

    if intensity == "+":
        prefix = "强" if descriptor in {"TS", "SH", "FZ"} else "大"
    elif intensity == "-":
        prefix = "小"
    else:
        prefix = ""
    if vicinity:
        prefix = "附近" + prefix
    return prefix + body


def decode_weather_tokens(tokens: list[str]) -> list[str]:
    result: list[str] = []
    for token in tokens:
        decoded = decode_weather_token(str(token))
        if decoded:
            result.append(decoded)
    return result


def derive_flight_category(
    visibility_km: float | None,
    clouds: list[CloudLayer],
) -> str:
    ceiling_ft: int | None = None
    for layer in clouds:
        if layer.cover in {"BKN", "OVC", "VV"} and layer.base_ft is not None:
            ceiling_ft = (
                layer.base_ft if ceiling_ft is None else min(ceiling_ft, layer.base_ft)
            )

    visibility_sm = visibility_km / MILES_TO_KM if visibility_km is not None else None
    if (visibility_sm is not None and visibility_sm < 1) or (
        ceiling_ft is not None and ceiling_ft < 500
    ):
        return "LIFR"
    if (visibility_sm is not None and visibility_sm < 3) or (
        ceiling_ft is not None and ceiling_ft < 1000
    ):
        return "IFR"
    if (visibility_sm is not None and visibility_sm <= 5) or (
        ceiling_ft is not None and ceiling_ft <= 3000
    ):
        return "MVFR"
    if visibility_sm is None and ceiling_ft is None:
        return ""
    return "VFR"


def parse_observation_time(token: str, reference: datetime) -> datetime | None:
    match = TIME_RE.fullmatch(token.upper())
    if not match:
        return None
    day, hour, minute = (int(part) for part in match.groups())
    return _closest_month_datetime(reference, day, hour, minute)


def parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def epoch_to_datetime(value: Any) -> datetime | None:
    number = _to_int(value)
    if number is None:
        return None
    try:
        return datetime.fromtimestamp(number, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def relative_humidity(temp_c: float | None, dew_c: float | None) -> int | None:
    if temp_c is None or dew_c is None:
        return None
    try:
        numerator = math.exp(
            (17.625 * dew_c) / (243.04 + dew_c) - (17.625 * temp_c) / (243.04 + temp_c)
        )
    except (OverflowError, ValueError, ZeroDivisionError):
        return None
    return max(0, min(100, round(100 * numerator)))


def compass_text(degrees: int | None, points: int = 8) -> str:
    if degrees is None:
        return "不定"
    table = COMPASS_8 if points == 8 else COMPASS_16
    index = int((degrees % 360) / (360 / len(table)) + 0.5) % len(table)
    return table[index]


def format_wind(wind: Wind | None) -> str:
    if wind is None:
        return "无数据"
    if wind.speed_mps < 0.3:
        return "静风（0 m/s）"

    if wind.variable or wind.direction is None:
        direction = "风向不定"
    else:
        direction = (
            f"{wind.direction:03d}°（{compass_text(wind.direction)}，"
            f"{_english_compass(wind.direction)}）"
        )

    speed_mps = wind.speed_mps
    speed_kt = wind.speed_kt
    speed_kmh = wind.speed_kmh
    beaufort = wind.beaufort
    label = BEAUFORT_LABELS[beaufort]
    text = (
        f"{direction} {speed_mps:.1f} m/s"
        f"（{speed_kt:.0f} kt / {speed_kmh:.0f} km/h，{beaufort} 级{label}）"
    )

    if wind.gust_mps is not None:
        text += f"，阵风 {wind.gust_mps:.1f} m/s（{wind.gust_mps * MPS_TO_KT:.0f} kt）"
    if wind.var_from is not None and wind.var_to is not None:
        text += f"；风向在 {wind.var_from:03d}°–{wind.var_to:03d}° 之间变化"
    return text


def format_visibility(data: MetarData) -> str:
    if data.cavok:
        return "CAVOK（≥10 km，5000 ft 以下无重要云，无 CB/TCU，无重要天气）"
    if data.visibility_text:
        return data.visibility_text
    return "无数据"


def format_clouds(clouds: list[CloudLayer], cavok: bool = False) -> str:
    if cavok or any(layer.cover == "CAVOK" for layer in clouds):
        return "无重要云（CAVOK）"
    if not clouds:
        return "无数据"
    if all(layer.cover in {"SKC", "CLR", "NSC", "NCD"} for layer in clouds):
        return "无重要云（" + "、".join(layer.cover for layer in clouds) + "）"

    parts: list[str] = []
    for layer in clouds:
        if layer.cover in {"SKC", "CLR", "NSC", "NCD"}:
            parts.append(COVER_LABELS.get(layer.cover, layer.cover))
            continue
        label = COVER_LABELS.get(layer.cover, layer.cover)
        if layer.base_ft is None:
            parts.append(f"{label}（{layer.cover}），云底高度不明")
            continue
        base_m = layer.base_ft * 0.3048
        text = f"{label}（{layer.cover}）{layer.base_ft} ft / 约 {base_m:.0f} m"
        if layer.cover in COVER_OKTAS:
            text += f"（{COVER_OKTAS[layer.cover]}）"
        if layer.cloud_type:
            text += f"，{_cloud_type_text(layer.cloud_type)}"
        parts.append(text)
    return "；".join(parts)


def format_weather(weather: list[str], raw: list[str]) -> str:
    if weather:
        return "、".join(weather)
    if raw:
        return "、".join(raw)
    return "无重要天气"


def format_temperature(data: MetarData) -> str:
    if data.temp_c is None and data.dew_c is None:
        return "无数据"
    parts: list[str] = []
    if data.temp_c is not None and data.dew_c is not None:
        parts.append(f"{data.temp_c:.0f}°C / {data.dew_c:.0f}°C")
    elif data.temp_c is not None:
        parts.append(f"{data.temp_c:.0f}°C")
    elif data.dew_c is not None:
        parts.append(f"露点 {data.dew_c:.0f}°C")
    rh = relative_humidity(data.temp_c, data.dew_c)
    if rh is not None:
        parts.append(f"相对湿度 {rh}%")
    if data.temp_c is not None and data.dew_c is not None:
        parts.append(f"温差 {abs(data.temp_c - data.dew_c):.0f}°C")
    return " ｜ ".join(parts)


def format_pressure(hpa: float | None) -> str:
    if hpa is None:
        return "无数据"
    inHg = hpa / INHG_TO_HPA
    return f"修正海压 {hpa:.0f} hPa（{inHg:.2f} inHg）"


def format_flight_category(category: str) -> str:
    labels = {
        "VFR": "VFR 目视飞行，天气良好（能见度 >5 SM，云底 >3000 ft）",
        "MVFR": "MVFR 边缘目视（能见度 3–5 SM 或云底 1000–3000 ft）",
        "IFR": "IFR 仪表飞行（能见度 1–3 SM 或云底 500–1000 ft）",
        "LIFR": "LIFR 低仪表（能见度 <1 SM 或云底 <500 ft）",
    }
    return labels.get(category, category or "无数据")


def format_trend(raw: str) -> str:
    if not raw:
        return ""
    if raw.upper().startswith("NOSIG"):
        return "NOSIG（未来 2 小时无显著变化）"
    return raw


def format_taf_group(group: TafGroup, tz: timezone) -> str:
    title = taf_change_label(group)
    change = group.change.upper()
    if change == "FM" and group.time_from:
        title += f" 从 {format_local_time(group.time_from, tz, date=True)} 起"
    elif change == "BECMG" and group.time_from and group.time_bec:
        start = format_local_time(group.time_from, tz, date=True)
        end = format_local_time(group.time_bec, tz, date=True)
        if start == end:
            title += f" 约 {end} 起逐渐转变"
        else:
            title += f" {start}–{end} 逐渐转变"
    elif group.time_from and group.time_to:
        title += (
            f" {format_local_time(group.time_from, tz, date=True)}"
            f" → {format_local_time(group.time_to, tz, date=True)}"
        )
    elif group.time_bec:
        title += f" 约 {format_local_time(group.time_bec, tz, date=True)} 起"

    details: list[str] = []
    if group.wind is not None:
        details.append(f"风 {format_wind(group.wind)}")
    if group.visibility_text:
        details.append(f"能见度 {group.visibility_text}")
    if group.weather:
        details.append(f"天气 {format_weather(group.weather, group.weather_raw)}")
    elif group.weather_raw:
        details.append(f"天气 {format_weather(group.weather, group.weather_raw)}")
    if group.clouds or group.cavok:
        details.append(f"云 {format_clouds(group.clouds, group.cavok)}")
    if group.vert_vis_ft is not None:
        details.append(
            f"垂直能见度 {group.vert_vis_ft} ft / 约 {group.vert_vis_ft * 0.3048:.0f} m"
        )
    if group.altim_hpa is not None:
        details.append(format_pressure(group.altim_hpa))
    if group.wind_shear:
        details.append(group.wind_shear)
    if group.raw:
        details.append(f"原始组 {group.raw}")

    return f"• {title}：" + ("；".join(details) if details else "无明确变化")


def taf_change_label(group: TafGroup) -> str:
    probability = f"{group.probability}% 概率" if group.probability else ""
    change = group.change.upper()
    if change == "FM":
        base = "后续预报"
    elif change == "BECMG":
        base = "变化"
    elif change == "TEMPO":
        base = "短时"
    elif change == "INTER":
        base = "间歇"
    elif change == "PROB":
        return f"（{probability or '一定概率'}预报）"
    elif probability and not change:
        base = "概率预报"
    else:
        base = "初始预报"
    if probability and probability not in base:
        base = f"{probability}{base}"
    return f"（{base}）"


def format_temp_extremes(items: list[dict[str, Any]], tz: timezone) -> str:
    if not items:
        return ""
    grouped: dict[str, list[str]] = {"MAX": [], "MIN": []}
    for item in items:
        kind = str(item.get("type") or "")
        temp = item.get("temp")
        when = item.get("time")
        if kind not in grouped or temp is None:
            continue
        text = f"{temp:g}°C"
        if isinstance(when, datetime):
            text += f"（{format_local_time(when, tz, date=True)}）"
        grouped[kind].append(text)

    parts: list[str] = []
    if grouped["MAX"]:
        parts.append("最高 " + "、".join(grouped["MAX"]))
    if grouped["MIN"]:
        parts.append("最低 " + "、".join(grouped["MIN"]))
    return "；".join(parts)


def format_local_time(
    value: datetime | None,
    tz: timezone,
    *,
    date: bool = True,
) -> str:
    if value is None:
        return "未知"
    local = value.astimezone(tz)
    if date:
        return local.strftime("%m-%d %H:%M")
    return local.strftime("%H:%M")


def format_utc_time(value: datetime | None) -> str:
    if value is None:
        return "未知"
    return value.astimezone(UTC).strftime("%H:%M UTC")


def format_obs_time(value: datetime | None, tz: timezone) -> str:
    if value is None:
        return "未知"
    return (
        value.astimezone(tz).strftime("%Y-%m-%d %H:%M")
        + "（"
        + timezone_label(tz)
        + "）｜ "
        + value.astimezone(UTC).strftime("%H:%M UTC")
    )


def timezone_label(tz: timezone) -> str:
    offset = datetime.now(tz).utcoffset()
    if offset is None:
        return "本地时间"
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    hours, minutes = divmod(abs(total_minutes), 60)
    if minutes:
        return f"UTC{sign}{hours:02d}:{minutes:02d}"
    return f"UTC{sign}{hours}"


def coordinate_text(lat: float | None, lon: float | None) -> str:
    if lat is None or lon is None:
        return ""
    lat_text = f"{abs(lat):.4f}°{'N' if lat >= 0 else 'S'}"
    lon_text = f"{abs(lon):.4f}°{'E' if lon >= 0 else 'W'}"
    return f"{lat_text}, {lon_text}"


def to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _signed_temp(value: str) -> float:
    upper = value.upper()
    if upper.startswith("M"):
        return -float(upper[1:])
    return float(upper)


def _meters_text(meters: int) -> str:
    if meters >= 1000:
        return f"{meters / 1000:.1f} km"
    return f"{meters} m"


def _parse_pressure_token(token: str) -> float | None:
    if token.startswith("Q") and token[1:].isdigit():
        value = int(token[1:])
        return float(value) / 10 if value >= 10000 else float(value)
    if token.startswith("A") and token[1:].isdigit() and len(token) == 5:
        return float(token[1:]) / 100 * INHG_TO_HPA
    if token.startswith("SLP") and token[3:].isdigit():
        return _pressure_from_slp(token[3:])
    return None


def _pressure_from_slp(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.startswith("SLP"):
        text = text[3:]
    if not text.isdigit():
        return None
    number = int(text)
    if number >= 500:
        return 900 + number / 10
    return 1000 + number / 10


def _rvr_trend_text(trend: str | None) -> str:
    return {"U": "，上升趋势", "D": "，下降趋势", "N": "，无显著变化"}.get(
        trend or "", ""
    )


def _cloud_type_text(cloud_type: str) -> str:
    return {"CB": "积雨云", "TCU": "浓积云"}.get(cloud_type, cloud_type)


def _english_compass(degrees: int | None) -> str:
    if degrees is None:
        return "VRB"
    names = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
    return names[int((degrees % 360) / 45 + 0.5) % 8]


def _format_wind_shear(payload: dict[str, Any]) -> str:
    height = _to_int(payload.get("wshearHgt"))
    direction = payload.get("wshearDir")
    speed = to_float(payload.get("wshearSpd"))
    if height is None and direction is None and speed is None:
        return ""
    parts = ["风切变"]
    if height is not None:
        parts.append(f"高度 {height} ft")
    if direction is not None and speed is not None:
        parts.append(f"风向 {direction}° / 风速 {speed:.0f} kt")
    return "，".join(parts)


def _extract_taf_temperatures(
    raw: str,
    data: TafData,
    reference: datetime,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int | None]] = set()

    for match in re.finditer(r"\b(TX|TN)(M?\d{2})/(\d{2})(\d{2})Z\b", raw):
        kind, temp_text, day, hour = match.groups()
        when = _resolve_day_hour(reference, int(day), int(hour))
        temp = (
            float(temp_text[1:]) * -1 if temp_text.startswith("M") else float(temp_text)
        )
        key = (kind, int(temp), int(when.timestamp()) if when else None)
        if key in seen:
            continue
        seen.add(key)
        result.append(
            {
                "type": "MAX" if kind == "TX" else "MIN",
                "temp": temp,
                "time": when,
            }
        )

    if result:
        return result

    # Some TAFs (notably in the US) do not carry TX/TN groups. Fall back to
    # the temperature extremes decoded by the NOAA JSON response.
    for group in data.groups:
        for item in group.temps:
            when = epoch_to_datetime(item.get("validTime"))
            temp = to_float(item.get("surfaceTemp"))
            kind = str(item.get("maxOrMin") or "").strip().upper()
            if when is None or temp is None or kind not in {"MAX", "MIN"}:
                continue
            key = (kind, int(temp), int(when.timestamp()))
            if key in seen:
                continue
            seen.add(key)
            result.append(
                {
                    "type": kind,
                    "temp": temp,
                    "time": when,
                }
            )
    result.sort(key=lambda item: item.get("time") or reference)
    return result


def _apply_raw_taf_base(data: TafData) -> None:
    """Restore CAVOK/9999 details that the JSON decoder normalizes to "6+".

    NOAA's JSON normalises visibility to statute miles and may emit ``6+`` for
    both ``9999`` and ``CAVOK``. The raw TAF keeps the distinction, so apply it
    to the initial forecast group.
    """

    if not data.raw or not data.groups:
        return

    base_tokens: list[str] = []
    for token in data.raw.replace("=", " ").split():
        upper = token.upper()
        if upper in {"TAF", "AMD", "COR"}:
            continue
        if TREND_RE.fullmatch(upper) or upper.startswith("PROB"):
            break
        base_tokens.append(upper)

    group = data.groups[0]
    if "CAVOK" in base_tokens:
        group.cavok = True
        group.visibility_text = "CAVOK（≥10 km，无重要云/天气）"
        group.visibility_km = 10.0
        group.clouds = []
        return
    if "9999" in base_tokens and group.visibility_text.startswith("≥9.7"):
        group.visibility_text = "≥10 km"
        group.visibility_km = 10.0


def _parse_taf_issue_time(raw: str) -> datetime | None:
    tokens = raw.split()
    for index, token in enumerate(tokens[:6]):
        if TIME_RE.fullmatch(token.upper()):
            return parse_observation_time(token, datetime.now(UTC))
        if token.upper() in {"TAF", "AMD", "COR"}:
            continue
        if re.fullmatch(r"[A-Z][A-Z0-9]{3}", token) and index > 0:
            continue
    return None


def _parse_taf_validity(
    raw: str,
    reference: datetime,
) -> tuple[datetime | None, datetime | None]:
    for token in raw.split()[:8]:
        match = TAF_VALID_RE.fullmatch(token)
        if not match:
            continue
        day_from, hour_from, day_to, hour_to = (int(part) for part in match.groups())
        start = _resolve_day_hour(reference, day_from, hour_from)
        end = _resolve_day_hour(reference, day_to, hour_to, after=start)
        return start, end
    return None, None


def _closest_month_datetime(
    reference: datetime,
    day: int,
    hour: int,
    minute: int,
) -> datetime | None:
    candidates: list[datetime] = []
    for month_delta in (-1, 0, 1):
        year = reference.year
        month = reference.month + month_delta
        while month < 1:
            month += 12
            year -= 1
        while month > 12:
            month -= 12
            year += 1
        try:
            candidates.append(datetime(year, month, day, hour, minute, tzinfo=UTC))
        except ValueError:
            continue
    if not candidates:
        return None
    return min(candidates, key=lambda item: abs((item - reference).total_seconds()))


def _resolve_day_hour(
    reference: datetime,
    day: int,
    hour: int,
    *,
    after: datetime | None = None,
) -> datetime | None:
    candidates: list[datetime] = []
    for month_delta in (-1, 0, 1, 2):
        year = reference.year
        month = reference.month + month_delta
        while month < 1:
            month += 12
            year -= 1
        while month > 12:
            month -= 12
            year += 1
        try:
            candidate = datetime(year, month, day, hour, tzinfo=UTC)
        except ValueError:
            continue
        if after is not None and candidate <= after:
            continue
        candidates.append(candidate)
    if not candidates:
        return None
    if after is not None:
        return min(candidates)
    return min(candidates, key=lambda item: abs((item - reference).total_seconds()))
