# 候选抽取输入范围 A/B 对照（2026-11-19）

视频：DJI Neo 2｜First Use (Drone Only)；6 个字幕切片，4,078 字符。

## 设置

- A：现有候选输入，包含标题、摘要、邻文、当前切片与术语。
- B：只在候选抽取请求中删除 `title`、`summary_context`、`neighbor_context`；保留当前切片、证据类型、术语及修复反馈。
- 两组使用相同提示词、修复逻辑、实体审核、身份规范化、关系分类、关系审核和校验逻辑。实体审核仍使用原标题和邻文。
- 模型：`deepseek-v4.1-flash-economy`，温度 0，抽取/审核推理均为 `none`。基础代码版本：`video-graph-v2:deepseek-v4.1-flash-economy:f1e886a11627-none-none`。B 是临时请求变换，不是已部署的生产抽取版本。
- 两组均重新调用真实 API，无应用层旧响应复用；按切片交替 AB/BA 执行。两组输入来自同一份内存源材料。
- 计时范围是单片 `extract()` 加最终校验，不含主题评估、选样、图谱发布与数据库读回；不能直接与前一次 131.99 秒的完整构图总计时比较。
- 未修改生产抽取代码，也未写入 Neo4j。

## 汇总

| 指标 | A：完整上下文 | B：只看当前切片 |
|---|---:|---:|
| 通过校验的切片 | 6/6 | 6/6 |
| 首轮候选校验错误数 | 41 | 0 |
| 候选修复请求数 | 6 | 0 |
| 候选抽取请求数 | 12 | 6 |
| 候选抽取耗时 | 42.27 秒 | 15.80 秒 |
| 抽取及审核总耗时 | 116.86 秒 | 113.85 秒 |
| 全部 HTTP 请求数 | 29 | 28 |
| 模型报告总 tokens | 54,592 | 54,662 |
| 提示词缓存命中 tokens | 29,824 | 3,072 |
| 实体条目（按切片累计，含重复提及） | 34 | 42 |
| 实体身份数（按现有入库 identity 去重） | 25 | 36 |
| 关系输出（按切片累计） | 2 | 15 |

候选抽取耗时减少 **62.6%**；但抽取及审核整体只减少 **2.6%**（3.02 秒）。总 tokens 基本持平。

## 分阶段

| 阶段 | A 请求数 | B 请求数 | A 耗时（秒） | B 耗时（秒） |
|---|---:|---:|---:|---:|
| 实体候选抽取（含修复） | 12 | 6 | 42.27 | 15.80 |
| 实体审核 | 6 | 6 | 36.55 | 34.32 |
| 关系分类 | 10 | 12 | 34.82 | 52.38 |
| 关系审核 | 1 | 4 | 2.75 | 10.91 |

B 的实体候选范围更准确，但最终保留了更多当前正文中的实体，增加了关系分类和关系审核工作。候选阶段的省时大部分被后续开销抵消。

## 分切片

| 切片 | A 耗时（秒） | B 耗时（秒） | A/B 实体条目 | A/B 关系条目 |
|---|---:|---:|---:|---:|
| 1 | 28.21 | 44.47 | 9/14 | 0/9 |
| 2 | 20.13 | 19.07 | 6/6 | 2/3 |
| 3 | 24.61 | 20.59 | 9/9 | 0/0 |
| 4 | 11.57 | 12.50 | 2/4 | 0/3 |
| 5 | 18.79 | 10.26 | 6/6 | 0/0 |
| 6 | 13.55 | 6.96 | 2/3 | 0/0 |

## 输出差异判断

1. 修复触发得到消除：A 有 41 处首次原文提及错误，6/6 切片触发修复；B 首次校验错误和候选修复均为 0。A 第二轮部分切片仍有无法定位的实体，后续审核会继续拒绝它们。
2. 主体识别改善的例子：B 在切片 1 和 4 先抽正文 `aircraft`，再由原审核依据标题规范为 `DJI Neo 2`，避免直接把标题型号当成正文提及。切片 1 因此产生设备到屏幕、指示灯、按钮、存储和 USB-C 接口等关系。
3. 身份数量不能当作召回率：B 仍同时输出 `DJI Neo 2 gimbal protector` 和其他切片的 `gimbal protector`，以及带型号/不带型号的 `takeoff button`、`status indicator`，现有命名会产生不同身份。36 个去重身份包含这类差异，不代表 36 个独立正确对象。
4. 质量问题仍存在：A 将切片 2 的 `firmware` 命名为 `DJI Care Refresh firmware`，原文并不支持这种所属关系；B 对应输出为 `firmware update`/technology，其边界和分类也值得人工确认。B 新增 `DJI Fly applied_to aircraft battery level LEDs`，该引用描述连接操作与 LED 状态，不足以直接证明这一关系，应列为人工复核疑点。A/B 都有 `DJI Fly applied_to aircraft power button`，同样不能仅凭通过自动审核认定正确。
5. 因此，这次对照支持“候选抽取缩小输入范围可以减少越界和修复请求”，尚不支持“整体提速显著”或“实体/关系 F1 已提高”。当前 6 片没有人工金标准，不能计算准确率、召回率和 F1。

