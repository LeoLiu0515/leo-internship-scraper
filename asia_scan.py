#!/usr/bin/env python3
"""
asia_scan.py -- Asia (Taiwan first; also Japan/Singapore/China/HK/Korea/SEA/India)
Summer 2027 embedded/hardware internship scanner for Leo.

Runs in GitHub Actions (LeoLiu0515/leo-internship-scraper) where outbound network is
unrestricted; writes asia.json. A small daily cloud routine then reads that file via
raw.githubusercontent.com and publishes the "Asia Internships" dashboard.

Sources (each wrapped so one failing source never kills the run):
  1. Workday CXS API for multinationals with Asian fabs/offices (full pagination)
  2. Dell Technologies via Oracle HCM public REST API (Taiwan/Asia)
  3. Yourator (Taiwan startup job board, public JSON API)
  4. Appier (Greenhouse, Taiwan)
  5. Community Singapore trackers on GitHub (sabersmash1412, CrunchyBiscuit19)

KNOWN GAPS (do not claim coverage): 104.com.tw / 1111 (Cloudflare/blocked), TSMC, MediaTek,
Quanta, ASE, Realtek and most Taiwan-native company portals (own sites, bot-blocked or no
public API), Japanese new-grad portals (Mynavi/Rikunabi), mainland China portals.
"""
import json, re, sys, time, urllib.request, urllib.parse, datetime as dt

NOW = time.time()
UA = {"User-Agent": "Mozilla/5.0 (asia-scan/1.0)"}

# ---------------------------------------------------------------- filters
ASIA_COUNTRIES = [
    ("Taiwan", r"taiwan|taipei|hsinchu|taoyuan|kaohsiung|taichung|tainan|台灣|臺灣|台北|臺北|新竹|桃園|台中|臺中|高雄|台南|臺南|新北|內湖|南港|竹北|\bTW\b"),
    ("Japan", r"japan|tokyo|osaka|yokohama|nagoya|kyoto|hiroshima|fukuoka|kanagawa|日本|東京|大阪|\bJP\b"),
    ("Singapore", r"singapore|\bSGP?\b"),
    ("Hong Kong", r"hong kong|\bHK\b|香港"),
    ("South Korea", r"korea|seoul|\bKR\b|한국"),
    ("China", r"china|shanghai|shenzhen|beijing|hangzhou|guangzhou|suzhou|chengdu|nanjing|wuhan|xi'an|\bCHN\b|\bPRC\b|上海|深圳|北京"),
    ("Malaysia", r"malaysia|penang|kulim|kuala lumpur|selangor|johor"),
    ("Vietnam", r"vietnam|ho chi minh|hanoi|ha noi"),
    ("Thailand", r"thailand|bangkok|chon buri"),
    ("Philippines", r"philippines|manila|cebu|laguna"),
    ("India", r"india|bangalore|bengaluru|hyderabad|pune|chennai|noida|gurgaon|gurugram|mumbai"),
]
ASIA_RE = [(n, re.compile(p, re.I)) for n, p in ASIA_COUNTRIES]

TITLE_EXCLUDE = ["phd", "ph.d", "mba", "sales", "marketing", "recruit", "legal", "supply chain",
                 "product manager", "product management", "program manager", "project manager",
                 "accounting", "accountant", "audit", "finance", "financial", "hr ", "human resources",
                 "talent", "procurement", "purchasing", "customer service", "weapon", "business",
                 "consult", "communications", "design (ux)", "ux ", "brand", "content", "analyst",
                 "administrat", "legal", "tax"]
