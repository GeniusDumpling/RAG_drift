# 关系金标准评测

抽取版本：`video-graph-v2:deepseek-v4.1-flash-economy:c88ca83a8725-none-none`。
金标准：`relation-gold-v3`；指纹：`7f5a51a4acb86bd267b789e8e0427de322b53540d7b22797dd857d88cb9fcea3`。
评分规则：`relation-gold-endpoint-one-to-one-v1`；执行方式：`extractor`。
成功 20/20，失败 0，待运行 0。

测试完整抽取流程；标题和当前字幕是模型输入，实体及关系答案、models、notes不传给模型。
只评价金标准实体范围内的关系：端点按名称/原文提及与类型一对一对齐，不靠标准关系猜部件所有者。类型错误或归属歧义仍可产生FP；漏抽端点对应关系计FN。
预测关系去重后 62 条，范围内 55 条，范围外 7 条单列待审，不计TP/FP。因此本分数不是全图所有预测关系的准确率。
三元组要求两个实体的身份、类型、方向和谓词正确；兼容/连接允许反向，其他关系不允许。
断言指标再要求asserted/uncertain/negated一致。严格限定指标再要求全部qualifiers逐字归一后相同；缺省modality等价actual。限定同义但措辞/范围不同仍可能记为差异，需人工复核。
quote只检查来源支持，不要求与金标准选择完全相同的引文；本脚本不做LLM语义裁判。失败及待运行样本仍计入召回分母，未完成结果只是阶段结果。

| 范围 | 指标 | TP | FP | FN | Precision | Recall | F1 |
|---|---|---:|---:|---:|---:|---:|---:|
| 全部 | 三元组 | 47 | 8 | 30 | 85.5% | 61.0% | 71.2% |
| 全部 | 三元组+断言 | 45 | 10 | 32 | 81.8% | 58.4% | 68.2% |
| 全部 | 三元组+断言+限定（严格） | 38 | 17 | 39 | 69.1% | 49.4% | 57.6% |
| en | 三元组 | 18 | 3 | 18 | 85.7% | 50.0% | 63.2% |
| en | 三元组+断言 | 17 | 4 | 19 | 81.0% | 47.2% | 59.6% |
| en | 三元组+断言+限定（严格） | 12 | 9 | 24 | 57.1% | 33.3% | 42.1% |
| zh | 三元组 | 29 | 5 | 12 | 85.3% | 70.7% | 77.3% |
| zh | 三元组+断言 | 28 | 6 | 13 | 82.4% | 68.3% | 74.7% |
| zh | 三元组+断言+限定（严格） | 26 | 8 | 15 | 76.5% | 63.4% | 69.3% |

按谓词统计（三元组+断言）：

| 谓词 | TP | FP | FN | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|
| applied_to | 0 | 0 | 3 | — | 0.0% | 0.0% |
| compatible_with | 6 | 0 | 1 | 100.0% | 85.7% | 92.3% |
| connected_to | 0 | 1 | 2 | 0.0% | 0.0% | 0.0% |
| contains | 13 | 2 | 8 | 86.7% | 61.9% | 72.2% |
| controls | 1 | 0 | 0 | 100.0% | 100.0% | 100.0% |
| has_model | 4 | 1 | 1 | 80.0% | 80.0% | 80.0% |
| installed_on | 0 | 0 | 1 | — | 0.0% | 0.0% |
| mitigates | 0 | 0 | 1 | — | 0.0% | 0.0% |
| offered_by | 9 | 0 | 8 | 100.0% | 52.9% | 69.2% |
| packaged_with | 0 | 0 | 3 | — | 0.0% | 0.0% |
| reports_fault | 1 | 1 | 0 | 50.0% | 100.0% | 66.7% |
| supports | 8 | 3 | 4 | 72.7% | 66.7% | 69.6% |
| uses | 3 | 2 | 0 | 60.0% | 100.0% | 75.0% |

## EG001 · zh

