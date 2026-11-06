# 当前旧版实体与关系 golden 逐项对照

版本：`video-graph-v2:deepseek-v4.1-flash-economy:c88ca83a8725-none-none`。模型`deepseek-v4.1-flash-economy`，抽取/审核推理档位均为none。

使用恢复旧版后的完整缓存重放结果，20/20段成功；92次阶段请求均为缓存重放，没有新增模型调用。结果与旧版独立复测逐项一致，不是新的随机抽样。

人工答案为entity-gold-v3（101条实体）和relation-gold-v3（77条关系），20段，中英文各10段。实际实体按身份去重100条；实际关系去重63条，其中60条在计分范围内，3条范围外待人工复核。

## 整体指标

| 指标 | TP命中 | FP未匹配预测 | FN漏匹配 | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|
| 实体识别（不要求类型） | 91 | 9 | 10 | 91.0% | 90.1% | 90.5% |
| 实体及类型 | 89 | 11 | 12 | 89.0% | 88.1% | 88.6% |
| 关系三元组 | 46 | 14 | 31 | 76.7% | 59.7% | 67.2% |
| 关系三元组＋断言 | 45 | 15 | 32 | 75.0% | 58.4% | 65.7% |
| 关系三元组＋断言＋严格限定 | 33 | 27 | 44 | 55.0% | 42.9% | 48.2% |

实体主指标要求名称/原文提及对齐且类型正确；关系主指标要求端点身份和类型、方向、谓词及assertion一致。兼容与连接关系允许反向。qualifiers差异只影响严格限定分数。

下表中的“漏抽”“多抽”表示未通过既定金标准匹配，不等于人工已判定现实事实错误。类型/断言不符的一行同时计1个FN及1个FP；没有因为并排显示而补分。关系范围外的3项不计TP或FP。金标准entities/relations是答案，历史notes不覆盖答案。

## 20段汇总

