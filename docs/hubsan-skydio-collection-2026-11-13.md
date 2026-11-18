# Hubsan 与 Skydio 文档采集和型号目录（2026-11-13）

新增工具：[Hubsan](../tools/hubsan_documents/README.md)、[Skydio](../tools/skydio_documents/README.md)。两者复用 Autel 的匿名下载客户端和公共归档模块 `tools/drone_collection`，保留官方原文、来源、内容哈希及采集时间，生成兼容 DJI 参数读取器的 HTML／CSV 目录。

| 品牌 | 去重 PDF | 原始文档引用成功／总数 | 前端型号或配置 | 参数项 |
|---|---:|---:|---:|---:|
| Hubsan | 171 | 212／222 | 59 | 1147 |
| Skydio | 96 | 112／112 | 9 | 656 |

文档引用包含共享来源和 ZIP 成员，不能当作独立手册或机型数量。Hubsan 另保存 27 个去重 ZIP；PDF 总字节数为 2,131,917,236。Skydio PDF 总字节数为 741,158,597。数据分别位于 `data/Hubsan无人机文档`、`data/Skydio无人机文档`，前端读取各目录内的 `机型参数目录`。

## 官方入口和处理方式

Hubsan 使用[中文下载索引](https://chn-filemanger.hubsan.com/index/index/getSupport.html)、[国际下载索引](https://www-filemanger.hubsan.com/index/index/getSupport.html)、[历史支持目录](https://www.hubsan.com/support/index/index/products.html)和[中文产品目录](https://chn.hubsan.com/index.php?main_page=index&cPath=1)。归档 PDF、历史手册 ZIP、官方产品网页及必要的规格图片。ZIP 仅提取 PDF，记录成员和原包哈希，限制包大小及展开规模，不解压固件。

型号匹配去除品牌空格、识别 PLUS 和套餐边界；ACE／SE／PRO、精英版、黑鹰避障／超越／4G 版、MINI／MINI 7 和 ACE 2／2+／3 分开。产品页中的购物重量不进入飞行器参数。图片数值经人工查看原图后转录，当前有 25 个型号的核对记录，均绑定实际归档原图 SHA256；OCR 只用于定位和辅助核对。原图变化后不沿用旧转录。部分参数标记为 `partial`，保留原文单位、电池配置和测试条件；例如 ACE 2 图片中的 `5000mah 46.08Wh` 与 ACE 3 的 `5000mAh 72Wh` 分别保留，不重新计算能量或合并。

Skydio 使用[公开支持中心分类 API](https://support.skydio.com/api/v2/help_center/en-us/categories.json?per_page=100)，逐分类和分页归档公开文章、手册及维护指南，并采集[官方 X10 规格](https://www.skydio.com/x10/technical-specs)、X10D 和 R10 规格页。共 628 份成功页面／API 快照；API 派生正文明确记录来源 API、哈希和更新时间。Skydio 2 与 2+ 按手册中的型号规格章节分开（23／27 分钟）；X2D INTL、ATO 使用各自手册。PDF 参数 JSON 保留来源页码和表格坐标。R1 仅引用官方 FAQ 中的飞行和影像信息，标记为部分资料。

## 缺失来源和校验

Hubsan 已发现的链接全部尝试完成，待下载为 0。其中 9 个文档链接返回 HTTP 404、1 个历史 ZIP 超过 150 MiB 的单文件上限；另有 1 个产品页返回 404，详见 `失败清单.json`。没有将失败响应当作 PDF。Skydio 页面和文档失败均为 0。

Hubsan 当前尚未形成参数目录的来源范围为：ZINO MINI、ZINO Pro+ (JP SPEC)、H501S High Edition、H501S Standard、H502C，详见 `技术参数缺失清单.json`。已收录的各型号也可能只有部分规格；原始手册和图片仍完整保留在归档中，不能将当前参数目录视为全品牌完整规格库。

校验重新读取成功文件并核对 SHA256、实际打开 PDF，同时检查生成目录的 HTML 哈希、JSON 参数数量和值、参数原始来源哈希。Hubsan 原始资料 283 份、规格图片 417 份、Skydio 736 份均通过校验。Hubsan CLI 因上述已记录的来源失败返回 2；其 `校验报告.json` 的 `ok=true` 表示已保存文件完整性通过，返回 1 才表示校验失败。

复现命令（在仓库根目录）：

```bash
.venv/bin/python -m tools.hubsan_documents collect --workers 8
.venv/bin/python -m tools.skydio_documents collect
.venv/bin/python -m tools.hubsan_documents verify
.venv/bin/python -m tools.skydio_documents verify
```

支持缓存校验和续采、阶段命令、指定输出目录、刷新来源。来源和参数均不依赖付费模型。采集本身不向数据库写入，应用通过 `HUBSAN_DOCUMENTS_DIR`、`SKYDIO_DOCUMENTS_DIR` 读取只读资料目录。
