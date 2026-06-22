---
name: tae-image-release
version: 0.2.2
description: 面向 IT 小白的 TAE 镜像零配置发布与部署技能。本地有 Docker 直接构建，无 Docker 自动 SSH 到远端 ECS 构建，无需安装任何软件、无需 sudo。自动完成 registry 登录，Dockerfile 基础镜像强制使用 registry.pixcakeai.com/pub/ 内部源。镜像推送成功后自动调用 tae-app-manager skill 完成应用创建或更新，并执行健康检查。用户只需一条命令即可完成从镜像构建到应用部署的全流程。
---

# TAE 镜像构建与发布

这个 skill 负责镜像构建、推送以及自动部署的完整流程。镜像成功推送到 `registry.pixcakeai.com/tae/` 后，会自动调用 [`tae-app-manager`](https://skill.tae.vera-mesh.com/skills/tae-app-manager/SKILL.md) skill 完成应用的创建或更新，并执行健康检查，实现从构建到部署的一站式交付。

最终交付时，`镜像地址` 只能引用发布脚本最后输出的 canonical registry 地址，不允许复述本地 build/tag 名称、仓库目录推断值，或任何不带 `registry.pixcakeai.com/tae/` 前缀的中间结果。

发布脚本必须从技能平台下载（`curl -fsSL "https://skill.tae.vera-mesh.com/skills/tae-image-release/scripts/publish-image.sh" -o scripts/publish-image.sh`），**严禁自行编写或凭记忆生成**。编写或评审 Dockerfile 时，先读取 [references/dockerfile-mirror-rules.md](references/dockerfile-mirror-rules.md) 并按其中规则落地，不要遗漏语言包管理器或系统包管理器的国内源配置。编写或评审 `.dockerignore` 时，先读取 [references/dockerignore-rules.md](references/dockerignore-rules.md)，确保 `node_modules`、`.git`、构建产物、密钥等不会被打进构建上下文。

## When to Use

- 用户要求把当前仓库构建成 `linux/amd64` Docker 镜像并推送到 `registry.pixcakeai.com/tae/`
- 用户要求把 TAE 的 Docker 构建流程沉淀成可复用脚本或发布步骤
- 用户要求新增或修改 Dockerfile，并明确要求 `node`、`python`、`go` 依赖源以及 `debian`、`ubuntu`、`alpine` 系统源都走国内镜像
- 用户抱怨镜像体积过大、`node_modules` 被打进镜像、`.git` 进了镜像等 `.dockerignore` 问题
- 用户已经给出镜像名、tag 或完整镜像地址，希望直接完成构建发布
- 用户希望在镜像发布后，顺手拿到 TAE 部署页可直接填写的字段内容
- 用户希望构建镜像后直接部署到 TAE 平台，一步到位
- 用户希望更新已部署应用的镜像版本

## When NOT to Use

- 用户只是做本地 `docker build` 验证，不需要推送到 `registry.pixcakeai.com/tae/`
- 用户要发布到其他镜像仓库或其他组织前缀，而不是 `registry.pixcakeai.com/tae/`
- 用户的任务与 Docker 镜像构建无关，只是普通代码修改、K8s 部署或运行时排障
- 当前环境无法访问 Docker daemon，且用户也不希望调整环境或补充权限

## 构建环境（重要）

脚本会自动检测本地是否有可用的 Docker。**用户无需提前安装任何东西**。

### 本地有 Docker

直接使用本机 `docker` 和 `docker buildx` 完成构建和推送，无额外步骤。

### 本地无 Docker → 自动 SSH 到远端 ECS 构建

如果 `docker info` 不可用，脚本会自动通过 SSH 连接到远端构建服务器 (`root@172.19.71.204`) 执行构建：

1. 自动下载 SSH 私钥（从 `https://package-inner.truesightai.com/aliyun-acr/tae-build-key`）
2. 通过 `ssh -i` 密钥认证直连远端 ECS（无需密码、无需 `sshpass`、无需 `brew`、无需 `sudo`）
3. 将构建上下文打包为本地 tarball，通过 `scp` 上传到远端
4. 在远端执行 `docker buildx build --push`
5. 无论构建成功或失败，远端临时文件都会自动清理

**严格禁止**以下做法：
- 禁止要求用户安装 Homebrew、OrbStack、Docker Desktop 或任何本地 Docker 运行时
- 禁止要求用户输入 sudo 密码
- 禁止手动拼 SSH/SCP 命令；统一由 `scripts/publish-image.sh` 脚本完成

## Prerequisites

- 当前工作目录中存在目标 `Dockerfile`
- **本地有 Docker 时**：直接使用本机 `docker` 和 `docker buildx` 完成构建和推送
- **本地无 Docker 时**：脚本自动 SSH 到远端 ECS 构建，全程无需用户手动操作、无需安装任何软件
- 目标镜像名优先复用当前对话里已经确认过的名称；若用户只给 tag，可以先从仓库目录名或 `package.json` 名称推断候选镜像名，再向用户回显
- 用户未提供镜像 tag 时，脚本按当前时间自动生成 `YYYYMMDDHHMMSS`；用户显式提供 tag 或完整 image-ref 时，不强制时间戳，仅要求符合 Docker tag 格式
- 推送目标固定为 `registry.pixcakeai.com/tae/`
- 登录账号固定为 `acr-tae@1055654169201791`
- registry 密码不要写死，也不要依赖技能环境变量；需要登录时，到 `https://package-inner.truesightai.com/aliyun-acr/password.txt` 获取最新密码，再执行 `docker login`
- 如需调整 Dockerfile，先读取 [references/dockerfile-mirror-rules.md](references/dockerfile-mirror-rules.md)
- 构建上下文根目录**必须**存在 `.dockerignore`；如需新增或修改，先读取 [references/dockerignore-rules.md](references/dockerignore-rules.md)。缺失时发布脚本会自动生成一份通用兜底模板，并提示用户按项目实际情况补充

## Execution Steps

1. 检查当前仓库的 `Dockerfile`、脚本和文档是否已经满足国内源规范；若不满足，先修正 Dockerfile，再继续发布流程。同时检查仓库根目录（或指定的 `--context` 目录）下是否存在 `.dockerignore`：
   - **存在**：对照 [references/dockerignore-rules.md](references/dockerignore-rules.md) 的审查清单快速过一遍，至少确认 `node_modules`、`.git`、`dist`/`build`/`.next` 等构建产物、`.env`/`*.pem` 等密钥都被排除；有遗漏时按规范补全。
   - **不存在**：先按 [references/dockerignore-rules.md](references/dockerignore-rules.md) 的"通用基础模板"+ 对应语言（Node/Python/Go 等）模板拼出 `.dockerignore` 写入仓库；如果未手动处理，发布脚本会在执行时自动生成一份兜底版本（但仍建议 reviewer 在合适时机把它提交到仓库）。
2. **判断项目的存储与数据库需求**。按以下优先级逐级判断：

   **A. 无存储需求**：项目不写文件、不用数据库、纯无状态服务 → `扩展存储：不开启`，无需数据库。

   **B. 简单数据库 / 文件存储需求（推荐 SQLite + 扩展存储）**：如果项目满足以下任一条件，建议使用 SQLite 并开启扩展存储，数据存放在 `/data` 目录：
   - 代码中使用了 SQLite、better-sqlite3、sql.js、Prisma（SQLite provider）、Sequelize（SQLite dialect）、TypeORM（SQLite）等轻量数据库
   - 只需要单实例读写、不需要多副本并发访问
   - 用户明确说"简单存一下数据"、"不需要 PostgreSQL"
   - 项目有上传文件、导出文件、日志落盘、缓存文件等本地写盘行为
   - Dockerfile 中有 `VOLUME` 指令，或代码中引用了 `/data`、`/uploads`、`/storage`、`/var/lib` 等目录
   - 判定为此类时：`扩展存储` 建议 `10Gi`，并在交付信息中提示用户确保应用将 SQLite 数据库文件存放在 `/data` 目录下（如 `/data/app.db`）

   **C. 复杂数据库需求（需要独立 PostgreSQL 数据库服务）**：如果项目满足以下任一条件，需要在部署后调用 `tae-app-manager` skill 创建独立 PostgreSQL 数据库：
   - 代码中使用了 PostgreSQL 客户端/ORM（如 pg、knex、Prisma PostgreSQL provider、TypeORM PostgreSQL、psycopg、asyncpg、sqlalchemy PostgreSQL、gorm PostgreSQL）
   - 代码中使用了 MySQL 客户端/ORM（如 mysql2、Prisma MySQL provider、TypeORM MySQL）— TAE 平台不支持 MySQL，需引导用户迁移到 PostgreSQL
   - 项目配置中有 `DATABASE_URL`、`DB_HOST`、`PGHOST` 等数据库连接环境变量
   - 用户明确说"需要 PostgreSQL"、"需要独立数据库"
   - 需要多副本共享同一份数据
   - **注意：TAE 平台只支持 PostgreSQL，不支持 MySQL**。如果项目原本使用 MySQL，需提示用户将数据库驱动/ORM 切换为 PostgreSQL 兼容配置，再创建数据库
   - 判定为此类时：先正常完成镜像发布和应用部署，然后在 Step 13 的部署流程中自动调用 `tae-app-manager` 的 `POST /databases` 创建 PostgreSQL 数据库，并将返回的连接信息配置到应用环境变量中
3. 先收敛参数，不要机械地把 1、2、3、4 四项全部重新问一遍：
   - 如果上下文里已经确认过镜像名，就直接复用
   - 如果用户只给了 `tag`，先校验其符合 Docker tag 格式，再尝试从仓库目录名或 `package.json` 名称推断镜像名候选，并把最终候选值回显给用户
   - 如果用户直接给了完整 `image-ref`，就不要再追问镜像名
   - 如果用户既没给 `tag` 也没给 `image-ref`，默认不要为了版本号单独卡住流程；直接生成当前时间 `YYYYMMDDHHMMSS` 版本标签，并在结果里回显
   - 如果用户的话术已经明确表达"现在开始发布 / 推送 / build"，可视为已给出执行确认；只有语义不明确时才补问
4. 不要把 registry 密码硬编码进脚本、文档或仓库文件，也不要重新引入技能级密码环境变量。需要登录时，明确到 `https://package-inner.truesightai.com/aliyun-acr/password.txt` 获取密码，并用该密码执行 `docker login registry.pixcakeai.com --username acr-tae@1055654169201791 --password-stdin`。
5. 默认只推送最终目标 tag，不额外推送 `latest` 别名；如果用户显式把 tag 设置为 `latest`，则按用户输入发布 `latest`。
6. **每次执行发布前**，都必须用 `curl` 从技能平台重新拉取最新版发布脚本，**禁止复用上次下载的旧脚本**，**严禁自己手写或凭记忆生成脚本内容**：
   ```bash
   mkdir -p scripts
   curl -fsSL "https://skill.tae.vera-mesh.com/skills/tae-image-release/scripts/publish-image.sh" -o scripts/publish-image.sh
   chmod +x scripts/publish-image.sh
   ```
   下载完成后执行 `bash scripts/publish-image.sh ...`。脚本会自动检测本地 Docker 可用性：
   - **本地有 Docker**：直接使用 `docker buildx build --platform linux/amd64 --push` 构建推送
   - **本地无 Docker**：自动通过 SSH 连接远端 ECS (`172.19.71.204`)，上传构建上下文并在远端执行构建推送，全程无需用户安装任何软件
   - 由脚本统一完成登录、镜像名推断、仓库前缀校验，以及最终交付字段输出
   - **不要自行用 Shell 命令逐步执行 SSH/SCP 或 docker 构建**；统一由脚本完成，保证流程一致性
   - **不要自己编写 publish-image.sh**；唯一合法来源是上面的 curl 下载地址
7. 发布完成后，先读取脚本最后输出的 canonical 结果块。`镜像地址` 只能来自脚本输出的 `FINAL_IMAGE_REF` 或同一结果块里的 `镜像地址：registry.pixcakeai.com/tae/...:tag`，不要从前面的 build 日志或本地 tag 回推。
8. 再按 TAE 创建页的字段顺序输出交付内容，而不是自由发挥。输出里始终固定保留四项：
   - `应用名称：<app-name>`
   - `镜像地址：<registry.pixcakeai.com/tae/...:tag>`
   - `服务端口：<port>`
   - `扩展存储：<不开启 | 10Gi | 20Gi ...>`
9. 如果脚本给出了 `FINAL_APP_NAME_CANDIDATE`、`FINAL_SERVICE_PORT_CANDIDATE` 等候选值，可以直接复用；如果脚本没有安全候选值，再从仓库上下文补齐。不要为了补齐 `服务端口` 或 `扩展存储` 去改写已经确认过的 `镜像地址`。
10. 根据第 2 步的判断结果输出存储和数据库信息：
    - **2A（无存储需求）**：`扩展存储：不开启`
    - **2B（简单数据库/文件存储 → SQLite + 扩展存储）**：`扩展存储：10Gi`，并在四行之后补充：
      > 存储提醒：请在高级选项里开启扩展存储，默认会挂载到 /data；如果不启用，Pod 重建后容器内数据不会保留。
      > 数据库建议：本项目使用 SQLite 即可满足需求，请确保应用将数据库文件存放在 /data 目录下（如 /data/app.db），这样数据会随扩展存储持久化。
    - **2C（复杂数据库需求 → 独立 PostgreSQL）**：`扩展存储` 根据是否还有文件写盘需求决定（有则 `10Gi`，无则 `不开启`），并在四行之后补充：
      > 数据库提醒：本项目需要独立 PostgreSQL 数据库服务（TAE 平台仅支持 PostgreSQL），部署完成后将自动调用 tae-app-manager 创建数据库并配置连接信息。
11. 无论哪种情况，`扩展存储` 字段都不能省略，必须明确写出。
12. 输出部署填写项时，字段名要尽量与页面一致，顺序也保持一致，方便用户直接照着表单填写。发布结果可补一句说明 tag 来源（自动时间戳或用户显式输入），但不要打乱前面的字段顺序。
13. **镜像推送成功后，自动调用 `tae-app-manager` skill 完成部署或更新**。具体流程：
    1. 先从技能平台拉取最新的 `tae-app-manager` SKILL.md：
       ```bash
       curl -fsSL "https://skill.tae.vera-mesh.com/skills/tae-app-manager/SKILL.md"
       ```
       读取其内容并严格按照其 Execution Steps 执行。
    2. 按照 `tae-app-manager` 的 Step 1 检查 `TAE_API_KEY` 环境变量。若不存在，提示用户获取并配置后再继续。
    3. 按照 `tae-app-manager` 的 Step 2 通过 WebFetch 获取最新 OpenAPI 文档（`https://tae.truesightai.com/api/v1/api-docs`）。
    4. 使用上面第 8 步收集到的交付信息（应用名称、镜像地址、服务端口、扩展存储）作为参数，先通过 `GET /apps/{name}` 查询应用是否已存在：
       - **应用不存在（返回 404）→ 创建新应用**：先向用户确认应用名称，格式如下：
         > 即将在 TAE 平台创建新应用，请确认以下信息：
         > - 应用名称：`{name}`
         > - 镜像地址：`{image_ref}`
         > - 服务端口：`{port}`
         > - 扩展存储：`{storage}`
         >
         > 确认创建吗？如需修改应用名称请直接告诉我。

         用户确认后才调用 `POST /apps` 创建；如果用户要求修改应用名称，使用修改后的名称。
       - **应用已存在（返回 200）→ 更新镜像**：先向用户确认是否更新，格式如下：
         > 检测到应用 `{name}` 已存在，当前镜像为 `{current_image}`。
         > 是否将镜像更新为 `{new_image_ref}`？

         用户明确同意后才调用 `PUT /apps/{name}` 更新；用户拒绝则跳过部署，仅输出交付信息。
    5. 按照 `tae-app-manager` 的 Step 4 执行部署后健康检查：等待 5 秒后查询状态，未就绪则重试（最多 6 次，间隔 10 秒），仍未就绪则进入问题排查。
    6. **如果第 2 步判定为 2C（复杂数据库需求），在应用部署成功后自动创建 PostgreSQL 数据库**：
       - 如果项目原本使用 MySQL，先提示用户：TAE 平台仅支持 PostgreSQL，需要将数据库驱动/ORM 切换为 PostgreSQL 兼容配置后再继续
       - 按照 `tae-app-manager` 的 Step 3 中"创建数据库"流程，调用 `POST /databases`，`app_name` 填应用名称
       - 创建成功后返回的连接信息（Host、端口、用户名、密码、连接 URL）**必须完整展示给用户，并强调密码仅此一次返回，务必保存**
       - 将数据库连接信息作为环境变量更新到应用配置中（调用 `PUT /apps/{name}` 更新 `env` 字段），常见变量名：`DATABASE_URL`、`DB_HOST`、`DB_PORT`、`DB_USER`、`DB_PASSWORD`、`DB_NAME`
       - 更新环境变量后需再次执行健康检查，确认应用能正常连接数据库
    7. 部署成功后，向用户汇报：应用名称、访问域名（`{name}.tae.vera-mesh.com`）、当前状态，如果创建了数据库还要汇报数据库连接信息，并给出下一步可选操作。
    - 如果用户在对话中明确说"只构建不部署"或"先不部署"，则跳过此步，仅输出第 8 步的交付信息。
    - 如果 `TAE_API_KEY` 不可用且用户不愿意现在配置，也跳过此步，并提示用户后续可手动到 `https://tae.truesightai.com/apps/create` 填写部署信息。

## Constraints

- 只允许推送到 `registry.pixcakeai.com/tae/`
- 固定平台 `linux/amd64`
- 本地无 Docker 时，统一通过 SSH 远端 ECS 构建；禁止要求用户安装 Homebrew、OrbStack、Docker Desktop 或任何本地 Docker 运行时；禁止要求用户输入 sudo 密码
- 远端构建的 SSH 认证通过密钥（`ssh -i`）自动完成，不依赖密码、sshpass 或任何需要 brew 安装的工具
- `publish-image.sh` 必须在每次发布前从 `https://skill.tae.vera-mesh.com/skills/tae-image-release/scripts/publish-image.sh` 用 curl 重新拉取；禁止复用上次下载的旧版本；严禁自行编写、拼接或凭记忆生成该脚本
- 如果用户没有显式给镜像名，只能做可解释、可回显的候选推断；不要静默拍脑袋决定镜像名
- 用户未提供 tag 时默认生成 `YYYYMMDDHHMMSS` 14 位数字；用户显式提供 tag 或完整 image-ref 时不强制时间戳，但必须符合 Docker tag 格式
- 不要在未配置国内源的前提下执行 `npm`、`pip`、`go mod download`、`apt-get install` 或 `apk add`
- `node`、`python`、`go` 的依赖源与 `debian`、`ubuntu`、`alpine` 的系统源都必须满足国内源规范，不能只配一层
- 构建上下文根目录必须存在 `.dockerignore`，且至少排除 `.git`、依赖目录（`node_modules`、`.venv` 等）、构建产物（`dist`、`build`、`.next` 等）、密钥（`.env`、`*.pem`、`*.key`）；不能因为"Dockerfile 里只 COPY 了 package.json"就省略 `.dockerignore`
- 远端 SSH 构建打 tarball 前，必须按 `.dockerignore` + `.git`/`node_modules` 硬编码黑名单过滤，避免把几百 MB 的依赖目录上传到远端 ECS；该逻辑统一由 `publish-image.sh` 实现，不要自行拼 `tar` 命令
- 不要把 registry 密码硬编码进脚本、文档或仓库文件；密码来源固定是 `https://package-inner.truesightai.com/aliyun-acr/password.txt`
- 用户一旦明确给出 tag、镜像地址或"开始发布/推送"的执行意图，就直接运行发布脚本，不要再回头追问是否执行
- 如果项目存在持久化需求，必须明确提醒用户在部署页开启"扩展存储"，并说明默认挂载路径是 `/data`
- 面向用户的最终交付格式必须固定给"应用名称 / 镜像地址 / 服务端口 / 扩展存储"四项，字段名和顺序尽量贴近页面，不要改成散文说明
- 最终 `镜像地址` 只能抄发布脚本末尾的 canonical registry 结果；禁止把本地镜像名、构建日志中的中间 tag、仓库目录推断值，或不带 registry 前缀的名字当成最终交付地址
- 不要额外推送 `latest` 别名；如果用户显式把目标 tag 设置为 `latest`，部署和发布记录使用脚本输出的 `latest` 镜像地址
- 镜像推送成功后，默认自动进入部署流程；仅在用户明确说"只构建不部署"或 `TAE_API_KEY` 不可用时跳过
- 创建新应用前必须向用户确认应用名称及关键参数，征得同意后才执行；用户要求改名则使用新名称
- 更新已有应用前必须向用户确认是否更新，展示当前镜像与新镜像的对比，用户同意后才执行；用户拒绝则跳过部署
- 部署流程严格遵循 `tae-app-manager` skill 的 Execution Steps，不要凭记忆猜测 API 接口
- 部署后必须执行健康检查（等待 + 重试），不能推完镜像就结束
- 数据库需求判断必须基于代码分析，不要凭猜测；简单需求（SQLite 够用）不要过度推荐 PostgreSQL，复杂需求（多副本/关系型）不要降级为 SQLite
- TAE 平台只支持 PostgreSQL 数据库，不支持 MySQL；如果项目使用 MySQL，必须提示用户迁移到 PostgreSQL
- 如果判定需要独立数据库，创建数据库后返回的密码仅出现一次，必须完整展示给用户并强调保存
- 创建数据库后必须将连接信息作为环境变量更新到应用，不能只创建数据库不配置连接

## Failure Handling

- 如果本地没有 Docker，脚本会自动通过 SSH 到远端 ECS 构建，全程无需用户手动操作
- 如果远端 ECS SSH 连接失败（网络不通、密钥无效等），直接报错并输出真实错误信息
- 如果远端 ECS 上的 docker/buildx 不可用，远端构建报错后如实展示
- 如果本地有 Docker 但 `docker buildx version` 不可用，先停止并提示用户安装或启用 buildx
- 如果镜像地址不以 `registry.pixcakeai.com/tae/` 开头，直接拒绝执行并要求用户修正参数
- 如果用户显式提供的镜像 tag 不符合 Docker tag 格式，直接拒绝执行并要求用户修正参数
- 如果 `Dockerfile` 不存在或构建上下文缺失，直接报错并返回缺失路径
- 如果镜像名无法从显式参数、上下文历史、仓库目录或 `package.json` 中推断出来，再向用户追问，不要伪造默认值
- 如果拉取密码 URL 失败、返回空内容，或 `docker login` 仍然失败，直接暴露真实错误；不要伪造"已登录"，也不要回退到仓库内明文密码
- 如果 Dockerfile 未满足国内源规范，先修正 Dockerfile，再重新执行发布流程
- 如果 `.dockerignore` 缺失，发布脚本会自动生成兜底版本并继续发布；但构建结束后要提示用户把生成的 `.dockerignore` 检查/提交到仓库，避免下次又被覆盖
- 如果用户反馈镜像里有 `node_modules`、`.git`、`.env` 等不该出现的内容，先检查 `.dockerignore` 是否存在、是否命中对应规则，再重新执行发布流程；不要靠 `docker image prune` 或手动清理镜像规避
- 如果无法判断项目的存储/数据库需求级别（2A/2B/2C），先基于代码中的数据库依赖（ORM/驱动包）、写盘行为、数据目录、环境变量名等线索做判断；线索不足时再用一句话向用户补问"你的项目需要数据库吗？简单存储用 SQLite 就行，复杂场景可以帮你创建 PostgreSQL"
- 如果用户没有给 tag，就自动生成当前时间 `YYYYMMDDHHMMSS` 版本标签并回显；不要为了版本号单独打断发布流程
- 如果当前环境没有 `publish_skill` MCP 能力，只交付符合规范的 `skill.yaml`、`SKILL.md` 和发布说明，不伪造"已发布成功"
- 如果脚本末尾没有输出 canonical 最终结果块，停止交付并回看脚本输出；不要自行从前面的 build/push 日志里猜最终镜像地址
- 如果 `TAE_API_KEY` 环境变量不存在或无效，跳过自动部署，提示用户到 `https://tae.truesightai.com/` 的「个人设置 → API Key」页面生成 Key 后重试；同时仍然输出完整的交付信息供手动部署
- 如果 `tae-app-manager` SKILL.md 拉取失败（curl 返回非 0），跳过自动部署，提示用户手动到 `https://tae.truesightai.com/apps/create` 创建应用
- 如果部署 API 调用失败（401/403/429/5xx），按 `tae-app-manager` 的 Failure Handling 表处理，不要静默吞错
- 如果部署后健康检查始终未通过，自动进入 `tae-app-manager` 的 Step 5 问题排查流程，向用户展示 Pod 事件和日志
- 如果无法判断项目是简单数据库需求还是复杂数据库需求，优先推荐 SQLite + 扩展存储（门槛最低），并告知用户如果后续需要 PostgreSQL 可随时通过 `tae-app-manager` 创建
- 如果数据库创建失败（`POST /databases` 返回错误），展示真实错误信息，并提示用户可到 TAE 控制台手动创建
- 如果数据库创建成功但应用环境变量更新失败，展示数据库连接信息让用户手动配置，不要丢弃已创建的数据库信息
