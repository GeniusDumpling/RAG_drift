# 关系golden与实际抽取逐条对照 · 受控对照

标准：`relation-gold-v3`（已人工复核）；运行：`relation-gold-v3-prompt-r2-20261105.json`。
抽取版本：`video-graph-v2:deepseek-v4.1-flash-economy:c88ca83a8725-none-none`。

20个片段、77条标准关系；实际预测62条（按评测规则去重），其中55条范围内、7条范围外。
按三元组+断言匹配：TP=45、FP=10、FN=32，Precision=81.8%、Recall=58.4%、F1=68.2%。
再严格比较qualifiers，命中38条，F1=57.6%。

G编号为本段标准关系顺序，P编号为本段去重后的预测顺序。实际名称原样展示，名称不同可能仍通过实体别名对齐。
“命中”要求端点身份、类型、方向、谓词和断言正确；兼容/连接允许反向。限定差异不影响主指标TP，但影响严格限定分数。
未匹配候选仅在端点对应唯一时与标准并排，断言或谓词差异仍按原评分计FN+FP；该展示配对不改变评分。
每条标准与每条实际预测均展示一次。范围外预测只供复核，不计TP/FP；未匹配预测也不自动等于现实事实错误。

| 片段 | golden条数 | 实际条数 | 命中TP | 漏匹配FN | 未匹配预测FP | 范围外 | 限定也匹配 |
|---|---:|---:|---:|---:|---:|---:|---:|
| [EG001](#eg001) | 5 | 4 | 3 | 2 | 1 | 0 | 2 |
| [EG002](#eg002) | 2 | 2 | 2 | 0 | 0 | 0 | 2 |
| [EG003](#eg003) | 1 | 1 | 0 | 1 | 1 | 0 | 0 |
| [EG004](#eg004) | 3 | 1 | 1 | 2 | 0 | 0 | 1 |
| [EG005](#eg005) | 12 | 6 | 5 | 7 | 1 | 0 | 4 |
| [EG006](#eg006) | 5 | 8 | 5 | 0 | 0 | 3 | 5 |
| [EG007](#eg007) | 7 | 7 | 7 | 0 | 0 | 0 | 7 |
| [EG008](#eg008) | 4 | 6 | 3 | 1 | 3 | 0 | 3 |
| [EG009](#eg009) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| [EG010](#eg010) | 2 | 4 | 2 | 0 | 0 | 2 | 2 |
| [EG011](#eg011) | 1 | 1 | 0 | 1 | 1 | 0 | 0 |
| [EG012](#eg012) | 5 | 4 | 3 | 2 | 1 | 0 | 0 |
| [EG013](#eg013) | 4 | 0 | 0 | 4 | 0 | 0 | 0 |
| [EG014](#eg014) | 4 | 4 | 4 | 0 | 0 | 0 | 4 |
| [EG015](#eg015) | 1 | 0 | 0 | 1 | 0 | 0 | 0 |
| [EG016](#eg016) | 4 | 0 | 0 | 4 | 0 | 0 | 0 |
| [EG017](#eg017) | 8 | 5 | 4 | 4 | 1 | 0 | 2 |
| [EG018](#eg018) | 4 | 4 | 2 | 2 | 0 | 2 | 2 |
| [EG019](#eg019) | 4 | 4 | 3 | 1 | 1 | 0 | 3 |
| [EG020](#eg020) | 1 | 1 | 1 | 0 | 0 | 0 | 1 |
| 合计 | 77 | 62 | 45 | 32 | 10 | 7 | 38 |

<a id="eg001"></a>
## EG001 · 中文

[无人机还能不能飞？大疆Avata 360 ，室内VR全景拍摄生产力工具](https://www.youtube.com/watch?v=_6E4tJWhqBM) · 238.3–297.0秒

| 编号 | golden关系（含类型/断言/限定） | 实际抽取（含类型/断言/限定） | 对照判定 |
|---|---|---|---|
| G01 / P01 | DJI Avata 360 [product] → <code>contains</code> → Avata 360桨叶保护罩 [component]<br>asserted | DJI Avata 360 [product] → <code>contains</code> → DJI Avata 360 桨叶保护 [component]<br>asserted | 命中（含限定） |
| G02 / P03 | DJI Avata 360 [product] → <code>contains</code> → DJI Avata 360 下方传感器 [component]<br>asserted | DJI Avata 360 [product] → <code>contains</code> → DJI Avata 360 视觉避障传感器 [component]<br>asserted | 命中（含限定） |
| G03 / P02 | DJI Avata 360 [product] → <code>supports</code> → 视觉避障 [technology]<br>asserted | DJI Avata 360 [product] → <code>contains</code> → Avata 360 视觉避障系统 [technology]<br>asserted | 谓词/方向差异（FN+FP） |
| G04 / P04 | DJI Avata 360 [product] → <code>supports</code> → 全向避障 [technology]<br>asserted<br>modality=conditional<br>scope=全向避障功能呢<br>在拍摄360全景的时候会起作用 | DJI Avata 360 [product] → <code>supports</code> → Avata 360 全向避障功能 [technology]<br>asserted | 关系/断言命中；限定差异 |
| G05 | Avata 360桨叶保护罩 [component] → <code>mitigates</code> → 炸机 [fault]<br>asserted<br>modality=capability | — | 漏匹配FN；至少一个端点未对齐 |

<details>
<summary>展开本段原文与逐条证据</summary>

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

| 关系编号 | 标准证据 | 实际证据 |
|---|---|---|
| G01 / P01 | Avata360有桨叶保护的安全设计<br>被桨保包裹<br>不用担心飞转的桨叶伤人 | Avata360有桨叶保护的安全设计<br>被桨保包裹 |
| G02 / P03 | 避障Avata360有独立的视觉避障系统<br>下面呢还有传感器 | 避障Avata360有独立的视觉避障系统<br>下面呢还有传感器 |
| G03 / P02 | 避障Avata360有独立的视觉避障系统 | 避障Avata360有独立的视觉避障系统 |
| G04 / P04 | 避障Avata360有独立的视觉避障系统<br>下面呢还有传感器<br>全向避障功能呢<br>在拍摄360全景的时候会起作用<br>提示你四面八方潜在的障碍物<br>并且做出提示<br>甚至是刹停来确保安全 | 全向避障功能呢<br>在拍摄360全景的时候会起作用 |
| G05 | Avata360有桨叶保护的安全设计<br>被桨保包裹<br>不用担心飞转的桨叶伤人<br>更不怕碰到障碍物炸机 | — |

</details>

<a id="eg002"></a>
## EG002 · 中文

[大疆的全景无人机？！Avata360上手](https://www.youtube.com/watch?v=UxbQRuKiwes) · 0.0–58.788秒

| 编号 | golden关系（含类型/断言/限定） | 实际抽取（含类型/断言/限定） | 对照判定 |
|---|---|---|---|
| G01 / P01 | DJI Avata 360 [product] → <code>offered_by</code> → 大疆 [organization]<br>asserted | DJI Avata 360 [product] → <code>offered_by</code> → DJI [organization]<br>asserted | 命中（含限定） |
| G02 / P02 | DJI Avata 360 [product] → <code>contains</code> → Avata 360方形传感器 [component]<br>asserted | DJI Avata 360 [product] → <code>contains</code> → DJI Avata 360 方形传感器 [component]<br>asserted | 命中（含限定） |

<details>
<summary>展开本段原文与逐条证据</summary>

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

| 关系编号 | 标准证据 | 实际证据 |
|---|---|---|
| G01 / P01 | 大疆2026年开年首作就是全景无人机<br>这个Avata360<br>他们打磨了挺长的时间了 | 大疆2026年开年首作就是全景无人机<br>这个Avata360 |
| G02 / P02 | 这个Avata360<br>他们打磨了挺长的时间了<br>那么飞下来感觉就这个无人机<br>它的画质很好<br>有着我们熟悉的摇杆操作<br>也还可以用传统的遥控器来飞行<br>这都在我们预期之内<br>但什么在我们预期之外<br>是它的价格<br>这玩意单机身卖2788元<br>一架无人机有着超大的传感器<br>卖2788元<br>没错这个价位<br>它放了两块<br>1/1.1英寸的方形传感器 | 它放了两块<br>1/1.1英寸的方形传感器 |

</details>

<a id="eg003"></a>
## EG003 · 中文

[【3C老實說】DJI Avata 360 全景空拍機評測：人人都能一秒變身空拍高手，5大新玩法介紹、DJI Fly App / DJI Studio 操作教學](https://www.youtube.com/watch?v=BXiP0SVRezc) · 923.231–979.5秒

| 编号 | golden关系（含类型/断言/限定） | 实际抽取（含类型/断言/限定） | 对照判定 |
|---|---|---|---|
| G01 / P01 | DJI Studio [software] → <code>applied_to</code> → DJI Avata 360 [product]<br>asserted<br>modality=instruction | DJI Avata 360 [product] → <code>connected_to</code> → DJI Studio [software]<br>asserted | 谓词/方向差异（FN+FP） |

<details>
<summary>展开本段原文与逐条证据</summary>

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

| 关系编号 | 标准证据 | 实际证据 |
|---|---|---|
| G01 / P01 | 也可以直接將 Avata 360 透過 USB 連接到電腦中。<br>我們首先開啟 DJI Studio，<br>然後選擇設備連接。<br>現在已經顯示出我飛機裡頭所有檔案了，<br>我就可以選影片出來做後製。點了之後跳出預覽，<br>你可以做 360 度的操作，<br>然後你可以直接下載或是點選創作。<br>偵測到文件在外部設備會建議你先把素材轉到電腦的本地磁碟再進行剪輯。 | 也可以直接將 Avata 360 透過 USB 連接到電腦中。<br>我們首先開啟 DJI Studio，<br>然後選擇設備連接。 |

</details>

<a id="eg004"></a>
## EG004 · 中文

[【Yipee科技新聞點評】 大疆 Neo 2 最強宿敵登場？Insta360 摺疊口袋無人機專利曝光！掌上自拍空拍機大洗牌！](https://www.youtube.com/watch?v=9bd--Zcc33M) · 12.533–68.5秒

| 编号 | golden关系（含类型/断言/限定） | 实际抽取（含类型/断言/限定） | 对照判定 |
|---|---|---|---|
| G01 | DJI Neo 2 [product] → <code>offered_by</code> → DJI [organization]<br>asserted | — | 漏匹配FN；两端点已对齐 |
| G02 | DJI Flip [product] → <code>offered_by</code> → DJI [organization]<br>asserted | — | 漏匹配FN；两端点已对齐 |
| G03 / P01 | Insta360 Antigravity A1 [product] → <code>offered_by</code> → Insta360 [organization]<br>asserted | Antigravity A1 [product] → <code>offered_by</code> → Insta360 [organization]<br>asserted | 命中（含限定） |

<details>
<summary>展开本段原文与逐条证据</summary>

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

| 关系编号 | 标准证据 | 实际证据 |
|---|---|---|
| G01 | Insta360 傳出摺疊空拍機直接對戰 DJI 的 Neo 2 | — |
| G02 | 產品定位接近 DJI Neo 2 或 DJI Flip | — |
| G03 / P01 | Insta360 進軍消費級無人機市場<br>雖然目前只推出 360 度空拍機 Antigravity A1 | 雖然目前只推出 360 度空拍機 Antigravity A1 |

</details>

<a id="eg005"></a>
## EG005 · 中文

[【Yipee科技新聞點評】DJI Neo 3 發表時間與售價曝光｜全新 RC Mini 螢幕遙控器、懸吊電機結構、鏡頭升級全解析！對比 Neo 2 差在哪？最快 10 月發表！](https://www.youtube.com/watch?v=7NQZfyw-kNI) · 191.5–250.0秒

| 编号 | golden关系（含类型/断言/限定） | 实际抽取（含类型/断言/限定） | 对照判定 |
|---|---|---|---|
| G01 | DJI Neo 3 [product] → <code>offered_by</code> → DJI [organization]<br>asserted | — | 漏匹配FN；两端点已对齐 |
| G02 | DJI Neo 2 [product] → <code>offered_by</code> → DJI [organization]<br>asserted | — | 漏匹配FN；两端点已对齐 |
| G03 | DJI Neo系列 [product_family] → <code>offered_by</code> → DJI [organization]<br>asserted | — | 漏匹配FN；两端点已对齐 |
| G04 / P02 | DJI Neo系列 [product_family] → <code>has_model</code> → DJI Neo 2 [product]<br>asserted | DJI Neo series [product_family] → <code>has_model</code> → DJI Neo 2 [product]<br>asserted | 命中（含限定） |
| G05 / P05 | DJI Neo系列 [product_family] → <code>has_model</code> → DJI Neo 3 [product]<br>uncertain<br>modality=hypothetical<br>scope=按照這個週期推算<br>DJI Neo 3 可能要等到今年 12 月或 2027 年初才會亮相 | DJI Neo series [product_family] → <code>has_model</code> → DJI Neo 3 [product]<br>asserted | 断言差异（FN+FP） |
| G06 / P01 | DJI Neo 3 [product] → <code>contains</code> → Neo 3相机模组 [component]<br>uncertain<br>modality=hypothetical<br>scope=另外,DJI Neo 3 的相機模組看起來似乎比 Neo 2 更大 | DJI Neo 3 [product] → <code>contains</code> → DJI Neo 3 相機模組 [component]<br>uncertain<br>modality=hypothetical<br>scope=看起來似乎 | 关系/断言命中；限定差异 |
| G07 | DJI Neo 3 [product] → <code>contains</code> → Neo 3镜头 [component]<br>uncertain<br>modality=hypothetical<br>scope=另外,DJI Neo 3 的相機模組看起來似乎比 Neo 2 更大<br>鏡頭向前突出的幅度也比前一代明顯 | — | 漏匹配FN；至少一个端点未对齐 |
| G08 / P03 | DJI Neo 3 [product] → <code>contains</code> → Neo 3云台 [component]<br>uncertain<br>modality=hypothetical<br>scope=外媒因此推測 DJI 可能重新設計 Neo 3 的雲台或相機系統 | DJI Neo 3 [product] → <code>contains</code> → DJI Neo 3 雲台 [component]<br>uncertain<br>modality=hypothetical<br>scope=外媒因此推測 DJI 可能重新設計 Neo 3 的雲台或相機系統 | 命中（含限定） |
| G09 / P04 | DJI Neo 3 [product] → <code>contains</code> → Neo 3相机系统 [component]<br>uncertain<br>modality=hypothetical<br>scope=外媒因此推測 DJI 可能重新設計 Neo 3 的雲台或相機系統 | DJI Neo 3 [product] → <code>contains</code> → DJI Neo 3 相機系統 [component]<br>uncertain<br>modality=hypothetical<br>scope=外媒因此推測 DJI 可能重新設計 Neo 3 的雲台或相機系統 | 命中（含限定） |
| G10 | DJI Neo 3 [product] → <code>contains</code> → Neo 3感光元件 [component]<br>uncertain<br>modality=hypothetical<br>scope=但由於曝光的包裝盒上沒有標示感光元件尺寸、錄影規格、重量與續航<br>因此無法確認 DJI Neo 3 是否會繼續維持輕量化設計<br>也無法證實先前傳出搭載 1/1.3 或 1/1.1 吋感光元件的消息是否屬實 | — | 漏匹配FN；至少一个端点未对齐 |
| G11 | DJI Neo [product] → <code>offered_by</code> → DJI [organization]<br>asserted | — | 漏匹配FN；两端点已对齐 |
| G12 / P06 | DJI Neo系列 [product_family] → <code>has_model</code> → DJI Neo [product]<br>asserted | DJI Neo series [product_family] → <code>has_model</code> → DJI Neo [product]<br>asserted | 命中（含限定） |

<details>
<summary>展开本段原文与逐条证据</summary>

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

| 关系编号 | 标准证据 | 实际证据 |
|---|---|---|
| G01 | 另外,DJI Neo 3 的相機模組看起來似乎比 Neo 2 更大 | — |
| G02 | DJI Neo 2 則是在 2025 年 10 月底推出 | — |
| G03 | 依照 DJI Neo 系列過去的更新週期 | — |
| G04 / P02 | 依照 DJI Neo 系列過去的更新週期<br>第一代 DJI Neo 在 2024 年 9 月推出<br>DJI Neo 2 則是在 2025 年 10 月底推出<br>兩代相隔約 14 個月 | DJI Neo 2 則是在 2025 年 10 月底推出 |
| G05 / P05 | 依照 DJI Neo 系列過去的更新週期<br>第一代 DJI Neo 在 2024 年 9 月推出<br>DJI Neo 2 則是在 2025 年 10 月底推出<br>兩代相隔約 14 個月<br>按照這個週期推算<br>DJI Neo 3 可能要等到今年 12 月或 2027 年初才會亮相 | 第一代 DJI Neo 在 2024 年 9 月推出<br>DJI Neo 2 則是在 2025 年 10 月底推出<br>兩代相隔約 14 個月<br>按照這個週期推算<br>DJI Neo 3 可能要等到今年 12 月或 2027 年初才會亮相 |
| G06 / P01 | 另外,DJI Neo 3 的相機模組看起來似乎比 Neo 2 更大 | DJI Neo 3 的相機模組看起來似乎比 Neo 2 更大 |
| G07 | 另外,DJI Neo 3 的相機模組看起來似乎比 Neo 2 更大<br>鏡頭向前突出的幅度也比前一代明顯 | — |
| G08 / P03 | 外媒因此推測 DJI 可能重新設計 Neo 3 的雲台或相機系統 | 外媒因此推測 DJI 可能重新設計 Neo 3 的雲台或相機系統 |
| G09 / P04 | 外媒因此推測 DJI 可能重新設計 Neo 3 的雲台或相機系統 | 外媒因此推測 DJI 可能重新設計 Neo 3 的雲台或相機系統 |
| G10 | 外媒因此推測 DJI 可能重新設計 Neo 3 的雲台或相機系統<br>但由於曝光的包裝盒上沒有標示感光元件尺寸、錄影規格、重量與續航<br>因此無法確認 DJI Neo 3 是否會繼續維持輕量化設計<br>也無法證實先前傳出搭載 1/1.3 或 1/1.1 吋感光元件的消息是否屬實 | — |
| G11 | 第一代 DJI Neo 在 2024 年 9 月推出 | — |
| G12 / P06 | 依照 DJI Neo 系列過去的更新週期<br>第一代 DJI Neo 在 2024 年 9 月推出<br>DJI Neo 2 則是在 2025 年 10 月底推出<br>兩代相隔約 14 個月 | 第一代 DJI Neo 在 2024 年 9 月推出<br>DJI Neo 2 則是在 2025 年 10 月底推出 |

</details>

<a id="eg006"></a>
## EG006 · 中文

[2026 新手無人機別亂買！DJI Neo 2 vs Lito 1 vs Lito X1 誰才是性價比之王？](https://www.youtube.com/watch?v=A9n3aAoDDBg) · 249.94–309.24秒

| 编号 | golden关系（含类型/断言/限定） | 实际抽取（含类型/断言/限定） | 对照判定 |
|---|---|---|---|
| G01 / P05 | DJI Neo 2 [product] → <code>uses</code> → CMOS [technology]<br>asserted | DJI Neo 2 [product] → <code>uses</code> → CMOS [technology]<br>asserted | 命中（含限定） |
| G02 / P06 | DJI Mini 4 Pro [product] → <code>compatible_with</code> → Mini 4 Pro长续航电池 [component]<br>asserted | DJI Mini 4 Pro [product] → <code>compatible_with</code> → 长续航电池 [component]<br>asserted | 命中（含限定） |
| G03 / P07 | DJI Mini 4 Pro [product] → <code>contains</code> → Mini 4 Pro三轴机械云台 [component]<br>asserted | DJI Mini 4 Pro [product] → <code>contains</code> → 三轴机械云台 [component]<br>asserted | 命中（含限定） |
| G04 / P08 | DJI Mini 4 Pro [product] → <code>supports</code> → 视觉全向避障 [technology]<br>asserted | DJI Mini 4 Pro [product] → <code>supports</code> → 视觉全向避障 [technology]<br>asserted | 命中（含限定） |
| G05 / P03 | DJI Mini 4 Pro [product] → <code>uses</code> → CMOS [technology]<br>asserted | DJI Mini 4 Pro [product] → <code>uses</code> → CMOS [technology]<br>asserted | 命中（含限定） |
| P01 | 标准实体范围外 | 1/1.3 英寸 CMOS 感光元件 [component] → <code>uses</code> → CMOS [technology]<br>asserted | 范围外；未计分 |
| P02 | 标准实体范围外 | DJI Mini 4 Pro [product] → <code>contains</code> → 1/1.3 英寸 CMOS 感光元件 [component]<br>asserted | 范围外；未计分 |
| P04 | 标准实体范围外 | DJI Neo 2 [product] → <code>uses</code> → 1/1.3 英寸 CMOS 感光元件 [component]<br>asserted | 范围外；未计分 |

<details>
<summary>展开本段原文与逐条证据</summary>

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

| 关系编号 | 标准证据 | 实际证据 |
|---|---|---|
| G01 / P05 | 跟大家讲讲 Mini 4 Pro 的规格<br>它定位为可折叠入门级无人机<br>重量 249 克<br>感光元件为 1/1.3 英寸 CMOS<br>和 Neo 2 是一样的 | 和 Neo 2 是一样的 |
| G02 / P06 | 跟大家讲讲 Mini 4 Pro 的规格<br>它定位为可折叠入门级无人机<br>重量 249 克<br>感光元件为 1/1.3 英寸 CMOS<br>和 Neo 2 是一样的<br>像素为 4800 万<br>也就是 4800 万像素<br>镜头焦距 24mm<br>光圈 f/1.7<br>搭配长续航电池最长飞行时间可达 45 分钟 | 搭配长续航电池最长飞行时间可达 45 分钟 |
| G03 / P07 | 跟大家讲讲 Mini 4 Pro 的规格<br>它定位为可折叠入门级无人机<br>重量 249 克<br>感光元件为 1/1.3 英寸 CMOS<br>和 Neo 2 是一样的<br>像素为 4800 万<br>也就是 4800 万像素<br>镜头焦距 24mm<br>光圈 f/1.7<br>搭配长续航电池最长飞行时间可达 45 分钟<br>支持 4K 60p/100p 等多种拍摄规格<br>同时配备三轴机械云台 | 同时配备三轴机械云台 |
| G04 / P08 | 同时配备三轴机械云台<br>它还具备全向避障功能<br>视觉全向避障<br>Mini 4 Pro 的定位则比 Neo 2 稍高一些 | 它还具备全向避障功能<br>视觉全向避障 |
| G05 / P03 | 跟大家讲讲 Mini 4 Pro 的规格<br>它定位为可折叠入门级无人机<br>重量 249 克<br>感光元件为 1/1.3 英寸 CMOS<br>和 Neo 2 是一样的 | 感光元件为 1/1.3 英寸 CMOS |
| P01 | — | 感光元件为 1/1.3 英寸 CMOS |
| P02 | — | 感光元件为 1/1.3 英寸 CMOS |
| P04 | — | 和 Neo 2 是一样的 |

</details>

<a id="eg007"></a>
## EG007 · 中文

[CP值最高的入門空拍機！DJI Lito X1 & Lito 1 完整評測｜免登記免考照、光達避障、D-Log M全都有 #dji  #DJILitoX1 #肯吉老王開箱](https://www.youtube.com/watch?v=2VqkvtTfK5g) · 61.833–121.266秒

| 编号 | golden关系（含类型/断言/限定） | 实际抽取（含类型/断言/限定） | 对照判定 |
|---|---|---|---|
| G01 / P05 | DJI Lito 1 [product] → <code>offered_by</code> → DJI [organization]<br>asserted | DJI Lito 1 [product] → <code>offered_by</code> → DJI [organization]<br>asserted | 命中（含限定） |
| G02 / P04 | DJI Lito X1 [product] → <code>offered_by</code> → DJI [organization]<br>asserted | DJI Lito X1 [product] → <code>offered_by</code> → DJI [organization]<br>asserted | 命中（含限定） |
| G03 / P02 | DJI Mini 3 [product] → <code>offered_by</code> → DJI [organization]<br>asserted | DJI Mini 3 [product] → <code>offered_by</code> → DJI [organization]<br>asserted | 命中（含限定） |
| G04 / P03 | DJI Mini 4K [product] → <code>offered_by</code> → DJI [organization]<br>asserted | DJI Mini 4K [product] → <code>offered_by</code> → DJI [organization]<br>asserted | 命中（含限定） |
| G05 / P01 | DJI Lito系列 [product_family] → <code>offered_by</code> → DJI [organization]<br>asserted | DJI Lito系列 [product_family] → <code>offered_by</code> → DJI [organization]<br>asserted | 命中（含限定） |
| G06 / P07 | DJI Lito系列 [product_family] → <code>has_model</code> → DJI Lito 1 [product]<br>asserted | DJI Lito系列 [product_family] → <code>has_model</code> → DJI Lito 1 [product]<br>asserted | 命中（含限定） |
| G07 / P06 | DJI Lito系列 [product_family] → <code>has_model</code> → DJI Lito X1 [product]<br>asserted | DJI Lito系列 [product_family] → <code>has_model</code> → DJI Lito X1 [product]<br>asserted | 命中（含限定） |

<details>
<summary>展开本段原文与逐条证据</summary>

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

| 关系编号 | 标准证据 | 实际证据 |
|---|---|---|
| G01 / P05 | 先來說一下為什麼DJI<br>會有這一次的Lito系列<br>DJI呢把過往的Mini 3<br>或者是Mini 4K<br>這種平價機種全部整合起來<br>把它變成一個新的系列<br>就是今天要講的Lito 1<br>那Lito X1是高價版<br>Lito 1是平價版 | 先來說一下為什麼DJI<br>會有這一次的Lito系列 |
| G02 / P04 | 先來說一下為什麼DJI<br>會有這一次的Lito系列<br>DJI呢把過往的Mini 3<br>或者是Mini 4K<br>這種平價機種全部整合起來<br>把它變成一個新的系列<br>就是今天要講的Lito 1<br>那Lito X1是高價版<br>Lito 1是平價版 | 先來說一下為什麼DJI<br>會有這一次的Lito系列 |
| G03 / P02 | 先來說一下為什麼DJI<br>會有這一次的Lito系列<br>DJI呢把過往的Mini 3<br>或者是Mini 4K<br>這種平價機種全部整合起來<br>把它變成一個新的系列<br>就是今天要講的Lito 1<br>那Lito X1是高價版<br>Lito 1是平價版 | DJI呢把過往的Mini 3<br>或者是Mini 4K<br>這種平價機種全部整合起來 |
| G04 / P03 | 先來說一下為什麼DJI<br>會有這一次的Lito系列<br>DJI呢把過往的Mini 3<br>或者是Mini 4K<br>這種平價機種全部整合起來<br>把它變成一個新的系列<br>就是今天要講的Lito 1<br>那Lito X1是高價版<br>Lito 1是平價版 | DJI呢把過往的Mini 3<br>或者是Mini 4K<br>這種平價機種全部整合起來 |
| G05 / P01 | 先來說一下為什麼DJI<br>會有這一次的Lito系列<br>DJI呢把過往的Mini 3<br>或者是Mini 4K<br>這種平價機種全部整合起來<br>把它變成一個新的系列<br>就是今天要講的Lito 1<br>那Lito X1是高價版<br>Lito 1是平價版 | DJI呢把過往的Mini 3<br>或者是Mini 4K<br>這種平價機種全部整合起來<br>把它變成一個新的系列<br>就是今天要講的Lito 1 |
| G06 / P07 | 先來說一下為什麼DJI<br>會有這一次的Lito系列<br>DJI呢把過往的Mini 3<br>或者是Mini 4K<br>這種平價機種全部整合起來<br>把它變成一個新的系列<br>就是今天要講的Lito 1<br>那Lito X1是高價版<br>Lito 1是平價版 | 就是今天要講的Lito 1<br>那Lito X1是高價版<br>Lito 1是平價版 |
| G07 / P06 | 先來說一下為什麼DJI<br>會有這一次的Lito系列<br>DJI呢把過往的Mini 3<br>或者是Mini 4K<br>這種平價機種全部整合起來<br>把它變成一個新的系列<br>就是今天要講的Lito 1<br>那Lito X1是高價版<br>Lito 1是平價版 | 就是今天要講的Lito 1<br>那Lito X1是高價版<br>Lito 1是平價版 |

</details>

<a id="eg008"></a>
## EG008 · 中文

[2788元的新形态无人机，好用吗？DJI Flip上手！](https://www.youtube.com/watch?v=jrvhN6yNovA) · 268.397–328.157秒

| 编号 | golden关系（含类型/断言/限定） | 实际抽取（含类型/断言/限定） | 对照判定 |
|---|---|---|---|
| G01 / P01 | DJI Flip [product] → <code>contains</code> → Flip传感器 [component]<br>asserted | DJI Flip [product] → <code>contains</code> → DJI Flip 传感器 [component]<br>asserted | 命中（含限定） |
| G02 / P02 | DJI Flip [product] → <code>contains</code> → Flip广角镜头 [component]<br>asserted | DJI Flip [product] → <code>contains</code> → DJI Flip 广角镜头 [component]<br>asserted | 命中（含限定） |
| G03 / P03 | DJI Flip [product] → <code>offered_by</code> → 大疆 [organization]<br>asserted | DJI Flip [product] → <code>offered_by</code> → DJI [organization]<br>asserted | 命中（含限定） |
| G04 | DJI Mavic 3 Pro [product] → <code>offered_by</code> → 大疆 [organization]<br>asserted | — | 漏匹配FN；两端点已对齐 |
| P04 | — | DJI Flip 传感器 [component] → <code>uses</code> → DJI Mini 4 Pro [product]<br>asserted | 未匹配预测FP |
| P05 | — | DJI Flip 传感器 [component] → <code>uses</code> → DJI Action 4 [product]<br>uncertain<br>modality=hypothetical<br>scope=Action 4还有那些别的各种机型<br>都有可能用到这块传感器 | 未匹配预测FP |
| P06 | — | DJI Flip [product] → <code>supports</code> → HDR [technology]<br>asserted | 未匹配预测FP |

<details>
<summary>展开本段原文与逐条证据</summary>

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

| 关系编号 | 标准证据 | 实际证据 |
|---|---|---|
| G01 / P01 | DJI Flip搭载的传感器<br>是1/1.3英寸的传感器 | DJI Flip搭载的传感器<br>是1/1.3英寸的传感器 |
| G02 / P02 | DJI Flip搭载的传感器<br>是1/1.3英寸的传感器<br>还有等效24mm f1.7的广角镜头 | DJI Flip搭载的传感器<br>是1/1.3英寸的传感器 |
| G03 / P03 | DJI Flip搭载的传感器<br>是1/1.3英寸的传感器<br>还有等效24mm f1.7的广角镜头<br>有没有听着很熟悉呢<br>答案就是这块传感器我们已经测麻了<br>它是大疆Mavic 3 Pro的中焦镜头<br>Air 3的广角和长焦<br>Air 3S的长焦<br>Mini 3、Mini 4 Pro的广角<br>Action 4还有那些别的各种机型<br>都有可能用到这块传感器<br>那优点就是经过这么多的迭代<br>大疆已经把它调教到了一个登峰造极的地步 | DJI Flip搭载的传感器 |
| G04 | 它是大疆Mavic 3 Pro的中焦镜头 | — |
| P04 | — | Mini 3、Mini 4 Pro的广角 |
| P05 | — | Action 4还有那些别的各种机型<br>都有可能用到这块传感器 |
| P06 | — | 它目前最高支持拍摄4K 60帧的HDR视频 |

</details>

<a id="eg009"></a>
## EG009 · 中文

[从豁然开朗到“死黑一片”：这台Mini 4K无人机，怎么好像也有点“近视”？ #广东麦克疯#越南 #航拍機 #老挝旅游 #越南旅遊](https://www.youtube.com/watch?v=7MJjAlpvj7w) · 0.0–58.6秒

| 编号 | golden关系（含类型/断言/限定） | 实际抽取（含类型/断言/限定） | 对照判定 |
|---|---|---|---|
| — | 无关系 | 无关系 | 双方均为空 |

<details>
<summary>展开本段原文与逐条证据</summary>

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

| 关系编号 | 标准证据 | 实际证据 |
|---|---|---|

</details>

<a id="eg010"></a>
## EG010 · 中文

[Inside a DJI Store in China with Prices 🇨🇳 DJI Pocket 4P + MORE (2026)](https://www.youtube.com/watch?v=TtfVuyfODLU) · 737.84–795.2秒

| 编号 | golden关系（含类型/断言/限定） | 实际抽取（含类型/断言/限定） | 对照判定 |
|---|---|---|---|
| G01 / P04 | DJI Mavic 4 Pro [product] → <code>compatible_with</code> → RC 2 [product]<br>asserted | DJI Mavic 4<br>Pro [product] → <code>compatible_with</code> → RC 2 [product]<br>asserted | 命中（含限定） |
| G02 / P02 | DJI Neo 2 [product] → <code>compatible_with</code> → 护目镜 [product]<br>asserted | DJI Neo 2 [product] → <code>compatible_with</code> → 护目镜 [product]<br>asserted | 命中（含限定） |
| P01 | 标准实体范围外 | DJI Mavic 4<br>Pro [product] → <code>offered_by</code> → DJI [organization]<br>asserted | 范围外；未计分 |
| P03 | 标准实体范围外 | DJI Neo 2 [product] → <code>offered_by</code> → DJI [organization]<br>asserted | 范围外；未计分 |

<details>
<summary>展开本段原文与逐条证据</summary>

```text
戴上它。这就是你操控无人机的方式。这里还有一个遥控器。
我喜欢他们提供的酒精湿巾，这样你就可以在
店里清洁和试戴。令人惊喜的是，这款护目镜也兼容我们的无人机 Neo 2。好了，
跟我来。接下来，我们当然不能错过 DJI Mavic 4
Pro。看看这个庞然大物，真是令人印象深刻。DJI Mavic 4 Pro 搭配
RC 2 遥控器的售价是 13,888 元人民币。下一个套装是 17,888 元人民币，我记得里面包含
额外的电池组和其他一些配件。DJI Mavic 4 Pro 的终极套装售价是 23,988
```

| 关系编号 | 标准证据 | 实际证据 |
|---|---|---|
| G01 / P04 | Pro。看看这个庞然大物，真是令人印象深刻。DJI Mavic 4 Pro 搭配<br>RC 2 遥控器的售价是 13,888 元人民币。下一个套装是 17,888 元人民币，我记得里面包含 | DJI Mavic 4 Pro 搭配<br>RC 2 遥控器的售价是 13,888 元人民币 |
| G02 / P02 | 店里清洁和试戴。令人惊喜的是，这款护目镜也兼容我们的无人机 Neo 2。好了， | 这款护目镜也兼容我们的无人机 Neo 2 |
| P01 | — | DJI Mavic 4<br>Pro |
| P03 | — | 我们的无人机 Neo 2 |

</details>

<a id="eg011"></a>
## EG011 · 英文

[How to Trippy Spin with DJI Neo 2 (FPV Stick Tutorial) 🛸​#DJINeo2 #TrippySpin  #DJIFPV #djineo](https://www.youtube.com/watch?v=qAgye8oJBKA) · 0.56–18.32秒

| 编号 | golden关系（含类型/断言/限定） | 实际抽取（含类型/断言/限定） | 对照判定 |
|---|---|---|---|
| G01 / P01 | Trippy Spin [method] → <code>applied_to</code> → DJI Neo 2 [product]<br>asserted<br>modality=instruction | DJI Neo 2 [product] → <code>supports</code> → trippy spin [method]<br>asserted<br>modality=instruction | 谓词/方向差异（FN+FP） |

<details>
<summary>展开本段原文与逐条证据</summary>

```text
How to perform a trippy spin with DJI Neo 2. [screaming] The obstacle must
be at least 5 m high. Camera is [music] pan to 30°. Punch up and pitch down when
nearing the pole. Throttle diagonally and roll to the side. Focus on pole
and adjust sticks accordingly. When done, lower the speed and level up.
```

| 关系编号 | 标准证据 | 实际证据 |
|---|---|---|
| G01 / P01 | How to perform a trippy spin with DJI Neo 2. [screaming] The obstacle must<br>be at least 5 m high. Camera is [music] pan to 30°. Punch up and pitch down when<br>nearing the pole. Throttle diagonally and roll to the side. Focus on pole<br>and adjust sticks accordingly. When done, lower the speed and level up. | How to perform a trippy spin with DJI Neo 2. |

</details>

<a id="eg012"></a>
## EG012 · 英文

[Fix Your DJI Air 3 Drone Like a Pro: Gimbal Ribbon Cable Replacement Guide](https://www.youtube.com/watch?v=_VofSIgvXOc) · 40.32–89.92秒

| 编号 | golden关系（含类型/断言/限定） | 实际抽取（含类型/断言/限定） | 对照判定 |
|---|---|---|---|
| G01 / P02 | DJI Air 3 [product] → <code>contains</code> → DJI Air 3 Battery [component]<br>asserted | DJI Air 3 [product] → <code>contains</code> → battery [component]<br>asserted<br>modality=instruction | 关系/断言命中；限定差异 |
| G02 / P01 | DJI Air 3 [product] → <code>contains</code> → DJI Air 3 Screws [component]<br>asserted | DJI Air 3 [product] → <code>contains</code> → screws [component]<br>asserted<br>modality=instruction | 关系/断言命中；限定差异 |
| G03 / P04 | DJI Air 3 [product] → <code>contains</code> → bottom cover [component]<br>asserted | DJI Air 3 [product] → <code>contains</code> → bottom cover [component]<br>asserted<br>modality=instruction | 关系/断言命中；限定差异 |
| G04 | DJI Air 3 [product] → <code>contains</code> → drone body  [component]<br>asserted | — | 漏匹配FN；至少一个端点未对齐 |
| G05 / P03 | DJI Air 3 Screws [component] → <code>connected_to</code> → bottom cover [component]<br>asserted | bottom cover [component] → <code>contains</code> → screws [component]<br>asserted<br>modality=instruction | 谓词/方向差异（FN+FP） |

<details>
<summary>展开本段原文与逐条证据</summary>

```text
approach will greatly assist you in successfully reassembling your DJI Air 3 drone. And you need
not worry about misplacing any screws; they will all stay securely on the magnetic mat.
Remove the battery before you start disassembling the DJI Air 3 drone.
Flip your drone over and remove the screws securing the bottom cover.
Remember, each screw might be a slightly different size, so keep track of where they go.
Use plastic tools to carefully open the drone body and ensure safe handling.
```

| 关系编号 | 标准证据 | 实际证据 |
|---|---|---|
| G01 / P02 | Remove the battery before you start disassembling the DJI Air 3 drone. | Remove the battery before you start disassembling the DJI Air 3 drone. |
| G02 / P01 | approach will greatly assist you in successfully reassembling your DJI Air 3 drone. And you need<br>not worry about misplacing any screws; they will all stay securely on the magnetic mat.<br>Remove the battery before you start disassembling the DJI Air 3 drone.<br>Flip your drone over and remove the screws securing the bottom cover. | Flip your drone over and remove the screws securing the bottom cover. |
| G03 / P04 | Remove the battery before you start disassembling the DJI Air 3 drone.<br>Flip your drone over and remove the screws securing the bottom cover. | Flip your drone over and remove the screws securing the bottom cover. |
| G04 | approach will greatly assist you in successfully reassembling your DJI Air 3 drone. And you need<br>not worry about misplacing any screws; they will all stay securely on the magnetic mat.<br>Remove the battery before you start disassembling the DJI Air 3 drone.<br>Flip your drone over and remove the screws securing the bottom cover.<br>Remember, each screw might be a slightly different size, so keep track of where they go.<br>Use plastic tools to carefully open the drone body and ensure safe handling. | — |
| G05 / P03 | approach will greatly assist you in successfully reassembling your DJI Air 3 drone. And you need<br>not worry about misplacing any screws; they will all stay securely on the magnetic mat.<br>Remove the battery before you start disassembling the DJI Air 3 drone.<br>Flip your drone over and remove the screws securing the bottom cover. | Flip your drone over and remove the screws securing the bottom cover. |

</details>

<a id="eg013"></a>
## EG013 · 英文

[DJI Mini 4 Pro｜First Use Guide](https://www.youtube.com/watch?v=JtYyP_y5Az0) · 4.08–58.8秒

| 编号 | golden关系（含类型/断言/限定） | 实际抽取（含类型/断言/限定） | 对照判定 |
|---|---|---|---|
| G01 | DJI Mini 4 Pro [product] → <code>packaged_with</code> → remote controller [product]<br>asserted | — | 漏匹配FN；至少一个端点未对齐 |
| G02 | DJI Fly [software] → <code>applied_to</code> → DJI Mini 4 Pro [product]<br>asserted<br>modality=instruction | — | 漏匹配FN；两端点已对齐 |
| G03 | DJI Mini 4 Pro [product] → <code>packaged_with</code> → propeller holder [component]<br>asserted | — | 漏匹配FN；至少一个端点未对齐 |
| G04 | DJI Mini 4 Pro [product] → <code>packaged_with</code> → gimbal protector [component]<br>asserted | — | 漏匹配FN；至少一个端点未对齐 |

<details>
<summary>展开本段原文与逐条证据</summary>

```text
thank you
welcome to DJI Mini 4 Pro tutorial
we will guide you through the unboxing and first use process of the product
open the package and take out the aircraft remote controller and accessories
downloading the DJI fly app scan the QR code on the package to download and install the DJI fly
app you can also watch the tutorial videos and read the user manual after scanning the QR code
remove the propeller holder and gimbal protector unfold the aircraft arms and turn
```

| 关系编号 | 标准证据 | 实际证据 |
|---|---|---|
| G01 | welcome to DJI Mini 4 Pro tutorial<br>we will guide you through the unboxing and first use process of the product<br>open the package and take out the aircraft remote controller and accessories | — |
| G02 | welcome to DJI Mini 4 Pro tutorial<br>we will guide you through the unboxing and first use process of the product<br>open the package and take out the aircraft remote controller and accessories<br>downloading the DJI fly app scan the QR code on the package to download and install the DJI fly<br>app you can also watch the tutorial videos and read the user manual after scanning the QR code | — |
| G03 | welcome to DJI Mini 4 Pro tutorial<br>we will guide you through the unboxing and first use process of the product<br>open the package and take out the aircraft remote controller and accessories<br>downloading the DJI fly app scan the QR code on the package to download and install the DJI fly<br>app you can also watch the tutorial videos and read the user manual after scanning the QR code<br>remove the propeller holder and gimbal protector unfold the aircraft arms and turn | — |
| G04 | welcome to DJI Mini 4 Pro tutorial<br>we will guide you through the unboxing and first use process of the product<br>open the package and take out the aircraft remote controller and accessories<br>downloading the DJI fly app scan the QR code on the package to download and install the DJI fly<br>app you can also watch the tutorial videos and read the user manual after scanning the QR code<br>remove the propeller holder and gimbal protector unfold the aircraft arms and turn | — |

</details>

<a id="eg014"></a>
## EG014 · 英文

[DJI Made Flipping a Drone Look This Easy](https://www.youtube.com/watch?v=AWQJTxxNpAI) · 0.13–24.075秒

| 编号 | golden关系（含类型/断言/限定） | 实际抽取（含类型/断言/限定） | 对照判定 |
|---|---|---|---|
| G01 / P01 | DJI Avata 2 [product] → <code>offered_by</code> → DJI [organization]<br>asserted | DJI Avata 2 [product] → <code>offered_by</code> → DJI [organization]<br>asserted | 命中（含限定） |
| G02 / P02 | DJI Avata 2 [product] → <code>compatible_with</code> → Goggles 3 [product]<br>asserted | DJI Avata 2 [product] → <code>compatible_with</code> → Goggles 3 [product]<br>asserted | 命中（含限定） |
| G03 / P03 | DJI Avata 2 [product] → <code>compatible_with</code> → RC Motion 3 [product]<br>asserted | DJI Avata 2 [product] → <code>compatible_with</code> → RC<br>Motion 3 [product]<br>asserted | 命中（含限定） |
| G04 / P04 | DJI Avata 2 [product] → <code>supports</code> → Easy ACRO [technology]<br>asserted | DJI Avata 2 [product] → <code>supports</code> → Easy ACRO [technology]<br>asserted | 命中（含限定） |

<details>
<summary>展开本段原文与逐条证据</summary>

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

| 关系编号 | 标准证据 | 实际证据 |
|---|---|---|
| G01 / P01 | DJI's Avata 2 makes flying feel<br>like sitting inside the camera. | DJI's Avata 2 makes flying feel<br>like sitting inside the camera. |
| G02 / P02 | DJI's Avata 2 makes flying feel<br>like sitting inside the camera.<br>Pair it with Goggles 3 and RC<br>Motion 3. Your hand steers the view, | Pair it with Goggles 3 and RC<br>Motion 3. |
| G03 / P03 | DJI's Avata 2 makes flying feel<br>like sitting inside the camera.<br>Pair it with Goggles 3 and RC<br>Motion 3. Your hand steers the view, | Pair it with Goggles 3 and RC<br>Motion 3. |
| G04 / P04 | DJI's Avata 2 makes flying feel<br>like sitting inside the camera.<br>Pair it with Goggles 3 and RC<br>Motion 3. Your hand steers the view,<br>and Easy ACRO gives you flips, rolls,<br>and a one hundred eighty degree drift. | Easy ACRO gives you flips, rolls,<br>and a one hundred eighty degree drift. |

</details>

<a id="eg015"></a>
## EG015 · 英文

[Parrot Anafi 4k Drone - Photographer's  Review](https://www.youtube.com/watch?v=8PjamSNj4BA) · 57.36–115.22秒

| 编号 | golden关系（含类型/断言/限定） | 实际抽取（含类型/断言/限定） | 对照判定 |
|---|---|---|---|
| G01 | Parrot ANAFI [product] → <code>compatible_with</code> → ANAFI遥控器 [product]<br>asserted | — | 漏匹配FN；两端点已对齐 |

<details>
<summary>展开本段原文与逐条证据</summary>

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

| 关系编号 | 标准证据 | 实际证据 |
|---|---|---|
| G01 | This is all that I use for the Parrot Anafi you've got the charging<br>cable USB "C" I've got a spare battery I've got the drone itself<br>I've got the USB cable for the iPhone or the whichever phone you have and then I've got the controller | — |

</details>

<a id="eg016"></a>
## EG016 · 英文

[Crashed? Here's How to Replace DJI Mini Drone Propellers / 3 and 4 Pro](https://www.youtube.com/watch?v=XUoFNFt8Mng) · 0.16–17.561秒

| 编号 | golden关系（含类型/断言/限定） | 实际抽取（含类型/断言/限定） | 对照判定 |
|---|---|---|---|
| G01 | DJI Mini 3 [product] → <code>contains</code> → propellers [component]<br>asserted | — | 漏匹配FN；两端点已对齐 |
| G02 | DJI Mini 3 [product] → <code>contains</code> → striped arms [component]<br>asserted | — | 漏匹配FN；至少一个端点未对齐 |
| G03 | DJI Mini 3 [product] → <code>contains</code> → striped propellers [component]<br>asserted | — | 漏匹配FN；两端点已对齐 |
| G04 | striped propellers [component] → <code>installed_on</code> → striped arms [component]<br>asserted<br>modality=instruction | — | 漏匹配FN；至少一个端点未对齐 |

<details>
<summary>展开本段原文与逐条证据</summary>

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

| 关系编号 | 标准证据 | 实际证据 |
|---|---|---|
| G01 | Crashed your DJI Mini 3 or 4 drone?<br>Let's fix it by replacing the propellers.<br>Here's the key: they're mirrored, so two turn clockwise and two counterclockwise.<br>Check the curved markings - the striped arms get the striped propellers. | — |
| G02 | Crashed your DJI Mini 3 or 4 drone?<br>Let's fix it by replacing the propellers.<br>Here's the key: they're mirrored, so two turn clockwise and two counterclockwise.<br>Check the curved markings - the striped arms get the striped propellers. | — |
| G03 | Crashed your DJI Mini 3 or 4 drone?<br>Let's fix it by replacing the propellers.<br>Here's the key: they're mirrored, so two turn clockwise and two counterclockwise.<br>Check the curved markings - the striped arms get the striped propellers. | — |
| G04 | Check the curved markings - the striped arms get the striped propellers. | — |

</details>

<a id="eg017"></a>
## EG017 · 英文

[DJI Air 3: The Complete Disassembly and Reassembly Guide](https://www.youtube.com/watch?v=8k8JhMTr9xc) · 741.95–787.49秒

| 编号 | golden关系（含类型/断言/限定） | 实际抽取（含类型/断言/限定） | 对照判定 |
|---|---|---|---|
| G01 / P04 | DJI Air 3 [product] → <code>contains</code> → Air 3电调板 [component]<br>asserted | DJI Air 3 [product] → <code>contains</code> → DJI Air 3 ESC board [component]<br>asserted | 命中（含限定） |
| G02 | DJI Air 3 [product] → <code>contains</code> → Air 3 motors [component]<br>asserted | — | 漏匹配FN；两端点已对齐 |
| G03 / P02 | Air 3电调板 [component] → <code>controls</code> → Air 3 motors [component]<br>asserted | DJI Air 3 ESC board [component] → <code>controls</code> → DJI Air 3 motors [component]<br>asserted | 命中（含限定） |
| G04 / P03 | DJI Air 3 [product] → <code>uses</code> → firmware [software]<br>asserted | DJI Air 3 [product] → <code>uses</code> → DJI Air 3 firmware [software]<br>asserted<br>modality=conditional<br>scope=If your DJI Air 3 drone has an ESC error and you can't fix this error by refreshing the<br>drone firmware | 关系/断言命中；限定差异 |
| G05 / P01 | DJI Air 3 [product] → <code>reports_fault</code> → ESC error [fault]<br>asserted<br>modality=conditional<br>scope=If your DJI Air 3 drone has an ESC error and you can't fix this error by refreshing the<br>drone firmware, it is most likely the ESC board is failing, so you need to replace it. | DJI Air 3 [product] → <code>reports_fault</code> → DJI Air 3 ESC error [fault]<br>asserted<br>modality=conditional<br>scope=If your DJI Air 3 drone has an ESC error and you can't fix this error by refreshing the<br>drone firmware | 关系/断言命中；限定差异 |
| G06 | DJI Air 3 [product] → <code>offered_by</code> → DJI [organization]<br>asserted | — | 漏匹配FN；两端点已对齐 |
| G07 | DJI Air 3 [product] → <code>contains</code> → DJI Air 3 arm wires [component]<br>asserted | — | 漏匹配FN；两端点已对齐 |
| G08 | DJI Air 3 arm wires [component] → <code>connected_to</code> → Air 3电调板 [component]<br>asserted | — | 漏匹配FN；两端点已对齐 |
| P05 | — | DJI Air 3 ESC board [component] → <code>reports_fault</code> → DJI Air 3 ESC error [fault]<br>uncertain<br>modality=conditional<br>scope=If your DJI Air 3 drone has an ESC error and you can't fix this error by refreshing the<br>drone firmware, it is most likely the ESC board is failing | 未匹配预测FP |

<details>
<summary>展开本段原文与逐条证据</summary>

```text
The ESC (Electronic Speed Controllers) board controls the motors which directly alters
the in-flight drone directional changes depending on the commands.
If your DJI Air 3 drone has an ESC error and you can't fix this error by refreshing the
drone firmware, it is most likely the ESC board is failing, so you need to replace it.
To replace the ESC board, you need to desolder the drone's arm wires.
If you do not have soldering experience and the required tools, I'd recommend sending
your drone to DJI service for ESC board replacement or repair.
```

| 关系编号 | 标准证据 | 实际证据 |
|---|---|---|
| G01 / P04 | The ESC (Electronic Speed Controllers) board controls the motors which directly alters<br>the in-flight drone directional changes depending on the commands.<br>If your DJI Air 3 drone has an ESC error and you can't fix this error by refreshing the<br>drone firmware, it is most likely the ESC board is failing, so you need to replace it.<br>To replace the ESC board, you need to desolder the drone's arm wires. | If your DJI Air 3 drone has an ESC error and you can't fix this error by refreshing the<br>drone firmware, it is most likely the ESC board is failing, so you need to replace it. |
| G02 | The ESC (Electronic Speed Controllers) board controls the motors which directly alters<br>the in-flight drone directional changes depending on the commands.<br>If your DJI Air 3 drone has an ESC error and you can't fix this error by refreshing the<br>drone firmware, it is most likely the ESC board is failing, so you need to replace it. | — |
| G03 / P02 | The ESC (Electronic Speed Controllers) board controls the motors which directly alters<br>the in-flight drone directional changes depending on the commands. | The ESC (Electronic Speed Controllers) board controls the motors which directly alters<br>the in-flight drone directional changes depending on the commands. |
| G04 / P03 | If your DJI Air 3 drone has an ESC error and you can't fix this error by refreshing the<br>drone firmware, it is most likely the ESC board is failing, so you need to replace it. | If your DJI Air 3 drone has an ESC error and you can't fix this error by refreshing the<br>drone firmware, it is most likely the ESC board is failing, so you need to replace it. |
| G05 / P01 | If your DJI Air 3 drone has an ESC error and you can't fix this error by refreshing the<br>drone firmware, it is most likely the ESC board is failing, so you need to replace it. | If your DJI Air 3 drone has an ESC error and you can't fix this error by refreshing the<br>drone firmware, it is most likely the ESC board is failing, so you need to replace it. |
| G06 | If your DJI Air 3 drone has an ESC error and you can't fix this error by refreshing the | — |
| G07 | If your DJI Air 3 drone has an ESC error and you can't fix this error by refreshing the<br>drone firmware, it is most likely the ESC board is failing, so you need to replace it.<br>To replace the ESC board, you need to desolder the drone's arm wires. | — |
| G08 | To replace the ESC board, you need to desolder the drone's arm wires. | — |
| P05 | — | it is most likely the ESC board is failing, so you need to replace it. |

</details>

<a id="eg018"></a>
## EG018 · 英文

[NEW DJI Mini 3 - The BEST Budget Drone EVER! (4K)](https://www.youtube.com/watch?v=RvSjkLzcFbw) · 115.86–161.56秒

| 编号 | golden关系（含类型/断言/限定） | 实际抽取（含类型/断言/限定） | 对照判定 |
|---|---|---|---|
| G01 / P03 | DJI Mini 3 [product] → <code>supports</code> → sport mode [technology]<br>asserted | DJI Mini 3 [product] → <code>supports</code> → sport mode [technology]<br>asserted | 命中（含限定） |
| G02 / P04 | Pro版本 [product] → <code>supports</code> → sport mode [technology]<br>asserted | DJI Mini 3 Pro [product] → <code>supports</code> → sport mode [technology]<br>asserted | 命中（含限定） |
| G03 | DJI Mini 3 [product] → <code>supports</code> → True vertical shooting [technology]<br>asserted | — | 漏匹配FN；至少一个端点未对齐 |
| G04 | Pro版本 [product] → <code>supports</code> → True vertical shooting [technology]<br>asserted | — | 漏匹配FN；至少一个端点未对齐 |
| P01 | 标准实体范围外 | DJI Mini 3 Pro [product] → <code>supports</code> → vertical shooting [technology]<br>asserted<br>modality=capability | 范围外；未计分 |
| P02 | 标准实体范围外 | DJI Mini 3 [product] → <code>supports</code> → vertical shooting [technology]<br>asserted<br>modality=capability | 范围外；未计分 |

<details>
<summary>展开本段原文与逐条证据</summary>

```text
True vertical shooting which is a very welcome addition to both of these drones, sharing content
on social media in the native aspect ratio without having to crop out two thirds of the screen.
The pro version and the mini 3 can now also shoot upwards past level horizon which neither of the
original Mavic mini or Mini 2 could do. This is great for capturing climbing shots and low angles.
So what else do they have in common. They both fly at the same speed at 16
meters per second in sport mode. Thats 24mph. Let’s see what 24mph looks like!
The mini 3
```

| 关系编号 | 标准证据 | 实际证据 |
|---|---|---|
| G01 / P03 | The pro version and the mini 3 can now also shoot upwards past level horizon which neither of the<br>original Mavic mini or Mini 2 could do. This is great for capturing climbing shots and low angles.<br>So what else do they have in common. They both fly at the same speed at 16<br>meters per second in sport mode. Thats 24mph. Let’s see what 24mph looks like! | They both fly at the same speed at 16<br>meters per second in sport mode. |
| G02 / P04 | The pro version and the mini 3 can now also shoot upwards past level horizon which neither of the<br>original Mavic mini or Mini 2 could do. This is great for capturing climbing shots and low angles.<br>So what else do they have in common. They both fly at the same speed at 16<br>meters per second in sport mode. Thats 24mph. Let’s see what 24mph looks like! | They both fly at the same speed at 16<br>meters per second in sport mode. |
| G03 | True vertical shooting which is a very welcome addition to both of these drones, sharing content<br>on social media in the native aspect ratio without having to crop out two thirds of the screen.<br>The pro version and the mini 3 can now also shoot upwards past level horizon which neither of the<br>original Mavic mini or Mini 2 could do. This is great for capturing climbing shots and low angles.<br>So what else do they have in common. They both fly at the same speed at 16<br>meters per second in sport mode. Thats 24mph. Let’s see what 24mph looks like! | — |
| G04 | True vertical shooting which is a very welcome addition to both of these drones, sharing content<br>on social media in the native aspect ratio without having to crop out two thirds of the screen.<br>The pro version and the mini 3 can now also shoot upwards past level horizon which neither of the<br>original Mavic mini or Mini 2 could do. This is great for capturing climbing shots and low angles.<br>So what else do they have in common. They both fly at the same speed at 16<br>meters per second in sport mode. Thats 24mph. Let’s see what 24mph looks like! | — |
| P01 | — | The pro version and the mini 3 can now also shoot upwards past level horizon |
| P02 | — | The pro version and the mini 3 can now also shoot upwards past level horizon |

</details>

<a id="eg019"></a>
## EG019 · 英文

[Avata 360 Single-Lens Mode | Struggling with Head Tracking + Motion 3 | Turn On Caption (CC)](https://www.youtube.com/watch?v=sjEluw4GIqI) · 8.0–61.0秒

| 编号 | golden关系（含类型/断言/限定） | 实际抽取（含类型/断言/限定） | 对照判定 |
|---|---|---|---|
| G01 / P02 | DJI Avata 360 [product] → <code>compatible_with</code> → RC Motion 3 [product]<br>asserted | Avata 360 [product] → <code>compatible_with</code> → RC Motion 3 [product]<br>asserted | 命中（含限定） |
| G02 / P01 | DJI Avata 360 [product] → <code>supports</code> → single-lens mode [technology]<br>asserted | Avata 360 [product] → <code>supports</code> → single-lens mode [technology]<br>asserted | 命中（含限定） |
| G03 / P04 | DJI Avata 360 [product] → <code>supports</code> → spherical mode [technology]<br>asserted | Avata 360 [product] → <code>supports</code> → spherical mode [technology]<br>asserted | 命中（含限定） |
| G04 / P03 | DJI Avata 360 [product] → <code>supports</code> → Head Tracking [technology]<br>uncertain<br>modality=hypothetical<br>scope=I'm nodding and turning my head to check how Head Tracking moves the camera and drone.<br>mode=single-lens mode | Avata 360 [product] → <code>supports</code> → Head Tracking [technology]<br>asserted | 断言差异（FN+FP） |

<details>
<summary>展开本段原文与逐条证据</summary>

```text
Today's experiment: Avata 360 in single-lens mode, with RC Motion 3 and Head Tracking.
The Avata 360 takes off in spherical mode. I switch to single-lens mode after takeoff.
I'm nodding and turning my head to check how Head Tracking moves the camera and drone.
Checking that Head Tracking is enabled.
```

| 关系编号 | 标准证据 | 实际证据 |
|---|---|---|
| G01 / P02 | Today's experiment: Avata 360 in single-lens mode, with RC Motion 3 and Head Tracking. | Avata 360 in single-lens mode, with RC Motion 3 and Head Tracking |
| G02 / P01 | The Avata 360 takes off in spherical mode. I switch to single-lens mode after takeoff. | Avata 360 in single-lens mode |
| G03 / P04 | The Avata 360 takes off in spherical mode. I switch to single-lens mode after takeoff. | The Avata 360 takes off in spherical mode. |
| G04 / P03 | Today's experiment: Avata 360 in single-lens mode, with RC Motion 3 and Head Tracking.<br>The Avata 360 takes off in spherical mode. I switch to single-lens mode after takeoff.<br>I'm nodding and turning my head to check how Head Tracking moves the camera and drone.<br>Checking that Head Tracking is enabled. | I'm nodding and turning my head to check how Head Tracking moves the camera and drone. |

</details>

<a id="eg020"></a>
## EG020 · 英文

[DJI Mini 5 Pro engel algılama testi: teller, yapraklar ve yağmur! 🚁](https://www.youtube.com/watch?v=ZqTrQBWrol0) · 0.0–34.5秒

| 编号 | golden关系（含类型/断言/限定） | 实际抽取（含类型/断言/限定） | 对照判定 |
|---|---|---|---|
| G01 / P01 | DJI Mini 5 Pro [product] → <code>supports</code> → OBSTACLE AVOIDANCE [technology]<br>asserted | DJI Mini 5 Pro [product] → <code>supports</code> → OBSTACLE AVOIDANCE [technology]<br>asserted | 命中（含限定） |

<details>
<summary>展开本段原文与逐条证据</summary>

```text
DJI Mini 5 Pro OBSTACLE AVOIDANCE TEST
TEST 1 Flying between wires
Side by side with the cable-car wire
TEST 2 It even detects leaves
TEST 3 Flying in heavy rain
Which one was the hardest? Tell me in the comments
```

| 关系编号 | 标准证据 | 实际证据 |
|---|---|---|
| G01 / P01 | DJI Mini 5 Pro OBSTACLE AVOIDANCE TEST<br>TEST 1 Flying between wires<br>Side by side with the cable-car wire<br>TEST 2 It even detects leaves | DJI Mini 5 Pro OBSTACLE AVOIDANCE TEST |

</details>