## 实体逐片对照

下表按原文提及对齐，列出规范名、类型和作用域。空白表示该组未保留该提及；不同规范名/作用域可能指同一对象，不能仅凭身份 ID 差异判断漏抽。

### 切片 1

| 原文提及 | A 规范实体 | B 规范实体 |
|---|---|---|
| aircraft | — | DJI Neo 2 / product / global |
| aircraft battery | — | DJI Neo 2 aircraft battery / component / video |
| battery | — | DJI Neo 2 aircraft battery / component / video |
| Bluetooth | — | Bluetooth / protocol / global |
| DJI Fly App | DJI Fly / software / global | DJI Fly / software / global |
| gimbal protector | gimbal protector / component / video | DJI Neo 2 gimbal protector / component / video |
| internal storage | internal storage / component / video | DJI Neo 2 internal storage / component / video |
| power button | power button / component / video | DJI Neo 2 power button / component / video |
| screen | screen / component / video | DJI Neo 2 screen / component / video |
| select button | select button / component / video | DJI Neo 2 select button / component / video |
| status indicator | status indicator / component / video | DJI Neo 2 status indicator / component / video |
| takeoff button | takeoff button / component / video | DJI Neo 2 takeoff button / component / video |
| USB-C port | USB-C port / component / video | DJI Neo 2 USB-C port / component / video |
| Wi-Fi | — | Wi-Fi / protocol / global |

### 切片 2

| 原文提及 | A 规范实体 | B 规范实体 |
|---|---|---|
| battery level LEDs | aircraft battery level LEDs / component / video | aircraft battery level LEDs / component / video |
| Connection Guide | Connection Guide / technology / global | Connection Guide / technology / video |
| DJI Care Refresh | DJI Care Refresh / product / global | DJI Care Refresh / product / global |
| DJI Fly | DJI Fly / software / global | DJI Fly / software / global |
| firmware | DJI Care Refresh firmware / software / video | — |
| firmware update | — | firmware update / technology / global |
| power button | aircraft power button / component / video | aircraft power button / component / video |

### 切片 3

| 原文提及 | A 规范实体 | B 规范实体 |
|---|---|---|
| DJI Fly app | DJI Fly / software / global | DJI Fly / software / global |
| Dronie | Dronie / technology / global | Dronie / technology / global |
| firmware | firmware / software / global | firmware / software / global |
| Follow | Follow / technology / global | Follow / technology / global |
| Intelligent Flight Battery | Intelligent Flight Battery / component / global | Intelligent Flight Battery / component / global |
| propeller guards | propeller guards / component / video | propeller guards / component / video |
| propellers | propellers / component / video | propellers / component / video |
| select button | select button / component / video | select button / component / video |
| SelfieShot | SelfieShot / technology / global | SelfieShot / technology / global |

### 切片 4

| 原文提及 | A 规范实体 | B 规范实体 |
|---|---|---|
| aircraft | — | DJI Neo 2 / product / global |
| gestures | — | gestures / technology / video |
| status indicator | status indicator / component / video | status indicator / component / video |
| takeoff button | takeoff button / component / video | takeoff button / component / video |

### 切片 5

| 原文提及 | A 规范实体 | B 规范实体 |
|---|---|---|
| follow | follow / technology / video | — |
| follow subject | — | follow subject / technology / video |
| gesture control | gesture control / technology / video | gesture control / technology / video |
| low battery warning | low battery warning / fault / video | low battery warning / fault / video |
| power button | power button / component / video | power button / component / video |
| propellers | propellers / component / video | propellers / component / video |
| status indicator | status indicator / component / video | status indicator / component / video |

### 切片 6

| 原文提及 | A 规范实体 | B 规范实体 |
|---|---|---|
| aircraft body | — | aircraft body / component / video |
| gimbal protector | gimbal protector / component / video | gimbal protector / component / video |
| sensors | sensors / component / video | sensors / component / video |

## 关系逐片对照

### 切片 1

