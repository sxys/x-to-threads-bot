import os
import re
import json
import random
import logging
import asyncio
import argparse
import httpx
from datetime import datetime
from typing import List, Dict, Any, Optional
from playwright.async_api import async_playwright, Page
from x_config import config


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIE_FILE = os.path.join(BASE_DIR, "threads_cookies.json")
REPLIED_HISTORY_FILE = os.path.join(BASE_DIR, "replied_comments.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [ThreadsAutoReply] %(message)s"
)
logger = logging.getLogger("ThreadsAutoReply")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

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
        check_posts_limit: int = 12,
        custom_phrases: Optional[List[str]] = None
    ):
        self.headless = headless
        self.max_replies_per_post = max_replies_per_post
        self.max_comments_threshold = max_comments_threshold
        self.check_posts_limit = check_posts_limit
        self.phrases = custom_phrases or DEFAULT_REPLY_PHRASES
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

    async def generate_gemini_reply(self, post_text: str, comment_text: str, comment_author: str) -> Optional[str]:
        """
        优先调用 Google Gemini API，根据原帖背景与粉丝评论生成切中要害、自然口语化的回复。
        若未配置 Key、遇到频次限制 (HTTP 429)、网络超时或报错，返回 None 以触发自动优雅降级。
        """
        api_key = getattr(config, "GEMINI_API_KEY", "")
        if not api_key or api_key.startswith("your_") or len(api_key.strip()) < 8:
            return None

        # 优先使用 gemini-2.5-flash；若配置为已停用的旧 1.5 系列，自动无缝升级为 2.5
        model = getattr(config, "GEMINI_MODEL", "gemini-2.5-flash") or "gemini-2.5-flash"
        if "1.5" in model:
            model = "gemini-2.5-flash"

        proxy = config.PROXY if config.PROXY and config.PROXY.strip() else None

        clean_post = (post_text or "").strip()
        if len(clean_post) > 300:
            clean_post = clean_post[:300] + "..."

        prompt = f"""你是 Threads 社交平台的中文博主。你发布了一条帖子，一位粉丝在你的帖子下方发表了评论。
请以博主本人的身份，针对该粉丝写一句自然、机智、接地气的互动回复。

【你发布的帖子内容】：
\"\"\"{clean_post if clean_post else '（日常随笔与社会热点讨论）'}\"\"\"

【粉丝 @{comment_author} 的评论内容】：
\"\"\"{comment_text.strip()}\"\"\"

【严格回复原则】：
1. 命中要害：必须针对粉丝评论的核心观点、吐槽或情绪进行针对性回应，切中要害，有观点、有态度，严禁答非所问。
2. 绝对严禁口头禅废话：【严禁】开头使用“哈哈”、“确实”、“你说的对”、“笑死”等无意义口头禅！直接切入主题接梗、表达看法或机智互动。
3. 短小精炼：字数在 12 ~ 30 个字之间（完整的一句话）。切忌长篇大论。
4. 真实社交口吻：语气随和接地气，像真人在社交平台冲浪，可自然带 1 个常用 emoji（如 😂、🤝、👀、👏 等）。
5. 纯净输出：仅输出单条回复文本，不要带任何前缀（如“回复：”）、双引号或额外解释。"""

        candidate_models = []
        # 将 gemini-2.5-flash-lite 作为首选高频互动模型，免费配额充足（每天数千次），绝不会触发 20 次/天的限制
        candidate_models.append("gemini-2.5-flash-lite")
        if model and model not in candidate_models:
            candidate_models.append(model)
        for m in ["gemini-2.5-flash", "gemini-flash-latest"]:
            if m not in candidate_models:
                candidate_models.append(m)

        payload = {
            "contents": [
                {
                    "parts": [{"text": prompt}]
                }
            ],
            "generationConfig": {
                "temperature": 0.8,
                "maxOutputTokens": 300,
                "topP": 0.9,
                "thinkingConfig": {"thinkingBudget": 0}
            }
        }

        try:
            async with httpx.AsyncClient(proxy=proxy, timeout=12.0) as client:
                for cur_model in candidate_models:
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{cur_model}:generateContent?key={api_key.strip()}"
                    resp = await client.post(url, json=payload)
                    if resp.status_code == 200:
                        data = resp.json()
                        candidates = data.get("candidates", [])
                        if candidates:
                            parts = candidates[0].get("content", {}).get("parts", [])
                            # 提取正文文本（排除 thought 节点）
                            text_parts = [p.get("text", "") for p in parts if "text" in p and not p.get("thought", False)]
                            if text_parts:
                                raw_reply = text_parts[0].strip()
                                # 文本后处理：清理多余引号、前缀与换行
                                cleaned = raw_reply.strip(' "“\'”`')
                                cleaned = re.sub(r'^(?:博主回复|回复|回覆)[:：]\s*', '', cleaned)
                                # 彻底剥除开头不自然的“哈哈，”、“哈哈 ”等机械口头禅
                                cleaned = re.sub(r'^(?:哈哈+[，,、\s]*)+', '', cleaned)
                                cleaned = cleaned.replace('\n', ' ').strip()
                                if cleaned and len(cleaned) <= 50:
                                    return cleaned
                                elif cleaned:
                                    return cleaned[:40] + "..."
                    elif resp.status_code == 404:
                        # 当前模型不存在，自动换下一个候选模型重试
                        continue
                    elif resp.status_code == 429:
                        logger.warning(f"⚠️ 模型 {cur_model} 触发配额限制 (HTTP 429)，自动尝试下一个可用模型...")
                        continue
                    else:
                        logger.warning(f"⚠️ Gemini API 返回异常 (HTTP {resp.status_code}): {resp.text[:120]}")
                        continue
        except Exception as e:
            logger.warning(f"⚠️ 调用 Gemini API 失败 ({e})，将平滑降级为预设话术。")

        return None

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

                # 3. 提取博主最近发布的帖子链接（支持向下滚动以加载满 limit 条）
                async def _scan_profile_links():
                    return await page.evaluate("""(myUser) => {
                        const links = Array.from(document.querySelectorAll('a'));
                        const urls = [];
                        for (const a of links) {
                            const href = a.getAttribute('href') || '';
                            if (href.endsWith('/media') || href.includes('/media')) continue;
                            if (href.includes(`/@${myUser}/post/`) || href.includes('/post/')) {
                                const full = href.startsWith('http') ? href : `https://www.threads.com${href}`;
                                const clean = full.split('?')[0];
                                if (!urls.includes(clean)) {
                                    urls.push(clean);
                                }
                            }
                        }
                        return urls;
                    }""", my_username)

                post_links = await _scan_profile_links()
                scroll_count = 0
                while len(post_links) < self.check_posts_limit and scroll_count < 4:
                    scroll_count += 1
                    await page.mouse.wheel(0, 1500)
                    await page.wait_for_timeout(2000)
                    post_links = await _scan_profile_links()

                post_links = post_links[:self.check_posts_limit]
                logger.info(f"共扫描到个人最新发布的 {len(post_links)} 条帖子，准备开始巡查评论区...")

                # 4. 逐一巡视每个帖子
                for p_idx, post_url in enumerate(post_links, 1):
                    logger.info(f"\n--------------------------------------------------")
                    logger.info(f"[{p_idx}/{len(post_links)}] 正在检查帖子: {post_url}")
                    summary["posts_checked"] += 1

                    await page.goto(post_url, timeout=35000, wait_until="domcontentloaded")
                    await page.wait_for_timeout(3000)
                    # 微幅下滚 400px 触发视口内评论的懒加载渲染
                    await page.mouse.wheel(0, 400)
                    await page.wait_for_timeout(2000)

                    # 提取该帖的主帖正文与所有真实粉丝评论（深度清洗，精准定位真实文本）
                    post_data = await page.evaluate("""({myUser, postUrl}) => {
                        const mPost = postUrl.match(/\\/post\\/([A-Za-z0-9_-]+)/);
                        const myPostId = mPost ? mPost[1] : '';

                        const cleanText = (str) => {
                            if (!str) return '';
                            // 彻底剥离 Threads 在外语/海外 VPS 环境下自动追加的 Translate / 翻译 后缀
                            return str.replace(/\\s*(?:Translate|翻译|翻譯)\\s*$/i, '').trim();
                        };

                        const isNoise = (str) => {
                            if (!str) return true;
                            const s = cleanText(str);
                            if (s.length < 2) return true;
                            if (/^\\d+$/.test(s)) return true; // 纯数字（点赞数、评论数）
                            if (/^(?:·\\s*)?\\d+\\s*[hdmwy秒分时天周](?:\\s*ago)?$/i.test(s)) return true; // 相对时间戳 (如 10m, 1h, · 2d)
                            if (/^(?:\\d{1,2}[\\/\\-]\\d{1,2}[\\/\\-]\\d{2,4}|\\d{4}[\\/\\-]\\d{1,2}[\\/\\-]\\d{1,2})$/.test(s)) return true; // 绝对日期 (如 09/18/26)
                            const lower = s.toLowerCase();
                            const exactNoise = [
                                'view activity', '查看动态', 'no replies yet', 'translate', '翻译', '翻譯',
                                'reply', '回复', '回覆', 'follow', '关注', 'liked by', 'likes', '赞', '讚',
                                'share', '分享', 'repost', '转帖', '轉貼', 'reposts', 'comments', 'views',
                                'thread', 'threads', 'related threads', 'more posts'
                            ];
                            if (exactNoise.includes(lower)) return true;
                            return false;
                        };

                        // 1. 提取主帖正文（优先从 meta 标签提取，其次从主帖容器筛选最长有效段落）
                        let postText = '';
                        const metaDesc = document.querySelector('meta[property="og:description"]') || document.querySelector('meta[name="description"]');
                        if (metaDesc && metaDesc.content) {
                            let raw = metaDesc.content.trim();
                            const m = raw.match(/:\\s*["“](.*)["”]$/s);
                            postText = cleanText(m ? m[1].trim() : raw);
                        }

                        if (!postText || postText.length < 6 || isNoise(postText)) {
                            const allSpans = Array.from(document.querySelectorAll('span[dir="auto"], div[dir="auto"]'));
                            let candidatePostTexts = [];
                            for (const s of allSpans.slice(0, 45)) {
                                const t = cleanText((s.innerText || '').trim());
                                if (t.length >= 6 && !isNoise(t) && t.toLowerCase() !== myUser.toLowerCase()) {
                                    candidatePostTexts.push(t);
                                }
                            }
                            if (candidatePostTexts.length > 0) {
                                candidatePostTexts.sort((a, b) => b.length - a.length);
                                postText = candidatePostTexts[0];
                            }
                        }

                        // 2. 提取粉丝评论
                        const allPostLinks = Array.from(document.querySelectorAll('a[href*="/post/"]'));
                        const results = [];
                        const seenAuthors = new Set();

                        // 查找“相关串文”分界线元素，避免将底部的推荐帖子误当成当前帖的评论
                        const relatedDivider = Array.from(document.querySelectorAll('span, h2, h3, div')).find(el => {
                            const t = (el.innerText || '').trim().toLowerCase();
                            return t === 'related threads' || t === '相关串文' || t === '相關串文' || t === 'more posts' || t === '更多串文';
                        });

                        for (const a of allPostLinks) {
                            // 如果遍历已越过“相关串文”推荐边界，直接退出评论提取
                            if (relatedDivider && (relatedDivider.compareDocumentPosition(a) & Node.DOCUMENT_POSITION_FOLLOWING)) {
                                break;
                            }

                            const href = a.getAttribute('href') || '';
                            if (myPostId && href.includes(myPostId)) continue; // 排除主帖自身的链接

                            // 兼容绝对 URL 和相对 URL
                            const m = href.match(/@([A-Za-z0-9._]+)/);
                            const author = m ? m[1] : 'unknown';
                            if (author.toLowerCase() === myUser.toLowerCase() || author === 'unknown') continue;
                            if (seenAuthors.has(author.toLowerCase())) continue;

                            // 向上查找评论卡片容器
                            let card = a.closest('div[data-pressable-container="true"]') || a.parentElement;
                            while (card && card.tagName !== 'BODY') {
                                if (card.querySelectorAll('svg').length >= 3) break;
                                card = card.parentElement;
                            }

                            let candidateTexts = [];
                            if (card) {
                                const spans = Array.from(card.querySelectorAll('span[dir="auto"], div[dir="auto"]'));
                                for (const s of spans) {
                                    const raw = (s.innerText || '').trim();
                                    const t = cleanText(raw);
                                    if (t && t !== author && t !== `@${author}` && !isNoise(t)) {
                                        candidateTexts.push(t);
                                    }
                                }
                            }

                            if (candidateTexts.length === 0) continue;

                            // 按长度排序挑选最长一段作为真实粉丝评论正文
                            candidateTexts.sort((a, b) => b.length - a.length);
                            const bestText = candidateTexts[0];

                            seenAuthors.add(author.toLowerCase());
                            results.push({
                                author,
                                text: bestText,
                                href
                            });
                        }
                        return { postText, comments: results };
                    }""", {"myUser": my_username, "postUrl": post_url})

                    post_text = post_data.get("postText", "")
                    comments = post_data.get("comments", [])
                    total_comments_count = len(comments)
                    logger.info(f"该帖子检测到粉丝评论数: {total_comments_count} 条 (主帖文案: '{post_text[:30]}...')")

                    # 条件过滤：只要有评论就参与互动；无评论则跳过
                    if total_comments_count == 0:
                        logger.info("ℹ️ 当前帖子暂无粉丝评论，跳过。")
                        continue

                    summary["posts_eligible"] += 1

                    # 筛选出尚未回复过的粉丝评论
                    candidates = []
                    for c in comments:
                        c_author = c["author"]
                        c_text = c["text"]
                        c_key = self._comment_key(post_url, c_author, c_text)

                        if c_key in replied_history:
                            logger.info(f"⏩ 评论已被回复过 (@{c_author}: '{c_text[:15]}...')，跳过。")
                            continue

                        candidates.append({
                            "key": c_key,
                            "href": c["href"],
                            "author": c_author,
                            "text": c_text
                        })

                    if not candidates:
                        logger.info("本帖所有现有粉丝评论均已在历史库中回复过，跳过。")
                        continue

                    # 动态回复策略：评论少于或等于 10 条回复 1 个；评论大于 10 条回复 2 个
                    target_quota = 1 if total_comments_count <= self.max_comments_threshold else 2

                    # 严格限制单帖累计回复总数：统计历史库中已经对该帖子累计回复过的数量（杜绝多轮巡查重复回复同一帖子）
                    already_replied_count = sum(1 for item in replied_history.values() if item.get("post_url") == post_url)
                    remaining_quota = max(0, target_quota - already_replied_count)

                    if remaining_quota <= 0:
                        logger.info(f"ℹ️ 该帖子历史已累计回复 {already_replied_count} 条，已达本帖总配额上限 ({target_quota} 条)，跳过。")
                        continue

                    target_count = min(remaining_quota, len(candidates))
                    selected_candidates = random.sample(candidates, target_count)
                    logger.info(f"🎯 帖子总评论 {total_comments_count} 条 (总配额: {target_quota} 人, 历史已回: {already_replied_count} 人, 本次计划回复: {len(selected_candidates)} 人)：")

                    for t_idx, target in enumerate(selected_candidates):
                        # 如果同一个帖子回复多位粉丝，回复完第一位后页面停留在其子线程，必须重新导航回帖子主页
                        if t_idx > 0 and not dry_run:
                            logger.info(f"正在返回帖子主页以处理下一条粉丝评论 (@{target['author']})...")
                            await page.goto(post_url, timeout=35000, wait_until="domcontentloaded")
                            await page.wait_for_timeout(3000)

                        # 优先调用 Gemini API 生成命中要害的智能回复，失败时优雅降级为预设话术
                        ai_reply = await self.generate_gemini_reply(post_text, target["text"], target["author"])
                        if ai_reply:
                            chosen_phrase = ai_reply
                            reply_source = "Gemini AI"
                        else:
                            chosen_phrase = random.choice(self.phrases)
                            reply_source = "预设精选"

                        logger.info(f"👉 准备回复 @{target['author']} ('{target['text'][:20]}...'): \"{chosen_phrase}\" [{reply_source}]")

                        if dry_run:
                            logger.info(f"[DRY-RUN 演示模式] 拟回复成功 (@{target['author']} -> '{chosen_phrase}' [{reply_source}])，已跳过真实点击。")
                            summary["replies_posted"] += 1
                            await asyncio.sleep(2)  # 演练模式下适度缓冲2秒，防止瞬间并发请求触发 RPM (每分钟频次) 限制
                            continue

                        # 精确定位该评论卡片内部的 Reply 按钮
                        # 策略 1: 通过评论专属子链接查找卡片容器
                        reply_btn = page.locator(f'a[href*="{target["href"]}"]').locator('xpath=ancestor::div[@data-pressable-container="true"]').locator('svg[title="Reply"], svg[title="回复"], svg[title="回覆"], svg[aria-label="Reply"], svg[aria-label="回复"], svg[aria-label="回覆"]').first

                        # 策略 2: 备用策略，通过评论作者主页链接查找卡片容器
                        if await reply_btn.count() == 0:
                            reply_btn = page.locator(f'a[href="/@{target["author"]}"]').first.locator('xpath=ancestor::div[@data-pressable-container="true"]').locator('svg[title="Reply"], svg[title="回复"], svg[title="回覆"], svg[aria-label="Reply"], svg[aria-label="回复"], svg[aria-label="回覆"]').first

                        # 策略 3: 如果当前不在视口内，向下微幅滚动并重试
                        if await reply_btn.count() == 0:
                            await page.mouse.wheel(0, 800)
                            await page.wait_for_timeout(1500)
                            reply_btn = page.locator(f'a[href*="{target["href"]}"]').locator('xpath=ancestor::div[@data-pressable-container="true"]').locator('svg[title="Reply"], svg[title="回复"], svg[title="回覆"], svg[aria-label="Reply"], svg[aria-label="回复"], svg[aria-label="回覆"]').first
                            if await reply_btn.count() == 0:
                                reply_btn = page.locator(f'a[href="/@{target["author"]}"]').first.locator('xpath=ancestor::div[@data-pressable-container="true"]').locator('svg[title="Reply"], svg[title="回复"], svg[title="回覆"], svg[aria-label="Reply"], svg[aria-label="回复"], svg[aria-label="回覆"]').first

                        if await reply_btn.count() == 0:
                            logger.warning(f"未能定位 @{target['author']} 评论的 Reply 按钮，跳过。")
                            continue

                        await reply_btn.scroll_into_view_if_needed()
                        await reply_btn.click()
                        await page.wait_for_timeout(1500)

                        editor = page.locator('div[contenteditable="true"]').last
                        if await editor.count() == 0:
                            logger.warning("未找到回复输入框，跳过本条。")
                            continue

                        try:
                            await editor.focus()
                        except Exception:
                            pass
                        try:
                            await editor.click(force=True, timeout=3000)
                        except Exception:
                            await editor.evaluate("el => el.focus()")
                        await page.wait_for_timeout(random.randint(600, 1200))
                        await page.keyboard.insert_text(chosen_phrase)
                        # 拟人打字后确认停顿
                        await page.wait_for_timeout(random.randint(1800, 3000))

                        # 查找发送按钮：在输入框同一区域的带有 Reply 标识的圆圈图标或按键
                        send_btn = page.locator('svg[aria-label="Reply"], svg[aria-label="回复"], svg[aria-label="回覆"]').last
                        if await send_btn.count() > 0:
                            await send_btn.click()
                        else:
                            await page.keyboard.press("Control+Enter")

                        await page.wait_for_timeout(4000)

                        # 记录到已回复持久化库
                        replied_history[target["key"]] = {
                            "post_url": post_url,
                            "comment_author": target["author"],
                            "comment_text": target["text"],
                            "my_reply": chosen_phrase,
                            "source": reply_source,
                            "replied_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        }
                        self.save_replied_history(replied_history)

                        summary["replies_posted"] += 1
                        summary["details"].append({
                            "author": target["author"],
                            "reply": chosen_phrase,
                            "source": reply_source,
                            "post_url": post_url
                        })
                        logger.info(f"✅ 成功回复 @{target['author']}！已记入去重库。")

                        # 防风控休眠：设定 35~75 秒拟人间隙，避免过于密集触发平台封控
                        delay_min = getattr(config, "AUTO_REPLY_DELAY_MIN", 35)
                        delay_max = getattr(config, "AUTO_REPLY_DELAY_MAX", 75)
                        jitter_wait = random.randint(min(delay_min, delay_max), max(delay_min, delay_max))
                        logger.info(f"⏳ [防风控拟人间隔] 等待 {jitter_wait} 秒后再进行下一项操作...")
                        await page.wait_for_timeout(jitter_wait * 1000)


                logger.info("\n================ 巡查与互动执行完毕 ================")
                logger.info(f"扫描帖子: {summary['posts_checked']} 篇 | 符合条件: {summary['posts_eligible']} 篇 | 成功回复: {summary['replies_posted']} 条")

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
    parser.add_argument("--threshold", type=int, default=10, help="评论分档阈值：<= 阈值回复 1 个，> 阈值回复 2 个（默认 10）")
    parser.add_argument("--per-post", type=int, default=2, help="每个帖子最多回复人数（默认 1~2 人）")
    parser.add_argument("--limit", type=int, default=12, help="巡查自己最近发布的帖子数量（默认 12 条，覆盖过去 6 小时内发布的帖子）")
    parser.add_argument("--daemon", action="store_true", help="以常驻后台模式运行，定期巡查")
    parser.add_argument("--interval-hours", type=float, default=2.0, help="常驻模式下的巡查间隔时间（小时，推荐 2 小时）")
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
