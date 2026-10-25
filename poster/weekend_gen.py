# -*- coding: utf-8 -*-
"""热币分析 · 周日「下周清单」海报生成器（事件预览 + 本周总结版，不含订单；中英各一张）。
沿用《热币海报标准》的色板/字体/落款；工作日「今日方向总表」生成器不动。
内容全部来自 weekly_pack.py 的 pack（日历 + 台账 + 日线收盘价），这里只管排版，不写死任何数字。
用法：python3 weekend_gen.py <pack.json> <输出目录>"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import weekly_pack as wp  # noqa: E402

HANDLE = "@Uncle_Onchain"

CSS = """
:root{--ink:#1b1b1b;--brown:#6f5b4b;--muted:#8a7a6a;--hair:#e6e0d3;--olive:#8C6C1F;--gray:#8A8477;--blue:#1A6694;
 --green:#1C7A4A;--red:#C02128;
 --serif:Georgia,'Times New Roman','Noto Serif CJK SC','Songti SC',serif;
 --mono:'JetBrains Mono','Liberation Mono','Courier New',monospace;
 --sans:-apple-system,'PingFang SC','Noto Sans CJK SC','Microsoft YaHei',Helvetica,Arial,sans-serif}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:var(--sans);color:var(--ink);background:#fff;width:1080px}
