#!/usr/bin/env bash
set -euo pipefail

readonly REGISTRY_HOST="registry.pixcakeai.com"
readonly REPOSITORY_PREFIX="${REGISTRY_HOST}/tae/"
readonly REGISTRY_USERNAME="acr-tae@1055654169201791"
readonly REGISTRY_PASSWORD_SOURCE_URL="https://package-inner.truesightai.com/aliyun-acr/password.txt"
readonly PLATFORM="linux/amd64"
readonly IMAGE_TAG_PATTERN="^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$"

readonly REMOTE_BUILD_HOST="172.19.71.204"
readonly REMOTE_BUILD_USER="root"
readonly SSH_KEY_SOURCE_URL="https://package-inner.truesightai.com/aliyun-acr/tae-build-key"
_SSH_KEY_FILE=""

ensure_dockerignore() {
  local ctx_dir="$1"
  local dockerignore_path="${ctx_dir}/.dockerignore"

  if [[ -f "${dockerignore_path}" ]]; then
    return 0
  fi

  echo "未检测到 ${dockerignore_path}，正在写入默认 .dockerignore，避免 node_modules / .git / 构建产物 / 密钥等被打进镜像..." >&2
  cat > "${dockerignore_path}" <<'DOCKERIGNORE_EOF'
# 自动生成的 .dockerignore（tae-image-release 兜底模板）
# 建议提交到仓库；如需定制请直接修改本文件，下次发布脚本不会覆盖已存在文件。

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

# 环境变量 / 密钥（保留 .env.example 之类示例文件）
.env
.env.*
!.env.example
*.pem
*.key

# Node / 前端
node_modules
bower_components
jspm_packages
.pnpm-store
npm-debug.log*
yarn-debug.log*
yarn-error.log*
.pnpm-debug.log*
.npm
.yarn/cache
.yarn/unplugged
.yarn/install-state.gz
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

# Python
__pycache__
*.py[cod]
*$py.class
*.so
.venv
venv
env
ENV
.pytest_cache
.mypy_cache
.ruff_cache
.tox
.coverage
.coverage.*
htmlcov
*.egg-info
*.egg

# Go / Rust / 其他
bin
tmp
target
*.test
*.exe
*.out
DOCKERIGNORE_EOF

  echo ".dockerignore 已生成：${dockerignore_path}。请在合适时机将其提交到仓库。" >&2
}

