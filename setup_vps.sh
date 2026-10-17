#!/bin/bash
set -e

echo "=========================================================="
echo "      X & Threads 机器人 Ubuntu 24.04 VPS 一键部署脚本     "
echo "=========================================================="

# 1. 检查并创建 4GB Swap (对于 1GB 内存服务器至关重要)
SWAP_TOTAL=$(free -m | awk '/^Swap:/ {print $2}')
if [ "$SWAP_TOTAL" -lt 2000 ]; then
    echo "[1/5] 检测到系统 Swap 小于 2GB，正在自动创建 4GB Swap 虚拟内存..."
    if [ ! -f /swapfile ]; then
        sudo fallocate -l 4G /swapfile || sudo dd if=/dev/zero of=/swapfile bs=1M count=4096
        sudo chmod 600 /swapfile
        sudo mkswap /swapfile
        sudo swapon /swapfile
        if ! grep -q '/swapfile' /etc/fstab; then
            echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
        fi
        echo "✅ 4GB Swap 创建成功！"
    else
        sudo swapon /swapfile || true
    fi
else
    echo "[1/5] 系统 Swap 充裕 (${SWAP_TOTAL} MB)，跳过 Swap 创建。"
fi

# 2. 安装系统底层依赖
echo "[2/5] 正在安装 Python 及多媒体底层系统库..."
sudo apt update
sudo apt install -y python3 python3-pip python3-venv git curl ffmpeg

# 3. 创建 Python 独立虚拟环境
echo "[3/5] 正在初始化 Python 独立虚拟环境 (.venv)..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate

# 4. 安装 Python 项目依赖与 Playwright 浏览器
echo "[4/5] 正在安装 Python 依赖及 Playwright Chromium 内核..."
pip install --upgrade pip
pip install -r requirements.txt
playwright install chromium
playwright install-deps chromium

echo "=========================================================="
echo "✅ 环境部署完毕！"
echo "----------------------------------------------------------"
echo "接下来请完成以下两步配置："
echo "1. 复制配置文件并填入凭据："
echo "   cp .env.example .env"
echo "   nano .env  (填入你的 TG_BOT_TOKEN, TG_CHAT_ID, X_AUTH_TOKEN, X_CT0)"
echo ""
echo "2. 上传你的 threads_cookies.json 到当前目录。"
echo ""
echo "3. 运行测试："
echo "   source .venv/bin/activate"
echo "   python x_top_bot.py --run-now        # 测试抓取推文并推送到 TG"
echo "   python threads_poster.py --status    # 查看 Threads 待发队列"
echo "   python threads_poster.py --post-now  # 测试立即发布一条到 Threads"
echo "   python threads_poster.py             # 启动全天候自动平摊发布服务"
echo "=========================================================="
