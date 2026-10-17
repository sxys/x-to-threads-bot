import re
import logging
from functools import partial
from twikit.x_client_transaction import transaction
from twikit import user as twikit_user
from twikit.utils import Result, find_dict
from twikit.tweet import tweet_from_data
from twikit.client.client import Client

logger = logging.getLogger("TwikitPatch")

# 2026年3月 X 拆分 webpack chunk 后的新结构
NEW_ON_DEMAND_FILE_REGEX = re.compile(
    r""",(\d+):["']ondemand\.s["']""", flags=(re.VERBOSE | re.MULTILINE)
)
NEW_ON_DEMAND_HASH_PATTERN = r',{}:"([0-9a-f]+)"'

# 早期版本的老结构 (备用回退)
OLD_ON_DEMAND_FILE_REGEX = re.compile(
    r"""['|\"]{1}ondemand\.s['|\"]{1}:\s*['|\"]{1}([\w]*)['|\"]{1}""", flags=(re.VERBOSE | re.MULTILINE)
)

INDICES_REGEX = re.compile(
    r"""(\(\w{1}\[(\d{1,2})\],\s*16\))+""", flags=(re.VERBOSE | re.MULTILINE)
)


async def patched_get_indices(self, home_page_response, session, headers):
    """
    两步查找补丁：解决 X 2026 更新 webpack chunk 导致的 Couldn't get KEY_BYTE indices 异常
    """
    key_byte_indices = []
    response = self.validate_response(home_page_response) or self.home_page_response
    body = str(response)
    on_demand_file_url = None

    # 1. 优先尝试新版两步查找 (name map -> hash map)
    idx_match = NEW_ON_DEMAND_FILE_REGEX.search(body)
    if idx_match:
        chunk_idx = idx_match.group(1)
        hash_regex = re.compile(NEW_ON_DEMAND_HASH_PATTERN.format(chunk_idx))
        hash_match = hash_regex.search(body)
        if hash_match:
            on_demand_file_url = (
                f"https://abs.twimg.com/responsive-web/client-web/ondemand.s.{hash_match.group(1)}a.js"
            )

    # 2. 如果未匹配到，尝试旧版直接匹配
    if not on_demand_file_url:
        old_match = OLD_ON_DEMAND_FILE_REGEX.search(body)
        if old_match:
            on_demand_file_url = (
                f"https://abs.twimg.com/responsive-web/client-web/ondemand.s.{old_match.group(1)}a.js"
            )

    if on_demand_file_url:
        try:
            on_demand_file_response = await session.request(
                method="GET", url=on_demand_file_url, headers=headers, timeout=10.0
            )
            key_byte_indices_match = INDICES_REGEX.finditer(str(on_demand_file_response.text))
            for item in key_byte_indices_match:
                key_byte_indices.append(item.group(2))
        except Exception:
            # 海外 CDN 连接若偶发超时，安全降级至已知稳定索引
            pass

    if not key_byte_indices:
        # 稳定兜底：使用 X 当前前端的基准 KEY_BYTE 索引
        return 33, [31, 7, 28]

    key_byte_indices = list(map(int, key_byte_indices))
    return key_byte_indices[0], key_byte_indices[1:]


class SafeDict(dict):
    """防止 X GraphQL 接口返回的字段被裁剪或变更导致 KeyError"""
    def __getitem__(self, key):
        val = super().get(key)
        if val is None:
            if key in ('description_urls', 'urls', 'pinned_tweet_ids_str', 'withheld_in_countries'):
                return []
            if key in ('fast_followers_count', 'normal_followers_count', 'friends_count', 'favourites_count',
                        'listed_count', 'media_count', 'statuses_count', 'followers_count'):
                return 0
            if key in ('is_blue_verified', 'verified', 'possibly_sensitive', 'can_dm', 'can_media_tag',
                        'want_retweets', 'default_profile', 'default_profile_image', 'has_custom_timelines',
                        'is_translator', 'protected'):
                return False
            if key == 'entities':
                return SafeDict({'description': SafeDict({'urls': []}), 'url': SafeDict({'urls': []})})
            return ""
        if isinstance(val, dict):
            return SafeDict(val)
        return val


