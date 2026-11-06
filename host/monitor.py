"""15 分钟监控：读后台的监控清单（Telegram 里 /watch 加的），拉币安合约 K 线，条件触发就发 Telegram 提醒。

只报数据，不给买卖建议。规则怎么写见 cloud/watch.mjs。
- 价格触及（上破 / 跌破）：看近 3 根 15 分钟线的最高 / 最低价，规则创建之后才算。
- 收盘类（日收上 / 日收下 / 4h收上 / 4h收下）：只看最近一根已收盘的 K 线，每根只判一次。
- 急涨 / 急跌：最近一根已收盘的 15 分钟线涨跌幅，每根只判一次。
- 常驻规则 6 小时内不重复提醒（后台记 last_fired）。
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BJ = timezone(timedelta(hours=8))
FAPI_BASES = ["https://fapi.binance.com", "https://www.binance.com"]  # 域名铁律：别试 api.binance.com，地域封锁
COOLDOWN_MS = 6 * 3600 * 1000
INTERVAL_OF = {"up": "15m", "down": "15m", "surge": "15m", "plunge": "15m",
               "dayup": "1d", "daydown": "1d", "h4up": "4h", "h4down": "4h"}
TEXT = {"up": "价格涨到", "down": "价格跌到", "dayup": "日线收盘站上", "daydown": "日线收盘跌破",
        "h4up": "4小时收盘站上", "h4down": "4小时收盘跌破", "surge": "15分钟涨幅达到", "plunge": "15分钟跌幅达到"}
USER_AGENT = "hotcoin-host/1.0"


class NoSymbol(Exception):
    pass


def http_json(url, timeout=20):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_klines(rows):
    return [{"open_time": int(r[0]), "open": float(r[1]), "high": float(r[2]), "low": float(r[3]),
             "close": float(r[4]), "close_time": int(r[6])} for r in rows]


def fetch_klines(symbol, interval, limit=3, getter=http_json):
    """symbol 是币的简称（SOL）；先试 SOLUSDT，再试 1000SOLUSDT（PEPE 这类）。"""
    last_error = None
    for contract in (symbol + "USDT", "1000" + symbol + "USDT"):
        query = urllib.parse.urlencode({"symbol": contract, "interval": interval, "limit": limit})
        for base in FAPI_BASES:
            try:
                return contract, parse_klines(getter(f"{base}/fapi/v1/klines?{query}"))
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", "replace")[:200] if hasattr(exc, "read") else ""
                if exc.code == 400 and "Invalid symbol" in body:
                    last_error = NoSymbol(symbol)
                    break  # 这个合约名不存在，换下一个合约名，不用再试备用域名
                last_error = exc
            except Exception as exc:  # noqa: BLE001 - 换备用域名
                last_error = exc
    if isinstance(last_error, NoSymbol):
        raise last_error
    raise RuntimeError(f"{symbol} 拉不到币安数据：{last_error}")


def fmt(value):
    return f"{value:,.6g}" if abs(value) < 1 else f"{value:,.2f}".rstrip("0").rstrip(".")


def evaluate(rule, bars, now_ms, seen):
    """返回 (触发文字或 None, 这次判过的那根收盘线的开盘时间或 None)。bars：从旧到新，最后一根可能还没收盘。"""
    kind, value, sym = rule["kind"], float(rule["value"]), rule["sym"]
    created = int(rule.get("created") or 0)
    last_fired = rule.get("last_fired")
    if last_fired and now_ms - int(last_fired) < COOLDOWN_MS:
        return None, None
    if kind in ("up", "down"):
        recent = [b for b in bars if b["close_time"] > max(created, int(last_fired or 0))]
        if not recent:
            return None, None
        now_price = bars[-1]["close"]
        if kind == "up" and max(b["high"] for b in recent) >= value:
            return f"{sym} {TEXT[kind]} {fmt(value)}：近15分钟最高 {fmt(max(b['high'] for b in recent))}，现价 {fmt(now_price)}", None
        if kind == "down" and min(b["low"] for b in recent) <= value:
            return f"{sym} {TEXT[kind]} {fmt(value)}：近15分钟最低 {fmt(min(b['low'] for b in recent))}，现价 {fmt(now_price)}", None
        return None, None
    closed = [b for b in bars if b["close_time"] < now_ms]
    fresh = [b for b in closed if b["close_time"] > created and (seen is None or b["open_time"] > seen)]
    if not fresh:
        return None, None
    for bar in fresh:  # 从旧到新，漏跑一轮也不会漏掉中间那根
        if kind in ("dayup", "h4up") and bar["close"] > value:
            return f"{sym} {TEXT[kind]} {fmt(value)}：收盘 {fmt(bar['close'])}", fresh[-1]["open_time"]
        if kind in ("daydown", "h4down") and bar["close"] < value:
            return f"{sym} {TEXT[kind]} {fmt(value)}：收盘 {fmt(bar['close'])}", fresh[-1]["open_time"]
        if kind in ("surge", "plunge"):
            change = (bar["close"] / bar["open"] - 1) * 100
            if (kind == "surge" and change >= value) or (kind == "plunge" and change <= -value):
                return f"{sym} {TEXT[kind]} {value:g}%：这根15分钟线 {change:+.2f}%（{fmt(bar['open'])} → {fmt(bar['close'])}）", fresh[-1]["open_time"]
    return None, fresh[-1]["open_time"]


def run_cycle(rules, state, notify, now_ms=None, getter=http_json):
    """跑一轮。state['bars'][规则id] 记着每条规则最近判过的收盘线；state['bad_symbols'] 记着已经提醒过“找不到”的币。"""
    now_ms = now_ms or int(time.time() * 1000)
    seen_bars = state.setdefault("bars", {})
    bad_symbols = state.setdefault("bad_symbols", [])
    cache, fired, lines, errors = {}, [], [], []
    live_ids = {str(r["id"]) for r in rules}
    for stale in [k for k in seen_bars if k not in live_ids]:
        seen_bars.pop(stale)
    for rule in rules:
        interval = INTERVAL_OF.get(rule["kind"])
        if not interval:
            continue
        key = (rule["sym"], interval)
        if key not in cache:
            try:
                cache[key] = fetch_klines(rule["sym"], interval, 3, getter)[1]
            except NoSymbol:
                cache[key] = None
                if rule["sym"] not in bad_symbols:
                    bad_symbols.append(rule["sym"])
                    lines.append(f"{rule['sym']} 在币安合约里找不到（试过 {rule['sym']}USDT 和 1000{rule['sym']}USDT），"
                                 f"这条规则不会触发。发 /unwatch {rule['sym']} 取消。")
            except Exception as exc:  # noqa: BLE001
                cache[key] = None
                errors.append(str(exc)[:160])
        bars = cache[key]
        if not bars:
            continue
        text, bar_open = evaluate(rule, bars, now_ms, seen_bars.get(str(rule["id"])))
        if bar_open is not None:
            seen_bars[str(rule["id"])] = bar_open
        if text:
            lines.append(text)
            fired.append(rule["id"])
    if lines:
        stamp = datetime.fromtimestamp(now_ms / 1000, BJ).strftime("%m-%d %H:%M")
        notify("【监控提醒】" + stamp + "（北京时间）\n" + "\n".join(lines), fired)
    return {"rules": len(rules), "fired": len(fired), "errors": errors, "fetched": len([v for v in cache.values() if v])}


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import hotcoin_host as host
    env = host.load_env()
    state = {}
    summary = run_cycle(host.fetch_watchlist(env), state, lambda text, ids: host.notify(env, text, ids))
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
