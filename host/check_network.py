"""服务器网络自检：数据源和后台都能不能连上。买完服务器、装完以后先跑一遍：python3 host/check_network.py
币安是最关键的一项：连不上（HTTP 451 / 403）说明这台机器的 IP 被币安拒绝，要换地区或机房。"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hotcoin_host as host  # noqa: E402

CHECKS = [
    ("币安合约（fapi）", "https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=15m&limit=1", True),
    ("币安备用域名（www）", "https://www.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=15m&limit=1", False),
    ("CoinGecko", "https://api.coingecko.com/api/v3/ping", True),
    ("Hyperliquid", "https://api.hyperliquid.xyz/info", False),
    ("GitHub（拉代码）", "https://raw.githubusercontent.com/ivonasmith/hotcoin-trade-automation/main/README.md", True),
    ("Notion API", "https://api.notion.com/v1/users/me", True),
    ("Telegram API", "https://api.telegram.org/", True),
]
OK_WITHOUT_BODY = {"Notion API": (401,), "Telegram API": (200, 302, 404), "Hyperliquid": (200, 405, 422, 500)}


def probe(url, timeout=15):
    request = urllib.request.Request(url, headers={"User-Agent": host.USER_AGENT})
    began = time.time()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read(2000)
            return response.status, time.time() - began, ""
    except urllib.error.HTTPError as exc:
        return exc.code, time.time() - began, ""
    except Exception as exc:  # noqa: BLE001
        return None, time.time() - began, str(exc)[:80]


def judge(name, status):
    if status is None:
        return False
    if name in OK_WITHOUT_BODY:
        return status in OK_WITHOUT_BODY[name]
    return 200 <= status < 300


def main():
    failed = []
    for name, url, required in CHECKS:
        status, seconds, error = probe(url)
        good = judge(name, status)
        print(f"{'通' if good else '不通'}  {name:<18} {status if status else error}  {seconds:.1f}s")
        if not good and required:
            failed.append(name)
    env = host.load_env()
    if env.get("WORKER_INGEST_URL") and env.get("PIPELINE_SECRET"):
        try:
            rules = host.fetch_watchlist(env)
            print(f"通  后台（Worker）      监控清单 {len(rules)} 条")
        except Exception as exc:  # noqa: BLE001
            print("不通  后台（Worker）     ", str(exc)[:120])
            failed.append("后台")
    else:
        print("跳过  后台（Worker）      环境文件里还没填 WORKER_INGEST_URL / PIPELINE_SECRET")
    proxy = env.get("HOTCOIN_HTTP_PROXY")
    print("代理：" + (proxy if proxy else "没设（服务器直连，正常）"))
    if failed:
        print("\n有必需项不通：" + "、".join(failed))
        return 1
    print("\n全部必需项都通。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
