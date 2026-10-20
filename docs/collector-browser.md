# 采集镜像的浏览器依赖

供应商采集的 `supplier` extra 包含 Playwright；项目主 Dockerfile 在构建时执行
`python -m playwright install --with-deps chromium`，安装配套浏览器及系统库。
浏览器固定保存到 `/ms-playwright`，运行时无需重新下载。

已有应用镜像可增量构建采集专用镜像，在项目根目录执行：

```bash
docker build -f docker/collector.Dockerfile \
  --build-arg BASE_IMAGE=<现有应用镜像标签> \
  -t intelligence-rag-collector:browser .
```

在 `.env` 设置 `COLLECTOR_IMAGE=intelligence-rag-collector:browser` 后，Compose 的
`jobs` 服务使用此镜像；未设置时仍使用 `INTELLIGENCE_RAG_IMAGE`。
`bash deploy/run-job.sh supplier-pipeline` 等入口在下一次启动任务时生效。
宿主机 systemd 若直接调用 `.venv`，仍使用宿主机依赖，需要改用容器入口才会使用该镜像。

供应商配置 `search.fetch_with_browser: true` 控制正文抓取的浏览器兜底。
安装依赖不会自动修复正文质量判定、验证码、登录限制或代理配置。