TITLE_INCLUDE_EN = ["embedded", "firmware", "hardware", "fpga", "verilog", "rtl", "asic", "silicon",
                    "digital design", "analog", "mixed signal", "mixed-signal", "soc", "robot", "controls",
                    "control system", "signal processing", "dsp", "pcb", "board design", "validation",
                    "verification", "test engineer", "test solutions", "vlsi", "electrical", "electronic",
                    "computer engineering", "device", "sensor", "power", "gpu", "physical design",
                    "circuit", "layout", "dft", "design for test", "semiconductor", "photonic", "rf ",
                    "wireless", "mechatronic", "system design", "systems design", "systems engineer",
                    "product engineer", "process engineer", "equipment engineer", "yield", "reliability",
                    "packaging", "failure analysis", "ic design", "ic validation", "npi", "automation",
                    "instrument", "metrology", "memory", "dram", "nand", "foundry", "fab", "microcontroller",
                    "mcu", "iot", "compiler", "computer architecture", "diagnostics", "thermal", "perception"]
TITLE_INCLUDE_LOCAL = ["嵌入式", "韌體", "固件", "硬體", "硬件", "電路", "电路", "電子", "电子", "晶片", "芯片",
                       "半導體", "半导体", "製程", "制程", "設備", "设备", "驗證", "验证", "類比", "模擬", "射頻",
                       "機器人", "控制", "訊號", "信号", "光電", "封裝", "良率", "測試", "测试", "電機", "电机",
                       "組込", "組み込み", "ハードウェア", "電気", "電子", "半導体", "回路", "ファームウェア",
                       "IC設計", "IC设计", "版圖", "FPGA", "ASIC", "RTL", "IC "]
INTERN_RE = re.compile(r"\bintern(ship)?s?\b|co-?op\b|實習|实习|インターン|\bstudent\b|trainee|working student|university (hire|grad)|summer", re.I)
INTERN_STRICT = re.compile(r"\bintern(ship)?s?\b|co-?op\b|實習|实习|インターン", re.I)
BAD_TERM = re.compile(r"\b(spring|fall|winter|autumn)\b", re.I)


def wb(needle, hay):
    return re.search(r"\b" + re.escape(needle) + r"\b", hay) is not None


def title_ok(title):
    t = title.lower()
    if not INTERN_STRICT.search(title):
        return False
    if any(b in t for b in TITLE_EXCLUDE):
        return False
    if re.search(r"\bmaster'?s\b", t) and not re.search(r"bachelor", t):
        return False
    if any(wb(g.strip(), t) if g.strip() == g and " " not in g else (g in t) for g in TITLE_INCLUDE_EN):
        return True
    return any(g in title for g in TITLE_INCLUDE_LOCAL)


def term_ok(title):
    t = title.lower()
    if BAD_TERM.search(t):
        return False
    years = re.findall(r"\b(20\d{2})\b", t)
    if years and "2027" not in years:
        return False
    return True


def country_of(loc):
    for name, rx in ASIA_RE:
        if rx.search(loc or ""):
            return name
    return None


def http_json(url, data=None, headers=None, timeout=40):
    h = dict(UA)
    if headers:
        h.update(headers)
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, data=data, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:  # transient 5xx / timeouts (e.g. Micron 520) -> retry
            last = e
            time.sleep(2 + attempt * 3)
    raise last


def parse_posted_days(s):
    """Workday 'Posted 3 Days Ago' / 'Posted Today' / 'Posted 30+ Days Ago' -> int days"""
    if not s:
        return None
    s = s.lower()
    if "today" in s:
        return 0
    if "yesterday" in s:
        return 1
    m = re.search(r"(\d+)\+?\s*day", s)
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------- source 1: Workday
WORKDAY = [
    # (tenant, wd host number, site, display name)
    ("nvidia", 5, "NVIDIAExternalCareerSite", "NVIDIA"),
    ("micron", 1, "External", "Micron"),
    ("intel", 1, "External", "Intel"),
    ("analogdevices", 1, "External", "Analog Devices"),
    ("cisco", 5, "Cisco_Careers", "Cisco"),
    ("marvell", 1, "MarvellCareers", "Marvell"),
    ("marvell", 1, "MarvellCareers2", "Marvell"),
    ("globalfoundries", 1, "External", "GlobalFoundries"),
    ("nxp", 3, "careers", "NXP"),
    ("amat", 1, "External", "Applied Materials"),
    ("kla", 1, "Search", "KLA"),
    ("cadence", 1, "External_Careers", "Cadence"),
    ("microchiphr", 5, "External", "Microchip"),
    ("hpe", 5, "Jobsathpe", "HPE"),
]


