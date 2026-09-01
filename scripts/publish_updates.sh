#!/usr/bin/env bash
# 抓取成功后，仅提交 data/ 与 site/，并推送到 GitHub 默认分支。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${MODEL_PRICE_REPO_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
PUBLISH_BRANCH="${MODEL_PRICE_PUBLISH_BRANCH:-main}"
PUBLISH_REMOTE="${MODEL_PRICE_PUBLISH_REMOTE:-origin}"
PYTHON_BIN="${MODEL_PRICE_PYTHON:-$REPO_DIR/.venv/bin/python}"

cd "$REPO_DIR"
git rev-parse --is-inside-work-tree >/dev/null

if [[ -n "${MODEL_PRICE_SSH_KEY:-}" ]]; then
    KNOWN_HOSTS="${MODEL_PRICE_KNOWN_HOSTS:-$REPO_DIR/.deploy/known_hosts}"
    export GIT_SSH_COMMAND="ssh -i $MODEL_PRICE_SSH_KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=$KNOWN_HOSTS"
fi

echo "运行发布前测试..."
"$PYTHON_BIN" -m unittest discover -s tests

# 路径白名单：即使工作区内有 .env、缓存或人工修改，也不会被提交。
git add -A -- data site
if git diff --cached --quiet -- data site; then
    echo "没有 data/site 变化，跳过 GitHub 提交"
else
    timestamp="$(date -u +%Y-%m-%dT%H:%MZ)"
    git -c user.name="model-price-bot" \
        -c user.email="model-price-bot@users.noreply.github.com" \
        commit --only -m "data: hourly update $timestamp" -- data site
fi

# 即使本轮没有新文件变化，也重试推送先前已提交但尚未送达的更新。
git push "$PUBLISH_REMOTE" "HEAD:refs/heads/$PUBLISH_BRANCH"
echo "已推送最新 data/site 到 $PUBLISH_REMOTE/$PUBLISH_BRANCH"
