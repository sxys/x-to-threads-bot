import re
import asyncio
import logging
import httpx
from datetime import datetime
from x_config import config
from queue_manager import QueueManager
from x_home_collector import XHomeCollector

logger = logging.getLogger("TGListener")

TWEET_URL_REGEX = re.compile(r'https?://(?:x\.com|twitter\.com)/\w+/status/(\d+)', re.IGNORECASE)


class TelegramListener:
    def __init__(self):
        self.bot_token = config.TG_BOT_TOKEN
        self.chat_id = str(config.TG_CHAT_ID)
        self.proxy = config.PROXY if config.PROXY and config.PROXY.strip() else None
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"
        self.offset = 0
        self._collector = None

    async def _get_collector(self):
        if self._collector is None:
            self._collector = XHomeCollector()
            await self._collector._login()
        return self._collector

    async def send_reply(self, client: httpx.AsyncClient, chat_id: str, text: str):
        """发送 Telegram 消息回复"""
        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        try:
            await client.post(url, json=payload, timeout=20.0)
        except Exception as e:
            logger.error(f"回复 Telegram 消息失败: {e}")

    async def handle_message(self, client: httpx.AsyncClient, message: dict):
        """处理用户发送的消息"""
        sender_chat = str(message.get("chat", {}).get("id", ""))
        text = (message.get("text") or "").strip()

        # 仅响应授权所有者（安全防越权）
        if sender_chat != self.chat_id:
            logger.warning(f"收到非白名单用户 ({sender_chat}) 发送的消息，已安全忽略。")
            return

        if not text:
            return

        # 1. 查询队列状态快捷指令
        if text.lower() in ("/status", "状态", "队列", "进度"):
            queue = QueueManager.load_queue()
            if not queue:
                await self.send_reply(client, sender_chat, "📋 当前没有排队的任务。")
                return

            published = [i for i in queue if i["status"] == "published"]
            pending = [i for i in queue if i["status"] == "pending"]
            next_task = pending[0] if pending else None

            next_info = f"⏰ <b>下一条待发</b>: <code>{next_task['scheduled_time']}</code>\n文案: {next_task['text'][:40]}..." if next_task else "🎉 所有任务已全部发布完毕！"
            msg = (
                f"📊 <b>【Threads 队列当前状态】</b>\n"
                f"────────────────────────\n"
                f"总任务数: <b>{len(queue)}</b> 条\n"
                f"已发布: <b>{len(published)}</b> 条 | 待发布: <b>{len(pending)}</b> 条\n"
                f"────────────────────────\n"
                f"{next_info}"
            )
            await self.send_reply(client, sender_chat, msg)
            return

        # 2. 清理媒体缓存快捷指令
        if text.lower() in ("/clean", "/cleanup", "清理", "清缓存"):
            result = QueueManager.cleanup_media_cache()
            msg = (
                f"🧹 <b>【媒体缓存清理完成】</b>\n"
                f"────────────────────────\n"
                f"已清理已发任务: <b>{result['cleaned_tasks_count']}</b> 个\n"
                f"已清理历史目录: <b>{result['cleaned_dirs_count']}</b> 个\n"
                f"共释放磁盘空间: <b>{result['mb_freed']:.2f} MB</b>\n"
                f"────────────────────────\n"
                f"🎉 VPS 磁盘空间已成功瘦身！"
            )
            await self.send_reply(client, sender_chat, msg)
            return

        # 3. 帮助提示
        if text.lower() in ("/help", "/start", "帮助"):
            help_msg = (
                f"👋 <b>您好！我是您的 X & Threads 助手</b>\n\n"
                f"💡 <b>使用方式</b>：\n"
                f"直接向我发送任何 <b>X (Twitter) 帖子链接</b>，我将立即自动抓取文案、高清图片或视频，"
                f"并以<b>最高优先级插队</b>安排在下一个发帖窗口发布到 Threads，后续任务自动顺延！\n\n"
                f"🔹 发送 <code>/status</code> 或 <code>状态</code> 可随时查看排期进度。\n"
                f"🔹 发送 <code>/clean</code> 或 <code>清理</code> 可随时清理已发推文的图片和视频缓存，释放 VPS 硬盘空间。"
            )
            await self.send_reply(client, sender_chat, help_msg)
            return

        # 4. 匹配 X/Twitter 链接进行优先插队处理
        match = TWEET_URL_REGEX.search(text)
        if not match:
            # 不是链接也不是指令，忽略其他普通杂项消息
            return

        tweet_id = match.group(1)
        logger.info(f"📥 接收到用户主动分享的推文链接: #{tweet_id}")

        await self.send_reply(
            client,
            sender_chat,
            f"📥 <b>收到分享的推文！</b>\n推文 ID: <code>{tweet_id}</code>\n正在为您解析并下载高清图片/视频..."
        )

        try:
            collector = await self._get_collector()
            tweet = await collector.client.get_tweet_by_id(tweet_id)
            if not tweet:
                await self.send_reply(
                    client,
                    sender_chat,
                    f"⚠️ <b>抓取失败</b>: 未能获取到推文 #{tweet_id} 的详情。\n请确认该推文是否被删除或属于受保护私密账号。"
                )
                return

            # 执行最高优先级插入与后续任务顺延
            result = await QueueManager.insert_priority_task(tweet, tweet_id)
            if not result.get("ok"):
                await self.send_reply(client, sender_chat, f"ℹ️ <b>提示</b>: {result.get('message', '处理失败')}")
                return

            item = result["item"]
            media_desc = f"[{item['media_type'].upper()}] ({len(item['media_files'])} 个附件)" if item['media_files'] else "[纯文本]"

            success_msg = (
                f"🚀 <b>【推文已成功插入 Threads 优先发布队列！】</b>\n"
                f"────────────────────────\n"
                f"👤 <b>作者</b>: {item['author']}\n"
                f"📝 <b>文案</b>: {item['text'][:90]}...\n"
                f"🎬 <b>媒体</b>: {media_desc}\n"
                f"⏰ <b>预计发布时间</b>: <code>{result['scheduled_time']}</code> (下一个安全发帖窗口)\n"
                f"📊 <b>当前队列概况</b>: 当天总数已增至 <b>{result['total_count']}</b> 条（剩余待发 {result['pending_count']} 条）\n"
                f"────────────────────────\n"
                f"<i>已自动重新均匀排布后续待发任务，发完为止。</i>"
            )
            await self.send_reply(client, sender_chat, success_msg)

        except Exception as e:
            logger.error(f"处理分享推文异常: {e}", exc_info=True)
            await self.send_reply(
                client,
                sender_chat,
                f"❌ <b>处理推文时发生错误</b>:\n<code>{str(e)}</code>"
            )

    async def run_listener(self):
        """Telegram 长轮询异步监听服务主循环"""
        if not self.bot_token or self.bot_token.startswith("YOUR_"):
            logger.warning("未配置有效的 TG_BOT_TOKEN，Telegram 互动监听服务未启动。")
            return

        logger.info("🤖 Telegram 双向互动监听器已就绪 (支持直接发送 X 链接插队)...")

        async with httpx.AsyncClient(proxy=self.proxy, timeout=35.0) as client:
            while True:
                try:
                    url = f"{self.base_url}/getUpdates"
                    params = {"offset": self.offset, "timeout": 25}
                    resp = await client.get(url, params=params)

                    if resp.status_code == 200:
                        data = resp.json()
                        updates = data.get("result", [])
                        for u in updates:
                            self.offset = u["update_id"] + 1
                            if "message" in u:
                                asyncio.create_task(self.handle_message(client, u["message"]))
                    elif resp.status_code == 409:
                        logger.warning("Telegram Bot 发生并发冲突 (getUpdates conflict)，等待 5 秒重试...")
                        await asyncio.sleep(5)
                    else:
                        logger.warning(f"Telegram getUpdates 响应非 200: HTTP {resp.status_code}")
                        await asyncio.sleep(3)

                except asyncio.CancelledError:
                    logger.info("Telegram 监听服务接收到退出信号。")
                    break
                except Exception as e:
                    logger.debug(f"Telegram 轮询网络波动: {e}")
                    await asyncio.sleep(3)
