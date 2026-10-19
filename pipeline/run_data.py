"""Run the existing collection scripts in a clean, disposable directory.

This only collects facts. It does not generate opinions, modify the trade ledger,
or publish posts. A failed collection can never fall back to yesterday's files.
"""
import argparse
import hashlib
import json
import math
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORE = {"BTC", "ETH", "SOL", "XRP", "BNB"}


def validate(summary, raw, now_ms):
    metrics = summary.get("指标")
    if not isinstance(metrics, dict):
        raise ValueError("没有结构化指标输出")
    missing = sorted(CORE - metrics.keys())
    if missing:
        raise ValueError("核心币缺失：" + ",".join(missing))
    fresh, excluded = [], {}
    for symbol, row in metrics.items():
        price = row.get("price")
        premium = raw.get(symbol, {}).get("prem") or {}
        source_time = premium.get("time")
        if not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
            excluded[symbol] = "无有效价格"
        elif not isinstance(source_time, (int, float)) or not -60000 <= now_ms-source_time <= 1800000:
            excluded[symbol] = "行情源时间戳缺失或超过30分钟"
        else:
            fresh.append(symbol)
    if CORE & excluded.keys():
        raise ValueError("核心币数据无法通过新鲜度检查：" + ",".join(sorted(CORE & excluded.keys())))
    return {"fresh_symbols": sorted(fresh), "excluded_symbols": excluded}


def run(output, extra):
    output.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat()
    attempt = output / ("collection-" + str(time.time_ns()))
    attempt.mkdir()
    manifest = json.loads((HERE / "source-manifest.json").read_text())
    with tempfile.TemporaryDirectory(prefix="hotcoin-collection-") as tmp:
        work = Path(tmp)
        for name, info in manifest.items():
            source = HERE / "vendor" / name
            if hashlib.sha256(source.read_bytes()).hexdigest() != info["vendor_sha256"]:
                raise RuntimeError("脚本发生变化，请先更新来源清单：" + name)
            shutil.copy2(source, work / name)
        try:
            with (attempt / "collection.log").open("w") as log:
                result = subprocess.run([sys.executable, str(work / "每日取数.py"), *extra],
                                        cwd=work, stdout=log, stderr=subprocess.STDOUT,
                                        timeout=2400, check=False)
            if result.returncode:
                raise RuntimeError("取数脚本退出码：" + str(result.returncode))
            summary = json.loads((work / "每日汇总.json").read_text())
            raw = json.loads((work / "raw.json").read_text())
            report = validate(summary, raw, time.time()*1000)
            for name in ["每日汇总.json", "送审稿四行.txt"]:
                if (work / name).exists():
                    shutil.copy2(work / name, attempt / name)
            report.update({"ok": True, "started_at": started,
                           "finished_at": datetime.now(timezone.utc).isoformat(),
                           "summary_sha256": hashlib.sha256((attempt / "每日汇总.json").read_bytes()).hexdigest()})
        except Exception as exc:
            report = {"ok": False, "started_at": started, "error": str(exc)}
            (attempt / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
            raise
        (attempt / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(str(attempt))
        return attempt


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=HERE.parent / "runtime")
    parser.add_argument("symbols", nargs="*")
    args = parser.parse_args()
    for symbol in args.symbols:
        if not symbol.isalnum() or len(symbol) > 30:
            parser.error("非法代币代码")
    run(args.output, args.symbols)
