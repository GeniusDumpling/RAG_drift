# 二维选中焦点框修复与部署

二维 SVG 节点与关系移除浏览器默认的矩形焦点轮廓。保留选中高亮和键盘操作，键盘焦点分别用节点圆周和连线颜色提示。

已部署 `rag-graph-focus:20261112-v1`，基于当前正式镜像 `rag-answer-speed:20261112-v2` 仅替换前端资源，保留回答速度优化、二维名称显隐与零逐帧 React 提交、原版三维拖拽和 iFixit 图片功能。

临时及正式页面均验证：节点和边焦点 outline 为 none，选中详情和键盘激活正常，无页面脚本异常。正式图谱为 300 节点、359 关系，概览名称数为 0，放大恢复为 659；布局和连续缩放期间 React 提交为 0。HTTP 健康检查通过，环境变量、挂载、图片资源及其他容器保持一致。

发布记录、配置备份及截图位于 `data/releases/graph-focus-20261112-v1/`。配置含私有信息，目录权限为 700。上一应用容器停用保留为 `intelligence-rag-app-before-graph-focus-20261112`。

回退命令：

```bash
bash data/releases/graph-focus-20261112-v1/rollback.sh
```
