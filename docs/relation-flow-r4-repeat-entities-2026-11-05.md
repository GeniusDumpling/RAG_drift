# 实体抽取基线评测

抽取版本：`video-graph-v2:deepseek-v4.1-flash-economy:3098b15645af-none-none`。
金标准：`entity-gold-v3`；指纹：`a48d12b661d90bce9636db0776985b35c40b97b56eea576d46094a53066bca0e`。
执行成功 20/20，错误 0，待运行 0。

基于人工确认的实体答案，测试工作区抽取代码。输入仅标题和当前完整字幕，不传标准答案、models或notes，不写生产图谱。
采用可复现的名称/原文叫法一对一匹配，容忍大小写、排版符号和空白差异；不进行自由翻译、模糊子串或模型自评。类型正确指标还要求实体类型相同。
同一预测不能匹配多个答案；同一规范身份的重复提及只计一次。已明确冲突的型号所有者不能匹配。匹配不唯一的项另行标出，不代表所有者身份已核验。
同义词、边界或命名粒度差异可能被判为漏抽/多抽，需结合下方清单复核；本报告不评属性、关系、完整提及边界或跨语言身份归一。

| 范围 | 指标 | TP | FP | FN | Precision | Recall | F1 |
|---|---|---:|---:|---:|---:|---:|---:|
| 全部 | 实体识别 | 96 | 17 | 5 | 85.0% | 95.0% | 89.7% |
| 全部 | 实体及类型正确 | 95 | 18 | 6 | 84.1% | 94.1% | 88.8% |
| zh | 实体识别 | 56 | 9 | 2 | 86.2% | 96.6% | 91.1% |
| zh | 实体及类型正确 | 55 | 10 | 3 | 84.6% | 94.8% | 89.4% |
| en | 实体识别 | 40 | 8 | 3 | 83.3% | 93.0% | 87.9% |
| en | 实体及类型正确 | 40 | 8 | 3 | 83.3% | 93.0% | 87.9% |

## EG001 · zh

未匹配标准实体（按实体及类型匹配）：

- DJI Avata系列 [product_family]；原文叫法：Avatar系列
- 视觉避障 [technology]；原文叫法：视觉避障系统

未匹配预测实体（按实体及类型匹配）：

- DJI Avata 360 桨叶 [component]；原文叫法：桨叶
- 视觉避障系统 [component]；原文叫法：视觉避障系统
- 360全景 [technology]；原文叫法：360全景

类型差异：视觉避障，标准 technology，预测 component。

## EG002 · zh

未匹配标准实体（按实体及类型匹配）：

- 无。

未匹配预测实体（按实体及类型匹配）：

- 无。


## EG003 · zh

未匹配标准实体（按实体及类型匹配）：

- 无。

未匹配预测实体（按实体及类型匹配）：

- 无。


## EG004 · zh

未匹配标准实体（按实体及类型匹配）：

- 无。

未匹配预测实体（按实体及类型匹配）：

- Insta360 摺疊無人機 [product]；原文叫法：摺疊無人機
- 摺疊機臂 [component]；原文叫法：摺疊機臂
- 環形螺旋槳保護罩 [component]；原文叫法：環形螺旋槳保護罩


## EG005 · zh

未匹配标准实体（按实体及类型匹配）：

- Neo 3感光元件 [component]；原文叫法：感光元件

未匹配预测实体（按实体及类型匹配）：

- 无。


## EG006 · zh

未匹配标准实体（按实体及类型匹配）：

- 无。

未匹配预测实体（按实体及类型匹配）：

- 全向避障 [technology]；原文叫法：全向避障


## EG007 · zh

未匹配标准实体（按实体及类型匹配）：

- 无。

未匹配预测实体（按实体及类型匹配）：

- 无。


## EG008 · zh

未匹配标准实体（按实体及类型匹配）：

- 无。

未匹配预测实体（按实体及类型匹配）：

- 4K 100帧的慢动作 [technology]；原文叫法：4K 100帧的慢动作


## EG009 · zh

未匹配标准实体（按实体及类型匹配）：

- 无。

未匹配预测实体（按实体及类型匹配）：

- 无。


## EG010 · zh

未匹配标准实体（按实体及类型匹配）：

- 无。

未匹配预测实体（按实体及类型匹配）：

- DJI [organization]；原文叫法：DJI
- RC 2 [product]；原文叫法：RC 2


## EG011 · en

未匹配标准实体（按实体及类型匹配）：

- 无。

未匹配预测实体（按实体及类型匹配）：

- 无。


## EG012 · en

未匹配标准实体（按实体及类型匹配）：

- 无。

未匹配预测实体（按实体及类型匹配）：

- 无。


## EG013 · en

未匹配标准实体（按实体及类型匹配）：

- remote controller [product]；原文叫法：remote controller

未匹配预测实体（按实体及类型匹配）：

- aircraft arms [component]；原文叫法：aircraft arms


## EG014 · en

未匹配标准实体（按实体及类型匹配）：

- 无。

未匹配预测实体（按实体及类型匹配）：

- 无。


## EG015 · en

未匹配标准实体（按实体及类型匹配）：

- 无。

未匹配预测实体（按实体及类型匹配）：

- charging
cable USB "C" [component]；原文叫法：charging ⏎ cable USB "C"
- spare battery [component]；原文叫法：spare battery
- USB cable for the iPhone [component]；原文叫法：USB cable for the iPhone


## EG016 · en

未匹配标准实体（按实体及类型匹配）：

- 无。

未匹配预测实体（按实体及类型匹配）：

- 无。


## EG017 · en

未匹配标准实体（按实体及类型匹配）：

- DJI [organization]；原文叫法：DJI service

未匹配预测实体（按实体及类型匹配）：

- 无。


## EG018 · en

未匹配标准实体（按实体及类型匹配）：

- 无。

未匹配预测实体（按实体及类型匹配）：

- 无。


## EG019 · en

未匹配标准实体（按实体及类型匹配）：

- 无。

未匹配预测实体（按实体及类型匹配）：

- 无。


## EG020 · en

未匹配标准实体（按实体及类型匹配）：

- OBSTACLE AVOIDANCE [technology]；原文叫法：OBSTACLE AVOIDANCE

未匹配预测实体（按实体及类型匹配）：

- OBSTACLE AVOIDANCE TEST [technology]；原文叫法：OBSTACLE AVOIDANCE TEST
- TEST 1 Flying between wires [method]；原文叫法：TEST 1 Flying between wires
- TEST 2 It even detects leaves [method]；原文叫法：TEST 2 It even detects leaves
- TEST 3 Flying in heavy rain [method]；原文叫法：TEST 3 Flying in heavy rain

