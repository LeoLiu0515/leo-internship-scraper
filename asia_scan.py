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
BAD_TERM = re.compile(r"\b(spring|fall|winter|autumn)\b|co-?op\b|off-?cycle|semester|year-?long|academic year|"
                      r"\b(6|12|9)[- ]?months?\b|\b(one|1|2)[- ]?(year|yr)s?\b|\b(jan|feb|mar|aug|sep|oct|nov|dec)[a-z]*\.?\s*(to|-|–|~)\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)|"
                      r"學期|學年|長期|长期|學制|寒假|春季|秋季|冬季|全職|全职|兼職|兼职|半年|一年|雙週|一年期|非暑期|非短期|大四|碩[一二]|碩士|研究所|應屆畢業|"
                      r"シーズン|通年|長期インターン|\b[12]h\b|\bh[12]\b|\bq[1-4]\b", re.I)


def wb(needle, hay):
    return re.search(r"\b" + re.escape(needle) + r"\b", hay) is not None


TECH_RE = re.compile(
    r"engineer|engineering|developer|software|firmware|hardware|embedded|\bdata\b|\bai\b|\bml\b|machine learning|algorithm|"
    r"research|r&d|\bic\b|chip|silicon|semiconductor|electr|circuit|fpga|asic|\bsoc\b|rtl|verif|valid|\btest|system|network|"
    r"cloud|security|cyber|\biot\b|automation|robot|device|sensor|power|photon|optic|\brf\b|wireless|analog|digital|process|"
    r"equipment|yield|reliab|packag|\bfab\b|manufactur|\bnpi\b|devops|backend|frontend|full.?stack|mobile|\bios\b|android|\bsre\b|"
    r"infrastructure|compiler|\bgpu\b|cuda|simulation|control|mechatron|programmer|programming|computer|\bit\b|information|"
    r"scientist|technolog|人工智能|人工智慧|智慧|設計|驗證|訊號|量測|電源|電路|實驗|ai[ -]?|"
    r"工程|研發|研究|軟體|韌體|硬體|演算法|數據|資料|資訊|電機|電子|半導體|晶片|測試|製程|設備|系統|網路|雲端|資安|人工智慧|機器學習|自動化|機器人|光電|封裝|良率|嵌入式|程式|"
    r"エンジニア|開発|研究|ソフト|ハード|組込|半導体|回路|データ", re.I)
NON_ECE = ["mechanical engineer", "civil", "chemical engineer", "biomedical", "industrial engineer", "機構", "土木", "化工",
           "investment", "banking", "analyst, finance", "financial analyst", "business analyst", "business development",
           "customer success", "customer service", "public relations", "social media", "graphic", "copywrit", "legal",
           "paralegal", "esg", "sustainability", "quant", "trading", "risk", "clinical", "pharma", "mba", "accelerator program",
           "tax", "account operations", "campaign", "ehs", "business process", "business excellence", "業務", "顧問", "稅務",
           "consultant", "strategy", "operations intern", "talent acquisition", "advisory", "sap ", "tax ", "audit",
           "統計", "biostat", "量化", "金融", "證券", "证券", "銀行", "银行", "保險", "保险", "行銷", "营销", "營銷", "企劃", "企划", "文案", "設計師", "设计师",
           "美術", "美术", "視覺設計", "ui/ux", "會計", "会计", "財務", "财务", "法務", "法务", "法規", "生物統計", "生醫", "採購", "采购", "招募", "人力", "倉儲", "仓储", "物流",
           "客服", "護理", "护理", "醫", "医", "藥", "药", "教育", "翻譯", "翻译", "編輯", "编辑", "營運", "运营", "管培", "儲備幹部", "值班", "助理人員", "資訊助理", "印刷", "餐", "廚", "房務", "門市",
           "人壽", "壽險", "機械設計", "機械實習", "車輛", "維修實習", "material management", "全週", "全周", "非短期", "學務", "business analytics", "customer journey", "樣品工讀", "supplier", "category manager", "buyer", "修護", "修車", "噴漆", "學徒", "技師", "cnc", "沖床", "射出", "堆高機", "鉗工", "焊", "冷凍", "空調", "水電", "洗濯", "水洗", "紡織", "警衛", "職業安全衛生", "工安", "總務", "生管", "灌充", "造粒", "配方", "領班", "內場", "外場", "實習幹部", "派駐科技大廠", "淨水", "廠務助理", "補習班", "不動產", "繪圖", "排版", "內容經營", "內容編輯", "doc review", "戰略客戶", "mechanical", "資產管理", "资产管理", "股票", "基金", "投資", "投资", "公關", "公关", "社群", "短影音", "直播", "電商", "电商", "商業分析", "數據分析師", "account manager", "web3", "crypto", "hr ", "人資", "行政"]