build_context_tar_excludes() {
  local ctx_dir="$1"
  local out_file="$2"

  : > "${out_file}"

  printf "%s\n" \
    ".git" \
    "node_modules" \
    >> "${out_file}"

  if [[ -f "${ctx_dir}/.dockerignore" ]]; then
    awk '
      {
        sub(/\r$/, "")
        line = $0
        sub(/^[[:space:]]+/, "", line)
        sub(/[[:space:]]+$/, "", line)
        if (line == "") next
        if (substr(line, 1, 1) == "#") next
        if (substr(line, 1, 1) == "!") next
        sub(/^\.\//, "", line)
        sub(/\/+$/, "", line)
        if (line == "") next
        print line
      }
    ' "${ctx_dir}/.dockerignore" >> "${out_file}"
  fi
}

app_name_candidate_from_ref() {
  local image_ref="$1"
  local repository="${image_ref%:*}"
  basename "${repository}"
}

infer_service_port() {
  local dockerfile_path="$1"
  local first_port=""

  if [[ ! -f "${dockerfile_path}" ]]; then
    return 1
  fi

  first_port="$(sed -nE 's/^[[:space:]]*EXPOSE[[:space:]]+([0-9]+).*/\1/p' "${dockerfile_path}" | head -n 1)"
  if [[ -z "${first_port}" ]]; then
    return 1
  fi

  printf "%s" "${first_port}"
}

infer_storage() {
  local dockerfile_path="$1"
  if [[ ! -f "${dockerfile_path}" ]]; then
    printf "不开启"
    return
  fi
  local content
  content="$(cat "${dockerfile_path}")"

  if printf '%s' "${content}" | grep -qiE 'VOLUME\s|sqlite|\.db|/data|/uploads|/storage|/var/lib|/tmp/cache|leveldb|rocksdb|badger'; then
    printf "10Gi（建议在高级选项开启扩展存储，默认挂载 /data）"
    return
  fi

  printf "不开启"
}

print_final_delivery_block() {
  local final_ref="$1"
  local dockerfile_path="$2"
  local app_name=""
  local service_port=""
  local storage=""

  app_name="$(app_name_candidate_from_ref "${final_ref}")"
  service_port="$(infer_service_port "${dockerfile_path}" || true)"
  storage="$(infer_storage "${dockerfile_path}")"

  echo
  echo "=== FINAL_IMAGE_OUTPUT ==="
  echo "FINAL_IMAGE_REF=${final_ref}"
  echo "FINAL_APP_NAME_CANDIDATE=${app_name}"
  if [[ -n "${service_port}" ]]; then
    echo "FINAL_SERVICE_PORT_CANDIDATE=${service_port}"
  fi
  echo "FINAL_STORAGE=${storage}"
  echo
  echo "应用名称：${app_name}"
  echo "镜像地址：${final_ref}"
  echo "服务端口：${service_port:-待确认}"
  echo "扩展存储：${storage}"
  echo "说明：最终镜像地址只能使用上面的 registry 地址；不要复述本地 build/tag 名称。"
  echo
  echo "=== DEPLOY_HINT ==="
  echo "镜像推送成功，下一步自动调用 tae-app-manager skill 部署应用。"
  echo "DEPLOY_APP_NAME=${app_name}"
  echo "DEPLOY_IMAGE_REF=${final_ref}"
  if [[ -n "${service_port}" ]]; then
    echo "DEPLOY_SERVICE_PORT=${service_port}"
  fi
  echo "DEPLOY_STORAGE=${storage}"
  echo "DEPLOY_SKILL_URL=https://skill.tae.vera-mesh.com/skills/tae-app-manager/SKILL.md"
}

usage() {
  cat <<'EOF'
用法:
  publish-image.sh --image-name <name> [--tag <tag>] [--dockerfile <path>] [--context <path>]
  publish-image.sh --image-ref <registry.pixcakeai.com/tae/...:tag> [--dockerfile <path>] [--context <path>]

参数:
  --tag           版本标签；不传时自动使用当前时间 YYYYMMDDHHMMSS
  --image-name    镜像名，例如 app-engine 或 foo/bar
  --image-ref     完整镜像地址，必须以 registry.pixcakeai.com/tae/ 开头
  --dockerfile    Dockerfile 路径，默认 Dockerfile
  --context       docker build 上下文，默认当前仓库根目录
  --push-latest   已禁用：默认不额外推送 latest；如需 latest，请显式使用 --tag latest
  --no-push-latest  兼容旧命令，当前默认不推送 latest
  --help          显示帮助
EOF
}

require_command() {
  local command_name="$1"
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "缺少命令: ${command_name}" >&2
    exit 1
  fi
}

fetch_registry_password() {
  local fetched_password=""

  require_command "curl"

  fetched_password="$(
    curl -fsSL "${REGISTRY_PASSWORD_SOURCE_URL}" | tr -d '\r'
  )" || {
    echo "无法从 ${REGISTRY_PASSWORD_SOURCE_URL} 获取 registry 密码" >&2
    exit 1
  }

  fetched_password="$(printf "%s" "${fetched_password}" | sed -e 's/[[:space:]]*$//')"
  if [[ -z "${fetched_password}" ]]; then
    echo "从 ${REGISTRY_PASSWORD_SOURCE_URL} 获取到的 registry 密码为空" >&2
    exit 1
  fi

  printf "%s" "${fetched_password}"
}

is_local_docker_available() {
  command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1
}

setup_ssh_key() {
  require_command "curl"
  _SSH_KEY_FILE="$(mktemp)"
  curl -fsSL "${SSH_KEY_SOURCE_URL}" -o "${_SSH_KEY_FILE}" || {
    echo "无法从 ${SSH_KEY_SOURCE_URL} 下载 SSH 私钥" >&2
    rm -f "${_SSH_KEY_FILE}"
    exit 1
  }
  if [[ ! -s "${_SSH_KEY_FILE}" ]]; then
    echo "下载的 SSH 私钥文件为空" >&2
    rm -f "${_SSH_KEY_FILE}"
    exit 1
  fi
  chmod 600 "${_SSH_KEY_FILE}"
}

