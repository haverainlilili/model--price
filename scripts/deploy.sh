#!/usr/bin/env bash
# 一键自部署: 克隆仓库、装依赖、配置 cron、生成 Caddy 配置示例。
# 生产环境使用 /opt/model-price + systemd timer；本脚本保留为普通用户自部署入口。
set -euo pipefail

REPO="https://github.com/haverainlilili/model--price.git"
DEPLOY_DIR="${MODEL_PRICE_DEPLOY_DIR:-$HOME/model-price}"
if [[ "$DEPLOY_DIR" != /* ]]; then
    echo "✗ MODEL_PRICE_DEPLOY_DIR 必须是绝对路径: $DEPLOY_DIR" >&2
    exit 2
fi
DOMAIN="${MODEL_PRICE_DOMAIN:-model-price.minggemini3test1.online}"
VENV="$DEPLOY_DIR/.venv"

echo "=== 大模型价格看板 - 服务器部署 ==="
echo "目标目录: $DEPLOY_DIR"
echo

LOCKS_HELD=0
acquire_deploy_locks() {
    [ "$LOCKS_HELD" -eq 1 ] && return
    if ! command -v flock &>/dev/null; then
        echo "安装部署单实例锁工具..."
        sudo apt-get update && sudo apt-get install -y util-linux
    fi
    mkdir -p "$DEPLOY_DIR/data"
    exec 9>"$DEPLOY_DIR/.git/model-price-cron.lock"
    if ! flock -n 9; then
        echo "✗ 每小时任务正在运行，稍后重试部署"
        exit 75
    fi
    exec 8>"$DEPLOY_DIR/data/.scraper.lock"
    if ! flock -n 8; then
        echo "✗ 抓取/构建任务正在运行，稍后重试部署"
        exit 75
    fi
    export MODEL_PRICE_LOCK_HELD=1
    LOCKS_HELD=1
}

# 1. 克隆仓库（若已存在则拉取最新）
if [ -d "$DEPLOY_DIR/.git" ]; then
    acquire_deploy_locks
    echo "目录已存在，先快照运行时 data、丢弃派生 site，再拉取最新代码..."
    cd "$DEPLOY_DIR"
    GENERATED_SNAPSHOT="$DEPLOY_DIR/.git/model-price-runtime-backup"
    SNAPSHOT_ACTIVE=0
    restore_runtime_data() {
        if [ "$SNAPSHOT_ACTIVE" -eq 1 ] && [ -f "$GENERATED_SNAPSHOT/.complete" ]; then
            mkdir -p data
            cp -a "$GENERATED_SNAPSHOT/data/." data/
            rm -rf "$GENERATED_SNAPSHOT"
            SNAPSHOT_ACTIVE=0
        fi
    }
    # 未完成的 staging 从未用于恢复；只有带完成标记且原子发布的备份可信。
    for stale_snapshot in "$GENERATED_SNAPSHOT".staging.*; do
        [ -e "$stale_snapshot" ] && rm -rf "$stale_snapshot"
    done
    if [ -d "$GENERATED_SNAPSHOT" ]; then
        if [ -f "$GENERATED_SNAPSHOT/.complete" ]; then
            echo "恢复上次未完成部署保留的运行时 data..."
            SNAPSHOT_ACTIVE=1
            restore_runtime_data
        else
            echo "丢弃上次未完整发布的运行时备份..."
            rm -rf "$GENERATED_SNAPSHOT"
        fi
    fi
    if [ -n "$(git status --porcelain --untracked-files=normal -- data site)" ]; then
        SNAPSHOT_STAGE="$GENERATED_SNAPSHOT.staging.$$"
        mkdir -p "$SNAPSHOT_STAGE/data"
        if [ -d data ]; then
            cp -a data/. "$SNAPSHOT_STAGE/data/"
            rm -f "$SNAPSHOT_STAGE/data/.scraper.lock"
        fi
        touch "$SNAPSHOT_STAGE/.complete"
        mv "$SNAPSHOT_STAGE" "$GENERATED_SNAPSHOT"
        SNAPSHOT_ACTIVE=1
        trap 'restore_runtime_data' EXIT
        trap 'exit 130' INT
        trap 'exit 143' TERM
        trap 'exit 129' HUP
        # site 完全由 data + 代码生成，不参与三方合并；data 稍后按文件覆盖回来，
        # 因而远端新增的种子文件仍保留，本机已抓取的同名事实优先。
        git restore --source=HEAD --staged --worktree -- data site
        git clean -fd -e data/.scraper.lock -- data site
    fi
    if ! git diff --quiet; then
        echo "✗ data/site 之外存在未提交的跟踪文件修改；拒绝覆盖，请先提交或还原"
        restore_runtime_data
        trap - EXIT INT TERM HUP
        exit 1
    fi
    if ! git pull --rebase; then
        restore_runtime_data
        trap - EXIT INT TERM HUP
        exit 1
    fi
    restore_runtime_data
    trap - EXIT INT TERM HUP

else
    echo "克隆仓库..."
    git clone "$REPO" "$DEPLOY_DIR"
    cd "$DEPLOY_DIR"
    acquire_deploy_locks
fi

# 2. 安装系统依赖
echo
echo "检查系统依赖..."
if ! command -v python3 &>/dev/null; then
    echo "安装 Python 3..."
    sudo apt-get update && sudo apt-get install -y python3 python3-pip python3-venv
fi

# 3. 创建虚拟环境并装依赖
echo
echo "配置 Python 虚拟环境..."
if [ ! -d "$VENV" ]; then
    python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r requirements.txt

# 4. 安装 Playwright 浏览器（首次或版本更新时）
echo
echo "安装 Playwright 浏览器..."
"$VENV/bin/playwright" install --with-deps chromium

# 5. 配置 .env（若不存在则从模板复制并提示填写）
if [ ! -f "$DEPLOY_DIR/.env" ]; then
    echo
    echo "⚠️  未检测到 .env 配置文件"
    install -m 600 "$DEPLOY_DIR/.env.example" "$DEPLOY_DIR/.env"
    echo "已从模板创建 .env，请编辑填入你的 API 密钥："
    echo
    echo "    nano $DEPLOY_DIR/.env"
    echo
    echo "必填项："
    echo "  OPENAI_API_KEY=sk-proj-...    # OpenAI API 密钥或中转 key"
    echo "  OPENAI_BASE_URL=              # 用中转填地址，官方 API 留空"
    echo "  OPENAI_MODEL=                 # 留空默认 gpt-5.6-sol"
    echo
    read -p "按回车继续配置定时任务（填好 .env 后再启用）..." _
fi
# API 密钥文件始终只允许部署用户读取；Caddy 仅获得 site/ ACL。
chmod 600 "$DEPLOY_DIR/.env"

# 6. 手动运行一次验证
echo
echo "测试运行一次（验证配置和依赖）..."
cd "$DEPLOY_DIR"
if "$VENV/bin/python" -m scraper --build-only; then
    echo "✓ 站点构建成功: $DEPLOY_DIR/site/index.html"
else
    echo "✗ 构建失败，请检查依赖和 .env 配置"
    exit 1
fi

# 7. 配置 cron 每小时运行。锁覆盖整轮抓取+构建，50 分钟硬超时，
# 避免慢页面让相邻小时任务并发写 JSON/site 文件。
if ! command -v flock &>/dev/null || ! command -v timeout &>/dev/null; then
    echo "安装 cron 单实例/超时工具..."
    sudo apt-get update && sudo apt-get install -y util-linux coreutils
fi
CRON_BEGIN="# BEGIN model-price hourly updater"
CRON_COMMENT="# 大模型价格看板 - 每小时更新（单实例，50 分钟超时）"
CRON_END="# END model-price hourly updater"
CRON_LOCK="$DEPLOY_DIR/.git/model-price-cron.lock"
CRON_CMD="0 * * * * cd \"$DEPLOY_DIR\" && /usr/bin/flock -n \"$CRON_LOCK\" /usr/bin/timeout --signal=TERM --kill-after=30s 50m \"$VENV/bin/python\" -m scraper >> \"$DEPLOY_DIR/cron.log\" 2>&1"

echo
echo "安装/升级 cron 任务（每小时整点运行）..."
CURRENT_CRON="$(crontab -l 2>/dev/null || true)"
# 只删除本脚本自己的标记块；兼容旧版“注释 + 下一行命令”和曾发布的锁路径，
# 绝不按 Python 解释器路径删除用户的其它维护任务。
CLEAN_CRON="$(printf '%s\n' "$CURRENT_CRON" | awk \
    -v begin="$CRON_BEGIN" -v end="$CRON_END" -v lock="$CRON_LOCK" '
    $0 == begin { in_block=1; next }
    in_block && $0 == end { in_block=0; next }
    in_block { next }
    /^# 大模型价格看板 - 每小时更新/ { legacy=1; next }
    legacy { legacy=0; if (index($0, "-m scraper")) next }
    index($0, lock) { next }
    { print }
')"
{
    printf '%s\n' "$CLEAN_CRON"
    printf '%s\n%s\n%s\n%s\n' "$CRON_BEGIN" "$CRON_COMMENT" "$CRON_CMD" "$CRON_END"
} | sed '/^[[:space:]]*$/N;/^\n$/D' | crontab -
echo "✓ cron 已配置：flock 单实例 + 50m timeout；日志: $DEPLOY_DIR/cron.log"

# 8. 生成 Caddy 配置示例（生产环境使用 Caddy，不再使用旧 nginx/IP 配置）
CADDY_CONF="$DEPLOY_DIR/Caddyfile.example"
cat > "$CADDY_CONF" <<EOF
# 将此站点块合并到 /etc/caddy/Caddyfile，然后执行：
# sudo caddy validate --config /etc/caddy/Caddyfile && sudo systemctl reload caddy

$DOMAIN {
    root * "$DEPLOY_DIR/site"
    encode zstd gzip
    header Cache-Control "public, max-age=300"
    file_server
}
EOF

if ! id caddy &>/dev/null; then
    echo "ℹ️  尚未安装 Caddy；安装后请重跑本部署脚本，以配置并复验最小读取 ACL。"
elif ! sudo -u caddy test -r "$DEPLOY_DIR/site/index.html"; then
    echo "配置 Caddy 最小读取 ACL（父链仅 traverse，site 可读；.env 仍为 0600）..."
    if ! command -v setfacl &>/dev/null; then
        sudo apt-get update && sudo apt-get install -y acl
    fi
    acl_path="$DEPLOY_DIR"
    while [ "$acl_path" != "/" ]; do
        sudo setfacl -m u:caddy:--x "$acl_path"
        next_acl_path="$(dirname "$acl_path")"
        if [ "$next_acl_path" = "$acl_path" ]; then
            echo "✗ 无法继续解析 Caddy ACL 父目录链: $acl_path" >&2
            exit 1
        fi
        acl_path="$next_acl_path"
    done
    sudo setfacl -R -m u:caddy:rX "$DEPLOY_DIR/site"
    if ! sudo -u caddy test -r "$DEPLOY_DIR/site/index.html"; then
        echo "✗ Caddy 仍无法读取 site/index.html；拒绝宣告部署成功"
        exit 1
    fi
fi

echo
echo "=== 部署完成 ==="
echo
echo "站点目录: $DEPLOY_DIR/site/"
echo "配置文件: $DEPLOY_DIR/.env"
echo "cron 日志: $DEPLOY_DIR/cron.log"
echo "Caddy 示例: $CADDY_CONF"
echo
echo "生产服务器当前配置: root@176.122.165.19:/opt/model-price (systemd timer + Caddy)"
echo "下一步："
echo "1. 编辑 .env 填入 OPENAI_API_KEY:"
echo "   nano $DEPLOY_DIR/.env"
echo
echo "2. 手动触发一次更新验证配置:"
echo "   cd $DEPLOY_DIR && $VENV/bin/python -m scraper"
echo
echo "3. （可选）配置 Caddy 对外提供服务:"
echo "   将 $CADDY_CONF 中的站点块合并到 /etc/caddy/Caddyfile"
echo "   sudo caddy validate --config /etc/caddy/Caddyfile && sudo systemctl reload caddy"
echo "   访问: https://$DOMAIN"
echo
