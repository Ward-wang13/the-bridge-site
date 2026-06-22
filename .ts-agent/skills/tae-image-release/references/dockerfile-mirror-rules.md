# Dockerfile 国内源规范

## 总则

- **Nginx / Node / Python / Go 基础镜像必须使用内部 registry**（见下方"内部基础镜像"章节），禁止直接 `FROM` Docker Hub。其他语言或工具链（如 Ruby、Java、Rust 等）内部 registry 未收录的，可以使用 Docker Hub 官方镜像。
- 在 Dockerfile 中，只要会安装语言依赖或系统包，就必须先配置国内源，再执行安装命令。
- 语言依赖源和系统包源是两层约束；若镜像里两者都存在，就两者都要配置。
- 优先把镜像源配置写在首次安装动作之前，避免出现"第一层已经走外网拉包"的情况。

## 内部基础镜像（最高优先级）

编写 Dockerfile 时，Nginx / Node / Python / Go 的 `FROM` 必须使用 `registry.pixcakeai.com/pub/` 下的镜像，不要写 `node:22-alpine`、`python:3.12-slim` 等 Docker Hub 地址。

**版本匹配规则**：如果用户项目要求的版本（如 `.nvmrc`、`engines`、`go.mod`、`runtime.txt` 等）与下方列表中的版本不完全一致，**仍然选择下方列表中最接近的版本**，不要回退到 Docker Hub。例如：
- 项目要求 Node 22.12 → 选择 `registry.pixcakeai.com/pub/node:22.18-alpine`（同主版本，向上兼容）
- 项目要求 Python 3.11.4 → 选择 `registry.pixcakeai.com/pub/python:3.11-slim`（同 minor 版本）
- 项目要求 Go 1.23.2 → 选择 `registry.pixcakeai.com/pub/golang:1.23`（同 minor 版本）

### Nginx

| 镜像地址 |
|---------|
| `registry.pixcakeai.com/pub/nginx:1.27-alpine` |
| `registry.pixcakeai.com/pub/nginx:1.27` |

### Node

| 镜像地址 |
|---------|
| `registry.pixcakeai.com/pub/node:22.18-alpine` |
| `registry.pixcakeai.com/pub/node:22.18` |
| `registry.pixcakeai.com/pub/node:22.18-slim` |
| `registry.pixcakeai.com/pub/node:24.14-alpine` |
| `registry.pixcakeai.com/pub/node:24.14` |
| `registry.pixcakeai.com/pub/node:24.14-slim` |
| `registry.pixcakeai.com/pub/node:25.9-alpine` |
| `registry.pixcakeai.com/pub/node:25.9` |
| `registry.pixcakeai.com/pub/node:25.9-slim` |

### Python

| 镜像地址 |
|---------|
| `registry.pixcakeai.com/pub/python:3.10` |
| `registry.pixcakeai.com/pub/python:3.10-slim` |
| `registry.pixcakeai.com/pub/python:3.11` |
| `registry.pixcakeai.com/pub/python:3.11-slim` |
| `registry.pixcakeai.com/pub/python:3.12` |
| `registry.pixcakeai.com/pub/python:3.12-slim` |
| `registry.pixcakeai.com/pub/python:3.13` |
| `registry.pixcakeai.com/pub/python:3.13-slim` |
| `registry.pixcakeai.com/pub/python:3.14` |
| `registry.pixcakeai.com/pub/python:3.14-slim` |

### Go

| 镜像地址 |
|---------|
| `registry.pixcakeai.com/pub/golang:1.20` / `1.20-alpine` |
| `registry.pixcakeai.com/pub/golang:1.21` / `1.21-alpine` |
| `registry.pixcakeai.com/pub/golang:1.22` / `1.22-alpine` |
| `registry.pixcakeai.com/pub/golang:1.23` / `1.23-alpine` |
| `registry.pixcakeai.com/pub/golang:1.24` / `1.24-alpine` |
| `registry.pixcakeai.com/pub/golang:1.25` / `1.25-alpine` |
| `registry.pixcakeai.com/pub/golang:1.26` / `1.26-alpine` |

### 示例

```dockerfile
# 正确 — 使用内部镜像
FROM registry.pixcakeai.com/pub/node:22.18-alpine AS builder

# 正确 — 项目要求 Node 22.12，列表里没有，选最接近的 22.18
FROM registry.pixcakeai.com/pub/node:22.18-alpine AS builder

# 错误 — Nginx/Node/Python/Go 禁止直接用 Docker Hub
FROM node:22-alpine AS builder

# 正确 — 内部 registry 未收录的语言，可以用 Docker Hub
FROM rust:1.78-slim AS builder
```

## 语言依赖源

### Node

```dockerfile
RUN npm config set registry https://registry.npmmirror.com && \
    npm ci
```

若使用 `pnpm` / `yarn`，也要显式配置国内源，例如：

```dockerfile
RUN pnpm config set registry https://registry.npmmirror.com
RUN yarn config set npmRegistryServer https://registry.npmmirror.com
```

### Python

```dockerfile
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/ && \
    pip config set global.trusted-host mirrors.aliyun.com && \
    pip install -r requirements.txt
```

### Go

```dockerfile
ENV GOPROXY=https://goproxy.cn,direct
RUN go mod download
```

## 系统包源

### Alpine

```dockerfile
RUN sed -i 's/dl-cdn.alpinelinux.org/mirrors.aliyun.com/g' /etc/apk/repositories && \
    apk update && \
    apk add --no-cache curl
```

### Debian

常见 Debian 镜像可能使用 `/etc/apt/sources.list` 或 `/etc/apt/sources.list.d/debian.sources`，按实际文件格式修改：

```dockerfile
RUN sed -i 's@deb.debian.org@mirrors.aliyun.com@g; s@security.debian.org@mirrors.aliyun.com@g' /etc/apt/sources.list.d/debian.sources && \
    apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*
```

### Ubuntu

```dockerfile
RUN sed -i 's@archive.ubuntu.com@mirrors.aliyun.com@g; s@security.ubuntu.com@mirrors.aliyun.com@g' /etc/apt/sources.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*
```

## 审查清单

- Nginx / Node / Python / Go 的 `FROM` 是否使用了 `registry.pixcakeai.com/pub/` 内部镜像（而非 Docker Hub）
- 如果项目版本与内部镜像列表不完全匹配，是否选择了最接近的内部镜像版本（而非回退到 Docker Hub）
- `npm` / `pnpm` / `yarn` 前是否已配置国内 registry
- `pip install` 前是否已配置国内 PyPI 源
- `go mod download` 前是否已配置 `GOPROXY`
- `apt-get install` / `apk add` 前是否已切换到国内系统源
- 是否把源配置写在第一次安装动作之前
