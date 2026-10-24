import os
import re
import json
import asyncio
import logging
import httpx
from datetime import datetime, timedelta
from typing import List, Dict, Any, Tuple
import twikit_patch
twikit_patch.apply_patch()

from twikit import Client
from x_config import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("XHomeCollector")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE = os.path.join(BASE_DIR, "history_tweets.json")
HISTORY_RETENTION_DAYS = 14  # 历史推文去重保留天数，保证14天内绝不重复抓取相同的热门推文

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

    @classmethod
    def is_personal_diary_tweet(cls, tweet) -> Tuple[bool, str]:
        """
        判断帖子是否为博主个人真实生活日记、家庭琐事、私人就医相亲或感情流水账：
        这类帖子由于是强第一人称的亲属关系和私密生活记录，作为公共聚合账号转发至 Threads 会显得突兀和怪异。
        """
        if not getattr(config, "FILTER_PERSONAL_DIARY", True):
            return False, ""

        text = getattr(tweet, "text", "") or ""
        full_text = getattr(tweet, "full_text", "") or text
        content = full_text or text
        if not content:
            return False, ""

        # 0. 用户自定义排除关键词检查（来自 .env 的 EXCLUDE_KEYWORDS）
        custom_kws = getattr(config, "EXCLUDE_KEYWORDS", "")
        if custom_kws:
            kws = [k.strip() for k in re.split(r'[,，]', custom_kws) if k.strip()]
            for kw in kws:
                if kw in content:
                    return True, f"命中用户自定义排除词: '{kw}'"

        # 1. 第一人称家庭/亲密关系核心代词高频出现 (>= 2 次)
        # 例如: "我妈...我妈", "我爸...我妈", "我老公...我"
        rel_pattern = r'我(妈|爸|母亲|父亲|老公|老婆|男朋友|女朋友|男票|女票|对象|公公|婆婆|岳父|岳母|闺蜜|室友|儿子|女儿)'
        rel_matches = re.findall(rel_pattern, content)
        if len(rel_matches) >= 2:
            return True, f"高频第一人称亲属代词 (出现 {len(rel_matches)} 次: {rel_matches})"

        # 2. 经典生活流水账与第一人称亲密互动正则模式
        DIARY_PATTERNS = [
            # 伴侣/亲属动作同行与互动 (如: "带我妈去看了演唱会", "陪我爸去医院", "跟我老公吵架")
            (r'(带|陪|跟|和|帮|替)我(妈|爸|母亲|父亲|老公|老婆|男朋友|女朋友|男票|女票|对象|公公|婆婆|岳父|岳母|闺蜜|室友|儿子|女儿)', '家庭/伴侣第一人称同行与互动'),
            # 亲属言语与家常行为 (如: "我妈问我票价多少，我说", "我老公给我买的", "我妈今天生日")
            (r'我(妈|爸|母亲|父亲|老公|老婆|男朋友|女朋友|男票|女票|对象|公公|婆婆|闺蜜|室友)(问我|跟我说|对我说|说|带我|买给|做饭|做菜|煮了|生日|生病|住院|体检|手术|催婚|逼我)', '家庭琐事行为与长辈言行'),
            # 私人庆祝生日/纪念日
            (r'祝(我|他|她|咱)?(妈|爸|老公|老婆|男朋友|女朋友|对象)?生日快乐', '私人/亲友生日祝福'),
            (r'今天(我|我妈|我爸|我老公|我老婆|我男朋友|我女朋友|我对象)生日', '私人亲友生日记录'),
            (r'(和|跟)(我)?(男朋友|女朋友|男票|女票|老公|老婆|对象|男盆友|女盆友).{0,15}(领证|结婚|订婚|同居|吵架|冷战|分手|复合|逛街|约会|看电影|吃火锅|旅游)', '伴侣情感与恋爱日常'),
            # 恋爱/结婚纪念日
            (r'(恋爱|结婚|领证|在一起|相恋|相识).{0,6}(一|两|二|三|四|五|六|七|八|九|十|\d+)周年', '恋爱/结婚周年纪念'),
            # 就医体检与生理私事
            (r'(今天去|刚去|准备去|陪你去)(相亲|拔智齿|看牙|做产检|打宫颈癌疫苗|打hpv|体检|做胃镜|做肠镜)', '个人就医与私事'),
            (r'(查出怀孕|我怀孕了|休产假|顺产|剖腹产|生了个男宝|生了个女宝)', '个人生育私事'),
            # 感情前任与相亲纠葛
            (r'(前任|前男友|前女友)(发微信|发信息|找我|点赞我|求复合|突然联系我)', '前任私事流水账'),
            (r'(今天|刚才|下午)去相亲.{0,20}(相亲对象|男方|女方|奇葩)', '相亲私事流水账'),
            # 琐事对白流水账 (如: "问我票价多少，我说...她说...")
            (r'(问我.{0,15}我说|我问.{0,10}她说|对我说.{0,15}我说|我说.{0,15}她说)', '第一人称对话流水账'),
        ]

        for pattern, desc in DIARY_PATTERNS:
            m = re.search(pattern, content)
            if m:
                return True, f"{desc} (匹配: '{m.group()}')"

        return False, ""

    # ================== 严格内容安全合规规则库 (遵循 Threads/Meta 推荐限制规范) ==================
    RESTRICTED_CATEGORIES = {
        "自残、自杀与进食障碍": [
            (r'(自杀|轻生|寻死|割腕|割脉|切腹|饮弹自尽|吞药自尽|吞药自杀|吃安眠药自杀|服毒自尽|服毒自杀|跳楼自杀|跳桥自杀|烧炭自杀|烧炭轻生|上吊自杀|上吊自尽|相约自杀|约死群)', '明确自杀/自绝/轻生词汇'),
            (r'(不想活了|不想活下去了|活着没意思|活着太痛苦了想死|活不下去了|好想死|真的想死|准备去死|准备自尽|下辈子不来了|留下遗书|写下遗书|厌世想死)', '自杀倾向与厌世绝望言论'),
            (r'(自残|自伤|划伤自己|用刀割自己|拿刀割手|割手腕|烟头烫自己)', '自残自伤行为'),
            (r'(暴食症|厌食症|神经性厌食|神经性贪食|进食障碍)', '进食障碍病理词汇'),
            (r'(催吐|催吐管|兔子管|嚼吐|锁喉催吐|插管催吐|暴食.*催吐|扣喉催吐)', '催吐及进食障碍行为'),
        ],
        "暴力伤害与血腥恐怖": [
            (r'(分尸|碎尸|斩首|凌迟|肢解|活埋|剥皮|割喉|绞杀|惨遭杀害|残忍杀害|虐杀)', '极端残酷杀戮与尸体毁损'),
            (r'(持刀砍人|持枪扫射|连环杀人|凶杀现场|灭门惨案|杀人抛尸|抛尸现场|尸块|杀人犯|杀人狂)', '恶性凶杀与持械行凶'),
            (r'(砍人|杀人|捅死|捅伤|砍死|砍伤|枪杀|射杀|击毙|打死人|殴打致死|被活活打死)', '暴力伤亡行为'),
            (r'(血肉模糊|开膛破肚|鲜血淋漓|头破血流|血流成河|脑浆迸裂)', '血腥伤亡视觉描写'),
            (r'(虐猫|虐狗|虐待动物|虐杀小动物|摔死猫|烫死猫|毒杀宠物)', '虐待动物'),
            (r'(暴打|围殴|聚众斗殴|持械斗殴|械斗|把人打残|打断腿|严重霸凌殴打|严刑拷打)', '严重身体暴力与霸凌械斗'),
            (r'(恐怖袭击|人体炸弹|汽车炸弹|引爆炸弹|劫持人质|无差别砍人|无差别杀人|报复社会.*杀人)', '恐怖袭击与极端恶性暴力'),
        ],
        "性暗示与色情违规": [
            (r'(约炮|同城约|招嫖|嫖娼|卖淫|援交|兼职上门|包养|找金主|外围女|外围模特|楼凤|品茶修车|出台陪睡|莞式服务)', '性交易与约炮招嫖'),
            (r'(黄推|福利姬|门槛群|微密圈|onlyfans|fansly|91pron|51吃瓜视频|汤不热)', '色情平台与黄推引流'),
            (r'(看片[+＋➕]v|看片加v|看片联系|进群看片|电报看片|tg看片|私信看完整|主页置顶看视频|推特福利|成人无码|成人网站|番号推荐|三级片|av女优)', '色情资源与看片引流'),
            (r'(露点|露胸|露逼|露屌|巨乳福利|爆乳福利|骚逼|性奴|调教|sm调教|捆绑调教|露出癖|野外露出|偷拍裙底|偷窥洗澡|针孔偷拍)', '露骨性暗示与性癖调教'),
            (r'(做爱|性交|啪啪啪|打炮|口交|深喉|颜射|乳交|中出|内射|后入|潮吹|自慰|手淫|打飞机|飞机杯|裸聊|裸照|裸体福利|私密私照|艳照|原味内裤|原味丝袜)', '露骨性行为与私密暴露'),
        ],
        "宣传烟草与管制产品": [
            (r'(电子烟|雾化器|烟弹|悦刻|relx|柚子yooz|魔笛mote|尼古丁盐|一次性电子烟|电子烟代购|电子烟批发|电子烟现货|烟油推荐|vape)', '电子烟及雾化产品宣传'),
            (r'(香烟代购|香烟批发|外烟代购|专柜正品香烟|免税香烟|细支香烟|雪茄代购|卷烟批发|低价香烟|买烟加v|抽烟推荐|正品烟草)', '烟草销售与代购推广'),
            (r'(大麻|thc|cbd烟油|冰毒|海洛因|可卡因|摇头丸|k粉|氯胺酮|麻古|依托咪酯|上头电子烟|笑气|吸毒|贩毒|违禁药物|迷魂药|听话水|乖乖水|迷奸药|三唑仑)', '毒品与精神管制药品'),
        ]
    }

    @classmethod
    def is_restricted_content_tweet(cls, tweet) -> Tuple[bool, str]:
        """
        严格判断推文是否包含 Meta / Threads 推荐指南中明确限制推荐的内容：
        1. X 官方标记的敏感媒体 (possibly_sensitive)
        2. 避开描绘或讨论自残、自杀、进食障碍的内容
        3. 避开描绘或讨论暴力的内容
        4. 避开性暗示内容与色情引流
        5. 避开宣传烟草、电子烟等管制产品的内容
        """
        if not getattr(config, "FILTER_RESTRICTED_CONTENT", True):
            return False, ""

        # 检查原推、转推与引用推
        sub_tweets = [tweet]
        retweeted = getattr(tweet, "retweeted_tweet", None)
        if retweeted:
            sub_tweets.append(retweeted)
        quoted = getattr(tweet, "quoted_tweet", None)
        if quoted:
            sub_tweets.append(quoted)

        # 1. 检查 X 平台原生敏感媒体标记 (possibly_sensitive)
        for st in sub_tweets:
            is_sens = getattr(st, "possibly_sensitive", False)
            if not is_sens and hasattr(st, "_legacy") and isinstance(st._legacy, dict):
                is_sens = st._legacy.get("possibly_sensitive", False)
            if not is_sens and hasattr(st, "_data") and isinstance(st._data, dict):
                is_sens = st._data.get("possibly_sensitive", False)
            if is_sens:
                return True, "X官方标记敏感媒体内容 (possibly_sensitive=True)"

        # 2. 汇集所有正文文本
        texts = []
        for st in sub_tweets:
            t = getattr(st, "text", "") or ""
            ft = getattr(st, "full_text", "") or t
            if ft:
                texts.append(ft)
            elif t:
                texts.append(t)
        combined_text = "\n".join(texts)
        if not combined_text.strip():
            return False, ""

        # 3. 逐项遍历四大合规违规正则库
        for cat_name, patterns in cls.RESTRICTED_CATEGORIES.items():
            for pattern, desc in patterns:
                m = re.search(pattern, combined_text, re.IGNORECASE)
                if m:
                    return True, f"命中[{cat_name}]限制: {desc} (匹配: '{m.group()}')"

        return False, ""

    @classmethod
    async def ai_verify_restricted_content(cls, candidates: List[Dict[str, Any]], target_count: int) -> List[Dict[str, Any]]:
        """
        可选的 Gemini AI 深度安全合规复审：
        对规则初筛通过的高分候选帖子进行双重确认，剔除隐晦的自残自杀、暴力、性暗示或烟草毒品宣传。
        若未配置有效 Key 或网络超时/429，自动平滑跳过，保证核心抓取流程不受阻断。
        """
        if not getattr(config, "FILTER_RESTRICTED_AI", False):
            return candidates[:target_count]

        api_key = getattr(config, "GEMINI_API_KEY", "")
        if not api_key or api_key.startswith("your_") or len(api_key.strip()) < 8:
            return candidates[:target_count]

        if not candidates:
            return []

        logger.info(f"🤖 正在调用 Gemini AI 对前 {min(len(candidates), target_count * 2)} 条候选推文进行深度合规安全复核...")
        proxy = config.PROXY if config.PROXY and config.PROXY.strip() else None
        model = getattr(config, "GEMINI_MODEL", "gemini-2.5-flash-lite") or "gemini-2.5-flash-lite"
        if "1.5" in model:
            model = "gemini-2.5-flash"

        check_pool = candidates[:target_count * 2]
        review_items = []
        for idx, item in enumerate(check_pool):
            raw_t = item.get("full_text") or item.get("text") or ""
            snippet = raw_t.replace("\n", " ").strip()[:180]
            review_items.append(f"[{idx}] {snippet}")

        prompt = (
            "你是一名严格的社交媒体合规安全审核员。请依照 Threads / Meta 推荐限制标准，审查以下推文文案，判断是否触犯以下任一限制红线：\n"
            "1. 自残、自杀、进食障碍（描绘或讨论轻生、割腕、绝望厌世、催吐、暴食厌食等）\n"
            "2. 暴力内容（描绘或讨论血腥、凶杀、打人、虐杀动物、恐怖主义等）\n"
            "3. 性暗示内容（性交易、黄推、看片引流、露骨性暗示、涉黄等）\n"
            "4. 宣传烟草等管制产品（宣传或推销电子烟、香烟、大麻毒品及精神管制药物等）\n\n"
            "待审核推文列表：\n"
            + "\n".join(review_items) + "\n\n"
            "【输出要求】：\n"
            "请以纯 JSON 数组格式返回每条推文审查结果：\n"
            '[{"id": 0, "safe": true, "reason": ""}, {"id": 1, "safe": false, "reason": "违规原因"}]'
        )

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 800,
                "responseMimeType": "application/json"
            }
        }

        candidate_models = ["gemini-2.5-flash-lite", model, "gemini-2.5-flash"]
        try:
            async with httpx.AsyncClient(proxy=proxy, timeout=15.0) as client:
                for cur_model in candidate_models:
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{cur_model}:generateContent?key={api_key.strip()}"
                    resp = await client.post(url, json=payload)
                    if resp.status_code == 200:
                        data = resp.json()
                        candidates_resp = data.get("candidates", [])
                        if candidates_resp:
                            parts = candidates_resp[0].get("content", {}).get("parts", [])
                            text_parts = [p.get("text", "") for p in parts if "text" in p and not p.get("thought", False)]
                            if text_parts:
                                results = json.loads(text_parts[0])
                                unsafe_ids = set()
                                for r in results:
                                    if not r.get("safe", True):
                                        unsafe_ids.add(r.get("id"))
                                        logger.info(f"🚫 [AI合规复检拦截] #{check_pool[r.get('id')]['id']}: {r.get('reason')} - {check_pool[r.get('id')]['text'][:40]}...")
                                
                                safe_candidates = [c for i, c in enumerate(check_pool) if i not in unsafe_ids]
                                # 补齐剩余候选
                                safe_candidates.extend(candidates[len(check_pool):])
                                logger.info(f"✅ Gemini AI 安全复核完成：放行 {len(safe_candidates)} 条，拦截 {len(unsafe_ids)} 条。")
                                return safe_candidates[:target_count]
                    elif resp.status_code in (404, 429):
                        continue
                    else:
                        break
        except Exception as e:
            logger.warning(f"⚠️ Gemini AI 合规复检跳过或网络异常 ({e})，将直接使用规则过滤结果。")

        return candidates[:target_count]

    @staticmethod
    def _normalize_text_fingerprint(text: str) -> str:
        """生成推文核心文案指纹，去除 URL、标点及空格，用于内容级查重"""
        if not text:
            return ""
        cleaned = re.sub(r'https?://\S+', '', text)
        cleaned = re.sub(r'[\s\W_]+', '', cleaned)
        return cleaned[:50]

    def _load_history(self) -> Dict[str, Any]:
        """读取历史抓取记录并自动剔除超过 14 天的过期条目"""
        history = {"ids": {}, "fingerprints": {}}
        now = datetime.now()

        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                    if isinstance(raw, dict):
                        raw_ids = raw.get("ids", {})
                        raw_fps = raw.get("fingerprints", {})

                        for tid, ts_str in raw_ids.items():
                            try:
                                dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                                if (now - dt).days <= HISTORY_RETENTION_DAYS:
                                    history["ids"][str(tid)] = ts_str
                            except Exception:
                                history["ids"][str(tid)] = ts_str

                        for fp, ts_str in raw_fps.items():
                            try:
                                dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                                if (now - dt).days <= HISTORY_RETENTION_DAYS:
                                    history["fingerprints"][fp] = ts_str
                            except Exception:
                                history["fingerprints"][fp] = ts_str
            except Exception as e:
                logger.warning(f"读取历史推文记录失败 ({e})，将重置历史库。")

        # 兜底：若本地已有现存的 threads_queue.json，将其中已有的推文也纳入历史去重库
        q_file = os.path.join(BASE_DIR, "threads_queue.json")
        if os.path.exists(q_file):
            try:
                with open(q_file, "r", encoding="utf-8") as f:
                    queue_data = json.load(f)
                    for item in queue_data:
                        tid = str(item.get("id", ""))
                        if tid and tid not in history["ids"]:
                            history["ids"][tid] = now.strftime("%Y-%m-%d %H:%M:%S")
                        fp = self._normalize_text_fingerprint(item.get("text", ""))
                        if len(fp) >= 8 and fp not in history["fingerprints"]:
                            history["fingerprints"][fp] = now.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass

        logger.info(f"历史推文去重库已加载 (已收录近 14 天 {len(history['ids'])} 条历史推文，绝不重复抓取)")
        return history

    def _is_duplicate_tweet(self, tweet, history: Dict[str, Any]) -> bool:
        """检查推文是否在近 14 天内已被抓取过（包含 ID 查重、转推原贴查重及文案指纹查重）"""
        # 1. 检查当前推文 ID
        tid = str(getattr(tweet, "id", "") or "")
        if tid and tid in history["ids"]:
            return True

        # 2. 若是转推，检查被转推的原贴 ID
        retweeted = getattr(tweet, "retweeted_tweet", None)
        if retweeted:
            orig_id = str(getattr(retweeted, "id", "") or "")
            if orig_id and orig_id in history["ids"]:
                return True

        # 3. 检查文案核心指纹（防止同一事件被换号洗稿转发）
        text = getattr(tweet, "text", "") or ""
        fp = self._normalize_text_fingerprint(text)
        if len(fp) >= 8 and fp in history["fingerprints"]:
            return True

        return False

    def _save_history(self, new_top_tweets: List[Dict[str, Any]], history: Dict[str, Any]):
        """将今日选出的 Top 推文持久化追加至历史去重库"""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for item in new_top_tweets:
            tid = str(item["id"])
            history["ids"][tid] = now_str
            fp = self._normalize_text_fingerprint(item.get("text", ""))
            if len(fp) >= 8:
                history["fingerprints"][fp] = now_str

        try:
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
            logger.info(f"历史推文去重库已更新持久化至 {HISTORY_FILE} (当前历史库总量: {len(history['ids'])} 条)")
        except Exception as e:
            logger.error(f"保存历史推文记录失败: {e}")

    async def _fetch_home_top_tweets_async(self) -> List[Dict[str, Any]]:
        """异步拉取主页推文并进行严格的中文、免广告、去重过滤与热度排序"""
        await self._login()

        # 加载历史推文去重库
        history = self._load_history()

        timeline_type = config.TIMELINE_TYPE.lower()
        max_pages = max(config.FETCH_PAGES, 8)  # 适当翻页确保候选池充足
        target_candidates = max(config.TOP_LIMIT * 3, 20)  # 收集足够多的原生中文候选即可提前停止

        logger.info(f"开始抓取主页流 (类型: {timeline_type})，严格去重、筛选原生中文且跳过广告帖子...")

        # 首次拉取
        if timeline_type == "following":
            result = await self.client.get_latest_timeline(count=40)
        else:
            result = await self.client.get_timeline(count=40)

        all_tweets = []
        seen_ids = set()
        duplicate_filtered_count = 0
        filtered_count = 0
        ad_filtered_count = 0
        diary_filtered_count = 0
        restricted_filtered_count = 0

        def process_batch(tweets_batch):
            nonlocal all_tweets, seen_ids, duplicate_filtered_count, filtered_count, ad_filtered_count, diary_filtered_count, restricted_filtered_count
            if not tweets_batch:
                return
            for tweet in tweets_batch:
                tweet_id = getattr(tweet, "id", None)
                if not tweet_id or tweet_id in seen_ids:
                    continue
                seen_ids.add(tweet_id)

                # 排除历史已发送过的重复推文（近 14 天严格去重）
                if self._is_duplicate_tweet(tweet, history):
                    duplicate_filtered_count += 1
                    continue

                # 排除广告推广（官方投放广告 + 商业软广）
                if self.is_ad_tweet(tweet):
                    ad_filtered_count += 1
                    continue

                # 严格原生中文过滤
                if not self.is_original_chinese(tweet):
                    filtered_count += 1
                    continue

                # 排除博主第一人称真实生活日记、家庭隐私琐事与流水账
                is_diary, diary_reason = self.is_personal_diary_tweet(tweet)
                if is_diary:
                    diary_filtered_count += 1
                    user_obj = getattr(tweet, "user", None)
                    s_name = getattr(user_obj, "screen_name", "user") if user_obj else "user"
                    t_snippet = (getattr(tweet, "text", "") or "").replace("\n", " ")[:35]
                    logger.info(f"🚫 [已跳过个人日记/私事推文] @{s_name}: {diary_reason} - {t_snippet}...")
                    continue

                # 排除自残自杀、进食障碍、暴力、性暗示、宣传烟草等管制内容（严格遵循 Threads/Meta 推荐合规指南）
                is_restricted, restricted_reason = self.is_restricted_content_tweet(tweet)
                if is_restricted:
                    restricted_filtered_count += 1
                    user_obj = getattr(tweet, "user", None)
                    s_name = getattr(user_obj, "screen_name", "user") if user_obj else "user"
                    t_snippet = (getattr(tweet, "text", "") or "").replace("\n", " ")[:35]
                    logger.info(f"🚫 [已跳过限制合规推文] @{s_name}: {restricted_reason} - {t_snippet}...")
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
        logger.info(f"第 1 页抓取完成，当前符合要求的全新中文帖子: {len(all_tweets)} (过滤历史重复: {duplicate_filtered_count}, 过滤非中文: {filtered_count}, 过滤广告: {ad_filtered_count}, 过滤个人日记: {diary_filtered_count}, 过滤限制违规: {restricted_filtered_count})")

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
                logger.info(f"第 {page} 页抓取完成，累计全新中文帖子: {len(all_tweets)} (过滤历史重复: {duplicate_filtered_count}, 过滤非中文: {filtered_count}, 过滤广告: {ad_filtered_count}, 过滤个人日记: {diary_filtered_count}, 过滤限制违规: {restricted_filtered_count})")
            except Exception as e:
                logger.warning(f"第 {page} 页翻页抓取出现异常 ({e})，停止翻页。")
                break

        if not all_tweets:
            logger.warning("未能从主页中获取到符合原生中文与安全合规要求的有效推文。")
            return []

        # 按热度降序排序
        all_tweets.sort(key=lambda x: x["score"], reverse=True)

        # 若开启了 AI 安全合规复审，对排名前列候选进行深度复核
        if getattr(config, "FILTER_RESTRICTED_AI", False):
            top_list = await self.ai_verify_restricted_content(all_tweets, config.TOP_LIMIT)
        else:
            top_list = all_tweets[:config.TOP_LIMIT]

        logger.info(f"排序完成！已选出热度前 {len(top_list)} 条纯中文帖子（最高热度分: {top_list[0]['score']}）。")

        # 持久化保存今日入选的 Top 推文至历史去重库
        self._save_history(top_list, history)

        # 自动生成/追加 Threads 待发队列并下载本地多媒体资源
        try:
            from queue_manager import QueueManager
            await QueueManager.build_threads_queue(top_list, interval_minutes=config.POST_INTERVAL_MINUTES)
        except Exception as e:
            logger.error(f"生成 Threads 队列或下载媒体失败: {e}", exc_info=True)

        return top_list

    def fetch_home_top_tweets(self) -> List[Dict[str, Any]]:
        """对外暴露的同步接口"""
        return asyncio.run(self._fetch_home_top_tweets_async())
