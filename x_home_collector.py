import os
import re
import asyncio
import logging
import httpx
from typing import List, Dict, Any
import twikit_patch
twikit_patch.apply_patch()

from twikit import Client
from x_config import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("XHomeCollector")

class XHomeCollector:
    def __init__(self):
        proxy_url = config.PROXY if config.PROXY and config.PROXY.strip() else None
        self.client = Client(language='en-US', proxy=proxy_url)
        # 调大网络超时阈值，防止在云服务器网络波动或 CDN 连接时超时（默认仅 5 秒过短）
        self.client.http.timeout = httpx.Timeout(30.0, connect=15.0)
        self.cookie_file = config.COOKIE_FILE

    async def _login(self):
        """登录或使用本地 Cookies 恢复会话"""
        # 1. 优先使用 .env 中填写的 auth_token 和 ct0（免密免挑战，最稳定）
        if config.X_AUTH_TOKEN and config.X_CT0 and not config.X_AUTH_TOKEN.startswith("your_"):
            logger.info("检测到配置的 auth_token 与 ct0，正在使用浏览器 Cookie 会话...")
            cookies = {
                "auth_token": config.X_AUTH_TOKEN.strip(),
                "ct0": config.X_CT0.strip()
            }
            self.client.set_cookies(cookies)
            self.client.save_cookies(self.cookie_file)
            logger.info(f"Cookie 会话设置成功并已持久化到 {self.cookie_file}")
            return

        # 2. 尝试从本地 Cookie 文件加载
        if os.path.exists(self.cookie_file):
            # 检查文件是否有效（避免加载之前生成的空 json {}）
            try:
                import json
                with open(self.cookie_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and len(data) > 0 and ("auth_token" in data or len(data) >= 2):
                    logger.info(f"检测到已存在的有效 Cookie 文件: {self.cookie_file}，正在加载会话...")
                    self.client.load_cookies(self.cookie_file)
                    logger.info("Cookie 加载成功！")
                    return
                else:
                    logger.warning(f"Cookie 文件 {self.cookie_file} 为空或无效，将删除并重新登录...")
                    os.remove(self.cookie_file)
            except Exception as e:
                logger.warning(f"Cookie 文件解析失败 ({e})，将删除并尝试重新登录...")
                if os.path.exists(self.cookie_file):
                    os.remove(self.cookie_file)

        # 3. 账号密码登录
        if config.X_USERNAME.startswith("YOUR_") or config.X_PASSWORD.startswith("YOUR_"):
            raise ValueError(
                "未找到有效登录凭证！请在 .env 中填写 X_AUTH_TOKEN 和 X_CT0 (最推荐)，"
                "或者填写 X_USERNAME, X_EMAIL 和 X_PASSWORD。"
            )

        logger.info(f"正在为用户 @{config.X_USERNAME} 执行交互式账号密码登录...")
        await self.client.login(
            auth_info_1=config.X_USERNAME,
            auth_info_2=config.X_EMAIL,
            password=config.X_PASSWORD,
            cookies_file=self.cookie_file
        )
        logger.info(f"登录成功并已将会话 Cookies 保存至 {self.cookie_file}")

    def calculate_score(self, tweet) -> float:
        """根据点赞、转发、评论、引用加权计算热度"""
        likes = getattr(tweet, "favorite_count", 0) or 0
        retweets = getattr(tweet, "retweet_count", 0) or 0
        replies = getattr(tweet, "reply_count", 0) or 0
        quotes = getattr(tweet, "quote_count", 0) or 0
        
        score = (
            likes * config.WEIGHT_LIKE +
            retweets * config.WEIGHT_RETWEET +
            replies * config.WEIGHT_REPLY +
            quotes * config.WEIGHT_QUOTE
        )
        return round(score, 1)

    @staticmethod
    def is_original_chinese(tweet) -> bool:
        """
        判断帖子是否为原生的中文内容：
        1. 检查 X 官方识别的原帖语言，若是西语 (es)、葡语 (pt)、英语 (en)、日语 (ja) 等，直接排除；
        2. 必须包含至少 5 个以上中文字符（汉字），排除纯网址贴、英文贴和误标中文的翻译贴。
        """
        # 1. 检查 X 平台标记的原帖语言
        lang = getattr(tweet, "lang", None)
        if lang:
            lang = str(lang).lower().strip()
            # 允许的中文标记前缀: zh, zh-cn, zh-tw, zh-hans, zh-hant, 或 zxx (无语言/混合)
            if not (lang.startswith("zh") or lang in ("zxx", "chinese")):
                return False

        # 2. 检查正文中真实汉字数量
        text = getattr(tweet, "text", "") or ""
        chinese_chars = re.findall(r'[\u4e00-\u9fff]', text)
        if len(chinese_chars) < 5:
            return False

        return True

    @staticmethod
    def is_ad_tweet(tweet) -> bool:
        """多重广告过滤判定（拦截官方投放与博主自主标注广告）"""
        # 1. 检查底层广告标志
        if getattr(tweet, "is_promoted", False):
            return True

        # 2. 检查推文底层数据中残留的广告元数据
        data = getattr(tweet, "_data", {}) or {}
        if "promotedMetadata" in data or "promoted_metadata" in data:
            return True

        # 3. 检查正文中的商业合作与广告关键词
        text = (getattr(tweet, "text", "") or "").lower()
        ad_keywords = ["#ad", "#advertisement", "#sponsored", "#推广", "#商业合作", "#商务合作", "#广告"]
        for kw in ad_keywords:
            if kw in text:
                return True

        return False

    async def _fetch_home_top_tweets_async(self) -> List[Dict[str, Any]]:
        """异步拉取主页推文并进行严格的中文与免广告过滤与热度排序"""
        await self._login()

        timeline_type = config.TIMELINE_TYPE.lower()
        max_pages = max(config.FETCH_PAGES, 8)  # 至少支持翻 8 页以防过滤外文后候选不足
        target_candidates = config.TOP_LIMIT * 2  # 收集到 40 条原生中文候选即可提前停止

        logger.info(f"开始抓取主页流 (类型: {timeline_type})，严格筛选原生中文且跳过广告帖子...")

        # 首次拉取
        if timeline_type == "following":
            result = await self.client.get_latest_timeline(count=40)
        else:
            result = await self.client.get_timeline(count=40)

        all_tweets = []
        seen_ids = set()
        filtered_count = 0
        ad_filtered_count = 0

        def process_batch(tweets_batch):
            nonlocal all_tweets, seen_ids, filtered_count, ad_filtered_count
            if not tweets_batch:
                return
            for tweet in tweets_batch:
                tweet_id = getattr(tweet, "id", None)
                if not tweet_id or tweet_id in seen_ids:
                    continue
                seen_ids.add(tweet_id)

                # 排除广告推广（官方投放广告 + 商业软广）
                if self.is_ad_tweet(tweet):
                    ad_filtered_count += 1
                    continue

                # 严格原生中文过滤
                if not self.is_original_chinese(tweet):
                    filtered_count += 1
                    continue

                user = getattr(tweet, "user", None)
                screen_name = getattr(user, "screen_name", "i") if user else "i"
                user_name = getattr(user, "name", "Twitter User") if user else "Twitter User"
                text = getattr(tweet, "text", "") or ""

                score = self.calculate_score(tweet)
                full_text = getattr(tweet, "full_text", "") or text
                all_tweets.append({
                    "id": tweet_id,
                    "url": f"https://x.com/{screen_name}/status/{tweet_id}",
                    "author": f"{user_name} (@{screen_name})",
                    "text": (text.replace("\n", " ")[:100] + "...") if len(text) > 100 else text.replace("\n", " "),
                    "full_text": full_text,
                    "tweet_obj": tweet,
                    "score": score,
                    "likes": getattr(tweet, "favorite_count", 0) or 0,
                    "retweets": getattr(tweet, "retweet_count", 0) or 0,
                    "replies": getattr(tweet, "reply_count", 0) or 0,
                    "quotes": getattr(tweet, "quote_count", 0) or 0,
                })

        process_batch(result)
        logger.info(f"第 1 页抓取完成，当前符合要求的中文帖子: {len(all_tweets)} (已过滤非中文: {filtered_count}, 过滤广告: {ad_filtered_count})")

        # 异步翻页收集更多中文帖子
        for page in range(2, max_pages + 1):
            if len(all_tweets) >= target_candidates:
                logger.info(f"已收集满 {len(all_tweets)} 条原生中文候选帖子，提前完成翻页。")
                break

            await asyncio.sleep(2)  # 防风控频率限制
            try:
                result = await result.next()
                if not result:
                    logger.info("无更多主页数据，停止翻页。")
                    break
                process_batch(result)
                logger.info(f"第 {page} 页抓取完成，累计中文帖子: {len(all_tweets)} (已过滤非中文: {filtered_count}, 过滤广告: {ad_filtered_count})")
            except Exception as e:
                logger.warning(f"第 {page} 页翻页抓取出现异常 ({e})，停止翻页。")
                break

        if not all_tweets:
            logger.warning("未能从主页中获取到符合原生中文要求的有效推文。")
            return []

        # 按热度降序排序
        all_tweets.sort(key=lambda x: x["score"], reverse=True)
        top_list = all_tweets[:config.TOP_LIMIT]

        logger.info(f"排序完成！已选出热度前 {len(top_list)} 条纯中文帖子（最高热度分: {top_list[0]['score']}）。")

        # 自动生成 Threads 待发队列并下载本地多媒体资源
        try:
            from queue_manager import QueueManager
            await QueueManager.build_threads_queue(top_list)
        except Exception as e:
            logger.error(f"生成 Threads 队列或下载媒体失败: {e}", exc_info=True)

        return top_list

    def fetch_home_top_tweets(self) -> List[Dict[str, Any]]:
        """对外暴露的同步接口"""
        return asyncio.run(self._fetch_home_top_tweets_async())
