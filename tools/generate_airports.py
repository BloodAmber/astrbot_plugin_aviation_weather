"""Regenerate airports.py from the public-domain OurAirports dataset.

Usage:
    python tools/generate_airports.py

The generated mapping is intentionally limited to:
  * all scheduled-service airports in China with an ICAO and IATA code;
  * large scheduled-service airports elsewhere.

It is only a convenience for users who type an IATA code. ICAO codes always
work without this mapping.
"""

from __future__ import annotations

import csv
import io
import json
import re
import urllib.request
from pathlib import Path

DATA_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
TARGET = Path(__file__).resolve().parents[1] / "airports.py"

AIRPORT_NAME_ZH = {
    "ZBAA": "北京首都国际机场",
    "ZBAD": "北京大兴国际机场",
    "ZSPD": "上海浦东国际机场",
    "ZSSS": "上海虹桥国际机场",
    "ZGGG": "广州白云国际机场",
    "ZGSZ": "深圳宝安国际机场",
    "ZUUU": "成都双流国际机场",
    "ZUTF": "成都天府国际机场",
    "ZUCK": "重庆江北国际机场",
    "ZSHC": "杭州萧山国际机场",
    "ZSNJ": "南京禄口国际机场",
    "ZSWX": "无锡硕放国际机场",
    "ZSQD": "青岛胶东国际机场",
    "ZSJN": "济南遥墙国际机场",
    "ZBTJ": "天津滨海国际机场",
    "ZBHH": "呼和浩特白塔国际机场",
    "ZBOW": "包头东河国际机场",
    "ZBLA": "呼伦贝尔海拉尔机场",
    "ZYTX": "沈阳桃仙国际机场",
    "ZYTL": "大连周水子国际机场",
    "ZYCC": "长春龙嘉国际机场",
    "ZYHB": "哈尔滨太平国际机场",
    "ZHHH": "武汉天河国际机场",
    "ZGHA": "长沙黄花国际机场",
    "ZGKL": "桂林两江国际机场",
    "ZGNN": "南宁吴圩国际机场",
    "ZJHK": "海口美兰国际机场",
    "ZJSY": "三亚凤凰国际机场",
    "ZPPP": "昆明长水国际机场",
    "ZUGY": "贵阳龙洞堡国际机场",
    "ZULS": "拉萨贡嘎国际机场",
    "ZWWW": "乌鲁木齐地窝堡国际机场",
    "ZLXY": "西安咸阳国际机场",
    "ZLLL": "兰州中川国际机场",
    "ZLIC": "银川河东国际机场",
    "ZSCN": "南昌昌北国际机场",
    "ZSFZ": "福州长乐国际机场",
    "ZSXM": "厦门高崎国际机场",
    "ZSWZ": "温州龙湾国际机场",
    "ZSNB": "宁波栎社国际机场",
    "ZBSJ": "石家庄正定国际机场",
    "ZBYN": "太原武宿国际机场",
    "ZUGU": "广元盘龙机场",
    "ZPHS": "普洱思茅机场",
    "ZPLJ": "丽江三义国际机场",
    "ZPMS": "德宏芒市国际机场",
    "ZPSM": "西双版纳嘎洒国际机场",
    "ZUDX": "达州金垭机场",
    "ZUGH": "广汉机场",
    "KJFK": "纽约肯尼迪国际机场",
    "KLAX": "洛杉矶国际机场",
    "KSFO": "旧金山国际机场",
    "KORD": "芝加哥奥黑尔国际机场",
    "KATL": "亚特兰大哈茨菲尔德-杰克逊国际机场",
    "KDFW": "达拉斯-沃思堡国际机场",
    "KSEA": "西雅图-塔科马国际机场",
    "KEWR": "纽瓦克自由国际机场",
    "KBOS": "波士顿洛根国际机场",
    "KIAD": "华盛顿杜勒斯国际机场",
    "CYYZ": "多伦多皮尔逊国际机场",
    "CYVR": "温哥华国际机场",
    "EGLL": "伦敦希思罗机场",
    "EGKK": "伦敦盖特威克机场",
    "LFPG": "巴黎戴高乐机场",
    "EDDF": "法兰克福机场",
    "EDDM": "慕尼黑机场",
    "EHAM": "阿姆斯特丹史基浦机场",
    "LSZH": "苏黎世机场",
    "LOWW": "维也纳国际机场",
    "LEMD": "马德里巴拉哈斯机场",
    "LIRF": "罗马菲乌米奇诺机场",
    "RJTT": "东京羽田机场",
    "RJAA": "东京成田国际机场",
    "RJBB": "大阪关西国际机场",
    "RKSI": "首尔仁川国际机场",
    "RKSS": "首尔金浦国际机场",
    "VHHH": "香港国际机场",
    "WSSS": "新加坡樟宜机场",
    "VTBS": "曼谷素万那普机场",
    "WMKK": "吉隆坡国际机场",
    "WIII": "雅加达苏加诺-哈达国际机场",
    "RPLL": "马尼拉尼诺伊·阿基诺国际机场",
    "VVTS": "胡志明市新山一国际机场",
    "OMDB": "迪拜国际机场",
    "OMAA": "阿布扎比国际机场",
    "OTHH": "多哈哈马德国际机场",
    "VIDP": "德里英迪拉·甘地国际机场",
    "VABB": "孟买贾特拉帕蒂·希瓦吉国际机场",
    "YSSY": "悉尼金斯福德·史密斯机场",
    "YMML": "墨尔本机场",
    "YBBN": "布里斯班机场",
    "NZAA": "奥克兰机场",
    "SBGR": "圣保罗瓜鲁柳斯国际机场",
    "SAEZ": "布宜诺斯艾利斯埃塞萨国际机场",
    "FAOR": "约翰内斯堡奥利弗·坦博国际机场",
    "FACT": "开普敦国际机场",
    "UUEE": "莫斯科谢列梅捷沃国际机场",
    "ULLI": "圣彼得堡普尔科沃机场",
    "EPWA": "华沙肖邦机场",
    "LKPR": "布拉格瓦茨拉夫·哈维尔机场",
    "ESSA": "斯德哥尔摩阿兰达机场",
    "ENGM": "奥斯陆加勒穆恩机场",
    "EKCH": "哥本哈根凯斯楚普机场",
    "EFHK": "赫尔辛基万塔机场",
    "BIKF": "凯夫拉维克国际机场",
}


