# The Bridge — 官网

本地部署的 Salesforce × 企业微信 自动化工具的产品官网与轻 API 服务。

- `index.html` 页面结构
- `styles.css` 样式与动效
- `script.js` 交互；顶部可替换下载链接与教程链接
- `server.py` 静态资源服务 + 统一登录鉴权 API

## TAE /data 静态资源

镜像只包含页面代码和 Nginx 配置，大体积资源不再打进镜像。

TAE 扩展存储挂载到 `/data` 后，按下面目录放文件：

```text
/data/thebridge/
  resources/
    The-Bridge.dmg
  updates/
    manifest.json
    code-x.y.z.zip
```

页面下载链接访问 `/resources/The-Bridge.dmg`，Nginx 会映射到
`/data/thebridge/resources/The-Bridge.dmg`。

应用热更新可以访问 `/updates/manifest.json` 和 `/updates/code-x.y.z.zip`，
对应 `/data/thebridge/updates/` 下的文件。

后续只替换 DMG、更新包或 manifest 时，不需要重新打包镜像；只有页面代码、
样式、脚本或 API 服务变化时才需要重新发布镜像。

## API 与统一登录

`server.py` 提供受保护 API。所有用户数据接口都必须带：

```http
Authorization: Bearer <auth-gateway-jwt>
```

当前已实现：

```text
GET /api/health  # 健康检查，不需要登录
GET /api/me      # 校验 JWT，返回当前用户与服务端 owner_key
POST /api/scrape-batches      # 上传一批抓取客户数据
GET  /api/scrape-batches      # 列出当前用户自己的抓取批次
GET  /api/scrape-batches/:id  # 查看当前用户自己的某个抓取批次
```

服务端会调用 `https://auth-gateway.truesightai.com/userinfo` 校验 token，
并用 `org_id + ":" + union_id` 生成 `owner_key`。后续抓取数据、发送任务、
agent 分析结果都必须按这个 `owner_key` 写入和查询。

抓取批次探索期存储在 SQLite：

```text
/data/thebridge/cloud/thebridge.db
```

后端会忽略客户端传来的 `owner_key` / `user_id` 等归属字段，只使用服务端通过
`/userinfo` 推导出的 `owner_key`。

当前线上 TAE 应用：

```text
应用名: thebridge
域名: https://thebridge.tae.vera-mesh.com
镜像: registry.pixcakeai.com/tae/the-bridge-site:data-static-202606111538
端口: 80
扩展存储: 10Gi -> /data
```

项目已 vendored `tae-image-release` 和 `tae-app-manager` skill，见
`.codex/skills/` 与 `.ts-agent/skills/`。新会话做 TAE 发布/部署时应先使用这些
skill，不要回到旧的“把 DMG 打进镜像”路径。
