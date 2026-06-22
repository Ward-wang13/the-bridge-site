---
name: tae-app-manager
description: 通过 TAE (Truesight App Engine) API 管理 K8s 应用的全生命周期（创建、更新、查看、重启、排查）、数据库（创建、查看、重置密码）以及 Git 仓库（申请、查看、Token 轮转、解除分支保护）。推送代码前自动根据项目类型生成 .gitignore。支持 API Key 认证，覆盖三大模块。
---

# TAE App Manager

通过 TAE (Truesight App Engine) API 管理 K8s 应用、数据库以及 Git 仓库的全生命周期。

## When to Use

- 用户要求在 TAE 平台创建/部署一个新应用
- 用户要求更新已有应用的镜像、端口、环境变量等配置
- 用户要求查看某个应用的运行状态或 Pod 信息
- 用户要求重启应用
- 用户遇到应用故障，需要排查 Pod 事件或日志
- 用户要求创建数据库、查看数据库信息、重置数据库密码
- 用户要求为应用申请 Git 仓库、查看 Git 仓库信息、轮转 Git Token、解除默认分支保护
- 用户要求把本地代码推送到 TAE 自建 GitLab 上的仓库（需配套生成 .gitignore）
- 用户刚在 TAE 创建了新应用且运行成功——**即使用户没提**，也必须紧接着走 Step 4.5 强制申请 Git 仓库并上传首版代码
- 用户提到 TAE、tae-app、tae 部署、K8s 应用管理、数据库管理、git 仓库、gitlab 等关键词

## When NOT to Use

- 用户只是问 TAE 平台的产品介绍或架构设计（不需要调 API）
- 用户在开发 TAE 平台本身的代码（此时应直接操作源码）
- 与 TAE 无关的 K8s 操作（如直接 kubectl 管理非 TAE 资源）
- 用户需要管理 TAE 用户/权限/组（API Key 白名单不支持这些操作）
- 用户需要删除数据库/Git 仓库或其他管理员级操作（API Key 不支持）
- 用户要直接操作 GitLab 平台本身（用户/Group/权限管理等）——只操作自己 app 名下的仓库

## Prerequisites

- 环境变量 `TAE_API_KEY`（格式 `tae_` + 64 位 hex）
- 可访问 `https://tae.truesightai.com`
- 可使用 `curl` 和 `git` 命令
- 可使用 WebFetch 工具获取 OpenAPI 文档

## Execution Steps

### Step 1 — API Key 检查

1. 运行 `echo $TAE_API_KEY` 检查环境变量
2. 若为空，在 `~/.zshrc` 中搜索 `TAE_API_KEY`
3. 若均无，提示用户：

> 请先到 https://tae.truesightai.com/ 登录后，在「个人设置 → API Key」页面生成 API Key，然后把 Key 发给我，我来帮你配置。

用户提供 Key 后：

```bash
echo 'export TAE_API_KEY=tae_xxx' >> ~/.zshrc && source ~/.zshrc
```

### Step 2 — 获取最新 API 文档

**每次会话首次使用时**，通过 WebFetch 工具获取 OpenAPI 文档：

```
URL: https://tae.truesightai.com/api/v1/api-docs
```

该文档是 OpenAPI 3.0 JSON，包含所有可用接口的路径、方法、参数、请求体 Schema 和响应格式。**以此文档为准构造 API 调用，不要凭记忆猜测接口**。

获取后缓存在当前会话中，后续操作直接使用。

### Step 3 — 执行操作

**API 基础信息**：
- Base URL: `https://tae.truesightai.com/api/v1`
- 认证: `Authorization: Bearer $TAE_API_KEY`
- 限流: 30 次/分钟
- 响应格式: `{"success": bool, "data": ..., "error": "..."}`

根据 Step 2 获取的 OpenAPI 文档，构造对应的 curl 请求执行操作。

**创建应用**：根据 `POST /apps` Schema 构造请求。关键约束：
- `name`: 小写字母开头，仅含小写字母/数字/连字符，3-63 字符
- `image`: 必须在平台允许的 registry 列表中
- `port`: 1-65535
- 非 admin 用户 `replicas` 只能设 1

**更新应用**：根据 `PUT /apps/{name}` Schema 构造请求，只传变更字段（PATCH 语义）。

**查看状态**：调用 `GET /apps/{name}`，关注 `status`、`pods[].phase`、`pods[].ready`、`pods[].restart_count`。

