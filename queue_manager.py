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
    def generate_schedule_times(count: int, start_dt: datetime = None, interval_minutes: int = 30) -> List[str]:
        """
        按指定时间间隔（默认 30 分钟）均匀分布发帖时间表：
        从 start_dt 开始（默认当前时刻），每条推文间隔 interval_minutes，
        附加 ±3 分钟人类拟真随机抖动。
        """
        if start_dt is None:
            start_dt = datetime.now()

        times = []
        for i in range(count):
            jitter = random.randint(-3, 3)
            minute_offset = int(i * interval_minutes) + jitter
            target_time = start_dt + timedelta(minutes=max(2, minute_offset))
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
    async def build_threads_queue(cls, top_tweets_with_obj: List[Dict[str, Any]], interval_minutes: int = 30) -> str:
        """
        根据抓取的热门帖子平摊排期并追加合并至 threads_queue.json：
        - 每 4 小时抓取一批（如 8 条），在 4 小时内按每隔约 30 分钟均匀发布一条
        - 如果队列中已有待发任务，新任务无缝顺延在其后，杜绝冲掉现有任务
        - 自动清理历史已发布媒体文件，保障磁盘空间充裕
        """
        count = len(top_tweets_with_obj)
        if count == 0:
            return QUEUE_FILE

        # 1. 自动清理已发帖子的本地媒体文件，保障磁盘空间充裕
        cls.cleanup_media_cache()

        # 2. 读取现有队列
        existing_queue = cls.load_queue()

        # 过滤并保留最近的已发布任务（最多保留 48 条记录用于状态查看）
        published_items = [i for i in existing_queue if i.get("status") == "published"][-48:]
        pending_items = [i for i in existing_queue if i.get("status") == "pending"]

        existing_ids = {str(i.get("id")) for i in existing_queue}

        # 3. 过滤掉队列中已存在的推文（防止重复加入）
        new_tweets = []
        for item in top_tweets_with_obj:
            tid = str(item["id"])
            if tid not in existing_ids:
                new_tweets.append(item)
            else:
                logger.info(f"推文 #{tid} 已在发布队列中，跳过重复添加。")

        if not new_tweets:
            logger.info("本次抓取的所有推文已存在于发布队列中，无需重复添加。")
            return QUEUE_FILE

        # 4. 计算排期时间：
        # 如果当前队列中已有待发任务，且最后一个任务的计划时间在未来，
        # 则新任务无缝排在最后一个待发任务之后（间隔 interval_minutes）
        latest_pending_dt = None
        for item in pending_items:
            t_str = item.get("scheduled_time")
            if t_str:
                try:
                    dt = datetime.strptime(t_str, "%Y-%m-%d %H:%M:%S")
                    if latest_pending_dt is None or dt > latest_pending_dt:
                        latest_pending_dt = dt
                except Exception:
                    pass

        now = datetime.now()
        if latest_pending_dt and latest_pending_dt > now:
            start_dt = latest_pending_dt + timedelta(minutes=interval_minutes)
        else:
            # 队列当前无待发任务，第一条在 12~18 分钟后发出
            start_dt = now + timedelta(minutes=max(10, interval_minutes // 2))

        times = cls.generate_schedule_times(len(new_tweets), start_dt=start_dt, interval_minutes=interval_minutes)

        logger.info(f"正在为 {len(new_tweets)} 条新增热门帖子下载媒体资源并排期 (每隔约 {interval_minutes} 分钟发一条)...")

        new_queue_items = []
        for idx, item in enumerate(new_tweets):
            tweet_obj = item.get("tweet_obj")
            tweet_id = item["id"]
            cleaned_text = cls.clean_tweet_text(item.get("full_text", item["text"]))

            media_info = {"type": "none", "files": []}
            if tweet_obj:
                media_info = await cls.download_tweet_media(tweet_obj, tweet_id)

            new_queue_items.append({
                "index": 0,
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

        full_queue = published_items + pending_items + new_queue_items
        for idx, item in enumerate(full_queue):
            item["index"] = idx + 1

        cls.save_queue(full_queue)
        logger.info(f"发布队列更新成功！本次新增 {len(new_queue_items)} 条，当前队列共 {len(full_queue)} 条（待发 {len(pending_items) + len(new_queue_items)} 条）。")
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
    def reschedule_pending_tasks(cls, start_dt: datetime = None, interval_minutes: int = 30):
        """
        对当前队列中所有尚未发布的任务进行平摊重排：
        将剩余待发任务按均匀时间间隔（每隔约 30 分钟）重新排布，
        避免中途启动或暂停后积压连发。
        """
        queue = cls.load_queue()
        pending_indices = [i for i, item in enumerate(queue) if item.get("status") == "pending"]
        if not pending_indices:
            return queue

        now = datetime.now()
        if start_dt is None:
            start_dt = now + timedelta(minutes=2)

        count = len(pending_indices)
        logger.info(f"正在对剩余 {count} 条待发任务进行平摊重排（每隔约 {interval_minutes} 分钟发布一条）...")

        for rank, q_idx in enumerate(pending_indices):
            offset = int(rank * interval_minutes)
            jitter = random.randint(-2, 2)
            task_time = start_dt + timedelta(minutes=max(0, offset + jitter))
            queue[q_idx]["scheduled_time"] = task_time.strftime("%Y-%m-%d %H:%M:%S")

        cls.save_queue(queue)
        logger.info(f"重排完成！共 {count} 条待发任务已重新排期，下一条将在 {queue[pending_indices[0]]['scheduled_time']} 发出。")
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

    @classmethod
    def delete_task_media(cls, task_id: str, media_files: list = None) -> int:
        """
        删除单个已发布任务的本地缓存文件及对应目录，立即回收磁盘空间
        """
        import shutil
        freed_bytes = 0
        try:
            # 1. 尝试删除 media_files 列表中记录的具体文件
            if media_files:
                for fpath in media_files:
                    if fpath and os.path.exists(fpath):
                        try:
                            freed_bytes += os.path.getsize(fpath)
                            os.remove(fpath)
                        except Exception as e:
                            logger.warning(f"删除媒体文件 {fpath} 异常: {e}")

            # 2. 检查并删除以该推文 ID 命名的目录（media_cache/<task_id>）
            tweet_dir = os.path.join(MEDIA_CACHE_DIR, str(task_id))
            if os.path.exists(tweet_dir):
                for root, _, files in os.walk(tweet_dir):
                    for file in files:
                        p = os.path.join(root, file)
                        try:
                            if os.path.exists(p):
                                freed_bytes += os.path.getsize(p)
                        except Exception:
                            pass
                shutil.rmtree(tweet_dir, ignore_errors=True)
        except Exception as e:
            logger.error(f"清理任务 #{task_id} 媒体缓存失败: {e}")
        return freed_bytes

    @classmethod
    def cleanup_media_cache(cls, clean_orphans: bool = True) -> Dict[str, Any]:
        """
        全面清理本地媒体缓存以节省磁盘空间：
        1. 清理所有状态为 'published' 的已发布帖子的媒体文件
        2. 清理 media_cache 目录下不属于任何活跃任务（'pending' / 'failed'）的孤立推文目录（如昨日遗留历史文件）
        """
        import shutil

        queue = cls.load_queue()
        active_ids = set(str(item["id"]) for item in queue if item.get("status") in ("pending", "failed"))

        freed_bytes = 0
        cleaned_tasks_count = 0
        cleaned_dirs_count = 0

        # 1. 清理已发布的任务
        for item in queue:
            if item.get("status") == "published":
                task_id = str(item.get("id"))
                files = item.get("media_files") or []
                b = cls.delete_task_media(task_id, files)
                if b > 0:
                    freed_bytes += b
                    cleaned_tasks_count += 1
                item["media_files"] = []

        cls.save_queue(queue)

        # 2. 清理孤立目录（不在当前活跃任务中的全部历史目录）
        if clean_orphans and os.path.exists(MEDIA_CACHE_DIR):
            for entry in os.listdir(MEDIA_CACHE_DIR):
                entry_path = os.path.join(MEDIA_CACHE_DIR, entry)
                if os.path.isdir(entry_path):
                    if entry not in active_ids:
                        dir_bytes = 0
                        for root, _, files in os.walk(entry_path):
                            for f in files:
                                try:
                                    dir_bytes += os.path.getsize(os.path.join(root, f))
                                except Exception:
                                    pass
                        shutil.rmtree(entry_path, ignore_errors=True)
                        freed_bytes += dir_bytes
                        cleaned_dirs_count += 1

        mb_freed = freed_bytes / (1024 * 1024)
        if freed_bytes > 0:
            logger.info(f"🧹 媒体缓存清理完毕！共清理 {cleaned_tasks_count} 个已发任务和 {cleaned_dirs_count} 个历史目录，释放空间: {mb_freed:.2f} MB")
        return {
            "freed_bytes": freed_bytes,
            "mb_freed": mb_freed,
            "cleaned_tasks_count": cleaned_tasks_count,
            "cleaned_dirs_count": cleaned_dirs_count
        }

    @classmethod
    def clear_queue(cls, clear_published: bool = False) -> Dict[str, Any]:
        """
        一键清空待发队列并清理本地图片/视频缓存，释放磁盘空间：
        clear_published: 是否连同已发布的历史记录也清空（默认保留最近的已发布记录用于状态展示）
        """
        queue = cls.load_queue()
        pending_items = [i for i in queue if i.get("status") == "pending"]
        published_items = [i for i in queue if i.get("status") != "pending"]

        freed_bytes = 0
        for item in pending_items:
            task_id = str(item.get("id"))
            files = item.get("media_files") or []
            freed_bytes += cls.delete_task_media(task_id, files)

        if clear_published:
            for item in published_items:
                task_id = str(item.get("id"))
                files = item.get("media_files") or []
                freed_bytes += cls.delete_task_media(task_id, files)
            cls.save_queue([])
        else:
            cls.save_queue(published_items)

        orphan_res = cls.cleanup_media_cache(clean_orphans=True)
        freed_bytes += orphan_res.get("freed_bytes", 0)

        mb_freed = freed_bytes / (1024 * 1024)
        logger.info(f"已清空 {len(pending_items)} 个待发任务，回收磁盘空间 {mb_freed:.2f} MB。")
        return {
            "cleared_pending_count": len(pending_items),
            "freed_mb": mb_freed
        }

    @classmethod
    async def insert_priority_task(cls, tweet, tweet_id: str) -> Dict[str, Any]:
        """
        将用户手动分享的推文作为最高优先级插队任务：
        1. 下载该推文的高清图片或 MP4 视频到本地媒体缓存
        2. 按照选项 A 计算满足冷却安全时间（至少 40 分钟）的最早发布时刻
        3. 插入到待发任务最前端（下一个就发它）
        4. 顺延并平摊排布后续所有待发任务，保持 24 小时均匀分布，发完为止
        5. 将该推文记录到历史去重库，防止日后重复抓取
        """
        tweet_id_str = str(tweet_id)
        queue = cls.load_queue()

        # 检查是否已发布
        existing_item = next((i for i in queue if str(i.get("id")) == tweet_id_str), None)
        if existing_item and existing_item.get("status") == "published":
            return {
                "ok": False,
                "reason": "already_published",
                "item": existing_item,
                "message": f"该推文已于 {existing_item.get('published_at')} 成功发布过，无需重复提交。"
            }

        # 移除已有待发副本（避免重复排期）
        queue = [i for i in queue if str(i.get("id")) != tweet_id_str]

        # 1. 下载媒体资源
        media_info = await cls.download_tweet_media(tweet, tweet_id_str)
        cleaned_text = cls.clean_tweet_text(getattr(tweet, "full_text", "") or getattr(tweet, "text", ""))

        # 2. 确定发帖时间：保持原有发帖节奏（每 30 分钟发一条），不重置时间起点
        now = datetime.now()
        published_items = [i for i in queue if i.get("status") != "pending"]
        other_pending = [i for i in queue if i.get("status") == "pending"]

        if other_pending:
            # 存在已有待发任务时，直接占用原本排在最先的时间槽（下一个就发它）
            existing_slots = [item["scheduled_time"] for item in other_pending]
            priority_time_str = existing_slots[0]

            # 后续原有的待发任务依次顺延占用下一个已有时间槽，保持节奏丝毫不乱
            for rank in range(len(other_pending)):
                if rank + 1 < len(existing_slots):
                    other_pending[rank]["scheduled_time"] = existing_slots[rank + 1]
                else:
                    # 最后一个任务顺延 30 分钟（保持每半小时一条的节奏）
                    try:
                        last_dt = datetime.strptime(existing_slots[-1], "%Y-%m-%d %H:%M:%S")
                    except Exception:
                        last_dt = now + timedelta(minutes=30 * len(other_pending))
                    jitter = random.randint(-2, 2)
                    new_last_time = last_dt + timedelta(minutes=30 + jitter)
                    other_pending[rank]["scheduled_time"] = new_last_time.strftime("%Y-%m-%d %H:%M:%S")
        else:
            # 队列当前为空时，按上次发帖时间顺延 30 分钟（或至少 2 分钟后）
            last_pub = cls.get_last_published_time()
            if last_pub:
                target_dt = last_pub + timedelta(minutes=30)
                if target_dt < now + timedelta(minutes=2):
                    target_dt = now + timedelta(minutes=2)
            else:
                target_dt = now + timedelta(minutes=2)
            priority_time_str = target_dt.strftime("%Y-%m-%d %H:%M:%S")

        user = getattr(tweet, "user", None)
        screen_name = getattr(user, "screen_name", "i") if user else "i"
        user_name = getattr(user, "name", "Twitter User") if user else "Twitter User"

        new_item = {
            "index": 1,
            "id": tweet_id_str,
            "url": f"https://x.com/{screen_name}/status/{tweet_id_str}",
            "author": f"{user_name} (@{screen_name})",
            "text": cleaned_text,
            "media_type": media_info["type"],
            "media_files": media_info["files"],
            "scheduled_time": priority_time_str,
            "status": "pending",
            "published_at": None,
            "error": None,
            "is_priority": True
        }

        full_queue = published_items + [new_item] + other_pending
        for idx, item in enumerate(full_queue):
            item["index"] = idx + 1

        cls.save_queue(full_queue)

        # 4. 同步录入历史去重库
        try:
            from x_home_collector import XHomeCollector, HISTORY_FILE
            collector = XHomeCollector()
            history = collector._load_history()
            history["ids"][tweet_id_str] = now.strftime("%Y-%m-%d %H:%M:%S")
            fp = collector._normalize_text_fingerprint(cleaned_text)
            if len(fp) >= 8:
                history["fingerprints"][fp] = now.strftime("%Y-%m-%d %H:%M:%S")
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        logger.info(f"✅ 优先级插队成功！任务已排期为 #{new_item['index']}，预计发布时间: {new_item['scheduled_time']}")
        return {
            "ok": True,
            "item": new_item,
            "scheduled_time": new_item["scheduled_time"],
            "total_count": len(full_queue),
            "pending_count": len(other_pending) + 1
        }
