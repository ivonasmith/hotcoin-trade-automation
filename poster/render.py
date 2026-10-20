"""Render the four posters with poster_gen.py + Chromium (2400px desktop / 1080px mobile, scale 2). No image model."""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import poster_gen as pg  # noqa: E402
from build_data import build, verify  # noqa: E402


def names(issue):
    return {("zh", "desktop"): f"poster_zh_第{issue}期.png", ("zh", "mobile"): f"poster_zh_第{issue}期-手机版.png",
            ("en", "desktop"): f"poster_en_第{issue}期.png", ("en", "mobile"): f"poster_en_第{issue}期-手机版.png"}


def html_set(payload):
    data, rows, closed = build(payload)
    pg.DATA.clear(); pg.DATA.update(data)
    pg.ROWS[:] = rows
    pg.CLOSED[:] = closed
    htmls = {(lang, dev): pg.build(lang, dev) for lang in ("zh", "en") for dev in ("desktop", "mobile")}
    problems = verify(htmls, payload)
    if problems:
        raise ValueError("海报自检未通过：" + "；".join(problems))
    return htmls


def render(payload, outdir, launch=None):
    htmls = html_set(payload)
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    files = {}
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        options = {}
        if os.environ.get("CHROMIUM_PATH"):
            options["executable_path"] = os.environ["CHROMIUM_PATH"]
        browser = p.chromium.launch(**options)
        for (lang, dev), name in names(payload["issue"]).items():
            page = browser.new_page(viewport={"width": 2400 if dev == "desktop" else 1080, "height": 1200}, device_scale_factor=2)
            page.set_content(htmls[(lang, dev)])
            page.wait_for_timeout(200)
            path = out / name
            page.screenshot(path=str(path), full_page=True)
            page.close()
            files[name] = path
        browser.close()
    return files