.bar{height:11px;background:#146290}
.wrap{padding:34px 40px 30px}
.kicker{font-family:var(--mono);font-weight:700;color:var(--olive);letter-spacing:.5px;font-size:21px}
h1{font-family:var(--serif);font-weight:500;font-size:62px;margin-top:6px;line-height:1.1}
.sub{font-family:var(--mono);font-size:24px;color:var(--brown);margin-top:8px}
.tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:26px}
.tile{background:#F9F7F2;border:1px solid var(--hair);padding:16px 14px 14px}
.tile .v{font-family:var(--mono);font-weight:800;font-size:31px;color:var(--blue);line-height:1.1;white-space:nowrap}
.tile .l{font-size:19px;color:var(--gray);margin-top:8px;line-height:1.3}
.sec{margin-top:30px}
.sech{display:flex;align-items:baseline;gap:14px;margin-bottom:12px}
.sech .t{font-family:var(--serif);font-weight:700;font-size:38px}
.sech .n{font-size:20px;color:var(--muted)}
.card{border:1px solid var(--hair);padding:22px 24px}
.card.red{border-left:7px solid var(--red);background:#FDF6F5}
.card.gold{border-left:7px solid #E3CD84;background:#FFFBEF}
.badge{display:inline-block;font-size:18px;font-weight:700;color:#fff;background:var(--red);padding:3px 14px;border-radius:999px;vertical-align:middle}
.eh{font-family:var(--serif);font-weight:700;font-size:36px}
.et{font-family:var(--mono);font-size:24px;color:var(--brown);margin:8px 0 10px}
.ep{font-size:26px;line-height:1.6;color:#333}
.two{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.box{padding:20px 22px;border:1px solid var(--hair)}
.box.a{background:#FDF6F5}.box.w{background:#F3F8FB}
.box h3{font-size:28px;font-weight:700;margin-bottom:10px}
.box.a h3{color:var(--red)}.box.w h3{color:var(--blue)}
.box li{font-size:23px;line-height:1.55;color:#333;margin:0 0 8px 20px}
.ev{width:100%;border-collapse:collapse}
.ev td{padding:11px 6px;border-bottom:1px solid var(--hair);font-size:24px;vertical-align:top}
.ev td.d{font-family:var(--mono);font-size:21px;color:var(--brown);white-space:nowrap;width:190px}
.ev td.c{color:var(--muted);text-align:right;font-size:21px;white-space:nowrap}
.note{font-size:20px;color:var(--muted);margin-top:10px;line-height:1.5}
.sum li{font-size:25px;line-height:1.6;color:#222;margin:0 0 10px 24px}
.honest{margin-top:14px;font-size:21px;color:var(--brown);background:#F9F7F2;border:1px solid var(--hair);padding:12px 16px;line-height:1.5}
.foot{margin-top:28px;padding-top:16px;border-top:1px solid var(--hair);display:flex;justify-content:space-between;gap:20px;align-items:flex-end}
.foot .l{font-size:17px;color:var(--gray);line-height:1.5;flex:1 1 auto;min-width:0}
.foot .r{text-align:right;white-space:nowrap}
.foot .s{font-family:var(--serif);font-size:28px;font-weight:700}
.foot .h{font-family:var(--mono);font-size:17px;color:var(--gray)}
"""



def names(day):
    return {"zh_desktop": f"poster_zh_周日下周清单_{day}.png", "en_desktop": f"poster_en_周日下周清单_{day}.png"}


def esc(x):
    return (str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def build(pack, lang):
    t = wp.view(pack, lang)
    li = lambda xs: "".join(f"<li>{esc(x)}</li>" for x in xs)
    rows = lambda items: "".join(f'<tr><td class="d">{esc(a)}</td><td>{esc(b)}</td><td class="c">{esc(c)}</td></tr>' for a, b, c in items)
    if t["events"]:
        cards = "".join(
            f'''<div class="card red" style="margin-bottom:12px"><div><span class="eh">{esc(e['h'])}</span> &nbsp;<span class="badge">{esc(t['s1b'])}</span></div>
 <div class="et">{esc(e['t'])}</div><div class="ep">{esc(e['p'])}</div></div>''' for e in t["events"])
    else:
        cards = f'<div class="card gold"><div class="ep">{esc(t["no_event"])}</div></div>'
    watch_block = ""
    if t["avoid"] or t["watch"]:
        watch_block = f'''<div class="sec"><div class="sech"><span class="t">{t['s2']}</span></div>
 <div class="two"><div class="box a"><h3>{t['avoid_h']}</h3><ul>{li(t['avoid']) or '<li>—</li>'}</ul></div>
 <div class="box w"><h3>{t['watch_h']}</h3><ul>{li(t['watch']) or '<li>—</li>'}</ul></div></div></div>'''
    if t["unl"] is None:
        unl_html = f'<div class="note">{esc(t["unl_empty"])}</div>'
    elif t["unl"]:
        unl_html = f'<table class="ev">{rows(t["unl"])}</table><div class="note">{esc(t["scn"])}</div>'
    else:
        unl_html = f'<div class="note">{esc(t["unl_empty"])}</div><div class="note">{esc(t["scn"])}</div>'
    conf_html = f'<table class="ev">{rows(t["conf"])}</table>' if t["conf"] else f'<div class="note">{esc(t["s3_empty"])}</div>'
    hack_html = ""
    if t["hack"]:
        hack_html = (f'<div style="font-size:22px;font-weight:700;color:var(--brown);margin-top:16px">{t["hack_h"]}</div>'
                     f'<table class="ev">' + "".join(f'<tr><td class="d">{esc(a)}</td><td colspan="2">{esc(b)}</td></tr>' for a, b in t["hack"]) + "</table>")
    tiles = "".join(f'<div class="tile"><div class="v">{esc(t[k+"v"])}</div><div class="l">{esc(t[k+"l"])}</div></div>' for k in ("t1", "t2", "t3", "t4"))
    return f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><style>{CSS}</style></head><body>
<div class="bar"></div><div class="wrap">
<div class="kicker">{esc(t['kicker'])}</div>
<h1>{t['title']}</h1><div class="sub">{esc(t['sub'])}</div>
<div class="tiles">{tiles}</div>
<div class="sec"><div class="sech"><span class="t">{t['s1']}</span></div>{cards}</div>
{watch_block}
<div class="sec"><div class="sech"><span class="t">{t['sc']}</span></div>{unl_html}</div>
<div class="sec"><div class="sech"><span class="t">{t['s3']}</span></div>
 {conf_html}{hack_html}
 <div class="note">{esc(t['s3n'])}</div></div>
<div class="sec"><div class="sech"><span class="t">{t['s4']}</span><span class="n">{esc(t['s4n'])}</span></div>
 <div class="card gold"><ul class="sum">{li(t['recap'])}</ul></div>
</div>
<div class="foot"><div class="l">{esc(t['foot'])}<br>{esc(t['src'])}</div><div class="r"><div class="s">{t['sig']}</div><div class="h">{HANDLE}</div></div></div>
</div></body></html>"""


def check(pack, htmls):
    """自检：英文版不许出现中文；海报上的关键数字必须来自 pack。"""
    problems = []
    text_en = re.sub(r"<style>.*?</style>", "", htmls["en"], flags=re.S)
    if re.search(r"[\u4e00-\u9fff]", text_en):
        problems.append("英文版出现中文字符")
    acc = pack["account"]
    musts = []
    if acc["ret_pct"] is not None:
        musts.append(wp._pct(acc["ret_pct"]))
    for e in pack["events"]:
        musts.append(e["time_bj"][11:16])
    for lang, h in htmls.items():
        for k in musts:
            if k not in h:
                problems.append(f"{lang}缺少 {k}")
    return problems


def render(pack, outdir, launch=None):
    htmls = {l: build(pack, l) for l in ("zh", "en")}
    problems = check(pack, htmls)
    if problems:
        raise ValueError("海报自检未通过：" + "；".join(problems))
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    import os
    from playwright.sync_api import sync_playwright
    files = {}
    with sync_playwright() as pw:
        options = {}
        if os.environ.get("CHROMIUM_PATH"):
            options["executable_path"] = os.environ["CHROMIUM_PATH"]
        br = pw.chromium.launch(**options)
        for lang, key in (("zh", "zh_desktop"), ("en", "en_desktop")):
            name = names(pack["day"])[key]
            (out / f"weekend_{lang}.html").write_text(htmls[lang], encoding="utf-8")
            pg = br.new_page(viewport={"width": 1080, "height": 1200}, device_scale_factor=2)
            pg.set_content(htmls[lang])
            pg.wait_for_timeout(300)
            path = out / name
            pg.screenshot(path=str(path), full_page=True)
            pg.close()
            files[name] = path
        br.close()
    return files


if __name__ == "__main__":
    pack = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    for name, path in render(pack, sys.argv[2] if len(sys.argv) > 2 else ".").items():
        print(path)
