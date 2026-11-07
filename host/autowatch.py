"""15 分钟监控 · 自动规则：读后台的“今日监控计划”（GET /pipeline/plan，每天取数后由分析结果生成），
拉币安合约 K 线，条件到了发 Telegram 提醒，提醒里带当日分析的上下文。只报数据，不给买卖建议。

和手动 /watch 规则（monitor.py）分开管：自动规则的触发状态、冷却、节流都存在主机本地（auto_monitor.json），
不占 /watch 的 30 条上限，也不受自动提醒的每日上限影响。

节流（默认值，可用环境变量 HOTCOIN_AUTO_QUIET / HOTCOIN_AUTO_CAP 改）：
- 同一价位：触发后至少 6 小时，并且价格要离开价位 ≥ 0.5×4h ATR（计划里的 rearm_pct）才重新生效；急涨急跌 1 小时冷却。
- 同一币：1 小时内最多发 1 次（同一轮里同一个币的多条合并成一块）；被压住的暂存，到点补发，12 小时没发出去就丢弃。
- 夜间静默（北京时间 02:00–07:00）：非台账规则暂存，静默结束后一条汇总补发。
- 每日上限 12 条（北京时间 07:00 起算一天）：超过后只放行台账在场单相关，并提示一次。
- 台账在场单相关的规则（src=ledger）不受静默、同币间隔和每日上限限制，但仍受价位冷却限制。
"""
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import monitor

BJ = timezone(timedelta(hours=8))
HOUR_MS = 3600 * 1000
COOLDOWN_MS = 6 * HOUR_MS          # 同一价位
VOL_COOLDOWN_MS = HOUR_MS          # 急涨急跌
COIN_GAP_MS = HOUR_MS              # 同一币两次提醒之间
HELD_MAX_MS = 12 * HOUR_MS         # 暂存的提醒最多留多久
ALERT_DAY_SHIFT_H = 7              # 提醒日从北京时间 07:00 起算
MSG_LIMIT = 3400                   # 后台单条消息上限是 3500 字
MAX_HELD = 200
ABORT_AFTER_FAILURES = 5           # 连续这么多次拉不到行情就放弃本轮剩下的，下一轮再来
DEFAULTS = {"quiet": (2 * 60, 7 * 60), "cap": 12}   # 静默起止（北京时间一天里的第几分钟）、每日上限
INTERVAL_OF = {**monitor.INTERVAL_OF, "near": "15m"}
UP_KINDS = {"up", "dayup", "h4up"}
DOWN_KINDS = {"down", "daydown", "h4down"}
BAR_KINDS = {"dayup", "daydown", "h4up", "h4down", "surge", "plunge"}   # 每根收盘线只判一次


# ---------- 设置与状态 ----------
def parse_quiet(text):
    """'02:00-07:00' → (120, 420)；'off' / 空 → None（不静默）。写错了就用默认值。"""
    value = str(text or "").strip().lower()
    if value in ("off", "none", "no", "0"):
        return None
    try:
        start, end = value.split("-")
        parse = lambda part: int(part.split(":")[0]) * 60 + int(part.split(":")[1] if ":" in part else 0)
        a, b = parse(start), parse(end)
        if 0 <= a < 1440 and 0 <= b < 1440 and a != b:
            return (a, b)
    except (ValueError, IndexError):
        pass
    return DEFAULTS["quiet"]


def settings_from_env(env):
    settings = dict(DEFAULTS)
    if "HOTCOIN_AUTO_QUIET" in env:
        settings["quiet"] = parse_quiet(env["HOTCOIN_AUTO_QUIET"])
    try:
        cap = int(env.get("HOTCOIN_AUTO_CAP", DEFAULTS["cap"]))
        settings["cap"] = cap if cap > 0 else DEFAULTS["cap"]
    except (TypeError, ValueError):
        pass
    return settings


def new_state():
    return {"rules": {}, "bars": {}, "coin_last": {}, "held": [], "day": None, "sent_today": 0,
            "suppressed_today": 0, "cap_notice": False, "bad_symbols": []}


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


def bj_dt(now_ms):
    return datetime.fromtimestamp(now_ms / 1000, BJ)


def stamp(ms_value):
    return bj_dt(ms_value).strftime("%m-%d %H:%M")


