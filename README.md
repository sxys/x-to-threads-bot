# X (Twitter) 热门推文抓取 ➔ Telegram 推送 ➔ Threads 24小时自动同步系统

一个工业级、高可靠的自动化社交媒体矩阵管理系统：每天自动从 **X (Twitter)** 登录主页抓取最高热度的 **Top 20 原生中文推文**，排版后推送到 **Telegram 频道/个人**，并自动下载文字、高清图片、全分辨率 MP4 视频，全天 **24 小时按约 70 分钟均匀间隔** 自动发布到您的 **Threads** 账号。

---

## 🌟 核心特性

1. **X (Twitter) 热门推文精准抓取**：
   - 登录主页推荐流（`For You`）或关注流（`Following`），多轮滚动翻页聚合候选池。
   - 综合点赞、转推、评论、引用加权评分，截取最热门的 Top 20。
   - **严格语言清洗**：内置双重正规中文检测，坚决剔除西班牙语、葡萄牙语等被机翻成中文的推文。
   - **广告与营销过滤**：自动拦截跳过官方 Promoted 广告推文与营销赞助内容。
2. **Telegram 优美卡片推送**：
   - 自动生成结构清晰的 HTML 消息卡片，包含发布者、文案摘要、热度指标（赞/转/评）及原推直达链接。
   - 智能分包推送，避免单条消息过长触发 Telegram API 限制；静默异常处理，抓取失败时不骚扰群聊。
3. **Threads 24 小时全天候自动平摊发布**：
   - 自动解析推文多媒体，下载完整 MP4 高清视频流与原图到本地缓存。
   - **全天 24 小时平摊排期**：20 条推文在 24 小时内（直到次日早晨 08:00）均匀分布，平均每隔约 **70~72 分钟** 发布一条，附带 $\pm 3$ 分钟随机抖动，完全模拟真人节奏。
   - **发帖防抖冷却保护（Cool-down Guard）**：两次发帖之间强制保证至少 40 分钟安全间隔，杜绝平台风控。
   - **断线自适应平摊（Smart Reschedule）**：无论是中途启动还是服务器维护，启动时自动根据剩余时间重新均匀排布，绝不堆积连发。
4. **轻量低消耗**：
   - 深度优化 Linux VPS 资源，配备专用内存防崩参数与 Swap 自动构建，即便在 **1核 / 1GB RAM** 的低配云服务器上也能极稳运行。

---

## 📁 项目结构

```text
x-to-threads-bot/
├── x_top_bot.py           # 【程序一】X 抓取与 TG 每日推送入口
├── x_home_collector.py    # X 信息流采集器（热度计算、中文过滤与广告拦截）
├── twikit_patch.py        # X Webpack chunk 适配、容错补丁与索引兜底
├── tg_sender.py           # Telegram 消息卡片排版与分发模块
├── queue_manager.py       # 媒体资源下载器、24小时排期生成与队列管理器
├── threads_poster.py      # 【程序二】Threads 自动化发帖核心（基于 Playwright）
├── setup_vps.sh           # Ubuntu 24.04 VPS 一键极速部署脚本
├── requirements.txt       # Python 依赖清单
├── .env.example           # 环境变量与账号凭据模板
├── .gitignore             # 严格的敏感信息过滤规则（防止 Token 泄露）
└── README.md              # 项目完整使用手册
```

---

## 🛠️ 常用命令速查手册（运维必看 📋）

在服务器或本地终端中（进入项目目录并执行 `source .venv/bin/activate` 后）：

### 1. 查看队列与发帖进度
| 操作 | 命令 | 说明 |
| :--- | :--- | :--- |
| **查看当前全部队列** | `python threads_poster.py --status` | 查看当天 20 条推文的时间表、状态（`[已发]` / `[待发]`）及文案预览 |
| **仅查看未发布的帖子** | `python threads_poster.py --status \| grep 待发` | 快速过滤出接下来尚未发布的推文列表 |
| **查看已发布的帖子** | `python threads_poster.py --status \| grep 已发` | 快速过滤已成功发布的推文 |

### 2. 即时测试与调试命令
| 操作 | 命令 | 说明 |
| :--- | :--- | :--- |
| **立即抓取推文并推送到 TG** | `python x_top_bot.py --run-now` | 手动触发单次完整抓取、热度筛选、TG 推送并生成 Threads 待发队列 |
| **测试 Telegram 连通性** | `python x_top_bot.py --test-tg` | 仅测试 TG Bot Token 和 Chat ID 是否有效 |
| **测试 Threads 直接发单条** | `python threads_poster.py --test "测试文本"` | 无需依赖队列，直接向 Threads 发布一条自定义文本，快速验证 Cookie 与浏览器环境 |
| **测试发布队列下一条** | `python threads_poster.py --post-now` | 立即提取队列中的下一个待发任务（含图文/视频）发布至 Threads |
| **手动平摊重排队列** | `python threads_poster.py --reschedule` | 将当前所有剩余待发任务按未来 24 小时均匀重新排布并启动监听 |

