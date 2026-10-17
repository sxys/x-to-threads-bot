import os
import sys
import time
import json
import asyncio
import logging
import argparse
from datetime import datetime

# Windows 终端编码保护
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from playwright.async_api import async_playwright, Page
from x_config import config
from queue_manager import QueueManager, QUEUE_FILE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [ThreadsPoster] %(message)s"
)
logger = logging.getLogger("ThreadsPoster")
COOKIE_FILE = "threads_cookies.json"


class ThreadsPublisher:
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.proxy_settings = {"server": config.PROXY} if config.PROXY and config.PROXY.strip() else None

    def _load_cookies(self):
        """读取并转换 Cookies 为 Playwright 标准格式"""
        if not os.path.exists(COOKIE_FILE):
            raise FileNotFoundError(f"未找到 {COOKIE_FILE}，请先确保 Cookie 文件存在！")
        with open(COOKIE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)

        formatted = []
        for c in raw:
            item = {
                "name": c["name"],
                "value": c["value"],
                "domain": c.get("domain", ".threads.net"),
                "path": c.get("path", "/"),
                "secure": True
            }
            formatted.append(item)
            # 同时也为 .threads.com 加上一份
            if "threads.net" in item["domain"]:
                c2 = item.copy()
                c2["domain"] = ".threads.com"
                formatted.append(c2)
        return formatted

    async def post_thread(self, text: str, media_files: list = None) -> bool:
        """
        核心发布逻辑：利用 Playwright 操作网页端完成图文/视频发布
        """
        media_files = [os.path.abspath(f) for f in (media_files or []) if os.path.exists(f)]
        cookies = self._load_cookies()

        async with async_playwright() as p:
            logger.info("正在启动 Chromium 浏览器内核...")
            browser = await p.chromium.launch(
                headless=self.headless,
                proxy=self.proxy_settings,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-blink-features=AutomationControlled"
                ]
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 900}
            )
            await context.add_cookies(cookies)
            page: Page = await context.new_page()

            try:
                logger.info("正在导航至 https://www.threads.com/ ...")
                await page.goto("https://www.threads.com/", timeout=45000, wait_until="domcontentloaded")
                await page.wait_for_timeout(3000)

                # 1. 打开发帖弹窗
                dialog = page.locator('div[role="dialog"]').first
                if await dialog.count() == 0 or not await dialog.is_visible():
                    logger.info("正在打开 Threads 发帖界面...")
                    new_btn = page.locator('div[role="button"]:has-text("New thread")').first
                    if await new_btn.count() == 0:
                        new_btn = page.get_by_role("button", name="New thread")
                    if await new_btn.count() == 0:
                        new_btn = page.locator('svg[aria-label="New thread"]').first

                    await new_btn.click()
                    await page.wait_for_timeout(2000)
                    dialog = page.locator('div[role="dialog"]').first

                if await dialog.count() == 0:
                    await page.screenshot(path="debug_open_editor_failed.png")
                    raise Exception("未能成功打开发帖弹窗，已截图 debug_open_editor_failed.png")

                # 2. 定位编辑器并写入推文内容
                logger.info("正在输入推文文案...")
                editor = dialog.locator('div[contenteditable="true"]').first
                await editor.click()
                await page.wait_for_timeout(500)
                await page.keyboard.insert_text(text)
                await page.wait_for_timeout(1000)

                # 3. 上传多媒体附件（图片或视频）
                if media_files:
                    logger.info(f"正在上传媒体附件 ({len(media_files)} 个文件)...")
                    file_input = dialog.locator('input[type="file"]').first
                    await file_input.set_input_files(media_files)

                    logger.info("文件已提交，正在等待 Threads 前端渲染与转码...")
                    for wait_i in range(20):
                        await page.wait_for_timeout(1000)
                        has_preview = await dialog.locator('img, video').count() > 0
                        if has_preview:
                            logger.info(f"媒体预览已成功渲染 (耗时约 {wait_i+1} 秒)")
                            break
                    await page.wait_for_timeout(2000)

                # 4. 定位精确的【Post】发布按钮（严格过滤排除 Post Options）
                post_btn = dialog.get_by_role("button", name="Post", exact=True)
                if await post_btn.count() == 0:
                    post_btn = dialog.locator('div[role="button"]').filter(has_text="Post").filter(has_not_text="Options")

                if await post_btn.count() == 0:
                    await page.screenshot(path="debug_find_post_btn_failed.png")
                    raise Exception("未找到严格匹配的 Post 按钮，已截图 debug_find_post_btn_failed.png")

                # 等待按钮处于就绪状态（媒体上传与转码完成时，aria-disabled 为空且 opacity 接近 1）
                logger.info("等待 Post 按钮就绪（媒体上传与转码完成）...")
                clicked_ok = False
                for attempt in range(45):
                    await page.wait_for_timeout(1000)
                    status = await post_btn.evaluate("""el => {
                        const s = window.getComputedStyle(el);
                        return {
                            ariaDisabled: el.getAttribute('aria-disabled'),
                            disabled: el.disabled,
                            opacity: parseFloat(s.opacity || "1"),
                            pointerEvents: s.pointerEvents
                        };
                    }""")
                    if status.get("ariaDisabled") != "true" and not status.get("disabled") and status.get("opacity") >= 0.8:
                        logger.info(f"Post 按钮已进入就绪状态 (耗时约 {attempt+1} 秒)，执行真实点击...")
                        await post_btn.click()
                        clicked_ok = True
                        break

                if not clicked_ok:
                    await page.screenshot(path="debug_click_failed.png")
                    raise Exception("Post 按钮在 45 秒内未变为可发布状态（可能媒体转码超时），已截图 debug_click_failed.png")

                logger.info("已提交发布，正在等待 Threads 弹窗关闭与网络入库...")

                # 5. 等待发帖弹窗关闭
                posted = False
                for check_i in range(25):
                    await page.wait_for_timeout(1000)
                    if await page.locator('div[role="dialog"]').count() == 0:
                        logger.info(f"发帖弹窗已关闭 (耗时约 {check_i+1} 秒)！")
                        posted = True
                        break

                if not posted:
                    await page.screenshot(path="threads_last_post_result.png")
                    raise Exception("发帖弹窗在点击 Post 后仍未消失，可能触发了 Meta 限制，请查看 threads_last_post_result.png")

                # 6. 等待后台媒体网络提交完成（等待页面底部的 'Posting...' 提示彻底完成）
                logger.info("正在等待后台媒体网络入库完成 (检测 Posting 提示)...")
                for wait_toast in range(30):
                    has_toast = await page.evaluate("""() => {
                        const all = Array.from(document.querySelectorAll('div, span'));
                        return all.some(el => (el.innerText || "").trim().startsWith('Posting'));
                    }""")
                    if not has_toast:
                        logger.info("后台媒体网络已全部提交完毕！")
                        break
                    await page.wait_for_timeout(1000)

                await page.wait_for_timeout(3000)
                await page.screenshot(path="threads_last_post_result.png")
                logger.info("✅ 帖子已成功在 Threads 上线！")
                return True

            except Exception as e:
                logger.error(f"发帖过程发生异常: {e}", exc_info=True)
                return False
            finally:
                await browser.close()


