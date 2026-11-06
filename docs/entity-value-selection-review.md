# 实体入图价值：历史人工取舍记录（2026-11-04）

本表记录2026-11-04的人工确认。用户之后又直接修订了golden，当前101条答案以[标注JSON](../scripts/evaluation_data/entity_gold_v3.json)为准；本表保留作历史依据，不自动补回已删除项。

状态：**用户已确认（2026-11-04）**。保留V04、V10、V16、V23，其余20项排除。以下助手建议为确认前的建议，最终以“你的决定”为准。

范围：当前首组错误分析中的24项——21个“标准排除、模型保留”和3个“标准保留、模型未抽出”。“多抽”只是相对现有标准，不预先认定这些原文对象没有价值。

“现有标准 / 模型”记录确认前的旧标准及模型输出，便于追溯；助手建议与旧标准不同的项已标出。用户决定已同步到新版golden，未调整提示词、词表、抽取流程或历史评测结果。

“保留”只确认原文对象值得入图，不自动认可模型的类型、补全型号或事实极性。V12的参数不单独建实体；V23保留原文Pro版本，不代表认可补全的具体型号。

[旧版golden阅读版](entity-gold-v1-review.md) · [当前golden阅读版（后续已修订）](entity-gold-v3-review.md)

| 编号 | 对象 / 来源 | 原文上下文 | 现有标准 / 模型 | 助手建议与理由 | 你的决定 |
|---|---|---|---|---|---|
| V01 | Avata 360桨叶<br>EG001 | Avata360有桨叶保护的安全设计<br>被桨保包裹<br>不用担心飞转的桨叶伤人 | 标准保留；模型未抽出 | **保留**：是被保护的具体部件，与桨保组成安全说明；泛称本身不构成排除理由。 | □ 保留<br>☑ 排除<br>□ 待定 |
| V02 | Avata 360传感器<br>EG001 | 避障Avata360有独立的视觉避障系统<br>下面呢还有传感器 | 标准保留；模型未抽出 | **保留**：有明确所属设备、位置和避障语境；不补未提到的传感器型号。 | □ 保留<br>☑ 排除<br>□ 待定 |
| V03 | 炸机<br>EG001 | 不用担心飞转的桨叶伤人<br>更不怕碰到障碍物炸机 | 标准排除；模型保留 | **排除**：泛泛安全风险，没有具体异常、故障现象或维修内容；与你之前删除该实体的决定一致。 | □ 保留<br>☑ 排除<br>□ 待定 |
| V04 | 摇杆<br>EG002 | 有着我们熟悉的摇杆操作<br>也还可以用传统的遥控器来飞行 | 标准保留；模型未抽出 | **保留**：用于说明这台无人机的操控方式，是有功能信息的操作部件；不擅自绑定到未给型号的遥控器。 | ☑ 保留<br>□ 排除<br>□ 待定 |
| V05 | DJI（软件名称中的品牌）<br>EG003 | 我們首先開啟 DJI Studio，<br>然後選擇設備連接。 | 标准排除；模型保留 | **排除**：这里的操作主体是DJI Studio；DJI只是软件名的一部分，没有单独的公司行为或信息。 | □ 保留<br>☑ 排除<br>□ 待定 |
| V06 | Insta360折叠无人机专利<br>EG004 | 但近期外媒發現 Insta360 在中國取得摺疊無人機專利<br>外型似乎與《The New Camera》在2025年8月19日<br>曝光的神祕無人機原型相似 | 标准排除；模型保留 | **排除**：建议保留专利相关叙述，但本段未给可稳定指认的专利题名或编号，不单独造document节点。 | □ 保留<br>☑ 排除<br>□ 待定 |
| V07 | 折叠机臂<br>EG004 | 從目前線索來看<br>這款無人機可能具備摺疊機臂、環形螺旋槳保護罩 | 标准排除；模型保留 | **待定**：有具体结构信息，但所属对象是未具名原型。需与你保留Neo 3推测部件的口径一起确认；不能只因“可能”就排除实体。 | □ 保留<br>☑ 排除<br>□ 待定 |
| V08 | 环形螺旋桨保护罩<br>EG004 | 從目前線索來看<br>這款無人機可能具備摺疊機臂、環形螺旋槳保護罩 | 标准排除；模型保留 | **待定**：具体保护结构有检索价值，但原型身份未明确。建议与V07同口径处理；保留时不补具体型号或当成已实装。 | □ 保留<br>☑ 排除<br>□ 待定 |
| V09 | 轻量化设计<br>EG004 | 這款無人機可能具備摺疊機臂、環形螺旋槳保護罩<br>並採用輕量化設計 | 标准排除；模型保留 | **排除**：只有普通设计属性，没有具体实现或具名技术；保留描述，不独立作为technology。 | □ 保留<br>☑ 排除<br>□ 待定 |
| V10 | 第一代DJI Neo<br>EG005 | 依照 DJI Neo 系列過去的更新週期<br>第一代 DJI Neo 在 2024 年 9 月推出<br>DJI Neo 2 則是在 2025 年 10 月底推出<br>兩代相隔約 14 個月 | 标准排除；模型保留 | **待定**：有明确发布日期，并非完全没信息。取舍取决于图谱是否收录型号沿革；只关注当前产品时可排除。 | ☑ 保留<br>□ 排除<br>□ 待定 |
| V11 | 后制的一些设置<br>EG007 | 那這兩個最大的落差呢<br>在於鏡頭 在於避障<br>還有在一些後製的一些設定上<br>待會我們會一一的做介紹 | 标准排除；模型保留 | **排除**：未给具体软件、设置或模式名称，也未展开内容，不把泛泛设置误标为独立软件。 | □ 保留<br>☑ 排除<br>□ 待定 |
| V12 | 慢动作（当前输出还带4K 100帧参数）<br>EG008 | 它目前最高支持拍摄4K 60帧的HDR视频<br>还有4K 100帧的慢动作 | 标准排除；模型保留 | **保留（与标准不同）**：明确支持的拍摄能力，能回答慢动作规格问题；4K/100帧作属性。与同句已保留的HDR需要一致口径。 | □ 保留<br>☑ 排除<br>□ 待定 |
| V13 | 动态范围<br>EG008 | 它的动态范围还有解析力和Mini 4 Pro<br>不能说是相似<br>只能说是难分伯仲 | 标准排除；模型保留 | **排除**：是画质比较指标，建议保留为描述或属性，不独立当作技术实体。 | □ 保留<br>☑ 排除<br>□ 待定 |
| V14 | 解析力<br>EG008 | 它的动态范围还有解析力和Mini 4 Pro<br>不能说是相似<br>只能说是难分伯仲 | 标准排除；模型保留 | **排除**：与动态范围相同，是画质指标而非独立技术；比较信息仍保留在原文中。 | □ 保留<br>☑ 排除<br>□ 待定 |
| V15 | DJI（产品名称中的品牌）<br>EG010 | 接下来，我们当然不能错过 DJI Mavic 4<br>Pro。 | 标准排除；模型保留 | **排除**：此处只作为Mavic 4 Pro名称中的品牌，不单独生成一个公司节点。 | □ 保留<br>☑ 排除<br>□ 待定 |
| V16 | 护目镜<br>EG010 | 令人惊喜的是，这款护目镜也兼容我们的无人机 Neo 2。 | 标准排除；模型保留 | **保留（与标准不同）**：原文明示与Neo 2兼容，具有独立检索价值。没有型号可保留视频内身份，不能补成Goggles 3。 | ☑ 保留<br>□ 排除<br>□ 待定 |
| V17 | DJI（拆机对象名称中的品牌）<br>EG012 | Remove the battery before you start disassembling the DJI Air 3 drone. | 标准排除；模型保留 | **排除**：该句仅以DJI Air 3指称维修对象，没有单独介绍公司；与V05、V15作对照。 | □ 保留<br>☑ 排除<br>□ 待定 |
| V18 | bottom cover（底盖）<br>EG012 | Flip your drone over and remove the screws securing the bottom cover. | 标准排除；模型保留 | **保留（与标准不同）**：明确的拆卸结构，并交代由螺丝固定。建议与同段已保留的电池、螺丝统一判断。 | □ 保留<br>☑ 排除<br>□ 待定 |
| V19 | drone body（机身）<br>EG012 | Use plastic tools to carefully open the drone body and ensure safe handling. | 标准排除；模型保留 | **排除**：这里只泛称机身，未区分具体壳体或结构；已有Air 3整机，建议避免再造含义模糊的节点。 | □ 保留<br>☑ 排除<br>□ 待定 |
| V20 | aircraft arms（机臂）<br>EG013 | remove the propeller holder and gimbal protector unfold the aircraft arms and turn | 标准排除；模型保留 | **排除**：只有通用展开动作，没有进一步结构、状态或配对信息。需与你保留同句桨叶固定器、云台保护罩的取舍相比较。 | □ 保留<br>☑ 排除<br>□ 待定 |
| V21 | 机臂（原文为striped arms）<br>EG016 | Check the curved markings - the striped arms get the striped propellers. | 标准排除；模型保留 | **保留（与标准不同）**：交代机臂标记与桨叶的装配配对，具有操作信息；可保留机臂，将条纹作为属性，不把条纹另建实体。 | □ 保留<br>☑ 排除<br>□ 待定 |
| V22 | Air 3机臂导线（drone's arm wires）<br>EG017 | To replace the ESC board, you need to desolder the drone's arm wires. | 标准排除；模型保留 | **保留（与标准不同）**：是换电调板前需要解焊的具体对象，所属设备和操作明确；与泛称工具、普通动作区分。 | □ 保留<br>☑ 排除<br>□ 待定 |
| V23 | Pro版本（模型补成了DJI Mini 3 Pro）<br>EG018 | The pro version and the mini 3 can now also shoot upwards past level horizon which neither of the<br>original Mavic mini or Mini 2 could do. | 标准排除；模型保留 | **待定**：本段原文只有pro version。可讨论是否保留局部泛称，但不能凭该词就认可具体Mini 3 Pro型号；此项兼有身份消歧问题。 | ☑ 保留<br>□ 排除<br>□ 待定 |
| V24 | True vertical shooting（竖拍）<br>EG018 | True vertical shooting which is a very welcome addition to both of these drones, sharing content<br>on social media in the native aspect ratio without having to crop out two thirds of the screen. | 标准排除；模型保留 | **保留（与标准不同）**：明确的拍摄能力并说明免裁切用途；建议与已保留的sport mode、HDR等能力保持一致。 | □ 保留<br>☑ 排除<br>□ 待定 |