TECH_COMPANIES = re.compile(
    r"tsmc|台積|mediatek|聯發科|realtek|瑞昱|novatek|聯詠|nvidia|qualcomm|intel\b|micron|\bamd\b|\barm\b|marvell|broadcom|"
    r"texas instruments|nxp|infineon|asml|applied materials|lam research|\bkla\b|synopsys|cadence|delta|台達|foxconn|鴻海|"
    r"quanta|廣達|asus|華碩|acer|宏碁|compal|仁寶|wistron|緯創|pegatron|和碩|inventec|英業達|\base\b|日月光|phison|群聯|"
    r"macronix|旺宏|winbond|華邦|nuvoton|新唐|alchip|世芯|\bguc\b|創意電子|andes|晶心|hynix|samsung|apple|google|microsoft|"
    r"amazon|\bmeta\b|cisco|\bdell\b|\bhp\b|lenovo|synology|群暉|moxa|gogoro|appier|garmin|sony|panasonic|toshiba|renesas|"
    r"rohm|hitachi|\bnec\b|fujitsu|tokyo electron|kioxia|canon|globalfoundries|umc|聯電|vanguard|世界先進|powerchip|力積電|"
    r"analog devices|microchip|keysight|teradyne|cadence|ansys|siemens|bosch|schneider|abb\b|honeywell|ericsson|nokia", re.I)


COMPANY_EXCLUDE = re.compile(
    r"屈臣氏|巨匠電腦|銀行|商銀|人壽|壽險|證券|金控|金融|保險|投信|博報堂|廣告|行銷|公關|企管|管理顧問|人事顧問|人力|飯店|大飯店|餐飲|百貨|補習班|"
    r"汽車|揚昇|龍一|國瑞|營造|不動產|聯華林德|林德|氣體|食品|生技|藥|醫院|學校|高級中學|國小|國中|幼兒|"
    r"bank|insurance|securities|advertis|marketing agency|restaurant|hotel", re.I)


def title_ok(title, company="", trusted=False):
    """Leo (2026-10-05): 'I do not pick, anything ECE-related is fine' -> broad: any intern/co-op title that looks
    technical, minus obvious business/finance/HR/mechanical roles."""
    t = title.lower()
    if not INTERN_STRICT.search(title):
        return False
    if any(b in t for b in TITLE_EXCLUDE) or any(b in t for b in NON_ECE) or re.search(r"\bCS\b", title):
        return False
    if re.search(r"\bmaster'?s\b", t) and not re.search(r"bachelor", t):
        return False
    if COMPANY_EXCLUDE.search(company or ""):
        return False
    return trusted or TECH_RE.search(title) is not None or TECH_COMPANIES.search(company or "") is not None


SUMMER_OK = re.compile(r"暑期|暑假|summer|寒暑假", re.I)
SEMESTER_ONLY = re.compile(r"學期|學年|semester|academic year", re.I)


def term_ok(title):
    t = title.lower()
    m = BAD_TERM.search(t)
    if m:
        # "暑期 & 學期實習" accepts summer interns -> a semester word alone must not kill it
        if not (SUMMER_OK.search(t) and SEMESTER_ONLY.search(m.group(0))):
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


def _wd_fill_desc(rows, tenant, wd, site):
    """Fetch each Workday posting's description (schedule wording lives there, e.g. Micron 'Jan to May 2027')."""
    import concurrent.futures as cf
    base = f"https://{tenant}.wd{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}"

    def one(r):
        try:
            path = r["url"].split(f"/{site}", 1)[1]
            d = http_json(base + path, timeout=30)
            html_ = (d.get("jobPostingInfo") or {}).get("jobDescription", "") or ""
            r["desc"] = htmllib.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html_)))[:6000]
        except Exception:
            pass  # fail open: keep the row if the description cannot be read
    with cf.ThreadPoolExecutor(6) as ex:
        list(ex.map(one, rows))


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
            _wd_fill_desc(out[n_before:], tenant, wd, site)
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
                    desc = " ".join(str(r.get(k) or "") for k in ("descWithoutHighlight", "description", "descSnippet"))[:1500]
                    out.append({"company": r.get("custName") or "", "title": r.get("jobName") or "",
                                "loc": ((r.get("jobAddrNoDesc") or "") + " " + (r.get("jobAddress") or ""))[:60].strip(),
                                "country": "Taiwan", "url": link, "age": age, "src": "104",
                                "trusted": cat in ("2007000000", "2008000000"), "desc": desc})
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