def print_queue_status():
    """打印当前队列任务清单与执行进度"""
    queue = QueueManager.load_queue()
    if not queue:
        print("当前没有任何任务队列。请先运行 x_top_bot.py 抓取帖子。")
        return

    print("\n================ 当前 Threads 待发队列 ================")
    published_count = 0
    pending_count = 0
    for item in queue:
        status_icon = "[待发]" if item["status"] == "pending" else ("[已发]" if item["status"] == "published" else "[失败]")
        if item["status"] == "published":
            published_count += 1
        elif item["status"] == "pending":
            pending_count += 1
        media_tag = f"[{item['media_type'].upper()}]" if item["media_type"] != "none" else "[纯文本]"
        print(f"#{item['index']:02d} | 计划时间: {item['scheduled_time']} | {status_icon} | {media_tag} {item['text'][:40]}...")

    print(f"-------------------------------------------------------")
    print(f"总计: {len(queue)} 条 | 已发布: {published_count} 条 | 待发布: {pending_count} 条\n")


async def run_worker(min_interval_minutes: int = 40):
    """
    后台轮询调度服务：全天 24 小时自动检查并均匀分发队列
    集成两大安全防护机制：
    1. 启动自检与平摊重排：检测到中途启动或积压过期任务时，自动按剩余时间均匀重新排布
    2. 严格的冷却防抖保护：强制两次发帖之间至少间隔 min_interval_minutes 分钟，绝不连发
    """
    logger.info("🚀 Threads 自动发布服务已启动！正在监控队列...")
    logger.info(f"全天 24 小时均匀分发（平均每隔约 70 分钟发布一条），发帖冷却保护设置为: 至少间隔 {min_interval_minutes} 分钟。")
    publisher = ThreadsPublisher(headless=True)

    # 启动 Telegram 双向互动监听（支持在 Telegram 发送 X 链接以最高优先级插队发帖）
    try:
        from tg_listener import TelegramListener
        tg_listener = TelegramListener()
        asyncio.create_task(tg_listener.run_listener())
        logger.info("🤖 Telegram 双向互动监听器已成功挂载！可在 Telegram 直接发 X 链接插队。")
    except Exception as e:
        logger.warning(f"Telegram 互动监听服务挂载失败: {e}")

    # 启动自检：如果存在任何计划时间早于当前时间的待发任务，立即在今日剩余时间内均匀重排
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    queue = QueueManager.load_queue()
    has_overdue = any(i.get("status") == "pending" and i.get("scheduled_time", "") < now_str for i in queue)
    if has_overdue:
        logger.info("⚠️ 检测到队列中存在早于当前时间的待发任务（中途启动或积压），正在重新均匀规划今日剩余时间段...")
        QueueManager.reschedule_pending_tasks()

    while True:
        try:
            now_dt = datetime.now()

            # 1. 发帖冷却保护检查
            last_pub_dt = QueueManager.get_last_published_time()
            if last_pub_dt:
                elapsed_seconds = (now_dt - last_pub_dt).total_seconds()
                min_interval_seconds = min_interval_minutes * 60
                if elapsed_seconds < min_interval_seconds:
                    wait_seconds = min_interval_seconds - elapsed_seconds
                    logger.info(f"⏳ [冷却防抖中] 距离上次发帖仅过去 {elapsed_seconds/60:.1f} 分钟（要求至少 {min_interval_minutes} 分钟）。将在 {wait_seconds/60:.1f} 分钟后继续检查...")
                    await asyncio.sleep(min(wait_seconds, 60))
                    continue

            # 2. 获取下一个已到达计划时间的待发任务
            task = QueueManager.get_next_pending_task()
            if task:
                logger.info(f"⏰ 检测到到达计划时间的任务: #{task['index']} (计划: {task['scheduled_time']})")
                logger.info(f"文案: {task['text'][:50]}... | 媒体: {task['media_type']} ({len(task['media_files'])} 文件)")

                success = await publisher.post_thread(
                    text=task["text"],
                    media_files=task["media_files"]
                )

                if success:
                    QueueManager.update_task_status(task["id"], "published")
                    logger.info(f"✅ 任务 #{task['index']} 发布成功并已更新队列状态！")
                else:
                    QueueManager.update_task_status(task["id"], "failed", error="发布异常")
                    logger.warning(f"❌ 任务 #{task['index']} 发布失败。")

            # 每 60 秒检查一次队列
            await asyncio.sleep(60)

        except (KeyboardInterrupt, SystemExit):
            logger.info("服务接收到终止信号，已安全退出。")
            break
        except Exception as e:
            logger.error(f"调度循环异常: {e}", exc_info=True)
            await asyncio.sleep(60)


