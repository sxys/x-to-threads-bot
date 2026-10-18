import os
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

@dataclass
class Config:
    # ================== Telegram 配置 ==================
    TG_BOT_TOKEN: str = os.getenv("TG_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
    TG_CHAT_ID: str = os.getenv("TG_CHAT_ID", "YOUR_TELEGRAM_CHAT_ID")
    
    # 登录方式 1：直接提供浏览器 Cookies（最推荐，免密且抗风控）
    X_AUTH_TOKEN: str = os.getenv("X_AUTH_TOKEN", "")
    X_CT0: str = os.getenv("X_CT0", "")
    
    # 登录方式 2：账号密码登录（在没有填写上述 Cookie 时生效）
    X_USERNAME: str = os.getenv("X_USERNAME", "YOUR_X_USERNAME")
    X_EMAIL: str = os.getenv("X_EMAIL", "YOUR_X_EMAIL")
    X_PASSWORD: str = os.getenv("X_PASSWORD", "YOUR_X_PASSWORD")
    COOKIE_FILE: str = os.getenv("COOKIE_FILE", "x_cookies.json")
    
    # 流类型: 'for_you' (主页推荐流) 或 'following' (正在关注流)
    TIMELINE_TYPE: str = os.getenv("TIMELINE_TYPE", "for_you")
    
    # ================== 抓取与热度权重配置 ==================
    TOP_LIMIT: int = int(os.getenv("TOP_LIMIT", "8"))          # 每次抓取热门推文数量（默认 8 条，每 4 小时一次，一天 6 次共 48 条）
    FETCH_PAGES: int = int(os.getenv("FETCH_PAGES", "5"))       # 滚动翻页次数（每次约20-40条候选）
    POST_INTERVAL_MINUTES: int = int(os.getenv("POST_INTERVAL_MINUTES", "30")) # 每条推文间隔发布时间（分钟，默认 30）
    SCHEDULE_INTERVAL_HOURS: int = int(os.getenv("SCHEDULE_INTERVAL_HOURS", "4")) # 抓取间隔周期（小时，默认 4）
    
    # 热度权重公式: Score = (likes * W_LIKE) + (retweets * W_RT) + (replies * W_REPLY) + (quotes * W_QUOTE)
    WEIGHT_LIKE: float = float(os.getenv("WEIGHT_LIKE", "1.0"))
    WEIGHT_RETWEET: float = float(os.getenv("WEIGHT_RETWEET", "2.0"))
    WEIGHT_REPLY: float = float(os.getenv("WEIGHT_REPLY", "1.5"))
    WEIGHT_QUOTE: float = float(os.getenv("WEIGHT_QUOTE", "1.5"))
    
    # ================== 网络代理配置 ==================
    # 在国内访问 X 和 Telegram 需要配置 HTTP/SOCKS 代理，例如: "http://127.0.0.1:7890"
    PROXY: Optional[str] = os.getenv("PROXY", None)

    # ================== 内容清洗与日记过滤配置 ==================
    # 自动过滤博主第一人称真实生活日记、家庭琐事、就医相亲、感情纠葛流水账（默认开启）
    FILTER_PERSONAL_DIARY: bool = os.getenv("FILTER_PERSONAL_DIARY", "true").lower() in ("true", "1", "yes")
    # 自定义排除关键词（以英文或中文逗号分隔，如: "林忆莲,相亲,打卡"）
    EXCLUDE_KEYWORDS: str = os.getenv("EXCLUDE_KEYWORDS", "")

    # ================== 定时任务配置 ==================
    # 每天定时执行的时间 (24小时制，每天早上 08:00)
    SCHEDULE_HOUR: int = int(os.getenv("SCHEDULE_HOUR", "8"))
    SCHEDULE_MINUTE: int = int(os.getenv("SCHEDULE_MINUTE", "0"))

config = Config()