def main() -> None:
    request = urllib.request.Request(DATA_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=90) as response:
        rows = list(csv.DictReader(io.StringIO(response.read().decode("utf-8"))))

    mapping: dict[str, str] = {}
    for row in rows:
        if row.get("scheduled_service") != "yes":
            continue
        icao = (row.get("icao_code") or row.get("ident") or "").upper()
        iata = (row.get("iata_code") or "").upper()
        if not re.fullmatch(r"[A-Z]{4}", icao) or not re.fullmatch(r"[A-Z]{3}", iata):
            continue
        country = row.get("iso_country") or ""
        if country == "CN" or row.get("type") == "large_airport":
            mapping.setdefault(icao, iata)

    # Remove stale Chinese names whose airport is not in the generated data.
    names = {key: value for key, value in AIRPORT_NAME_ZH.items() if key in mapping}

    pairs = sorted(mapping.items())
    lines: list[str] = [
        '"""ICAO/IATA convenience mapping.',
        "",
        "Generated from the public-domain OurAirports dataset:",
        "https://davidmegginson.github.io/ourairports-data/airports.csv",
        "",
        "Only common IATA codes are included. Any valid four-letter ICAO code",
        "works without this table.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "IATA_TO_ICAO: dict[str, str] = {",
    ]

    for index in range(0, len(pairs), 4):
        block = pairs[index : index + 4]
        text = " ".join(f'"{iata}": "{icao}",' for icao, iata in block)
        lines.append("    " + text)
    lines.append("}")
    lines.append("")
    lines.append(
        "ICAO_TO_IATA: dict[str, str] = {icao: iata for iata, icao in IATA_TO_ICAO.items()}"
    )
    lines.append("")
    lines.append("AIRPORT_NAME_ZH: dict[str, str] = {")
    for icao, name in sorted(names.items()):
        lines.append(f'    "{icao}": {json.dumps(name, ensure_ascii=False)},')
    lines.append("}")
    lines.append("")

    TARGET.write_text("\n".join(lines), encoding="utf-8")
    print(
        f"Wrote {len(mapping)} IATA mappings and {len(names)} Chinese names to {TARGET}"
    )


if __name__ == "__main__":
    main()