cleanup_ssh_key() {
  [[ -n "${_SSH_KEY_FILE}" && -f "${_SSH_KEY_FILE}" ]] && rm -f "${_SSH_KEY_FILE}"
  _SSH_KEY_FILE=""
}

remote_ssh() {
  ssh -i "${_SSH_KEY_FILE}" \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    -o LogLevel=ERROR \
    -o ConnectTimeout=15 \
    -o BatchMode=yes \
    "${REMOTE_BUILD_USER}@${REMOTE_BUILD_HOST}" "$@"
}

remote_scp() {
  scp -i "${_SSH_KEY_FILE}" \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    -o LogLevel=ERROR \
    -o ConnectTimeout=15 \
    -o BatchMode=yes \
    "$@"
}

remote_build_and_push() {
  local target_ref="$1"
  local dockerfile="$2"
  local context="$3"

  echo "本地未检测到可用的 Docker 环境，将通过 SSH 在远端 ECS (${REMOTE_BUILD_HOST}) 执行构建..."

  local registry_pw
  registry_pw="$(fetch_registry_password)"

  setup_ssh_key
  _REMOTE_BUILD_TARBALL=""
  trap 'cleanup_ssh_key; [[ -n "${_REMOTE_BUILD_TARBALL:-}" && -f "${_REMOTE_BUILD_TARBALL:-}" ]] && rm -f "${_REMOTE_BUILD_TARBALL}"' EXIT

  local remote_dir="/tmp/tae-build-$(date +%s)-$$"

  local abs_context abs_dockerfile
  abs_context="$(cd "${context}" && pwd)"
  abs_dockerfile="$(cd "$(dirname "${dockerfile}")" && pwd)/$(basename "${dockerfile}")"

  _REMOTE_BUILD_TARBALL="$(mktemp -t tae-ctx-XXXXXX).tar.gz"
  local tar_exclude_file
  tar_exclude_file="$(mktemp -t tae-ignore-XXXXXX)"
  build_context_tar_excludes "${abs_context}" "${tar_exclude_file}"

  echo "正在打包构建上下文（已按 .dockerignore 过滤 node_modules / .git 等）..."
  tar -czf "${_REMOTE_BUILD_TARBALL}" \
    --exclude-from="${tar_exclude_file}" \
    -C "${abs_context}" .
  rm -f "${tar_exclude_file}"

  local tarball_size=""
  if command -v du >/dev/null 2>&1; then
    tarball_size="$(du -h "${_REMOTE_BUILD_TARBALL}" 2>/dev/null | awk '{print $1}')"
  fi
  if [[ -n "${tarball_size}" ]]; then
    echo "构建上下文打包大小：${tarball_size}"
  fi

  echo "正在上传构建上下文到远端..."
  remote_ssh "mkdir -p '${remote_dir}/ctx'"
  remote_scp "${_REMOTE_BUILD_TARBALL}" "${REMOTE_BUILD_USER}@${REMOTE_BUILD_HOST}:${remote_dir}/ctx.tar.gz"
  rm -f "${_REMOTE_BUILD_TARBALL}"
  _REMOTE_BUILD_TARBALL=""
  remote_ssh "cd '${remote_dir}/ctx' && tar xzf ../ctx.tar.gz && rm -f ../ctx.tar.gz"

  local remote_dockerfile_path="${remote_dir}/ctx/${dockerfile}"
  if [[ "${abs_dockerfile}" != "${abs_context}/"* ]]; then
    remote_scp "${abs_dockerfile}" "${REMOTE_BUILD_USER}@${REMOTE_BUILD_HOST}:${remote_dir}/Dockerfile.external"
    remote_dockerfile_path="${remote_dir}/Dockerfile.external"
  fi

  local registry_pw_b64
  registry_pw_b64="$(printf '%s' "${registry_pw}" | base64)"

  echo "正在远端执行 docker build & push..."
  remote_ssh "
set -euo pipefail
trap 'rm -rf \"${remote_dir}\"' EXIT
printf '%s' '${registry_pw_b64}' | base64 -d | docker login '${REGISTRY_HOST}' --username '${REGISTRY_USERNAME}' --password-stdin >/dev/null 2>&1
echo 'registry 登录成功'
docker buildx build \\
  --platform '${PLATFORM}' \\
  --file '${remote_dockerfile_path}' \\
  --tag '${target_ref}' \\
  --push \\
  '${remote_dir}/ctx'
"

  cleanup_ssh_key
  _REMOTE_BUILD_TARBALL=""
  trap - EXIT
  echo "远端构建并推送完成"
}