[无人机还能不能飞？大疆Avata 360 ，室内VR全景拍摄生产力工具](https://www.youtube.com/watch?v=_6E4tJWhqBM)

漏抽关系（三元组+断言）：

- DJI Avata 360 → supports → 视觉避障 [asserted]
  - 限定：{}
  - 证据：避障Avata360有独立的视觉避障系统
- Avata 360桨叶保护罩 → mitigates → 炸机 [asserted]
  - 限定：{"modality": "capability"}
  - 证据：Avata360有桨叶保护的安全设计 ⏎ 被桨保包裹 ⏎ 不用担心飞转的桨叶伤人 ⏎ 更不怕碰到障碍物炸机

多抽/未匹配关系（三元组+断言）：

- DJI Avata 360 → contains → Avata 360 视觉避障系统 [asserted]
  - 限定：{}
  - 证据：避障Avata360有独立的视觉避障系统

范围外关系（未计分，待审）：

- 无。

限定差异（断言已匹配）：

- DJI Avata 360 → supports → 全向避障 [asserted]
  - 标准：{"modality": "conditional", "scope": "全向避障功能呢\n在拍摄360全景的时候会起作用"}
  - 预测：{}

## EG002 · zh

[大疆的全景无人机？！Avata360上手](https://www.youtube.com/watch?v=UxbQRuKiwes)

漏抽关系（三元组+断言）：

- 无。

多抽/未匹配关系（三元组+断言）：

- 无。

范围外关系（未计分，待审）：

- 无。


## EG003 · zh

[【3C老實說】DJI Avata 360 全景空拍機評測：人人都能一秒變身空拍高手，5大新玩法介紹、DJI Fly App / DJI Studio 操作教學](https://www.youtube.com/watch?v=BXiP0SVRezc)

漏抽关系（三元组+断言）：

- DJI Studio → applied_to → DJI Avata 360 [asserted]
  - 限定：{"modality": "instruction"}
  - 证据：也可以直接將 Avata 360 透過 USB 連接到電腦中。 ⏎ 我們首先開啟 DJI Studio， ⏎ 然後選擇設備連接。 ⏎ 現在已經顯示出我飛機裡頭所有檔案了， ⏎ 我就可以選影片出來做後製。點了之後跳出預覽， ⏎ 你可以做 360 度的操作， ⏎ 然後你可以直接下載或是點選創作。 ⏎ 偵測到文件在外部設備會建議你先把素材轉到電腦的本地磁碟再進行剪輯。

多抽/未匹配关系（三元组+断言）：

- DJI Avata 360 → connected_to → DJI Studio [asserted]
  - 限定：{}
  - 证据：也可以直接將 Avata 360 透過 USB 連接到電腦中。 ⏎ 我們首先開啟 DJI Studio， ⏎ 然後選擇設備連接。

范围外关系（未计分，待审）：

- 无。


## EG004 · zh

[【Yipee科技新聞點評】 大疆 Neo 2 最強宿敵登場？Insta360 摺疊口袋無人機專利曝光！掌上自拍空拍機大洗牌！](https://www.youtube.com/watch?v=9bd--Zcc33M)

漏抽关系（三元组+断言）：

- DJI Neo 2 → offered_by → DJI [asserted]
  - 限定：{}
  - 证据：Insta360 傳出摺疊空拍機直接對戰 DJI 的 Neo 2
- DJI Flip → offered_by → DJI [asserted]
  - 限定：{}
  - 证据：產品定位接近 DJI Neo 2 或 DJI Flip

多抽/未匹配关系（三元组+断言）：

- 无。

范围外关系（未计分，待审）：

- 无。


## EG005 · zh

[【Yipee科技新聞點評】DJI Neo 3 發表時間與售價曝光｜全新 RC Mini 螢幕遙控器、懸吊電機結構、鏡頭升級全解析！對比 Neo 2 差在哪？最快 10 月發表！](https://www.youtube.com/watch?v=7NQZfyw-kNI)

漏抽关系（三元组+断言）：

- DJI Neo 3 → offered_by → DJI [asserted]
  - 限定：{}
  - 证据：另外,DJI Neo 3 的相機模組看起來似乎比 Neo 2 更大
- DJI Neo 2 → offered_by → DJI [asserted]
  - 限定：{}
  - 证据：DJI Neo 2 則是在 2025 年 10 月底推出
- DJI Neo系列 → offered_by → DJI [asserted]
  - 限定：{}
  - 证据：依照 DJI Neo 系列過去的更新週期
- DJI Neo系列 → has_model → DJI Neo 3 [uncertain]
  - 限定：{"modality": "hypothetical", "scope": "按照這個週期推算\nDJI Neo 3 可能要等到今年 12 月或 2027 年初才會亮相"}
  - 证据：依照 DJI Neo 系列過去的更新週期 ⏎ 第一代 DJI Neo 在 2024 年 9 月推出 ⏎ DJI Neo 2 則是在 2025 年 10 月底推出 ⏎ 兩代相隔約 14 個月 ⏎ 按照這個週期推算 ⏎ DJI Neo 3 可能要等到今年 12 月或 2027 年初才會亮相
- DJI Neo 3 → contains → Neo 3镜头 [uncertain]
  - 限定：{"modality": "hypothetical", "scope": "另外,DJI Neo 3 的相機模組看起來似乎比 Neo 2 更大\n鏡頭向前突出的幅度也比前一代明顯"}
  - 证据：另外,DJI Neo 3 的相機模組看起來似乎比 Neo 2 更大 ⏎ 鏡頭向前突出的幅度也比前一代明顯
- DJI Neo 3 → contains → Neo 3感光元件 [uncertain]
  - 限定：{"modality": "hypothetical", "scope": "但由於曝光的包裝盒上沒有標示感光元件尺寸、錄影規格、重量與續航\n因此無法確認 DJI Neo 3 是否會繼續維持輕量化設計\n也無法證實先前傳出搭載 1/1.3 或 1/1.1 吋感光元件的消息是否屬實"}
  - 证据：外媒因此推測 DJI 可能重新設計 Neo 3 的雲台或相機系統 ⏎ 但由於曝光的包裝盒上沒有標示感光元件尺寸、錄影規格、重量與續航 ⏎ 因此無法確認 DJI Neo 3 是否會繼續維持輕量化設計 ⏎ 也無法證實先前傳出搭載 1/1.3 或 1/1.1 吋感光元件的消息是否屬實
- DJI Neo → offered_by → DJI [asserted]
  - 限定：{}
  - 证据：第一代 DJI Neo 在 2024 年 9 月推出

多抽/未匹配关系（三元组+断言）：

- DJI Neo series → has_model → DJI Neo 3 [asserted]
  - 限定：{}
  - 证据：第一代 DJI Neo 在 2024 年 9 月推出 ⏎ DJI Neo 2 則是在 2025 年 10 月底推出 ⏎ 兩代相隔約 14 個月 ⏎ 按照這個週期推算 ⏎ DJI Neo 3 可能要等到今年 12 月或 2027 年初才會亮相

范围外关系（未计分，待审）：

- 无。

限定差异（断言已匹配）：

- DJI Neo 3 → contains → Neo 3相机模组 [uncertain]
  - 标准：{"modality": "hypothetical", "scope": "另外,DJI Neo 3 的相機模組看起來似乎比 Neo 2 更大"}
  - 预测：{"modality": "hypothetical", "scope": "看起來似乎"}

## EG006 · zh

[2026 新手無人機別亂買！DJI Neo 2 vs Lito 1 vs Lito X1 誰才是性價比之王？](https://www.youtube.com/watch?v=A9n3aAoDDBg)

漏抽关系（三元组+断言）：

- 无。

多抽/未匹配关系（三元组+断言）：

- 无。

范围外关系（未计分，待审）：

- 1/1.3 英寸 CMOS 感光元件 → uses → CMOS [asserted]
  - 限定：{}
  - 证据：感光元件为 1/1.3 英寸 CMOS
- DJI Mini 4 Pro → contains → 1/1.3 英寸 CMOS 感光元件 [asserted]
  - 限定：{}
  - 证据：感光元件为 1/1.3 英寸 CMOS
- DJI Neo 2 → uses → 1/1.3 英寸 CMOS 感光元件 [asserted]
  - 限定：{}
  - 证据：和 Neo 2 是一样的


## EG007 · zh

[CP值最高的入門空拍機！DJI Lito X1 & Lito 1 完整評測｜免登記免考照、光達避障、D-Log M全都有 #dji  #DJILitoX1 #肯吉老王開箱](https://www.youtube.com/watch?v=2VqkvtTfK5g)

漏抽关系（三元组+断言）：

- 无。

多抽/未匹配关系（三元组+断言）：

- 无。

范围外关系（未计分，待审）：

- 无。


## EG008 · zh

[2788元的新形态无人机，好用吗？DJI Flip上手！](https://www.youtube.com/watch?v=jrvhN6yNovA)

漏抽关系（三元组+断言）：

- DJI Mavic 3 Pro → offered_by → 大疆 [asserted]
  - 限定：{}
  - 证据：它是大疆Mavic 3 Pro的中焦镜头

多抽/未匹配关系（三元组+断言）：

- DJI Flip 传感器 → uses → DJI Mini 4 Pro [asserted]
  - 限定：{}
  - 证据：Mini 3、Mini 4 Pro的广角
- DJI Flip 传感器 → uses → DJI Action 4 [uncertain]
  - 限定：{"modality": "hypothetical", "scope": "Action 4还有那些别的各种机型\n都有可能用到这块传感器"}
  - 证据：Action 4还有那些别的各种机型 ⏎ 都有可能用到这块传感器
- DJI Flip → supports → HDR [asserted]
  - 限定：{}
  - 证据：它目前最高支持拍摄4K 60帧的HDR视频

范围外关系（未计分，待审）：

- 无。


## EG009 · zh

[从豁然开朗到“死黑一片”：这台Mini 4K无人机，怎么好像也有点“近视”？ #广东麦克疯#越南 #航拍機 #老挝旅游 #越南旅遊](https://www.youtube.com/watch?v=7MJjAlpvj7w)

漏抽关系（三元组+断言）：

- 无。

多抽/未匹配关系（三元组+断言）：

- 无。

范围外关系（未计分，待审）：

- 无。


## EG010 · zh

[Inside a DJI Store in China with Prices 🇨🇳 DJI Pocket 4P + MORE (2026)](https://www.youtube.com/watch?v=TtfVuyfODLU)

漏抽关系（三元组+断言）：

- 无。

多抽/未匹配关系（三元组+断言）：

- 无。

范围外关系（未计分，待审）：

- DJI Mavic 4 ⏎ Pro → offered_by → DJI [asserted]
  - 限定：{}
  - 证据：DJI Mavic 4 ⏎ Pro
- DJI Neo 2 → offered_by → DJI [asserted]
  - 限定：{}
  - 证据：我们的无人机 Neo 2


## EG011 · en

[How to Trippy Spin with DJI Neo 2 (FPV Stick Tutorial) 🛸​#DJINeo2 #TrippySpin  #DJIFPV #djineo](https://www.youtube.com/watch?v=qAgye8oJBKA)

漏抽关系（三元组+断言）：

- Trippy Spin → applied_to → DJI Neo 2 [asserted]
  - 限定：{"modality": "instruction"}
  - 证据：How to perform a trippy spin with DJI Neo 2. [screaming] The obstacle must ⏎ be at least 5 m high. Camera is [music] pan to 30°. Punch up and pitch down when ⏎ nearing the pole. Throttle diagonally and roll to the side. Focus on pole ⏎ and adjust sticks accordingly. When done, lower the speed and level up.

多抽/未匹配关系（三元组+断言）：

- DJI Neo 2 → supports → trippy spin [asserted]
  - 限定：{"modality": "instruction"}
  - 证据：How to perform a trippy spin with DJI Neo 2.

范围外关系（未计分，待审）：

- 无。


## EG012 · en

[Fix Your DJI Air 3 Drone Like a Pro: Gimbal Ribbon Cable Replacement Guide](https://www.youtube.com/watch?v=_VofSIgvXOc)

漏抽关系（三元组+断言）：

- DJI Air 3 → contains → drone body  [asserted]
  - 限定：{}
  - 证据：approach will greatly assist you in successfully reassembling your DJI Air 3 drone. And you need ⏎ not worry about misplacing any screws; they will all stay securely on the magnetic mat. ⏎ Remove the battery before you start disassembling the DJI Air 3 drone. ⏎ Flip your drone over and remove the screws securing the bottom cover. ⏎ Remember, each screw might be a slightly different size, so keep track of where they go. ⏎ Use plastic tools to carefully open the drone body and ensure safe handling.
- DJI Air 3 Screws → connected_to → bottom cover [asserted]
  - 限定：{}
  - 证据：approach will greatly assist you in successfully reassembling your DJI Air 3 drone. And you need ⏎ not worry about misplacing any screws; they will all stay securely on the magnetic mat. ⏎ Remove the battery before you start disassembling the DJI Air 3 drone. ⏎ Flip your drone over and remove the screws securing the bottom cover.

多抽/未匹配关系（三元组+断言）：

- bottom cover → contains → screws [asserted]
  - 限定：{"modality": "instruction"}
  - 证据：Flip your drone over and remove the screws securing the bottom cover.

范围外关系（未计分，待审）：

- 无。

限定差异（断言已匹配）：

- DJI Air 3 → contains → DJI Air 3 Battery [asserted]
  - 标准：{}
  - 预测：{"modality": "instruction"}
- DJI Air 3 → contains → DJI Air 3 Screws [asserted]
  - 标准：{}
  - 预测：{"modality": "instruction"}
- DJI Air 3 → contains → bottom cover [asserted]
  - 标准：{}
  - 预测：{"modality": "instruction"}

## EG013 · en

[DJI Mini 4 Pro｜First Use Guide](https://www.youtube.com/watch?v=JtYyP_y5Az0)

漏抽关系（三元组+断言）：

- DJI Mini 4 Pro → packaged_with → remote controller [asserted]
  - 限定：{}
  - 证据：welcome to DJI Mini 4 Pro tutorial ⏎ we will guide you through the unboxing and first use process of the product ⏎ open the package and take out the aircraft remote controller and accessories
- DJI Fly → applied_to → DJI Mini 4 Pro [asserted]
  - 限定：{"modality": "instruction"}
  - 证据：welcome to DJI Mini 4 Pro tutorial ⏎ we will guide you through the unboxing and first use process of the product ⏎ open the package and take out the aircraft remote controller and accessories ⏎ downloading the DJI fly app scan the QR code on the package to download and install the DJI fly ⏎ app you can also watch the tutorial videos and read the user manual after scanning the QR code
- DJI Mini 4 Pro → packaged_with → propeller holder [asserted]
  - 限定：{}
  - 证据：welcome to DJI Mini 4 Pro tutorial ⏎ we will guide you through the unboxing and first use process of the product ⏎ open the package and take out the aircraft remote controller and accessories ⏎ downloading the DJI fly app scan the QR code on the package to download and install the DJI fly ⏎ app you can also watch the tutorial videos and read the user manual after scanning the QR code ⏎ remove the propeller holder and gimbal protector unfold the aircraft arms and turn
- DJI Mini 4 Pro → packaged_with → gimbal protector [asserted]
  - 限定：{}
  - 证据：welcome to DJI Mini 4 Pro tutorial ⏎ we will guide you through the unboxing and first use process of the product ⏎ open the package and take out the aircraft remote controller and accessories ⏎ downloading the DJI fly app scan the QR code on the package to download and install the DJI fly ⏎ app you can also watch the tutorial videos and read the user manual after scanning the QR code ⏎ remove the propeller holder and gimbal protector unfold the aircraft arms and turn

多抽/未匹配关系（三元组+断言）：

- 无。

范围外关系（未计分，待审）：

- 无。


## EG014 · en

[DJI Made Flipping a Drone Look This Easy](https://www.youtube.com/watch?v=AWQJTxxNpAI)

漏抽关系（三元组+断言）：

- 无。

多抽/未匹配关系（三元组+断言）：

- 无。

范围外关系（未计分，待审）：

- 无。


## EG015 · en

[Parrot Anafi 4k Drone - Photographer's  Review](https://www.youtube.com/watch?v=8PjamSNj4BA)

漏抽关系（三元组+断言）：

- Parrot ANAFI → compatible_with → ANAFI遥控器 [asserted]
  - 限定：{}
  - 证据：This is all that I use for the Parrot Anafi you've got the charging ⏎ cable USB "C" I've got a spare battery I've got the drone itself ⏎ I've got the USB cable for the iPhone or the whichever phone you have and then I've got the controller

多抽/未匹配关系（三元组+断言）：

- 无。

范围外关系（未计分，待审）：

- 无。


## EG016 · en

[Crashed? Here's How to Replace DJI Mini Drone Propellers / 3 and 4 Pro](https://www.youtube.com/watch?v=XUoFNFt8Mng)

漏抽关系（三元组+断言）：

- DJI Mini 3 → contains → propellers [asserted]
  - 限定：{}
  - 证据：Crashed your DJI Mini 3 or 4 drone? ⏎ Let's fix it by replacing the propellers. ⏎ Here's the key: they're mirrored, so two turn clockwise and two counterclockwise. ⏎ Check the curved markings - the striped arms get the striped propellers.
- DJI Mini 3 → contains → striped arms [asserted]
  - 限定：{}
  - 证据：Crashed your DJI Mini 3 or 4 drone? ⏎ Let's fix it by replacing the propellers. ⏎ Here's the key: they're mirrored, so two turn clockwise and two counterclockwise. ⏎ Check the curved markings - the striped arms get the striped propellers.
- DJI Mini 3 → contains → striped propellers [asserted]
  - 限定：{}
  - 证据：Crashed your DJI Mini 3 or 4 drone? ⏎ Let's fix it by replacing the propellers. ⏎ Here's the key: they're mirrored, so two turn clockwise and two counterclockwise. ⏎ Check the curved markings - the striped arms get the striped propellers.
- striped propellers → installed_on → striped arms [asserted]
  - 限定：{"modality": "instruction"}
  - 证据：Check the curved markings - the striped arms get the striped propellers.

多抽/未匹配关系（三元组+断言）：

- 无。

范围外关系（未计分，待审）：

- 无。


## EG017 · en

[DJI Air 3: The Complete Disassembly and Reassembly Guide](https://www.youtube.com/watch?v=8k8JhMTr9xc)

漏抽关系（三元组+断言）：

- DJI Air 3 → contains → Air 3 motors [asserted]
  - 限定：{}
  - 证据：The ESC (Electronic Speed Controllers) board controls the motors which directly alters ⏎ the in-flight drone directional changes depending on the commands. ⏎ If your DJI Air 3 drone has an ESC error and you can't fix this error by refreshing the ⏎ drone firmware, it is most likely the ESC board is failing, so you need to replace it.
- DJI Air 3 → offered_by → DJI [asserted]
  - 限定：{}
  - 证据：If your DJI Air 3 drone has an ESC error and you can't fix this error by refreshing the
- DJI Air 3 → contains → DJI Air 3 arm wires [asserted]
  - 限定：{}
  - 证据：If your DJI Air 3 drone has an ESC error and you can't fix this error by refreshing the ⏎ drone firmware, it is most likely the ESC board is failing, so you need to replace it. ⏎ To replace the ESC board, you need to desolder the drone's arm wires.
- DJI Air 3 arm wires → connected_to → Air 3电调板 [asserted]
  - 限定：{}
  - 证据：To replace the ESC board, you need to desolder the drone's arm wires.

多抽/未匹配关系（三元组+断言）：

- DJI Air 3 ESC board → reports_fault → DJI Air 3 ESC error [uncertain]
  - 限定：{"modality": "conditional", "scope": "If your DJI Air 3 drone has an ESC error and you can't fix this error by refreshing the\ndrone firmware, it is most likely the ESC board is failing"}
  - 证据：it is most likely the ESC board is failing, so you need to replace it.

范围外关系（未计分，待审）：

- 无。

限定差异（断言已匹配）：

- DJI Air 3 → uses → firmware [asserted]
  - 标准：{}
  - 预测：{"modality": "conditional", "scope": "If your DJI Air 3 drone has an ESC error and you can't fix this error by refreshing the\ndrone firmware"}
- DJI Air 3 → reports_fault → ESC error [asserted]
  - 标准：{"modality": "conditional", "scope": "If your DJI Air 3 drone has an ESC error and you can't fix this error by refreshing the\ndrone firmware, it is most likely the ESC board is failing, so you need to replace it."}
  - 预测：{"modality": "conditional", "scope": "If your DJI Air 3 drone has an ESC error and you can't fix this error by refreshing the\ndrone firmware"}

## EG018 · en

[NEW DJI Mini 3 - The BEST Budget Drone EVER! (4K)](https://www.youtube.com/watch?v=RvSjkLzcFbw)

漏抽关系（三元组+断言）：

- DJI Mini 3 → supports → True vertical shooting [asserted]
  - 限定：{}
  - 证据：True vertical shooting which is a very welcome addition to both of these drones, sharing content ⏎ on social media in the native aspect ratio without having to crop out two thirds of the screen. ⏎ The pro version and the mini 3 can now also shoot upwards past level horizon which neither of the ⏎ original Mavic mini or Mini 2 could do. This is great for capturing climbing shots and low angles. ⏎ So what else do they have in common. They both fly at the same speed at 16 ⏎ meters per second in sport mode. Thats 24mph. Let’s see what 24mph looks like!
- Pro版本 → supports → True vertical shooting [asserted]
  - 限定：{}
  - 证据：True vertical shooting which is a very welcome addition to both of these drones, sharing content ⏎ on social media in the native aspect ratio without having to crop out two thirds of the screen. ⏎ The pro version and the mini 3 can now also shoot upwards past level horizon which neither of the ⏎ original Mavic mini or Mini 2 could do. This is great for capturing climbing shots and low angles. ⏎ So what else do they have in common. They both fly at the same speed at 16 ⏎ meters per second in sport mode. Thats 24mph. Let’s see what 24mph looks like!

多抽/未匹配关系（三元组+断言）：

- 无。

范围外关系（未计分，待审）：

- DJI Mini 3 Pro → supports → vertical shooting [asserted]
  - 限定：{"modality": "capability"}
  - 证据：The pro version and the mini 3 can now also shoot upwards past level horizon
- DJI Mini 3 → supports → vertical shooting [asserted]
  - 限定：{"modality": "capability"}
  - 证据：The pro version and the mini 3 can now also shoot upwards past level horizon


## EG019 · en

[Avata 360 Single-Lens Mode | Struggling with Head Tracking + Motion 3 | Turn On Caption (CC)](https://www.youtube.com/watch?v=sjEluw4GIqI)

漏抽关系（三元组+断言）：

- DJI Avata 360 → supports → Head Tracking [uncertain]
  - 限定：{"modality": "hypothetical", "scope": "I'm nodding and turning my head to check how Head Tracking moves the camera and drone.", "mode": "single-lens mode"}
  - 证据：Today's experiment: Avata 360 in single-lens mode, with RC Motion 3 and Head Tracking. ⏎ The Avata 360 takes off in spherical mode. I switch to single-lens mode after takeoff. ⏎ I'm nodding and turning my head to check how Head Tracking moves the camera and drone. ⏎ Checking that Head Tracking is enabled.

多抽/未匹配关系（三元组+断言）：

- Avata 360 → supports → Head Tracking [asserted]
  - 限定：{}
  - 证据：I'm nodding and turning my head to check how Head Tracking moves the camera and drone.

范围外关系（未计分，待审）：

- 无。


## EG020 · en

[DJI Mini 5 Pro engel algılama testi: teller, yapraklar ve yağmur! 🚁](https://www.youtube.com/watch?v=ZqTrQBWrol0)

漏抽关系（三元组+断言）：

- 无。

多抽/未匹配关系（三元组+断言）：

- 无。

范围外关系（未计分，待审）：

- 无。

