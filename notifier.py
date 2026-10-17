import logging
import requests
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str, proxy: Optional[str] = None):
        self.bot_token = bot_token.strip()
        self.chat_id = str(chat_id).strip()
        self.proxy = proxy.strip() if proxy else None
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"

    def _get_proxies(self) -> Optional[Dict[str, str]]:
        if self.proxy:
            return {
                "http": self.proxy,
                "https": self.proxy
            }
        return None

    def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """发送 Telegram 消息"""
        if not self.bot_token or not self.chat_id:
            logger.error("未配置 Telegram Bot Token 或 Chat ID！")
            return False

        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }

        try:
            proxies = self._get_proxies()
            resp = requests.post(url, json=payload, proxies=proxies, timeout=15)
            data = resp.json()
            if resp.status_code == 200 and data.get("ok"):
                logger.info("Telegram 预警消息发送成功！")
                return True
            else:
                logger.error(f"Telegram 消息发送失败: HTTP {resp.status_code}, 返回: {data}")
                return False
        except requests.exceptions.ProxyError as e:
            logger.error(f"代理连接失败: {e}。请检查 config.yaml 中的代理设置是否正确。")
            return False
        except requests.exceptions.ConnectionError as e:
            logger.error(f"网络连接错误: {e}。若在国内环境，请在 config.yaml 配置 proxy (如 http://127.0.0.1:7890)。")
            return False
        except Exception as e:
            logger.error(f"发送 Telegram 消息时出现异常: {e}")
            return False

    def get_latest_chat_id(self) -> Optional[int]:
        """获取最近与 Bot 发送过消息的用户 chat_id (辅助排错)"""
        if not self.bot_token:
            logger.error("未配置 Telegram Bot Token！")
            return None

        url = f"{self.base_url}/getUpdates"
        try:
            proxies = self._get_proxies()
            resp = requests.get(url, proxies=proxies, timeout=15)
            data = resp.json()
            if not data.get("ok"):
                logger.error(f"获取更新失败: {data}")
                return None

            results = data.get("result", [])
            if not results:
                logger.warning("未找到任何互动记录，请先在 Telegram 打开你的机器人并发送一条任意消息（例如 /start），然后再试。")
                return None

            latest_update = results[-1]
            message = latest_update.get("message") or latest_update.get("channel_post") or {}
            chat = message.get("chat", {})
            chat_id = chat.get("id")
            user_name = chat.get("username") or chat.get("first_name") or "未知用户"
            text = message.get("text", "")
            logger.info(f"成功检测到消息！来自: {user_name}, Chat ID: {chat_id}, 消息内容: {text}")
            return chat_id
        except Exception as e:
            logger.error(f"请求 getUpdates 异常: {e}")
            return None