# ---------------------------------------------------------------- Leo (2026-10-07): schedule + dedupe hardening
# He can only intern in the summer break (US spring quarter ends 6/12, autumn starts 9/29). Anything that is a semester /
# academic-year / long-term / 4-days-a-week programme conflicts with school, even if the title does not say so.
DESC_BAD = re.compile(
    r"學期制|學年制|學期實習|學年實習|一年制|一年期|一學期|全學年|下學期|上學期|大四.{0,4}學年|長期(實習|工讀|簽約|合作|實習生)|"
    r"實習時間\s*[:：]?\s*(一年|半年|6\s*個月|六個月)|6\s*個月|六個月|半年|非短期|配合學校簽約|"
    r"\b(6|six|9|nine|12|twelve)[- ]months?\b|year[- ]?long|one[- ]year|1[- ]year|academic year|semester|"
    r"6\s*(months?)?\s*(to|-|–)\s*(1|one)\s*year|"
    r"\b(5|five)[- ]?months?\b|\b(2\d|3\d)[- ]?weeks?\b|\bjan(uary)?\b[^.]{0,25}\b(to|-|–|and|until|till)\b[^.]{0,12}\b(may|jun(e)?)\b|credit-bearing", re.I)


def desc_ok(title, desc):
    blob = (title or "") + " " + (desc or "")
    m = DESC_BAD.search(blob)
    if not m:
        return True
    if SUMMER_OK.search(blob) and SEMESTER_ONLY.search(m.group(0)):
        return True  # "summer or semester" programme
    return False


def li_description(url):
    m = re.search(r"(\d{8,})/?$", url.split("?")[0])
    if not m:
        return ""
    try:
        req = urllib.request.Request("https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/" + m.group(1),
                                     headers={"User-Agent": UA["User-Agent"], "Accept-Language": "en-US,en;q=0.9"})
        with urllib.request.urlopen(req, timeout=30) as r:
            h = r.read().decode("utf-8", "replace")
        mm = re.search(r'show-more-less-html__markup[^>]*>(.*?)</div>', h, re.S)
        return htmllib.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", mm.group(1) if mm else "")))[:3000]
    except Exception:
        return ""  # fail open: keep the job if the detail page can't be read


_CO_SUFFIX = re.compile(r"股份有限公司|有限公司|股份|台灣分公司|分公司|公司|co\.?,? ?ltd\.?|inc\.?|corporation|corp\.?|limited", re.I)


def norm_company(c):
    c = _CO_SUFFIX.sub(" ", (c or "").lower())
    latin = re.findall(r"[a-z0-9]+", c)
    if latin:
        return latin[0]
    return re.sub(r"[^\w]", "", c)


def norm_title(t):
    t = (t or "").lower()
    t = re.sub(r"[(（][^()（）]*(月薪制|時薪制|初階|中階|高階)[^()（）]*[)）]", "", t)
    t = re.sub(r"[-－ ]*(初階|中階|高階)\s*$", "", t)
    m = re.search(r"[(（]([^()（）]*[a-z]{3,}[^()（）]*)[)）]", t)
    if m and len(re.findall(r"[a-z0-9]+", m.group(1))) >= 2:
        t = m.group(1)
    toks = re.findall(r"[a-z0-9]+", t)
    if len(toks) >= 3:
        return " ".join(toks)
    return re.sub(r"[^\w]", "", t)


# ---------------------------------------------------------------- WATCH LIST (Leo, 2026-10-07)
# Companies that deserve special attention: his uncle worked at Eaton / Applied Materials / ASML, plus every company where
# someone can refer or recommend him (TSMC, Delta, Cisco via Toby, Amazon, Tesla). Rows from these companies are NEVER
# dropped for schedule/duration reasons -- they are kept, starred, and labelled "long-term?" so Leo decides himself.
WATCH_RE = re.compile(r"eaton|伊頓|applied materials|應用材料|應材|asml|艾司摩爾|愛斯莫爾|delta electronics|台達|tsmc|台積|cisco|amazon|tesla|mediatek|聯發科|airoha|達發|jentech|健策|\bhp\b|hewlett|google", re.I)
WATCH_NAMES = ["Eaton", "ASML", "Applied Materials", "Delta Electronics", "TSMC", "Cisco", "Amazon", "Tesla", "MediaTek", "Airoha", "Jentech", "HP", "Google"]
WATCH_LOCS = ["Taiwan", "Japan", "Singapore", "China", "South Korea", "Hong Kong SAR"]
LOCAL_LANG_RE = re.compile(r"[\u3040-\u30ff\uac00-\ud7a3]|卒|\((korean|japanese)\)|native-level|\bfluent in (japanese|korean)", re.I)  # kana / hangul / 28卒 etc.
TARGET_COUNTRIES = {"Taiwan", "Japan", "Singapore", "Hong Kong", "South Korea", "China"}  # Leo 2026-10-07: advanced Asian economies only