orig_user_init = twikit_user.User.__init__

def patched_user_init(self, client, data: dict):
    safe_data = SafeDict(data)
    if 'legacy' in safe_data and isinstance(safe_data['legacy'], dict):
        safe_data['legacy'] = SafeDict(safe_data['legacy'])
    orig_user_init(self, client, safe_data)


def is_promoted_item(item: dict) -> bool:
    """判定 GraphQL 数据项是否属于官方广告推广"""
    entry_id = str(item.get('entryId', '')).lower()
    if entry_id.startswith(('promoted-', 'promoted_', 'ad-')):
        return True
    content = item.get('content', {})
    item_content = content.get('itemContent', {})
    if 'promotedMetadata' in item_content or 'promoted_metadata' in item_content:
        return True
    if 'clientEventInfo' in content:
        cei = str(content['clientEventInfo']).lower()
        if 'promoted' in cei or 'advertiser' in cei:
            return True
    return False


async def patched_get_timeline(self, count: int = 20, seen_tweet_ids: list[str] | None = None, cursor: str | None = None):
    """拦截官方广告推文的 get_timeline (For You)"""
    response, _ = await self.gql.home_timeline(count, seen_tweet_ids, cursor)
    items = find_dict(response, 'entries', find_one=True)[0]
    next_cursor = items[-1]['content']['value']
    results = []

    for item in items:
        if 'itemContent' not in item.get('content', {}):
            continue
        # 直接跳过广告推广项
        if is_promoted_item(item):
            continue
        tweet = tweet_from_data(self, item)
        if tweet is None:
            continue
        tweet.is_promoted = False
        results.append(tweet)

    return Result(
        results,
        partial(self.get_timeline, count, seen_tweet_ids, next_cursor),
        next_cursor
    )


async def patched_get_latest_timeline(self, count: int = 20, seen_tweet_ids: list[str] | None = None, cursor: str | None = None):
    """拦截官方广告推文的 get_latest_timeline (Following)"""
    response, _ = await self.gql.home_latest_timeline(count, seen_tweet_ids, cursor)
    items = find_dict(response, 'entries', find_one=True)[0]
    next_cursor = items[-1]['content']['value']
    results = []

    for item in items:
        if 'itemContent' not in item.get('content', {}):
            continue
        if is_promoted_item(item):
            continue
        tweet = tweet_from_data(self, item)
        if tweet is None:
            continue
        tweet.is_promoted = False
        results.append(tweet)

    return Result(
        results,
        partial(self.get_latest_timeline, count, seen_tweet_ids, next_cursor),
        next_cursor
    )


async def patched_get_tweet_by_id(self, tweet_id: str, cursor: str | None = None):
    """安全获取指定推文详情，兼容 2026 最新 X GraphQL 结构，避免 KeyError: itemContent"""
    data, _ = await self.gql.tweet_detail(tweet_id, cursor)
    entries = find_dict(data, 'entries')
    if not entries:
        return None
    entries = entries[0]
    for entry in entries:
        if 'tweet-' in entry.get('entryId', ''):
            tweet = tweet_from_data(self, entry)
            if tweet:
                return tweet
    return None


def apply_patch():
    """动态注入所有增强补丁"""
    transaction.ClientTransaction.get_indices = patched_get_indices
    twikit_user.User.__init__ = patched_user_init
    Client.get_timeline = patched_get_timeline
    Client.get_latest_timeline = patched_get_latest_timeline
    Client.get_tweet_by_id = patched_get_tweet_by_id
    logger.debug("Twikit patches (Transaction, User, Anti-Ad, GetTweetById) applied.")
