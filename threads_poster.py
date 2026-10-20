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
# 静音 httpx/httpcore 的高频轮询日志，防止长期运行撑大 systemd journal 磁盘
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIE_FILE = os.path.join(BASE_DIR, "threads_cookies.json")


class ThreadsPublisher:
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.proxy_settings = {"server": config.PROXY} if config.PROXY and config.PROXY.strip() else None

    def _load_cookies(self):
        """读取并转换 Cookies 为 Playwright 标准格式，智能兼容 JSON、Netscape 及 Key=Value 格式"""
        if not os.path.exists(COOKIE_FILE):
            raise FileNotFoundError(f"未找到 {COOKIE_FILE}，请先确保 Cookie 文件存在！")
        if os.path.getsize(COOKIE_FILE) == 0:
            raise ValueError(f"❌ {COOKIE_FILE} 文件内容为空 (0 字节)！")

        with open(COOKIE_FILE, "r", encoding="utf-8-sig") as f:
            content = f.read().strip()

        formatted = []

        # 格式 1: 标准 JSON 格式 ([ { "name": ..., "value": ... } ])
        if content.startswith("[") or content.startswith("{"):
            try:
                raw = json.loads(content)
                if isinstance(raw, dict):
                    raw = [raw]
                for c in raw:
                    item = {
                        "name": c["name"],
                        "value": c["value"],
                        "domain": c.get("domain", ".threads.net"),
                        "path": c.get("path", "/"),
                        "secure": True
                    }
                    formatted.append(item)
                    if "threads.net" in item["domain"]:
                        c2 = item.copy()
                        c2["domain"] = ".threads.com"
                        formatted.append(c2)
                    elif "threads.com" in item["domain"]:
                        c2 = item.copy()
                        c2["domain"] = ".threads.net"
                        formatted.append(c2)
                return formatted
            except json.JSONDecodeError:
                pass

        # 格式 2: Netscape HTTP Cookie 格式 (# Netscape HTTP Cookie File 或 制表符分隔文本)
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 7:
                domain, flag, path, secure, expiration, name, value = parts[:7]
                item = {
                    "name": name.strip(),
                    "value": value.strip(),
                    "domain": domain.strip(),
                    "path": path.strip(),
                    "secure": secure.strip().upper() == "TRUE"
                }
                formatted.append(item)
                if "threads.net" in item["domain"]:
                    c2 = item.copy()
                    c2["domain"] = ".threads.com"
                    formatted.append(c2)
                elif "threads.com" in item["domain"]:
                    c2 = item.copy()
                    c2["domain"] = ".threads.net"
                    formatted.append(c2)

        if formatted:
            return formatted

        # 格式 3: Header String (name=value; name2=value2;)
        if "=" in content:
            for pair in content.replace("\n", ";").split(";"):
                pair = pair.strip()
                if not pair or "=" not in pair:
                    continue
                k, v = pair.split("=", 1)
                item = {
                    "name": k.strip(),
                    "value": v.strip(),
                    "domain": ".threads.net",
                    "path": "/",
                    "secure": True
                }
                formatted.append(item)
                c2 = item.copy()
                c2["domain"] = ".threads.com"
                formatted.append(c2)

        if not formatted:
            raise ValueError(f"❌ 无法解析 {COOKIE_FILE} 中的 Cookie 内容，请确保是标准的 JSON 或 Netscape 格式！")

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
                await page.wait_for_timeout(3500)

                # 0. 登录态存活检测：检查 Cookie 是否失效或被踢出登录
                is_logged_out = await page.evaluate("""() => {
                    const text = document.body.innerText || '';
                    if (text.includes('Say more with Threads') || text.includes('Continue with Instagram') || text.includes('Log in with Instagram')) {
                        return true;
                    }
                    const hasLoginBtn = Array.from(document.querySelectorAll('a, button, div[role="button"]')).some(el => {
                        const t = (el.innerText || '').trim();
                        return t === 'Log in' || t === '登录' || t === '登入';
                    });
                    const hasUserAvatarOrProfile = !!document.querySelector('a[href*="/@"]');
                    return hasLoginBtn && !hasUserAvatarOrProfile;
                }""")

                if is_logged_out:
                    await page.screenshot(path="debug_cookie_expired.png")
                    err_msg = "❌ Threads Cookie 已失效/过期（页面弹出 Continue with Instagram / Log in 登录拦截窗）！请重新从浏览器导出最新的 threads_cookies.json 替换并重启服务。"
                    logger.error(err_msg)
                    try:
                        from tg_sender import TelegramSender
                        TelegramSender().send_message(
                            "⚠️ <b>Threads 发帖服务异常告警</b>\n\n"
                            "检测到 <b>Threads 登录态已失效 (Cookie 过期)</b>，导致推文无法发布！\n\n"
                            "<b>恢复方法：</b>\n"
                            "1. 在浏览器登录 Threads 并导出最新 <code>threads_cookies.json</code>\n"
                            "2. 替换 VPS 上的 <code>/var/www/x-to-threads-bot/threads_cookies.json</code>\n"
                            "3. 运行 <code>sudo systemctl restart threads-poster</code> 即可自动恢复发帖！"
                        )
                    except Exception as tg_err:
                        logger.warning(f"发送 TG 告警失败: {tg_err}")
                    raise Exception(err_msg)

                # 确保当前处于 Threads 首页，严防点入其他用户的推文评论页
                if "/post/" in page.url:
                    logger.info("检测到当前处于推文详情页，正在返回 Threads 首页...")
                    await page.goto("https://www.threads.com/", timeout=30000, wait_until="domcontentloaded")
                    await page.wait_for_timeout(2000)

                # 1. 打开发帖弹窗（严密锁定 role="dialog" 独立发帖容器）
                dialog = page.locator('div[role="dialog"]').filter(has=page.locator('div[contenteditable="true"]')).first
                
                # 如果尚未打开发帖弹窗，多策略智能唤起
                if await dialog.count() == 0 or not await dialog.is_visible():
                    logger.info("正在打开 Threads 发帖界面...")
                    
                    # 策略 1: 点击左侧边栏专属 "+ New thread" / "Create" 导航按键
                    create_svg = page.locator('svg[aria-label="Create"], svg[aria-label="New thread"], svg:has(title:has-text("Create"))').first
                    if await create_svg.count() > 0:
                        logger.info("尝试点击左侧边栏 Create / New thread 按钮...")
                        create_btn = create_svg.locator('xpath=ancestor::div[@role="button" or @tabindex="0"][1]')
                        try:
                            if await create_btn.count() > 0:
                                await create_btn.evaluate("el => el.click()")
                            else:
                                await create_svg.evaluate("el => el.parentElement.click()")
                        except Exception as e:
                            logger.warning(f"点击侧边栏按键异常: {e}")
                        
                        for _ in range(6):
                            await page.wait_for_timeout(500)
                            if await dialog.count() > 0 and await dialog.is_visible():
                                break

                    # 策略 2: 点击主页信息流顶部的 "What's new?" / "有什么新鲜事？" 输入区域 (必须位于顶部 y < 250)
                    if await dialog.count() == 0 or not await dialog.is_visible():
                        feed_box = page.locator('div:has-text("What\'s new?"), span:has-text("What\'s new?"), div:has-text("有什么新鲜事？")').first
                        if await feed_box.count() > 0 and await feed_box.is_visible():
                            box = await feed_box.bounding_box()
                            if box and box.get("y", 999) < 250:
                                logger.info("尝试点击信息流顶部 What's new 占位框...")
                                try:
                                    await feed_box.evaluate("el => el.click()")
                                except Exception:
                                    pass
                                for _ in range(6):
                                    await page.wait_for_timeout(500)
                                    if await dialog.count() > 0 and await dialog.is_visible():
                                        break

                    # 策略 3: 点击右下角固定的圆形 '+' 悬浮发帖按钮 (通过坐标定位绝对不会点错)
                    if await dialog.count() == 0 or not await dialog.is_visible():
                        logger.info("尝试点击右下角悬浮发帖按钮...")
                        await page.evaluate("""() => {
                            const all = document.querySelectorAll('div[role="button"]');
                            for (const b of all) {
                                const rect = b.getBoundingClientRect();
                                if (rect.x > window.innerWidth * 0.7 && rect.y > window.innerHeight * 0.5 && rect.width < 100 && rect.height < 100) {
                                    b.click();
                                    return;
                                }
                            }
                        }""")
                        for _ in range(6):
                            await page.wait_for_timeout(500)
                            if await dialog.count() > 0 and await dialog.is_visible():
                                break

                    # 策略 4: 聚焦页面主体并按下官方快捷键 'c'
                    if await dialog.count() == 0 or not await dialog.is_visible():
                        logger.info("尝试聚焦页面主体并按快捷键 'c' 唤起发帖弹窗...")
                        try:
                            await page.locator('body').click(force=True, timeout=2000)
                        except Exception:
                            pass
                        await page.keyboard.press("c")
                        for _ in range(6):
                            await page.wait_for_timeout(500)
                            if await dialog.count() > 0 and await dialog.is_visible():
                                break

                # 严格校验：发帖必须在独立的发帖弹窗 (role="dialog") 内完成，杜绝误选其他用户的评论框！
                if await dialog.count() == 0 or not await dialog.is_visible():
                    await page.screenshot(path="debug_open_editor_failed.png")
                    raise Exception("未能成功打开发帖弹窗，已截图 debug_open_editor_failed.png")

                # 检查是否存在多弹窗堆叠（自动清理空白遮挡层）
                all_dialogs = await page.locator('div[role="dialog"]').all()
                if len(all_dialogs) > 1:
                    logger.info(f"检测到 {len(all_dialogs)} 个弹窗，正在检查并关闭空白遮挡层...")
                    for d in all_dialogs:
                        d_text = "".join(await d.locator('div[contenteditable="true"]').all_inner_texts()) if await d.locator('div[contenteditable="true"]').count() > 0 else ""
                        has_media = await d.locator('img, video').count() > 0
                        if not d_text.strip() and not has_media:
                            cancel_btn = d.locator('div[role="button"]:has-text("Cancel"), button:has-text("Cancel"), div[role="button"]:has-text("取消")').first
                            if await cancel_btn.count() > 0:
                                await cancel_btn.click(force=True)
                                await page.wait_for_timeout(500)

                # 2. 定位弹窗内的编辑器并写入推文内容
                logger.info("正在输入推文文案...")
                editor = dialog.locator('div[contenteditable="true"]').first
                try:
                    await editor.focus()
                except Exception:
                    pass
                try:
                    await editor.click(force=True, timeout=3000)
                except Exception:
                    await editor.evaluate("el => el.focus()")
                await page.wait_for_timeout(500)
                await page.keyboard.insert_text(text)
                await page.wait_for_timeout(1000)

                # 3. 上传多媒体附件（严格限制在当前发帖弹窗内）
                if media_files:
                    logger.info(f"正在上传媒体附件 ({len(media_files)} 个文件)...")
                    file_input = dialog.locator('input[type="file"]').first
                    await file_input.set_input_files(media_files)

                    logger.info("文件已提交，正在等待 Threads 前端渲染与转码...")
                    for wait_i in range(25):
                        await page.wait_for_timeout(1000)
                        has_preview = await dialog.locator('img, video').count() > 0
                        if has_preview:
                            logger.info(f"媒体预览已成功渲染 (耗时约 {wait_i+1} 秒)")
                            break
                    await page.wait_for_timeout(2000)

                # 4. 定位精确的【Post/发布】按钮（锁定当前发帖弹窗内的专属提交按键）
                post_candidates = dialog.get_by_role("button", name="Post", exact=True)
                if await post_candidates.count() == 0:
                    post_candidates = dialog.get_by_role("button", name="发布", exact=True)
                if await post_candidates.count() == 0:
                    post_candidates = dialog.locator('div[role="button"]').filter(has_text="Post").filter(has_not_text="Options")
                if await post_candidates.count() == 0:
                    post_candidates = dialog.locator('div[role="button"]').filter(has_text="发布")

                if await post_candidates.count() == 0:
                    await page.screenshot(path="debug_find_post_btn_failed.png")
                    raise Exception("未找到当前发帖弹窗内的 Post 按钮，已截图 debug_find_post_btn_failed.png")

                post_btn = post_candidates.last

                # 判断附件是否含视频：视频上传后 Threads 后台转码较慢，适当延长等待阈值
                is_video = any(f.lower().endswith(('.mp4', '.mov', '.webm', '.mkv')) for f in media_files) if media_files else False
                post_ready_timeout = 150 if is_video else 45
                logger.info(f"等待 Post 按钮就绪（媒体上传与转码完成，类型: {'视频(最长150s)' if is_video else '图片/纯文本(最长45s)'}）...")

                clicked_ok = False
                for attempt in range(post_ready_timeout):
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
                        try:
                            # 1. 尝试 force=True 强制点击，杜绝页面文案/话题联想等浮层拦截 (intercepts pointer events)
                            await post_btn.click(force=True, timeout=5000)
                        except Exception as click_err:
                            logger.warning(f"force click 出现轻微抖动 ({click_err})，采用原生 DOM Click 穿透点击...")
                            # 2. 兜底直接派发原生 DOM 点击事件，100% 杜绝遮挡问题
                            await post_btn.evaluate("el => el.click()")

                        clicked_ok = True
                        break

                if not clicked_ok:
                    await page.screenshot(path="debug_click_failed.png")
                    raise Exception(f"Post 按钮在 {post_ready_timeout} 秒内未变为可发布状态（可能视频转码超时），已截图 debug_click_failed.png")

                logger.info("已提交发布，正在等待 Threads 弹窗关闭与网络入库...")

                # 5. 等待发帖完成响应（发帖弹窗必须真正从界面消失）
                posted = False
                for check_i in range(35):
                    await page.wait_for_timeout(1000)
                    # 检查当前发帖弹窗是否真正关闭
                    if await dialog.count() == 0 or not await dialog.is_visible():
                        logger.info(f"发帖弹窗已成功关闭 (耗时约 {check_i+1} 秒)！")
                        posted = True
                        break

                    # 检查是否有错误提示
                    err_box = dialog.locator('div:has-text("Couldn\'t post"), div:has-text("无法发布")').first
                    if await err_box.count() > 0:
                        raise Exception(f"Threads 界面提示错误: {await err_box.inner_text()}")

                if not posted:
                    await page.screenshot(path="threads_last_post_result.png")
                    raise Exception("发帖弹窗在点击 Post 后仍未消失，可能仍在转码或触发限制，请查看 threads_last_post_result.png")

                # 6. 等待后台媒体网络提交完成（严密监听中英文 Toast 状态与网络静默）
                is_video = any(f.lower().endswith(('.mp4', '.mov', '.webm', '.mkv')) for f in media_files) if media_files else False
                max_wait_seconds = 180 if is_video else 35
                logger.info(f"正在等待媒体后台网络入库完成 (附件类型: {'视频' if is_video else '图片/纯文本'}，最长等待 {max_wait_seconds} 秒)...")

                # 先缓冲等待 2 秒让前端提示框完成挂载
                await page.wait_for_timeout(2000)

                upload_started = False
                upload_finished = False

                for wait_i in range(max_wait_seconds):
                    # 获取页面底部所有提示文案
                    toast_status = await page.evaluate("""() => {
                        const all = Array.from(document.querySelectorAll('div, span, p'));
                        let posting = false;
                        let posted = false;
                        let failed = false;
                        let failMsg = '';

                        for (const el of all) {
                            const text = (el.innerText || "").trim();
                            if (!text) continue;
                            if (text.startsWith('Posting') || text.startsWith('正在发布') || text.startsWith('發布中') || text.startsWith('Uploading') || text.startsWith('正在上傳')) {
                                posting = true;
                            }
                            if (text === 'Posted' || text === '已发布' || text === '已發布' || text.startsWith('View') || text.startsWith('查看')) {
                                posted = true;
                            }
                            if (text.includes("Couldn't post") || text.includes("無法發布") || text.includes("无法发布") || text.includes("出错了") || text.includes("Something went wrong")) {
                                failed = true;
                                failMsg = text;
                            }
                        }
                        return { posting, posted, failed, failMsg };
                    }""")

                    if toast_status.get("failed"):
                        await page.screenshot(path="threads_last_post_failed.png")
                        raise Exception(f"Threads 网页提示发布失败: {toast_status.get('failMsg')}，已截图 threads_last_post_failed.png")

                    if toast_status.get("posting"):
                        upload_started = True
                        if wait_i % 5 == 0:
                            logger.info(f"媒体正在后台上传中 (已等待 {wait_i+2} 秒)...")
                        await page.wait_for_timeout(1000)
                        continue

                    # 如果检测到明确的成功提示
                    if toast_status.get("posted"):
                        logger.info(f"检测到 Threads '已发布' (Posted) 状态提示 (耗时 {wait_i+2} 秒)！")
                        upload_finished = True
                        break

                    # 如果之前已经出现过 posting，现在 posting 消失了且没有失败提示，说明上传已完成
                    if upload_started and not toast_status.get("posting"):
                        logger.info(f"后台媒体上传已结束 (Posting 状态已解除，耗时约 {wait_i+2} 秒)！")
                        upload_finished = True
                        break

                    # 如果尚未检测到 posting，前 10 秒继续轮询等待以防前端延迟
                    if not upload_started and wait_i < 10:
                        await page.wait_for_timeout(1000)
                        continue

                    # 超过 10 秒没有出现 posting 且没有失败，尝试检测网络空闲
                    if not upload_started and wait_i >= 10:
                        try:
                            await page.wait_for_load_state("networkidle", timeout=5000)
                        except Exception:
                            pass
                        upload_finished = True
                        break

                # 额外多等待 3 秒，确保 WebSocket / HTTP 连接完全平稳落库
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