**重启应用**：调用 `POST /apps/{name}/restart`。

**创建数据库**：根据 `POST /databases` Schema 构造请求。关键约束：
- `app_name`: 关联的应用名称，每个应用只能创建一个数据库
- 创建成功后返回数据库连接信息（含密码），**密码仅在此时返回，务必提示用户保存**

**查看我的数据库**：调用 `GET /databases` 列出当前用户创建的所有数据库。

**查看数据库详情**：调用 `GET /databases/{app_name}`，仅数据库创建者可查看。

**重置数据库密码**：调用 `POST /databases/{app_name}/reset-password`，返回新的连接信息（含新密码），**提醒用户同步更新应用中的数据库密码配置**。

**申请 Git 仓库**：调用 `POST /apps/{name}/git-repo`。关键点：
- 仅应用 owner（或 admin 代申请）可调用，每个应用只能绑定一个仓库
- **所有仓库固定创建在 group `tool-platform/other` 下**，`project_path` 形如 `tool-platform/other/<repo_name>`；`repo_name` 默认等于应用名，冲突时平台自动追加 `-2` / `-3`
- 响应 `data` 为 `GitRepo`，关注 `http_url`、`project_path`、`token_value`、`clone_command_https`、`token_expires_at`、`default_branch`
- **`token_value` 和 `clone_command_https` 仅在创建/轮转的响应里返回明文**，必须立即展示给用户并提醒保存；不要把 `clone_command_https` 写进任何会被提交或上传的文件
- 默认分支在创建时已自动解锁，可直接 push；**分支名以响应里的 `default_branch` 为准（可能是 `main` 或 `master`），不要硬编码**

**查看 Git 仓库**：调用 `GET /apps/{name}/git-repo`，返回的 `token_value` 是当前有效 token 的明文（仅 owner/admin 可见）。

**列出我的 Git 仓库**：调用 `GET /git-repos`，返回当前用户名下所有仓库（不含其他人的）。

**立即轮转 Git Token**：调用 `POST /apps/{name}/git-repo/rotate-token`。调用后旧 token 立即失效，响应返回新 token 明文。**提醒用户立刻更新本地 `git remote`**：
```bash
git remote set-url origin <new_clone_url_without_.git_path_or_with_token>
```

**解除默认分支保护**：调用 `POST /apps/{name}/git-repo/unprotect-branches`。仅用于历史老仓库修复（新仓库创建时已自动解锁）。幂等，重复调用无副作用。

### Step 4 — 部署后健康检查

创建或更新应用后**必须**执行：

1. 等待 5 秒后查询应用详情
2. 检查 `status == "running"` 且所有 Pod `ready == true`
3. 若未就绪，最多重试 6 次（间隔 10 秒，共约 1 分钟）
4. 若仍未就绪，自动进入问题排查流程（Step 5）
5. 向用户报告最终状态

### Step 4.5 — 新建应用运行成功后必须落仓（强制）

**触发条件**：Step 4 判定应用状态为 `running` 且所有 Pod `ready == true`，并且当前是**首次创建**该应用（区别于 update 场景）。判定依据：

- Step 3 走的是 `POST /apps`（而不是 `PUT /apps/{name}` / `POST /apps/{name}/restart`）。
- 或者 `GET /apps/{name}/git-repo` 返回 404（尚未绑定仓库）。

**必须完成的动作（不得跳过、不得改为"稍后再做"）**：

1. 调用 `POST /apps/{name}/git-repo` 申请仓库（走 Step 3「申请 Git 仓库」）。
2. 立即向用户展示 `http_url` / `project_path` / `token_value` / `token_expires_at`，并明确提示 token 仅此一次返回明文。
3. 在用户的本地项目目录里执行 Step 6 全流程：**识别项目类型 → 生成 / 补齐 `.gitignore`（需用户确认一次）→ 通过 TAE API 自动注入凭据 → 按 `default_branch` 对齐本地分支 → `git push` 首版代码**。
4. Step 6.5 推送后校验通过后才算真正完成，最终汇报需要同时覆盖「应用运行状态」+「仓库地址、首版 commit、Web URL」。

**豁免条件（只有满足任意一条才可跳过）**：

