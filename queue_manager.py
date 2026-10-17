import os
import re
import json
import random
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any

logger = logging.getLogger("QueueManager")
QUEUE_FILE = "threads_queue.json"
MEDIA_CACHE_DIR = "media_cache"


class QueueManager:
    @staticmethod
    def clean_tweet_text(text: str) -> str:
        """清洗推文内容，去除尾部 t.co 短链接，保留核心文案和话题"""
        if not text:
            return ""
        # 移除结尾的短链接 https://t.co/xxxx
        cleaned = re.sub(r'https?://t\.co/\w+\s*$', '', text).strip()
        return cleaned

    @staticmethod
    def generate_schedule_times(count: int, start_hour: int = 8, end_hour: int = 23) -> List[str]:
        """
        在当天 start_hour ~ end_hour (08:00~23:00) 之间生成均匀分布的计划时间
        带 ±3 分钟的随机抖动，防平台频控
        """
        today = datetime.now().date()
        start_dt = datetime(today.year, today.month, today.day, start_hour, 0, 0)
        total_minutes = (end_hour - start_hour) * 60  # 15 * 60 = 900 分钟

        usable_minutes = total_minutes - 40
        step = usable_minutes / max(count - 1, 1)

        times = []
        for i in range(count):
            jitter = random.randint(-3, 3)
            minute_offset = 15 + int(i * step) + jitter
            minute_offset = max(5, min(total_minutes - 10, minute_offset))
            target_time = start_dt + timedelta(minutes=minute_offset)
            times.append(target_time.strftime("%Y-%m-%d %H:%M:%S"))
        return times

    @classmethod
    async def download_tweet_media(cls, tweet, tweet_id: str) -> Dict[str, Any]:
        """下载推文中的高清图片或视频到本地"""
        media_list = getattr(tweet, "media", []) or []
        if not media_list:
            return {"type": "none", "files": []}

        tweet_dir = os.path.join(MEDIA_CACHE_DIR, str(tweet_id))
        os.makedirs(tweet_dir, exist_ok=True)

        downloaded_files = []
        media_type = "image"

        for idx, m in enumerate(media_list):
            m_type = getattr(m, "type", "photo")
            try:
                if m_type in ("video", "animated_gif"):
                    media_type = "video"
                    file_path = os.path.abspath(os.path.join(tweet_dir, f"video_{idx}.mp4"))
                    # 关键修复：从 streams 中挑选最高清晰度的真实 MP4 视频流下载
                    streams = getattr(m, "streams", []) or []
                    if streams:
                        best_stream = max(streams, key=lambda s: getattr(s, "bitrate", 0) or 0)
                        logger.info(f"正在下载高清 MP4 视频 (码率: {best_stream.bitrate})...")
                        await best_stream.download(file_path)
                        downloaded_files.append(file_path)
                        break
                    else:
                        await m.download(file_path)
                        downloaded_files.append(file_path)
                        break
                else:
                    # 图片
                    file_path = os.path.abspath(os.path.join(tweet_dir, f"image_{idx}.jpg"))
                    if not os.path.exists(file_path):
                        await m.download(file_path)
                    downloaded_files.append(file_path)
            except Exception as e:
                logger.warning(f"下载媒体失败 [Tweet {tweet_id}, idx {idx}]: {e}")

        if not downloaded_files:
            return {"type": "none", "files": []}

        return {"type": media_type, "files": downloaded_files}

    @classmethod
    async def build_threads_queue(cls, top_tweets_with_obj: List[Dict[str, Any]]) -> str:
        """
        根据 Top 20 帖子生成包含本地媒体路径与发布时间的 threads_queue.json
        """
        count = len(top_tweets_with_obj)
        if count == 0:
            return QUEUE_FILE

        times = cls.generate_schedule_times(count, start_hour=8, end_hour=23)
        queue_items = []

        logger.info(f"正在为 {count} 条热门帖子下载媒体资源并生成发布队列...")

        for idx, item in enumerate(top_tweets_with_obj):
            tweet_obj = item.get("tweet_obj")
            tweet_id = item["id"]
            cleaned_text = cls.clean_tweet_text(item.get("full_text", item["text"]))

            media_info = {"type": "none", "files": []}
            if tweet_obj:
                media_info = await cls.download_tweet_media(tweet_obj, tweet_id)

            queue_items.append({
                "index": idx + 1,
                "id": tweet_id,
                "url": item["url"],
                "author": item["author"],
                "text": cleaned_text,
                "media_type": media_info["type"],
                "media_files": media_info["files"],
                "scheduled_time": times[idx],
                "status": "pending",  # pending -> publishing -> published / failed
                "published_at": None,
                "error": None
            })

        with open(QUEUE_FILE, "w", encoding="utf-8") as f:
            json.dump(queue_items, f, ensure_ascii=False, indent=2)

        logger.info(f"发布队列生成成功！共 {len(queue_items)} 个任务已写入 {QUEUE_FILE}")
        return QUEUE_FILE

    @classmethod
    def load_queue(cls) -> List[Dict[str, Any]]:
        """读取当前发布队列"""
        if not os.path.exists(QUEUE_FILE):
            return []
        try:
            with open(QUEUE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"读取队列失败: {e}")
            return []

    @classmethod
    def save_queue(cls, queue: List[Dict[str, Any]]):
        """保存更新后的发布队列"""
        with open(QUEUE_FILE, "w", encoding="utf-8") as f:
            json.dump(queue, f, ensure_ascii=False, indent=2)

    @classmethod
    def get_next_pending_task(cls) -> Dict[str, Any] | None:
        """获取当前时间已到期且尚未发布的下一个任务"""
        queue = cls.load_queue()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for item in queue:
            if item.get("status") == "pending":
                # 如果计划时间已到或已过
                if item.get("scheduled_time", "") <= now_str:
                    return item
        return None

    @classmethod
    def get_last_published_time(cls) -> datetime | None:
        """获取最近一条已成功发布任务的时间"""
        queue = cls.load_queue()
        published_times = []
        for item in queue:
            if item.get("status") == "published" and item.get("published_at"):
                try:
                    dt = datetime.strptime(item["published_at"], "%Y-%m-%d %H:%M:%S")
                    published_times.append(dt)
                except Exception:
                    pass
        if published_times:
            return max(published_times)
        return None

    @classmethod
    def reschedule_pending_tasks(cls, start_dt: datetime = None, end_hour: int = 23):
        """
        对当前队列中所有尚未发布的任务进行平摊重排。
        如果当前时间已经晚于早上 08:00，则从当前时间（+2分钟）开始，一直到今天 23:00，
        将剩余待发任务按均匀时间间隔重新排布，避免中途启动时积压连发。
        """
        queue = cls.load_queue()
        pending_indices = [i for i, item in enumerate(queue) if item.get("status") == "pending"]
        if not pending_indices:
            return queue

        now = datetime.now()
        if start_dt is None:
            # 默认从当前时间后 2 分钟开始排
            start_dt = now + timedelta(minutes=2)

        today = now.date()
        end_dt = datetime(today.year, today.month, today.day, end_hour, 0, 0)

        if start_dt >= end_dt:
            logger.warning("当前时间已超过今日计划结束时间 23:00，暂不执行自动重排。")
            return queue

        remaining_minutes = (end_dt - start_dt).total_seconds() / 60
        count = len(pending_indices)

        if count == 1:
            step = 0
        else:
            step = remaining_minutes / max(count - 1, 1)

        logger.info(f"正在对剩余 {count} 条待发任务进行平摊重排（剩余可用时间: {remaining_minutes:.0f} 分钟，平均间隔: {step:.1f} 分钟）...")

        for rank, q_idx in enumerate(pending_indices):
            offset = int(rank * step)
            jitter = random.randint(-2, 2) if step >= 10 else 0
            task_time = start_dt + timedelta(minutes=max(0, offset + jitter))
            if task_time > end_dt:
                task_time = end_dt
            queue[q_idx]["scheduled_time"] = task_time.strftime("%Y-%m-%d %H:%M:%S")

        cls.save_queue(queue)
        logger.info(f"重排完成！下一条任务计划将在 {queue[pending_indices[0]]['scheduled_time']} 发出。")
        return queue

    @classmethod
    def update_task_status(cls, task_id: str, status: str, error: str = None):
        """更新指定任务的状态"""
        queue = cls.load_queue()
        for item in queue:
            if item.get("id") == task_id:
                item["status"] = status
                if status == "published":
                    item["published_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if error:
                    item["error"] = error
                break
        cls.save_queue(queue)
