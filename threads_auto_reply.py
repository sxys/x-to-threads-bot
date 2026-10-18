import os
import re
import json
import random
import logging
import asyncio
import argparse
from datetime import datetime
from typing import List, Dict, Any, Optional
from playwright.async_api import async_playwright, Page
from x_config import config
from tg_sender import TelegramSender

COOKIE_FILE = "threads_cookies.json"
REPLIED_HISTORY_FILE = "replied_comments.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [ThreadsAutoReply] %(message)s"
)
logger = logging.getLogger("ThreadsAutoReply")

# 默认亲和、赞同的简短回复短语库
DEFAULT_REPLY_PHRASES = [
    "你说的很对",
    "我很赞同你的观点",
    "确实是这样",
    "说的在理",
    "事情本来就是如此",
    "精辟，我也是这么觉得的",
    "确实，分析得很到位",
    "说得太对了",
    "赞同，很有道理",
    "所言极是",
    "深有同感"
]


class ThreadsAutoReplier:
    def __init__(
        self,
        headless: bool = True,
        max_replies_per_post: int = 2,
        max_comments_threshold: int = 10,
        check_posts_limit: int = 5,
        custom_phrases: Optional[List[str]] = None
    ):
        self.headless = headless
        self.max_replies_per_post = max_replies_per_post
        self.max_comments_threshold = max_comments_threshold
        self.check_posts_limit = check_posts_limit
        self.phrases = custom_phrases or DEFAULT_REPLY_PHRASES
        self.proxy_settings = {"server": config.PROXY} if config.PROXY and config.PROXY.strip() else None

    def _load_cookies(self):
        """读取并转换 Cookies 为 Playwright 格式"""
        if not os.path.exists(COOKIE_FILE):
            raise FileNotFoundError(f"未找到 {COOKIE_FILE}，请先配置 Threads 登录 Cookie！")
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
            if "threads.net" in item["domain"]:
                c2 = item.copy()
                c2["domain"] = ".threads.com"
                formatted.append(c2)
        return formatted

    @staticmethod
    def load_replied_history() -> Dict[str, Any]:
        """读取已回复评论记录，防止对同一条评论重复回复"""
        if not os.path.exists(REPLIED_HISTORY_FILE):
            return {}
        try:
            with open(REPLIED_HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def save_replied_history(history: Dict[str, Any]):
        """持久化保存已回复评论记录"""
        try:
            with open(REPLIED_HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存已回复历史记录失败: {e}")

    @staticmethod
    def _comment_key(post_url: str, comment_author: str, comment_snippet: str) -> str:
        """生成评论唯一特征键"""
        p_id = re.search(r'/post/([A-Za-z0-9_-]+)', post_url)
        post_id = p_id.group(1) if p_id else post_url
        clean_text = re.sub(r'\s+', '', comment_snippet)[:30]
        return f"{post_id}_{comment_author}_{clean_text}"

    async def patrol_and_reply(self, dry_run: bool = False) -> Dict[str, Any]:
        """
        核心巡查主流程：
        1. 启动 Chromium 并注入 Threads Cookie 会话
        2. 定位到当前登录账号的个人主页 (Profile)
        3. 提取最新的若干条自己发布的帖子链接
        4. 逐一巡视每个帖子的评论区：
           - 若评论数为 0 或 大于设定阈值（默认 10 条），则跳过
           - 若评论较少（1~10 条之间），筛选出未曾回复过的真实粉丝留言
           - 随机挑选 1~2 条，从亲和短语库中随机挑选进行拟人回复
           - 记录回复历史，并在每次回复间休眠防风控
        """
        cookies = self._load_cookies()
        replied_history = self.load_replied_history()
        summary = {
            "posts_checked": 0,
            "posts_eligible": 0,
            "replies_posted": 0,
            "details": []
        }

        async with async_playwright() as p:
            logger.info("正在启动 Chromium 浏览器巡查内核...")
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
                # 1. 访问 Threads 首页并定位自己的个人资料链接
                logger.info("正在导航至 https://www.threads.com/ ...")
                await page.goto("https://www.threads.com/", timeout=45000, wait_until="domcontentloaded")
                await page.wait_for_timeout(3000)

                # 获取自己的用户名与个人主页 URL
                logger.info("正在获取当前登录账号的个人主页信息...")
                my_username = await page.evaluate("""() => {
                    const links = Array.from(document.querySelectorAll('a'));
                    // 导航栏通常包含指向 /@username 的个人主页链接
                    for (const a of links) {
                        const href = a.getAttribute('href') || '';
                        if (href.startsWith('/@') && !href.includes('/post/') && !href.includes('/activity')) {
                            const match = href.match(/^\\/@([A-Za-z0-9._]+)/);
                            if (match) return match[1];
                        }
                    }
                    return null;
                }""")

                if not my_username:
                    # 尝试点击导航栏 Profile 图标
                    profile_btn = page.locator('svg[aria-label="Profile"]').first
                    if await profile_btn.count() > 0:
                        parent_link = profile_btn.locator('xpath=ancestor::a').first
                        href = await parent_link.get_attribute("href") if await parent_link.count() > 0 else ""
                        m = re.search(r'/@([A-Za-z0-9._]+)', href)
                        if m:
                            my_username = m.group(1)

                if not my_username:
                    logger.warning("未能通过导航栏自动嗅探到用户名，尝试从 Cookie 中的 ds_user_id 匹配...")
                    await page.screenshot(path="debug_profile_not_found.png")
                    # 兜底直接点击 Profile 图标跳转
                    profile_icon = page.locator('svg[aria-label="Profile"]').first
                    if await profile_icon.count() > 0:
                        await profile_icon.click()
                        await page.wait_for_timeout(3000)
                        cur_url = page.url
                        m = re.search(r'/@([A-Za-z0-9._]+)', cur_url)
                        if m:
                            my_username = m.group(1)

                if not my_username:
                    raise Exception("无法解析当前账号的个人主页 URL，请检查登录 Cookie 是否失效。")

                logger.info(f"✅ 成功定位当前博主账号: @{my_username}")
                profile_url = f"https://www.threads.com/@{my_username}"

                # 2. 跳转进入博主个人主页
                logger.info(f"正在访问个人主页: {profile_url} ...")
                await page.goto(profile_url, timeout=30000, wait_until="domcontentloaded")
                await page.wait_for_timeout(4000)

                # 3. 提取博主最近发布的帖子链接
                post_links = await page.evaluate("""(myUser) => {
                    const links = Array.from(document.querySelectorAll('a'));
                    const urls = [];
                    for (const a of links) {
                        const href = a.getAttribute('href') || '';
                        if (href.includes(`/@${myUser}/post/`)) {
                            const full = href.startsWith('http') ? href : `https://www.threads.com${href}`;
                            // 去除 url 后面可能附带的查询参数
                            const clean = full.split('?')[0];
                            if (!urls.includes(clean)) {
                                urls.push(clean);
                            }
                        }
                    }
                    return urls;
                }""", my_username)

                if not post_links:
                    logger.info("在个人主页未检测到发布的帖子（或页面加载中），尝试滚动页面重新提取...")
                    await page.mouse.wheel(0, 1000)
                    await page.wait_for_timeout(2000)
                    post_links = await page.evaluate("""(myUser) => {
                        const links = Array.from(document.querySelectorAll('a'));
                        const urls = [];
                        for (const a of links) {
                            const href = a.getAttribute('href') || '';
                            if (href.includes('/post/')) {
                                const full = href.startsWith('http') ? href : `https://www.threads.com${href}`;
                                const clean = full.split('?')[0];
                                if (!urls.includes(clean)) {
                                    urls.push(clean);
                                }
                            }
                        }
                        return urls;
                    }""", my_username)

                post_links = post_links[:self.check_posts_limit]
                logger.info(f"共扫描到个人最新发布的 {len(post_links)} 条帖子，准备开始巡查评论区...")

                # 4. 逐一巡视每个帖子
                for p_idx, post_url in enumerate(post_links, 1):
                    logger.info(f"\n--------------------------------------------------")
                    logger.info(f"[{p_idx}/{len(post_links)}] 正在检查帖子: {post_url}")
                    summary["posts_checked"] += 1

                    await page.goto(post_url, timeout=30000, wait_until="domcontentloaded")
                    await page.wait_for_timeout(3000)

                    # 提取该帖下的所有评论与作者信息
                    comments_data = await page.evaluate("""(myUser) => {
                        // 寻找主帖之后的所有回复容器
                        // Threads 详情页每个独立评论通常为一个带有包含用户名的文章或容器
                        const items = [];
                        // 查找所有的回复块 (排除置顶的主帖)
                        const allArticles = Array.from(document.querySelectorAll('div[data-pressable-container="true"], article, div[role="article"]'));

                        // 如果没找到特定结构，通过用户名链接与文字结构分析
                        const userLinks = Array.from(document.querySelectorAll('a[href^="/@"]'));
                        return {
                            pageTitle: document.title,
                            articleCount: allArticles.length
                        };
                    }""", my_username)

                    # 更稳定地从 DOM 中解析评论：
                    # Threads post 页面中，主帖作者通常位于首部，后续每个评论有 Reply / 气泡 按钮
                    comments = await page.evaluate("""(myUser) => {
                        const results = [];
                        // 查找所有具备交互按钮或用户名的评论条目
                        // 在 Threads 详情页中，主帖和每条回复都有对应的操作栏（赞、评论、转帖）
                        const actionBars = Array.from(document.querySelectorAll('svg[aria-label="Reply"], svg[aria-label="回复"]'));

                        // 第一个 Reply 通常是主帖的回复按钮，后面的对应各条评论
                        actionBars.forEach((svg, idx) => {
                            if (idx === 0) return; // 跳过主贴自身的回复按钮

                            // 向上查找该评论的卡片容器
                            let container = svg.closest('div[data-pressable-container="true"]') || svg.closest('article') || svg.closest('div[style*="flex"]');
                            let author = "unknown";
                            let text = "";

                            if (container) {
                                const uLink = container.querySelector('a[href^="/@"]');
                                if (uLink) {
                                    const match = uLink.getAttribute('href').match(/^\\/@([A-Za-z0-9._]+)/);
                                    if (match) author = match[1];
                                }
                                const spans = Array.from(container.querySelectorAll('span[dir="auto"], div[dir="auto"]'));
                                for (const sp of spans) {
                                    const t = (sp.innerText || "").trim();
                                    if (t && t !== author && !t.includes('小时') && !t.includes('天') && !t.includes('分钟')) {
                                        text = t;
                                        break;
                                    }
                                }
                            }

                            results.push({
                                index: idx,
                                author: author,
                                text: text
                            });
                        });
                        return results;
                    }""", my_username)

                    total_comments_count = len(comments)
                    logger.info(f"该帖子检测到评论数: {total_comments_count} 条")

                    # 条件过滤：只在评论很少（1 ~ max_comments_threshold 条之间，默认 <= 10）时才进行回复
                    if total_comments_count == 0:
                        logger.info("ℹ️ 当前帖子暂无粉丝评论，跳过。")
                        continue

                    if total_comments_count > self.max_comments_threshold:
                        logger.info(f"ℹ️ 该帖子评论数 ({total_comments_count}) 已超过上限 ({self.max_comments_threshold} 条)，互动已足够活跃，无需自动回复。")
                        continue

                    summary["posts_eligible"] += 1

                    # 筛选出属于真实粉丝且尚未回复过的评论
                    candidates = []
                    for c in comments:
                        c_author = c["author"]
                        c_text = c["text"]

                        # 1. 绝对不回复博主自己
                        if c_author == my_username or c_author == "unknown":
                            continue

                        # 2. 检查去重记录库
                        c_key = self._comment_key(post_url, c_author, c_text)
                        if c_key in replied_history:
                            logger.info(f"⏩ 评论已被回复过 (@{c_author}: '{c_text[:15]}...')，跳过。")
                            continue

                        candidates.append({
                            "key": c_key,
                            "index": c["index"],
                            "author": c_author,
                            "text": c_text
                        })

                    if not candidates:
                        logger.info("本帖所有现有粉丝评论均已回复过，跳过。")
                        continue

                    # 每个帖子只随机回复 1~2 个未回复的粉丝
                    target_count = min(self.max_replies_per_post, len(candidates))
                    selected_candidates = random.sample(candidates, target_count)
                    logger.info(f"🎯 选定回复其中 {len(selected_candidates)} 条粉丝评论：")

                    for target in selected_candidates:
                        chosen_phrase = random.choice(self.phrases)
                        logger.info(f"👉 准备回复 @{target['author']} ('{target['text'][:20]}...'): \"{chosen_phrase}\"")

                        if dry_run:
                            logger.info(f"[DRY-RUN 演示模式] 拟回复成功，已跳过真实点击。")
                            summary["replies_posted"] += 1
                            continue

                        # 定位对应的 Reply 按钮并点击
                        reply_buttons = page.locator('svg[aria-label="Reply"], svg[aria-label="回复"]')
                        btn_count = await reply_buttons.count()
                        if target["index"] >= btn_count:
                            logger.warning(f"未能正确定位第 {target['index']} 个评论的回复按钮，跳过。")
                            continue

                        target_btn = reply_buttons.nth(target["index"])
                        await target_btn.click()
                        await page.wait_for_timeout(1500)

                        # 定位回复输入框
                        # 弹窗内或行内的 contenteditable 输入框
                        editor = page.locator('div[role="dialog"] div[contenteditable="true"], div[contenteditable="true"]').last
                        if await editor.count() == 0:
                            logger.warning("未找到回复输入框，跳过本条。")
                            continue

                        await editor.click()
                        await page.wait_for_timeout(500)
                        # 模拟真人输入文字
                        await page.keyboard.insert_text(chosen_phrase)
                        await page.wait_for_timeout(1500)

                        # 点击 Post / Reply 发送按钮
                        post_btn = page.locator('div[role="dialog"] div[role="button"]:has-text("Post"), div[role="button"]:has-text("Post"), div[role="button"]:has-text("发布"), div[role="button"]:has-text("Reply")').last
                        if await post_btn.count() == 0:
                            logger.warning("未找到发送按钮，尝试回车发送...")
                            await page.keyboard.press("Control+Enter")
                        else:
                            await post_btn.click()

                        # 等待发送网络提交完成
                        await page.wait_for_timeout(4000)

                        # 记录到已回复持久化库
                        replied_history[target["key"]] = {
                            "post_url": post_url,
                            "comment_author": target["author"],
                            "comment_text": target["text"],
                            "my_reply": chosen_phrase,
                            "replied_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        }
                        self.save_replied_history(replied_history)

                        summary["replies_posted"] += 1
                        summary["details"].append({
                            "author": target["author"],
                            "reply": chosen_phrase,
                            "post_url": post_url
                        })
                        logger.info(f"✅ 成功回复 @{target['author']}！已记入去重库。")

                        # 防风控休眠 12~25 秒
                        jitter_wait = random.randint(12, 25)
                        logger.info(f"⏳ [防风控拟人间隔] 等待 {jitter_wait} 秒后再进行下一项操作...")
                        await page.wait_for_timeout(jitter_wait * 1000)

                logger.info("\n================ 巡查与互动执行完毕 ================")
                logger.info(f"扫描帖子: {summary['posts_checked']} 篇 | 符合条件: {summary['posts_eligible']} 篇 | 成功回复: {summary['replies_posted']} 条")

                # 5. Telegram 消息通知（若有回复产生）
                if summary["replies_posted"] > 0 and config.TG_BOT_TOKEN and config.TG_CHAT_ID:
                    try:
                        sender = TelegramSender()
                        lines = [f"• 回复 @{d['author']}: <i>\"{d['reply']}\"</i>" for d in summary["details"][:5]]
                        tg_msg = (
                            f"💬 <b>【Threads 粉丝评论自动互动汇报】</b>\n"
                            f"────────────────────────\n"
                            f"巡查帖子: <b>{summary['posts_checked']}</b> 篇\n"
                            f"成功互动: <b>{summary['replies_posted']}</b> 位粉丝留言\n\n"
                            f"<b>互动明细</b>：\n" + "\n".join(lines) + "\n"
                            f"────────────────────────\n"
                            f"账号活跃度已提升，已自动记录防重。"
                        )
                        sender.send_message(tg_msg)
                    except Exception as tg_err:
                        logger.warning(f"发送 Telegram 互动汇报失败: {tg_err}")

                return summary

            except Exception as e:
                logger.error(f"巡查过程发生异常: {e}", exc_info=True)
                await page.screenshot(path="threads_auto_reply_error.png")
                return summary
            finally:
                await browser.close()


def main():
    parser = argparse.ArgumentParser(description="Threads 主页博主帖子评论自动巡查与轻量互动机器人")
    parser.add_argument("--run-now", action="store_true", help="立即执行单次巡查与回复")
    parser.add_argument("--dry-run", action="store_true", help="仅巡查并展示匹配到的评论，不执行真实回复点击")
    parser.add_argument("--threshold", type=int, default=10, help="触发回复的评论数上限（默认 10 条以内）")
    parser.add_argument("--per-post", type=int, default=2, help="每个帖子最多回复人数（默认 1~2 人）")
    parser.add_argument("--limit", type=int, default=5, help="巡查自己最近发布的帖子数量（默认 5 条）")
    parser.add_argument("--daemon", action="store_true", help="以常驻后台模式运行，定期巡查")
    parser.add_argument("--interval-hours", type=float, default=4.0, help="常驻模式下的巡查间隔时间（小时，默认 4 小时）")
    args = parser.parse_args()

    replier = ThreadsAutoReplier(
        headless=True,
        max_replies_per_post=args.per_post,
        max_comments_threshold=args.threshold,
        check_posts_limit=args.limit
    )

    if args.run_now or args.dry_run:
        logger.info(f"正在启动巡查任务 (dry_run={args.dry_run}, threshold={args.threshold}, per_post={args.per_post})...")
        asyncio.run(replier.patrol_and_reply(dry_run=args.dry_run))
        return

    if args.daemon:
        logger.info(f"以守护进程常驻模式启动，每隔约 {args.interval_hours} 小时自动巡查一次主页帖子...")
        while True:
            try:
                asyncio.run(replier.patrol_and_reply(dry_run=False))
            except Exception as e:
                logger.error(f"巡查周期异常: {e}")
            jitter = random.randint(-15, 15) * 60
            sleep_seconds = max(1800, int(args.interval_hours * 3600) + jitter)
            logger.info(f"💤 巡查完成，将在 {sleep_seconds / 3600:.1f} 小时后进行下一次巡查...")
            asyncio.run(asyncio.sleep(sleep_seconds))
        return

    # 默认单次执行
    asyncio.run(replier.patrol_and_reply(dry_run=False))


if __name__ == "__main__":
    main()
