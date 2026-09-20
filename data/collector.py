"""
数据采集模块 — Steam Market API自动抓取
功能：
1. 从Steam市场获取皮肤当前价格和成交量
2. 增量写入CSV，逐步积累历史数据
3. 速率限制和错误重试机制
"""

import os
import sys
import time
import urllib.parse
import requests
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple, List

from config import settings


class SteamMarketCollector:
    """Steam市场数据采集器"""

    def __init__(self):
        self.config = settings.COLLECTOR
        self.app_id = settings.STEAM_APP_ID
        self.base_url = settings.STEAM_API_BASE_URL
        self.data_file = settings.DATA_FILE
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.config["user_agent"],
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": "https://steamcommunity.com/market/",
        })
        if self.config.get("proxy"):
            self.session.proxies = {
                "http": self.config["proxy"],
                "https": self.config["proxy"],
            }
        self._last_request_time = 0

    def _rate_limit(self):
        """速率限制：确保请求间隔不低于配置的时间"""
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self.config["request_interval"]:
            time.sleep(self.config["request_interval"] - elapsed)
        self._last_request_time = time.time()

    def _retry_request(self, url: str, params: dict) -> Optional[dict]:
        """带重试机制的请求"""
        for attempt in range(self.config["max_retries"]):
            try:
                self._rate_limit()
                response = self.session.get(
                    url,
                    params=params,
                    timeout=self.config["request_timeout"],
                )
                response.raise_for_status()
                return response.json()
            except requests.RequestException as e:
                print(f"  ❌ 请求失败 (尝试 {attempt + 1}/{self.config['max_retries']}): {e}")
                if attempt < self.config["max_retries"] - 1:
                    time.sleep(self.config["retry_delay"])
        return None

    def _parse_price(self, price_str: str) -> float:
        """解析价格字符串为数字"""
        if not price_str:
            return 0.0
        try:
            cleaned = price_str.replace("¥", "").replace(",", "").strip()
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0

    def fetch_skin_price(self, skin_name: str) -> Optional[Dict]:
        """
        获取单个皮肤的当前市场数据
        返回: {price, volume, lowest_price}
        """
        params = {
            "appid": self.app_id,
            "market_hash_name": skin_name,
            "currency": self.config["currency"],
        }

        print(f"  📡 正在获取: {skin_name}...", end=" ")
        data = self._retry_request(self.base_url, params)

        if not data:
            print("❌ 连接失败")
            return None

        if data.get("success") != True:
            print("❌ API错误")
            return None

        result = {
            "skin_name": skin_name,
            "price": self._parse_price(data.get("median_price")),
            "lowest_price": self._parse_price(data.get("lowest_price")),
            "volume": data.get("volume"),
            "date": datetime.now().strftime("%Y-%m-%d"),
        }

        if result["price"] == 0:
            result["price"] = result["lowest_price"]

        vol_str = result["volume"]
        if vol_str:
            try:
                result["volume"] = int(vol_str.replace(",", ""))
            except ValueError:
                result["volume"] = 0
        else:
            result["volume"] = 0

        print(f"✅ ¥{result['price']:.2f} | {result['volume']}件")
        return result

    def _load_existing_data(self) -> pd.DataFrame:
        """加载已有的CSV数据"""
        if os.path.exists(self.data_file):
            return pd.read_csv(self.data_file)
        return pd.DataFrame(columns=["skin_name", "date", "price", "volume"])

    def _append_to_csv(self, records: List[Dict]):
        """将新记录追加到CSV"""
        df = self._load_existing_data()
        new_df = pd.DataFrame(records)

        for _, row in new_df.iterrows():
            existing = df[
                (df["skin_name"] == row["skin_name"]) &
                (df["date"] == row["date"])
            ]
            if len(existing) == 0:
                df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
            else:
                df.loc[
                    (df["skin_name"] == row["skin_name"]) &
                    (df["date"] == row["date"]),
                    ["price", "volume"]
                ] = [row["price"], row["volume"]]

        df = df.sort_values(["skin_name", "date"]).reset_index(drop=True)
        os.makedirs(os.path.dirname(self.data_file), exist_ok=True)
        df.to_csv(self.data_file, index=False, encoding="utf-8-sig")

    def collect_all(self, skin_names: Optional[List[str]] = None) -> Tuple[int, int]:
        """
        批量采集所有热门皮肤数据
        返回: (成功数量, 失败数量)
        """
        if skin_names is None:
            skin_names = settings.HOT_SKINS

        print("=" * 60)
        print("🔄 Steam Market 数据采集器")
        print("=" * 60)
        print(f"\n📊 待采集皮肤: {len(skin_names)} 款")
        print(f"⏱️  请求间隔: {self.config['request_interval']}秒")
        print(f"🗓️  采集日期: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print()

        success_count = 0
        fail_count = 0
        records = []

        for i, skin_name in enumerate(skin_names):
            print(f"\n[{i + 1}/{len(skin_names)}]", end=" ")
            data = self.fetch_skin_price(skin_name)
            if data and data["price"] > 0:
                records.append({
                    "skin_name": data["skin_name"],
                    "date": data["date"],
                    "price": data["price"],
                    "volume": data["volume"],
                })
                success_count += 1
            else:
                fail_count += 1

        if records:
            print(f"\n💾 正在写入CSV...")
            self._append_to_csv(records)
            print(f"✅ 已写入 {len(records)} 条记录")

        print("\n" + "=" * 60)
        print(f"📋 采集完成: {success_count} 成功, {fail_count} 失败")
        print(f"📂 数据文件: {self.data_file}")
        
        if fail_count > 0 and success_count == 0:
            print("\n⚠️" + "=" * 56)
            print("⚠️  所有请求均失败！可能是网络问题")
            print("⚠️  如果您在中国大陆，请配置代理：")
            print("⚠️  修改 config/settings.py 中 COLLECTOR['proxy']")
            print("⚠️  示例: 'http://127.0.0.1:1080' 或 'socks5://127.0.0.1:1080'")
            print("⚠️" + "=" * 56)
        
        print("=" * 60)

        return success_count, fail_count

    def collect_recent_missing(self, days: int = 7) -> Tuple[int, int]:
        """
        补采最近N天缺失的数据
        返回: (成功数量, 失败数量)
        """
        df = self._load_existing_data()
        if df.empty:
            print("⚠️ 没有历史数据，执行全量采集")
            return self.collect_all()

        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=days)

        missing_dates = []
        for delta in range(days + 1):
            date = start_date + timedelta(days=delta)
            missing_dates.append(date.strftime("%Y-%m-%d"))

        skin_names = settings.HOT_SKINS
        need_collect = []

        for skin in skin_names:
            skin_data = df[df["skin_name"] == skin]
            skin_dates = set(skin_data["date"].astype(str).tolist())
            missing = [d for d in missing_dates if d not in skin_dates]
            if missing:
                need_collect.append((skin, missing))

        if not need_collect:
            print(f"✅ 最近{days}天数据完整，无需补采")
            return 0, 0

        print(f"🔍 发现 {len(need_collect)} 款皮肤有缺失数据")
        return self.collect_all([skin for skin, _ in need_collect])


def collect_main():
    """采集器主入口"""
    collector = SteamMarketCollector()
    collector.collect_all()


if __name__ == "__main__":
    collect_main()