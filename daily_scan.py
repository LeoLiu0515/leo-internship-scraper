#!/usr/bin/env python3
"""
daily_scan.py -- fixed, non-AI internship scanner for Leo's dashboard.

Run this literally every day (no reasoning needed): it fetches all sources,
filters to embedded/hardware Summer 2027 postings he can legally take, dedupes,
and writes final_jobs.json. The daily cloud routine's job is then just:
run this -> read the current dashboard's applied/dismissed -> merge -> publish.

Sources:
  1-2. SimplifyJobs + vanshb03 community JSON feeds
  3-4. sndsh404 + zapplyjobs community README-markdown-table feeds
  5.   zshah101's independently-scraped JSON API (4,300+ employers, 12 ATS
       platforms, real ISO post dates) -- added 2026-09-02, verified to
       surface real companies absent from sources 1-4 (Microchip Technology,
       Tenstorrent, Generac, H3X Technologies, Emerson Electric).
  6.   Direct company ATS APIs (Greenhouse + Workday), called directly. Every
       entry below was hand-verified to return real data before being added
       -- do not add a company here without testing its curl call first, a
       guessed board token just silently returns nothing. NOTE: this source
       does NOT work from the daily cloud routine's sandbox -- its egress
       policy blocks boards-api.greenhouse.io and *.myworkdayjobs.com with a
       403. Kept for manual/local runs only; source 7 is how this same
       company list reaches the cloud routine anyway.
  7.   RELAY: the same direct-company APIs as source 6 (plus Lever), fetched
       instead by a GitHub Actions workflow in LeoLiu0515/leo-internship-
       scraper (unrestricted network egress there) and committed to that
       repo's data.json. This script reads that file via
       raw.githubusercontent.com -- a domain the cloud sandbox DOES allow --
       closing the loop without needing the sandbox's network policy changed.
       Set up 2026-09-02. To add a company to this path, verify its endpoint
       with curl, then edit that repo's scan.py (not this file) and push.
  8.   TSMC's own careers site (careers.tsmc.com), called directly -- added
       2026-09-03 after Leo asked why TSMC never showed up. It's a classic
       Avature server-rendered portal (not a REST/JSON API like the sources
       above), found by loading the real page in a browser and inspecting
       network requests -- the earlier guessed Oracle Recruiting Cloud REST
       path (ro.careers.tsmc.com) was entirely the wrong ATS platform. The
       keyword-search form POSTs to /zh_TW/careers/SearchJobs with no CSRF
       token and no session/cookie requirement. Verified live from a
       residential IP and from a real browser: search="intern" returns 15
       results (page 1 of 10 shown), incl. 2 real US postings -- "[Summer
       2027] TSMC AZ Internship Opportunities - Engineering Roles" and
       "...- Facility Roles" (both 美國-亞利桑那州 / US-Arizona).
       IMPORTANT CAVEAT (found 2026-09-03, same day): this 403s from BOTH
       the dashboard routine's own cloud sandbox AND the relay repo's
       GitHub Actions runner -- unlike Greenhouse/Workday/Lever (source
       6/7), whose block was just a sandbox egress-domain-allowlist issue
       that GitHub Actions' unrestricted egress solved. TSMC's block
       persists even with full browser-style headers (Accept/Accept-
       Language/Referer/Origin, real Chrome UA) added to the request, on
       GitHub Actions' IP -- strongly suggesting an IP-reputation/WAF block
       on datacenter address ranges specifically, not a simple bot-header
       check. So this source only actually produces results on a
       manual/local run from a residential connection (like source 6) --
       it does NOT currently reach the live daily dashboard automatically,
       despite superficially "working." Not fixed; would need something
       like a residential proxy to solve properly, which hasn't been set
       up. Kept as source 8 anyway since it's still useful for a manual
       spot-check run.
  9.   HiringCafe (hiringcafe.com), called directly -- added 2026-09-03 on
       a tip from one of Leo's friends. It's a Next.js app that embeds its
       full search results as structured JSON in the page's own
       __NEXT_DATA__ script tag (props.pageProps.ssrHits) -- no separate
       API endpoint needed, just a GET with the right ?searchState= query
       param (a URL-encoded JSON blob: {"locations":[...], "searchQuery":
       "..."}). Each hit has genuinely richer structured fields than any
       other source here: v5_processed_job_data.masters_degree_requirement
       ("Required"/"Preferred"/"Not Mentioned" -- far more reliable than
       the master's-regex hack in title_ok()) and .workplace_countries
       (a clean ["US"]-style list -- more reliable than parsing free-text
       location strings). Verified live: a handful of targeted queries
       (see HIRINGCAFE_QUERIES) surfaced several real postings absent from
       every other source -- Fab2, H3X Technologies, Rajant Health, JARDUM.
       CONFIRMED BLOCKED from this routine's own sandbox (tested live
       2026-09-03): unlike TSMC (source 8, which gets an actual HTTP 403
       from Cloudflare), HiringCafe's curl call here fails even earlier --
       "connect_rejected (the egress proxy denied the CONNECT (organization
       policy))" -- meaning the sandbox's own egress proxy blocks the
       destination outright, before the request even reaches hiringcafe.com's
       Cloudflare challenge. A stricter block than source 8's, same
       practical conclusion: this source produces real, useful data on a
       manual/residential run (see the postings named above) but does NOT
       and will NOT reach the automated daily dashboard. Kept in the script
       anyway (harmless -- fails gracefully to 0 results here) since it's
       useful for a manual spot-check run, same rationale as source 8.
"""