def _flat(x):
    return " ".join(re.sub(r"<[^>]+>", " ", x or "").split())


def load_watch_linkedin():
    out, seen = [], set()
    for name in WATCH_NAMES:
        rx = re.compile((r"\b" + re.escape(name.split()[0]) + r"\b") if name == "HP" else re.escape(name.split()[0]), re.I)
        for loc in WATCH_LOCS:
            for start in (0, 10):
                try:
                    h = _li_fetch({"keywords": name + " intern", "location": loc, "start": start, "f_TPR": "r7776000"})
                except Exception:
                    break
                cards = re.findall(r"<li>(.*?)</li>", h, re.S)
                if not cards:
                    break
                for c in cards:
                    m_url = re.search(r'href="(https://[a-z.]*linkedin[.]com/jobs/view/[^"?]+)', c)
                    m_t = re.search(r'base-search-card__title[^>]*>(.*?)</h3>', c, re.S)
                    m_c = re.search(r'base-search-card__subtitle[^>]*>(.*?)</h4>', c, re.S)
                    m_l = re.search(r'job-search-card__location[^>]*>(.*?)</span>', c, re.S)
                    m_d = re.search(r'datetime="([0-9-]{10})"', c)
                    if not (m_url and m_t and m_c):
                        continue
                    comp = htmllib.unescape(_flat(m_c.group(1)))
                    if not rx.search(comp) or m_url.group(1) in seen:
                        continue
                    seen.add(m_url.group(1))
                    where = htmllib.unescape(_flat(m_l.group(1) if m_l else ""))
                    try:
                        age = max(0, int((NOW - dt.datetime.strptime(m_d.group(1), "%Y-%m-%d").timestamp()) / 86400)) if m_d else 7
                    except Exception:
                        age = 7
                    out.append({"company": comp, "title": htmllib.unescape(_flat(m_t.group(1))), "loc": where[:60],
                                "country": country_of(where) or loc, "url": m_url.group(1), "age": age, "src": "linkedin-watch"})
                time.sleep(0.8)
    print(f"  watch-list linkedin rows: {len(out)}", file=sys.stderr)
    return out


def load_eaton():
    """Eaton's own career site (Eightfold 'pcsx' API, works without login). Keeps only Asian locations."""
    out = []
    try:
        for start in range(0, 300, 10):
            url = "https://eaton.eightfold.ai/api/pcsx/search?domain=eaton.com&query=intern&num=10&start=" + str(start)
            d = http_json(url, headers={"Referer": "https://eaton.eightfold.ai/careers", "Accept": "application/json"})
            ps = (d.get("data") or {}).get("positions", [])
            if not ps:
                break
            for p in ps:
                loc = p.get("location") or ", ".join(p.get("locations") or [])
                ctry = country_of(loc)
                if ctry:
                    out.append({"company": "Eaton", "title": p.get("name") or "", "loc": str(loc)[:60], "country": ctry,
                                "url": p.get("canonicalPositionUrl") or ("https://eaton.eightfold.ai/careers/job/" + str(p.get("id"))),
                                "age": 7, "src": "eaton"})
    except Exception as e:
        print(f"  ! eaton failed: {e}", file=sys.stderr)
    print(f"  eaton asia rows: {len(out)}", file=sys.stderr)
    return out


