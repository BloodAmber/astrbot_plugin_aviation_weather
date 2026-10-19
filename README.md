# 航空气象 METAR/TAF（AstrBot 插件）

GitHub：<https://github.com/BloodAmber/astrbot_plugin_aviation_weather>

收到 **`/天气 机场代码`** 时：

1. 插件先直接回复 **METAR/TAF 报文原文**；
2. 然后把这段原文当作当前消息，走 AstrBot **正常的 LLM 回复流程**，像普通聊天一样触发一次模型回复。

- 无需 API Key
- 支持 4 位 ICAO 代码（`ZBAA`、`ZSPD`、`KJFK`）和常见 3 位 IATA 代码（`PEK`、`PVG`、`JFK`）
- 第一条消息只有报文原文，不附加机场介绍、中文译码或来源说明
- 模型回复使用 AstrBot 当前提供商、人格、上下文和工具配置
- NOAA 不可用时自动回退到 VATSIM METAR（仅 METAR）

## 安装

把整个 `astrbot_plugin_aviation_weather` 目录放到 AstrBot 的插件目录：

```text
AstrBot/data/plugins/astrbot_plugin_aviation_weather/
```

然后在 AstrBot WebUI 的「插件管理」中重载/启用插件。插件依赖 `aiohttp`（AstrBot 通常已自带；如缺失，AstrBot 会根据 `requirements.txt` 安装）。

要求 AstrBot `>= 4.16, < 5`，并且已经配置可用的模型提供商。

## 使用

```text
/天气 ZBAA
/天气 PEK
/天气 KJFK
/天气 EGLL
```

别名：

```text
/机场天气 ZBAA
/航空气象 ZBAA
/metar ZBAA
/taf ZBAA
```

不带参数时返回用法说明。参数错误、机场无报文或 API 失败时只回复错误说明，不会触发模型。

## 回复效果

第一条消息是纯原文，例如：

```text
METAR ZBAA 190330Z 17002MPS 110V270 CAVOK 16/03 Q1020 NOSIG
TAF ZBAA 182107Z 1900/2006 36004MPS CAVOK TX18/1906Z TX13/2006Z TN06/1921Z
```

随后 AstrBot 会把这两行原文当成一条普通用户消息，触发一次正常 LLM 回复，例如：

```text
北京首都机场当前为 CAVOK，能见度 10 公里以上，地面风 170°、2 m/s……
```

模型回复的具体内容取决于你配置的模型、人格和上下文。

## 输出流程

```python
# 1. 只发原文
yield event.plain_result(raw_report)

# 2. 把刚发出的原文变成当前消息，并允许 AstrBot 的默认 LLM 阶段执行
event.message_str = raw_report
event.is_at_or_wake_command = True
event._has_send_oper = False
event.clear_result()

# 3. 命令处理器结束后，AstrBot 会像处理普通聊天一样调用一次 LLM
```

实现见 [main.py](main.py#L80-L90)，原文提取见 [formatter.py](formatter.py#L33-L55)。

关键点：

- 直发内容只有 `rawOb` / `rawTAF` 两段原文，不包含额外解读。
- 不调用 `event.request_llm` 构造特殊请求，而是复用 AstrBot 的默认 LLM 流程。
- `_has_send_oper` 是 AstrBot 内部标记；直发原文会把它设为 `True`，导致默认 LLM 阶段被跳过，所以这里复位为 `False`。
- 成功路径不调用 `event.stop_event()`；错误路径会调用，避免错误提示触发模型。

## 配置

在 AstrBot WebUI 的插件配置页修改，默认值适合大多数用户。

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `api_base` | string | `https://aviationweather.gov/api/data` | API 根地址；可改为同结构的反向代理地址 |
| `timeout` | int | `15` | 单次请求超时秒数，建议 10–30 |
| `proxy` | string | 空 | 可选 HTTP/HTTPS 代理，例如 `http://127.0.0.1:7890` |
| `fallback_vatsim` | bool | `true` | NOAA METAR 失败时回退到 VATSIM（仅 METAR） |
| `cache_ttl` | int | `60` | 成功结果缓存秒数，`0` 关闭 |

模型回复不再使用插件自定义提示词，而是完全走 AstrBot 的正常聊天流程。

## 数据源

### 主数据源：NOAA Aviation Weather Center

- 文档：<https://aviationweather.gov/data/api/>
- 免费、无需 API Key
- 使用三个端点：
  - `GET /api/data/metar?ids=ZBAA&format=json`
  - `GET /api/data/taf?ids=ZBAA&format=json`
  - `GET /api/data/stationinfo?ids=ZBAA&format=json`

METAR 通常每小时更新；TAF 通常每日 4 次。插件直接使用响应中的 `rawOb` / `rawTAF`。

### 备用源：VATSIM METAR

- <https://metar.vatsim.net/>
- 仅在 NOAA 的 METAR 不可用且 `fallback_vatsim` 开启时使用
- 通常只提供 METAR，没有 TAF 和机场元数据

### 机场代码映射

`airports.py` 中的 IATA→ICAO 映射由公共领域的 [OurAirports](https://davidmegginson.github.io/ourairports-data/airports.csv) 数据生成，包含中国所有有定期航班的机场以及全球大型机场。ICAO 代码不依赖该映射，任何有效的 4 位 ICAO 代码都可以直接查询。

如需更新映射：

```bash
python tools/generate_airports.py
```

## 实现结构

```text
astrbot_plugin_aviation_weather/
├── main.py              # 命令、原文直发、切换为普通 LLM 消息、错误处理
├── weather_api.py       # 异步 API 客户端、重试、缓存、VATSIM 回退
├── formatter.py         # 从 API 响应中提取纯原文
├── decoder.py           # METAR/TAF 解析工具（保留用于扩展与测试）
├── airports.py          # IATA→ICAO 映射与中文机场名
├── metadata.yaml
├── _conf_schema.json
├── requirements.txt
├── tests/
└── tools/generate_airports.py
```

## 测试

```bash
python -m unittest discover -s tests -v
```

测试覆盖：

- 原文提取只包含 `rawOb` / `rawTAF`
- 原文直发后，事件状态被切换为可触发普通 LLM 回复
- 无参数、错误代码、空报文时停止事件且不触发模型
- METAR/TAF 解析工具、IATA 代码解析、消息拆分

可选的实时 API 冒烟测试：

```bash
AVWX_LIVE=1 python -m unittest tests.test_live -v
```

## 已知限制

- 模型回复的质量取决于所配置的模型、人格和上下文；它可能不按固定格式回答。
- 因为复用了默认 LLM 流程，模型可能会看到当前会话的历史上下文，这是“像正常聊天”的一部分。
- NOAA 的 `stationinfo` 只接受 ICAO 代码；3 位 IATA 代码依赖本地映射表。未收录的 IATA 代码会提示改用 ICAO。
- 备用源 VATSIM 不提供 TAF；此时第一条消息只会有 METAR 原文。
- 报文是观测/预报原文，不构成飞行放行、签派或管制许可。实际飞行决策必须以官方气象简报和运行单位规定为准。
- 如果服务器访问 `aviationweather.gov` 不稳定，可在插件配置中设置 `proxy`，或把 `api_base` 指向自建反向代理。

## License

MIT
