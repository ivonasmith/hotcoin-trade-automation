"""纸面交易用的宏观闸门和事件窗口核对（《操作规格》v6.5 第 08 节 P0 + v4.1 近价改 ATR 口径）。
全部由程序按数据算，读不到的才写进“未核对”：
- 涨腿 89 天线：BTC 当前是涨腿、超过 89 天且腿内收盘最大回撤 <10% → 只减不加（不开新多）。涨腿 / 跌腿、起点、天数、回撤都来自快照里的 poster_facts，
  poster_facts 直接读网站 uncleonchain.com/data/leg_latest.json（2026-11-13 起和网站同一份数据，不再自己登记起点）。
  BTC 在跌腿时 89 天线不适用（它只管涨腿），《操作规格》没有给跌腿另设闸门，这里也不加。
- BTC 日线收盘在 MA20 下方，且过去 7 天跌幅 >5% → 不开新多。
- 48 小时内两个及以上二元事件（非农 / FOMC / CPI / 监管或立法投票）叠加 → 不开新多。
- 事件窗口（多空都管，按挂单那一刻算）：事件前 3 天不挂距现价 <1×日线ATR 的单；事件当天和次日不挂任何新单。
闸门关闭时，未成交的多单按规则作废——纸面账本里等于这条提醒不开真纸面单、记成影子单。
"""
import json
from datetime import datetime, timedelta, timezone

import monitor

CAL_URLS = ["https://uncleonchain.com/data/cal/cal_latest.json",
            "https://raw.githubusercontent.com/ivonasmith/uncleonchain/main/site/data/cal/cal_latest.json"]
BINARY_TYPES = {"FOMC", "CPI", "NFP", "REG", "VOTE"}   # REG/VOTE = 监管或立法的二元投票
VOTE_WORDS = ("投票", "表决", "vote")
HOUR = 3600000
DAY = 86400000
CACHE_MS = 3600000
BJ = timezone(timedelta(hours=8))
LEG_LIMIT_DAYS = 89
LEG_BREAK_PCT = 10.0
MA20_DROP_PCT = 5.0
REG_NOTE = "监管/立法二元投票不在日历里，需你自己留意"