def load_google():
    """Google careers (public results page embeds job data as JSON in AF_initDataCallback ds:1)."""
    cc = {"TW": "Taiwan", "JP": "Japan", "SG": "Singapore", "HK": "Hong Kong", "KR": "South Korea", "CN": "China"}
    out, seen = [], set()
    for loc in ("Taiwan", "Japan", "Singapore", "Hong Kong", "South Korea", "China"):
        for q in ("intern", "internship", "student", "apprentice", "early career"):
            for page in (1, 2, 3):
                try:
                    u = "https://www.google.com/about/careers/applications/jobs/results/?" + urllib.parse.urlencode({"q": q, "location": loc, "page": page})
                    h = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=30).read().decode("utf-8", "replace")
                    m = re.search(r"AF_initDataCallback[(]{key: 'ds:1'.*?data:(.*?), sideChannel", h, re.S)
                    d = json.loads(m.group(1))
                except Exception as e:
                    print(f"  ! google {loc}/{q}/{page} failed: {e}", file=sys.stderr)
                    break
                jobs = d[0] or []
                for j in jobs:
                    jid = str(j[0])
                    if jid in seen:
                        continue
                    seen.add(jid)
                    locs = j[9] if len(j) > 9 and isinstance(j[9], list) else []
                    ctry = next((cc[l[5]] for l in locs if len(l) > 5 and l[5] in cc), None)
                    if not ctry:
                        continue
                    title = str(j[1])
                    if not re.search(r"intern|student|apprentic|co-op", title, re.I):
                        continue
                    out.append({"company": "Google", "title": title, "loc": str(locs[0][0])[:60], "country": ctry,
                                "url": "https://www.google.com/about/careers/applications/jobs/results/" + jid, "age": 3, "src": "google"})
                if len(jobs) < 20:
                    break
    print(f"  google asia rows: {len(out)}", file=sys.stderr)
    return out


# ---------------------------------------------------------------- main
# Leo (2026-10-08): drop roles too far from his ECE/CS major and experience (embedded, CAN/firmware, PyTorch, C/C++, Python).
# Not applied to watch-list companies (those are always kept).
ECE_CORE = re.compile(
    r"embedded|firmware|hardware|software|fpga|asic|rtl|verilog|vlsi|\bic\b|ic設計|ic设计|chip|silicon|\bsoc\b|circuit|analog|digital|"
    r"signal|\brf\b|wireless|antenna|power (electronic|supply|management)|電源|电源|pcb|layout|dft|verification|validation|"
    r"driver|kernel|linux|rtos|iot|sensor|robot|control|automation|mechatron|\bai\b|\bml\b|machine learning|deep learning|"
    r"computer vision|vision|algorithm|data (engineer|scien)|backend|front-?end|full.?stack|developer|programmer|devops|\bsre\b|cloud|"
    r"network|security|cyber|compiler|gpu|cuda|hpc|architecture|systems? (engineer|software|design|validation)|test (engineer|automation)|"
    r"\bqa\b|sdet|semiconductor|photonic|optical|memory|dram|nand|\beda\b|"
    r"軟體|软件|軟韌體|韌體|固件|硬體|硬件|電路|电路|電子|电子|晶片|芯片|半導體|半导体|驗證|验证|類比|模擬|射頻|機器人|控制|訊號|信号|光電|電機|电机|"
    r"嵌入式|演算法|算法|人工智慧|人工智能|機器學習|深度學習|資訊|資安|網路|网络|雲端|後端|后端|前端|全端|程式|开发|開發|資料|数据|數據|"
    r"r&d|研發|研发|研究|電氣|电气|electrical|electronic|device|cmos|bios|research|scientist|information technology|"
    r"design and technology|product engineering|applications? engineer|field application|system|physical design|"
    r"llm|模塊|模組|模块|integration|reliability|\bfa\b|failure|test|測試|测试|simulation|機電整合|賦能|empower|develop|\besd\b|\bnpu\b|"
    r"processing|technical|\bio\b|車用|應用工程|应用工程|technology|automotive", re.I)
OPS_ROLE = re.compile(
    r"助理技師|助理工程師|助理人員|助理設備|技術員|技師|學徒|廠務|品保|品管|\bqc\b|\bie\b|\bim\b|工安|環安|safety|\behs\b|氣體|灌充|維修|維護|"
    r"保養|儲備|預聘|機電|冷凍|空調|施工|營造|土木|製程助理|設備助理|設備實習|生產|產線|倉|物料|採購|"
    r"maintenance|facility|facilities|technician|operator|production|manufacturing (engineer|support)|supplier|category", re.I)
NON_TECH_CO = re.compile(r"銀行|银行|金控|金融|保險|保险|人壽|證券|证券|媒體|media|tvbs|醫療器材|不動產|餐飲|百貨|飯店", re.I)
OPS_EXCEPT = re.compile(r"firmware|software|硬體研發|研發|驗證|verification|開發|develop|vision|視覺|\bic\b|device", re.I)