## 确认前提出的口径比较（保留作记录）

- **推测与未具名原型**：V07、V08对应原型部件；EG005目前保留了Neo 3的推测部件。需要明确差别来自对象是否可指认、是否有实质信息，而不能简单按“可能/未发布”排除。
- **具名功能与属性**：V12慢动作、V24竖拍与现有HDR、sport mode作比较；V13、V14是画质指标。需决定功能实体与属性的边界。
- **具体维修对象与普通步骤**：V18底盖、V22导线与EG012已保留的电池/螺丝作比较；V20普通展开与V21装配配对信息也应区分。
- **缺型号与检索价值**：V16没有护目镜型号，但明确兼容Neo 2；EG013没有遥控器型号也已保留。缺型号可以影响作用域，不应自动等于没价值。
- **品牌名与公司主体**：V05、V15、V17只是名称中的品牌部分；EG017的“sending your drone to DJI service”则提供维修服务主体信息，当前golden保留组织DJI。

上述内容保留为当时的比较依据；用户已经逐项作出决定，不再作为待确认问题。此次决定只应用于本表对应的具体片段，不自动扩展到其他所有传感器、机臂或拍摄功能。

## 原片段入口

- **EG001** · [无人机还能不能飞？大疆Avata 360 ，室内VR全景拍摄生产力工具](https://www.youtube.com/watch?v=_6E4tJWhqBM) · 238.3–297.0秒
- **EG002** · [大疆的全景无人机？！Avata360上手](https://www.youtube.com/watch?v=UxbQRuKiwes) · 0.0–58.788秒
- **EG003** · [【3C老實說】DJI Avata 360 全景空拍機評測：人人都能一秒變身空拍高手，5大新玩法介紹、DJI Fly App / DJI Studio 操作教學](https://www.youtube.com/watch?v=BXiP0SVRezc) · 923.231–979.5秒
- **EG004** · [【Yipee科技新聞點評】 大疆 Neo 2 最強宿敵登場？Insta360 摺疊口袋無人機專利曝光！掌上自拍空拍機大洗牌！](https://www.youtube.com/watch?v=9bd--Zcc33M) · 12.533–68.5秒
- **EG005** · [【Yipee科技新聞點評】DJI Neo 3 發表時間與售價曝光｜全新 RC Mini 螢幕遙控器、懸吊電機結構、鏡頭升級全解析！對比 Neo 2 差在哪？最快 10 月發表！](https://www.youtube.com/watch?v=7NQZfyw-kNI) · 191.5–250.0秒
- **EG007** · [CP值最高的入門空拍機！DJI Lito X1 & Lito 1 完整評測｜免登記免考照、光達避障、D-Log M全都有 #dji  #DJILitoX1 #肯吉老王開箱](https://www.youtube.com/watch?v=2VqkvtTfK5g) · 61.833–121.266秒
- **EG008** · [2788元的新形态无人机，好用吗？DJI Flip上手！](https://www.youtube.com/watch?v=jrvhN6yNovA) · 268.397–328.157秒
- **EG010** · [Inside a DJI Store in China with Prices 🇨🇳 DJI Pocket 4P + MORE (2026)](https://www.youtube.com/watch?v=TtfVuyfODLU) · 737.84–795.2秒
- **EG012** · [Fix Your DJI Air 3 Drone Like a Pro: Gimbal Ribbon Cable Replacement Guide](https://www.youtube.com/watch?v=_VofSIgvXOc) · 40.32–89.92秒
- **EG013** · [DJI Mini 4 Pro｜First Use Guide](https://www.youtube.com/watch?v=JtYyP_y5Az0) · 4.08–58.8秒
- **EG016** · [Crashed? Here's How to Replace DJI Mini Drone Propellers / 3 and 4 Pro](https://www.youtube.com/watch?v=XUoFNFt8Mng) · 0.16–17.561秒
- **EG017** · [DJI Air 3: The Complete Disassembly and Reassembly Guide](https://www.youtube.com/watch?v=8k8JhMTr9xc) · 741.95–787.49秒
- **EG018** · [NEW DJI Mini 3 - The BEST Budget Drone EVER! (4K)](https://www.youtube.com/watch?v=RvSjkLzcFbw) · 115.86–161.56秒

修订结果：旧标准106条；删除EG001的桨叶、传感器2条；新增EG005的第一代DJI Neo、EG010的护目镜、EG018的Pro版本3条；摇杆原已保留。新版共107条、20段，中英文仍各10段。旧版JSON与历史评测均保留，改标准后的分数不能直接当作模型进步。

新版数据：[entity_gold_v3.json](../scripts/evaluation_data/entity_gold_v3.json)；旧数据：[entity_gold_v1.json](../scripts/evaluation_data/entity_gold_v1.json)（内部版本为entity-gold-v2）。此次仅确认这24项入图取舍，不代表全部通用规则或其他错误项已复核完成。
