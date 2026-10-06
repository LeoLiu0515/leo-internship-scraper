import re, sys, json, urllib.request
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
def plain(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-TW,zh;q=0.9", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            b = r.read().decode("utf-8", "replace"); return r.status, b
    except Exception as e:
        return getattr(e, "code", "ERR"), str(e)[:80]
print("== plain HTTP from Actions IP ==")
for name, url, h in [
    ("104 api", "https://www.104.com.tw/jobs/search/api/jobs?keyword=%E5%AF%A6%E7%BF%92&page=1&order=15&jobsource=index_s", {"Referer": "https://www.104.com.tw/jobs/search/"}),
    ("yes123", "https://www.yes123.com.tw/wk_index/joblist.asp?find_key1=%E5%AF%A6%E7%BF%92", None),
    ("NCU", "https://careercenter.ncu.edu.tw/internship", None),
    ("NTU", "https://career.ntu.edu.tw/board/index/tab/5", None),
    ("1111", "https://www.1111.com.tw/search/job?ks=%E5%AF%A6%E7%BF%92&col=da&sort=desc", None),
]:
    st, body = plain(url, h)
    print(f"{name}: {st} len={len(body)} :: {body[:80]!r}")
print("== Playwright ==")
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
    ctx = b.new_context(user_agent=UA, locale="zh-TW", viewport={"width": 1366, "height": 900})
    page = ctx.new_page()
    for name, url in [("104 search", "https://www.104.com.tw/jobs/search/?keyword=%E5%AF%A6%E7%BF%92&order=15&jobsource=index_s"),
                      ("1111 search", "https://www.1111.com.tw/search/job?ks=%E5%AF%A6%E7%BF%92&col=da&sort=desc"),
                      ("cake", "https://www.cake.me/jobs/%E6%9A%91%E6%9C%9F%E5%AF%A6%E7%BF%92"),
                      ("mediatek", "https://careers.mediatek.com/en/jobs")]:
        try:
            page.goto(url, timeout=45000, wait_until="domcontentloaded")
            page.wait_for_timeout(9000)
            links = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
            jobish = [l for l in links if re.search(r"/job/|/jobs/|/job\?|job_id|jobNo", l)]
            print(f"{name}: title={page.title()!r} links={len(links)} jobish={len(jobish)} sample={jobish[:2]}")
        except Exception as e:
            print(f"{name}: ERR {str(e)[:100]}")
    b.close()