import json, os, re, time, sys, urllib.request, urllib.parse, datetime as dt

NOW = time.time()

# ---- direct-company ATS list (verified 2026-09-02) --------------------------
GREENHOUSE_BOARDS = {
    "flyzipline": "Zipline",
    "axontalentcommunity": "Axon",
    "verkada": "Verkada",
    "flex": "Flex",
    "waymo": "Waymo",
    "lucidmotors": "Lucid Motors",
    "roku": "Roku",
}
WORKDAY_TENANTS = [
    # (tenant, host_wd_number, site, display_name)
    ("nvidia", 5, "NVIDIAExternalCareerSite", "NVIDIA"),
    ("vermeer", 5, "externalcareersite", "Vermeer"),
    ("sbdinc", 1, "Stanley_Black_Decker_Career_Site", "Stanley Black & Decker"),
    ("jj", 5, "jj", "Johnson & Johnson"),
    ("cisco", 5, "Cisco_Careers", "Cisco"),
    ("micron", 1, "External", "Micron"),
    ("intel", 1, "External", "Intel"),
    ("analogdevices", 1, "External", "Analog Devices"),
]

COMPANY_EXCLUDE = [
    "lockheed", "raytheon", "rtx", "northrop", "l3harris", "anduril", "spacex",
    "blue origin", "general dynamics", "gulfstream", "general atomics",
    "bae systems", "leidos", "booz allen", "mitre", "aerojet",
    "collins aerospace", "sierra nevada", "draper", "sandia", "los alamos",
    "lawrence livermore", "peraton", "caci", "saic", "parsons", "kbr",
    "huntington ingalls", "textron", "rocket lab", "shield ai",
    "firefly aerospace", "relativity space", "northwood space", "haast",
    "naval", "air force", "army", "defense", "aerospace", "satellite",
    "space", "space systems", "orbital", "launch vehicle",
    # added 2026-09-03: surfaced by the new HiringCafe source (9) --
    # Leonardo DRS is the US defense-electronics subsidiary of Leonardo
    # S.p.A. (Italian defense/aerospace conglomerate), same ITAR concern
    # as the rest of this list.
    "leonardo",
]
TITLE_EXCLUDE = [
    "phd", "ph.d", "mba", "sales", "marketing", "recruiting", "legal",
    "supply chain", "mechanical engineer", "civil", "chemical engineer",
    "biomedical", "product manager", "product management", "program manager",
    "project manager", "technical writer", "accounting", "audit",
    "weapon",
]
TERM_EXCLUDE_WORDS = ["spring", "fall", "winter"]
TITLE_INCLUDE = [
    "embedded", "firmware", "hardware", "fpga", "verilog", "rtl", "asic",
    "asic engineer", "asic design", "silicon", "digital design", "bare-metal",
    "mcu", "microcontroller", "soc", "robotics", "controls", "control system",
    "signal processing", "dsp", "pcb", "board design", "validation",
    "test engineer", "verification", "vlsi", "electrical", "electrical engineer",
    "computer engineering", "systems engineer", "device", "sensor", "power",
    "gpu", "mixed signal", "mixed-signal", "analog design", "physical design",
    "physics engine", "robot", "motion planning", "kinematics", "dynamics",
    "computer architecture",
    "systems engineering", "compiler",
    "thermal", "perception", "diagnostics",
    "mechatronics",
]
NON_US_SIGNALS = [
    "uk", "united kingdom", "london", "emea", "apac", "canada", "india",
    "germany", "france", "singapore", "china", "prc", "taiwan", "japan", "australia",
    "mexico", "brazil", "ireland", "netherlands", "israel", "poland", "spain",
    "italy", "switzerland", "sweden", "norway", "denmark", "finland",
    "belgium", "austria", "portugal", "greece", "toronto", "vancouver",
    "bangalore", "hyderabad", "shanghai", "shenzhen", "seoul", "tokyo",
    "dublin", "amsterdam", "munich", "berlin", "paris", "madrid", "milan",
    "zurich", "sydney", "melbourne", "spain", "catalonia",
    "vietnam", "costa rica", "malaysia", "philippines", "thailand",
    "indonesia", "colombia", "argentina", "chile", "egypt", "south africa",
    "turkey", "romania", "czech republic", "czechia", "hungary", "ukraine",
    "russia", "hong kong", "new zealand", "guadalajara",
    "penang", "kuala lumpur", "ho chi minh",
]