def load_workday():
    out = []
    for tenant, wd, site, name in WORKDAY:
        base = f"https://{tenant}.wd{wd}.myworkdayjobs.com"
        try:
            got, total = [], None
            for off in range(0, 3400, 20):
                body = json.dumps({"limit": 20, "offset": off, "searchText": "intern"}).encode()
                d = http_json(f"{base}/wday/cxs/{tenant}/{site}/jobs", data=body,
                              headers={"Content-Type": "application/json"})
                if d.get("total"):
                    total = d["total"]
                page = d.get("jobPostings", [])
                got += page
                if not page or off + 20 >= (total or 0):
                    break
            n_before = len(out)
            for g in got:
                title, loc = g.get("title", ""), g.get("locationsText", "") or ""
                ctry = country_of(loc)
                if not ctry:
                    continue
                days = parse_posted_days(g.get("postedOn"))
                out.append({"company": name, "title": title, "loc": loc[:60], "country": ctry,
                            "url": base + "/" + site + g.get("externalPath", ""),
                            "age": days if days is not None else 7, "src": "workday-" + tenant})
            print(f"  workday {tenant}/{site}: scanned {len(got)}/{total}, asia intern-ish rows {len(out)-n_before}", file=sys.stderr)
        except Exception as e:
            print(f"  ! workday {tenant}/{site} failed: {e}", file=sys.stderr)
    return out


# ---------------------------------------------------------------- source 2: Dell (Oracle HCM)
def load_dell():
    out = []
    try:
        base = "https://enterpriseplatform.dell.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
        finder = "findReqs;siteNumber=CX,limit=200,keyword=intern,sortBy=POSTING_DATES_DESC"
        url = base + "?onlyData=true&expand=requisitionList.secondaryLocations&finder=" + urllib.parse.quote(finder, safe=";=,")
        d = http_json(url)
        reqs = d["items"][0].get("requisitionList", [])
        for r in reqs:
            loc = r.get("PrimaryLocation", "") or ""
            ctry = country_of(loc)
            if not ctry:
                continue
            try:
                posted = dt.datetime.strptime(r.get("PostedDate", ""), "%Y-%m-%d").timestamp()
                age = int((NOW - posted) / 86400)
            except Exception:
                age = 7
            out.append({"company": "Dell Technologies", "title": r.get("Title", ""), "loc": loc[:60], "country": ctry,
                        "url": "https://jobs.dell.com/en/job/x/" + str(r.get("Id")) if False else
                               "https://enterpriseplatform.dell.com/hcmUI/CandidateExperience/en/sites/CX/job/" + str(r.get("Id")),
                        "age": age, "src": "dell-oracle"})
        print(f"  dell: {len(reqs)} reqs, asia rows {len(out)}", file=sys.stderr)
    except Exception as e:
        print("  ! dell failed:", e, file=sys.stderr)
    return out


# ---------------------------------------------------------------- source 3: Yourator (Taiwan)
YOURATOR_TERMS = ["實習", "嵌入式", "韌體", "硬體", "電機", "電子", "硬體工程師 實習", "FPGA", "IC 設計", "半導體 實習"]


def load_yourator():
    out, seen = [], set()
    for term in YOURATOR_TERMS:
        try:
            for page in range(1, 6):
                url = "https://www.yourator.co/api/v4/jobs?term%5B%5D=" + urllib.parse.quote(term) + f"&page={page}"
                d = http_json(url)["payload"]
                for j in d.get("jobs", []):
                    if j["id"] in seen:
                        continue
                    seen.add(j["id"])
                    out.append({"company": (j.get("company") or {}).get("brand", ""), "title": j.get("name", ""),
                                "loc": (j.get("location") or "")[:60], "country": "Taiwan",
                                "url": "https://www.yourator.co" + j.get("path", ""), "age": 7, "src": "yourator"})
                if not d.get("hasMore"):
                    break
        except Exception as e:
            print(f"  ! yourator term {term} failed: {e}", file=sys.stderr)
    print(f"  yourator: {len(out)} unique rows (pre-filter)", file=sys.stderr)
    return out


