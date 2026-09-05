#!/usr/bin/env bash
# 一键自部署: 克隆仓库、装依赖、配置 cron、生成 Caddy 配置示例。
# 生产环境使用 /opt/model-price + systemd timer；本脚本保留为普通用户自部署入口。
set -euo pipefail

REPO="https://github.com/haverainlilili/model--price.git"
DEPLOY_DIR="${MODEL_PRICE_DEPLOY_DIR:-$HOME/model-price}"
DOMAIN="${MODEL_PRICE_DOMAIN:-model-price.minggemini3test1.online}"
VENV="$DEPLOY_DIR/.venv"

echo "=== 大模型价格看板 - 服务器部署 ==="
echo "目标目录: $DEPLOY_DIR"
echo

# 1. 克隆仓库（若已存在则拉取最新）
if [ -d "$DEPLOY_DIR" ]; then
    echo "目录已存在，拉取最新代码..."
    cd "$DEPLOY_DIR"
    git pull --rebase
else
    echo "克隆仓库..."
    git clone "$REPO" "$DEPLOY_DIR"
    cd "$DEPLOY_DIR"
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
    cp "$DEPLOY_DIR/.env.example" "$DEPLOY_DIR/.env"
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

# 7. 配置 cron 每小时运行
CRON_CMD="0 * * * * cd $DEPLOY_DIR && $VENV/bin/python -m scraper >> $DEPLOY_DIR/cron.log 2>&1"
CRON_COMMENT="# 大模型价格看板 - 每小时更新"

if crontab -l 2>/dev/null | grep -qF "$DEPLOY_DIR"; then
    echo
    echo "✓ cron 任务已存在"
else
    echo
    echo "添加 cron 任务（每小时整点运行）..."
    (crontab -l 2>/dev/null || true; echo "$CRON_COMMENT"; echo "$CRON_CMD") | crontab -
    echo "✓ cron 已配置，日志: $DEPLOY_DIR/cron.log"
fi

# 8. 生成 Caddy 配置示例（生产环境使用 Caddy，不再使用旧 nginx/IP 配置）
CADDY_CONF="$DEPLOY_DIR/Caddyfile.example"
cat > "$CADDY_CONF" <<EOF
# 将此站点块合并到 /etc/caddy/Caddyfile，然后执行：
# sudo caddy validate --config /etc/caddy/Caddyfile && sudo systemctl reload caddy

$DOMAIN {
    root * $DEPLOY_DIR/site
    encode zstd gzip
    header Cache-Control "public, max-age=300"
    file_server
}
EOF

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
