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
import json, re, sys, time, html as htmllib, urllib.request, urllib.parse, datetime as dt

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
                 "procurement", "purchasing", "customer service", "weapon", 
                  
                 "administrat", "legal", 
                 "rdss", "研發替代役"]  # RDSS = Taiwan military-service substitute program (grad students with service obligation) -- not for Leo
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


TECH_RE = re.compile(
    r"engineer|engineering|developer|software|firmware|hardware|embedded|data|ai|ml|machine learning|algorithm|"
    r"research|r&d|ic|chip|silicon|semiconductor|electr|circuit|fpga|asic|soc|rtl|verif|valid|test|system|network|"
    r"cloud|security|cyber|iot|automation|robot|device|sensor|power|photon|optic|rf|wireless|analog|digital|process|"
    r"equipment|yield|reliab|packag|fab|manufactur|npi|devops|backend|frontend|full.?stack|mobile|ios|android|sre|"
    r"infrastructure|compiler|gpu|cuda|simulation|control|mechatron|programmer|programming|computer|it|information|"
    r"scientist|technolog|"
    r"工程|研發|研究|軟體|韌體|硬體|演算法|數據|資料|資訊|電機|電子|半導體|晶片|測試|製程|設備|系統|網路|雲端|資安|人工智慧|機器學習|自動化|機器人|光電|封裝|良率|嵌入式|程式|"
    r"エンジニア|開発|研究|ソフト|ハード|組込|半導体|回路|データ", re.I)
NON_ECE = ["mechanical engineer", "civil", "chemical engineer", "biomedical", "industrial engineer", "機構", "土木", "化工",
           "investment", "banking", "analyst, finance", "financial analyst", "business analyst", "business development",
           "customer success", "customer service", "public relations", "social media", "graphic", "copywrit", "legal",
           "paralegal", "esg", "sustainability", "quant", "trading", "risk", "clinical", "pharma", "mba", "accelerator program",
           "tax", "account operations", "campaign", "ehs", "business process", "business excellence", "業務", "顧問", "稅務",
           "consultant", "strategy", "operations intern", "account manager", "web3", "crypto", "hr ", "人資", "行政"]


TECH_COMPANIES = re.compile(
    r"tsmc|台積|mediatek|聯發科|realtek|瑞昱|novatek|聯詠|nvidia|qualcomm|intel|micron|amd|arm|marvell|broadcom|"
    r"texas instruments|nxp|infineon|asml|applied materials|lam research|kla|synopsys|cadence|delta|台達|foxconn|鴻海|"
    r"quanta|廣達|asus|華碩|acer|宏碁|compal|仁寶|wistron|緯創|pegatron|和碩|inventec|英業達|ase|日月光|phison|群聯|"
    r"macronix|旺宏|winbond|華邦|nuvoton|新唐|alchip|世芯|guc|創意電子|andes|晶心|hynix|samsung|apple|google|microsoft|"
    r"amazon|meta|cisco|dell|hp|lenovo|synology|群暉|moxa|gogoro|appier|garmin|sony|panasonic|toshiba|renesas|"
    r"rohm|hitachi|nec|fujitsu|tokyo electron|kioxia|canon|globalfoundries|umc|聯電|vanguard|世界先進|powerchip|力積電|"
    r"analog devices|microchip|keysight|teradyne|cadence|ansys|siemens|bosch|schneider|abb|honeywell|ericsson|nokia", re.I)


def title_ok(title, company="", trusted=False):
    """Leo (2026-10-05): 'I do not pick, anything ECE-related is fine' -> broad: any intern/co-op title that looks
    technical, minus obvious business/finance/HR/mechanical roles."""
    t = title.lower()
    if not INTERN_STRICT.search(title):
        return False
    if any(b in t for b in TITLE_EXCLUDE) or any(b in t for b in NON_ECE):
        return False
    if re.search(r"master'?s", t) and not re.search(r"bachelor", t):
        return False
    return trusted or TECH_RE.search(title) is not None or TECH_COMPANIES.search(company or "") is not None