def in_quiet(now_ms, quiet):
    if not quiet:
        return False
    moment = bj_dt(now_ms)
    minute = moment.hour * 60 + moment.minute
    start, end = quiet
    return start <= minute < end if start < end else (minute >= start or minute < end)


def alert_day(now_ms):
    return (bj_dt(now_ms) - timedelta(hours=ALERT_DAY_SHIFT_H)).strftime("%Y-%m-%d")


# ---------- 判断 ----------
def evaluate_near(rule, bars, created):
    """价格进入价位 ±band% 以内。用最新一根（可能还没收盘）的收盘价当现价。"""
    if not bars or bars[-1]["close_time"] <= created:
        return None
    value, band = float(rule["value"]), float(rule.get("band") or 0.5)
    price = bars[-1]["close"]
    gap = (price / value - 1) * 100
    if abs(gap) <= band:
        return f"{rule['sym']} 价格接近 {monitor.fmt(value)}（±{band:g}% 内）：现价 {monitor.fmt(price)}，距价位 {gap:+.2f}%"
    return None


def is_away(rule, price, rearm_pct):
    """价格有没有离开价位足够远（重新武装的条件之一）。"""
    kind, value = rule["kind"], float(rule["value"])
    if kind in UP_KINDS:
        return price <= value * (1 - rearm_pct / 100)
    if kind in DOWN_KINDS:
        return price >= value * (1 + rearm_pct / 100)
    if kind == "near":
        return abs(price / value - 1) * 100 >= 2 * float(rule.get("band") or 0.5)
    return True


def latest_closed_open(bars, now_ms):
    closed = [bar for bar in bars if bar["close_time"] < now_ms]
    return closed[-1]["open_time"] if closed else None


# ---------- 一轮 ----------
def run_auto_cycle(plan, state, notify, now_ms=None, getter=monitor.http_json, settings=None, paper=None):
    """跑一轮。notify(text) 发一条 Telegram。返回给 /host 看的摘要。"""
    now_ms = now_ms or int(time.time() * 1000)
    settings = {**DEFAULTS, **(settings or {})}
    day = alert_day(now_ms)
    if state.get("day") != day:
        state.update(day=day, sent_today=0, suppressed_today=0, cap_notice=False)
    summary = {"rules": 0, "fired": 0, "sent": 0, "errors": [], "status": "ok", "built_at": None,
               "sent_today": 0, "cap": settings["cap"], "held": 0, "suppressed": 0}
    new_items = []
    if not plan or not plan.get("rules"):
        summary["status"] = "没有计划"
    elif now_ms >= int(plan.get("expires_at") or 0):
        summary["status"] = "计划已过期"
        summary["built_at"] = plan.get("built_at")
    else:
        summary["built_at"] = plan.get("built_at")
        summary["rules"] = len(plan["rules"])
        new_items = evaluate_rules(plan, state, now_ms, getter, summary)
        summary["fired"] = len(new_items)
        if paper:   # 纸面账本：每条触发的提醒进账本，机会卡的价位/仓位附在提醒后面；账本出错不影响提醒本身
            for item in new_items:
                try:
                    item["paper"] = paper.handle_alert(item, plan, now_ms, getter)
                except Exception as exc:  # noqa: BLE001
                    summary["errors"].append("纸面账本：" + str(exc)[:100])
    deliver(plan, state, new_items, notify, now_ms, settings, summary)
    summary.update(sent_today=state["sent_today"], held=len(state["held"]), suppressed=state["suppressed_today"])
    return summary