| 片段 | 实体：gold / 实际 | 实体：命中 / 漏 / 多 | 关系：gold / 实际 | 关系：命中 / 漏 / 多 | 范围外关系 | 严格限定命中 |
|---|---:|---:|---:|---:|---:|---:|
| [EG001](#eg001) | 7 / 7 | 5 / 2 / 2 | 5 / 11 | 4 / 1 / 6 | 1 | 2 |
| [EG002](#eg002) | 4 / 5 | 4 / 0 / 1 | 2 / 4 | 2 / 0 / 0 | 2 | 2 |
| [EG003](#eg003) | 2 / 2 | 2 / 0 / 0 | 1 / 1 | 0 / 1 / 1 | 0 | 0 |
| [EG004](#eg004) | 5 / 8 | 5 / 0 / 3 | 3 / 1 | 1 / 2 / 0 | 0 | 1 |
| [EG005](#eg005) | 10 / 9 | 9 / 1 / 0 | 12 / 5 | 5 / 7 / 0 | 0 | 4 |
| [EG006](#eg006) | 6 / 4 | 4 / 2 / 0 | 5 / 2 | 2 / 3 / 0 | 0 | 2 |
| [EG007](#eg007) | 8 / 8 | 8 / 0 / 0 | 7 / 7 | 7 / 0 / 0 | 0 | 7 |
| [EG008](#eg008) | 11 / 11 | 11 / 0 / 0 | 4 / 6 | 3 / 1 / 3 | 0 | 3 |
| [EG009](#eg009) | 1 / 1 | 1 / 0 / 0 | 0 / 0 | 0 / 0 / 0 | 0 | 0 |
| [EG010](#eg010) | 4 / 4 | 4 / 0 / 0 | 2 / 2 | 2 / 0 / 0 | 0 | 2 |
| [EG011](#eg011) | 2 / 2 | 1 / 1 / 1 | 1 / 1 | 0 / 1 / 1 | 0 | 0 |
| [EG012](#eg012) | 5 / 4 | 4 / 1 / 0 | 5 / 4 | 4 / 1 / 0 | 0 | 0 |
| [EG013](#eg013) | 5 / 2 | 2 / 3 / 0 | 4 / 1 | 0 / 4 / 1 | 0 | 0 |
| [EG014](#eg014) | 5 / 5 | 5 / 0 / 0 | 4 / 4 | 4 / 0 / 0 | 0 | 2 |
| [EG015](#eg015) | 2 / 5 | 2 / 0 / 3 | 1 / 0 | 0 / 1 / 0 | 0 | 0 |
| [EG016](#eg016) | 4 / 3 | 2 / 2 / 1 | 4 / 0 | 0 / 4 / 0 | 0 | 0 |
| [EG017](#eg017) | 7 / 7 | 7 / 0 / 0 | 8 / 4 | 3 / 5 / 1 | 0 | 2 |
| [EG018](#eg018) | 6 / 6 | 6 / 0 / 0 | 4 / 5 | 4 / 0 / 1 | 0 | 2 |
| [EG019](#eg019) | 5 / 5 | 5 / 0 / 0 | 4 / 4 | 3 / 1 / 1 | 0 | 3 |
| [EG020](#eg020) | 2 / 2 | 2 / 0 / 0 | 1 / 1 | 1 / 0 / 0 | 0 | 1 |
| 合计 | 101 / 100 | 89 / 12 / 11 | 77 / 63 | 45 / 32 / 15 | 3 | 33 |

每段表内编号分别对应本段golden/实际数组的顺序；实际名称、类型、方向与断言原样展示。名称不同但在mentions中有相同原文叫法时，仍可命中。所有标准项和实际项各展示一次；只依据已有一对一匹配结果并排，不凭近似名称或标准关系猜配对。

<a id="eg001"></a>

## EG001 · 中文

[无人机还能不能飞？大疆Avata 360 ，室内VR全景拍摄生产力工具](https://www.youtube.com/watch?v=_6E4tJWhqBM) · 238.3–297.0秒

### 实体

| 状态 | Golden实体（类型） | 实际抽取实体（类型） |
|---|---|---|
| 命中 | 1. DJI Avata 360（product） | 1. DJI Avata 360（product） |
| 漏抽 | 2. DJI Avata系列（product_family） | — |
| 命中 | 3. Avata 360桨叶保护罩（component） | 2. DJI Avata 360 桨叶保护（component） |
| 类型不符（FN＋FP） | 4. 视觉避障（technology） | 3. DJI Avata 360 视觉避障系统（component） |
| 命中 | 5. 全向避障（technology） | 5. 全向避障功能（technology） |
| 命中 | 6. DJI Avata 360 下方传感器（component） | 4. DJI Avata 360 传感器（component） |
| 命中 | 7. 炸机（fault） | 7. 炸机（fault） |
| 多抽/未匹配 | — | 6. 360全景拍摄（technology） |

### 关系

| 状态 | Golden关系 | 实际抽取关系 | 限定条件 |
|---|---|---|---|
| 命中 | 1. DJI Avata 360 → contains → Avata 360桨叶保护罩 [asserted] | 1. DJI Avata 360 → contains → DJI Avata 360 桨叶保护 [asserted] | — |
| 命中 | 2. DJI Avata 360 → contains → DJI Avata 360 下方传感器 [asserted] | 9. DJI Avata 360 → contains → DJI Avata 360 传感器 [asserted] | — |
| 漏抽 | 3. DJI Avata 360 → supports → 视觉避障 [asserted] | — | — |
| 命中；限定不同 | 4. DJI Avata 360 → supports → 全向避障 [asserted] | 10. DJI Avata 360 → supports → 全向避障功能 [asserted] | 不同<br>标准：{"modality":"conditional","scope":"全向避障功能呢 ⏎ 在拍摄360全景的时候会起作用"}<br>实际：{} |
| 命中；限定不同 | 5. Avata 360桨叶保护罩 → mitigates → 炸机 [asserted] | 4. DJI Avata 360 桨叶保护 → mitigates → 炸机 [asserted] | 不同<br>标准：{"modality":"capability"}<br>实际：{} |
| 多抽/未匹配 | — | 2. DJI Avata 360 视觉避障系统 → contains → DJI Avata 360 传感器 [asserted] | — |
| 多抽/未匹配 | — | 3. DJI Avata 360 视觉避障系统 → supports → 全向避障功能 [asserted] | — |
| 多抽/未匹配 | — | 5. DJI Avata 360 视觉避障系统 → mitigates → 炸机 [asserted] | — |
| 多抽/未匹配 | — | 6. DJI Avata 360 传感器 → mitigates → 炸机 [asserted] | — |
| 多抽/未匹配 | — | 7. 全向避障功能 → mitigates → 炸机 [asserted] | — |
| 多抽/未匹配 | — | 8. DJI Avata 360 → contains → DJI Avata 360 视觉避障系统 [asserted] | — |
| 范围外，待复核 | — | 11. DJI Avata 360 → supports → 360全景拍摄 [asserted] | — |

<a id="eg002"></a>

## EG002 · 中文

[大疆的全景无人机？！Avata360上手](https://www.youtube.com/watch?v=UxbQRuKiwes) · 0.0–58.788秒

### 实体

| 状态 | Golden实体（类型） | 实际抽取实体（类型） |
|---|---|---|
| 命中 | 1. DJI Avata 360（product） | 1. DJI Avata 360（product） |
| 命中 | 2. DJI Avata系列（product_family） | 4. DJI Avata（product_family） |
| 命中 | 3. 大疆（organization） | 2. DJI（organization） |
| 命中 | 4. Avata 360方形传感器（component） | 5. 方形传感器（component） |
| 多抽/未匹配 | — | 3. 全景无人机（product_family） |

### 关系

| 状态 | Golden关系 | 实际抽取关系 | 限定条件 |
|---|---|---|---|
| 命中 | 1. DJI Avata 360 → offered_by → 大疆 [asserted] | 1. DJI Avata 360 → offered_by → DJI [asserted] | — |
| 命中 | 2. DJI Avata 360 → contains → Avata 360方形传感器 [asserted] | 4. DJI Avata 360 → contains → 方形传感器 [asserted] | — |
| 范围外，待复核 | — | 2. 全景无人机 → offered_by → DJI [asserted] | — |
| 范围外，待复核 | — | 3. 全景无人机 → has_model → DJI Avata 360 [asserted] | — |

<a id="eg003"></a>

## EG003 · 中文

[【3C老實說】DJI Avata 360 全景空拍機評測：人人都能一秒變身空拍高手，5大新玩法介紹、DJI Fly App / DJI Studio 操作教學](https://www.youtube.com/watch?v=BXiP0SVRezc) · 923.231–979.5秒

### 实体

| 状态 | Golden实体（类型） | 实际抽取实体（类型） |
|---|---|---|
| 命中 | 1. DJI Avata 360（product） | 1. DJI Avata 360（product） |
| 命中 | 2. DJI Studio（software） | 2. DJI Studio（software） |

### 关系

| 状态 | Golden关系 | 实际抽取关系 | 限定条件 |
|---|---|---|---|
| 漏抽 | 1. DJI Studio → applied_to → DJI Avata 360 [asserted] | — | 标准：{"modality":"instruction"} |
| 多抽/未匹配 | — | 1. DJI Avata 360 → connected_to → DJI Studio [asserted] | — |

<a id="eg004"></a>

## EG004 · 中文

[【Yipee科技新聞點評】 大疆 Neo 2 最強宿敵登場？Insta360 摺疊口袋無人機專利曝光！掌上自拍空拍機大洗牌！](https://www.youtube.com/watch?v=9bd--Zcc33M) · 12.533–68.5秒

### 实体

| 状态 | Golden实体（类型） | 实际抽取实体（类型） |
|---|---|---|
| 命中 | 1. DJI Neo 2（product） | 3. DJI Neo 2（product） |
| 命中 | 2. DJI Flip（product） | 4. DJI Flip（product） |
| 命中 | 3. Insta360 Antigravity A1（product） | 5. Antigravity A1（product） |
| 命中 | 4. Insta360（organization） | 1. Insta360（organization） |
| 命中 | 5. DJI（organization） | 2. DJI（organization） |
| 多抽/未匹配 | — | 6. Insta360 摺疊無人機專利（technology） |
| 多抽/未匹配 | — | 7. 摺疊機臂（component） |
| 多抽/未匹配 | — | 8. 環形螺旋槳保護罩（component） |

### 关系

| 状态 | Golden关系 | 实际抽取关系 | 限定条件 |
|---|---|---|---|
| 漏抽 | 1. DJI Neo 2 → offered_by → DJI [asserted] | — | — |
| 漏抽 | 2. DJI Flip → offered_by → DJI [asserted] | — | — |
| 命中 | 3. Insta360 Antigravity A1 → offered_by → Insta360 [asserted] | 1. Antigravity A1 → offered_by → Insta360 [asserted] | — |

<a id="eg005"></a>

## EG005 · 中文

[【Yipee科技新聞點評】DJI Neo 3 發表時間與售價曝光｜全新 RC Mini 螢幕遙控器、懸吊電機結構、鏡頭升級全解析！對比 Neo 2 差在哪？最快 10 月發表！](https://www.youtube.com/watch?v=7NQZfyw-kNI) · 191.5–250.0秒

### 实体

| 状态 | Golden实体（类型） | 实际抽取实体（类型） |
|---|---|---|
| 命中 | 1. DJI Neo 3（product） | 1. DJI Neo 3（product） |
| 命中 | 2. DJI Neo 2（product） | 2. DJI Neo 2（product） |
| 命中 | 3. DJI Neo系列（product_family） | 8. DJI Neo series（product_family） |
| 命中 | 4. DJI（organization） | 9. DJI（organization） |
| 命中 | 5. Neo 3相机模组（component） | 4. DJI Neo 3 相機模組（component） |
| 漏抽 | 6. Neo 3镜头（component） | — |
| 命中 | 7. Neo 3云台（component） | 5. DJI Neo 3 雲台（component） |
| 命中 | 8. Neo 3相机系统（component） | 6. DJI Neo 3 相機系統（component） |
| 命中 | 9. Neo 3感光元件（component） | 7. 感光元件（component） |
| 命中 | 10. DJI Neo（product） | 3. DJI Neo（product） |

### 关系

| 状态 | Golden关系 | 实际抽取关系 | 限定条件 |
|---|---|---|---|
| 漏抽 | 1. DJI Neo 3 → offered_by → DJI [asserted] | — | — |
| 漏抽 | 2. DJI Neo 2 → offered_by → DJI [asserted] | — | — |
| 漏抽 | 3. DJI Neo系列 → offered_by → DJI [asserted] | — | — |
| 命中 | 4. DJI Neo系列 → has_model → DJI Neo 2 [asserted] | 3. DJI Neo series → has_model → DJI Neo 2 [asserted] | — |
| 漏抽 | 5. DJI Neo系列 → has_model → DJI Neo 3 [uncertain] | — | 标准：{"modality":"hypothetical","scope":"按照這個週期推算 ⏎ DJI Neo 3 可能要等到今年 12 月或 2027 年初才會亮相"} |
| 命中；限定不同 | 6. DJI Neo 3 → contains → Neo 3相机模组 [uncertain] | 2. DJI Neo 3 → contains → DJI Neo 3 相機模組 [uncertain] | 不同<br>标准：{"modality":"hypothetical","scope":"另外,DJI Neo 3 的相機模組看起來似乎比 Neo 2 更大"}<br>实际：{"modality":"hypothetical","scope":"DJI Neo 3 的相機模組看起來似乎比 Neo 2 更大"} |
| 漏抽 | 7. DJI Neo 3 → contains → Neo 3镜头 [uncertain] | — | 标准：{"modality":"hypothetical","scope":"另外,DJI Neo 3 的相機模組看起來似乎比 Neo 2 更大 ⏎ 鏡頭向前突出的幅度也比前一代明顯"} |
| 命中 | 8. DJI Neo 3 → contains → Neo 3云台 [uncertain] | 4. DJI Neo 3 → contains → DJI Neo 3 雲台 [uncertain] | 一致：{"modality":"hypothetical","scope":"外媒因此推測 DJI 可能重新設計 Neo 3 的雲台或相機系統"} |
| 命中 | 9. DJI Neo 3 → contains → Neo 3相机系统 [uncertain] | 5. DJI Neo 3 → contains → DJI Neo 3 相機系統 [uncertain] | 一致：{"modality":"hypothetical","scope":"外媒因此推測 DJI 可能重新設計 Neo 3 的雲台或相機系統"} |
| 漏抽 | 10. DJI Neo 3 → contains → Neo 3感光元件 [uncertain] | — | 标准：{"modality":"hypothetical","scope":"但由於曝光的包裝盒上沒有標示感光元件尺寸、錄影規格、重量與續航 ⏎ 因此無法確認 DJI Neo 3 是否會繼續維持輕量化設計 ⏎ 也無法證實先前傳出搭載 1/1.3 或 1/1.1 吋感光元件的消息是否屬實"} |
| 命中 | 11. DJI Neo → offered_by → DJI [asserted] | 1. DJI Neo → offered_by → DJI [asserted] | — |
| 漏抽 | 12. DJI Neo系列 → has_model → DJI Neo [asserted] | — | — |

<a id="eg006"></a>

## EG006 · 中文

[2026 新手無人機別亂買！DJI Neo 2 vs Lito 1 vs Lito X1 誰才是性價比之王？](https://www.youtube.com/watch?v=A9n3aAoDDBg) · 249.94–309.24秒

### 实体

| 状态 | Golden实体（类型） | 实际抽取实体（类型） |
|---|---|---|
| 命中 | 1. DJI Mini 4 Pro（product） | 2. DJI Mini 4 Pro（product） |
| 命中 | 2. DJI Neo 2（product） | 1. DJI Neo 2（product） |
| 漏抽 | 3. CMOS（technology） | — |
| 漏抽 | 4. Mini 4 Pro长续航电池（component） | — |
| 命中 | 5. Mini 4 Pro三轴机械云台（component） | 3. DJI Mini 4 Pro 三轴机械云台（component） |
| 命中 | 6. 视觉全向避障（technology） | 4. 视觉全向避障（technology） |

### 关系

| 状态 | Golden关系 | 实际抽取关系 | 限定条件 |
|---|---|---|---|
| 漏抽 | 1. DJI Neo 2 → uses → CMOS [asserted] | — | — |
| 漏抽 | 2. DJI Mini 4 Pro → compatible_with → Mini 4 Pro长续航电池 [asserted] | — | — |
| 命中 | 3. DJI Mini 4 Pro → contains → Mini 4 Pro三轴机械云台 [asserted] | 1. DJI Mini 4 Pro → contains → DJI Mini 4 Pro 三轴机械云台 [asserted] | — |
| 命中 | 4. DJI Mini 4 Pro → supports → 视觉全向避障 [asserted] | 2. DJI Mini 4 Pro → supports → 视觉全向避障 [asserted] | — |
| 漏抽 | 5. DJI Mini 4 Pro → uses → CMOS [asserted] | — | — |

<a id="eg007"></a>

## EG007 · 中文

[CP值最高的入門空拍機！DJI Lito X1 &amp; Lito 1 完整評測｜免登記免考照、光達避障、D-Log M全都有 #dji  #DJILitoX1 #肯吉老王開箱](https://www.youtube.com/watch?v=2VqkvtTfK5g) · 61.833–121.266秒

### 实体

| 状态 | Golden实体（类型） | 实际抽取实体（类型） |
|---|---|---|
| 命中 | 1. DJI Lito 1（product） | 3. DJI Lito 1（product） |
| 命中 | 2. DJI Lito X1（product） | 2. DJI Lito X1（product） |
| 命中 | 3. DJI Mini 3（product） | 5. DJI Mini 3（product） |
| 命中 | 4. DJI Mini 4K（product） | 6. DJI Mini 4K（product） |
| 命中 | 5. DJI Lito系列（product_family） | 4. DJI Lito系列（product_family） |
| 命中 | 6. DJI（organization） | 1. DJI（organization） |
| 命中 | 7. 镜头（component） | 7. 鏡頭（component） |
| 命中 | 8. 避障（technology） | 8. 避障（technology） |

### 关系

| 状态 | Golden关系 | 实际抽取关系 | 限定条件 |
|---|---|---|---|
| 命中 | 1. DJI Lito 1 → offered_by → DJI [asserted] | 5. DJI Lito 1 → offered_by → DJI [asserted] | — |
| 命中 | 2. DJI Lito X1 → offered_by → DJI [asserted] | 4. DJI Lito X1 → offered_by → DJI [asserted] | — |
| 命中 | 3. DJI Mini 3 → offered_by → DJI [asserted] | 2. DJI Mini 3 → offered_by → DJI [asserted] | — |
| 命中 | 4. DJI Mini 4K → offered_by → DJI [asserted] | 3. DJI Mini 4K → offered_by → DJI [asserted] | — |
| 命中 | 5. DJI Lito系列 → offered_by → DJI [asserted] | 1. DJI Lito系列 → offered_by → DJI [asserted] | — |
| 命中 | 6. DJI Lito系列 → has_model → DJI Lito 1 [asserted] | 7. DJI Lito系列 → has_model → DJI Lito 1 [asserted] | — |
| 命中 | 7. DJI Lito系列 → has_model → DJI Lito X1 [asserted] | 6. DJI Lito系列 → has_model → DJI Lito X1 [asserted] | — |

<a id="eg008"></a>

## EG008 · 中文

[2788元的新形态无人机，好用吗？DJI Flip上手！](https://www.youtube.com/watch?v=jrvhN6yNovA) · 268.397–328.157秒

### 实体

| 状态 | Golden实体（类型） | 实际抽取实体（类型） |
|---|---|---|
| 命中 | 1. DJI Flip（product） | 1. DJI Flip（product） |
| 命中 | 2. DJI Mavic 3 Pro（product） | 4. DJI Mavic 3 Pro（product） |
| 命中 | 3. DJI Air 3（product） | 5. DJI Air 3（product） |
| 命中 | 4. DJI Air 3S（product） | 6. DJI Air 3S（product） |
| 命中 | 5. DJI Mini 3（product） | 7. DJI Mini 3（product） |
| 命中 | 6. DJI Mini 4 Pro（product） | 8. DJI Mini 4 Pro（product） |
| 命中 | 7. DJI Action 4（product） | 9. DJI Action 4（product） |
| 命中 | 8. 大疆（organization） | 10. DJI（organization） |
| 命中 | 9. Flip传感器（component） | 2. 传感器（component） |
| 命中 | 10. Flip广角镜头（component） | 3. 广角镜头（component） |
| 命中 | 11. HDR（technology） | 11. HDR（technology） |

### 关系

| 状态 | Golden关系 | 实际抽取关系 | 限定条件 |
|---|---|---|---|
| 命中 | 1. DJI Flip → contains → Flip传感器 [asserted] | 2. DJI Flip → contains → 传感器 [asserted] | — |
| 命中 | 2. DJI Flip → contains → Flip广角镜头 [asserted] | 5. DJI Flip → contains → 广角镜头 [asserted] | — |
| 漏抽 | 3. DJI Flip → offered_by → 大疆 [asserted] | — | — |
| 命中 | 4. DJI Mavic 3 Pro → offered_by → 大疆 [asserted] | 1. DJI Mavic 3 Pro → offered_by → DJI [asserted] | — |
| 多抽/未匹配 | — | 3. DJI Air 3 → offered_by → DJI [asserted] | — |
| 多抽/未匹配 | — | 4. DJI Air 3S → offered_by → DJI [asserted] | — |
| 多抽/未匹配 | — | 6. DJI Flip → supports → HDR [asserted] | — |

<a id="eg009"></a>

## EG009 · 中文

[从豁然开朗到“死黑一片”：这台Mini 4K无人机，怎么好像也有点“近视”？ #广东麦克疯#越南 #航拍機 #老挝旅游 #越南旅遊](https://www.youtube.com/watch?v=7MJjAlpvj7w) · 0.0–58.6秒

### 实体

| 状态 | Golden实体（类型） | 实际抽取实体（类型） |
|---|---|---|
| 命中 | 1. DJI Mini 4K（product） | 1. MINI 4K（product） |

### 关系

| 状态 | Golden关系 | 实际抽取关系 | 限定条件 |
|---|---|---|---|

<a id="eg010"></a>

## EG010 · 中文

[Inside a DJI Store in China with Prices 🇨🇳 DJI Pocket 4P + MORE (2026)](https://www.youtube.com/watch?v=TtfVuyfODLU) · 737.84–795.2秒

### 实体

| 状态 | Golden实体（类型） | 实际抽取实体（类型） |
|---|---|---|
| 命中 | 1. DJI Neo 2（product） | 3. DJI Neo 2（product） |
| 命中 | 2. DJI Mavic 4 Pro（product） | 1. DJI Mavic 4<br>Pro（product） |
| 命中 | 3. RC 2（product） | 2. RC 2 遥控器（product） |
| 命中 | 4. 护目镜（product） | 4. 护目镜（product） |

### 关系

| 状态 | Golden关系 | 实际抽取关系 | 限定条件 |
|---|---|---|---|
| 命中 | 1. DJI Mavic 4 Pro → compatible_with → RC 2 [asserted] | 2. DJI Mavic 4<br>Pro → compatible_with → RC 2 遥控器 [asserted] | — |
| 命中 | 2. DJI Neo 2 → compatible_with → 护目镜 [asserted] | 1. 护目镜 → compatible_with → DJI Neo 2 [asserted] | — |

<a id="eg011"></a>

## EG011 · 英文

[How to Trippy Spin with DJI Neo 2 (FPV Stick Tutorial) 🛸​#DJINeo2 #TrippySpin  #DJIFPV #djineo](https://www.youtube.com/watch?v=qAgye8oJBKA) · 0.56–18.32秒

### 实体

| 状态 | Golden实体（类型） | 实际抽取实体（类型） |
|---|---|---|
| 命中 | 1. DJI Neo 2（product） | 1. DJI Neo 2（product） |
| 类型不符（FN＋FP） | 2. Trippy Spin（method） | 2. trippy spin（technology） |

### 关系

| 状态 | Golden关系 | 实际抽取关系 | 限定条件 |
|---|---|---|---|
| 漏抽 | 1. Trippy Spin → applied_to → DJI Neo 2 [asserted] | — | 标准：{"modality":"instruction"} |
| 多抽/未匹配 | — | 1. DJI Neo 2 → supports → trippy spin [asserted] | 实际：{"modality":"instruction"} |

<a id="eg012"></a>

## EG012 · 英文

[Fix Your DJI Air 3 Drone Like a Pro: Gimbal Ribbon Cable Replacement Guide](https://www.youtube.com/watch?v=_VofSIgvXOc) · 40.32–89.92秒

### 实体

| 状态 | Golden实体（类型） | 实际抽取实体（类型） |
|---|---|---|
| 命中 | 1. DJI Air 3（product） | 1. DJI Air 3（product） |
| 命中 | 2. DJI Air 3 Battery（component） | 2. battery（component） |
| 命中 | 3. DJI Air 3 Screws（component） | 4. screws（component） |
| 命中 | 4. bottom cover（component） | 3. bottom cover（component） |
| 漏抽 | 5. drone body （component） | — |

### 关系

| 状态 | Golden关系 | 实际抽取关系 | 限定条件 |
|---|---|---|---|
| 命中；限定不同 | 1. DJI Air 3 → contains → DJI Air 3 Battery [asserted] | 2. DJI Air 3 → contains → battery [asserted] | 不同<br>标准：{}<br>实际：{"modality":"instruction"} |
| 命中；限定不同 | 2. DJI Air 3 → contains → DJI Air 3 Screws [asserted] | 1. DJI Air 3 → contains → screws [asserted] | 不同<br>标准：{}<br>实际：{"modality":"instruction"} |
| 命中；限定不同 | 3. DJI Air 3 → contains → bottom cover [asserted] | 4. DJI Air 3 → contains → bottom cover [asserted] | 不同<br>标准：{}<br>实际：{"modality":"instruction"} |
| 漏抽 | 4. DJI Air 3 → contains → drone body  [asserted] | — | — |
| 命中；限定不同 | 5. DJI Air 3 Screws → connected_to → bottom cover [asserted] | 3. bottom cover → connected_to → screws [asserted] | 不同<br>标准：{}<br>实际：{"modality":"instruction"} |

<a id="eg013"></a>

## EG013 · 英文

[DJI Mini 4 Pro｜First Use Guide](https://www.youtube.com/watch?v=JtYyP_y5Az0) · 4.08–58.8秒

### 实体

| 状态 | Golden实体（类型） | 实际抽取实体（类型） |
|---|---|---|
| 命中 | 1. DJI Mini 4 Pro（product） | 1. DJI Mini 4 Pro（product） |
| 漏抽 | 2. remote controller（product） | — |
| 命中 | 3. DJI Fly（software） | 2. DJI Fly（software） |
| 漏抽 | 4. propeller holder（component） | — |
| 漏抽 | 5. gimbal protector（component） | — |

### 关系

| 状态 | Golden关系 | 实际抽取关系 | 限定条件 |
|---|---|---|---|
| 漏抽 | 1. DJI Mini 4 Pro → packaged_with → remote controller [asserted] | — | — |
| 漏抽 | 2. DJI Fly → applied_to → DJI Mini 4 Pro [asserted] | — | 标准：{"modality":"instruction"} |
| 漏抽 | 3. DJI Mini 4 Pro → packaged_with → propeller holder [asserted] | — | — |
| 漏抽 | 4. DJI Mini 4 Pro → packaged_with → gimbal protector [asserted] | — | — |
| 多抽/未匹配 | — | 1. DJI Mini 4 Pro → uses → DJI Fly [asserted] | 实际：{"modality":"instruction"} |

<a id="eg014"></a>

## EG014 · 英文

[DJI Made Flipping a Drone Look This Easy](https://www.youtube.com/watch?v=AWQJTxxNpAI) · 0.13–24.075秒

### 实体

| 状态 | Golden实体（类型） | 实际抽取实体（类型） |
|---|---|---|
| 命中 | 1. DJI Avata 2（product） | 2. DJI Avata 2（product） |
| 命中 | 2. DJI（organization） | 1. DJI（organization） |
| 命中 | 3. Goggles 3（product） | 3. Goggles 3（product） |
| 命中 | 4. RC Motion 3（product） | 4. RC<br>Motion 3（product） |
| 命中 | 5. Easy ACRO（technology） | 5. Easy ACRO（technology） |

### 关系

| 状态 | Golden关系 | 实际抽取关系 | 限定条件 |
|---|---|---|---|
| 命中 | 1. DJI Avata 2 → offered_by → DJI [asserted] | 1. DJI Avata 2 → offered_by → DJI [asserted] | — |
| 命中；限定不同 | 2. DJI Avata 2 → compatible_with → Goggles 3 [asserted] | 2. DJI Avata 2 → compatible_with → Goggles 3 [asserted] | 不同<br>标准：{}<br>实际：{"modality":"instruction"} |
| 命中；限定不同 | 3. DJI Avata 2 → compatible_with → RC Motion 3 [asserted] | 3. DJI Avata 2 → compatible_with → RC<br>Motion 3 [asserted] | 不同<br>标准：{}<br>实际：{"modality":"instruction"} |
| 命中 | 4. DJI Avata 2 → supports → Easy ACRO [asserted] | 4. DJI Avata 2 → supports → Easy ACRO [asserted] | — |

<a id="eg015"></a>

## EG015 · 英文

[Parrot Anafi 4k Drone - Photographer's  Review](https://www.youtube.com/watch?v=8PjamSNj4BA) · 57.36–115.22秒

### 实体

| 状态 | Golden实体（类型） | 实际抽取实体（类型） |
|---|---|---|
| 命中 | 1. Parrot ANAFI（product） | 1. Parrot Anafi（product） |
| 命中 | 2. ANAFI遥控器（product） | 5. controller（product） |
| 多抽/未匹配 | — | 2. charging<br>cable USB "C"（component） |
| 多抽/未匹配 | — | 3. spare battery（component） |
| 多抽/未匹配 | — | 4. USB cable for the iPhone（component） |

### 关系

| 状态 | Golden关系 | 实际抽取关系 | 限定条件 |
|---|---|---|---|
| 漏抽 | 1. Parrot ANAFI → compatible_with → ANAFI遥控器 [asserted] | — | — |

<a id="eg016"></a>

## EG016 · 英文

[Crashed? Here's How to Replace DJI Mini Drone Propellers / 3 and 4 Pro](https://www.youtube.com/watch?v=XUoFNFt8Mng) · 0.16–17.561秒

### 实体

| 状态 | Golden实体（类型） | 实际抽取实体（类型） |
|---|---|---|
| 命中 | 1. DJI Mini 3（product） | 1. DJI Mini 3（product） |
| 命中 | 2. propellers（component） | 3. propellers（component） |
| 漏抽 | 3. striped arms（component） | — |
| 漏抽 | 4. striped propellers（component） | — |
| 多抽/未匹配 | — | 2. DJI Mini 4 Pro（product） |

### 关系

| 状态 | Golden关系 | 实际抽取关系 | 限定条件 |
|---|---|---|---|
| 漏抽 | 1. DJI Mini 3 → contains → propellers [asserted] | — | — |
| 漏抽 | 2. DJI Mini 3 → contains → striped arms [asserted] | — | — |
| 漏抽 | 3. DJI Mini 3 → contains → striped propellers [asserted] | — | — |
| 漏抽 | 4. striped propellers → installed_on → striped arms [asserted] | — | 标准：{"modality":"instruction"} |

<a id="eg017"></a>

## EG017 · 英文

[DJI Air 3: The Complete Disassembly and Reassembly Guide](https://www.youtube.com/watch?v=8k8JhMTr9xc) · 741.95–787.49秒

### 实体

| 状态 | Golden实体（类型） | 实际抽取实体（类型） |
|---|---|---|
| 命中 | 1. DJI Air 3（product） | 1. DJI Air 3（product） |
| 命中 | 2. Air 3电调板（component） | 2. DJI Air 3 ESC board（component） |
| 命中 | 3. Air 3 motors（component） | 3. DJI Air 3 motors（component） |
| 命中 | 4. ESC error（fault） | 5. ESC error（fault） |
| 命中 | 5. DJI（organization） | 7. DJI（organization） |
| 命中 | 6. firmware（software） | 4. DJI Air 3 firmware（software） |
| 命中 | 7. DJI Air 3 arm wires（component） | 6. DJI Air 3 arm wires（component） |

### 关系

| 状态 | Golden关系 | 实际抽取关系 | 限定条件 |
|---|---|---|---|
| 漏抽 | 1. DJI Air 3 → contains → Air 3电调板 [asserted] | — | — |
| 漏抽 | 2. DJI Air 3 → contains → Air 3 motors [asserted] | — | — |
| 漏抽 | 3. Air 3电调板 → controls → Air 3 motors [asserted] | — | — |
| 命中 | 4. DJI Air 3 → uses → firmware [asserted] | 2. DJI Air 3 → uses → DJI Air 3 firmware [asserted] | — |
| 命中；限定不同 | 5. DJI Air 3 → reports_fault → ESC error [asserted] | 1. DJI Air 3 → reports_fault → ESC error [asserted] | 不同<br>标准：{"modality":"conditional","scope":"If your DJI Air 3 drone has an ESC error and you can't fix this error by refreshing the ⏎ drone firmware, it is most likely the ESC board is failing, so you need to replace it."}<br>实际：{"modality":"conditional","scope":"If your DJI Air 3 drone has an ESC error and you can't fix this error by refreshing the ⏎ drone firmware"} |
| 漏抽 | 6. DJI Air 3 → offered_by → DJI [asserted] | — | — |
| 命中 | 7. DJI Air 3 → contains → DJI Air 3 arm wires [asserted] | 4. DJI Air 3 → contains → DJI Air 3 arm wires [asserted] | — |
| 漏抽 | 8. DJI Air 3 arm wires → connected_to → Air 3电调板 [asserted] | — | — |
| 多抽/未匹配 | — | 3. DJI Air 3 ESC board → reports_fault → ESC error [uncertain] | 实际：{"modality":"hypothetical","scope":"it is most likely the ESC board is failing"} |

<a id="eg018"></a>

## EG018 · 英文

[NEW DJI Mini 3 - The BEST Budget Drone EVER! (4K)](https://www.youtube.com/watch?v=RvSjkLzcFbw) · 115.86–161.56秒

### 实体

| 状态 | Golden实体（类型） | 实际抽取实体（类型） |
|---|---|---|
| 命中 | 1. DJI Mini 3（product） | 1. DJI Mini 3（product） |
| 命中 | 2. DJI Mavic Mini（product） | 3. DJI Mavic Mini（product） |
| 命中 | 3. DJI Mini 2（product） | 4. DJI Mini 2（product） |
| 命中 | 4. sport mode（technology） | 5. sport mode（technology） |
| 命中 | 5. Pro版本（product） | 2. DJI Mini 3 Pro（product） |
| 命中 | 6. True vertical shooting（technology） | 6. True vertical shooting（technology） |

### 关系

| 状态 | Golden关系 | 实际抽取关系 | 限定条件 |
|---|---|---|---|
| 命中 | 1. DJI Mini 3 → supports → sport mode [asserted] | 4. DJI Mini 3 → supports → sport mode [asserted] | — |
| 命中 | 2. Pro版本 → supports → sport mode [asserted] | 5. DJI Mini 3 Pro → supports → sport mode [asserted] | — |
| 命中；限定不同 | 3. DJI Mini 3 → supports → True vertical shooting [asserted] | 3. DJI Mini 3 → supports → True vertical shooting [asserted] | 不同<br>标准：{}<br>实际：{"modality":"capability"} |
| 命中；限定不同 | 4. Pro版本 → supports → True vertical shooting [asserted] | 2. DJI Mini 3 Pro → supports → True vertical shooting [asserted] | 不同<br>标准：{}<br>实际：{"modality":"capability"} |
| 多抽/未匹配 | — | 1. DJI Mini 2 → supports → sport mode [asserted] | — |

<a id="eg019"></a>

## EG019 · 英文

[Avata 360 Single-Lens Mode &#124; Struggling with Head Tracking + Motion 3 &#124; Turn On Caption (CC)](https://www.youtube.com/watch?v=sjEluw4GIqI) · 8.0–61.0秒

### 实体

| 状态 | Golden实体（类型） | 实际抽取实体（类型） |
|---|---|---|
| 命中 | 1. DJI Avata 360（product） | 1. Avata 360（product） |
| 命中 | 2. RC Motion 3（product） | 3. RC Motion 3（product） |
| 命中 | 3. Head Tracking（technology） | 4. Head Tracking（technology） |
| 命中 | 4. single-lens mode（technology） | 2. single-lens mode（technology） |
| 命中 | 5. spherical mode（technology） | 5. spherical mode（technology） |

### 关系

| 状态 | Golden关系 | 实际抽取关系 | 限定条件 |
|---|---|---|---|
| 命中 | 1. DJI Avata 360 → compatible_with → RC Motion 3 [asserted] | 2. Avata 360 → compatible_with → RC Motion 3 [asserted] | — |
| 命中 | 2. DJI Avata 360 → supports → single-lens mode [asserted] | 1. Avata 360 → supports → single-lens mode [asserted] | — |
| 命中 | 3. DJI Avata 360 → supports → spherical mode [asserted] | 4. Avata 360 → supports → spherical mode [asserted] | — |
| 断言不符（FN＋FP） | 4. DJI Avata 360 → supports → Head Tracking [uncertain] | 3. Avata 360 → supports → Head Tracking [asserted] | 不同<br>标准：{"modality":"hypothetical","scope":"I'm nodding and turning my head to check how Head Tracking moves the camera and drone.","mode":"single-lens mode"}<br>实际：{} |

<a id="eg020"></a>

## EG020 · 英文

[DJI Mini 5 Pro engel algılama testi: teller, yapraklar ve yağmur! 🚁](https://www.youtube.com/watch?v=ZqTrQBWrol0) · 0.0–34.5秒

### 实体

| 状态 | Golden实体（类型） | 实际抽取实体（类型） |
|---|---|---|
| 命中 | 1. DJI Mini 5 Pro（product） | 1. DJI Mini 5 Pro（product） |
| 命中 | 2. OBSTACLE AVOIDANCE（technology） | 2. OBSTACLE AVOIDANCE（technology） |

### 关系

| 状态 | Golden关系 | 实际抽取关系 | 限定条件 |
|---|---|---|---|
| 命中 | 1. DJI Mini 5 Pro → supports → OBSTACLE AVOIDANCE [asserted] | 1. DJI Mini 5 Pro → supports → OBSTACLE AVOIDANCE [asserted] | — |

## 主要差异

实体及类型共漏匹配12条、多抽/未匹配11条。关系主指标共漏匹配32条、多抽/未匹配15条；条件进一步严格匹配后命中从45条降至33条。

3条applied_to、3条packaged_with和1条installed_on标准关系均未命中。部分部件在实体阶段被过滤，部分关系发生主体归属、谓词或范围差异；具体对照以上表为准，不根据本次输出修改人工答案。

来源：[原始评测结果](../data/video_graph_runs/relation-gold-v3-restored-c88-20261105.json)、[实体金标准](../scripts/evaluation_data/entity_gold_v3.json)、[关系金标准](../scripts/evaluation_data/relation_gold_v3.json)、[恢复核验](../data/video_graph_runs/restored-c88-verification-20261105.json)。