def parse_event(raw):
    """返回 {title, type, start_ms, day(UTC 日期)} 或 None。只留二元事件；没有具体时刻的按当天 0 点（UTC）算。"""
    kind = str(raw.get("type") or "")
    title = str(raw.get("title_zh") or raw.get("title_en") or kind)
    if kind not in BINARY_TYPES and not any(word in title.lower() for word in VOTE_WORDS):
        return None
    try:
        if raw.get("time_utc"):
            start = datetime.strptime(raw["time_utc"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        else:
            start = datetime.strptime(raw["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (KeyError, ValueError):
        return None
    return {"title": title, "type": kind, "start": int(start.timestamp() * 1000), "day": start.date()}


def day_str(ms):
    return datetime.fromtimestamp(ms / 1000, BJ).strftime("%m-%d")


class Gates:
    def __init__(self, getter=monitor.http_json):
        self.getter = getter
        self.cal = (0, None)
        self.btc = (0, None)

    def events(self, now_ms):
        if self.cal[1] is not None and now_ms - self.cal[0] < CACHE_MS:
            return self.cal[1]
        for url in CAL_URLS:
            try:
                data = self.getter(url)
                raw = (data.get("btc") or {}).get("events")
                if isinstance(raw, list):
                    self.cal = (now_ms, [e for e in (parse_event(item) for item in raw) if e])
                    return self.cal[1]
            except Exception:  # noqa: BLE001
                continue
        return self.cal[1]   # 取不到：沿用旧缓存，没有就是 None

    def btc_ma20(self, now_ms):
        """返回 (收盘, MA20, 7日涨跌%) ；取不到返回 None。只用已收盘的日线。"""
        if self.btc[1] is not None and now_ms - self.btc[0] < CACHE_MS // 4:
            return self.btc[1]
        try:
            bars = [b for b in monitor.fetch_klines("BTC", "1d", 40, self.getter)[1] if b["close_time"] <= now_ms]
            closes = [b["close"] for b in bars]
            if len(closes) < 21:
                return None
            self.btc = (now_ms, (closes[-1], sum(closes[-20:]) / 20, (closes[-1] / closes[-8] - 1) * 100))
            return self.btc[1]
        except Exception:  # noqa: BLE001
            return None

    def leg(self, plan, now_ms):
        """天数直接用网站的（网站 = 最后一根已收盘 UTC 日线 − 起点）；老快照没有 dir / day 时按旧办法用起点算。"""
        leg = ((plan or {}).get("macro") or {}).get("leg") or {}
        if not leg.get("ok"):
            return None
        day = leg.get("day")
        if day is None:
            start = datetime.strptime(leg["start"], "%Y-%m-%d").date()
            day = (datetime.fromtimestamp(now_ms / 1000, BJ).date() - start).days
        return {"dir": leg.get("dir") or "up", "day": int(day), "dd": leg.get("dd"), "drop": leg.get("drop_pct"), "start": leg["start"]}

    def check(self, plan, card, now_ms):
        """返回 {blocks, passes, unchecked}。blocks 里的每一条都会让这单变成影子单。"""
        long = card["side"] == "多"
        blocks, passes, unchecked = [], [], []
        events = self.events(now_ms)
        if long:
            leg = self.leg(plan, now_ms)
            if leg is None:
                unchecked.append("BTC 涨跌腿（快照里没有网站的涨跌腿数据）")
            elif leg["dir"] == "down":
                drop = f"，高点 {leg['start']} 起最深 −{leg['drop']:.1f}%" if leg.get("drop") is not None else ""
                passes.append(f"BTC 当前是跌腿第{leg['day']}天{drop}：89天线只管涨腿，不亮")
            elif leg["day"] > LEG_LIMIT_DAYS and leg["dd"] < LEG_BREAK_PCT:
                blocks.append(f"宏观闸门·涨腿第{leg['day']}天（>{LEG_LIMIT_DAYS}天线）且腿内最大回撤 {leg['dd']:.1f}%（<{LEG_BREAK_PCT:.0f}%）：只减不加")
            elif leg["day"] > LEG_LIMIT_DAYS:
                passes.append(f"涨腿已被 {leg['dd']:.1f}% 回撤打断（起点 {leg['start']} 需人工重新确认），89天线不亮")
            else:
                passes.append(f"涨腿第{leg['day']}天，没过89天线")
            ma = self.btc_ma20(now_ms)
            if ma is None:
                unchecked.append("BTC日线MA20+7日跌幅（日线取不到）")
            elif ma[0] < ma[1] and ma[2] < -MA20_DROP_PCT:
                blocks.append(f"宏观闸门·BTC日线收盘 {ma[0]:,.0f} 在MA20 {ma[1]:,.0f} 下方，且7日跌 {ma[2]:.1f}%（>{MA20_DROP_PCT:.0f}%）")
            else:
                passes.append(f"BTC MA20+7日跌幅闸门不亮（收盘{'在' if ma[0] >= ma[1] else '低于'}MA20，7日 {ma[2]:+.1f}%）")
            if events is not None:
                soon = [e for e in events if now_ms < e["start"] <= now_ms + 2 * DAY]
                if len(soon) >= 2:
                    blocks.append("宏观闸门·48小时内二元事件叠加：" + "、".join(f"{e['title']}({day_str(e['start'])})" for e in soon))
                else:
                    passes.append(f"48小时内二元事件 {len(soon)} 个，不叠加")
        if events is None:
            unchecked.append("事件日历（读取失败：事件窗口和48小时叠加都没核对）")
        else:
            blocks.extend(self.window(plan, card, events, now_ms))
        unchecked.append(REG_NOTE)
        return {"blocks": blocks, "passes": passes, "unchecked": unchecked}

    def window(self, plan, card, events, now_ms):
        """事件窗口按“挂单那一刻”（计划生成时间）算。近价 = 分析时距现价 <1×日线ATR。"""
        placed = int(((plan or {}).get("built_at")) or now_ms)
        placed_day = datetime.fromtimestamp(placed / 1000, timezone.utc).date()
        atr = card.get("atr_d_pct")
        dist = (card.get("level") or {}).get("dist")
        near = dist is not None and atr is not None and abs(dist) < atr
        out = []
        for event in events:
            gap_days = (event["day"] - placed_day).days
            if gap_days in (0, -1):
                out.append(f"事件窗口·{event['title']}（{day_str(event['start'])}）当天/次日不挂任何新单")
            elif 0 < gap_days <= 3 and near:
                out.append(f"事件窗口·{event['title']}（{day_str(event['start'])}）前3天，距现价 {abs(dist):.1f}% 小于1×ATR（{atr:.1f}%）的单不挂")
        return out
