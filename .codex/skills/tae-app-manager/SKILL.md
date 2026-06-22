---
name: tae-app-manager
description: 通过 TAE (Truesight App Engine) API 管理 K8s 应用的全生命周期（创建、更新、查看、重启、排查）、数据库（创建、查看、重置密码）以及 Git 仓库（申请、查看、Token 轮转、解除分支保护）。推送代码前自动根据项目类型生成 .gitignore。支持 API Key 认证，覆盖三大模块。
version: 0.1.1
x-ts-skill:
  schemaVersion: 1
  scope: project
  runtime: codex
  source: .ts-agent/skills/tae-app-manager
  sourceDigest: sha256:308e66c92fb3e0fb7819fcc1105f51a96aedbacc9f62e6ab828ea893d84238b3
  invoke: ts-skill use tae-app-manager
  canonicalId: "@global/tae-app-manager"
  managedBy: ts-skill-platform
---

# tae-app-manager

通过 TAE (Truesight App Engine) API 管理 K8s 应用的全生命周期（创建、更新、查看、重启、排查）、数据库（创建、查看、重置密码）以及 Git 仓库（申请、查看、Token 轮转、解除分支保护）。推送代码前自动根据项目类型生成 .gitignore。支持 API Key 认证，覆盖三大模块。

## How to invoke

**This is a bridge file — not the real skill content.** The routing
summary above is for deciding whether this skill fits your task. To
actually execute the skill, route through the ts-skill-platform manager:

```bash
ts-skill use tae-app-manager
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
