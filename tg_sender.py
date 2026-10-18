import time
import html
import logging
from typing import List, Dict, Any
import requests
from x_config import config

logger = logging.getLogger("TGSender")

class TelegramSender:
    def __init__(self):
        self.bot_token = config.TG_BOT_TOKEN
        self.chat_id = config.TG_CHAT_ID
        self.proxies = {"http": config.PROXY, "https": config.PROXY} if config.PROXY and config.PROXY.strip() else None

    def send_message(self, text: str, disable_preview: bool = True) -> bool:
        """调用 Telegram Bot API 发送单条消息"""
        if not self.bot_token or self.bot_token.startswith("YOUR_"):
            logger.error("未配置有效的 TG_BOT_TOKEN！")
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": disable_preview
        }

        try:
            resp = requests.post(url, json=payload, proxies=self.proxies, timeout=20)
            if resp.status_code == 200:
                return True
            else:
                logger.error(f"Telegram 发送失败: HTTP {resp.status_code} - {resp.text}")
                return False
        except Exception as e:
            logger.error(f"Telegram 网络请求异常: {e}")
            return False

    def send_top_tweets(self, tweets: List[Dict[str, Any]]) -> bool:
        """格式化 Top 20 帖子并分批推送到 Telegram"""
        if not tweets:
            logger.info("推文列表为空，无需推送。")
            return False

        logger.info(f"开始向 Telegram 推送 {len(tweets)} 条热门推文...")

        # 批次分组，例如每 5 条发一条消息，排版最清晰且不会超出 4096 字符上限
        batch_size = 5
        total_batches = (len(tweets) + batch_size - 1) // batch_size

        # 1. 先发一个头部汇总
        header_msg = (
            f"🔥 <b>【X 主页最新热度 Top {len(tweets)} 推荐】</b>\n"
            f"📅 时间: <code>{time.strftime('%Y-%m-%d %H:%M:%S')}</code>\n"
            f"🎯 来源: <b>{config.TIMELINE_TYPE.upper()} 流</b> | 筛选总数: <b>{len(tweets)}</b> 条\n"
            f"────────────────────────"
        )
        self.send_message(header_msg)
        time.sleep(1)

        # 2. 分批发送卡片详情
        success_count = 0
        for b_idx in range(total_batches):
            batch = tweets[b_idx * batch_size: (b_idx + 1) * batch_size]
            lines = []

            for i, item in enumerate(batch, start=(b_idx * batch_size) + 1):
                author_safe = html.escape(item["author"])
                text_safe = html.escape(item["text"])
                post_url = item["url"]

                block = (
                    f"<b>#{i} | 热度分: {item['score']}</b>\n"
                    f"👤 <b>{author_safe}</b>\n"
                    f"📊 👍 {item['likes']} | 🔁 {item['retweets']} | 💬 {item['replies']}\n"
                    f"📝 <i>{text_safe}</i>\n"
                    f"🔗 <a href=\"{post_url}\">点击查看原帖</a>\n"
                )
                lines.append(block)

            body = "\n".join(lines)
            if self.send_message(body, disable_preview=True):
                success_count += 1
            time.sleep(1.2)  # 避免触发 Telegram API 速率限制

        logger.info(f"Telegram 推送完毕，成功发送 {success_count}/{total_batches} 组消息。")
        return success_count > 0