def ece_relevant(title, company=""):
    t = title or ""
    if NON_TECH_CO.search(company or "") and not re.search(r"firmware|embedded|hardware|ic設計|硬體", t, re.I):
        return False
    if re.search(r"data analyst|數據分析|数据分析|資料分析", t, re.I):
        return False
    if OPS_ROLE.search(t) and not OPS_EXCEPT.search(t):
        return False
    if ECE_CORE.search(t):
        return True
    return TECH_COMPANIES.search(company or "") is not None and not OPS_ROLE.search(t)



# Leo (2026-10-08): pure-software roles are out; keep anything touching hardware / firmware / chips / systems close to hardware.
PURE_SW = re.compile(
    r"software|軟體|软件|backend|back-?end|後端|后端|front-?end|前端|full.?stack|全端|web|網站|网站|\bapp\b|android|ios\b|java\b|python|typescript|"
    r"javascript|\.net|php|golang|developer|programmer|程式|程序|devops|\bsre\b|cloud|雲端|\bmis\b|資訊|資料|data|數據|(?<![a-z])ai(?![a-z])|\bml\b|llm|系統開發|系统开发|"
    r"machine learning|deep learning|人工智慧|人工智能|機器學習|深度學習|演算法|算法|algorithm|\bit\b|information technology|資安|security|cyber|"
    r"\bqa\b|sdet|軟體測試|unity|game|遊戲|網路工程|rag\b|prompt|nlp|scientist|analytics|analyst", re.I)
HW_KEEP = re.compile(
    r"firmware|韌體|軟韌體|固件|embedded|嵌入式|hardware|硬體|硬件|\bic\b|ic設計|ic设计|asic|fpga|rtl|verilog|vlsi|soc\b|chip|晶片|芯片|silicon|"
    r"circuit|電路|电路|analog|類比|digital design|signal|訊號|信号|\brf\b|射頻|wireless|antenna|power (electronic|supply|management|ic)|電源|电源|pcb|layout|dft|verification|驗證|验证|"
    r"bios|bmc|driver|kernel|rtos|iot|sensor|感測|robot|機器人|機電|mechatron|control|控制|automation|自動化控制|plc|gpu|cuda|\bnpu\b|hpc|"
    r"compiler|architecture|semiconductor|半導體|半导体|photonic|optical|光|memory|dram|nand|\beda\b|device|cmos|electrical|電機|電氣|electronic|電子|"
    r"edge|車用|automotive|5g|6g|網路晶片|switch|network(ing)? (hardware|device)|system software.*(gpu|soc)|(gpu|soc).*system software", re.I)


def not_pure_software(title):
    t = title or ""
    return (not PURE_SW.search(t)) or HW_KEEP.search(t) is not None



# Learned from Leo's dismissals (2026-10-08, 626 rows): fab/manufacturing/field-service roles, internet/finance employers,
# civil-electrical infrastructure, generic "research" roles and non-technical program/admin interns are dropped. Applies to
# watch-list rows too (he dismissed Applied Materials 23/24, TSMC EE/MFG/FAC/CPO).
LEARN_TITLE = re.compile(
    r"logistics|物流|corporate|customer engineer|field (support|quality|service)|after.?sales|售後|service engineer|service intern|technical trainer|trainer|"
    r"global support|"
    r"equity research|research (intern|sciences|analyst)|lab (testing|research)|marketing|brand|business (planning|operations|analy)|operations intern|"
    r"upskill|program support|digital transformation|content|design operations|"
    r"人才|經營支援|產業學院|專利|智權|招募|行政|"
    r"wiring|signalling|signaling|\bhv\b|transport|railway|solar|floating pv|marine|shipyard|"
    r"\bie\b|\bpe助理|品保|\bqa\b助理|助理工程師|技術員|技師|機械所|測試人員|寬頻|電信工程|電子商務|說明會|宣講|暑期\s*[&＆和]\s*學期|jan\s*(till|until)\s*jun", re.I)
LEARN_CO = re.compile(
    r"tiktok|bytedance|字節|巨量移動|tencent|騰訊|腾讯|bybit|autodesk|shopback|shopee|stripe|manulife|mufg|tiger brokers|societe generale|fidelity|"
    r"targetjobs|optiver|frost & sullivan|celine|adidas|mondel|christian dior|parfums|brand|razer|邑方|arup|sembcorp|hanwha|china railway|"
    r"centre for strategic|marina bay|marinabay|ministry of|certis|hitachi energy|syensqo|qima|msd\b|mcc industrial|hp\b|hewlett|hpe\b", re.I)


GENERIC_TITLE = re.compile(r"^[\s\W]*(college |university |engineering |graduate |technical )?(intern(ship)?|實習生?|研發|工讀)?[\s\W]*$", re.I)