ensure_docker_login() {
  if docker pull "${REPOSITORY_PREFIX}login-probe:nonexistent" >/dev/null 2>&1; then
    return 0
  fi
  local err
  err="$(docker pull "${REPOSITORY_PREFIX}login-probe:nonexistent" 2>&1 || true)"
  if printf '%s' "${err}" | grep -qi "unauthorized\|authentication required\|denied"; then
    echo "未检测到 ${REGISTRY_HOST} 的有效登录态，正在自动登录..."
    local registry_pw
    registry_pw="$(fetch_registry_password)"
    printf '%s' "${registry_pw}" | docker login "${REGISTRY_HOST}" --username "${REGISTRY_USERNAME}" --password-stdin
    echo "registry 登录成功"
  fi
}

require_option_value() {
  local option_name="$1"
  local option_value="${2:-}"

  if [[ -z "${option_value}" ]]; then
    echo "参数 ${option_name} 缺少取值" >&2
    usage >&2
    exit 1
  fi
}

normalize_image_name() {
  local raw_value="$1"
  printf "%s" "${raw_value}" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -E 's/^@[^/]+\///; s/[^a-z0-9._/-]+/-/g; s/-platform$//; s/-service$//; s/-app$//; s#/{2,}#/#g; s/-{2,}/-/g; s#^[-/]+##; s#[-/]+$##'
}

package_name_to_image_name() {
  local package_path="$1"
  local raw_name=""

  if ! command -v node >/dev/null 2>&1; then
    return 1
  fi

  raw_name="$(node -e "const fs=require('fs'); const file=process.argv[1]; try { const data=JSON.parse(fs.readFileSync(file,'utf8')); if (typeof data.name === 'string') process.stdout.write(data.name); } catch {}" "${package_path}")"
  if [[ -z "${raw_name}" ]]; then
    return 1
  fi

  normalize_image_name "${raw_name}"
}

infer_image_name() {
  local search_context="$1"
  local resolved_context=""
  local candidate=""
  local package_path=""

  resolved_context="$(cd "${search_context}" 2>/dev/null && pwd || printf "%s" "${search_context}")"

  if [[ -n "${TAE_IMAGE_NAME:-}" ]]; then
    candidate="$(normalize_image_name "${TAE_IMAGE_NAME}")"
    [[ -n "${candidate}" ]] && printf "%s" "${candidate}" && return 0
  fi

  if [[ -n "${IMAGE_NAME:-}" ]]; then
    candidate="$(normalize_image_name "${IMAGE_NAME}")"
    [[ -n "${candidate}" ]] && printf "%s" "${candidate}" && return 0
  fi

  for package_path in \
    "${resolved_context}/package.json" \
    "${PWD}/package.json" \
    "${resolved_context}/platform/package.json" \
    "${PWD}/platform/package.json"
  do
    if [[ -f "${package_path}" ]]; then
      candidate="$(package_name_to_image_name "${package_path}" || true)"
      [[ -n "${candidate}" ]] && printf "%s" "${candidate}" && return 0
    fi
  done

  candidate="$(normalize_image_name "$(basename "${resolved_context}")")"
  [[ -n "${candidate}" ]] && printf "%s" "${candidate}" && return 0

  return 1
}

