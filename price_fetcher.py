import logging
import requests
from typing import Tuple, Optional
import yfinance as yf

logger = logging.getLogger(__name__)

class PriceFetcher:
    """价格获取器：支持美股与加密货币"""

    def __init__(self, proxy: Optional[str] = None):
        self.proxy = proxy.strip() if proxy else None
        self.session = requests.Session()
        if self.proxy:
            self.session.proxies = {
                "http": self.proxy,
                "https": self.proxy
            }

    def get_stock_price(self, symbol: str) -> Tuple[Optional[float], Optional[str]]:
        """获取美股价格（使用 yfinance）
        返回: (price, source)
        """
        symbol = symbol.strip().upper()
        try:
            ticker = yf.Ticker(symbol)
            fast_info = getattr(ticker, 'fast_info', None)
            if fast_info:
                # 优先获取最新价格
                price = getattr(fast_info, 'last_price', None)
                if price is not None and price > 0:
                    return float(price), "YahooFinance"

            # 备选方案：尝试获取当日或最新收盘行情
            hist = ticker.history(period="1d")
            if not hist.empty and 'Close' in hist:
                return float(hist['Close'].iloc[-1]), "YahooFinance(Hist)"

        except Exception as e:
            logger.warning(f"获取美股 {symbol} 价格失败: {e}")

        return None, None

    def get_crypto_price(self, symbol: str) -> Tuple[Optional[float], Optional[str]]:
        """获取加密货币价格（支持 OKX / Binance / Binance.US / Coinbase / Yahoo 多源容灾）
        输入可以是 BTC、BTCUSDT、BTC-USDT 等
        返回: (price, source)
        """
        raw_symbol = symbol.strip().upper()
        # 提取基础币种，例如 BTCUSDT -> BTC
        base_sym = raw_symbol.replace("USDT", "").replace("USD", "").replace("-", "")

        # 1. 尝试 OKX API (低延迟、全球大部分地区无阻碍)
        try:
            url = f"https://www.okx.com/api/v5/market/ticker?instId={base_sym}-USDT"
            resp = self.session.get(url, timeout=5)
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                if data and "last" in data[0]:
                    return float(data[0]["last"]), "OKX"
        except Exception:
            pass

        # 2. 尝试 Binance 公开行情 API
        try:
            url = f"https://api.binance.com/api/v3/ticker/price?symbol={base_sym}USDT"
            resp = self.session.get(url, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                if "price" in data:
                    return float(data["price"]), "Binance"
        except Exception:
            pass

        # 3. 尝试 Binance.US API (美区 IP 可用)
        try:
            url = f"https://api.binance.us/api/v3/ticker/price?symbol={base_sym}USDT"
            resp = self.session.get(url, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                if "price" in data:
                    return float(data["price"]), "Binance.US"
        except Exception:
            pass

        # 4. 尝试 Coinbase API (欧美高可用)
        try:
            url = f"https://api.coinbase.com/v2/prices/{base_sym}-USD/spot"
            resp = self.session.get(url, timeout=5)
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                if "amount" in data:
                    return float(data["amount"]), "Coinbase"
        except Exception:
            pass

        # 5. 备选兜底：Yahoo Finance (BTC-USD 等)
        try:
            yf_sym = f"{base_sym}-USD"
            price, _ = self.get_stock_price(yf_sym)
            if price is not None:
                return price, "YahooFinance"
        except Exception:
            pass

        logger.warning(f"获取加密货币 {symbol} 失败：已尝试 OKX, Binance, Coinbase, Yahoo")
        return None, None
