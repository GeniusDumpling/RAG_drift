# Hubsan 官方文档与型号参数

在仓库根目录运行，默认保存至 `data/Hubsan无人机文档`：

```bash
.venv/bin/pip install -r tools/hubsan_documents/requirements.txt
.venv/bin/python -m tools.hubsan_documents collect
.venv/bin/python -m tools.hubsan_documents verify
```

完整采集依次发现官方入口、下载手册、采集产品图片并生成型号参数目录。图片识别需要系统安装 Tesseract 和 `chi_sim`、`eng` 语言包；可用 `tesseract --list-langs` 检查。没有外部 OCR 服务或模型调用。

支持 `discover`、`download`、`build-catalog`、`verify`、`report` 分阶段执行；通用选项为 `--output PATH`、`--workers 1..8`（默认 4）、`--refresh`。只采集或补全图片可运行：

```bash
.venv/bin/python -m tools.hubsan_documents.images
.venv/bin/python -m tools.hubsan_documents build-catalog
```

图片命令同样支持 `--output PATH`、`--refresh`；完整 `collect --output PATH` 会将图片放到指定资料目录。重复运行校验并复用缓存，失败项重新下载；同一目录持有文件锁，避免两个采集实例覆盖进度。`--refresh` 重取来源，原文件按内容哈希保留。只有公开的 Hubsan 官方域名可采集，每次跳转都重新检查；不下载固件或 APP。历史 ZIP 手册仅提取 PDF，保存原 ZIP 和成员来源，不直接展开归档路径。

来源包含中英文下载索引、历史支持目录和产品页面。菜单地区与 PDF 语言分开记录；文件名没有明确语言标记时使用 `und`，不将法国站菜单中的英文手册误标为法语。产品链接去除会话及购物参数，套装重复页面合并，明确的精英版、避障版、4G 版仍分别保留。

输出包括原始 PDF／ZIP、HTML／JSON 快照、`文档索引.csv`、`文档清单.json`、`网页采集记录.csv`、`采集状态.json`、`下载进度.jsonl`、`汇总.json`、`失败清单.json` 和 `资料目录.html`。图片及逐格 OCR 辅助结果存放于 `参数图片/`。

前端只读取 `机型参数目录/技术参数索引.csv` 和各型号 HTML。文字参数从产品规格区域提取，排除购物重量；图片参数使用 `reviewed_images.json` 中人工核对并绑定原图 SHA256 的记录。**未经核对的 OCR 数值不会发布**；原图未归档或哈希已变时不复用旧转录。单位、电池配置、测试条件、原文不一致的数值均保留，不自行计算或修正。已核对的部分参数标记为 `partial`；暂未提取参数的型号记录在 `技术参数缺失清单.json`，不会伪造为空值机型。

更新官网图片后，先检查 `参数图片` 的来源、图片和 OCR，再修改转录记录并重新生成目录。仅改哈希而不核对原图不构成更新。

`verify` 检查所有成功归档的哈希，并实际打开 PDF；失败返回 1。完成命令后若仍有失败来源或待下载文档返回 2，具体原因见失败清单；这不代表已下载文件无效。离线 `build-catalog` 不发送网络请求。应用通过 `HUBSAN_DOCUMENTS_DIR` 指向资料根目录，Docker 只读挂载资料，文件不打包进镜像。

```bash
.venv/bin/pytest -q tools/drone_collection/tests tests/unit/test_drone_brands.py
.venv/bin/ruff check tools/drone_collection tools/hubsan_documents
```
