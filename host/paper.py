"""纸面交易账本：监控提醒 → 机会卡（价位、止损、止盈、仓位）→ 纸面单 / 影子单 → 自动跟踪 → 复盘。

和 Notion 公开台账完全分开，只存在主机本地（HOTCOIN_PAPER_FILE，默认 /var/lib/hotcoin/paper.json）。
规矩（和《操作规格》v6.5 一致，参数在 cloud/paper.mjs）：
- 15 分钟数据只负责触发；是不是机会，由云端按日线快照算好的“机会卡”决定（多：顺势买回踩，不抄底；空：做空筛选三道门）。
- 账户 $10,000、2 倍杠杆；每笔名义 $2,000 × 仓位倍数；名义总仓位 ≤ $20,000；主流币同向 ≤ 3 张（利润已锁定的不计）；同一个币只留一张纸面持仓。
- 熔断：连续 3 笔止损 → 新单减半，直到赢一笔；连续 5 笔止损 / 已实现回撤 > 1.5% / 已实现利润回吐 > 30% → 暂停新纸面单
  （纸面简化：暂停 3 天后自动恢复并重置基准；实盘要写完书面复盘才恢复）。
- 成交：限价单，提醒触发（触及进场价）即按进场价成交。
- 止损按日线收盘价；同一根线里止损和目标都到，保守按止损。目标是限价，触及即止盈（4R）。
- 浮盈到 2R 止损移到进场价，到 3R 起止损跟随日线 MA20（只往盈利方向移）；持仓最多 20 个交易日，到期按收盘价了结。
- 成本：每笔来回 0.1% 名义。
- 没过闸门的机会不开真单，但同样按这套规则跟踪成“影子单”，用来检验闸门是挡对了还是挡错了。
"""
import csv
import io
import json
import statistics
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import monitor

BJ = timezone(timedelta(hours=8))
DAY_MS = 86400000
DEFAULT_RULES = {"account_usd": 10000, "leverage": 2, "base_usd": 2000, "max_notional_usd": 20000,
                 "max_same_side_major": 3, "major": ["BTC", "ETH", "SOL", "XRP", "BNB"], "max_hold_days": 20,
                 "fee_pct": 0.1}
HALT_DAYS = 3
DD_LIMIT_PCT = 1.5
GIVEBACK_LIMIT = 0.30
TOUCH_KINDS = ("up", "down")
UP_KINDS = {"up", "dayup", "h4up", "surge"}
DOWN_KINDS = {"down", "daydown", "h4down", "plunge"}
FWD_DAYS = (1, 3, 7)
KLINE_LIMIT = 60


def fmt_price(value):
    n = float(value)
    if n >= 1000:
        return f"{n:,.2f}"
    if n >= 1:
        return f"{n:.4f}".rstrip("0").rstrip(".")
    return f"{float(f'{n:.4g}'):g}"


def fmt_usd(value, signed=False):
    sign = ("+" if value >= 0 else "-") if signed else ("-" if value < 0 else "")
    return f"{sign}${abs(value):,.0f}" if abs(value) >= 100 else f"{sign}${abs(value):,.2f}"


def stamp(ms):
    return datetime.fromtimestamp(ms / 1000, BJ).strftime("%m-%d %H:%M")


def new_state():
    return {"version": 1, "next_id": 1, "alerts": [], "trades": [], "halt": None, "baseline_ts": 0,
            "pending_notices": [], "last_review_week": "", "last_request": 0}


