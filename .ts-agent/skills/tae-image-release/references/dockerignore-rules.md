# .dockerignore 规范

## 为什么必须有 .dockerignore

`docker build` 会把整个"构建上下文"（默认是 `.`）发送给 Docker daemon。哪怕 Dockerfile 里只有 `COPY package.json ./`，只要上下文里有 `node_modules/`、`.git/`、`dist/` 等大目录，它们都会被：

1. 打进临时 tarball 传给 daemon（本地构建）或 scp 到远端 ECS（远端构建）；
2. 计入构建缓存的 hash，导致任何无关文件变更都会让缓存失效、镜像重建；
3. 如果 Dockerfile 里出现 `COPY . .`，这些文件会**直接被塞进最终镜像层**，让镜像体积膨胀几百 MB 甚至几个 GB。

`.dockerignore` 是唯一正确的解决方式——`.gitignore` 对 Docker 不生效。

## 总则

- 每个需要发布的仓库根目录都必须有 `.dockerignore`。
- `.dockerignore` 必须至少覆盖 VCS 目录、依赖目录、构建产物、缓存、日志、IDE/系统元数据、环境变量/密钥。
- 和 Dockerfile 里的 `COPY` 策略配合：如果用了 `COPY . .`，`.dockerignore` 必须格外严格；如果 Dockerfile 只 `COPY` 白名单文件，`.dockerignore` 仍然建议写全，保护构建缓存和远端上传体积。
- 不要把 `Dockerfile` 或 `.dockerignore` 本身加到 `.dockerignore` 里——Docker 本来就会特殊处理它们。

## 通用基础模板（任何语言都应包含）

```dockerignore
# 版本控制
.git
.gitignore
.gitattributes
.github
.gitlab-ci.yml

# IDE / 编辑器 / 系统
.idea
.vscode
.cursor
.DS_Store
Thumbs.db
*.swp

# 日志
*.log
logs

# 环境变量 / 密钥（保留 .env.example 这类示例文件）
.env
.env.*
!.env.example
*.pem
*.key

# 构建上下文自身
Dockerfile*
.dockerignore

# 测试与文档（按需启用，被 COPY 时才生效）
# tests
# docs
# README.md
```

> `Dockerfile*` 和 `.dockerignore` 加不加都行，docker 会自动处理；列出来只是告诉 reviewer 你故意没 COPY 它们。

## Node / 前端场景

```dockerignore
# 依赖目录 —— 永远不要打进镜像
node_modules
bower_components
jspm_packages
.pnpm-store

# 包管理器日志 / 缓存
npm-debug.log*
yarn-debug.log*
yarn-error.log*
.pnpm-debug.log*
.npm
.yarn/cache
.yarn/unplugged
.yarn/install-state.gz

# 构建产物 / 框架缓存
dist
build
out
coverage
.next
.nuxt
.svelte-kit
.turbo
.cache
.parcel-cache
.vite
.storybook-static
```

> 如果是 monorepo（pnpm workspaces、turbo），要把每个包的 `node_modules`、`dist`、`.turbo` 都排除，`node_modules` 这种裸名称会匹配所有层级的同名目录，通常一行就够。

## Python 场景

```dockerignore
__pycache__
*.py[cod]
*$py.class
*.so

# 虚拟环境
.venv
venv
env
ENV

# 测试 / 类型 / lint 缓存
.pytest_cache
.mypy_cache
.ruff_cache
.tox
.coverage
.coverage.*
htmlcov
.hypothesis

# 构建 / 打包产物
build
dist
*.egg-info
*.egg
pip-wheel-metadata
```

## Go 场景

```dockerignore
# 二进制构建产物
bin
tmp
*.exe
*.test
*.out

# vendor 按需保留 —— 如果镜像构建阶段需要 offline modules，就不要排除
# vendor
```

> 多数 Go 项目不需要把 `vendor/` 加进 `.dockerignore`（如果项目启用了 vendor 模式，构建时需要它）。仅当项目从不使用 vendor 时再排除。

## Rust 场景

```dockerignore
target
Cargo.lock  # 仅 library 项目需要排除；二进制项目不要排除
```

## 多阶段构建的额外建议

如果 Dockerfile 使用多阶段（常见于 Node/Go），第一阶段依赖完整源码，最终阶段只 COPY 产物。这时仍然要保证：

- `.dockerignore` 排除依赖目录、构建产物（`node_modules`、`dist`、`build`）——避免本地已有的 `dist/` 被打进 builder 阶段，覆盖掉容器内新鲜构建出来的内容。
- 不要依赖"反正最后阶段不 COPY 它"。构建上下文体积膨胀本身就会拖慢每一次构建和推送。

## 审查清单

- 仓库根目录下是否存在 `.dockerignore`
- 是否至少排除了 `.git`、`node_modules`（若有）、`__pycache__`（若有）、`.venv`/`venv`（若有）、`dist`/`build`/`out`/`.next` 等典型构建产物
- 是否排除 `.env`、`*.pem`、`*.key` 等密钥文件（避免意外打进镜像）
- monorepo 是否在根目录就覆盖了所有子包的 `node_modules`、`dist`
- Dockerfile 里用了 `COPY . .` 时，`.dockerignore` 是否足够严格
- 是否误把 `Dockerfile` 或需要的源码目录（`src/`、`internal/`）加进了 `.dockerignore`