def evaluate_rules(plan, state, now_ms, getter, summary):
    rules = sorted(plan["rules"], key=lambda r: 0 if r.get("src") == "ledger" else 1)
    coins = plan.get("coins") or {}
    live = {r["key"] for r in rules}
    states, seen = state.setdefault("rules", {}), state.setdefault("bars", {})
    for stale in [k for k in states if k not in live]:
        states.pop(stale)
    for stale in [k for k in seen if k not in live]:
        seen.pop(stale)
    bad = state.setdefault("bad_symbols", [])
    cache, failures, items = {}, 0, []

    def bars_for(sym, interval):
        nonlocal failures
        key = (sym, interval)
        if key in cache:
            return cache[key]
        if failures >= ABORT_AFTER_FAILURES:
            cache[key] = None
            return None
        try:
            cache[key] = monitor.fetch_klines(sym, interval, 3, getter)[1]
            failures = 0
        except monitor.NoSymbol:
            cache[key] = None
            if sym not in bad:
                bad.append(sym)
                summary["errors"].append(f"{sym} 在币安合约里找不到，这个币的自动规则不会触发")
        except Exception as exc:  # noqa: BLE001
            cache[key] = None
            failures += 1
            summary["errors"].append(str(exc)[:160])
            if failures >= ABORT_AFTER_FAILURES:
                summary["errors"].append(f"连续 {ABORT_AFTER_FAILURES} 次拉不到币安行情，本轮剩下的币留到下一轮")
        return cache[key]

    for rule in rules:
        interval = INTERVAL_OF.get(rule["kind"])
        if not interval:
            continue
        gate = None
        if rule.get("requires"):   # 成交后才监控的规则：进场价触及的提醒发出过，才开始判，且只看那之后收盘的线
            gate = (states.get(rule["requires"]) or {}).get("fired_at")
            if gate is None:
                continue
        bars = bars_for(rule["sym"], interval)
        if not bars:
            continue
        st = states.setdefault(rule["key"], {"fired_at": None, "armed": True, "away": False})
        created = max(int(rule.get("created") or plan.get("built_at") or 0), int(gate or 0))
        rearm = float((coins.get(rule["sym"]) or {}).get("rearm_pct") or 0.5)
        fired_at = st.get("fired_at")
        if fired_at is not None and not st.get("armed", True):
            quote = cache.get((rule["sym"], "15m")) or bars
            price = quote[-1]["close"]
            if is_away(rule, price, rearm):
                st["away"] = True
            cooldown = VOL_COOLDOWN_MS if rule["kind"] in ("surge", "plunge") else COOLDOWN_MS
            if st.get("away") and now_ms - int(fired_at) >= cooldown:
                st.update(armed=True, away=False)
            else:
                opened = latest_closed_open(bars, now_ms)
                if rule["kind"] in BAR_KINDS and opened is not None:
                    seen[rule["key"]] = opened   # 没武装期间收盘的线不补判
                continue
        pseudo = {"kind": rule["kind"], "value": rule["value"], "sym": rule["sym"],
                  "created": max(created, int(fired_at or 0)), "last_fired": None}
        if rule["kind"] == "near":
            text, bar_open = evaluate_near(rule, bars, pseudo["created"]), None
        else:
            text, bar_open = monitor.evaluate(pseudo, bars, now_ms, seen.get(rule["key"]))
        if bar_open is not None:
            seen[rule["key"]] = bar_open
        if text:
            st.update(fired_at=now_ms, armed=False, away=False)
            items.append({"key": rule["key"], "sym": rule["sym"], "kind": rule["kind"], "src": rule.get("src"),
                          "ledger": rule.get("src") == "ledger", "tag": rule.get("tag", ""), "note": rule.get("note", ""),
                          "text": text, "ts": now_ms})
    return items