async def run_worker(min_interval_minutes: int = 20):
    """
    后台轮询调度服务：全天 24 小时自动检查并均匀分发队列
    集成两大安全防护机制：
    1. 启动自检与平摊重排：检测到中途启动或积压过期任务时，自动按配置的时间间隔重新排布
    2. 严格的冷却防抖保护：强制两次发帖之间至少间隔 min_interval_minutes 分钟，绝不连发
    """
    logger.info("🚀 Threads 自动发布服务已启动！正在监控队列...")
    logger.info(f"全天 24 小时均匀分发（平均每隔约 {config.POST_INTERVAL_MINUTES} 分钟发布一条），发帖冷却保护设置为: 至少间隔 {min_interval_minutes} 分钟。")
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
        logger.info("⚠️ 检测到队列中存在早于当前时间的待发任务（中途启动或积压），正在重新均匀规划时间段...")
        QueueManager.reschedule_pending_tasks(interval_minutes=config.POST_INTERVAL_MINUTES)

    last_cleanup_dt = datetime.now()

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
                    # 即发即清：发帖成功立即删除该任务的图片与视频文件，释放 VPS 磁盘
                    freed = QueueManager.delete_task_media(task["id"], task.get("media_files", []))
                    logger.info(f"✅ 任务 #{task['index']} 发布成功！已更新状态并清理本地媒体缓存 (释放 {freed/1024/1024:.2f} MB)。")
                else:
                    QueueManager.update_task_status(task["id"], "failed", error="发布异常")
                    logger.warning(f"❌ 任务 #{task['index']} 发布失败。")

            # 3. 每 6 小时自动执行一次全局媒体缓存清理，彻底杜绝孤立文件积压
            if (now_dt - last_cleanup_dt).total_seconds() > 6 * 3600:
                QueueManager.cleanup_media_cache()
                last_cleanup_dt = now_dt

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
    parser.add_argument("--post-now", action="store_true", help="立即发布队列中的下一个待发任务进行实测")
    parser.add_argument("--post-task", type=int, help="立即测试发布指定序号的任务（例如 --post-task 55 重试任务 #55）")
    parser.add_argument("--clean-media", action="store_true", help="全面清理所有已发任务与历史孤立的多媒体文件，释放 VPS 磁盘空间")
    parser.add_argument("--test", type=str, help="直接发布自定义测试文案到 Threads（无需依赖队列）")
    parser.add_argument("--add-tweet", type=str, help="手动传入一个 X 推文链接或 ID，以最高优先级插队排期")
    parser.add_argument("--reschedule", action="store_true", help="启动时先强制重新平摊排布待发任务，再进入 24 小时常驻监听服务")
    parser.add_argument("--interval", type=int, default=20, help="两次发帖之间的最小安全冷却时间（分钟，默认 20）")
    parser.add_argument("--clear-queue", action="store_true", help="一键清空所有尚未发布的任务并删除对应媒体缓存，释放磁盘空间")
    parser.add_argument("--delete", type=int, help="从待发队列中单独删除指定序号的任务（例如 --delete 16）")
    args = parser.parse_args()

    if args.delete is not None:
        target_no = args.delete
        logger.info(f"正在尝试从队列删除任务 #{target_no}...")
        res = QueueManager.delete_task(target_index=target_no)
        if res.get("ok"):
            print(f"✅ 成功从待发队列删除任务 #{res['deleted_index']}！")
            print(f"文案: {res['text'][:60]}...")
            print(f"剩余待发任务: {res['remaining_pending_count']} 条。后续任务已自动向前补位，保持每 30 分钟发布节奏不变。")
            if res.get("freed_mb", 0) > 0:
                print(f"已清理媒体缓存，释放 {res['freed_mb']:.2f} MB。")
        else:
            print(f"❌ 删除失败: {res.get('msg')}")
        return

    if args.clear_queue:
        logger.info("正在清空待发队列与媒体缓存...")
        res = QueueManager.clear_queue()
        print(f"✅ 已成功清空 {res['cleared_pending_count']} 个未发布任务，并释放了 {res['freed_mb']:.2f} MB 磁盘空间！")
        print("当前待发队列已重置为空，等待下一次定时抓取或手动抓取。")
        return

    if args.clean_media:
        logger.info("正在执行本地媒体缓存清理...")
        result = QueueManager.cleanup_media_cache()
        print(f"✅ 媒体缓存清理完成！已清理 {result['cleaned_tasks_count']} 个已发任务和 {result['cleaned_dirs_count']} 个历史目录，共释放 {result['mb_freed']:.2f} MB 磁盘空间。")
        return

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
        QueueManager.reschedule_pending_tasks(interval_minutes=config.POST_INTERVAL_MINUTES)
        print("\n已完成平摊重排，队列计划如下：")
        print_queue_status()
        logger.info("已完成重排，正在进入全天 24 小时自动发布常驻服务...")

    if args.post_task is not None:
        target_no = args.post_task
        queue = QueueManager.load_queue()
        matching = [i for i in queue if i.get("index") == target_no]
        if not matching:
            print(f"❌ 队列中未找到序号为 #{target_no} 的任务。请运行 --status 检查队列。")
            return
        target = matching[0]
        print(f"🚀 正在单独测试发布任务 #{target['index']} (当前状态: {target.get('status')})...")
        print(f"文案: {target['text'][:60]}... | 附件: {len(target.get('media_files', []))} 个")
        pub = ThreadsPublisher(headless=True)
        res = asyncio.run(pub.post_thread(target["text"], target.get("media_files", [])))
        if res:
            QueueManager.update_task_status(target["id"], "published")
            freed = QueueManager.delete_task_media(target["id"], target.get("media_files", []))
            print(f"✅ 任务 #{target['index']} 立即发布成功！已在 Threads 上线并清理本地媒体缓存 (释放 {freed/1024/1024:.2f} MB)！")
        else:
            print(f"❌ 任务 #{target['index']} 发布失败，请查看日志。")
        return

    if args.post_now:
        queue = QueueManager.load_queue()
        targets = [i for i in queue if i.get("status") == "pending"]
        if not targets:
            targets = [i for i in queue if i.get("status") == "failed"]
        if not targets:
            print("当前队列中没有待发布或失败的任务。")
            return
        target = targets[0]
        print(f"立即测试发布任务 #{target['index']} (状态: {target.get('status')}): {target['text'][:40]}...")
        pub = ThreadsPublisher(headless=True)
        res = asyncio.run(pub.post_thread(target["text"], target.get("media_files", [])))
        if res:
            QueueManager.update_task_status(target["id"], "published")
            freed = QueueManager.delete_task_media(target["id"], target.get("media_files", []))
            print(f"✅ 立即发布成功！已在 Threads 上线并清理本地媒体缓存 (释放 {freed/1024/1024:.2f} MB)！")
        else:
            print("❌ 发布失败，请查看日志。")
        return

    # 默认模式：后台守护常驻，按计划时间发布
    asyncio.run(run_worker(min_interval_minutes=args.interval))


if __name__ == "__main__":
    main()