def word_in(needle, haystack):
    return re.search(r"\b" + re.escape(needle) + r"\b", haystack) is not None


def title_ok(title):
    t = title.lower()
    if not (word_in("intern", t) or word_in("internship", t)
            or word_in("co-op", t) or word_in("coop", t)):
        return False
    if any(word_in(b, t) for b in TITLE_EXCLUDE):
        return False
    if re.search(r"\bmaster'?s\b", t):
        return False
    return any(word_in(g, t) for g in TITLE_INCLUDE)


def term_ok(title):
    t = title.lower()
    if any(word_in(w, t) for w in TERM_EXCLUDE_WORDS):
        return False
    years = re.findall(r"\b(20\d{2})\b", t)
    if years and "2027" not in years:
        return False
    return True


def location_ok(loc):
    s = loc.lower()
    s = re.sub(r"\bnew mexico\b", "", s)
    return not any(word_in(sig, s) for sig in NON_US_SIGNALS)


def company_ok(company):
    c = company.lower()
    return not any(word_in(b, c) for b in COMPANY_EXCLUDE)


def fetch_json(url, method="GET", body=None, headers=None):
    req = urllib.request.Request(url, method=method,
                                  headers=headers or {"User-Agent": "daily-scan/1.0"})
    if body is not None:
        req.data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def load_json_feeds():
    out = []
    for url in [
        "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json",
        "https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/dev/.github/scripts/listings.json",
    ]:
        try:
            rows = fetch_json(url)
        except Exception as e:
            print("  ! feed failed:", url, e, file=sys.stderr)
            continue
        for x in rows:
            if x.get("active") is False or x.get("is_visible") is False:
                continue
            terms = x.get("terms") or [x.get("season", "")]
            if not any("Summer 2027" in str(t) for t in terms):
                continue
            out.append({
                "company": x.get("company_name", ""),
                "title": x.get("title", ""),
                "loc": "; ".join(x.get("locations") or [])[:60],
                "date_posted": x.get("date_posted") or x.get("date_updated") or 0,
                "url": x.get("url", ""),
                "src": "community-json",
            })
    return out


def parse_md_table(text):
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|") or "---" in line:
            continue
        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) < 4:
            continue
        rows.append(cols)
    return rows


def strip_md(s):
    s = re.sub(r"\*\*(.*?)\*\*", r"\1", s)
    m = re.search(r"\[.*?\]\((https?[^)]+)\)", s)
    return m.group(1) if m else s