def load_state(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return {**new_state(), **data}
    except (OSError, ValueError):
        return new_state()


def save_state(state, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


# ---------- 模拟：一张单在日线上怎么走 ----------
def simulate(trade, bars, now_ms, rules=None):
    """按日线重放一张单（无状态、可重复算）。bars 是日线（含正在走的那根）。返回要写回 trade 的字段。"""
    rules = {**DEFAULT_RULES, **(rules or {})}
    long = trade["side"] == "多"
    d = 1 if long else -1
    entry, target = trade["entry"], trade["target"]
    risk = abs(entry - trade["stop0"])
    stop, stage = trade["stop0"], 0
    mfe = mae = 0.0
    held = 0
    exit_info = None
    last_close = None
    closes = [b["close"] for b in bars]
    for i, bar in enumerate(bars):
        if bar["close_time"] <= trade["ts"]:
            continue
        closed = bar["close_time"] <= now_ms
        last_close = bar["close"]
        fav = (bar["high"] - entry) / risk if long else (entry - bar["low"]) / risk
        adv = (entry - bar["low"]) / risk if long else (bar["high"] - entry) / risk
        mfe, mae = max(mfe, fav), max(mae, adv)
        if closed and ((bar["close"] <= stop) if long else (bar["close"] >= stop)):
            exit_info = ("stop", bar["close"], bar["close_time"])   # 同一根里止损和目标都到：保守按止损
            break
        if (bar["high"] >= target) if long else (bar["low"] <= target):
            exit_info = ("target", target, min(bar["close_time"], now_ms))
            break
        if fav >= 2 and stage < 1:
            stage, stop = 1, (max(stop, entry) if long else min(stop, entry))
        if fav >= 3 and stage < 2:
            stage = 2
        if closed:
            held += 1
            if stage == 2 and i >= 19:
                ma20 = sum(closes[i - 19:i + 1]) / 20
                stop = max(stop, ma20) if long else min(stop, ma20)
            if held >= rules["max_hold_days"]:
                exit_info = ("timeout", bar["close"], bar["close_time"])
                break
    out = {"stop_now": stop, "stage": stage, "mfe_r": round(mfe, 2), "mae_r": round(mae, 2), "days_held": held,
           "status": "open", "exit": None, "r": None, "pnl_usd": None, "last_price": last_close}
    fee = trade["notional_usd"] * rules["fee_pct"] / 100
    if exit_info:
        reason, price, at = exit_info
        out["status"] = "closed"
        out["exit"] = {"reason": reason, "price": price, "ts": int(at)}
        out["r"] = round(d * (price - entry) / risk, 2)
        out["pnl_usd"] = round(trade["notional_usd"] * d * (price - entry) / entry - fee, 2)
    elif last_close is not None:
        out["floating_r"] = round(d * (last_close - entry) / risk, 2)
        out["floating_usd"] = round(trade["notional_usd"] * d * (last_close - entry) / entry - fee, 2)
    return out


def is_locked(trade):
    """利润已锁定：止损已经越过进场价进入盈利一侧。"""
    stop = trade.get("stop_now", trade["stop0"])
    return stop > trade["entry"] if trade["side"] == "多" else stop < trade["entry"]


# ---------- 熔断 ----------
def risk_state(state, now_ms):
    """返回 {halted, until, reason, half, consec, dd_usd, giveback}，只看基准时间之后了结的纸面单。"""
    halt = state.get("halt")
    if halt and now_ms < halt["until"]:
        return {"halted": True, "until": halt["until"], "reason": halt["reason"], "half": False, "consec": 0}
    closed = sorted((t for t in state["trades"] if t["paper"] and t["status"] == "closed"
                     and t["exit"]["ts"] > state.get("baseline_ts", 0)), key=lambda t: t["exit"]["ts"])
    consec = 0
    for trade in reversed(closed):
        if trade["pnl_usd"] > 0:
            break
        if trade["exit"]["reason"] == "stop":
            consec += 1
    cum = peak = 0.0
    for trade in closed:
        cum += trade["pnl_usd"]
        peak = max(peak, cum)
    dd = peak - cum
    reason = None
    if consec >= 5:
        reason = f"连续 {consec} 笔止损"
    elif dd > DEFAULT_RULES["account_usd"] * DD_LIMIT_PCT / 100:
        reason = f"已实现回撤 {fmt_usd(dd)}（超过账户 {DD_LIMIT_PCT}%）"
    elif peak > 0 and dd / peak > GIVEBACK_LIMIT:
        reason = f"累计利润从峰值 {fmt_usd(peak)} 回吐 {dd / peak * 100:.0f}%（超过 30%）"
    return {"halted": False, "reason": reason, "half": consec >= 3, "consec": consec, "dd_usd": dd, "peak": peak,
            "cum": cum}


# ---------- 账本 ----------
class Paper:
    def __init__(self, path, rules=None, gates=None):
        self.gates = gates   # host/gates.Gates：宏观闸门和事件窗口；None 时卡片里原样写“未核对”
        self.path = Path(path)
        self.state = load_state(self.path)
        self.rules = {**DEFAULT_RULES, **(rules or {})}

    def save(self):
        save_state(self.state, self.path)

    # --- 提醒进来 ---
    def handle_alert(self, item, plan, now_ms, getter=monitor.http_json):
        """每条自动提醒触发时调用一次。返回要附在提醒后面的文字行和分类（写进 item["paper"]）。"""
        state = self.state
        card = ((plan or {}).get("cards") or {}).get(item["key"])
        touch = item["kind"] in TOUCH_KINDS and item.get("src") == "level"
        alert = {"id": state["next_id"], "ts": item["ts"], "sym": item["sym"], "kind": item["kind"], "key": item["key"],
                 "src": item.get("src"), "tag": item.get("tag", ""), "price": self.price_at(item, getter),
                 "cls": "info", "trade_id": None, "fwd": {}}
        state["next_id"] += 1
        lines = []
        if touch and card:
            rules = {**self.rules, **((plan or {}).get("paper") or {})}
            trade, lines = self.open_trade(alert, card, item, now_ms, rules, plan)
            alert["cls"] = "paper" if trade["paper"] else "shadow"
            alert["trade_id"] = trade["id"]
        elif item.get("src") == "ledger":
            alert["cls"] = "ledger"
            lines = ["  📒 台账实盘单相关提醒，不进纸面账本。"]
        elif item["kind"] in TOUCH_KINDS and item.get("src") in ("order", "level"):
            lines = ["  📒 仅记录：这个位置没有机会卡（慢档数据不可用或不是日线关键位），不开单。"]
        else:
            lines = ["  📒 仅记录：这类提醒不给进场位，不开单；只统计之后的走势，用于复盘这类提醒有没有用。"]
        state["alerts"].append(alert)
        self.save()
        return {"cls": alert["cls"], "alert": alert["id"], "trade": alert["trade_id"], "lines": lines}

    def price_at(self, item, getter):
        try:
            return monitor.fetch_klines(item["sym"], "15m", 1, getter)[1][-1]["close"]
        except Exception:  # noqa: BLE001
            return None

    def open_trade(self, alert, card, item, now_ms, rules, item_plan=None):
        state = self.state
        sym, side = card["sym"], card["side"]
        blocks = list(card.get("blocks") or [])
        size, notional = card["size"], card["notional_usd"]
        notes = []
        gate = None
        if self.gates is not None:
            try:
                gate = self.gates.check(item_plan, card, now_ms)
            except Exception as exc:  # noqa: BLE001
                gate = {"blocks": [], "passes": [], "unchecked": [f"闸门核对出错：{str(exc)[:60]}"]}
            blocks.extend(gate["blocks"])
        if card["qualified"] and not (gate and gate["blocks"]):
            risk = risk_state(state, now_ms)
            open_paper = [t for t in state["trades"] if t["paper"] and t["status"] == "open"]
            if risk["halted"]:
                blocks.append(f"熔断停机中（{risk['reason']}），到 {stamp(risk['until'])} 前不开新纸面单")
            elif risk.get("reason"):
                blocks.append(f"熔断触发：{risk['reason']}，暂停新纸面单")
            else:
                if risk["half"]:
                    size, notional = size * 0.5, round(notional * 0.5)
                    notes.append(f"熔断减半：已连续 {risk['consec']} 笔止损，仓位 ×0.5，赢一笔后恢复")
                if any(t["sym"] == sym for t in open_paper):
                    blocks.append(f"{sym} 已有一张纸面持仓，不重复开")
                total = sum(t["notional_usd"] for t in open_paper)
                if total + notional > rules["max_notional_usd"]:
                    blocks.append(f"名义总仓位会到 {fmt_usd(total + notional)}，超过 {fmt_usd(rules['max_notional_usd'])} 上限")
                if sym in rules["major"]:
                    same = [t for t in open_paper if t["sym"] in rules["major"] and t["side"] == side and not is_locked(t)]
                    if len(same) >= rules["max_same_side_major"]:
                        blocks.append(f"主流币同向{side}单已有 {len(same)} 张（上限 {rules['max_same_side_major']}，利润已锁定的不计）")
        paper = card["qualified"] and not blocks
        notional_usd = int(notional)
        stop_pct = card["stop_pct"]
        trade = {"id": state["next_id"], "alert_id": alert["id"], "ts": item["ts"], "sym": sym, "side": side,
                 "paper": paper, "entry": card["entry"], "stop0": card["stop"], "target": card["target"],
                 "r2": card["r2"], "r3": card["r3"], "stop_pct": stop_pct, "size": round(size, 4),
                 "notional_usd": notional_usd, "margin_usd": round(notional_usd / rules["leverage"]),
                 "risk_usd": round(notional_usd * stop_pct / 100, 2), "blocks": blocks, "passes": (card.get("passes") or []) + (gate["passes"] if gate else []),
                 "stop_now": card["stop"], "stage": 0, "status": "open", "exit": None, "r": None, "pnl_usd": None,
                 "mfe_r": 0, "mae_r": 0, "level": card.get("level")}
        state["next_id"] += 1
        state["trades"].append(trade)
        return trade, self.card_lines(trade, card, notes, rules, gate)

    # --- 文字 ---
    def card_lines(self, trade, card, notes, rules, gate=None):
        long = trade["side"] == "多"
        head = (f"纸面单 #{trade['id']} {trade['sym']} 做{trade['side']}｜已开（纸面，限价成交）" if trade["paper"]
                else f"不开纸面单（观察）：{trade['sym']} 若做{trade['side']}")
        lines = [f"  📒 {head}"]
        if trade["blocks"]:
            lines.append("  未过闸门：" + "；".join(trade["blocks"]))
            lines.append("  仍按同一套规则记一张影子单，之后看它的结果来检验闸门挡对没有。")
        lines.append(f"  进场 {fmt_price(trade['entry'])}｜止损 {fmt_price(trade['stop0'])}（{'-' if long else '+'}{card['stop_pct']:.2f}%，"
                     f"日线收盘{'跌破' if long else '站上'}才算，影线不算）")
        lines.append(f"  止盈 {fmt_price(trade['target'])}（4R，{'+' if long else '-'}{card['target_pct']:.2f}%）｜"
                     f"2R {fmt_price(trade['r2'])} 止损移到进场价｜3R {fmt_price(trade['r3'])} 起止损跟随日线MA20")
        lines.append(f"  仓位 {trade['size']:.2f}仓：名义 {fmt_usd(trade['notional_usd'])}、保证金 {fmt_usd(trade['margin_usd'])}"
                     f"（{rules['leverage']}倍杠杆，账户 {fmt_usd(rules['account_usd'])}）｜止损亏约 {fmt_usd(trade['risk_usd'])}"
                     f"（账户 {trade['risk_usd'] / rules['account_usd'] * 100:.2f}%）｜倍数：{card.get('size_parts', '')}")
        if trade["passes"]:
            lines.append("  依据：" + "；".join(trade["passes"]))
        lines.extend("  " + note for note in notes)
        if trade["paper"]:
            open_paper = [t for t in self.state["trades"] if t["paper"] and t["status"] == "open"]
            total = sum(t["notional_usd"] for t in open_paper)
            lines.append(f"  组合：纸面持仓 {len(open_paper)} 张，名义 {fmt_usd(total)}/{fmt_usd(rules['max_notional_usd'])}")
        unchecked = gate["unchecked"] if gate else (card.get("unchecked") or [])
        if unchecked:
            lines.append("  未核对（程序读不到，需你留意）：" + "；".join(u.split('（')[0] if gate is None else u for u in unchecked))
        return lines

    # --- 每轮：更新、通知、复盘 ---
    def cycle(self, now_ms, getter=monitor.http_json, notify=None, quiet=False, request_ts=0, send_files=None):
        errors = []
        try:
            self.update(now_ms, getter, errors)
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc)[:120])
        notices = self.state["pending_notices"]
        if notify and notices and not quiet:
            try:
                notify("【纸面交易】\n" + "\n".join(notices[:12]))
                self.state["pending_notices"] = notices[12:]
            except Exception as exc:  # noqa: BLE001
                errors.append("发送了结通知失败：" + str(exc)[:80])
        self.review_if_due(now_ms, notify, send_files, request_ts, errors)
        self.save()
        return errors

    def update(self, now_ms, getter, errors):
        state = self.state
        syms = {t["sym"] for t in state["trades"] if t["status"] == "open"}
        horizon = now_ms - (max(FWD_DAYS) + 1) * DAY_MS
        syms |= {a["sym"] for a in state["alerts"] if a["ts"] > horizon and a.get("price")
                 and any(f"d{n}" not in a["fwd"] for n in FWD_DAYS)}
        bars = {}
        for sym in sorted(syms):
            try:
                bars[sym] = monitor.fetch_klines(sym, "1d", KLINE_LIMIT, getter)[1]
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{sym} 日线拉取失败：{str(exc)[:80]}")
        for trade in state["trades"]:
            if trade["status"] != "open" or trade["sym"] not in bars:
                continue
            result = simulate(trade, bars[trade["sym"]], now_ms, self.rules)
            trade.update(result)
            if trade["status"] == "closed":
                self.close_notice(trade)
        for alert in state["alerts"]:
            if alert["sym"] not in bars or not alert.get("price"):
                continue
            for n in FWD_DAYS:
                if f"d{n}" in alert["fwd"]:
                    continue
                target = alert["ts"] + n * DAY_MS
                bar = next((b for b in bars[alert["sym"]] if b["close_time"] >= target and b["close_time"] <= now_ms), None)
                if bar:
                    alert["fwd"][f"d{n}"] = round((bar["close"] / alert["price"] - 1) * 100, 2)
        risk = risk_state(state, now_ms)
        if risk.get("reason") and not risk["halted"] and not state.get("halt"):
            self.start_halt(risk["reason"], now_ms)
        elif state.get("halt") and now_ms >= state["halt"]["until"]:
            state["baseline_ts"] = state["halt"]["until"]
            state["pending_notices"].append("纸面账户熔断暂停结束，基准已重置，恢复开新纸面单。")
            state["halt"] = None

    def start_halt(self, reason, now_ms):
        self.state["halt"] = {"since": now_ms, "until": now_ms + HALT_DAYS * DAY_MS, "reason": reason}
        self.state["pending_notices"].append(f"纸面账户触发熔断：{reason}。暂停新纸面单 {HALT_DAYS} 天（影子单照常记录）。")

    def close_notice(self, trade):
        label = {"stop": "止损" if trade["stage"] == 0 else "移动止损", "target": "止盈", "timeout": "到期了结"}[trade["exit"]["reason"]]
        kind = "纸面单" if trade["paper"] else "影子单"
        self.state["pending_notices"].append(
            f"{kind} #{trade['id']} {trade['sym']} 做{trade['side']} {label}：进场 {fmt_price(trade['entry'])} → 出场 "
            f"{fmt_price(trade['exit']['price'])}，{trade['r']:+.2f}R，{fmt_usd(trade['pnl_usd'], True)}"
            f"（持有 {trade['days_held']} 个交易日，最大浮盈 {trade['mfe_r']}R）")

    def review_if_due(self, now_ms, notify, send_files, request_ts, errors):
        state = self.state
        moment = datetime.fromtimestamp(now_ms / 1000, BJ)
        week = moment.strftime("%G-W%V")
        weekly = moment.weekday() == 6 and moment.hour >= 21 and state["last_review_week"] != week
        requested = request_ts and request_ts > state.get("last_request", 0)
        if not (weekly or requested) or not notify:
            return
        try:
            notify(self.review_text(now_ms))
            if send_files:
                stamp_day = moment.strftime("%Y%m%d")
                for name, content in self.review_files(now_ms, stamp_day):
                    send_files(name, content, "纸面交易完整记录")
            if weekly:
                state["last_review_week"] = week
            if requested:
                state["last_request"] = request_ts
        except Exception as exc:  # noqa: BLE001
            errors.append("发送复盘失败：" + str(exc)[:80])

    # --- 复盘 ---
    def review_files(self, now_ms, stamp_day):
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        columns = ["id", "类别", "开仓时间", "币", "方向", "进场", "止损", "目标", "仓位倍数", "名义USD", "状态", "结果R", "盈亏USD",
                   "最大浮盈R", "最大浮亏R", "持有天", "出场原因", "未过闸门"]
        writer.writerow(columns)
        for t in self.state["trades"]:
            writer.writerow([t["id"], "纸面" if t["paper"] else "影子", stamp(t["ts"]), t["sym"], t["side"], t["entry"], t["stop0"],
                             t["target"], t["size"], t["notional_usd"], t["status"], t["r"], t["pnl_usd"], t["mfe_r"], t["mae_r"],
                             t.get("days_held"), (t["exit"] or {}).get("reason", ""), "；".join(t["blocks"])])
        return [(f"paper-journal-{stamp_day}.json", json.dumps(self.state, ensure_ascii=False, indent=1).encode("utf-8")),
                (f"paper-trades-{stamp_day}.csv", ("﻿" + buffer.getvalue()).encode("utf-8"))]

    def review_text(self, now_ms):
        state = self.state
        paper = [t for t in state["trades"] if t["paper"]]
        shadow = [t for t in state["trades"] if not t["paper"]]
        lines = [f"【纸面交易复盘】统计到 {stamp(now_ms)}（北京时间）｜账户 {fmt_usd(self.rules['account_usd'])}，{self.rules['leverage']}倍杠杆"]
        closed = [t for t in paper if t["status"] == "closed"]
        openp = [t for t in paper if t["status"] == "open"]
        realized = sum(t["pnl_usd"] for t in closed)
        floating = sum(t.get("floating_usd", 0) for t in openp)
        risk = risk_state(state, now_ms)
        status = (f"熔断停机至 {stamp(risk['until'])}（{risk['reason']}）" if risk["halted"]
                  else "熔断：新单减半（连续 %d 笔止损）" % risk["consec"] if risk["half"] else "熔断：正常")
        lines.append(f"账户：已实现 {fmt_usd(realized, True)}（{realized / self.rules['account_usd'] * 100:+.2f}%），"
                     f"在场 {len(openp)} 张浮动 {fmt_usd(floating, True)}｜{status}")
        alerts = state["alerts"]
        count = lambda cls: sum(1 for a in alerts if a["cls"] == cls)  # noqa: E731
        lines.append(f"提醒共 {len(alerts)} 条：合格开纸面单 {count('paper')}、未过闸门（影子单）{count('shadow')}、"
                     f"仅记录 {count('info')}、台账相关 {count('ledger')}")
        lines.append("")
        lines.append("—— 纸面单（过了闸门，真开）——")
        lines.extend(self.trade_lines(paper) or ["（还没有）"])
        lines.append("")
        lines.append("—— 影子单（没过闸门，没真开）——")
        lines.extend(self.trade_lines(shadow, limit=8) or ["（还没有）"])
        lines.append("")
        lines.extend(self.compare_lines(paper, shadow))
        lines.extend(self.info_lines(alerts))
        text = "\n".join(lines)
        return text if len(text) <= 3400 else text[:3380] + "\n…（完整记录见随后的文件）"

    def trade_lines(self, trades, limit=10):
        rows = []
        for t in sorted(trades, key=lambda x: x["ts"], reverse=True)[:limit]:
            head = f"#{t['id']} {stamp(t['ts'])} {t['sym']}{t['side']} 进{fmt_price(t['entry'])} 损{fmt_price(t['stop0'])} 盈{fmt_price(t['target'])} {fmt_usd(t['notional_usd'])}"
            if t["status"] == "closed":
                label = {"stop": "止损" if t["stage"] == 0 else "移动止损", "target": "止盈", "timeout": "到期"}[t["exit"]["reason"]]
                verdict = "真机会" if t["pnl_usd"] > 0 else "假机会"
                rows.append(f"· {head}｜{label} {t['r']:+.2f}R {fmt_usd(t['pnl_usd'], True)}｜{verdict}")
            else:
                rows.append(f"· {head}｜进行中 {t.get('floating_r', 0):+.2f}R（最大浮盈 {t['mfe_r']}R，止损现在 {fmt_price(t['stop_now'])}）")
        return rows

    def compare_lines(self, paper, shadow):
        def stats(trades):
            closed = [t for t in trades if t["status"] == "closed"]
            if not closed:
                return None
            wins = sum(1 for t in closed if t["pnl_usd"] > 0)
            return len(closed), wins, statistics.mean(t["r"] for t in closed), sum(t["pnl_usd"] for t in closed)
        a, b = stats(paper), stats(shadow)
        fmt = lambda s: "了结 0 笔" if not s else f"了结 {s[0]} 笔，赢 {s[1]}（{s[1] / s[0] * 100:.0f}%），平均 {s[2]:+.2f}R，合计 {fmt_usd(s[3], True)}"  # noqa: E731
        lines = ["—— 闸门检验 ——", f"过闸门：{fmt(a)}", f"没过闸门：{fmt(b)}"]
        if not a or not b or a[0] + b[0] < 20:
            lines.append("样本太少（了结合计不到 20 笔），先不下结论，只看数。")
        else:
            gap = a[2] - b[2]
            lines.append(f"过闸门比没过的平均多 {gap:+.2f}R。" + ("闸门有区分度。" if gap > 0.3 else "区分度不明显，闸门可能没起作用。"))
        reasons = {}
        for t in shadow:
            if t["status"] != "closed":
                continue
            for block in t["blocks"][:1]:
                key = block.split("（")[0].split("：")[0][:24]
                bucket = reasons.setdefault(key, [0, 0])
                bucket[0] += 1
                bucket[1] += 1 if t["pnl_usd"] > 0 else 0
        for key, (n, w) in sorted(reasons.items(), key=lambda kv: -kv[1][0])[:5]:
            lines.append(f"· 被「{key}」挡掉：了结 {n} 笔，其中会赚的 {w} 笔")
        lines.append("")
        return lines

    def info_lines(self, alerts):
        lines = ["—— 仅记录类提醒之后的走势（顺着提醒方向的涨跌幅中位数）——"]
        groups = {}
        for a in alerts:
            if a["cls"] != "info":
                continue
            sign = 1 if a["kind"] in UP_KINDS else -1 if a["kind"] in DOWN_KINDS else 0
            if not sign:
                continue
            for n in FWD_DAYS:
                value = a["fwd"].get(f"d{n}")
                if value is not None:
                    groups.setdefault((a["kind"], n), []).append(sign * value)
        if not groups:
            return lines + ["（还没有够 1 天的数据）"]
        for kind in ("up", "down", "dayup", "daydown", "h4up", "h4down", "surge", "plunge"):
            parts = [f"{n}日 {statistics.median(groups[(kind, n)]):+.1f}%（{len(groups[(kind, n)])}条）" for n in FWD_DAYS if groups.get((kind, n))]
            if parts:
                lines.append(f"· {monitor.TEXT.get(kind, kind)}：" + "，".join(parts))
        return lines