- `GET /apps/{name}/git-repo` 已返回存在的仓库（说明已落过仓）——此时只需提醒用户仓库地址、token 过期时间，不重复创建。
- `POST /apps/{name}/git-repo` 返回 `503 Git 仓库功能未启用`——说明该环境平台未启用 GitLab 对接，如实告知用户"该环境暂不支持，已跳过仓库创建"。
- 用户**明确**说"这是临时应用 / 稍后自己手动建仓"，此时仍要**记录一条警示**：没有入库的代码意味着下次更新镜像时无法回溯 commit，用户自担风险。

除以上三种情况外，**不得以"用户没说要建仓"为由跳过本步骤**——平台对新应用的默认契约就是"应用上线 ⇒ 代码入库"。

### Step 5 — 问题排查（按顺序）

1. **应用状态** — `GET /apps/{name}` 检查 status 和 Pod 状态
2. **应用事件** — `GET /apps/{name}/events` 关注 Warning 类型
3. **Pod 事件** — `GET /apps/{name}/pods/{pod}/events`（pod 名从步骤 1 获取）
4. **Pod 日志** — `GET /apps/{name}/pods/{pod}/logs?tail=200`
5. **崩溃日志** — `GET /apps/{name}/pods/{pod}/logs?tail=200&previous=true`（CrashLoopBackOff 时）

### Step 6 — 上传代码到 Git 仓库

当用户要把本地代码推送到 TAE 刚申请/已申请的 Git 仓库时，**严格按顺序**：

#### 6.1 确认仓库信息

调用 `GET /apps/{name}/git-repo` 获取当前仓库的 `http_url`、`token_value`、**`default_branch`** 三个字段。若接口返回 404，则先走 Step 3 的「申请 Git 仓库」。

> **重要**：`default_branch` 可能是 `main` 也可能是 `master`，**不同仓库不一定统一**（GitLab 创建时的默认分支名由平台配置决定，新老仓库也可能不同）。**必须以接口返回的 `default_branch` 为准**，不要硬编码 `main`。后续 6.4 的自动注入脚本会把这三个字段统一读出来给到 `$HTTP_URL` / `$REMOTE_DEFAULT` / `$TOKEN_VALUE` 变量，这里无需重复调用。

#### 6.2 识别项目类型

先 `ls -la` 查看项目根目录。根据下列标识文件**自动识别**语言/框架（可并行命中多种，如 Node + Python 混合项目）：

| 标识文件 | 项目类型 |
|----------|----------|
| `go.mod` / `go.sum` | Go |
| `package.json` | Node.js / Frontend（进一步看 `next.config.*` / `vite.config.*` / `nuxt.config.*` 细分） |
| `requirements.txt` / `pyproject.toml` / `Pipfile` / `setup.py` | Python |
| `pom.xml` / `build.gradle` / `build.gradle.kts` | Java (Maven / Gradle) |
| `Cargo.toml` | Rust |
| `composer.json` | PHP |
| `Gemfile` | Ruby |
| `*.csproj` / `*.sln` | .NET |
| `Dockerfile` | 容器项目（叠加生成规则） |
| `.terraform/` / `*.tf` | Terraform |

#### 6.3 生成或补齐 .gitignore

规则：
1. 若项目根目录**不存在** `.gitignore`，直接生成一份新的。
2. 若已存在，**读取现有内容**，只向文件末尾追加缺失的分组条目（以 `# === TAE added: <section> ===` 包裹），**不要覆盖、不要删除用户已有规则**。
3. 必含的「通用分组」（任何项目都要有）：

```gitignore
# === TAE: OS & Editor ===
.DS_Store
Thumbs.db
desktop.ini
*.swp
*.swo
*~
.idea/
.vscode/
*.iml
.project
.classpath
.settings/

# === TAE: Secrets & Local ===
.env
.env.*
!.env.example
!.env.sample
*.pem
*.key
*.crt
*.p12
.secrets/
credentials.json
kubeconfig
*.kubeconfig

# === TAE: Logs & Temp ===
*.log
logs/
tmp/
temp/
.cache/
```

4. 再按识别到的语言**叠加对应分组**（示例，根据实际项目裁剪）：

**Go**
```gitignore
# === TAE: Go ===
/bin/
/dist/
/build/
*.exe
*.test
*.out
coverage.txt
vendor/
```