def learned_drop(title, company=""):
    t = title or ""
    if LEARN_CO.search(company or ""):
        return True
    core = re.sub(r"[【\[].*?[】\]]", " ", t)
    core = re.sub(r"(?i)intern(ship)?|實習生?|研發|單位|總公司|汐止|中港廠|工讀生?|college|university|engineering|graduate|[-_/~\s]", "", core)
    if len(core) <= 2 and not re.search(r"mediatek|聯發科|tsmc|台積|nvidia|輝達", company or "", re.I):
        return True
    if re.search(r"tsmc|台積", company or "", re.I) and not re.search(r"equipment|manufacturing|facility|corporate", t, re.I):
        return False
    if re.search(r"asml", company or "", re.I) and not re.search(r"global support", t, re.I):
        return False
    if LEARN_TITLE.search(t) and not re.search(r"design|verification|validation|firmware|embedded|asic|\bic\b|rtl|fpga|analog|circuit|silicon|soc\b", t, re.I):
        return True
    return False



# Leo (2026-10-08): skip companies too small to be worth the application. Not "tier 1 only" -- the floor is roughly:
# listed in Taiwan (TWSE/TPEx), a known multinational / subsidiary of one, or a research institute / university.
BIG_CO = re.compile(
    r"钧正|哈啰|hello inc|迪芬尼|tymphany|primax|致伸|bear robotics|innosilicon|芯动|"
    r"apple|amazon|google|microsoft|\bmeta\b|airbus|bmw|bosch|siemens|\babb\b|rockwell|ericsson|nokia|rohde|signify|borgwarner|commscope|coherent|"
    r"keysight|seagate|western digital|skyworks|monolithic|stmicro|onsemi|renesas|omnivision|infineon|nxp|cadence|synopsys|mediatek|aumovio|"
    r"mann\+hummel|capgemini|凯捷|focaltech|敦泰|杭州迪普|迪普科技|宇视|宇視|贝岭|貝嶺|leybold|莱宝|atlas copco|syntegon|healthineers|hitachi|"
    r"teradyne|泰瑞達|panasonic|松下|mitac|神達|ingrasys|鴻佰|hyve|海峰|foxconn|鴻海|富士康|coretronic|中光電|delta|台達|honeywell|schneider|"
    r"intel|\bamd\b|\barm\b|qualcomm|broadcom|nvidia|輝達|micron|美光|marvell|邁威爾|tsmc|台積|asml|applied materials|lam research|\bkla\b|tokyo electron|"
    r"samsung|sk hynix|toshiba|kioxia|\bsony\b|\bcanon\b|\bnikon\b|fujitsu|\bnec\b|\brohm\b|murata|\btdk\b|denso|toyota|honda|nissan|huawei|华为|華為|xiaomi|小米|"
    r"lenovo|聯想|联想|\bdell\b|\bhp\b|hewlett|\bibm\b|oracle|cisco|juniper|intel|texas instruments|analog devices|microchip|wolfspeed|globalfoundries|"
    r"united microelectronics|聯電|聯華電子|vanguard|世界先進|powerchip|力積|\base\b|日月光|矽品|siliconware|amkor|phison|群聯|realtek|瑞昱|novatek|聯詠|"
    r"gogoro|appier|synology|群暉|moxa|qnap|威聯通|asustek|華碩|acer|宏碁|\bmsi\b|微星|gigabyte|技嘉|quanta|廣達|wistron|緯創|compal|仁寶|pegatron|和碩|inventec|英業達", re.I)
INSTITUTE = re.compile(r"工研院|資策會|國家實驗室|國研院|財團法人|研究院|中科院|大學|university|institute|nsysu|ntu\b", re.I)
_LISTED = None


def _norm_co(c):
    c = re.sub(r"[_\s]|股份有限公司|有限公司|股份|台灣分公司|臺灣分公司|分公司|\(.*?\)|（.*?）|co\.?,? ?ltd\.?|inc\.?|corp(oration)?\.?|limited", "", (c or "").lower())
    return c.replace("臺", "台")


def _load_listed():
    """TWSE + TPEx listed company names (open data). Fail open: if unreachable, size filter keeps everything."""
    global _LISTED
    if _LISTED is not None:
        return _LISTED
    names = set()
    try:
        for r in http_json("https://openapi.twse.com.tw/v1/opendata/t187ap03_L"):
            names |= {_norm_co(r.get("公司名稱")), _norm_co(r.get("公司簡稱")), _norm_co(r.get("英文簡稱"))}
        for r in http_json("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"):
            names |= {_norm_co(r.get("CompanyName")), _norm_co(r.get("CompanyAbbreviation"))}
    except Exception as e:
        print("  ! listed-company list unavailable, size filter disabled:", e, file=sys.stderr)
        _LISTED = False
        return _LISTED
    _LISTED = {n for n in names if len(n) >= 2}
    return _LISTED