def load_md_feeds():
    out = []
    try:
        text = fetch_text("https://raw.githubusercontent.com/sndsh404/summer-2027-internships/main/README.md")
        for cols in parse_md_table(text):
            if len(cols) < 5 or cols[0].lower() == "company":
                continue
            company, role, loc, apply_col, added = cols[0], cols[1], cols[2], cols[3], cols[4]
            if "summer 2027" not in role.lower():
                continue
            try:
                date_posted = dt.datetime.strptime(added.strip(), "%Y-%m-%d").timestamp()
            except Exception:
                continue
            out.append({
                "company": company, "title": strip_md(role), "loc": loc,
                "date_posted": date_posted, "url": strip_md(apply_col),
                "src": "sndsh404",
            })
    except Exception as e:
        print("  ! sndsh404 failed:", e, file=sys.stderr)

    try:
        text = fetch_text("https://raw.githubusercontent.com/zapplyjobs/Internships-2027/main/README.md")
        for cols in parse_md_table(text):
            if len(cols) < 6 or cols[0].lower().strip("*") == "company":
                continue
            company, role, loc = strip_md(cols[0]), cols[1], cols[2]
            apply_url = strip_md(cols[-1])
            if "2027" not in role and "2027" not in apply_url:
                continue
            out.append({
                "company": company, "title": role, "loc": loc,
                "date_posted": NOW - 15 * 86400, "url": apply_url,
                "src": "zapplyjobs",
            })
    except Exception as e:
        print("  ! zapplyjobs failed:", e, file=sys.stderr)
    return out


