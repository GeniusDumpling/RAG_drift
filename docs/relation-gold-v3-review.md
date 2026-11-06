# 无人机关系金标准集复核稿

状态：已人工复核。20个片段（中文、英文各10段），77条关系。

沿用实体金标准集的原文、来源和实体答案；JSON是编辑入口，本文件由脚本生成。
复核：是否漏标/多标，方向和谓词是否准确，原文是否支持，条件与不确定性是否保留。
asserted仅表示来源明确陈述，不代表外部事实已核实。未标关系不等于明确否定。

## EG001 · 中文

涉及型号：DJI Avata 360。

[无人机还能不能飞？大疆Avata 360 ，室内VR全景拍摄生产力工具](https://www.youtube.com/watch?v=_6E4tJWhqBM) · 238.3–297.0秒

```text
20分钟之内
咱能把这活干完了
Avata360有桨叶保护的安全设计
被桨保包裹
不用担心飞转的桨叶伤人
更不怕碰到障碍物炸机
Avatar系列在低空或是
近距离拍摄的时候呢
不会有任何的心理压力
在拍摄现场
除了声音有点吵以外的话
不会影响到任何人
但是你要在飞的时候
一定要小心啊
下边别有什么绿植啊窗帘
什么的卷到这个桨叶里边
避障Avata360有独立的视觉避障系统
下面呢还有传感器
全向避障功能呢
在拍摄360全景的时候会起作用
提示你四面八方潜在的障碍物
并且做出提示
甚至是刹停来确保安全
```

| 已确认实体 | 类型 | 原文提及 |
|---|---|---|
| DJI Avata 360 | 产品/设备 | Avata360 |
| DJI Avata系列 | 产品系列 | Avatar系列 |
| Avata 360桨叶保护罩 | 硬件部件 | 桨叶保护；桨保 |
| 视觉避障 | 技术/功能 | 视觉避障系统 |
| 全向避障 | 技术/功能 | 全向避障功能 |
| DJI Avata 360 下方传感器 | 硬件部件 | 传感器 |
| 炸机 | 故障 | 炸机 |

| 主体 | 关系 | 客体 | 断言 | 限定 | 证据原文 |
|---|---|---|---|---|---|
| DJI Avata 360 | contains（组成/功能包含） | Avata 360桨叶保护罩 | asserted | — | Avata360有桨叶保护的安全设计 ⏎ 被桨保包裹 ⏎ 不用担心飞转的桨叶伤人 |
| DJI Avata 360 | contains（组成/功能包含） | DJI Avata 360 下方传感器 | asserted | — | 避障Avata360有独立的视觉避障系统 ⏎ 下面呢还有传感器 |
| DJI Avata 360 | supports（支持功能/协议） | 视觉避障 | asserted | — | 避障Avata360有独立的视觉避障系统 |
| DJI Avata 360 | supports（支持功能/协议） | 全向避障 | asserted | modality=conditional；scope=全向避障功能呢 ⏎ 在拍摄360全景的时候会起作用 | 避障Avata360有独立的视觉避障系统 ⏎ 下面呢还有传感器 ⏎ 全向避障功能呢 ⏎ 在拍摄360全景的时候会起作用 ⏎ 提示你四面八方潜在的障碍物 ⏎ 并且做出提示 ⏎ 甚至是刹停来确保安全 |
| Avata 360桨叶保护罩 | mitigates（缓解故障） | 炸机 | asserted | modality=capability | Avata360有桨叶保护的安全设计 ⏎ 被桨保包裹 ⏎ 不用担心飞转的桨叶伤人 ⏎ 更不怕碰到障碍物炸机 |

复核说明：

- 实体严格沿用当前entity_gold_v3：保留桨保、下方传感器、炸机及两种避障能力；不补回已移除的桨叶。Avatar系列仍按已确认的Avata系列处理，不改写字幕。
- 关系标注：原传感器contains关系改指DJI Avata 360 下方传感器；桨保缓解碰撞炸机风险标mitigates、capability，表示来源描述的保护能力，不是设备已经炸机，也不标否定故障。该风险缓解关系已随本轮人工复核确认。
- 关系标注：全向避障仍保留拍摄360全景的条件；不凭规范名称补系列归属，保护罩包裹桨叶不自动标为组成关系。

## EG002 · 中文

涉及型号：DJI Avata 360、DJI Avata。

[大疆的全景无人机？！Avata360上手](https://www.youtube.com/watch?v=UxbQRuKiwes) · 0.0–58.788秒

```text
去年我们就说过
全景无人机这个市场
今年应该会变得热闹起来
但是谁能想到
大疆2026年开年首作就是全景无人机
这个Avata360
他们打磨了挺长的时间了
那么飞下来感觉就这个无人机
它的画质很好
有着我们熟悉的摇杆操作
也还可以用传统的遥控器来飞行
这都在我们预期之内
但什么在我们预期之外
是它的价格
这玩意单机身卖2788元
一架无人机有着超大的传感器
卖2788元
没错这个价位
它放了两块
1/1.1英寸的方形传感器
我不知道你下巴有没有合上
所以就让我们一起来看看吧
大疆做的第一架全景无人机
有什么过人之处
来个英雄镜头吧
首先它的外形
你乍一看和之前的Avata是比较相似的
```

| 已确认实体 | 类型 | 原文提及 |
|---|---|---|
| DJI Avata 360 | 产品/设备 | Avata360 |
| DJI Avata系列 | 产品系列 | Avata |
| 大疆 | 组织 | 大疆 |
| Avata 360方形传感器 | 硬件部件 | 传感器；方形传感器 |

| 主体 | 关系 | 客体 | 断言 | 限定 | 证据原文 |
|---|---|---|---|---|---|
| DJI Avata 360 | offered_by（所属公司/品牌） | 大疆 | asserted | — | 大疆2026年开年首作就是全景无人机 ⏎ 这个Avata360 ⏎ 他们打磨了挺长的时间了 |
| DJI Avata 360 | contains（组成/功能包含） | Avata 360方形传感器 | asserted | — | 这个Avata360 ⏎ 他们打磨了挺长的时间了 ⏎ 那么飞下来感觉就这个无人机 ⏎ 它的画质很好 ⏎ 有着我们熟悉的摇杆操作 ⏎ 也还可以用传统的遥控器来飞行 ⏎ 这都在我们预期之内 ⏎ 但什么在我们预期之外 ⏎ 是它的价格 ⏎ 这玩意单机身卖2788元 ⏎ 一架无人机有着超大的传感器 ⏎ 卖2788元 ⏎ 没错这个价位 ⏎ 它放了两块 ⏎ 1/1.1英寸的方形传感器 |

复核说明：

- 实体严格沿用v3：具体型号、Avata系列、大疆和方形传感器；摇杆及无型号遥控器已不在答案内。价格与1/1.1英寸仍是属性。
- 关系标注：删除摇杆/遥控器controls无人机的两条旧关系，只保留品牌和方形传感器组成关系；不补回已删端点。

## EG003 · 中文

涉及型号：DJI Avata 360。

[【3C老實說】DJI Avata 360 全景空拍機評測：人人都能一秒變身空拍高手，5大新玩法介紹、DJI Fly App / DJI Studio 操作教學](https://www.youtube.com/watch?v=BXiP0SVRezc) · 923.231–979.5秒

```text
過去對 360 後製存在一些困擾或覺得麻煩的人，
用這個功能我相信是非常方便的。
接下來展示用電腦應用。你可以直接讀卡內容，
也可以直接將 Avata 360 透過 USB 連接到電腦中。
我們首先開啟 DJI Studio，
然後選擇設備連接。
現在已經顯示出我飛機裡頭所有檔案了，
我就可以選影片出來做後製。點了之後跳出預覽，
你可以做 360 度的操作，
然後你可以直接下載或是點選創作。
偵測到文件在外部設備會建議你先把素材轉到電腦的本地磁碟再進行剪輯。
這邊已經把素材導入進來了，
這是一支 360 畫面的全景影片，
```

| 已确认实体 | 类型 | 原文提及 |
|---|---|---|
| DJI Avata 360 | 产品/设备 | Avata 360 |
| DJI Studio | 软件 | DJI Studio |

| 主体 | 关系 | 客体 | 断言 | 限定 | 证据原文 |
|---|---|---|---|---|---|
| DJI Studio | applied_to（应用对象） | DJI Avata 360 | asserted | modality=instruction | 也可以直接將 Avata 360 透過 USB 連接到電腦中。 ⏎ 我們首先開啟 DJI Studio， ⏎ 然後選擇設備連接。 ⏎ 現在已經顯示出我飛機裡頭所有檔案了， ⏎ 我就可以選影片出來做後製。點了之後跳出預覽， ⏎ 你可以做 360 度的操作， ⏎ 然後你可以直接下載或是點選創作。 ⏎ 偵測到文件在外部設備會建議你先把素材轉到電腦的本地磁碟再進行剪輯。 |

复核说明：

- 本次人工答案仅保留Avata 360和DJI Studio；其他原文对象不由notes补回。
- 设备连接、创作等界面操作不另建软件实体。
- 关系标注：DJI Studio用于该无人机的设备连接、素材导入及后制流程，标applied_to；不是安装在无人机上的软件，也未说明能控制飞行。

## EG004 · 中文

涉及型号：DJI Neo 2、DJI Flip、Antigravity A1。

[【Yipee科技新聞點評】 大疆 Neo 2 最強宿敵登場？Insta360 摺疊口袋無人機專利曝光！掌上自拍空拍機大洗牌！](https://www.youtube.com/watch?v=9bd--Zcc33M) · 12.533–68.5秒

```text
哈囉哈囉大家好,我是柏青哥
Insta360 傳出摺疊空拍機直接對戰 DJI 的 Neo 2
我們來看怎麼回事吧
Insta360 進軍消費級無人機市場
雖然目前只推出 360 度空拍機 Antigravity A1
但近期外媒發現 Insta360 在中國取得摺疊無人機專利
外型似乎與《The New Camera》在2025年8月19日
曝光的神祕無人機原型相似
從目前線索來看
這款無人機可能具備摺疊機臂、環形螺旋槳保護罩
並採用輕量化設計
產品定位接近 DJI Neo 2 或 DJI Flip
不過,由於 Insta360 尚未公佈這款產品的相關消息
因此目前停留在外界推測階段
```

| 已确认实体 | 类型 | 原文提及 |
|---|---|---|
| DJI Neo 2 | 产品/设备 | Neo 2 |
| DJI Flip | 产品/设备 | DJI Flip |
| Insta360 Antigravity A1 | 产品/设备 | Antigravity A1 |
| Insta360 | 组织 | Insta360 |
| DJI | 组织 | DJI |

| 主体 | 关系 | 客体 | 断言 | 限定 | 证据原文 |
|---|---|---|---|---|---|
| DJI Neo 2 | offered_by（所属公司/品牌） | DJI | asserted | — | Insta360 傳出摺疊空拍機直接對戰 DJI 的 Neo 2 |
| DJI Flip | offered_by（所属公司/品牌） | DJI | asserted | — | 產品定位接近 DJI Neo 2 或 DJI Flip |
| Insta360 Antigravity A1 | offered_by（所属公司/品牌） | Insta360 | asserted | — | Insta360 進軍消費級無人機市場 ⏎ 雖然目前只推出 360 度空拍機 Antigravity A1 |

复核说明：

- 标准答案保留明确型号及Insta360、DJI组织，Antigravity A1规范名采用本轮人工修订。
- 原型、报道媒体、主持人和泛称部件本次未纳入实体答案；日期、360度不是实体。
- 关系标注：品牌/推出方标offered_by；定位接近、对战和外形相似均不表示兼容。未命名原型及其推测部件不在已复核实体范围内。

## EG005 · 中文

涉及型号：DJI Neo 3、DJI Neo 2、DJI Neo。

[【Yipee科技新聞點評】DJI Neo 3 發表時間與售價曝光｜全新 RC Mini 螢幕遙控器、懸吊電機結構、鏡頭升級全解析！對比 Neo 2 差在哪？最快 10 月發表！](https://www.youtube.com/watch?v=7NQZfyw-kNI) · 191.5–250.0秒

```text
另外,DJI Neo 3 的相機模組看起來似乎比 Neo 2 更大
鏡頭向前突出的幅度也比前一代明顯
外媒因此推測 DJI 可能重新設計 Neo 3 的雲台或相機系統
但由於曝光的包裝盒上沒有標示感光元件尺寸、錄影規格、重量與續航
因此無法確認 DJI Neo 3 是否會繼續維持輕量化設計
也無法證實先前傳出搭載 1/1.3 或 1/1.1 吋感光元件的消息是否屬實
那麼 DJI Neo 3 預期什麼時候會發表呢?
依照 DJI Neo 系列過去的更新週期
第一代 DJI Neo 在 2024 年 9 月推出
DJI Neo 2 則是在 2025 年 10 月底推出
兩代相隔約 14 個月
按照這個週期推算
DJI Neo 3 可能要等到今年 12 月或 2027 年初才會亮相
```

| 已确认实体 | 类型 | 原文提及 |
|---|---|---|
| DJI Neo 3 | 产品/设备 | DJI Neo 3；Neo 3 |
| DJI Neo 2 | 产品/设备 | Neo 2；DJI Neo 2 |
| DJI Neo系列 | 产品系列 | DJI Neo 系列 |
| DJI | 组织 | DJI |
| Neo 3相机模组 | 硬件部件 | 相機模組 |
| Neo 3镜头 | 硬件部件 | 鏡頭 |
| Neo 3云台 | 硬件部件 | 雲台 |
| Neo 3相机系统 | 硬件部件 | 相機系統 |
| Neo 3感光元件 | 硬件部件 | 感光元件 |
| DJI Neo | 产品/设备 | 第一代 DJI Neo |

| 主体 | 关系 | 客体 | 断言 | 限定 | 证据原文 |
|---|---|---|---|---|---|
| DJI Neo 3 | offered_by（所属公司/品牌） | DJI | asserted | — | 另外,DJI Neo 3 的相機模組看起來似乎比 Neo 2 更大 |
| DJI Neo 2 | offered_by（所属公司/品牌） | DJI | asserted | — | DJI Neo 2 則是在 2025 年 10 月底推出 |
| DJI Neo系列 | offered_by（所属公司/品牌） | DJI | asserted | — | 依照 DJI Neo 系列過去的更新週期 |
| DJI Neo系列 | has_model（系列型号） | DJI Neo 2 | asserted | — | 依照 DJI Neo 系列過去的更新週期 ⏎ 第一代 DJI Neo 在 2024 年 9 月推出 ⏎ DJI Neo 2 則是在 2025 年 10 月底推出 ⏎ 兩代相隔約 14 個月 |
| DJI Neo系列 | has_model（系列型号） | DJI Neo 3 | uncertain | modality=hypothetical；scope=按照這個週期推算 ⏎ DJI Neo 3 可能要等到今年 12 月或 2027 年初才會亮相 | 依照 DJI Neo 系列過去的更新週期 ⏎ 第一代 DJI Neo 在 2024 年 9 月推出 ⏎ DJI Neo 2 則是在 2025 年 10 月底推出 ⏎ 兩代相隔約 14 個月 ⏎ 按照這個週期推算 ⏎ DJI Neo 3 可能要等到今年 12 月或 2027 年初才會亮相 |
| DJI Neo 3 | contains（组成/功能包含） | Neo 3相机模组 | uncertain | modality=hypothetical；scope=另外,DJI Neo 3 的相機模組看起來似乎比 Neo 2 更大 | 另外,DJI Neo 3 的相機模組看起來似乎比 Neo 2 更大 |
| DJI Neo 3 | contains（组成/功能包含） | Neo 3镜头 | uncertain | modality=hypothetical；scope=另外,DJI Neo 3 的相機模組看起來似乎比 Neo 2 更大 ⏎ 鏡頭向前突出的幅度也比前一代明顯 | 另外,DJI Neo 3 的相機模組看起來似乎比 Neo 2 更大 ⏎ 鏡頭向前突出的幅度也比前一代明顯 |
| DJI Neo 3 | contains（组成/功能包含） | Neo 3云台 | uncertain | modality=hypothetical；scope=外媒因此推測 DJI 可能重新設計 Neo 3 的雲台或相機系統 | 外媒因此推測 DJI 可能重新設計 Neo 3 的雲台或相機系統 |
| DJI Neo 3 | contains（组成/功能包含） | Neo 3相机系统 | uncertain | modality=hypothetical；scope=外媒因此推測 DJI 可能重新設計 Neo 3 的雲台或相機系統 | 外媒因此推測 DJI 可能重新設計 Neo 3 的雲台或相機系統 |
| DJI Neo 3 | contains（组成/功能包含） | Neo 3感光元件 | uncertain | modality=hypothetical；scope=但由於曝光的包裝盒上沒有標示感光元件尺寸、錄影規格、重量與續航 ⏎ 因此無法確認 DJI Neo 3 是否會繼續維持輕量化設計 ⏎ 也無法證實先前傳出搭載 1/1.3 或 1/1.1 吋感光元件的消息是否屬實 | 外媒因此推測 DJI 可能重新設計 Neo 3 的雲台或相機系統 ⏎ 但由於曝光的包裝盒上沒有標示感光元件尺寸、錄影規格、重量與續航 ⏎ 因此無法確認 DJI Neo 3 是否會繼續維持輕量化設計 ⏎ 也無法證實先前傳出搭載 1/1.3 或 1/1.1 吋感光元件的消息是否屬實 |
| DJI Neo | offered_by（所属公司/品牌） | DJI | asserted | — | 第一代 DJI Neo 在 2024 年 9 月推出 |
| DJI Neo系列 | has_model（系列型号） | DJI Neo | asserted | — | 依照 DJI Neo 系列過去的更新週期 ⏎ 第一代 DJI Neo 在 2024 年 9 月推出 ⏎ DJI Neo 2 則是在 2025 年 10 月底推出 ⏎ 兩代相隔約 14 個月 |

复核说明：

- v3保留第一代DJI Neo：有明确推出日期及代际对比；日期本身不另建实体。Neo 3仍包含曝光、推测、未证实配置及未来发布信息。
- 关系标注：沿用旧版Neo 3关系的不确定性；新增DJI Neo offered_by DJI、DJI Neo系列 has_model DJI Neo两条明确关系，依据为系列更新周期及第一代推出句。
- 关系标注：无法确认不等于否定；不补相机模组、镜头、云台之间未明示的层级。

## EG006 · 中文

涉及型号：DJI Mini 4 Pro、DJI Neo 2。

[2026 新手無人機別亂買！DJI Neo 2 vs Lito 1 vs Lito X1 誰才是性價比之王？](https://www.youtube.com/watch?v=A9n3aAoDDBg) · 249.94–309.24秒

```text
它们本身就是无人机
正在收音
大家可以看到 Neo 2 也在跟着我
哎哟差点断连
你光顾着看相机没在录像
跟大家讲讲 Mini 4 Pro 的规格
它定位为可折叠入门级无人机
重量 249 克
感光元件为 1/1.3 英寸 CMOS
和 Neo 2 是一样的
像素为 4800 万
也就是 4800 万像素
镜头焦距 24mm
光圈 f/1.7
搭配长续航电池最长飞行时间可达 45 分钟
支持 4K 60p/100p 等多种拍摄规格
同时配备三轴机械云台
它还具备全向避障功能
视觉全向避障
Mini 4 Pro 的定位则比 Neo 2 稍高一些
这款无人机自带遥控器
```

| 已确认实体 | 类型 | 原文提及 |
|---|---|---|
| DJI Mini 4 Pro | 产品/设备 | Mini 4 Pro |
| DJI Neo 2 | 产品/设备 | Neo 2 |
| CMOS | 技术/功能 | CMOS |
| Mini 4 Pro长续航电池 | 硬件部件 | 长续航电池 |
| Mini 4 Pro三轴机械云台 | 硬件部件 | 三轴机械云台 |
| 视觉全向避障 | 技术/功能 | 全向避障功能；视觉全向避障 |

| 主体 | 关系 | 客体 | 断言 | 限定 | 证据原文 |
|---|---|---|---|---|---|
| DJI Neo 2 | uses（使用） | CMOS | asserted | — | 跟大家讲讲 Mini 4 Pro 的规格 ⏎ 它定位为可折叠入门级无人机 ⏎ 重量 249 克 ⏎ 感光元件为 1/1.3 英寸 CMOS ⏎ 和 Neo 2 是一样的 |
| DJI Mini 4 Pro | compatible_with（可配用） | Mini 4 Pro长续航电池 | asserted | — | 跟大家讲讲 Mini 4 Pro 的规格 ⏎ 它定位为可折叠入门级无人机 ⏎ 重量 249 克 ⏎ 感光元件为 1/1.3 英寸 CMOS ⏎ 和 Neo 2 是一样的 ⏎ 像素为 4800 万 ⏎ 也就是 4800 万像素 ⏎ 镜头焦距 24mm ⏎ 光圈 f/1.7 ⏎ 搭配长续航电池最长飞行时间可达 45 分钟 |
| DJI Mini 4 Pro | contains（组成/功能包含） | Mini 4 Pro三轴机械云台 | asserted | — | 跟大家讲讲 Mini 4 Pro 的规格 ⏎ 它定位为可折叠入门级无人机 ⏎ 重量 249 克 ⏎ 感光元件为 1/1.3 英寸 CMOS ⏎ 和 Neo 2 是一样的 ⏎ 像素为 4800 万 ⏎ 也就是 4800 万像素 ⏎ 镜头焦距 24mm ⏎ 光圈 f/1.7 ⏎ 搭配长续航电池最长飞行时间可达 45 分钟 ⏎ 支持 4K 60p/100p 等多种拍摄规格 ⏎ 同时配备三轴机械云台 |
| DJI Mini 4 Pro | supports（支持功能/协议） | 视觉全向避障 | asserted | — | 同时配备三轴机械云台 ⏎ 它还具备全向避障功能 ⏎ 视觉全向避障 ⏎ Mini 4 Pro 的定位则比 Neo 2 稍高一些 |
| DJI Mini 4 Pro | uses（使用） | CMOS | asserted | — | 跟大家讲讲 Mini 4 Pro 的规格 ⏎ 它定位为可折叠入门级无人机 ⏎ 重量 249 克 ⏎ 感光元件为 1/1.3 英寸 CMOS ⏎ 和 Neo 2 是一样的 |

复核说明：

- v3不再将感光元件、镜头和无型号遥控器作为实体；保留CMOS、长续航电池、三轴机械云台与视觉全向避障。参数数值仍不建实体。
- 关系标注：删除涉及上述已移除实体的4条关系。感光元件类型仍明确为CMOS，因此新增Mini 4 Pro uses CMOS；保留Neo 2 uses CMOS，但不把不同无人机的物理传感器合并。
- 关系标注：电池仅明示可搭配，标compatible_with；避障别名合并为同一功能，不补回遥控器的包装关系。

## EG007 · 中文

涉及型号：DJI Lito 1、DJI Lito X1、DJI Mini 3、DJI Mini 4K。

[CP值最高的入門空拍機！DJI Lito X1 & Lito 1 完整評測｜免登記免考照、光達避障、D-Log M全都有 #dji  #DJILitoX1 #肯吉老王開箱](https://www.youtube.com/watch?v=2VqkvtTfK5g) · 61.833–121.266秒

```text
我覺得空拍機就是非常簡單
能夠幫你交代這整個景色的一個大景
帶出一個景色的感動
而近幾年因為分享
這麼多旅遊影片的關係
還有拍攝的一些分享
器材的分享
我也常常被問到一些
空拍機選擇啊
或是相關知識的問題
而如果你是一個空拍新手
或是正要入門空拍機的話
也許今天要講的
Lito 1跟Lito X1
可能是你可以考量的一個機種喔
那今天呢我們會從基本規格
實際的拍攝
還有我實際應用後的
實際心得來跟你一一的分享
那我們先來聊聊他的規格吧
先來說一下為什麼DJI
會有這一次的Lito系列
DJI呢把過往的Mini 3
或者是Mini 4K
這種平價機種全部整合起來
把它變成一個新的系列
就是今天要講的Lito 1
那Lito X1是高價版
Lito 1是平價版
那這兩個最大的落差呢
在於鏡頭 在於避障
還有在一些後製的一些設定上
待會我們會一一的做介紹
```

| 已确认实体 | 类型 | 原文提及 |
|---|---|---|
| DJI Lito 1 | 产品/设备 | Lito 1 |
| DJI Lito X1 | 产品/设备 | Lito X1 |
| DJI Mini 3 | 产品/设备 | Mini 3 |
| DJI Mini 4K | 产品/设备 | Mini 4K |
| DJI Lito系列 | 产品系列 | Lito系列 |
| DJI | 组织 | DJI |
| 镜头 | 硬件部件 | 鏡頭 |
| 避障 | 技术/功能 | 避障 |

| 主体 | 关系 | 客体 | 断言 | 限定 | 证据原文 |
|---|---|---|---|---|---|
| DJI Lito 1 | offered_by（所属公司/品牌） | DJI | asserted | — | 先來說一下為什麼DJI ⏎ 會有這一次的Lito系列 ⏎ DJI呢把過往的Mini 3 ⏎ 或者是Mini 4K ⏎ 這種平價機種全部整合起來 ⏎ 把它變成一個新的系列 ⏎ 就是今天要講的Lito 1 ⏎ 那Lito X1是高價版 ⏎ Lito 1是平價版 |
| DJI Lito X1 | offered_by（所属公司/品牌） | DJI | asserted | — | 先來說一下為什麼DJI ⏎ 會有這一次的Lito系列 ⏎ DJI呢把過往的Mini 3 ⏎ 或者是Mini 4K ⏎ 這種平價機種全部整合起來 ⏎ 把它變成一個新的系列 ⏎ 就是今天要講的Lito 1 ⏎ 那Lito X1是高價版 ⏎ Lito 1是平價版 |
| DJI Mini 3 | offered_by（所属公司/品牌） | DJI | asserted | — | 先來說一下為什麼DJI ⏎ 會有這一次的Lito系列 ⏎ DJI呢把過往的Mini 3 ⏎ 或者是Mini 4K ⏎ 這種平價機種全部整合起來 ⏎ 把它變成一個新的系列 ⏎ 就是今天要講的Lito 1 ⏎ 那Lito X1是高價版 ⏎ Lito 1是平價版 |
| DJI Mini 4K | offered_by（所属公司/品牌） | DJI | asserted | — | 先來說一下為什麼DJI ⏎ 會有這一次的Lito系列 ⏎ DJI呢把過往的Mini 3 ⏎ 或者是Mini 4K ⏎ 這種平價機種全部整合起來 ⏎ 把它變成一個新的系列 ⏎ 就是今天要講的Lito 1 ⏎ 那Lito X1是高價版 ⏎ Lito 1是平價版 |
| DJI Lito系列 | offered_by（所属公司/品牌） | DJI | asserted | — | 先來說一下為什麼DJI ⏎ 會有這一次的Lito系列 ⏎ DJI呢把過往的Mini 3 ⏎ 或者是Mini 4K ⏎ 這種平價機種全部整合起來 ⏎ 把它變成一個新的系列 ⏎ 就是今天要講的Lito 1 ⏎ 那Lito X1是高價版 ⏎ Lito 1是平價版 |
| DJI Lito系列 | has_model（系列型号） | DJI Lito 1 | asserted | — | 先來說一下為什麼DJI ⏎ 會有這一次的Lito系列 ⏎ DJI呢把過往的Mini 3 ⏎ 或者是Mini 4K ⏎ 這種平價機種全部整合起來 ⏎ 把它變成一個新的系列 ⏎ 就是今天要講的Lito 1 ⏎ 那Lito X1是高價版 ⏎ Lito 1是平價版 |
| DJI Lito系列 | has_model（系列型号） | DJI Lito X1 | asserted | — | 先來說一下為什麼DJI ⏎ 會有這一次的Lito系列 ⏎ DJI呢把過往的Mini 3 ⏎ 或者是Mini 4K ⏎ 這種平價機種全部整合起來 ⏎ 把它變成一個新的系列 ⏎ 就是今天要講的Lito 1 ⏎ 那Lito X1是高價版 ⏎ Lito 1是平價版 |

复核说明：

- Lito系列不是一台无人机；Lito 1、Lito X1和被比较的Mini型号分别列出。
- 镜头未逐一说明所属设备，暂保留泛称；后制设置是背景描述，不独立标成软件或技术。
- 关系标注：新系列明确包含Lito 1与Lito X1；整合过往Mini机种不表示Mini 3、Mini 4K属于Lito系列。只说镜头、避障有差别，不能确定各自配置，不给泛称镜头强加归属。

## EG008 · 中文

涉及型号：DJI Flip、DJI Mavic 3 Pro、DJI Air 3、DJI Air 3S、DJI Mini 3、DJI Mini 4 Pro。

[2788元的新形态无人机，好用吗？DJI Flip上手！](https://www.youtube.com/watch?v=jrvhN6yNovA) · 268.397–328.157秒

```text
还是老规矩
样片这次我们上传了一些到官网
你可以免费下载下来调一调
试一试
不过我不知道你看完这些样片
你会不会有一种似曾相识的感觉
你没有猜错
DJI Flip搭载的传感器
是1/1.3英寸的传感器
还有等效24mm f1.7的广角镜头
有没有听着很熟悉呢
答案就是这块传感器我们已经测麻了
它是大疆Mavic 3 Pro的中焦镜头
Air 3的广角和长焦
Air 3S的长焦
Mini 3、Mini 4 Pro的广角
Action 4还有那些别的各种机型
都有可能用到这块传感器
那优点就是经过这么多的迭代
大疆已经把它调教到了一个登峰造极的地步
确实画面拍出来
即便不能给你带来惊喜
但是它确实还是挺能打的
它目前最高支持拍摄4K 60帧的HDR视频
还有4K 100帧的慢动作
那我们测试下来发现
它的动态范围还有解析力和Mini 4 Pro
不能说是相似
只能说是难分伯仲
在默认的锐化和降噪之下
```

| 已确认实体 | 类型 | 原文提及 |
|---|---|---|
| DJI Flip | 产品/设备 | DJI Flip |
| DJI Mavic 3 Pro | 产品/设备 | 大疆Mavic 3 Pro |
| DJI Air 3 | 产品/设备 | Air 3 |
| DJI Air 3S | 产品/设备 | Air 3S |
| DJI Mini 3 | 产品/设备 | Mini 3 |
| DJI Mini 4 Pro | 产品/设备 | Mini 4 Pro |
| DJI Action 4 | 产品/设备 | Action 4 |
| 大疆 | 组织 | 大疆 |
| Flip传感器 | 硬件部件 | 传感器 |
| Flip广角镜头 | 硬件部件 | 广角镜头 |
| HDR | 技术/功能 | HDR |

| 主体 | 关系 | 客体 | 断言 | 限定 | 证据原文 |
|---|---|---|---|---|---|
| DJI Flip | contains（组成/功能包含） | Flip传感器 | asserted | — | DJI Flip搭载的传感器 ⏎ 是1/1.3英寸的传感器 |
| DJI Flip | contains（组成/功能包含） | Flip广角镜头 | asserted | — | DJI Flip搭载的传感器 ⏎ 是1/1.3英寸的传感器 ⏎ 还有等效24mm f1.7的广角镜头 |
| DJI Flip | offered_by（所属公司/品牌） | 大疆 | asserted | — | DJI Flip搭载的传感器 ⏎ 是1/1.3英寸的传感器 ⏎ 还有等效24mm f1.7的广角镜头 ⏎ 有没有听着很熟悉呢 ⏎ 答案就是这块传感器我们已经测麻了 ⏎ 它是大疆Mavic 3 Pro的中焦镜头 ⏎ Air 3的广角和长焦 ⏎ Air 3S的长焦 ⏎ Mini 3、Mini 4 Pro的广角 ⏎ Action 4还有那些别的各种机型 ⏎ 都有可能用到这块传感器 ⏎ 那优点就是经过这么多的迭代 ⏎ 大疆已经把它调教到了一个登峰造极的地步 |
| DJI Mavic 3 Pro | offered_by（所属公司/品牌） | 大疆 | asserted | — | 它是大疆Mavic 3 Pro的中焦镜头 |

复核说明：

- v3移除对比机型的6个镜头实体，保留各型号、Flip传感器、Flip广角镜头、大疆及HDR。相同规格不代表同一个物理传感器。
- 关系标注：沿用人工复核后的4条关系；先前已删除的对比机型镜头关系与Flip supports HDR不自动恢复。HDR虽仍是实体，也不强制要求有关系。

## EG009 · 中文

涉及型号：DJI Mini 4K。

[从豁然开朗到“死黑一片”：这台Mini 4K无人机，怎么好像也有点“近视”？ #广东麦克疯#越南 #航拍機 #老挝旅游 #越南旅遊](https://www.youtube.com/watch?v=7MJjAlpvj7w) · 0.0–58.6秒

```text
在阳台虽然感觉楼距好近景观受限
但是升空之后
你的心情就会截然不同了
它就像是大地上面的一幅精美油画
而我所在的地方
就是组成它的颜色之一
如果没有无人机
真的会以为这里是沙漠
但是航拍的视角可以告诉你
不远处就是海边了
这里只是长得像沙漠一样的沙滩
这些画面
是我用新买到的MINI 4K
无人机拍下的
它号称重量不到250克
在这个行李费快要贵过机票的年代
它真的是太懂消费者了
正在操作无人机的
是老挝本地的朋友
这是他第一次飞无人机
但是我也不是很担心
毕竟价格比之前那台
```

| 已确认实体 | 类型 | 原文提及 |
|---|---|---|
| DJI Mini 4K | 产品/设备 | MINI 4K |

| 主体 | 关系 | 客体 | 断言 | 限定 | 证据原文 |
|---|---|---|---|---|---|

本段未标注关系，原因见下方说明。

复核说明：

- v3仅保留DJI Mini 4K，老挝不在实体答案内；参数与泛称环境不建实体。
- 关系标注：relations保持为空，不从操作者的本地身份推导飞行地点，也不补回地理端点。

## EG010 · 中文

涉及型号：DJI Neo 2、DJI Mavic 4 Pro。

[Inside a DJI Store in China with Prices 🇨🇳 DJI Pocket 4P + MORE (2026)](https://www.youtube.com/watch?v=TtfVuyfODLU) · 737.84–795.2秒

```text
戴上它。这就是你操控无人机的方式。这里还有一个遥控器。
我喜欢他们提供的酒精湿巾，这样你就可以在
店里清洁和试戴。令人惊喜的是，这款护目镜也兼容我们的无人机 Neo 2。好了，
跟我来。接下来，我们当然不能错过 DJI Mavic 4
Pro。看看这个庞然大物，真是令人印象深刻。DJI Mavic 4 Pro 搭配
RC 2 遥控器的售价是 13,888 元人民币。下一个套装是 17,888 元人民币，我记得里面包含
额外的电池组和其他一些配件。DJI Mavic 4 Pro 的终极套装售价是 23,988
```

| 已确认实体 | 类型 | 原文提及 |
|---|---|---|
| DJI Neo 2 | 产品/设备 | Neo 2 |
| DJI Mavic 4 Pro | 产品/设备 | DJI Mavic 4 ⏎ Pro；DJI Mavic 4 Pro |
| RC 2 | 产品/设备 | RC 2 遥控器 |
| 护目镜 | 产品/设备 | 护目镜 |

| 主体 | 关系 | 客体 | 断言 | 限定 | 证据原文 |
|---|---|---|---|---|---|
| DJI Mavic 4 Pro | compatible_with（可配用） | RC 2 | asserted | — | Pro。看看这个庞然大物，真是令人印象深刻。DJI Mavic 4 Pro 搭配 ⏎ RC 2 遥控器的售价是 13,888 元人民币。下一个套装是 17,888 元人民币，我记得里面包含 |
| DJI Neo 2 | compatible_with（可配用） | 护目镜 | asserted | — | 店里清洁和试戴。令人惊喜的是，这款护目镜也兼容我们的无人机 Neo 2。好了， |

复核说明：

- v3保留无型号护目镜，类型为完整设备product；不补成Goggles 3。开头未具名遥控器仍不保留，明确型号RC 2另列。
- 关系标注：保留Mavic 4 Pro compatible_with RC 2，新增Neo 2 compatible_with 护目镜。两组配对分开，不能把RC 2接到Neo 2，也不从戴上护目镜推断其直接操控Neo 2。

## EG011 · 英文

涉及型号：DJI Neo 2。

[How to Trippy Spin with DJI Neo 2 (FPV Stick Tutorial) 🛸​#DJINeo2 #TrippySpin  #DJIFPV #djineo](https://www.youtube.com/watch?v=qAgye8oJBKA) · 0.56–18.32秒

```text
How to perform a trippy spin with DJI Neo 2. [screaming] The obstacle must
be at least 5 m high. Camera is [music] pan to 30°. Punch up and pitch down when
nearing the pole. Throttle diagonally and roll to the side. Focus on pole
and adjust sticks accordingly. When done, lower the speed and level up.
```

| 已确认实体 | 类型 | 原文提及 |
|---|---|---|
| DJI Neo 2 | 产品/设备 | DJI Neo 2 |
| Trippy Spin | 方法/操作 | trippy spin |

| 主体 | 关系 | 客体 | 断言 | 限定 | 证据原文 |
|---|---|---|---|---|---|
| Trippy Spin | applied_to（应用对象） | DJI Neo 2 | asserted | modality=instruction | How to perform a trippy spin with DJI Neo 2. [screaming] The obstacle must ⏎ be at least 5 m high. Camera is [music] pan to 30°. Punch up and pitch down when ⏎ nearing the pole. Throttle diagonally and roll to the side. Focus on pole ⏎ and adjust sticks accordingly. When done, lower the speed and level up. |

复核说明：

- trippy spin是命名飞行动作；普通pitch、roll、调速指令不逐句扩成独立实体。
- 杆子是飞行参照物，不是无人机组件；角度、5米和音乐提示不建实体。
- 关系标注：Trippy Spin是教程中的飞行动作，方向为方法applied_to设备，不视作无人机内置自动功能。

## EG012 · 英文

涉及型号：DJI Air 3。

[Fix Your DJI Air 3 Drone Like a Pro: Gimbal Ribbon Cable Replacement Guide](https://www.youtube.com/watch?v=_VofSIgvXOc) · 40.32–89.92秒

```text
approach will greatly assist you in successfully reassembling your DJI Air 3 drone. And you need
not worry about misplacing any screws; they will all stay securely on the magnetic mat.
Remove the battery before you start disassembling the DJI Air 3 drone.
Flip your drone over and remove the screws securing the bottom cover.
Remember, each screw might be a slightly different size, so keep track of where they go.
Use plastic tools to carefully open the drone body and ensure safe handling.
```

| 已确认实体 | 类型 | 原文提及 |
|---|---|---|
| DJI Air 3 | 产品/设备 | DJI Air 3 drone；DJI Air 3 |
| DJI Air 3 Battery | 硬件部件 | battery |
| DJI Air 3 Screws | 硬件部件 | screws；screw |
| bottom cover | 硬件部件 | bottom cover |
| drone body  | 硬件部件 | drone body |

| 主体 | 关系 | 客体 | 断言 | 限定 | 证据原文 |
|---|---|---|---|---|---|
| DJI Air 3 | contains（组成/功能包含） | DJI Air 3 Battery | asserted | — | Remove the battery before you start disassembling the DJI Air 3 drone. |
| DJI Air 3 | contains（组成/功能包含） | DJI Air 3 Screws | asserted | — | approach will greatly assist you in successfully reassembling your DJI Air 3 drone. And you need ⏎ not worry about misplacing any screws; they will all stay securely on the magnetic mat. ⏎ Remove the battery before you start disassembling the DJI Air 3 drone. ⏎ Flip your drone over and remove the screws securing the bottom cover. |
| DJI Air 3 | contains（组成/功能包含） | bottom cover | asserted | — | Remove the battery before you start disassembling the DJI Air 3 drone. ⏎ Flip your drone over and remove the screws securing the bottom cover. |
| DJI Air 3 | contains（组成/功能包含） | drone body  | asserted | — | approach will greatly assist you in successfully reassembling your DJI Air 3 drone. And you need ⏎ not worry about misplacing any screws; they will all stay securely on the magnetic mat. ⏎ Remove the battery before you start disassembling the DJI Air 3 drone. ⏎ Flip your drone over and remove the screws securing the bottom cover. ⏎ Remember, each screw might be a slightly different size, so keep track of where they go. ⏎ Use plastic tools to carefully open the drone body and ensure safe handling. |
| DJI Air 3 Screws | connected_to（连接） | bottom cover | asserted | — | approach will greatly assist you in successfully reassembling your DJI Air 3 drone. And you need ⏎ not worry about misplacing any screws; they will all stay securely on the magnetic mat. ⏎ Remove the battery before you start disassembling the DJI Air 3 drone. ⏎ Flip your drone over and remove the screws securing the bottom cover. |

复核说明：

- v3新增bottom cover与drone body，电池及螺丝仍保留。drone body实体名末尾的空格逐字沿用实体文件，未擅自改名。
- 关系标注：保留设备与电池、螺丝的组成关系，新增设备与底盖、机身的组成关系；移除步骤不等于产品永久不包含部件。
- 关系标注：screws securing the bottom cover按人工复核确认为螺丝connected_to底盖，表示机械固定连接；不推断底盖与机身的额外层级。

## EG013 · 英文

涉及型号：DJI Mini 4 Pro。

[DJI Mini 4 Pro｜First Use Guide](https://www.youtube.com/watch?v=JtYyP_y5Az0) · 4.08–58.8秒

```text
thank you
welcome to DJI Mini 4 Pro tutorial
we will guide you through the unboxing and first use process of the product
open the package and take out the aircraft remote controller and accessories
downloading the DJI fly app scan the QR code on the package to download and install the DJI fly
app you can also watch the tutorial videos and read the user manual after scanning the QR code
remove the propeller holder and gimbal protector unfold the aircraft arms and turn
```

| 已确认实体 | 类型 | 原文提及 |
|---|---|---|
| DJI Mini 4 Pro | 产品/设备 | DJI Mini 4 Pro |
| remote controller | 产品/设备 | remote controller |
| DJI Fly | 软件 | DJI fly app；DJI fly ⏎ app |
| propeller holder | 硬件部件 | propeller holder |
| gimbal protector | 硬件部件 | gimbal protector |

| 主体 | 关系 | 客体 | 断言 | 限定 | 证据原文 |
|---|---|---|---|---|---|
| DJI Mini 4 Pro | packaged_with（包装随附） | remote controller | asserted | — | welcome to DJI Mini 4 Pro tutorial ⏎ we will guide you through the unboxing and first use process of the product ⏎ open the package and take out the aircraft remote controller and accessories |
| DJI Fly | applied_to（应用对象） | DJI Mini 4 Pro | asserted | modality=instruction | welcome to DJI Mini 4 Pro tutorial ⏎ we will guide you through the unboxing and first use process of the product ⏎ open the package and take out the aircraft remote controller and accessories ⏎ downloading the DJI fly app scan the QR code on the package to download and install the DJI fly ⏎ app you can also watch the tutorial videos and read the user manual after scanning the QR code |
| DJI Mini 4 Pro | packaged_with（包装随附） | propeller holder | asserted | — | welcome to DJI Mini 4 Pro tutorial ⏎ we will guide you through the unboxing and first use process of the product ⏎ open the package and take out the aircraft remote controller and accessories ⏎ downloading the DJI fly app scan the QR code on the package to download and install the DJI fly ⏎ app you can also watch the tutorial videos and read the user manual after scanning the QR code ⏎ remove the propeller holder and gimbal protector unfold the aircraft arms and turn |
| DJI Mini 4 Pro | packaged_with（包装随附） | gimbal protector | asserted | — | welcome to DJI Mini 4 Pro tutorial ⏎ we will guide you through the unboxing and first use process of the product ⏎ open the package and take out the aircraft remote controller and accessories ⏎ downloading the DJI fly app scan the QR code on the package to download and install the DJI fly ⏎ app you can also watch the tutorial videos and read the user manual after scanning the QR code ⏎ remove the propeller holder and gimbal protector unfold the aircraft arms and turn |

复核说明：

- DJI Fly是软件；遥控器虽未给型号，仍按完整设备标为product。
- 本次未保留用户手册、机臂；二维码不当作软件，笼统accessories不另造部件实体。
- 关系标注：开箱取出遥控器、移除桨叶固定器与云台保护罩，按随产品附带的物品标packaged_with，保护件不当作机身永久组成。首次使用流程中的DJI Fly标applied_to，不臆造其安装在无人机上或直接操控飞行。

## EG014 · 英文

涉及型号：DJI Avata 2。

[DJI Made Flipping a Drone Look This Easy](https://www.youtube.com/watch?v=AWQJTxxNpAI) · 0.13–24.075秒

```text
That is a full drone flip.
DJI's Avata 2 makes flying feel
like sitting inside the camera.
Pair it with Goggles 3 and RC
Motion 3. Your hand steers the view,
and Easy ACRO gives you flips, rolls,
and a one hundred eighty degree drift.
The camera can shoot 4K while the world
becomes your racecourse. Forget another slow shot of
the skyline. This is the shot that
makes you lean sideways in your chair.
```

| 已确认实体 | 类型 | 原文提及 |
|---|---|---|
| DJI Avata 2 | 产品/设备 | Avata 2 |
| DJI | 组织 | DJI |
| Goggles 3 | 产品/设备 | Goggles 3 |
| RC Motion 3 | 产品/设备 | RC ⏎ Motion 3 |
| Easy ACRO | 技术/功能 | Easy ACRO |

| 主体 | 关系 | 客体 | 断言 | 限定 | 证据原文 |
|---|---|---|---|---|---|
| DJI Avata 2 | offered_by（所属公司/品牌） | DJI | asserted | — | DJI's Avata 2 makes flying feel ⏎ like sitting inside the camera. |
| DJI Avata 2 | compatible_with（可配用） | Goggles 3 | asserted | — | DJI's Avata 2 makes flying feel ⏎ like sitting inside the camera. ⏎ Pair it with Goggles 3 and RC ⏎ Motion 3. Your hand steers the view, |
| DJI Avata 2 | compatible_with（可配用） | RC Motion 3 | asserted | — | DJI's Avata 2 makes flying feel ⏎ like sitting inside the camera. ⏎ Pair it with Goggles 3 and RC ⏎ Motion 3. Your hand steers the view, |
| DJI Avata 2 | supports（支持功能/协议） | Easy ACRO | asserted | — | DJI's Avata 2 makes flying feel ⏎ like sitting inside the camera. ⏎ Pair it with Goggles 3 and RC ⏎ Motion 3. Your hand steers the view, ⏎ and Easy ACRO gives you flips, rolls, ⏎ and a one hundred eighty degree drift. |

复核说明：

- Goggles 3、RC Motion 3是完整设备，标为product；Easy ACRO是功能。
- 本次不将翻转、翻滚、漂移或相机另列为实体；4K是参数，坐在相机里是比喻。
- 关系标注：Pair it with分别建立无人机与两台完整设备的可配用关系；不据同句出现建立Goggles 3与RC Motion 3之间的连接关系。

## EG015 · 英文

涉及型号：Parrot ANAFI。

[Parrot Anafi 4k Drone - Photographer's  Review](https://www.youtube.com/watch?v=8PjamSNj4BA) · 57.36–115.22秒

```text
This is all that I use for the Parrot Anafi you've got the charging
cable USB "C" I've got a spare battery I've got the drone itself
I've got the USB cable for the iPhone or the whichever phone you have and then I've got the controller
Now as you can see it sort of looks like a big bug or a
dragonfly or something. It even sounds like bees when it's flying
but it's it's very quiet very fast it takes fantastic video and
it stays up for a long time. Now to fold it up you literally just fold these down
here, push that up there and then it folds into this sort of long skinny compact
```

| 已确认实体 | 类型 | 原文提及 |
|---|---|---|
| Parrot ANAFI | 产品/设备 | Parrot Anafi |
| ANAFI遥控器 | 产品/设备 | controller |

| 主体 | 关系 | 客体 | 断言 | 限定 | 证据原文 |
|---|---|---|---|---|---|
| Parrot ANAFI | compatible_with（可配用） | ANAFI遥控器 | asserted | — | This is all that I use for the Parrot Anafi you've got the charging ⏎ cable USB "C" I've got a spare battery I've got the drone itself ⏎ I've got the USB cable for the iPhone or the whichever phone you have and then I've got the controller |

复核说明：

- v3仅保留Parrot ANAFI和ANAFI遥控器，手机已移除；数据线、电池和昆虫比喻均不补回。
- 关系标注：删除无人机与手机的可配用关系，保留与遥控器的可配用关系；个人使用清单不等于原厂包装清单。

## EG016 · 英文

涉及型号：DJI Mini 3、DJI Mini 4 Pro。

[Crashed? Here's How to Replace DJI Mini Drone Propellers / 3 and 4 Pro](https://www.youtube.com/watch?v=XUoFNFt8Mng) · 0.16–17.561秒

```text
Crashed your DJI Mini 3 or 4 drone?
Let's fix it by replacing the propellers.
Here's the key: they're mirrored, so two turn clockwise and two counterclockwise.
Check the curved markings - the striped arms get the striped propellers.
Unscrew the old ones.
Line up the new.
Tighten snugly, but not too tight.
And just like that, your drone is ready to fly!
```

| 已确认实体 | 类型 | 原文提及 |
|---|---|---|
| DJI Mini 3 | 产品/设备 | DJI Mini 3 |
| propellers | 硬件部件 | propellers |
| striped arms | 硬件部件 | striped arms |
| striped propellers | 硬件部件 | striped propellers |

| 主体 | 关系 | 客体 | 断言 | 限定 | 证据原文 |
|---|---|---|---|---|---|
| DJI Mini 3 | contains（组成/功能包含） | propellers | asserted | — | Crashed your DJI Mini 3 or 4 drone? ⏎ Let's fix it by replacing the propellers. ⏎ Here's the key: they're mirrored, so two turn clockwise and two counterclockwise. ⏎ Check the curved markings - the striped arms get the striped propellers. |
| DJI Mini 3 | contains（组成/功能包含） | striped arms | asserted | — | Crashed your DJI Mini 3 or 4 drone? ⏎ Let's fix it by replacing the propellers. ⏎ Here's the key: they're mirrored, so two turn clockwise and two counterclockwise. ⏎ Check the curved markings - the striped arms get the striped propellers. |
| DJI Mini 3 | contains（组成/功能包含） | striped propellers | asserted | — | Crashed your DJI Mini 3 or 4 drone? ⏎ Let's fix it by replacing the propellers. ⏎ Here's the key: they're mirrored, so two turn clockwise and two counterclockwise. ⏎ Check the curved markings - the striped arms get the striped propellers. |
| striped propellers | installed_on（安装于） | striped arms | asserted | modality=instruction | Check the curved markings - the striped arms get the striped propellers. |

复核说明：

- v3保留propellers，并新增striped arms、striped propellers；原文4 drone仍不补为具体型号。Crashed问句不作为已发生故障。
- 关系标注：新增Mini 3包含条纹机臂及条纹螺旋桨，并按striped arms get the striped propellers标后者installed_on前者，保留instruction步骤语境。
- 关系标注：propellers与striped propellers按实体答案分别引用，不把一般类别与条纹子集之间的关系错标为部件contains。

## EG017 · 英文

涉及型号：DJI Air 3。

[DJI Air 3: The Complete Disassembly and Reassembly Guide](https://www.youtube.com/watch?v=8k8JhMTr9xc) · 741.95–787.49秒

```text
The ESC (Electronic Speed Controllers) board controls the motors which directly alters
the in-flight drone directional changes depending on the commands.
If your DJI Air 3 drone has an ESC error and you can't fix this error by refreshing the
drone firmware, it is most likely the ESC board is failing, so you need to replace it.
To replace the ESC board, you need to desolder the drone's arm wires.
If you do not have soldering experience and the required tools, I'd recommend sending
your drone to DJI service for ESC board replacement or repair.
```

| 已确认实体 | 类型 | 原文提及 |
|---|---|---|
| DJI Air 3 | 产品/设备 | DJI Air 3 |
| Air 3电调板 | 硬件部件 | ESC (Electronic Speed Controllers) board；ESC board |
| Air 3 motors | 硬件部件 | motors |
| ESC error | 故障 | ESC error |
| DJI | 组织 | DJI service |
| firmware | 软件 | firmware |
| DJI Air 3 arm wires | 硬件部件 | arm wires |

| 主体 | 关系 | 客体 | 断言 | 限定 | 证据原文 |
|---|---|---|---|---|---|
| DJI Air 3 | contains（组成/功能包含） | Air 3电调板 | asserted | — | The ESC (Electronic Speed Controllers) board controls the motors which directly alters ⏎ the in-flight drone directional changes depending on the commands. ⏎ If your DJI Air 3 drone has an ESC error and you can't fix this error by refreshing the ⏎ drone firmware, it is most likely the ESC board is failing, so you need to replace it. ⏎ To replace the ESC board, you need to desolder the drone's arm wires. |
| DJI Air 3 | contains（组成/功能包含） | Air 3 motors | asserted | — | The ESC (Electronic Speed Controllers) board controls the motors which directly alters ⏎ the in-flight drone directional changes depending on the commands. ⏎ If your DJI Air 3 drone has an ESC error and you can't fix this error by refreshing the ⏎ drone firmware, it is most likely the ESC board is failing, so you need to replace it. |
| Air 3电调板 | controls（操控设备/改变设置） | Air 3 motors | asserted | — | The ESC (Electronic Speed Controllers) board controls the motors which directly alters ⏎ the in-flight drone directional changes depending on the commands. |
| DJI Air 3 | uses（使用） | firmware | asserted | — | If your DJI Air 3 drone has an ESC error and you can't fix this error by refreshing the ⏎ drone firmware, it is most likely the ESC board is failing, so you need to replace it. |
| DJI Air 3 | reports_fault（设备出现异常，不要求报警） | ESC error | asserted | modality=conditional；scope=If your DJI Air 3 drone has an ESC error and you can't fix this error by refreshing the ⏎ drone firmware, it is most likely the ESC board is failing, so you need to replace it. | If your DJI Air 3 drone has an ESC error and you can't fix this error by refreshing the ⏎ drone firmware, it is most likely the ESC board is failing, so you need to replace it. |
| DJI Air 3 | offered_by（所属公司/品牌） | DJI | asserted | — | If your DJI Air 3 drone has an ESC error and you can't fix this error by refreshing the |
| DJI Air 3 | contains（组成/功能包含） | DJI Air 3 arm wires | asserted | — | If your DJI Air 3 drone has an ESC error and you can't fix this error by refreshing the ⏎ drone firmware, it is most likely the ESC board is failing, so you need to replace it. ⏎ To replace the ESC board, you need to desolder the drone's arm wires. |
| DJI Air 3 arm wires | connected_to（连接） | Air 3电调板 | asserted | — | To replace the ESC board, you need to desolder the drone's arm wires. |

复核说明：

- v3新增DJI Air 3 arm wires；固件仍是software，电调板、马达、ESC故障与DJI组织沿用原答案。
- 关系标注：保留If故障条件，不表示所有Air 3当前均有ESC故障；刷新固件失败不直接变成固件negated mitigates。
- 关系标注：新增设备contains机臂导线；更换电调板需要解焊机臂导线，按人工复核确认导线connected_to电调板，表示维修步骤涉及的电气连接；不额外补出导线与马达的连接。

## EG018 · 英文

涉及型号：DJI Mini 3、DJI Mavic Mini、DJI Mini 2。

[NEW DJI Mini 3 - The BEST Budget Drone EVER! (4K)](https://www.youtube.com/watch?v=RvSjkLzcFbw) · 115.86–161.56秒

```text
True vertical shooting which is a very welcome addition to both of these drones, sharing content
on social media in the native aspect ratio without having to crop out two thirds of the screen.
The pro version and the mini 3 can now also shoot upwards past level horizon which neither of the
original Mavic mini or Mini 2 could do. This is great for capturing climbing shots and low angles.
So what else do they have in common. They both fly at the same speed at 16
meters per second in sport mode. Thats 24mph. Let’s see what 24mph looks like!
The mini 3
```

| 已确认实体 | 类型 | 原文提及 |
|---|---|---|
| DJI Mini 3 | 产品/设备 | mini 3 |
| DJI Mavic Mini | 产品/设备 | Mavic mini |
| DJI Mini 2 | 产品/设备 | Mini 2 |
| sport mode | 技术/功能 | sport mode |
| Pro版本 | 产品/设备 | pro version |
| True vertical shooting | 技术/功能 | True vertical shooting |

| 主体 | 关系 | 客体 | 断言 | 限定 | 证据原文 |
|---|---|---|---|---|---|
| DJI Mini 3 | supports（支持功能/协议） | sport mode | asserted | — | The pro version and the mini 3 can now also shoot upwards past level horizon which neither of the ⏎ original Mavic mini or Mini 2 could do. This is great for capturing climbing shots and low angles. ⏎ So what else do they have in common. They both fly at the same speed at 16 ⏎ meters per second in sport mode. Thats 24mph. Let’s see what 24mph looks like! |
| Pro版本 | supports（支持功能/协议） | sport mode | asserted | — | The pro version and the mini 3 can now also shoot upwards past level horizon which neither of the ⏎ original Mavic mini or Mini 2 could do. This is great for capturing climbing shots and low angles. ⏎ So what else do they have in common. They both fly at the same speed at 16 ⏎ meters per second in sport mode. Thats 24mph. Let’s see what 24mph looks like! |
| DJI Mini 3 | supports（支持功能/协议） | True vertical shooting | asserted | — | True vertical shooting which is a very welcome addition to both of these drones, sharing content ⏎ on social media in the native aspect ratio without having to crop out two thirds of the screen. ⏎ The pro version and the mini 3 can now also shoot upwards past level horizon which neither of the ⏎ original Mavic mini or Mini 2 could do. This is great for capturing climbing shots and low angles. ⏎ So what else do they have in common. They both fly at the same speed at 16 ⏎ meters per second in sport mode. Thats 24mph. Let’s see what 24mph looks like! |
| Pro版本 | supports（支持功能/协议） | True vertical shooting | asserted | — | True vertical shooting which is a very welcome addition to both of these drones, sharing content ⏎ on social media in the native aspect ratio without having to crop out two thirds of the screen. ⏎ The pro version and the mini 3 can now also shoot upwards past level horizon which neither of the ⏎ original Mavic mini or Mini 2 could do. This is great for capturing climbing shots and low angles. ⏎ So what else do they have in common. They both fly at the same speed at 16 ⏎ meters per second in sport mode. Thats 24mph. Let’s see what 24mph looks like! |

复核说明：

- v3保留Pro版本及True vertical shooting；Pro版本仅按片段内泛称使用，不补成Mini 3 Pro或其他具体型号。
- 关系标注：both/They both在本段回指Pro版本与Mini 3，新增Pro版本支持sport mode以及两台设备分别支持True vertical shooting的关系。开头both的指代已随本轮人工复核确认。
- 关系标注：Mavic mini、Mini 2的否定从句针对向上仰拍，不是竖拍或sport mode；不把该否定套到其他功能。

## EG019 · 英文

涉及型号：DJI Avata 360。

[Avata 360 Single-Lens Mode | Struggling with Head Tracking + Motion 3 | Turn On Caption (CC)](https://www.youtube.com/watch?v=sjEluw4GIqI) · 8.0–61.0秒

```text
Today's experiment: Avata 360 in single-lens mode, with RC Motion 3 and Head Tracking.
The Avata 360 takes off in spherical mode. I switch to single-lens mode after takeoff.
I'm nodding and turning my head to check how Head Tracking moves the camera and drone.
Checking that Head Tracking is enabled.
```

| 已确认实体 | 类型 | 原文提及 |
|---|---|---|
| DJI Avata 360 | 产品/设备 | Avata 360 |
| RC Motion 3 | 产品/设备 | RC Motion 3 |
| Head Tracking | 技术/功能 | Head Tracking |
| single-lens mode | 技术/功能 | single-lens mode |
| spherical mode | 技术/功能 | spherical mode |

| 主体 | 关系 | 客体 | 断言 | 限定 | 证据原文 |
|---|---|---|---|---|---|
| DJI Avata 360 | compatible_with（可配用） | RC Motion 3 | asserted | — | Today's experiment: Avata 360 in single-lens mode, with RC Motion 3 and Head Tracking. |
| DJI Avata 360 | supports（支持功能/协议） | single-lens mode | asserted | — | The Avata 360 takes off in spherical mode. I switch to single-lens mode after takeoff. |
| DJI Avata 360 | supports（支持功能/协议） | spherical mode | asserted | — | The Avata 360 takes off in spherical mode. I switch to single-lens mode after takeoff. |
| DJI Avata 360 | supports（支持功能/协议） | Head Tracking | uncertain | modality=hypothetical；scope=I'm nodding and turning my head to check how Head Tracking moves the camera and drone.；mode=single-lens mode | Today's experiment: Avata 360 in single-lens mode, with RC Motion 3 and Head Tracking. ⏎ The Avata 360 takes off in spherical mode. I switch to single-lens mode after takeoff. ⏎ I'm nodding and turning my head to check how Head Tracking moves the camera and drone. ⏎ Checking that Head Tracking is enabled. |

复核说明：

- v3移除Avata 360 camera，保留两种模式、Head Tracking、RC Motion 3及无人机；点头转头不是额外方法实体。
- 关系标注：删除设备contains相机关系；保留RC Motion 3可配用、两种模式支持，以及单镜头范围内Head Tracking的uncertain支持关系。旧版人工删除的两条Head Tracking controls关系不恢复。

## EG020 · 英文

涉及型号：DJI Mini 5 Pro。

[DJI Mini 5 Pro engel algılama testi: teller, yapraklar ve yağmur! 🚁](https://www.youtube.com/watch?v=ZqTrQBWrol0) · 0.0–34.5秒

```text
DJI Mini 5 Pro OBSTACLE AVOIDANCE TEST
TEST 1 Flying between wires
Side by side with the cable-car wire
TEST 2 It even detects leaves
TEST 3 Flying in heavy rain
Which one was the hardest? Tell me in the comments
```

| 已确认实体 | 类型 | 原文提及 |
|---|---|---|
| DJI Mini 5 Pro | 产品/设备 | DJI Mini 5 Pro |
| OBSTACLE AVOIDANCE | 技术/功能 | OBSTACLE AVOIDANCE |

| 主体 | 关系 | 客体 | 断言 | 限定 | 证据原文 |
|---|---|---|---|---|---|
| DJI Mini 5 Pro | supports（支持功能/协议） | OBSTACLE AVOIDANCE | asserted | — | DJI Mini 5 Pro OBSTACLE AVOIDANCE TEST ⏎ TEST 1 Flying between wires ⏎ Side by side with the cable-car wire ⏎ TEST 2 It even detects leaves |

复核说明：

- 电线、树叶、雨是测试环境或障碍物，不是无人机硬件，也不是无人机支持的技术。
- 只标来源提及的避障能力，不据标题或测试问题判断实测结果真伪。
- 关系标注：标题与“detects leaves”支持避障能力；不据测试清单补出与电线、树叶、雨的功能关系，也不声称每项测试均成功。