# ---------------------------------------------------------------- source 4: Appier (Greenhouse)
def load_appier():
    out = []
    try:
        d = http_json("https://boards-api.greenhouse.io/v1/boards/appier/jobs")
        for j in d.get("jobs", []):
            loc = (j.get("location") or {}).get("name", "")
            ctry = country_of(loc)
            if ctry:
                out.append({"company": "Appier", "title": j.get("title", ""), "loc": loc[:60], "country": ctry,
                            "url": j.get("absolute_url", ""), "age": 7, "src": "greenhouse-appier"})
        print(f"  appier: asia rows {len(out)}", file=sys.stderr)
    except Exception as e:
        print("  ! appier failed:", e, file=sys.stderr)
    return out


# ---------------------------------------------------------------- source 5: SG community trackers
def load_sg_trackers():
    out = []
    try:
        d = http_json("https://raw.githubusercontent.com/sabersmash1412/singapore-internship-tracker/main/data/jobs.json", timeout=60)
        for j in d.get("jobs", []):
            if j.get("is_open") is False:
                continue
            url = j.get("url") or j.get("apply_url") or j.get("link") or ""
            if not url:
                continue
            loc = j.get("location") or ""
            out.append({"company": j.get("company", ""), "title": j.get("title", ""), "loc": loc[:60],
                        "country": country_of(loc) or "Singapore", "url": url,
                        "age": _age_iso(j.get("posted_at") or j.get("first_seen_at")), "src": "sg-saber"})
        print(f"  sg-saber: rows {len(out)}", file=sys.stderr)
    except Exception as e:
        print("  ! sg-saber failed:", e, file=sys.stderr)
    try:
        d = http_json("https://raw.githubusercontent.com/CrunchyBiscuit19/Automated-List-Of-Summer-2027-and-Fall-2026-Tech-Internships/main/docs/api/jobs.json", timeout=60)
        n = 0
        for j in d.get("jobs", []):
            loc = j.get("location") or ""
            ctry = country_of(loc)
            if not ctry or not j.get("url"):
                continue
            out.append({"company": j.get("company", ""), "title": j.get("title", ""), "loc": loc[:60], "country": ctry,
                        "url": j["url"], "age": _age_iso(j.get("posted_at") or j.get("first_seen_at")), "src": "sg-crunchy"})
            n += 1
        print(f"  sg-crunchy: rows {n}", file=sys.stderr)
    except Exception as e:
        print("  ! sg-crunchy failed:", e, file=sys.stderr)
    return out


def _age_iso(s):
    try:
        t = dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        return max(0, int((NOW - t) / 86400))
    except Exception:
        return 7


# ---------------------------------------------------------------- main
def main():
    rows = []
    for fn in (load_workday, load_dell, load_yourator, load_appier, load_sg_trackers):
        rows += fn()
    kept, seen = [], set()
    for r in rows:
        if not r["url"] or not r["title"]:
            continue
        if not title_ok(r["title"]) or not term_ok(r["title"]):
            continue
        key = (r["company"].lower().strip(), r["title"].lower().strip(), r["country"])
        ukey = r["url"].split("?")[0]
        if key in seen or ukey in seen:
            continue
        seen.add(key); seen.add(ukey)
        if r["age"] > 60 and r["age"] != 999:
            continue
        kept.append(r)
    kept.sort(key=lambda j: (j["country"] != "Taiwan", j["age"]))
    json.dump({"generated_at": NOW, "count": len(kept), "jobs": kept},
              open("asia.json", "w", encoding="utf-8"), ensure_ascii=False)
    from collections import Counter
    print("total kept:", len(kept))
    print("by country:", dict(Counter(j["country"] for j in kept)))
    print("by source:", dict(Counter(j["src"] for j in kept)))


if __name__ == "__main__":
    main()