| 组别 | 主体 | 谓词 | 客体 | 极性/范围 | 原文证据 |
|---|---|---|---|---|---|
| B | DJI Fly | applied_to | DJI Neo 2 | asserted / instruction | DJI Fly App is required to use the aircraft |
| B | DJI Neo 2 | contains | DJI Neo 2 gimbal protector | asserted / instruction | Press down to remove the gimbal protector |
| B | DJI Neo 2 | contains | DJI Neo 2 screen | asserted / actual | There is a screen and a status indicator on the front of the aircraft |
| B | DJI Neo 2 | contains | DJI Neo 2 status indicator | asserted / actual | There is a screen and a status indicator on the front of the aircraft |
| B | DJI Neo 2 | contains | DJI Neo 2 takeoff button | asserted / actual | The takeoff button and select button are located on the side |
| B | DJI Neo 2 | contains | DJI Neo 2 select button | asserted / actual | The takeoff button and select button are located on the side |
| B | DJI Neo 2 | contains | DJI Neo 2 power button | asserted / actual | The takeoff button and select button are located on the side Press the power button to activate the aircraft battery |
| B | DJI Neo 2 | contains | DJI Neo 2 internal storage | asserted / actual | The aircraft comes with an internal storage |
| B | DJI Neo 2 | contains | DJI Neo 2 USB-C port | asserted / actual | the USB-C port on the aircraft can be used for charging |

### 切片 2

| 组别 | 主体 | 谓词 | 客体 | 极性/范围 | 原文证据 |
|---|---|---|---|---|---|
| A | DJI Fly | uses | Connection Guide | asserted / instruction | On the home screen in DJI Fly tap Connection Guide in the bottom right corner |
| A | DJI Fly | applied_to | aircraft power button | asserted / instruction | On the home screen in DJI Fly tap Connection Guide in the bottom right corner and select the device model When connecting to the app for the first time press and hold the power button on the aircraft |
| B | DJI Fly | supports | Connection Guide | asserted / instruction | On the home screen in DJI Fly tap Connection Guide in the bottom right corner |
| B | DJI Fly | applied_to | aircraft power button | asserted / instruction | On the home screen in DJI Fly tap Connection Guide in the bottom right corner and select the device model When connecting to the app for the first time press and hold the power button on the aircraft |
| B | DJI Fly | applied_to | aircraft battery level LEDs | asserted / instruction | On the home screen in DJI Fly tap Connection Guide in the bottom right corner and select the device model When connecting to the app for the first time press and hold the power button on the aircraft when the prompt appears The battery level LEDs blink from inside to out and then turn solid |

### 切片 3

| 组别 | 主体 | 谓词 | 客体 | 极性/范围 | 原文证据 |
|---|---|---|---|---|---|
| A/B | — | — | — | — | 均无关系输出 |

### 切片 4

| 组别 | 主体 | 谓词 | 客体 | 极性/范围 | 原文证据 |
|---|---|---|---|---|---|
| B | DJI Neo 2 | contains | takeoff button | asserted / instruction | Press the takeoff button once The aircraft will announce the selected mode |
| B | DJI Neo 2 | contains | status indicator | asserted / instruction | Once the status indicator turns blue you can control the aircraft with gestures |
| B | DJI Neo 2 | supports | gestures | asserted / capability | you can control the aircraft with gestures |

### 切片 5

| 组别 | 主体 | 谓词 | 客体 | 极性/范围 | 原文证据 |
|---|---|---|---|---|---|
| A/B | — | — | — | — | 均无关系输出 |

### 切片 6

| 组别 | 主体 | 谓词 | 客体 | 极性/范围 | 原文证据 |
|---|---|---|---|---|---|
| A/B | — | — | — | — | 均无关系输出 |

## 验证与限制

- 已离线核验两组请求的模型参数和各阶段 system 提示词一致，B 仅在候选阶段删除三项上下文字段；两组实体审核的标题和邻文保持原值。
- 两组共 12 个最终输出均通过 schema、审核凭证和原文定位校验。自动校验通过不等同于人工语义准确。
- 只测一个视频的一轮配对，模型结果和服务负载存在波动；两组 provider 提示词缓存命中量不同，因此不能把耗时差直接当作稳定性能收益。
- 若继续落地，应先做人工金标准回归，并检查新增实体的身份一致性及关系语义。

## 产物

- [完整请求、响应、计时与输出 JSON](../data/video_graph_runs/entity-candidate-scope-ab-20261119.json)
- 临时对照入口：`/tmp/compare_candidate_scope_20261119.py`。