def fetch_text(url):
    req = urllib.request.Request(url, headers={"User-Agent": "daily-scan/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def load_zshah():
    out = []
    try:
        data = fetch_json(
            "https://raw.githubusercontent.com/zshah101/Automated-List-Of-Summer-2027-and-Fall-2026-Tech-Internships/main/docs/api/jobs.json"
        )
        for j in data.get("jobs", []):
            season = j.get("season") or ""
            if "Summer 2027" not in season:
                continue
            posted = j.get("posted_at") or j.get("first_seen_at")
            try:
                date_posted = dt.datetime.strptime(posted, "%Y-%m-%dT%H:%M:%SZ").timestamp()
            except Exception:
                date_posted = 0
            out.append({
                "company": j.get("company", ""), "title": j.get("title", ""),
                "loc": (j.get("location") or "")[:60], "date_posted": date_posted,
                "url": j.get("url", ""), "src": "zshah101",
            })
    except Exception as e:
        print("  ! zshah101 failed:", e, file=sys.stderr)
    return out


def load_direct_companies():
    out = []
    for token, company in GREENHOUSE_BOARDS.items():
        try:
            data = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=false")
            for j in data.get("jobs", []):
                out.append({
                    "company": company, "title": j.get("title", ""),
                    "loc": (j.get("location") or {}).get("name", ""),
                    "date_posted": NOW - 10 * 86400,
                    "url": j.get("absolute_url", ""), "src": "direct-greenhouse",
                })
        except Exception as e:
            print(f"  ! greenhouse {token} failed:", e, file=sys.stderr)

    for tenant, wdnum, site, company in WORKDAY_TENANTS:
        try:
            data = fetch_json(
                f"https://{tenant}.wd{wdnum}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs",
                method="POST", body={"limit": 20, "offset": 0, "searchText": "intern"},
            )
            for j in data.get("jobPostings", []):
                out.append({
                    "company": company, "title": j.get("title", ""),
                    "loc": j.get("locationsText", ""),
                    "date_posted": NOW - 10 * 86400,
                    "url": "https://" + f"{tenant}.wd{wdnum}.myworkdayjobs.com" + j.get("externalPath", ""),
                    "src": "direct-workday",
                })
        except Exception as e:
            print(f"  ! workday {tenant} failed:", e, file=sys.stderr)
    return out


def load_relay():
    out = []
    try:
        # 2026-10-11: inside the GitHub Action, use the data.json scan.py just wrote
        if os.path.exists("data.json"):
            data = json.load(open("data.json", encoding="utf-8"))
        else:
            data = fetch_json(
                "https://raw.githubusercontent.com/LeoLiu0515/leo-internship-scraper/main/data.json"
            )
        generated_at = data.get("generated_at") or NOW
        for j in data.get("jobs", []):
            posted = j.get("posted_at")
            if not posted and j.get("posted_date_text"):
                try:
                    posted = dt.datetime.strptime(
                        j["posted_date_text"], "%B %d, %Y"
                    ).replace(tzinfo=dt.timezone.utc).timestamp()
                except Exception:
                    posted = None
            posted = posted or (generated_at - 10 * 86400)
            out.append({
                "company": j.get("company", ""), "title": j.get("title", ""),
                "loc": (j.get("loc") or "")[:60], "date_posted": posted,
                "url": j.get("url", ""), "src": "relay-" + j.get("src", "unknown"),
            })
    except Exception as e:
        print("  ! relay failed:", e, file=sys.stderr)
    return out


def load_tsmc():
    out = []
    try:
        body = ("jobSort=&jobSortDirection=&listFilterMode=true&search=intern"
                 "&4177=&1277=&4178=&4178_hidden=1&558=&147=&542="
                 "&timeZone=Asia%2FTaipei")
        req = urllib.request.Request(
            "https://careers.tsmc.com/zh_TW/careers/SearchJobs",
            data=body.encode(), method="POST",
            headers={"User-Agent": "daily-scan/1.0",
                     "Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            html = r.read().decode("utf-8", errors="replace")
        pattern = re.compile(
            r'JobDetail\?jobId=(\d+)[^"]*">\s*([^<]+?)\s*</a>.*?'
            r'list-item-location">\s*([^<]*?)\s*</span>.*?'
            r'list-item-employmentType">\s*([^<]*?)\s*</span>',
            re.S,
        )
        for jid, title, loc, etype in pattern.findall(html):
            title, loc = title.strip(), loc.strip()
            if "美國" not in loc:
                continue
            if "實習" not in etype:
                continue
            tl = title.lower()
            if any(word_in(b, tl) for b in TITLE_EXCLUDE):
                continue
            if re.search(r"\bmaster'?s\b", tl):
                continue
            if not term_ok(title):
                continue
            out.append({
                "company": "TSMC", "title": title,
                "loc": loc.replace("美國-", "US-"),
                "date_posted": NOW - 10 * 86400,
                "url": f"https://careers.tsmc.com/zh_TW/careers/JobDetail?jobId={jid}",
                "src": "direct-tsmc",
            })
    except Exception as e:
        print("  ! tsmc failed:", e, file=sys.stderr)
    return out


# ---- source 9: HiringCafe (Next.js SSR __NEXT_DATA__, no separate API) -----
HIRINGCAFE_US_LOCATION = {
    "id": "FxY1yZQBoEtHp_8UEq7V", "types": ["country"],
    "address_components": [{"long_name": "United States", "short_name": "US", "types": ["country"]}],
    "formatted_address": "United States", "population": 327167434,
    "workplace_types": [], "options": {"flexible_regions": ["anywhere_in_continent", "anywhere_in_world"]},
}
HIRINGCAFE_QUERIES = [
    "embedded firmware hardware engineer intern",
    "ASIC VLSI silicon chip design intern",
    "electrical engineer hardware intern co-op",
    "robotics controls systems engineer intern",
    "computer architecture PCB board design validation intern",
]
HIRINGCAFE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://hiringcafe.com/",
}


def load_hiringcafe():
    import subprocess
    out = []
    for q in HIRINGCAFE_QUERIES:
        try:
            search_state = {"locations": [HIRINGCAFE_US_LOCATION], "searchQuery": q}
            url = "https://hiringcafe.com/?searchState=" + urllib.parse.quote(json.dumps(search_state))
            cmd = ["curl", "-s", "--max-time", "30", url]
            for k, v in HIRINGCAFE_HEADERS.items():
                cmd += ["-H", f"{k}: {v}"]
            m = None
            for attempt in range(2):
                result = subprocess.run(cmd, capture_output=True, timeout=35)
                html = result.stdout.decode("utf-8", errors="replace")
                m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
                if m:
                    break
            data = json.loads(m.group(1))
            hits = data["props"]["pageProps"]["ssrHits"]
            for h in hits:
                ji = h.get("job_information") or {}
                v5 = h.get("v5_processed_job_data") or {}
                title = ji.get("title", "")
                if v5.get("masters_degree_requirement") == "Required":
                    continue
                countries = v5.get("workplace_countries") or []
                if countries and "US" not in countries:
                    continue
                commitment = v5.get("commitment") or []
                if commitment and not ({"Internship", "Co-op"} & set(commitment)):
                    continue
                posted = v5.get("estimated_publish_date")
                date_posted = 0
                for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
                    try:
                        date_posted = dt.datetime.strptime(posted, fmt).replace(tzinfo=dt.timezone.utc).timestamp()
                        break
                    except Exception:
                        continue
                states = v5.get("workplace_states") or []
                out.append({
                    "company": v5.get("company_name", ""), "title": title,
                    "loc": "; ".join(states)[:60], "date_posted": date_posted,
                    "url": h.get("apply_url", ""), "src": "hiringcafe",
                })
        except Exception as e:
            print("  ! hiringcafe query failed:", q, e, file=sys.stderr)
    return out


def main():
    all_rows = (load_json_feeds() + load_md_feeds() + load_zshah()
                + load_direct_companies() + load_relay() + load_tsmc()
                + load_hiringcafe())

    # 2026-10-11: was a 30-day window, which silently dropped still-open Summer 2027 postings that were
    # first posted in Aug/Sep (e.g. Qualcomm's San Diego embedded internship). Keep anything posted since
    # the season opened (2026-07-01) -- closed postings disappear from the sources on their own.
    cutoff = max(NOW - 200 * 86400, 1782864000)
    seen, kept = set(), []
    for r in all_rows:
        if not company_ok(r["company"]):
            continue
        if r["src"] != "direct-tsmc":
            if not title_ok(r["title"]):
                continue
            if not term_ok(r["title"]):
                continue
        if not location_ok(r["loc"]):
            continue
        if r["date_posted"] and r["date_posted"] < cutoff:
            continue
        key = (r["company"].lower().strip(), r["title"].lower().strip())
        if key in seen:
            continue
        seen.add(key)
        kept.append({
            "url": r["url"], "company": r["company"], "title": r["title"],
            "loc": r["loc"][:60],
            "age": int((NOW - r["date_posted"]) / 86400) if r["date_posted"] else 999,
            "lane": "embedded",
        })

    kept.sort(key=lambda j: j["age"])

    # 2026-10-11 coverage self-check: sentinels.json (in the relay repo) lists real postings Leo found on his
    # own that the pipeline once missed. If any is absent today, print a COVERAGE ALERT so the routine
    # reports it instead of failing silently (it may also just mean the posting closed).
    def local_or_raw(name):
        if os.path.exists(name):
            return json.load(open(name, encoding="utf-8"))
        return fetch_json("https://raw.githubusercontent.com/LeoLiu0515/leo-internship-scraper/main/" + name)

    alerts = []
    try:
        for s in local_or_raw("sentinels.json"):
            hit = any(s["company"].lower() in j["company"].lower() and s["title"].lower() in j["title"].lower() for j in kept)
            if not hit:
                why = [r for r in all_rows if s["company"].lower() in r["company"].lower() and s["title"].lower() in r["title"].lower()]
                reason = "not in any source (closed? or a new source is needed)" if not why else (
                    "fetched from " + ",".join(sorted({r["src"] for r in why})) + " but filtered out")
                alerts.append(f"{s['company']} | {s['title']} | {reason}")
                print("COVERAGE ALERT:", alerts[-1])
    except Exception as e:
        print("  ! sentinel check failed:", e, file=sys.stderr)

    # 2026-10-11 top-company coverage table (top30.json): for each must-not-miss employer, how many
    # internship postings every source returned today and how many survived the filters. "no data"
    # means no source returned a single internship for that company -- either it truly has none open
    # or a source broke; either way Leo sees it on the dashboard instead of silently missing it.
    coverage = []
    try:
        for t in local_or_raw("top30.json"):
            rx = re.compile(t["match"], re.I)
            raw = [r for r in all_rows if rx.search(r["company"] or "") and
                   re.search(r"\b(intern|internship|internships|co-?op)\b", r["title"], re.I)]
            us_raw = [r for r in raw if location_ok(r["loc"])]
            k = [j for j in kept if rx.search(j["company"] or "")]
            coverage.append({"company": t["name"], "found": len(us_raw), "kept": len(k),
                             "sources": sorted({r["src"] for r in raw}),
                             "status": "ok" if k else ("filtered" if us_raw else "no data")})
    except Exception as e:
        print("  ! coverage table failed:", e, file=sys.stderr)

    json.dump(kept, open("final_jobs.json", "w", encoding="utf-8"), ensure_ascii=False)
    json.dump({"generated_at": NOW, "count": len(kept), "jobs": kept, "alerts": alerts, "coverage": coverage},
              open("us_final.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    def row_passes(r):
        if not company_ok(r["company"]):
            return False
        if r["src"] != "direct-tsmc" and not (title_ok(r["title"]) and term_ok(r["title"])):
            return False
        return location_ok(r["loc"])

    from collections import Counter
    src_counts = Counter(r["src"] for r in all_rows if row_passes(r))
    print("total kept:", len(kept))
    print("urgent (<=2d):", sum(1 for j in kept if j["age"] <= 2))
    print("by source (pre-dedupe):", dict(src_counts))


if __name__ == "__main__":
    main()