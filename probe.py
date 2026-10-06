import re, sys, json, urllib.request, urllib.parse
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
def api(params):
    url = "https://www.104.com.tw/jobs/search/api/jobs?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://www.104.com.tw/jobs/search/", "Accept": "application/json, text/plain, */*", "Accept-Language": "zh-TW,zh;q=0.9"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))
d = api({"keyword": "實習", "page": 1, "order": 15, "jobsource": "index_s", "mode": "s"})
print("TOP KEYS", list(d.keys()))
print("METADATA", json.dumps(d.get("metadata"), ensure_ascii=False)[:400])
rows = d.get("data", [])
print("ROWS", len(rows))
print("ROW0 KEYS", sorted(rows[0].keys()))
for r in rows[:3]:
    print(json.dumps({k: r.get(k) for k in ("jobName", "custName", "jobAddrNoDesc", "appearDate", "link", "jobType", "jobRole", "salaryLow", "period", "optionEdu")}, ensure_ascii=False))
print("--- totals per keyword ---")
for kw in ["實習", "實習生", "暑期實習", "intern", "2027 實習", "韌體 實習", "硬體 實習", "IC 實習", "軟體 實習"]:
    try:
        dd = api({"keyword": kw, "page": 1, "order": 15, "jobsource": "index_s", "mode": "s"})
        md = (dd.get("metadata") or {}).get("pagination") or {}
        print(kw, "->", md.get("total"), "pages", md.get("lastPage"), "rows/page", len(dd.get("data", [])))
    except Exception as e:
        print(kw, "ERR", str(e)[:80])
print("--- deep page test ---")
try:
    dd = api({"keyword": "實習", "page": 30, "order": 15, "jobsource": "index_s", "mode": "s"})
    print("page30 rows", len(dd.get("data", [])), [x.get("jobName") for x in dd.get("data", [])[:2]])
except Exception as e:
    print("page30 ERR", str(e)[:100])
