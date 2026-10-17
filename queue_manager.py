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
    def generate_schedule_times(count: int, start_dt: datetime = None, duration_hours: float = 24.0) -> List[str]:
        """
        全天 24 小时均匀分布发帖时间表：
        从 start_dt 开始（默认当前时刻），在接下来的 24 小时（1440 分钟）内均匀分布。
        20 条推文平均间隔约 70~72 分钟，并附加 ±3 分钟的随机抖动，完全模拟真人发帖规律。
        """
        if start_dt is None:
            start_dt = datetime.now()

        total_minutes = int(duration_hours * 60)  # 24 * 60 = 1440 分钟
        step = (total_minutes - 30) / max(count, 1)

        times = []
        for i in range(count):
            jitter = random.randint(-3, 3)
            minute_offset = 10 + int(i * step) + jitter
            minute_offset = max(3, min(total_minutes - 5, minute_offset))
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

        times = cls.generate_schedule_times(count, duration_hours=24.0)
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
    def reschedule_pending_tasks(cls, start_dt: datetime = None, target_end_dt: datetime = None):
        """
        对当前队列中所有尚未发布的任务进行平摊重排：
        将剩余待发任务按均匀时间间隔重新排布至下一个 24 小时周期（或次日早上 08:00），
        避免中途启动或暂停后积压连发。
        """
        queue = cls.load_queue()
        pending_indices = [i for i, item in enumerate(queue) if item.get("status") == "pending"]
        if not pending_indices:
            return queue

        now = datetime.now()
        if start_dt is None:
            start_dt = now + timedelta(minutes=2)

        if target_end_dt is None:
            # 默认目标结束时间为下一次抓取的时间（次日早上 08:00）
            today_8am = datetime(now.year, now.month, now.day, 8, 0, 0)
            if now < today_8am:
                next_cycle = today_8am
            else:
                next_cycle = today_8am + timedelta(days=1)
            # 保证平均每个任务至少有 30 分钟间隔
            min_end = start_dt + timedelta(minutes=max(len(pending_indices) * 45, 120))
            target_end_dt = max(next_cycle, min_end)

        remaining_minutes = (target_end_dt - start_dt).total_seconds() / 60
        count = len(pending_indices)

        if count == 1:
            step = 0
        else:
            step = remaining_minutes / max(count - 1, 1)

        logger.info(f"正在对剩余 {count} 条待发任务进行 24 小时均匀平摊重排（可用时间: {remaining_minutes/60:.1f} 小时，平均间隔: {step:.1f} 分钟）...")

        for rank, q_idx in enumerate(pending_indices):
            offset = int(rank * step)
            jitter = random.randint(-3, 3) if step >= 15 else 0
            task_time = start_dt + timedelta(minutes=max(0, offset + jitter))
            if task_time > target_end_dt:
                task_time = target_end_dt
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