### 3. 定时抓取任务管理（Crontab，每天早上 08:00）
| 操作 | 命令 | 说明 |
| :--- | :--- | :--- |
| **编辑定时任务配置** | `crontab -e` | 打开系统定时任务编辑器 |
| **查看已安装的定时任务** | `crontab -l` | 查看当前正在生效的 cron 清单 |
| **查看抓取执行历史日志** | `cat x_bot_daily.log` 或 `tail -n 50 x_bot_daily.log` | 查看每天早上 08:00 任务的抓取与推送日志 |

### 4. Threads 常驻后台服务管理（Systemd）
| 操作 | 命令 | 说明 |
| :--- | :--- | :--- |
| **查看发帖服务实时状态** | `sudo systemctl status threads-poster` | 查看服务是否处于 active (running) |
| **实时滚动查看发帖日志** | `journalctl -u threads-poster -f` | 实时查看浏览器启动、媒体上传、Post 点击与发布完成日志 |
| **重启发布服务** | `sudo systemctl restart threads-poster` | 更新代码或 Cookie 后重启生效 |
| **停止发布服务** | `sudo systemctl stop threads-poster` | 暂停后台自动发帖 |
| **启动发布服务** | `sudo systemctl start threads-poster` | 启动后台自动发帖 |

---

## 🚀 生产环境（Ubuntu 24.04 VPS）从零部署指南

### 第一步：克隆代码与一键环境配置
```bash
# 1. 克隆代码仓库
git clone https://github.com/sxys/x-to-threads-bot.git
cd x-to-threads-bot

# 2. 运行一键配置脚本 (自动创建 4GB Swap、安装 Python 虚拟环境与 Playwright Chromium)
chmod +x setup_vps.sh
./setup_vps.sh
```

### 第二步：配置账号与凭据
1. **配置环境变量**：
   ```bash
   cp .env.example .env
   nano .env
   ```
   填入你的凭据：
   - `TG_BOT_TOKEN`: Telegram 机器人 Token
   - `TG_CHAT_ID`: Telegram 接收人 Chat ID
   - `X_AUTH_TOKEN` & `X_CT0`: 从浏览器获取的 X Cookie
   - `PROXY=`: **海外 VPS 请保持等号右侧留空！**（无需代理）
   *(按 `Ctrl + O` 回车保存，`Ctrl + X` 退出)*

2. **配置 Threads Cookies**：
   在项目根目录下创建 `threads_cookies.json`：
   ```bash
   nano threads_cookies.json
   ```
   将导出的 Threads JSON 格式 Cookies 完整粘贴进去并保存。

### 第三步：校准服务器时区为北京时间（重要 ⚠️）
```bash
sudo timedatectl set-timezone Asia/Shanghai
date  # 确认显示 CST 且时间与北京时间完全一致
```

### 第四步：设置每天早上 08:00 自动抓取定时任务
```bash
crontab -e
```
在文件末尾添加以下一行：
```cron
0 8 * * * /var/www/x-to-threads-bot/.venv/bin/python /var/www/x-to-threads-bot/x_top_bot.py --run-now >> /var/www/x-to-threads-bot/x_bot_daily.log 2>&1
```

### 第五步：注册 Threads 24小时后台自动发帖系统服务
一键创建 Systemd 服务并开启开机自启：
```bash
# 1. 写入服务配置文件
sudo bash -c 'cat > /etc/systemd/system/threads-poster.service << "EOF"
[Unit]
Description=Threads Auto Poster 24H Worker Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/var/www/x-to-threads-bot
ExecStart=/var/www/x-to-threads-bot/.venv/bin/python threads_poster.py --reschedule
Restart=always
RestartSec=15
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF'

# 2. 重载并启动服务
sudo systemctl daemon-reload
sudo systemctl enable --now threads-poster

# 3. 检查服务运行状态
sudo systemctl status threads-poster
```

---

## 💻 本地 Windows 运行指南

1. **环境准备**：
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   playwright install chromium
   ```
2. **复制配置**：
   复制 `.env.example` 为 `.env`，并在 `PROXY=` 中填写本地科学上网代理端口（如 `http://127.0.0.1:7890`）。
3. **注册 Windows 每日定时任务**（每天早上 08:00 自动唤起抓取）：
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\register_daily_task.ps1
   ```
4. **启动 Threads 自动发布**：
   双击运行根目录下的 `start_threads_worker.bat`，或执行：
   ```powershell
   python threads_poster.py
   ```

---

## ❓ 常见问题排查 (FAQ)

- **Q1: 为什么抓取推文时提示 `ConnectTimeout`？**
  - 海外 VPS 直连 X 时不需要代理，请确保 `.env` 中的 `PROXY=` 等号右边完全留空。
- **Q2: 为什么 1GB 内存服务器不会崩溃？**
  - 系统已配置 4GB Swap 虚拟内存，且 Playwright 启动时携带了 `--disable-dev-shm-usage` 和 `--disable-gpu` 内存保护参数，发帖后会自动释放 Chromium 进程。
- **Q3: 遇到服务器维护关机，重启后推文会连续狂发吗？**
  - 绝对不会！程序内置了启动自检平摊算法与发帖冷却防抖机制（`Cool-down Guard`），重启后会自动根据剩余时间重新将未发推文均匀排布，且两次发帖强制间隔 40 分钟以上。