def main():
    parser = argparse.ArgumentParser(description="Threads 自动定时均匀发布机器人")
    parser.add_argument("--status", action="store_true", help="查看当前待发队列详情与进度")
    parser.add_argument("--post-now", action="store_true", help="立即发布队列中的第一个待发任务进行实测")
    parser.add_argument("--test", type=str, help="直接发布自定义测试文案到 Threads（无需依赖队列）")
    parser.add_argument("--add-tweet", type=str, help="手动传入一个 X 推文链接或 ID，以最高优先级插队排期")
    parser.add_argument("--reschedule", action="store_true", help="启动时先强制重新平摊排布待发任务，再进入 24 小时常驻监听服务")
    parser.add_argument("--interval", type=int, default=40, help="两次发帖之间的最小安全冷却时间（分钟，默认 40）")
    args = parser.parse_args()

    if args.add_tweet:
        import re
        from x_home_collector import XHomeCollector
        raw = args.add_tweet.strip()
        m = re.search(r'(\d{10,})', raw)
        if not m:
            print("❌ 无法从输入中提取有效的推文 ID，请输入形如 https://x.com/user/status/123456789 或纯数字 ID。")
            return
        tweet_id = m.group(1)
        print(f"🚀 正在拉取推文 #{tweet_id} 并下载媒体附件...")

        async def _do_add():
            collector = XHomeCollector()
            await collector._login()
            tweet = await collector.client.get_tweet_by_id(tweet_id)
            if not tweet:
                print("❌ 获取推文失败，请确认推文 ID 正确且属于公开账号。")
                return
            res = await QueueManager.insert_priority_task(tweet, tweet_id)
            if res.get("ok"):
                print(f"✅ 成功插入最高优先级！排期在 #{res['item']['index']} (预计发布时刻: {res['scheduled_time']})")
                print_queue_status()
            else:
                print(f"ℹ️ {res.get('message', '处理失败')}")

        asyncio.run(_do_add())
        return

    if args.test:
        text = args.test.strip()
        print(f"🚀 正在测试直接发布单条文案到 Threads: {text}")
        pub = ThreadsPublisher(headless=True)
        res = asyncio.run(pub.post_thread(text))
        if res:
            print("✅ 恭喜！测试帖子已成功在 Threads 上线！请前往个人主页确认。")
        else:
            print("❌ 发布失败，请查看日志排查。")
        return

    if args.status:
        print_queue_status()
        return

    if args.reschedule:
        QueueManager.reschedule_pending_tasks()
        print("\n已完成 24 小时平摊重排，队列计划如下：")
        print_queue_status()
        logger.info("已完成重排，正在进入全天 24 小时自动发布常驻服务...")

    if args.post_now:
        queue = QueueManager.load_queue()
        pending = [i for i in queue if i["status"] == "pending"]
        if not pending:
            print("当前队列中没有待发布的任务。")
            return
        target = pending[0]
        print(f"立即测试发布任务 #{target['index']}: {target['text'][:40]}...")
        pub = ThreadsPublisher(headless=True)
        res = asyncio.run(pub.post_thread(target["text"], target["media_files"]))
        if res:
            QueueManager.update_task_status(target["id"], "published")
            print("✅ 立即发布成功！已在 Threads 上线！")
        else:
            print("❌ 发布失败，请查看日志。")
        return

    # 默认模式：后台守护常驻，按计划时间发布
    asyncio.run(run_worker(min_interval_minutes=args.interval))


if __name__ == "__main__":
    main()