def deliver(plan, state, new_items, notify, now_ms, settings, summary):
    """把本轮新触发的和之前暂存的放在一起，按静默、同币间隔、每日上限过一遍，能发的合成消息发出去。"""
    items = sorted(state.get("held", []) + new_items, key=lambda it: (not it.get("ledger"), it["ts"]))
    quiet = in_quiet(now_ms, settings.get("quiet"))
    cap = settings["cap"]
    keep, send, opened = [], [], set()
    planned = state["sent_today"]
    capped = False
    for item in items:
        if now_ms - item["ts"] > HELD_MAX_MS:
            state["suppressed_today"] += 1
            continue
        if item.get("ledger"):
            send.append(item)
            opened.add(item["sym"])
            planned += 1
            continue
        if quiet:
            keep.append(item)
            continue
        last = state["coin_last"].get(item["sym"])
        if item["sym"] not in opened and last is not None and now_ms - last < COIN_GAP_MS:
            keep.append(item)
            continue
        if planned >= cap:
            state["suppressed_today"] += 1
            capped = True
            continue
        send.append(item)
        opened.add(item["sym"])
        planned += 1
    notice = None
    if capped and not state.get("cap_notice"):
        notice = f"今日自动提醒已达 {cap} 条上限，之后只放行台账在场单相关的提醒；被压住的条数在 /host 里能看到。"
    if not send and not notice:
        state["held"] = keep[-MAX_HELD:]
        return
    texts = format_messages(plan, send, now_ms, notice)
    delivered = 0
    for index, text in enumerate(texts):
        try:
            notify(text)
        except Exception as exc:  # noqa: BLE001 - 没发出去的放回暂存，下一轮重发
            summary["errors"].append("发送提醒失败：" + str(exc)[:120])
            break
        delivered += 1
    sent_items = [it for it in send if it.get("_chunk", 0) < delivered]
    failed_items = [it for it in send if it.get("_chunk", 0) >= delivered]
    for item in sent_items:
        state["coin_last"][item["sym"]] = now_ms
    state["sent_today"] += len(sent_items)
    if notice and delivered == len(texts):
        state["cap_notice"] = True
    for item in failed_items:
        item.pop("_chunk", None)
    for item in sent_items:
        item.pop("_chunk", None)
    state["held"] = (keep + failed_items)[-MAX_HELD:]
    summary["sent"] = len(sent_items)


# ---------- 消息 ----------
def format_messages(plan, send, now_ms, notice=None):
    """按币分块；一条消息不超过 MSG_LIMIT 字，超了就分多条。每个 item 记下它进了第几条（_chunk）。"""
    coins = (plan or {}).get("coins") or {}
    analysed = stamp((plan or {}).get("snapshot_at") or (plan or {}).get("built_at") or now_ms)
    head = f"【监控提醒·自动】{stamp(now_ms)}（北京时间）"
    order = []
    for item in send:
        if item["sym"] not in order:
            order.append(item["sym"])
    blocks = []
    for sym in order:
        mine = [it for it in send if it["sym"] == sym]
        lines = []
        for it in mine:
            delayed = now_ms - it["ts"] > 2 * 60000
            prefix = f"[{stamp(it['ts'])} 触发，暂存后补发] " if delayed else ""
            lines.append(f"{prefix}{it['text']}")
            if it.get("tag"):
                lines.append(f"  ↳ {it['tag']}")
            lines.extend((it.get("paper") or {}).get("lines") or [])
        analysis = (coins.get(sym) or {}).get("analysis")
        if analysis:
            lines.append(f"  分析（取数 {analysed}）：{analysis}")
        for note in dict.fromkeys(it["note"] for it in mine if it.get("note")):
            lines.append(f"  {note}")
        blocks.append((mine, "\n".join(lines)))
    messages, current, current_items = [], [], []
    size = len(head)
    for mine, block in blocks:
        if current and size + len(block) + 2 > MSG_LIMIT:
            messages.append((current, current_items))
            current, current_items, size = [], [], len(head)
        current.append(block[:MSG_LIMIT - len(head) - 4])
        current_items.extend(mine)
        size += len(block) + 2
    if current:
        messages.append((current, current_items))
    texts = []
    for index, (parts, mine) in enumerate(messages):
        for it in mine:
            it["_chunk"] = index
        texts.append(head + "\n\n" + "\n\n".join(parts))
    if notice:
        if texts and len(texts[-1]) + len(notice) + 2 <= MSG_LIMIT:
            texts[-1] += "\n\n" + notice
        else:
            texts.append(head + "\n\n" + notice)
    return texts


def status_text(summary):
    """给 /host 看的一句话。"""
    if summary["status"] != "ok":
        return f"自动规则：{summary['status']}" + (f"，暂存{summary['held']}条" if summary["held"] else "")
    built = stamp(summary["built_at"]) if summary.get("built_at") else "未知"
    text = (f"自动规则{summary['rules']}条（计划 {built} 生成），本轮触发{summary['fired']}条，"
            f"今日已发{summary['sent_today']}/{summary['cap']}")
    if summary["held"]:
        text += f"，暂存{summary['held']}条"
    if summary["suppressed"]:
        text += f"，今日压住{summary['suppressed']}条"
    if summary["errors"]:
        text += f"，错误{len(summary['errors'])}"
    return text
