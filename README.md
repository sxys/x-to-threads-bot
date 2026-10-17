# 资产价格监听与 Telegram 预警机器人

一个轻量、稳定且功能完善的 Python 程序，用于实时监听**美股**和**加密货币**价格，并在价格突破或跌破你设定的范围时，自动通过 Telegram 机器人发送提醒消息。

---

## 🌟 核心特性

1. **多资产支持**：
   - **美股**：基于 Yahoo Finance，支持美股任意主流代码（如 `AAPL`, `TSLA`, `NVDA`, `MSFT` 等）。
   - **加密货币**：内置多源容灾（OKX、Binance、Binance.US、Coinbase、Yahoo Finance），自动切换最稳定行情源，支持主流币种（如 `BTC`, `ETH`, `SOL`, `DOGE` 等）。
2. **范围预警**：
   - 支持设置下限（`min_price`）与上限（`max_price`）。
   - 跌破下限时发出 📉 警报，突破上限时发出 🚀 警报。
   - 自动计算偏离金额与偏离百分比。
3. **防刷屏机制（报警冷却）**：
   - 避免价格持续破位时每分钟轰炸群聊，支持自定义冷却时间（如 30 分钟）。
   - 当价格回归正常区间后自动重置状态，下次再次破位时立即响应。
4. **内置辅助工具**：
   - `--test-tg`：一键测试机器人连通性与权限。
   - `--get-chat-id`：快速自动查询你与机器人的 Chat ID。
   - 完整支持 HTTP/SOCKS5 代理配置（适合国内网络直连 Telegram）。

---

## 📁 项目结构

```text
testGravity/
├── config.yaml          # 核心配置文件（配置 Bot Token、监听资产及预警区间）
├── monitor.py           # 程序主入口（包含轮询循环、报警判断与命令行功能）
├── price_fetcher.py     # 行情获取模块（美股与加密货币数据源）
├── notifier.py          # Telegram 消息推送与辅助模块
├── requirements.txt     # Python 依赖清单
└── README.md            # 项目说明文档
```

---

## 🚀 快速上手

### 1. 安装依赖

确保已安装 Python 3.9+，在项目目录下执行：

```bash
pip install -r requirements.txt
```

*(本项目依赖已预先为你安装完成：`yfinance`, `pyyaml`, `requests`)*

---

### 2. 配置 Telegram 机器人

#### 第一步：获取 Bot Token
1. 在 Telegram 中搜索并打开 `@BotFather`。
2. 发送 `/newbot`，按照指引给机器人起一个名称和用户名。
3. 创建成功后，BotFather 会给你一段 `HTTP API Token`（类似 `7123456789:AAFl...`）。

#### 第二步：获取你的 Chat ID
1. 在 Telegram 中搜索打开你刚才创建的机器人，点击 **Start**（或给它发送一条任意消息，如 `hello`）。
2. 打开项目下的 [config.yaml](file:///d:/abel/testGravity/config.yaml)，先将你的 `bot_token` 填入。
3. 在终端运行以下命令，程序会自动从最近消息中读取你的 `chat_id`：
   ```bash
   python monitor.py --get-chat-id
   ```
4. 将终端输出的 Chat ID 填入 [config.yaml](file:///d:/abel/testGravity/config.yaml) 中的 `chat_id`。

*(如果你的网络环境需要梯子才能访问 Telegram，请在 `config.yaml` 中配置代理，如 `proxy: "http://127.0.0.1:7890"`)*

---

### 3. 测试 Telegram 连接

配置好 Token 和 Chat ID 后，运行测试命令：

```bash
python monitor.py --test-tg
```

如果配置无误，你的 Telegram 会立即收到一条机器人的测试消息。

---

### 4. 自定义监控标的与预警区间

打开 [config.yaml](file:///d:/abel/testGravity/config.yaml)，根据需求添加或修改：

```yaml
# 1. 美股列表
stocks:
  - symbol: "AAPL"
    name: "苹果 (Apple)"
    min_price: 220.0     # 低于 220 美元触发跌破预警
    max_price: 250.0     # 高于 250 美元触发突破预警

  - symbol: "TSLA"
    name: "特斯拉 (Tesla)"
    min_price: 220.0
    max_price: 280.0

# 2. 加密货币列表
cryptos:
  - symbol: "BTC"
    name: "比特币 (Bitcoin)"
    min_price: 75000.0   # 低于 75000 USDT 报警
    max_price: 90000.0   # 高于 90000 USDT 报警

  - symbol: "ETH"
    name: "以太坊 (Ethereum)"
    min_price: 2300.0
    max_price: 2800.0
```

---

### 5. 启动监控程序

- **单次检查测试（不进入死循环，打印当前所有标的价格与状态）**：
  ```bash
  python monitor.py --once
  ```

- **正式启动常驻监控**：
  ```bash
  python monitor.py
  ```

程序启动后，会按照 `config.yaml` 中的 `check_interval_seconds` 定时轮询价格，并在控制台实时输出监控日志。一旦超出指定区间，立即发送如下样式的 Telegram 预警卡片：

```text
⚠️ 【价格超出范围预警】

🏷️ 标的名称: 苹果 (Apple) (AAPL)
💰 最新价格: $334.84
📊 监控区间: 220.0 ~ 250.0
🚨 预警状态: 🚀 突破价格上限
📈 偏离幅度: 高于上限 +$84.84 (+33.94%)
📡 行情源: YahooFinance
⏰ 预警时间: 2026-10-07 21:55:00
```