def size_ok(company, country, watch=False):
    if watch:
        return True
    co = company or ""
    if BIG_CO.search(co) or INSTITUTE.search(co):
        return True
    if country == "Taiwan":
        listed = _load_listed()
        if listed is False:
            return True
        return any(_norm_co(p) in listed for p in [co] + re.split(r"[_/|｜]", co))
    return False  # outside Taiwan an unknown employer is treated as small


def main():
    rows = []
    for fn in (load_workday, load_dell, load_yourator, load_appier, load_sg_trackers, load_linkedin, load_104, load_school_boards, load_watch_linkedin, load_eaton, load_google):
        rows += fn()
    kept, seen = [], set()
    for r in rows:
        r["company"] = str(r.get("company") or "")
        r["title"] = str(r.get("title") or "")
        r["loc"] = str(r.get("loc") or "")
        if not r.get("url") or not r["title"]:
            continue
        if r.get("country") not in TARGET_COUNTRIES:
            continue
        if r.get("country") in ("Japan", "South Korea") and LOCAL_LANG_RE.search(r["title"]):
            continue  # Leo speaks no Japanese/Korean: local-language postings are wasted applications
        if learned_drop(r["title"], r["company"]):
            continue
        if not size_ok(r["company"], r.get("country"), bool(WATCH_RE.search(r["company"]))):
            continue
        w = bool(WATCH_RE.search(r["company"]))
        if w:
            tl = r["title"].lower()
            if (not INTERN_STRICT.search(r["title"])) or any(b in tl for b in TITLE_EXCLUDE) or any(b in tl for b in NON_ECE):
                continue
            r["watch"] = True
            # Leo 2026-10-07 (final rule): delete ONLY when the text itself says long-term/semester/year-contract (title or
            # description actually read). Never infer it -- ASML's form even lets him pick "<3 months", so absence of a
            # duration (or an unread description) means KEEP.
            if (not term_ok(r["title"])) or bool(r.get("desc") and not desc_ok(r["title"], r["desc"])):
                continue
        else:
            if not title_ok(r["title"], r["title"] if r.get("school") else r["company"], trusted=r.get("trusted", False)) or not term_ok(r["title"]) or not ece_relevant(r["title"], r["company"]) or not not_pure_software(r["title"]):
                continue
            if r.get("desc") and not desc_ok(r["title"], r["desc"]):
                continue
        key = (norm_company(r["company"]), norm_title(r["title"]), r["country"])
        ukey = r["url"].split("?")[0]
        if key in seen or ukey in seen:
            continue
        seen.add(key); seen.add(ukey)
        if r["age"] > 60 and r["age"] != 999:
            continue
        kept.append(r)
    # LinkedIn cards carry no description -> read the posting for Taiwan rows and drop semester/long-term programmes
    drop = 0
    final = []
    for r in kept:
        if r.get("watch"):
            if r["src"].startswith("linkedin"):
                d = li_description(r["url"])
                time.sleep(0.8)
                if d and not desc_ok(r["title"], d):
                    drop += 1
                    continue  # the posting text itself says long-term
        elif r["src"] == "linkedin" and r["country"] == "Taiwan":
            d = li_description(r["url"])
            time.sleep(0.8)
            if d and not desc_ok(r["title"], d):
                drop += 1
                continue
        r.pop("desc", None)
        final.append(r)
    print("linkedin-taiwan dropped by description:", drop)
    kept = final
    for r in kept:
        if r.get("watch"):
            r["loc"] = ("★ " + r["loc"])[:60]
    kept.sort(key=lambda j: (not j.get("watch"), j["country"] != "Taiwan", j["age"]))
    print("watch-list rows kept:", sum(1 for j in kept if j.get("watch")))
    json.dump({"generated_at": NOW, "count": len(kept), "jobs": kept},
              open("asia.json", "w", encoding="utf-8"), ensure_ascii=False)
    from collections import Counter
    print("total kept:", len(kept))
    print("by country:", dict(Counter(j["country"] for j in kept)))
    print("by source:", dict(Counter(j["src"] for j in kept)))


if __name__ == "__main__":
    main()
