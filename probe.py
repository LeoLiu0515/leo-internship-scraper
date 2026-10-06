import re, sys, json, urllib.request, urllib.parse, collections
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
H = {"User-Agent": UA, "Referer": "https://www.104.com.tw/jobs/search/", "Accept": "application/json, text/plain, */*", "Accept-Language": "zh-TW,zh;q=0.9"}
def get(url):
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=40).read().decode("utf-8", "replace"))
def api(params):
    return get("https://www.104.com.tw/jobs/search/api/jobs?" + urllib.parse.urlencode(params, doseq=True))
def show(label, d, n=4):
    md = (d.get("metadata") or {}).get("pagination") or {}
    print(f"[{label}] total={md.get('total')} lastPage={md.get('lastPage')} rows={len(d.get('data', []))}")
    for r in d.get("data", [])[:n]:
        print("    -", r.get("appearDate"), "|", (r.get("custName") or "")[:22], "|", (r.get("jobName") or "")[:46], "| jobType", r.get("jobType"), "ro", r.get("jobRo"), "| s9", r.get("s9"))
print("== JobCat tree (top level) ==")
try:
    cats = get("https://static.104.com.tw/category-tool/json/JobCat.json")
    for c in cats:
        nm = c.get("des", "")
        if re.search(r"資訊|軟體|電子|電機|研發|半導體|工程|品保|生產|製造|機械", nm):
            print(c.get("no"), nm, "| children:", [(x.get("no"), x.get("des")) for x in c.get("n", [])][:14])
except Exception as e:
    print("JobCat ERR", e)
print("== ro (job nature) tests, keyword=實習 ==")
for ro in range(0, 9):
    try: show(f"ro={ro}", api({"keyword": "實習", "page": 1, "order": 15, "ro": ro, "mode": "s", "jobsource": "index_s"}), 2)
    except Exception as e: print("ro", ro, "ERR", str(e)[:60])
print("== jobcat tests, keyword=實習 ==")
for jc in ["2007000000", "2013000000", "2008000000", "2009000000", "2010000000"]:
    try: show(f"jobcat={jc}", api({"keyword": "實習", "page": 1, "order": 15, "jobcat": jc, "mode": "s", "jobsource": "index_s"}), 4)
    except Exception as e: print(jc, "ERR", str(e)[:60])
