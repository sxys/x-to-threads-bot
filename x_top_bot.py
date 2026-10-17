import sys
import argparse
import logging
from datetime import datetime
from apscheduler.schedulers.blocking import BlockingScheduler
from x_config import config
from x_home_collector import XHomeCollector
from tg_sender import TelegramSender

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("XTopBotMain")


def execute_daily_task():
    """每日抓取和推送主逻辑"""
    logger.info("========== 开始执行 X 主页热门推文抓取任务 ==========")
    sender = TelegramSender()
    try:
        collector = XHomeCollector()
        top_tweets = collector.fetch_home_top_tweets()

        if not top_tweets:
            logger.warning("本次未获取到推文数据，跳过推送。")
            return

        sender.send_top_tweets(top_tweets)
    except Exception as e:
        logger.error(f"任务执行异常: {e}", exc_info=True)


def main():
    parser = argparse.ArgumentParser(description="X (Twitter) Home 流每日热门 Top 20 监控推送机器人")
    parser.add_argument("--run-now", action="store_true", help="立即执行一次抓取与推送，不等待定时器")
    parser.add_argument("--test-tg", action="store_true", help="仅测试 Telegram 发信连通性")
    args = parser.parse_args()

    sender = TelegramSender()

    if args.test_tg:
        logger.info("正在发送测试消息至 Telegram...")
        success = sender.send_message("🤖 <b>Telegram 测试通知</b>\n您的 X Top 监控机器人配置连通成功！")
        if success:
            logger.info("✅ Telegram 连通性测试通过！")
        else:
            logger.error("❌ Telegram 发送失败，请检查 TG_BOT_TOKEN、TG_CHAT_ID 或 PROXY 代理配置。")
        return

    if args.run_now:
        logger.info("模式: 立即单次执行...")
        execute_daily_task()
        return

    # 默认模式：定时任务常驻
    scheduler = BlockingScheduler()
    scheduler.add_job(
        execute_daily_task,
        trigger='cron',
        hour=config.SCHEDULE_HOUR,
        minute=config.SCHEDULE_MINUTE
    )

    logger.info(
        f"🚀 调度程序已启动！每天 {config.SCHEDULE_HOUR:02d}:{config.SCHEDULE_MINUTE:02d} "
        f"自动从 X 主页抓取 Top {config.TOP_LIMIT} 帖子推送至 Telegram。"
    )
    logger.info("提示: 可随时使用 Ctrl + C 停止程序。如需立即测试一次可运行: python x_top_bot.py --run-now")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("接收到终止信号，监控已退出。")


if __name__ == "__main__":
    main()