**Node.js**
```gitignore
# === TAE: Node ===
node_modules/
npm-debug.log*
yarn-debug.log*
yarn-error.log*
pnpm-debug.log*
.pnpm-store/
dist/
build/
.next/
out/
.nuxt/
.vite/
.turbo/
coverage/
.npmrc
```

**Python**
```gitignore
# === TAE: Python ===
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
build/
dist/
*.egg-info/
.venv/
venv/
env/
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
htmlcov/
```

**Java**
```gitignore
# === TAE: Java ===
target/
build/
*.class
*.jar
*.war
.gradle/
```

**Rust**
```gitignore
# === TAE: Rust ===
target/
Cargo.lock.bak
```

**Docker / Terraform**（如命中）
```gitignore
# === TAE: Docker ===
.docker/
docker-compose.override.yml

# === TAE: Terraform ===
.terraform/
*.tfstate
*.tfstate.*
*.tfvars
!*.tfvars.example
```

> 若用户项目语言不在上表，使用 WebFetch 拉 `https://github.com/github/gitignore` 对应模板后再叠加。

生成后向用户展示新增/合并后的 `.gitignore` 摘要，**必须人工确认一次**再继续。

#### 6.4 初始化仓库并推送

**凭据策略（Recommended）：只在当前仓库持久化 token，不污染全局**

TAE 每个仓库都有自己的 Project Access Token，所以应把 token 持久化到**该仓库本地**的 credential store，而不是写进全局 `~/.git-credentials` 或 `~/.gitconfig`：

```bash
# 只在本仓库启用 store，并把凭据文件限定在 .git/ 内（不会被推送，也不会被其它仓库读到）
git config --local credential.helper 'store --file=.git/.git-credentials'
```

要点：
- `--local` 确保写入的是当前仓库的 `.git/config`，**不修改 `~/.gitconfig`**。
- `--file=.git/.git-credentials` 把明文凭据文件放在 `.git/` 下；`.git/` 本身就不会被 git 追踪，天然不会提交。**但仍需提醒用户 `.git/.git-credentials` 是明文 token**，不要手动 tar/拷贝整个项目目录外发。
- 如果用户坚持使用系统默认位置，可省略 `--file`（默认写到 `~/.git-credentials`），但**必须明确告知这会被同机器其它 repo 共享**。

#### 自动通过 TAE API 注入凭据（**必做，不要让用户手敲 token**）

所有 token 都从 TAE API 实时拉取并**直接写入当前仓库的 credential store**，整个过程用户零输入、terminal 也看不到明文。标准片段（后续场景 A/B 都复用）：

```bash
# 1. 一次性拉取仓库信息，包含 token_value / http_url / default_branch
APP_NAME="<你的 tae app 名>"
REPO_JSON="$(curl -sS -H "Authorization: Bearer $TAE_API_KEY" \
  "https://tae.truesightai.com/api/v1/apps/${APP_NAME}/git-repo")"

# 校验（失败就中断，不要盲目继续）
if [ "$(echo "$REPO_JSON" | jq -r '.success')" != "true" ]; then
  echo "拉取 git-repo 失败：$(echo "$REPO_JSON" | jq -r '.error')" >&2
  return 1 2>/dev/null || exit 1
fi

HTTP_URL="$(echo "$REPO_JSON"       | jq -r '.data.http_url')"
REMOTE_DEFAULT="$(echo "$REPO_JSON" | jq -r '.data.default_branch')"
TOKEN_VALUE="$(echo "$REPO_JSON"    | jq -r '.data.token_value')"

# http_url 里解析出 protocol 和 host，供 git credential 用
PROTO="${HTTP_URL%%://*}"
HOST_AND_PATH="${HTTP_URL#*://}"
HOST="${HOST_AND_PATH%%/*}"

# 2. 只在本仓库启用 store helper，指向 .git/ 下的凭据文件
git config --local credential.helper "store --file=.git/.git-credentials"

# 3. 通过 git credential approve 写入凭据（token 不会出现在 shell history 或 ps 输出里）
printf 'protocol=%s\nhost=%s\nusername=oauth2\npassword=%s\n\n' \
  "$PROTO" "$HOST" "$TOKEN_VALUE" \
  | git credential approve

# 4. 立即清空变量 + 收紧凭据文件权限
unset TOKEN_VALUE REPO_JSON
[ -f .git/.git-credentials ] && chmod 600 .git/.git-credentials

# 5. 配置 remote 用「纯净 URL」（不含 token；git 会自动从 store 里取凭据）
if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "$HTTP_URL"
else
  git remote add    origin "$HTTP_URL"
fi
```

