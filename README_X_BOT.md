# X (Twitter) 主页热门帖子 Telegram 每日推送机器人

每天自动抓取 X 登录后的 **Home 信息流**（支持 `For You` 推荐流 或 `Following` 关注流），自动多轮翻页收集候选池，根据点赞、转推、评论、引用综合热度排序，截取 **Top 20** 帖子并推送到您的 Telegram。

---

## 📁 项目核心文件

- [x_config.py](file:///d:/abel/testGravity/x_config.py): 配置解析模块（支持 `.env` 读取与代码内默认配置）。
- [x_home_collector.py](file:///d:/abel/testGravity/x_home_collector.py): X 信息流抓取器（基于 `twikit`，支持 Cookie 会话缓存与翻页多批候选）。
- [tg_sender.py](file:///d:/abel/testGravity/tg_sender.py): Telegram 消息排版与推送器（支持卡片排版、防溢出拆包分发与代理）。
- [x_top_bot.py](file:///d:/abel/testGravity/x_top_bot.py): 主程序与 APScheduler 定时执行器。
- [.env.example](file:///d:/abel/testGravity/.env.example): 环境变量模板。

---

## 🚀 快速上手（推荐使用虚拟环境）

### 1. 创建并激活独立虚拟环境

在项目根目录下：

- **Windows PowerShell**:
  ```powershell
  # 1. 创建虚拟环境 (.venv)
  python -m venv .venv

  # 2. 激活虚拟环境
  .\.venv\Scripts\Activate.ps1
  ```
  *(注：若 PowerShell 提示脚本执行受限，可先运行 `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` 放行)*

- **Windows CMD**:
  ```cmd
  python -m venv .venv
  .\.venv\Scripts\activate.bat
  ```

- **Linux / macOS**:
  ```bash
  python3 -m venv .venv
  source .venv/bin/activate
  ```

### 2. 在虚拟环境中安装依赖
激活后（终端命令提示符前会出现 `(.venv)` 前缀）：
```bash
pip install -r requirements.txt
```

### 3. 配置环境变量
复制并重命名 `.env.example` 为 `.env`：
```bash
copy .env.example .env
```
用编辑器打开 `.env` 并填写：
- **TG_BOT_TOKEN**: 从 Telegram `@BotFather` 获取的机器人 Token。
- **TG_CHAT_ID**: 从 `@userinfobot` 获取的你本人的 Chat ID。
- **X 认证方式（任选其一，推荐方式 1）**：
  - **方式 1（极力推荐，完全绕过密码验证和风控）**：
    在浏览器打开 `x.com` 并登录，按 `F12` -> 进入 **Application (应用程序)** -> **Cookies** -> `https://x.com`，找到并复制：
    - `auth_token`
    - `ct0`
    粘贴到 `.env` 的 `X_AUTH_TOKEN=` 和 `X_CT0=`。
  - **方式 2**：填写 `X_USERNAME`、`X_PASSWORD`、`X_EMAIL` 账号密码进行交互登录。
- **PROXY**: 访问 X 和 Telegram 的本地代理（如 `http://127.0.0.1:7890`，如果在海外服务器可留空）。

---

## 🛠️ 运行方式

### ① 仅测试 Telegram 机器人连通性
```bash
python x_top_bot.py --test-tg
```

### ② 立即测试执行一次（抓取并推送）
```bash
python x_top_bot.py --run-now
```

### ③ 定时任务运行方式（每天早上 08:00 自动执行）

**方式 A【最推荐：Windows 系统级任务计划（已自动为你注册就绪）】**：
无需 24 小时开着终端窗口挂进程，Windows 会在每天早上 08:00 准时自动拉起虚拟环境执行抓取与推送，推完即自动退出。
- 重新注册/更新任务：
  ```powershell
  powershell -ExecutionPolicy Bypass -File .\register_daily_task.ps1
  ```
- 移除任务：
  ```powershell
  powershell -ExecutionPolicy Bypass -File .\unregister_daily_task.ps1
  ```

**方式 B【Python 进程常驻后台】**：
保持终端窗口开启：
```bash
python x_top_bot.py
```
> 程序内置 APScheduler，会在每天早上 08:00 自动触发并推送 20 条。

---

## 🧵 Threads 自动发帖系统 (08:00 ~ 23:00 均匀发布)

针对抓取到的 Top 20 热门推文，系统已打通全自动转发发布到 Threads 的流水线：

### 核心特性
1. **富媒体完整下载**：自动将 X 帖子的高清原图（支持多图 Carousel）和 MP4 视频提取并保存到本地 `media_cache/` 目录。
2. **科学发帖节奏**：每天 08:00 到 23:00（15 小时 = 900 分钟），20 条推文**严格按每约 45 分钟（带 ±3 分钟微小随机扰动）均匀发布**，最大程度贴合真人行为并防止平台限流。
3. **Cookie 会话驱动**：基于浏览器导出的 `threads_cookies.json`，通过无头自动化内核在后台静默操作发布，无需官方企业级 API 权限。

### 常用命令
- **查看当前待发队列详情与进度**：
  ```bash
  python threads_poster.py --status
  ```
- **启动 Threads 后台发布守护服务**：
  ```bash
  python threads_poster.py
  ```
  *(或直接双击运行 `start_threads_worker.bat`)*
- **立即测试发布队列中的第 1 条**：
  ```bash
  python threads_poster.py --post-now
  ```

---

## ⚖️ 热度计算公式说明

每条推文的热度指数按如下公式计算：
$$\text{Score} = \text{点赞} \times 1.0 + \text{转发} \times 2.0 + \text{评论} \times 1.5 + \text{引用} \times 1.5$$

可在 `.env` 中自定义各个维度的权重比例（`WEIGHT_LIKE`、`WEIGHT_RETWEET`、`WEIGHT_REPLY`、`WEIGHT_QUOTE`）。
