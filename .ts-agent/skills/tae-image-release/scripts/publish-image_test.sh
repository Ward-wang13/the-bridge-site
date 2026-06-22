#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PUBLISH_SCRIPT="${SCRIPT_DIR}/publish-image.sh"

assert_contains() {
  local haystack="$1"
  local needle="$2"
  if [[ "${haystack}" != *"${needle}"* ]]; then
    echo "expected output to contain: ${needle}" >&2
    echo "actual output:" >&2
    echo "${haystack}" >&2
    exit 1
  fi
}

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

output="$(
  bash "${PUBLISH_SCRIPT}" \
    --image-name demo \
    --tag bad/tag \
    --dockerfile "${tmp_dir}/Dockerfile.missing" \
    --context "${tmp_dir}" \
    2>&1 || true
)"
assert_contains "${output}" "镜像 tag 格式不合法"

output="$(
  bash "${PUBLISH_SCRIPT}" \
    --image-name demo \
    --tag v1.2.3 \
    --dockerfile "${tmp_dir}/Dockerfile.missing" \
    --context "${tmp_dir}" \
    2>&1 || true
)"
assert_contains "${output}" "Dockerfile 不存在"

output="$(
  bash "${PUBLISH_SCRIPT}" \
    --image-ref registry.pixcakeai.com/tae/demo:release-1 \
    --dockerfile "${tmp_dir}/Dockerfile.missing" \
    --context "${tmp_dir}" \
    2>&1 || true
)"
assert_contains "${output}" "Dockerfile 不存在"

output="$(
  bash "${PUBLISH_SCRIPT}" \
    --image-name demo \
    --tag 20260520123456 \
    --push-latest \
    --dockerfile "${tmp_dir}/Dockerfile.missing" \
    --context "${tmp_dir}" \
    2>&1 || true
)"
assert_contains "${output}" "默认不额外推送 latest"

echo "publish-image tag validation tests passed"
