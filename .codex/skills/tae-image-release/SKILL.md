---
name: tae-image-release
description: 面向 IT 小白的 TAE 镜像零配置发布与部署技能。本地有 Docker 直接构建，无 Docker 自动 SSH 到远端 ECS 构建，无需安装任何软件、无需 sudo。自动完成 registry 登录，Dockerfile 基础镜像强制使用 registry.pixcakeai.com/pub/ 内部源。镜像推送成功后自动调用 tae-app-manager skill 完成应用创建或更新，并执行健康检查。用户只需一条命令即可完成从镜像构建到应用部署的全流程。
version: 0.2.2
x-ts-skill:
  schemaVersion: 1
  scope: project
  runtime: codex
  source: .ts-agent/skills/tae-image-release
  sourceDigest: sha256:9c24f8d35f231e2b381f3db1db0075bf371aa1881c62f5e26b3602ce6647ad3e
  invoke: ts-skill use tae-image-release
  canonicalId: "@global/tae-image-release"
  managedBy: ts-skill-platform
---

# tae-image-release

面向 IT 小白的 TAE 镜像零配置发布与部署技能。本地有 Docker 直接构建，无 Docker 自动 SSH 到远端 ECS 构建，无需安装任何软件、无需 sudo。自动完成 registry 登录，Dockerfile 基础镜像强制使用 registry.pixcakeai.com/pub/ 内部源。镜像推送成功后自动调用 tae-app-manager skill 完成应用创建或更新，并执行健康检查。用户只需一条命令即可完成从镜像构建到应用部署的全流程。

## How to invoke

**This is a bridge file — not the real skill content.** The routing
summary above is for deciding whether this skill fits your task. To
actually execute the skill, route through the ts-skill-platform manager:

```bash
ts-skill use tae-image-release
```

If the `ts-skill` CLI itself is missing, install the manager first:
```bash
curl -fsSL https://skill.tae.vera-mesh.com/skills/ts-skill-platform/bootstrap.sh | bash -s -- --base-url https://skill.tae.vera-mesh.com --scope global --yes
```

## Behavior contract

The packet returned by `ts-skill use` is the only valid execution context
for this skill. It carries version gates, execution reporting, and
`nextActions`. If a packet (or `ts-skill use` itself) returns
`update_required` / `blocked` / `dirty` / `missing` / `ambiguous`, follow
the printed `nextAction` first — do not bypass it to read the source
SKILL.md directly.