def term_ok(title):
    t = title.lower()
    if BAD_TERM.search(t):
        return False
    years = re.findall(r"(?<!\d)(20\d{2})(?!\d)", t)  # also catches 'Y2026', '2026實習' (no \b next to CJK)
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
                    out.append({"company": (j.get("company") or {}).get(""), "title": j.get("name", ""),
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


# ---------------------------------------------------------------- source 6: LinkedIn public guest search
# LinkedIn indexes Taiwanese/Asian company postings whose own portals block bots (TSMC, MediaTek, Compal, ...).
# Public guest endpoint, no login. f_JT=I = Internship job type, f_TPR=r2592000 = last 30 days.
LI_QUERIES = {
    # location string -> keywords (Taiwan gets the deepest coverage)
    "Taiwan": ["intern", "internship", "實習", "實習生", "2027 intern", "summer intern", "engineer intern",
               "software engineer intern", "hardware intern", "firmware intern", "IC design intern",
               "semiconductor intern", "data intern", "AI intern", "電機 實習", "工程師 實習", "研發 實習",
               "暑期實習", "韌體 實習", "硬體 實習", "軟體 實習"],
    "Singapore": ["intern", "internship", "2027 intern", "engineer intern", "software intern", "hardware intern"],
    "Japan": ["intern", "internship", "インターン", "2027 intern", "engineer intern", "software intern"],
    "Hong Kong": ["intern", "internship", "2027 intern", "engineer intern", "software intern"],
    "South Korea": ["intern", "internship", "2027 intern", "engineer intern", "software intern"],
    "China": ["intern", "internship", "2027 intern", "engineer intern", "software intern"],
    "Malaysia": ["intern", "internship", "engineer intern"],
    "Thailand": ["intern", "internship", "engineer intern"],
    "Vietnam": ["intern", "internship", "engineer intern"],
}
LI_BASE = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"


def _li_fetch(params):
    url = LI_BASE + "?" + urllib.parse.urlencode(params)
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
                                                       "Accept-Language": "en-US,en;q=0.9"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (429, 999, 503):
                time.sleep(15 + attempt * 20)
                continue
            return ""
        except Exception:
            time.sleep(3)
    return ""


def load_linkedin():
    out, seen = [], set()
    stats = {}
    for loc, kws in LI_QUERIES.items():
        n_loc = 0
        for kw in kws:
            empty_streak = 0
            for start in range(0, 100, 10):  # up to 10 pages of 10
                html = _li_fetch({"keywords": kw, "location": loc, "f_JT": "I", "f_TPR": "r2592000", "start": start})
                cards = re.findall(r"<li>(.*?)</li>", html, re.S)
                if not cards:
                    break
                new_here = 0
                for c in cards:
                    m_url = re.search(r'href="(https://[a-z.]*linkedin\.com/jobs/view/[^"?]+)', c)
                    m_t = re.search(r'base-search-card__title[^>]*>\s*(.*?)\s*</h3>', c, re.S)
                    m_c = re.search(r'base-search-card__subtitle[^>]*>(.*?)</h4>', c, re.S)
                    m_l = re.search(r'job-search-card__location[^>]*>\s*(.*?)\s*</span>', c, re.S)
                    m_d = re.search(r'datetime="(\d{4}-\d{2}-\d{2})"', c)
                    if not (m_url and m_t):
                        continue
                    u = m_url.group(1)
                    if u in seen:
                        continue
                    seen.add(u); new_here += 1
                    strip = lambda x: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", x or "")).strip()
                    title, comp, where = htmllib.unescape(strip(m_t.group(1))), htmllib.unescape(strip(m_c.group(1) if m_c else "")), htmllib.unescape(strip(m_l.group(1) if m_l else ""))
                    try:
                        age = max(0, int((NOW - dt.datetime.strptime(m_d.group(1), "%Y-%m-%d").timestamp()) / 86400)) if m_d else 7
                    except Exception:
                        age = 7
                    out.append({"company": comp, "title": title, "loc": where[:60], "country": country_of(where) or loc,
                                "url": u, "age": age, "src": "linkedin"})
                n_loc += new_here
                empty_streak = empty_streak + 1 if new_here == 0 else 0
                if empty_streak >= 2:
                    break
                time.sleep(1.2)
            time.sleep(1.0)
        stats[loc] = n_loc
    print("  linkedin raw rows by query-location:", stats, file=sys.stderr)
    return out


# ---------------------------------------------------------------- source 7: 104 job bank (Taiwan's biggest local board)
# The public JSON API answers from GitHub's network (it 403s residential/other IPs). Restricted to tech-relevant
# 104 job categories so keyword "實習" does not drag in restaurant part-time jobs.
J104_CATS = [("2007000000", "資訊軟體系統"), ("2008000000", "研發"), ("2009000000", "生產製造/品管"), ("2010000000", "操作/技術/維修")]
J104_KWS = ["實習", "intern", "暑期實習"]
J104_MAX_PAGES = 45


def _104_api(params):
    url = "https://www.104.com.tw/jobs/search/api/jobs?" + urllib.parse.urlencode(params)
    return http_json(url, headers={"Referer": "https://www.104.com.tw/jobs/search/", "Accept": "application/json, text/plain, */*",
                                   "Accept-Language": "zh-TW,zh;q=0.9"})


def load_104():
    out, seen = [], set()
    for cat, cat_name in J104_CATS:
        n_cat = 0
        for kw in J104_KWS:
            old_streak = 0
            for page in range(1, J104_MAX_PAGES + 1):
                try:
                    d = _104_api({"keyword": kw, "page": page, "order": 15, "jobcat": cat, "mode": "s", "jobsource": "index_s"})
                except Exception as e:
                    print(f"  ! 104 {cat_name}/{kw}/p{page} failed: {e}", file=sys.stderr)
                    break
                rows = d.get("data", [])
                if not rows:
                    break
                n_old = 0
                for r in rows:
                    link = ((r.get("link") or {}).get("job") or "").split("?")[0]
                    if not link or link in seen:
                        continue
                    seen.add(link)
                    try:
                        age = max(0, int((NOW - dt.datetime.strptime(str(r.get("appearDate")), "%Y%m%d").timestamp()) / 86400))
                    except Exception:
                        age = 7
                    if age > 75:
                        n_old += 1
                    out.append({"company": r.get("custName") or "", "title": r.get("jobName") or "",
                                "loc": ((r.get("jobAddrNoDesc") or "") + " " + (r.get("jobAddress") or ""))[:60].strip(),
                                "country": "Taiwan", "url": link, "age": age, "src": "104", "trusted": True})
                    n_cat += 1
                old_streak = old_streak + 1 if n_old >= len(rows) - 1 else 0
                if old_streak >= 2:
                    break
                time.sleep(0.35)
        print(f"  104 {cat_name}: {n_cat} raw rows", file=sys.stderr)
    return out


# ---------------------------------------------------------------- source 8: university career-center announcements (Taiwan)
SCHOOL_BOARDS = [("NCU", "https://careercenter.ncu.edu.tw/internship")]


def load_school_boards():
    out = []
    for name, url in SCHOOL_BOARDS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA["User-Agent"], "Accept-Language": "zh-TW,zh;q=0.9"})
            with urllib.request.urlopen(req, timeout=40) as r:
                h = r.read().decode("utf-8", "replace")
            seen = set()
            for m in re.finditer(r'<a href="(https://careercenter\.ncu\.edu\.tw/internship/show/\d+)">(.*?)</a>', h, re.S):
                u, inner = m.group(1), m.group(2)
                if u in seen:
                    continue
                seen.add(u)
                t = re.search(r'card-title[^>]*>(.*?)</h5>', inner, re.S)
                d = re.search(r'(\d{4}-\d{2}-\d{2})', inner)
                title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", t.group(1) if t else "")).strip()
                if not title:
                    continue
                try:
                    age = max(0, int((NOW - dt.datetime.strptime(d.group(1), "%Y-%m-%d").timestamp()) / 86400)) if d else 7
                except Exception:
                    age = 7
                out.append({"company": "(" + name + " 職涯中心公告)", "title": htmllib.unescape(title), "loc": "Taiwan",
                            "country": "Taiwan", "url": u, "age": age, "src": "school-" + name.lower(), "school": True})
            print(f"  school {name}: {len(seen)} announcements", file=sys.stderr)
        except Exception as e:
            print(f"  ! school {name} failed: {e}", file=sys.stderr)
    return out


# ---------------------------------------------------------------- main
def main():
    rows = []
    for fn in (load_workday, load_dell, load_yourator, load_appier, load_sg_trackers, load_linkedin, load_104, load_school_boards):
        rows += fn()
    kept, seen = [], set()
    for r in rows:
        r["company"] = str(r.get("company") or "")
        r["title"] = str(r.get("title") or "")
        r["loc"] = str(r.get("loc") or "")
        if not r.get("url") or not r["title"]:
            continue
        if not title_ok(r["title"], r["title"] if r.get("school") else r["company"], trusted=r.get("trusted", False)) or not term_ok(r["title"]):
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