执行要点：
- `git credential approve` 读取当前仓库的 `credential.helper`，因为我们刚 `--local` 设了 store，**只会**写进 `.git/.git-credentials`，不会动 `~/.git-credentials`。
- 通过 `printf | git credential approve` 传递，token 只在管道内存里走一遍，**不进 shell 历史、不会被 `ps aux` 看到**。
- 不要改用 `git remote add origin "http://oauth2:$TOKEN@host/..."`——那样 token 会落进 `.git/config` 里。
- 之后的 `git pull / push` 直接用 remote 纯净 URL，git 自动从 `.git/.git-credentials` 里取凭据，**用户全程不用输入**。

**场景 A：本地还不是 git 仓库**

```bash
git init
# 用接口返回的 default_branch（main 或 master）统一本地分支名
git branch -M "$REMOTE_DEFAULT"

# —— 插入上面「自动通过 TAE API 注入凭据」的 1~5 步 ——

git add .
git status   # 先让用户肉眼复核一遍，确认没有误提交的敏感文件
git commit -m "chore: initial commit"
git push -u origin "$REMOTE_DEFAULT"   # 凭据已注入，无需任何输入
```

**场景 B：已有本地 git 仓库，只是换/加 remote**

先对比本地当前分支 (`git rev-parse --abbrev-ref HEAD`) 与 `$REMOTE_DEFAULT`：

- 两者一致（都是 main 或都是 master）→ 直接 push：
  ```bash
  git push -u origin "$REMOTE_DEFAULT"
  ```
- 本地是 `master` 而远端默认 `main`（或反之）→ 三选一，由用户决定：
  1. **把本地分支重命名成远端默认名**（推荐，避免歧义）：
     ```bash
     git branch -M "$REMOTE_DEFAULT"
     git push -u origin "$REMOTE_DEFAULT"
     ```
  2. **保留本地名，但把远端默认分支改成本地名**：
     登录 GitLab Web 进入 `Settings → Repository → Default branch` 修改；或让 admin 走 GitLab API。
  3. **两个分支都推**（一般不推荐）：
     ```bash
     git push -u origin HEAD:"$REMOTE_DEFAULT"   # 把当前 HEAD 推到远端默认分支
     ```

完整命令（续上面的自动注入片段）：

```bash
git status                           # 确认工作区干净
git add .gitignore
git commit -m "chore: add .gitignore for TAE deploy"

# —— 已通过「自动注入凭据」完成 credential.helper + remote 配置 ——

LOCAL_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ "$LOCAL_BRANCH" = "$REMOTE_DEFAULT" ]; then
  git push -u origin "$REMOTE_DEFAULT"
else
  echo "本地分支 $LOCAL_BRANCH 与远端默认分支 $REMOTE_DEFAULT 不一致，请先确认处理方式"
fi
```

执行要点：
- **不要**给用户生成 `git remote add origin https://oauth2:<token>@...` 这种把 token 拼进 URL 的命令——这会让 token 同时出现在 `.git/config` 和 `git remote -v` 输出里，更难回收。改用上面的 store helper 方案。
- **在执行 `git push` 之前**，必须先 `git status` + `git ls-files -ci --exclude-standard`（或 `git check-ignore -v <sensitive files>`）人工核对，确认 `.env`、密钥、`node_modules`、构建产物未被纳入提交。
- 若必须写带 token 的 URL（比如一次性 `git clone` 做备份），**绝不能回显到终端历史或日志**，用环境变量注入：
  ```bash
  read -rs TAE_GIT_TOKEN   # 隐式读入，不会留在 history
  git clone "http://oauth2:${TAE_GIT_TOKEN}@git.inner.truesightai.com/tool-platform/other/my-app.git"
  unset TAE_GIT_TOKEN
  ```
- 推送完成后提醒用户：Token 会在到期前 7 天自动轮转。轮转后 remote URL 不用动，**再跑一次上面那段「自动通过 TAE API 注入凭据」的脚本**，会自动用新 token 覆盖 `.git/.git-credentials`（`git credential approve` 对同一 `protocol+host+username` 是覆盖写）。如果用户嫌麻烦，也可以直接：
  ```bash
  rm -f .git/.git-credentials   # 删掉旧记录
  ```
  然后再跑一次自动注入脚本即可。

