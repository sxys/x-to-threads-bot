#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
资产价格监听与 Telegram 预警机器人
- 支持美股与加密货币价格监听
- 支持自定义价格范围预警（跌破下限、突破上限）
- 支持报警冷却机制，防止通知刷屏
"""

import os
import sys
import time
import argparse
import logging
from datetime import datetime
from typing import Dict, Any, Optional

import yaml

from notifier import TelegramNotifier
from price_fetcher import PriceFetcher

# 解决 Windows 控制台编码问题
if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 配置控制台日志格式
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("PriceMonitor")


class PriceMonitor:
    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = config_path
        self.config = self._load_config()

        # 读取配置项
        tg_conf = self.config.get("telegram", {})
        self.bot_token = tg_conf.get("bot_token", "")
        self.chat_id = str(tg_conf.get("chat_id", ""))
        self.proxy = tg_conf.get("proxy", "")

        settings = self.config.get("settings", {})
        self.check_interval = int(settings.get("check_interval_seconds", 60))
        self.alert_cooldown = int(settings.get("alert_cooldown_minutes", 30)) * 60

        # 初始化组件
        self.notifier = TelegramNotifier(self.bot_token, self.chat_id, self.proxy)
        self.fetcher = PriceFetcher(self.proxy)

        # 记录报警历史: { "ASSET_TYPE:SYMBOL": {"last_alert_time": timestamp, "last_direction": "HIGH"|"LOW"|"NORMAL"} }
        self.alert_history: Dict[str, Dict[str, Any]] = {}

    def _load_config(self) -> Dict[str, Any]:
        """加载 YAML 配置文件"""
        if not os.path.exists(self.config_path):
            logger.error(f"配置文件不存在: {self.config_path}")
            sys.exit(1)

        with open(self.config_path, "r", encoding="utf-8") as f:
            try:
                return yaml.safe_load(f) or {}
            except Exception as e:
                logger.error(f"解析配置文件失败: {e}")
                sys.exit(1)

    def check_asset(self, asset_type: str, item: Dict[str, Any]) -> None:
        """检查单个标的的价格并判断是否触发警报"""
        symbol = item.get("symbol", "").strip()
        name = item.get("name", symbol)
        min_price = item.get("min_price")
        max_price = item.get("max_price")

        if not symbol:
            return

        # 1. 获取最新价格
        if asset_type == "stock":
            price, source = self.fetcher.get_stock_price(symbol)
        else:
            price, source = self.fetcher.get_crypto_price(symbol)

        if price is None:
            logger.warning(f"[{name}] {symbol}: 无法获取最新价格，请检查标的代码或网络")
            return

        # 2. 判断是否超出范围
        key = f"{asset_type}:{symbol}"
        history = self.alert_history.setdefault(key, {"last_alert_time": 0, "last_direction": "NORMAL"})
        now = time.time()

        direction = "NORMAL"
        alert_text = ""
        diff_str = ""

        if min_price is not None and price < float(min_price):
            direction = "LOW"
            diff = float(min_price) - price
            pct = (diff / float(min_price)) * 100
            diff_str = f"低于下限 -${diff:.2f} (-{pct:.2f}%)"
            alert_text = "📉 <b>跌破价格下限</b>"
        elif max_price is not None and price > float(max_price):
            direction = "HIGH"
            diff = price - float(max_price)
            pct = (diff / float(max_price)) * 100
            diff_str = f"高于上限 +${diff:.2f} (+{pct:.2f}%)"
            alert_text = "🚀 <b>突破价格上限</b>"

        # 打印控制台监控状态
        range_str = f"[{min_price if min_price is not None else '-∞'} ~ {max_price if max_price is not None else '+∞'}]"
        logger.info(f"[{'美股' if asset_type == 'stock' else '加密'}] {name} ({symbol}): 当前价 ${price:.2f} | 设定区间 {range_str} | 状态: {direction} (来源: {source})")

        # 3. 报警触发逻辑
        if direction != "NORMAL":
            last_alert_time = history["last_alert_time"]
            last_direction = history["last_direction"]
            time_since_last = now - last_alert_time

            # 触发条件：
            # 1. 报警方向发生变化（例如从上限变成下限）
            # 2. 距离上次报警已超过冷却时间
            is_new_direction = (direction != last_direction)
            is_cooled_down = (time_since_last >= self.alert_cooldown)

            if is_new_direction or is_cooled_down:
                logger.warning(f"触发预警 -> [{name}] {symbol} 价格 ${price:.2f} 超出范围！正在推送 Telegram...")
                
                # 组装 Telegram 通知内容
                time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                message = (
                    f"⚠️ <b>【价格超出范围预警】</b>\n\n"
                    f"🏷️ <b>标的名称:</b> {name} (<code>{symbol}</code>)\n"
                    f"💰 <b>最新价格:</b> <code>${price:,.2f}</code>\n"
                    f"📊 <b>监控区间:</b> <code>{min_price or '-'} ~ {max_price or '-'}</code>\n"
                    f"🚨 <b>预警状态:</b> {alert_text}\n"
                    f"📈 <b>偏离幅度:</b> {diff_str}\n"
                    f"📡 <b>行情源:</b> {source}\n"
                    f"⏰ <b>预警时间:</b> {time_str}\n"
                )

                success = self.notifier.send_message(message)
                if success:
                    history["last_alert_time"] = now
                    history["last_direction"] = direction
            else:
                remaining = int((self.alert_cooldown - time_since_last) / 60)
                logger.debug(f"[{symbol}] 仍处于报警冷却期内，剩余约 {remaining} 分钟")
        else:
            # 价格回归正常区间时，重置状态（下次再破位可以立刻报警）
            if history["last_direction"] != "NORMAL":
                logger.info(f"[{name}] {symbol} 价格回归正常区间，重置预警状态。")
                history["last_direction"] = "NORMAL"

    def run_once(self) -> None:
        """运行单轮检查"""
        stocks = self.config.get("stocks", [])
        cryptos = self.config.get("cryptos", [])

        for item in stocks:
            try:
                self.check_asset("stock", item)
            except Exception as e:
                logger.error(f"检查美股 {item} 出错: {e}")

        for item in cryptos:
            try:
                self.check_asset("crypto", item)
            except Exception as e:
                logger.error(f"检查加密货币 {item} 出错: {e}")

    def start_loop(self) -> None:
        """持续运行监听循环"""
        logger.info("=" * 55)
        logger.info("🚀 价格监控服务已启动！")
        logger.info(f"⏱️ 轮询周期: 每 {self.check_interval} 秒一次")
        logger.info(f"⏳ 报警冷却: {self.alert_cooldown // 60} 分钟")
        logger.info(f"🔔 Telegram 推送: {'已开启' if self.bot_token else '未配置'}")
        logger.info("=" * 55)

        try:
            while True:
                start_time = time.time()
                self.run_once()
                elapsed = time.time() - start_time
                sleep_time = max(1.0, self.check_interval - elapsed)
                time.sleep(sleep_time)
        except KeyboardInterrupt:
            logger.info("用户停止运行，监控服务已退出。")


def test_telegram(config_path: str):
    """测试 Telegram 发送功能"""
    with open(config_path, "r", encoding="utf-8") as f:
        conf = yaml.safe_load(f) or {}

    tg_conf = conf.get("telegram", {})
    notifier = TelegramNotifier(
        tg_conf.get("bot_token", ""),
        str(tg_conf.get("chat_id", "")),
        tg_conf.get("proxy", "")
    )

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    msg = (
        f"🤖 <b>【Telegram 机器人连接测试】</b>\n\n"
        f"✅ 恭喜！你的价格预警机器人已成功连接并能正常推送消息！\n"
        f"⏰ 测试时间: {now}\n\n"
        f"<i>价格监控运行中若发现标的破位，将自动在此推送预警！</i>"
    )
    print("正在尝试向 Telegram 发送测试消息...")
    ok = notifier.send_message(msg)
    if ok:
        print("✅ 测试消息发送成功！请检查你的 Telegram 客户端。")
    else:
        print("❌ 发送失败，请检查 bot_token、chat_id 或代理设置。")


def get_chat_id(config_path: str):
    """辅助获取 Chat ID"""
    with open(config_path, "r", encoding="utf-8") as f:
        conf = yaml.safe_load(f) or {}

    tg_conf = conf.get("telegram", {})
    token = tg_conf.get("bot_token", "")
    proxy = tg_conf.get("proxy", "")

    if not token or "YOUR_" in token:
        print("❌ 请先在 config.yaml 中填入你的 bot_token！")
        return

    notifier = TelegramNotifier(token, "", proxy)
    print("正在查询机器人最近接收到的消息以提取 Chat ID...")
    print("提示：如果提示找不到消息，请先在 Telegram 打开你的机器人，点击 /start 或发送任意文本。")
    chat_id = notifier.get_latest_chat_id()
    if chat_id:
        print("\n" + "=" * 40)
        print(f"🎉 成功获取到你的 Chat ID: {chat_id}")
        print(f"👉 请将此值填入 config.yaml 中的 chat_id: \"{chat_id}\"")
        print("=" * 40 + "\n")


def main():
    parser = argparse.ArgumentParser(description="美股 & 加密货币价格监听与 Telegram 预警机器人")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径 (默认 config.yaml)")
    parser.add_argument("--once", action="store_true", help="只运行单次检查并退出 (用于测试)")
    parser.add_argument("--test-tg", action="store_true", help="发送一条测试消息到 Telegram 验证配置")
    parser.add_argument("--get-chat-id", action="store_true", help="从 Telegram 获取互动用户的 Chat ID")

    args = parser.parse_args()

    if args.test_tg:
        test_telegram(args.config)
    elif args.get_chat_id:
        get_chat_id(args.config)
    else:
        monitor = PriceMonitor(args.config)
        if args.once:
            monitor.run_once()
        else:
            monitor.start_loop()


if __name__ == "__main__":
    main()
