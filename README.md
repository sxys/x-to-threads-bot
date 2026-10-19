# X (Twitter) 热门推文抓取 ➔ Telegram 推送 ➔ Threads 24小时自动同步系统

一个工业级、高可靠的自动化社交媒体矩阵管理系统：每 4 小时自动从 **X (Twitter)** 登录主页抓取最高热度的 **8 条原生中文热门推文**（每天 6 次共 48 条），排版后推送到 **Telegram 频道/个人**，并自动下载文字、高清图片、全分辨率 MP4 视频，全天 **24 小时按约 30 分钟（半小时）均匀间隔** 自动发布到您的 **Threads** 账号。

---

## 🌟 核心特性

1. **X (Twitter) 热门推文精准抓取**：
   - **4 小时轮巡机制**：每天 00:00、04:00、08:00、12:00、16:00、20:00 准时巡检抓取，每次抓取热度前 8 条，一天 6 次共 48 条。
   - 登录主页推荐流（`For You`）或关注流（`Following`），多轮滚动翻页聚合候选池。
   - 综合点赞、转推、评论、引用加权评分，严格挑选高热度内容。
   - **严格语言清洗**：内置双重正规中文检测，坚决剔除西班牙语、葡萄牙语等被机翻成中文的推文。
   - **广告与营销过滤**：自动拦截跳过官方 Promoted 广告推文与营销赞助内容。
   - **智能过滤个人生活日记与私密琐事（NEW 🔥）**：自动识别并跳过博主个人第一人称日常生活日记、家庭琐事、就医相亲、感情纠葛流水账（如“带我妈看演唱会”、“今天我妈生日”、“和我老公吵架”等强个人私密感的推文），确保推送到 Threads 的都是具有公共讨论价值、幽默段子或深度社会观察的热门内容！
   - **支持自定义排除词库**：可在 `.env` 中通过 `EXCLUDE_KEYWORDS` 随意追加需要排除的词汇。
2. **Telegram 优美卡片推送 & 交互式专属插队**：
   - 自动生成结构清晰的 HTML 消息卡片，包含发布者、文案摘要、热度指标（赞/转/评）及原推直达链接。
   - 智能分包推送，避免单条消息过长触发 Telegram API 限制；静默异常处理，抓取失败时不骚扰群聊。
   - **手机 Telegram 直接插队**：随时把推文链接（`https://x.com/...`）转发给机器人，后台自动抓取高清图文与 MP4 视频，并作为**最高优先级任务插队发布**。
   - **智能防风控平摊**：插队推文将在满足 20 分钟安全冷却保护后最先发布，当天其余未发任务自适应顺延，总帖数自动累加（发完为止）。
   - **快捷指令**：随时在 TG 发送 `/status` 查看待发计划表、`/clean` 清理已发缓存、`/clear` 一键清空未发队列，或发送 `/help` 查看说明。
3. **Threads 24 小时全天候自动平摊发布**：
   - 自动解析推文多媒体，下载完整 MP4 高清视频流与原图到本地缓存。
   - **全天 24 小时平摊排期**：每 4 小时抓取 8 条热门，在 24 小时内均匀发布共 48 条，平均每隔约 **30 分钟**（半小时）发布一条，附带 $\pm 3$ 分钟随机抖动，完全模拟真人节奏。
   - **发帖防抖冷却保护（Cool-down Guard）**：两次发帖之间强制保证至少 20 分钟安全间隔，杜绝平台风控。
4. **Threads 个人主页评论巡查与 Gemini 智能拟人互动（NEW 🔥）**：
   - **只要有评论就回复（动态配额）**：自动巡视主页发布的最新 12 篇帖子，只要有粉丝评论就参与互动；若评论数 $\le 10$ 条，则回复 1 位粉丝；若评论数 $> 10$ 条，则回复 2 位粉丝。
   - **接入 Gemini 智能互动内核**：优先采用高配额、低延迟的 `gemini-2.5-flash-lite` 模型，结合主帖正文与粉丝留言上下文，生成 10~30 字以内切中要害、自然风趣的拟人回复（坚决杜绝“哈哈”口癖与机械套话）。
   - **高可用级联故障转移（Failover）**：遇 API 配额限制（HTTP 429）或网络抖动时，自动级联尝试备选模型；若全部异常则无缝降级为精选预设话术库，确保服务永不中断。
   - **拟人防风控保护**：回复之间强制执行 35~75 秒拟人随机间隔，每篇帖子最多仅回复 1~2 位真实粉丝，回复内容为纯净正文（控制台日志中的 `[Gemini AI]` 仅为内部来源标记，绝不外发）。
   - **永久去重记忆库**：内置持久化记录库（`replied_comments.json`），绝不重复回复同位粉丝或回复自己。
   - **纯静默运行**：巡查与互动均在后台静默记录日志，不发送冗余消息打扰 Telegram。