#### 6.5 推送后校验

1. 报告 push 成功时的本地分支、commit SHA。
2. 提示 GitLab 页面 URL（`http_url` 去掉 token 和 `.git` 后缀即是 Web URL，或直接用响应里的 `web_url` 字段如果有）。
3. 若 push 因为 `protected branch` 失败，**自动**调用「解除默认分支保护」接口修复并重试一次，流程见 6.6。

#### 6.6 Protected branch 自动自愈（无需用户介入）

新仓库创建时分支已自动解锁，但老仓库 / 某些异常路径下首次 push 仍可能拿到：

```
remote: GitLab: You are not allowed to push code to protected branches on this project.
remote: error: failed to push some refs to '...'
```

或带关键字 `protected branch` / `protected branches`。这属于**已知自愈场景**，**严禁**直接把错误抛给用户让其去 GitLab UI 手点——必须在本次操作里一次性闭环：

```bash
# 1. push 失败后捕获错误输出
PUSH_LOG="$(git push -u origin "$REMOTE_DEFAULT" 2>&1)"
PUSH_RC=$?

if [ $PUSH_RC -ne 0 ] && printf '%s' "$PUSH_LOG" | grep -qiE 'protected branch|not allowed to push'; then
  echo "检测到 protected branch 阻止 push，自动调用 unprotect-branches 接口..."

  # 2. 调用 TAE 接口解除所有默认分支保护（幂等）
  UNPROTECT_RESP="$(curl -sS -m 10 -X POST \
    -H "Authorization: Bearer $TAE_API_KEY" \
    "https://tae.truesightai.com/api/v1/apps/${APP_NAME}/git-repo/unprotect-branches")"

  if [ "$(echo "$UNPROTECT_RESP" | jq -r '.success')" != "true" ]; then
    echo "解除分支保护失败：$(echo "$UNPROTECT_RESP" | jq -r '.error')" >&2
    echo "请在 GitLab Web 上手动清空 Protected Branches 后重试。" >&2
    return 1 2>/dev/null || exit 1
  fi

  # 3. 解除成功后立即重试一次 push
  git push -u origin "$REMOTE_DEFAULT"
  PUSH_RC=$?
fi

# 若仍失败，此时才抛回给用户看真实原因
[ $PUSH_RC -eq 0 ] || { echo "$PUSH_LOG" >&2; exit $PUSH_RC; }
```

触发规则：
- **匹配关键字**：push 输出里出现 `protected branch` / `protected branches` / `not allowed to push`（不区分大小写）。
- **自愈动作**：`POST /apps/{name}/git-repo/unprotect-branches`（幂等，无副作用，重复调用 OK）。
- **重试次数**：最多 1 次。若第二次仍失败，原样抛错给用户，并提示可能原因：GitLab Web 上手工加的其它保护规则、push 内容触发了其它 hook（不是 protected branch）等。
- **禁止循环**：不要写成 `while` 反复 unprotect + push，避免把真正非 protected-branch 的故障当成同一类问题无限重试。
- **汇报给用户**：如果触发了自愈，必须在最终总结里写明「首次 push 遇到 protected branch，已自动调用解保护接口并重试成功」，让用户知晓这一行为。

## Constraints

- 不要凭记忆猜测 API 接口，必须从 OpenAPI 文档获取最新接口信息
- API Key 限流 30 次/分钟，批量操作时注意控制频率
- 非 admin 用户不可设置 replicas > 1，不可调整副本数
- 镜像地址必须在平台允许的 registry 列表中
- 不要执行删除数据库/Git 仓库等 API Key 不支持的操作
- **推送代码前必须先生成/补齐 `.gitignore`，并由用户确认一次**；严禁让 `.env`、密钥、`node_modules`、构建产物、`venv/` 等进入提交
- **绝不能把 `clone_command_https`（含 token 明文）写进任何会被 `git add` 的文件、README 或 commit message**；也不要直接写进 `git remote add origin https://oauth2:<token>@...`，因为这样 token 会落进 `.git/config`
- 凭据持久化**必须**使用 `git config --local credential.helper 'store --file=.git/.git-credentials'`，保证只影响当前仓库；严禁修改全局 `~/.gitconfig` 或默认 `~/.git-credentials`
- **Token 必须通过 TAE API 自动注入**（`curl /apps/:name/git-repo` → `git credential approve`），不要让用户手动粘贴 token 到终端，更不要拼进 URL 里
- **新应用一旦 `running` 且 Pod 全部 ready，必须立刻进入 Step 4.5 申请 Git 仓库并推首版代码**，不得以"用户没说"为由跳过；只有仓库已存在、平台未启用 GitLab、或用户明确拒绝并接受风险时方可豁免
- **push 遇到 `protected branch` 类错误必须自动调用 `POST /apps/{name}/git-repo/unprotect-branches` 并重试一次**（Step 6.6），严禁直接把错误原样抛给用户或让其去 GitLab UI 手工处理；仅当重试后仍失败才原样返回错误
- 轮转 token 后再跑一次自动注入脚本即可（`git credential approve` 会覆盖同 host 的旧记录），否则下次 push 会 401

