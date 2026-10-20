import sys
from playwright.sync_api import sync_playwright

configs = [
    ("poster_zh_desktop.html", 2400, "zh_desktop.png"),
    ("poster_zh_mobile.html", 1080, "zh_mobile.png"),
    ("poster_en_desktop.html", 2400, "en_desktop.png"),
    ("poster_en_mobile.html", 1080, "en_mobile.png"),
]

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path="/opt/pw-browsers/chromium")
    for html_file, width, out in configs:
        page = browser.new_page(viewport={"width": width, "height": 1200}, device_scale_factor=2)
        page.goto(f"file://{__import__('os').path.abspath(html_file)}")
        page.wait_for_timeout(200)
        page.screenshot(path=out, full_page=True)
        page.close()
    browser.close()
print("done")