5. **磁盘空间极致瘦身（即发即清，专为小硬盘 VPS 优化）**：
   - **即发即清（Real-time Cleanup）**：帖子一旦成功发布至 Threads，系统立即自动删除本地保存的 MP4 视频与高清图片附件，不浪费一兆磁盘。
   - **后台 6 小时自动巡检**：常驻服务每 6 小时自动全盘扫描 `media_cache`，清理孤立遗留文件。
   - **支持 Telegram / CLI 一键清空**：发送 `/clear` 或执行 `--clear-queue` 即可清空未发任务并回收磁盘空间。
   - **支持 Telegram 一键清理**：手机发送 `/clean` 即可随时主动触发已发媒体清理并查看释放的 MB 数。
6. **轻量低消耗**：
   - 深度优化 Linux VPS 资源，配备专用内存防崩参数与 Swap 自动构建，即便在 **1核 / 1GB RAM / 10GB 硬盘** 的超低配云服务器上也能长期极稳运行。

---

## 📁 项目结构

```text
x-to-threads-bot/
├── x_top_bot.py           # 【程序一】X 抓取与 TG 每日推送入口
├── x_home_collector.py    # X 信息流采集器（热度计算、中文过滤与广告拦截）
├── twikit_patch.py        # X Webpack chunk 适配、容错补丁与索引兜底
├── tg_sender.py           # Telegram 消息卡片排版与分发模块
├── tg_listener.py         # Telegram 交互监听器（支持推文链接插队与 /status /clean）
├── queue_manager.py       # 媒体资源下载器、24小时排期生成与队列管理器
├── threads_poster.py      # 【程序二】Threads 自动化发帖核心（基于 Playwright）
├── threads_auto_reply.py  # 【程序三】Threads 主页评论自动巡查与粉丝互动机器人
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
| **查看当前全部队列** | `python threads_poster.py --status` | 查看当前待发队列的时间表（每隔约 30 分钟）、状态（`[已发]` / `[待发]`）及文案预览 |
| **单独删除指定待发任务** | `python threads_poster.py --delete <序号>` | 单独取消指定序号（如 `--delete 16`）的待发推文，自动清理媒体并向前补位顺延 |
| **仅查看未发布的帖子** | `python threads_poster.py --status \| grep 待发` | 快速过滤出接下来尚未发布的推文列表 |
| **查看已发布的帖子** | `python threads_poster.py --status \| grep 已发` | 快速过滤已成功发布的推文 |

### 2. 即时测试与调试命令
| 操作 | 命令 | 说明 |
| :--- | :--- | :--- |
| **立即抓取推文并推送到 TG** | `python x_top_bot.py --run-now` | 手动触发单次完整抓取、热度筛选、TG 推送并无缝排入 Threads 待发队列 |
| **一键清空待发队列与媒体** | `python threads_poster.py --clear-queue` | 一键清空所有尚未发布的任务，并彻底删除对应的图片与视频缓存，释放 VPS 磁盘 |
| **立即清理本地媒体缓存** | `python threads_poster.py --clean-media` | 立即删除所有已发布任务及历史孤立的图片与视频文件，释放 VPS 磁盘 |
| **手动插队一条 X 推文** | `python threads_poster.py --add-tweet <URL>` | 本地命令行插队：抓取指定链接并排在最前优先发布 |
| **测试 Telegram 连通性** | `python x_top_bot.py --test-tg` | 仅测试 TG Bot Token 和 Chat ID 是否有效 |
| **测试 Threads 直接发单条** | `python threads_poster.py --test "测试文本"` | 无需依赖队列，直接向 Threads 发布一条自定义文本，快速验证 Cookie 与浏览器环境 |
| **测试发布队列下一条** | `python threads_poster.py --post-now` | 立即提取队列中的下一个待发任务（含图文/视频）发布至 Threads 并自动清理缓存 |
| **手动平摊重排队列** | `python threads_poster.py --reschedule` | 将当前所有剩余待发任务按每隔 30 分钟均匀重新排布并启动监听 |

### 3. 主页评论自动巡查与 Gemini 智能互动（threads_auto_reply.py）
| 操作 | 命令 | 说明 |
| :--- | :--- | :--- |
| **立即单次巡查并回复** | `python threads_auto_reply.py --run-now` | 立即巡查主页最新 12 条帖子（覆盖过去 6 小时内容），优先调用 Gemini AI 针对原帖和粉丝评论生成命中要害的回复（10~30字），若 API 受限则自动降级为预设话术 |
| **仅模拟巡查（不真实发送）** | `python threads_auto_reply.py --dry-run` | 仅抓取和分析主页帖子评论区，并在控制台展示选中的粉丝和 AI / 预设回复话术 |
| **自定义评论分档阈值** | `python threads_auto_reply.py --threshold 15` | 设置评论分档阈值：$\le$ 阈值回复 1 个，$>$ 阈值回复 2 个（默认 10） |
| **自定义巡查帖子深度** | `python threads_auto_reply.py --limit 16` | 自定义单次扫描的主页帖子篇数（默认 12 篇） |
| **常驻守护后台巡查** | `python threads_auto_reply.py --daemon --interval-hours 2` | 后台常驻，每 2 小时自动巡查一次（默认 2 小时） |

> 🛡️ **防风控与拟人保护**：回复之间内置 35~75 秒拟人随机长间隔，配合真实键盘输入与停顿，且单帖仅互动 1~2 人，彻底杜绝高频触发平台风控。

### 4. Telegram 机器人互动指令（无需登录服务器）
直接在 Telegram 聊天窗口中向机器人发送：
- **分享推文链接**（如 `https://x.com/username/status/12345678`）：机器人自动下载媒体并以最高优先级插队发布至 Threads，后续任务自动顺延！
- `/status`（或 `/list`、`队列`）：查看排期进度与近期待发推文列表及 `#序号`。
- `/del <序号>` 或 `删除 <序号>`（例如 `/del 16` 或 `删除 16`）：单独删除指定序号的待发推文，自动清理本地图片视频并向前平移补位，保持 30 分钟发帖节奏不变。
- `/clean`：一键清理所有已发推文占用的图片与视频，并回复释放的磁盘 MB 空间。
- `/clear`（或 `/reset`）：一键清空当前所有未发任务并删除其媒体缓存，重置待发队列。
- `/help`：获取帮助与功能说明。