## Failure Handling

| 症状 | 可能原因 | 处理方式 |
|------|----------|----------|
| 401 Unauthorized | API Key 无效或过期 | 提示用户重新生成 API Key |
| 403 Forbidden | API Key 不支持此操作 / 非 owner | 说明该操作需要通过 Web UI 完成或让 owner 本人来 |
| 404 仓库不存在 | 应用未申请 Git 仓库 | 走 Step 3 的「申请 Git 仓库」 |
| 409 仓库已绑定或配额已用完 | 该应用已有仓库或用户配额满 | 先 `GET /apps/{name}/git-repo` 查看现有仓库 |
| 429 Too Many Requests | 超过限流 | 等待 60 秒后重试 |
| 503 Git 仓库功能未启用 | 平台未配置 GitLab 对接 | 告知用户该环境暂不支持 Git 仓库能力 |
| Pod Pending | 资源不足 / 调度失败 | 查看应用事件，报告给用户 |
| ImagePullBackOff | 镜像地址错误 / 无拉取权限 | 确认镜像地址和 registry 是否正确 |
| CrashLoopBackOff | 应用启动崩溃 | 查看 Pod 日志（含 previous），分析崩溃原因 |
| OOMKilled | 内存超限 | 查看 Pod 事件，建议调大资源限制 |
| 502/503（应用访问） | 端口不匹配 / 应用未就绪 | 确认 port 配置与应用实际监听端口一致 |
| git push `remote: You are not allowed to push code to protected branches` / `protected branch` 关键字 | main/master 仍处于保护状态 | **自动**调用 `POST /apps/{name}/git-repo/unprotect-branches` 并重试一次 push（见 Step 6.6），不要把这条错误直接抛给用户 |
| `error: src refspec main does not match any` / `master does not match any` | 本地分支名和 push 目标分支名不一致（例如本地是 master 要推 main） | 先 `git rev-parse --abbrev-ref HEAD` 查本地分支，再 `GET /apps/{name}/git-repo` 拿 `default_branch`，用 `git branch -M <remote_default>` 对齐，或 `git push origin HEAD:<remote_default>` 显式映射 |
| git push 401 | token 已被轮转或过期 | 再跑一次 6.4 的「自动注入凭据」脚本，`git credential approve` 会用新 token 覆盖旧记录；remote URL 不用动 |
| git push 413 / "pack exceeds maximum" | 仓库内包含大文件 / 构建产物 | 检查 `.gitignore` 是否漏了 `dist/` `build/` `node_modules/` 等，必要时 `git rm --cached` + `git filter-repo` |

## 输出规范

操作完成后向用户汇报：
- **创建/更新应用**: 应用名称、访问域名（`{name}.tae.vera-mesh.com`）、当前状态；**新建场景还必须附带 Git 仓库地址、首版 commit SHA、Web URL、token 过期时间**（来自 Step 4.5）
- **创建数据库**: 数据库名、用户名、密码、Host、端口、连接 URL，提示用户保存密码
- **重置数据库密码**: 新密码和新连接 URL，提醒更新应用配置
- **申请/轮转 Git 仓库**: 仓库路径、HTTPS URL、token 明文（单独成行便于复制）、`clone_command_https`、token 过期时间；提醒 token 仅此一次返回明文，务必妥善保管
- **推送代码**: 生成的 `.gitignore` 内容摘要、push 后的分支/SHA、Web 访问地址、下一次 token 轮转时间
- **排查**: 问题摘要、原因分析、修复建议
- 始终给出下一步可选操作
