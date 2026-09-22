"""
SteamDT API 客户端
==================
统一封装鉴权、限速、重试与错误处理。

已确认可用的接口（均需 Authorization: Bearer <key>）：
    GET  /open/cs2/v1/base          全量饰品基础信息（每日 1 次）
    GET  /open/cs2/v1/price/single  单品各平台价格 / 挂单量 / 求购量
    POST /open/cs2/v1/price/batch   批量价格（每分钟 1 次）
    GET  /open/cs2/v1/price/avg     单品跨平台均价
    POST /open/cs2/item/v1/kline    单品 K 线（120 次/分钟）
    GET  /open/cs2/broad/v1/index   CS2 大盘指数
    POST /open/cs2/v1/wear          磨损度
"""

import os
import sys
import time
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from config import STEAMDT, get_api_key


class SteamDTError(Exception):
    """SteamDT 接口错误"""

    def __init__(self, message: str, code: Optional[int] = None):
        super().__init__(message)
        self.code = code


# 接口错误码含义
ERROR_MEANING = {
    4001: "API Key 无效",
    4005: "接口请求已达上限（注意 base 为每日 1 次）",
    4006: "系统异常，请稍后重试",
    100002: "参数错误",
}


class SteamDTClient:
    """SteamDT 开放平台客户端"""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or get_api_key()
        self.base_url = STEAMDT["base_url"].rstrip("/")
        self.timeout = STEAMDT["timeout"]
        self.rate_limit = STEAMDT["rate_limit"]

        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

        self._last_call: Dict[str, float] = {}
        self.stats = {"requests": 0, "errors": 0}

    # ------------------------------------------------------------
    # 底层请求
    # ------------------------------------------------------------

    def _throttle(self, endpoint: str):
        """按接口限速"""
        interval = self.rate_limit.get(endpoint)
        if interval is None:
            return
        elapsed = time.time() - self._last_call.get(endpoint, 0.0)
        if elapsed < interval:
            time.sleep(interval - elapsed)
        self._last_call[endpoint] = time.time()

    def request(
        self,
        method: str,
        path: str,
        *,
        endpoint: str = "default",
        params: Optional[Dict] = None,
        json_body: Optional[Dict] = None,
        retries: int = 3,
    ) -> Dict[str, Any]:
        """
        发起请求并返回成功响应体。

        失败时抛 SteamDTError（携带错误码，便于上层判断是否为限流）。
        """
        url = f"{self.base_url}{path}"
        last_err: Optional[Exception] = None

        for attempt in range(1, retries + 1):
            self._throttle(endpoint)
            try:
                resp = self.session.request(
                    method, url, params=params, json=json_body, timeout=self.timeout
                )
                self.stats["requests"] += 1

                # 方法/路径错误直接抛，不重试
                if resp.status_code in (404, 405):
                    raise SteamDTError(f"接口不存在或方法错误: {method} {path}")

                if resp.status_code != 200:
                    last_err = SteamDTError(f"HTTP {resp.status_code}")
                    if attempt < retries:
                        time.sleep(2 * attempt)
                        continue
                    raise last_err

                data = resp.json()

                if not data.get("success"):
                    code = data.get("errorCode")
                    msg = data.get("errorMsg") or data.get("errorCodeStr") or "未知错误"
                    meaning = ERROR_MEANING.get(code, "")
                    err = SteamDTError(f"[{code}] {msg}" + (f" — {meaning}" if meaning else ""), code=code)

                    # 限流/系统异常可重试；参数错误不重试
                    if code in (4005, 4006) and attempt < retries:
                        self.stats["errors"] += 1
                        time.sleep(3 * attempt)
                        last_err = err
                        continue
                    raise err

                return data

            except requests.RequestException as e:
                self.stats["errors"] += 1
                last_err = SteamDTError(f"网络异常: {e}")
                if attempt < retries:
                    time.sleep(2 * attempt)
                    continue
                raise last_err

        raise last_err or SteamDTError("请求失败")

    # ------------------------------------------------------------
    # 业务接口
    # ------------------------------------------------------------

    def get_base_items(self) -> List[Dict]:
        """全量饰品基础信息（⚠️ 每日仅 1 次，请走 base_cache 而非直接调用）"""
        data = self.request("GET", "/open/cs2/v1/base", endpoint="base")
        return data.get("data") or []

    def get_price_single(self, market_hash_name: str) -> List[Dict]:
        """单品各平台价格 / 挂单量 / 求购量"""
        data = self.request(
            "GET", "/open/cs2/v1/price/single",
            endpoint="price_single",
            params={"marketHashName": market_hash_name},
        )
        return data.get("data") or []

    def get_price_batch(self, market_hash_names: List[str]) -> List[Dict]:
        """批量价格（每分钟 1 次，单次最多 100 个）"""
        data = self.request(
            "POST", "/open/cs2/v1/price/batch",
            endpoint="price_batch",
            json_body={"marketHashNames": market_hash_names},
        )
        return data.get("data") or []

    def get_price_avg(self, market_hash_name: str, days: int = 7) -> Dict:
        """单品跨平台均价"""
        data = self.request(
            "GET", "/open/cs2/v1/price/avg",
            endpoint="price_avg",
            params={"marketHashName": market_hash_name, "days": days},
        )
        return data.get("data") or {}

    def get_kline(self, market_hash_name: str, ktype: int = 2) -> List[List]:
        """
        单品 K 线。

        参数:
            ktype: 1=时K, 2=日K, 3=周K
        返回:
            [[时间戳, 开, 收, 高, 低], ...]
        """
        data = self.request(
            "POST", "/open/cs2/item/v1/kline",
            endpoint="kline",
            json_body={"marketHashName": market_hash_name, "type": ktype},
        )
        return data.get("data") or []

    def get_broad_index(self) -> Dict:
        """CS2 大盘最新指数"""
        data = self.request("GET", "/open/cs2/broad/v1/index", endpoint="price_single")
        return data.get("data") or {}

    # ------------------------------------------------------------

    def close(self):
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