### 5. 定时任务管理（Crontab）
| 操作 | 命令 | 说明 |
| :--- | :--- | :--- |
| **编辑定时任务配置** | `crontab -e` | 打开系统定时任务编辑器 |
| **查看已安装的定时任务** | `crontab -l` | 查看当前正在生效的 cron 清单 |
| **查看抓取执行历史日志** | `cat x_bot_daily.log` 或 `tail -n 50 x_bot_daily.log` | 查看每 4 小时任务的抓取与推送日志 |
| **(可选) 凌晨定时深度清理媒体** | `0 3 * * * ... --clean-media` | 每天凌晨 03:00 自动清理一次媒体缓存（详见后文） |
| **(推荐) 评论自动巡查 Crontab** | `0 */2 * * * ... threads_auto_reply.py --run-now` | 每 2 小时自动巡查一次主页最新 12 篇帖子并互动 |

### 6. Threads 常驻后台服务管理（Systemd）
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
   - `GEMINI_API_KEY`: Google Gemini API Key（在 [Google AI Studio](https://aistudio.google.com/) 免费创建，用于自动回复粉丝）
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

### 第四步：设置每 4 小时自动抓取定时任务
```bash
crontab -e
```
在文件末尾添加以下一行（每 4 小时整点自动抓取 8 条热门，一天 6 次共 48 条，并在 24 小时内每隔 30 分钟均匀发布）：
```cron
0 */4 * * * cd /var/www/x-to-threads-bot && /var/www/x-to-threads-bot/.venv/bin/python x_top_bot.py --run-now >> /var/www/x-to-threads-bot/x_bot_daily.log 2>&1
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
  - 绝对不会！程序内置了启动自检平摊算法与发帖冷却防抖机制（`Cool-down Guard`），重启后会自动按每隔 30 分钟重新将未发推文均匀排布，且两次发帖强制间隔 20 分钟以上。
- **Q4: 如果我想切换规则或重新开始，旧的未发任务怎么处理？**
  - 两种方式皆可：
    - **一键清空重置**：在终端执行 `python threads_poster.py --clear-queue` 或在手机 Telegram 发送 `/clear`，即可一键清空待发队列并彻底删除对应的图片与视频缓存；
    - **顺延发完**：不执行清空，直接重启后台服务，系统会自动按每隔 30 分钟逐条顺延发完，下一批 4 小时抓取的新推文会自动无缝排在它们之后。
- **Q5: 每天 48 条推文是如何分布的？**
  - Crontab 设置为 `0 */4 * * *`（每 4 小时一次，每天 6 次），每次抓取热度前 8 条（$6 \times 8 = 48$ 条）。发帖服务在 24 小时内按每隔约 30 分钟（附带 $\pm 3$ 分钟拟人随机抖动）平摊发出，各批次之间无缝流转。
- **Q6: 评论自动回复调用的 Gemini API 会产生费用或触发配额限制吗？**
  - 完全免费！系统默认且优先调用 Google AI Studio 免费层配额极充裕的 `gemini-2.5-flash-lite` 模型（每天提供上千次免费调用），日常高频互动无需任何花费。
  - 同时内置了 HTTP 429 级联故障转移（Failover）与本地精选话术库兜底机制：即使单模型遇到临时受限，程序会自动尝试备选模型或平滑降级为本地预设，保障服务永不中断，绝对零扣费风险。
- **Q7: 真实发送到 Threads 时，回复内容会带 `[Gemini AI]` 这种标识吗？**
  - 绝对不会！控制台与日志中显示的 `[Gemini AI]` 或 `[预设精选]` 仅为方便管理员监控调试的内部来源标记；真正向 Threads 粉丝评论框输入的只有纯净的拟人回复正文。