validate_image_name() {
  local image_name="$1"

  if [[ -z "${image_name}" ]]; then
    echo "镜像名不能为空" >&2
    exit 1
  fi

  if [[ "${image_name}" == */ || "${image_name}" == /* || "${image_name}" == *:* || "${image_name}" == "${REGISTRY_HOST}"* ]]; then
    echo "镜像名只能填写仓库前缀后的名称，例如 app-engine 或 foo/bar" >&2
    exit 1
  fi
}

validate_image_tag() {
  local image_tag="$1"

  if [[ ! "${image_tag}" =~ ${IMAGE_TAG_PATTERN} ]]; then
    echo "镜像 tag 格式不合法，仅支持 Docker tag 格式，例如 20260520123456、v1.2.3、release-1" >&2
    exit 1
  fi
}

build_image_ref() {
  local image_name="$1"
  local tag="$2"
  printf "%s%s:%s" "${REPOSITORY_PREFIX}" "${image_name}" "${tag}"
}

generate_default_tag() {
  date +%Y%m%d%H%M%S
}

image_name=""
image_ref=""
tag=""
dockerfile="Dockerfile"
context="."
inferred_image_name="false"
generated_tag="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tag)
      require_option_value "$1" "${2:-}"
      tag="${2:-}"
      shift 2
      ;;
    --image-name)
      require_option_value "$1" "${2:-}"
      image_name="${2:-}"
      shift 2
      ;;
    --image-ref)
      require_option_value "$1" "${2:-}"
      image_ref="${2:-}"
      shift 2
      ;;
    --dockerfile)
      require_option_value "$1" "${2:-}"
      dockerfile="${2:-}"
      shift 2
      ;;
    --context)
      require_option_value "$1" "${2:-}"
      context="${2:-}"
      shift 2
      ;;
    --push-latest)
      echo "默认不额外推送 latest；如需 latest，请显式使用 --tag latest" >&2
      exit 1
      ;;
    --no-push-latest)
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "未知参数: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -n "${image_ref}" && -n "${tag}" ]]; then
  echo "--image-ref 与 --tag 只能二选一" >&2
  exit 1
fi

if [[ -n "${image_ref}" ]]; then
  if [[ "${image_ref}" != "${REPOSITORY_PREFIX}"* ]]; then
    echo "镜像地址必须以 ${REPOSITORY_PREFIX} 开头" >&2
    exit 1
  fi
  if [[ "${image_ref##*/}" != *:* ]]; then
    echo "--image-ref 必须包含 tag，例如 registry.pixcakeai.com/tae/app-engine:v1.2.3" >&2
    exit 1
  fi
  validate_image_tag "${image_ref##*:}"
  target_ref="${image_ref}"
else
  if [[ -z "${image_name}" ]]; then
    image_name="$(infer_image_name "${context}" || true)"
    if [[ -z "${image_name}" ]]; then
      echo "未显式提供 --image-name，且当前仓库/环境不足以推断镜像名" >&2
      usage >&2
      exit 1
    fi
    inferred_image_name="true"
  fi
  if [[ -z "${tag}" ]]; then
    tag="$(generate_default_tag)"
    generated_tag="true"
  fi
  validate_image_name "${image_name}"
  validate_image_tag "${tag}"
  target_ref="$(build_image_ref "${image_name}" "${tag}")"
fi

if [[ ! -f "${dockerfile}" ]]; then
  echo "Dockerfile 不存在: ${dockerfile}" >&2
  exit 1
fi

if [[ ! -d "${context}" ]]; then
  echo "构建上下文必须是目录: ${context}" >&2
  exit 1
fi

ensure_dockerignore "${context}"

echo "开始构建并推送: ${target_ref}"
if [[ "${inferred_image_name}" == "true" ]]; then
  echo "自动推断镜像名: ${image_name}"
fi
if [[ "${generated_tag}" == "true" ]]; then
  echo "自动生成版本标签: ${tag}"
fi

if is_local_docker_available; then
  if ! docker buildx version >/dev/null 2>&1; then
    echo "当前环境不可用 docker buildx，请先安装或启用 buildx" >&2
    exit 1
  fi

  ensure_docker_login

  build_args=(
    docker buildx build
    --platform "${PLATFORM}"
    --file "${dockerfile}"
    --tag "${target_ref}"
  )
  build_args+=(--push "${context}")

  "${build_args[@]}"
else
  remote_build_and_push "${target_ref}" "${dockerfile}" "${context}"
fi

print_final_delivery_block "${target_ref}" "${dockerfile}"
