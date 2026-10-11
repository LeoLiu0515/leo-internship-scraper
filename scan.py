#!/usr/bin/env python3
"""
scan.py -- runs on GitHub Actions (unrestricted network egress), fetches
direct-company ATS APIs that Leo's cloud dashboard routine cannot reach
directly (its sandbox blocks boards-api.greenhouse.io, *.myworkdayjobs.com,
api.lever.co with a 403). This script has no such restriction, so it does
the fetching here and commits the result as data.json. The dashboard's
daily_scan.py then reads data.json via raw.githubusercontent.com, which
IS allowed from that sandbox.

Every company below was hand-verified (curl-tested) to return real data
before being added -- do not add a guessed board token, it silently
returns nothing.

2026-09-02: added Cisco (Workday) and Amazon (its own jobs.amazon.com API,
not Greenhouse/Workday/Lever) after Leo asked to make sure his referral
companies (Cisco, TSMC, Amazon, Tesla) aren't missed. TSMC and Tesla were
checked but no working public API endpoint was found in the time available
-- see this repo's README for status.

2026-09-03: added TSMC (careers.tsmc.com). daily_scan.py's own direct
attempt at this (source 8 there) works fine locally/manually but 403s from
the dashboard's cloud sandbox -- same egress-allowlist problem as
Greenhouse/Workday/Lever, confirmed by an actual cloud run. Same fix as
those: fetch it here instead (unrestricted egress), tag src "tsmc",
daily_scan.py's load_relay() re-applies the TSMC-specific eligibility
checks (title exclusions, Masters-degree exclusion, season/year) before
folding it in as "relay-tsmc". Endpoint: POST to
https://careers.tsmc.com/zh_TW/careers/SearchJobs (classic Avature
server-rendered portal, no CSRF token or session/cookie needed --
verified with a bare curl POST). Structural filtering done here (not
content/eligibility filtering, which stays in daily_scan.py): only rows
with 美國 (US) in the location span and 實習 (Internship) as the
employment-type span are kept.

2026-09-04: TRIED adding HiringCafe (hiringcafe.com) here, same as TSMC's
relay fix -- did NOT work, reverted. Reasoning it seemed worth trying: the
dashboard routine's own cloud sandbox blocks HiringCafe at its
egress-proxy level ("organization policy", a block on that sandbox
specifically, confirmed by a live cloud run) -- a different failure mode
than TSMC's (TSMC's WAF gives an actual HTTP 403 from Cloudflare, meaning
it reaches hiringcafe.com's/TSMC's server and gets rejected there by IP
reputation). Since the dashboard sandbox's block looked like it might be
Anthropic-side rather than hiringcafe.com-side, GitHub Actions' totally
different network seemed worth a real test rather than assuming it would
fail the same way TSMC's relay attempt did. Pushed a version using curl
via subprocess (hiringcafe.com Cloudflare-challenges plain urllib even
with full browser headers; curl's TLS fingerprint passes it locally), ran
the workflow live, and got: `HTTP_STATUS:403` from Cloudflare on every
query -- so it turns out to be the same story as TSMC after all:
Cloudflare's bot-challenge itself flags GitHub Actions' IP ranges,
independent of which HTTP client makes the request. Removed the code
(kept as this note, not as an inert stub, so a future attempt doesn't
have to rediscover this) -- HiringCafe stays a manual/local-only source
via daily_scan.py's own source 9, same as TSMC.

2026-09-02 (later same day): broad sweep after Leo asked to add every
company we could think of. Verified and added: Flex, Waymo, Lucid Motors,
Roku (Greenhouse), and Micron, Intel, Analog Devices (Workday, site
"External", wd1). Intel/Micron/ADI are especially high-value for Leo --
real semiconductor/hardware intern roles (e.g. Intel "SoC Functional
Validation Intern"). Tried and confirmed NOT working (404/no valid site
name found): Qualcomm, Broadcom, Texas Instruments, Western Digital,
Garmin, Bosch (Workday); Apple, Google, Microsoft, Meta, TI, Micron*,
WD, Seagate, Bosch, Samsung, LG, Sony, Panasonic, Rockwell, Emerson, GE,
Motorola, John Deere, Caterpillar, Boeing, ASML, KLA, Infineon, onsemi,
Skyworks, Qorvo, Marvell, Ambarella, Cirrus Logic, Maxim, Microchip,
Teledyne, Jabil, Sanmina, Celestica, Benchmark, Plexus (Greenhouse).
Deliberately NOT added despite valid boards: Stripe, Coinbase, Airbnb,
Pinterest, Dropbox, Datadog, MongoDB, Elastic, Instacart, Okta,
Robinhood, Affirm, Block, Twilio, Scale AI, Databricks, Anthropic,
Palantir -- pure software/no hardware division, no embedded/hardware
intern roles, would just be wasted fetch time given Leo dropped the
software lane. "national" Greenhouse board exists but is a small,
identity-ambiguous company (6 jobs, no interns) -- skipped.
"""

import html as htmllib
import urllib.parse
import urllib.error
import json, re, time, urllib.request

NOW = time.time()

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
    # 2026-10-11: top-30 coverage push -- each of these was curl-tested before adding.
    ("broadcom", 1, "External_Career", "Broadcom"),
    ("marvell", 1, "MarvellCareers", "Marvell"),
    ("hpe", 5, "Jobsathpe", "Hewlett Packard Enterprise"),
    ("hp", 5, "ExternalCareerSite", "HP"),
    ("sec", 3, "Samsung_Careers", "Samsung"),
    ("amat", 1, "External", "Applied Materials"),
    ("kla", 1, "Search", "KLA"),
    ("cadence", 1, "External_Careers", "Cadence"),
    ("nxp", 3, "careers", "NXP"),
    ("microchiphr", 5, "External", "Microchip Technology"),
    ("globalfoundries", 1, "External", "GlobalFoundries"),
]
LEVER_BOARDS = {
    "voleon": "The Voleon Group",
}
# Amazon runs its own jobs API (not Greenhouse/Workday/Lever) -- public, no auth.
AMAZON_QUERY = "intern"
AMAZON_COUNTRY = "USA"


def fetch_json(url, method="GET", body=None):
    req = urllib.request.Request(url, method=method,
                                  headers={"User-Agent": "leo-internship-scraper/1.0"})
    if body is not None:
        req.data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def main():
    out = []

    for token, company in GREENHOUSE_BOARDS.items():
        try:
            data = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=false")
            for j in data.get("jobs", []):
                out.append({
                    "company": company, "title": j.get("title", ""),
                    "loc": (j.get("location") or {}).get("name", ""),
                    "url": j.get("absolute_url", ""),
                    "src": "direct-greenhouse", "fetched_at": NOW,
                })
        except Exception as e:
            print(f"! greenhouse {token} failed:", e)

    for tenant, wdnum, site, company in WORKDAY_TENANTS:
        try:
            seen_wd = set()
            for offset in range(0, 2000, 20):  # 2026-10-11: was only the first 20 -> missed e.g. Micron/ADI firmware interns
                data = fetch_json(
                    f"https://{tenant}.wd{wdnum}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs",
                    method="POST", body={"limit": 20, "offset": offset, "searchText": "intern"},
                )
                postings = data.get("jobPostings", [])
                if not postings:
                    break
                for j in postings:
                    path = j.get("externalPath", "")
                    if path in seen_wd:
                        continue
                    seen_wd.add(path)
                    out.append({
                        "company": company, "title": j.get("title", ""),
                        "loc": j.get("locationsText", ""),
                        "url": "https://" + f"{tenant}.wd{wdnum}.myworkdayjobs.com" + path,
                        "src": "direct-workday", "fetched_at": NOW,
                    })
                if offset + 20 >= (data.get("total") or 0):
                    break
        except Exception as e:
            print(f"! workday {tenant} failed:", e)

    for token, company in LEVER_BOARDS.items():
        try:
            data = fetch_json(f"https://api.lever.co/v0/postings/{token}?mode=json")
            for j in data:
                cat = j.get("categories", {}) or {}
                out.append({
                    "company": company, "title": j.get("text", ""),
                    "loc": cat.get("location", ""),
                    "url": j.get("hostedUrl", ""),
                    "src": "direct-lever", "fetched_at": NOW,
                })
        except Exception as e:
            print(f"! lever {token} failed:", e)

    try:
        data = fetch_json(
            f"https://www.amazon.jobs/en/search.json?base_query={AMAZON_QUERY}"
            f"&country={AMAZON_COUNTRY}&result_limit=100"
        )
        for j in data.get("jobs", []):
            out.append({
                "company": j.get("company_name") or "Amazon",
                "title": j.get("title", ""),
                "loc": j.get("normalized_location") or j.get("location", ""),
                "url": "https://www.amazon.jobs" + j.get("job_path", ""),
                "posted_date_text": j.get("posted_date", ""),  # e.g. "August 27, 2026" -- parse in daily_scan.py
                "src": "direct-amazon", "fetched_at": NOW,
            })
    except Exception as e:
        print("! amazon failed:", e)

    try:
        body = ("jobSort=&jobSortDirection=&listFilterMode=true&search=intern"
                 "&4177=&1277=&4178=&4178_hidden=1&558=&147=&542="
                 "&timeZone=Asia%2FTaipei")
        req = urllib.request.Request(
            "https://careers.tsmc.com/zh_TW/careers/SearchJobs",
            data=body.encode(), method="POST",
            headers={
                "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/128.0.0.0 Safari/537.36"),
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
                "Referer": "https://careers.tsmc.com/zh_TW/careers",
                "Origin": "https://careers.tsmc.com",
            },
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
            if "美國" not in loc or "實習" not in etype:
                continue  # structural only -- eligibility filtering happens in daily_scan.py
            out.append({
                "company": "TSMC", "title": title,
                "loc": loc.replace("美國-", "US-"),
                "url": f"https://careers.tsmc.com/zh_TW/careers/JobDetail?jobId={jid}",
                "src": "tsmc", "fetched_at": NOW,
            })
    except Exception as e:
        print("! tsmc failed:", e)


    # 2026-10-11: Qualcomm's own career site (Eightfold, public API) -- the San Diego "Embedded Engineering Internship"
    # was in neither community feed. Structural only; US/title/term filtering happens in daily_scan.py.
    # Same Eightfold API also serves Microsoft and Lam Research (verified 2026-10-11).
    for ef_host, ef_domain, ef_company in (("qualcomm.eightfold.ai", "qualcomm.com", "Qualcomm"),
                                           ("microsoft.eightfold.ai", "microsoft.com", "Microsoft"),
                                           ("lamresearch.eightfold.ai", "lamresearch.com", "Lam Research")):
        try:
            for start in range(0, 300, 10):
                d = fetch_json(f"https://{ef_host}/api/pcsx/search?domain={ef_domain}&query=intern"
                               f"&location=United%20States&num=10&start={start}")
                ps = (d.get("data") or {}).get("positions", [])
                if not ps:
                    break
                for p in ps:
                    locs = p.get("locations") or ([p.get("location")] if p.get("location") else [])
                    out.append({
                        "company": ef_company, "title": p.get("name") or "",
                        "loc": "; ".join(str(l) for l in locs)[:60],
                        "url": p.get("canonicalPositionUrl") or (f"https://{ef_host}/careers/job/" + str(p.get("id"))),
                        "posted_at": p.get("postedTs"),
                        "src": "direct-eightfold", "fetched_at": NOW,
                    })
        except Exception as e:
            print(f"! eightfold {ef_company} failed:", e)

    # Google's own careers site (results page embeds job data as JSON; verified 2026-10-11: it lists
    # "Hardware Engineering Intern, BS/MS, Summer 2027" that the community feeds title differently).
    try:
        gseen = set()
        for q in ("intern", "internship", "student"):
            for page in range(1, 6):
                u = ("https://www.google.com/about/careers/applications/jobs/results/?"
                     + urllib.parse.urlencode({"q": q, "location": "United States", "page": page}))
                req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    h = r.read().decode("utf-8", errors="replace")
                m = re.search(r"AF_initDataCallback[(]{key: 'ds:1'.*?data:(.*?), sideChannel", h, re.S)
                gd = json.loads(m.group(1)) if m else [None]
                rows = gd[0] or []
                for x in rows:
                    jid = str(x[0])
                    if jid in gseen:
                        continue
                    gseen.add(jid)
                    locs = x[9] if len(x) > 9 and isinstance(x[9], list) else []
                    if not any(len(l) > 5 and l[5] == "US" for l in locs):
                        continue
                    out.append({
                        "company": "Google", "title": str(x[1]),
                        "loc": "; ".join(str(l[0]) for l in locs)[:60],
                        "url": "https://www.google.com/about/careers/applications/jobs/results/" + jid,
                        "src": "direct-google", "fetched_at": NOW,
                    })
                if len(rows) < 20:
                    break
    except Exception as e:
        print("! google failed:", e)

    # 2026-10-11: Apple's student internships are evergreen umbrella postings ("Hardware Undergrad Engineering Internships",
    # "Internship Opportunities - Silicon Engineering") that sort far down by date, so search by relevance across several queries.
    try:
        hdr = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json", "Accept": "application/json",
               "Origin": "https://jobs.apple.com", "Referer": "https://jobs.apple.com/en-us/search"}
        r = urllib.request.urlopen(urllib.request.Request("https://jobs.apple.com/api/v1/CSRFToken", headers=hdr), timeout=30)
        hdr["x-apple-csrf-token"] = r.headers.get("x-apple-csrf-token")
        hdr["Cookie"] = "; ".join(c.split(";")[0] for c in (r.headers.get_all("Set-Cookie") or []))
        apple = {}
        for query in ("internship", "intern", "Internship Opportunities", "undergrad internships",
                      "silicon engineering internship", "hardware internship", "software internship",
                      "Silicon Engineering", "Opportunities", "students", "Internship Opportunities - Silicon Engineering"):
            for page in range(1, 6):
                body = {"query": query, "filters": {"locations": ["postLocation-USA"]}, "page": page, "locale": "en-us",
                        "sort": "relevance", "format": {"longDate": "MMMM D, YYYY", "mediumDate": "MMM D, YYYY"}}
                req = urllib.request.Request("https://jobs.apple.com/api/v1/search", data=json.dumps(body).encode(), headers=hdr, method="POST")
                res = json.loads(urllib.request.urlopen(req, timeout=30).read()).get("res", {})
                rows = res.get("searchResults", [])
                if not rows:
                    break
                for x in rows:
                    t = x.get("postingTitle", "")
                    if re.search(r"intern", t, re.I) and not re.search(r"internal|international", t, re.I):
                        apple[x["positionId"]] = x
        for pid, x in apple.items():
            title = re.sub(r"Internships\b", "Internship", x["postingTitle"])  # daily_scan wants the singular word
            out.append({
                "company": "Apple", "title": title,
                "loc": "; ".join(l.get("name", "") for l in x.get("locations", []))[:60] or "United States",
                "url": "https://jobs.apple.com/en-us/details/" + str(pid),
                "src": "direct-apple", "fetched_at": NOW,
            })
    except Exception as e:
        print("! apple failed:", e)


    # 2026-10-11: LinkedIn public (guest) job search, US, experience level = Internship.
    # This is the same index Leo browses and gets daily LinkedIn emails from, so anything he can
    # "just see on LinkedIn" should land here too. Structural only -- title/term/location/ITAR
    # filtering still happens in daily_scan.py. Titles that explicitly require US citizenship /
    # US-person status / clearance are dropped here because an F-1 student cannot take them.
    LI_QUERIES = [
        "embedded intern", "firmware intern", "hardware engineering intern", "hardware engineer intern",
        "electrical engineering intern", "electrical engineer intern", "FPGA intern", "ASIC intern",
        "silicon intern", "chip design intern", "verification intern", "validation intern",
        "test engineer intern", "robotics intern", "controls intern", "signal processing intern",
        "PCB intern", "systems engineering intern", "computer engineering intern", "embedded software intern",
    ]
    li_seen = set()
    li_block = re.compile(r"u\.?s\.? ?(citizen|person)|clearance|green card", re.I)
    # Top-company backstop (top30.json): one company-specific query each, so a big employer can
    # never disappear just because its title wording missed the generic keyword queries.
    try:
        top = json.load(open("top30.json", encoding="utf-8"))
    except Exception:
        top = []
    # company backstops first (they are what guarantees the top-30), then the generic keyword sweep
    li_jobs = [(t["name"] + " intern", 30) for t in top] + [(q, 150) for q in LI_QUERIES]
    li_ratelimited = 0

    def li_page(q, start):
        u = ("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?"
             + urllib.parse.urlencode({"keywords": q, "location": "United States", "f_E": "1",
                                       "sortBy": "DD", "start": start}))
        for attempt in range(3):
            try:
                req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    return r.read().decode("utf-8", errors="replace")
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 1:
                    time.sleep(40)  # rate limited: back off once, then retry
                    continue
                raise

    for q, depth in li_jobs:
        if li_ratelimited >= 2:
            # circuit breaker: LinkedIn keeps refusing this runner's IP; stop instead of burning an hour
            print(f"! linkedin rate-limited, skipped remaining query {q!r}")
            continue
        for start in range(0, depth, 10):
            try:
                page = li_page(q, start)
                li_ratelimited = 0
            except Exception as e:
                print(f"! linkedin {q!r} start={start} failed:", e)
                if isinstance(e, urllib.error.HTTPError) and e.code == 429:
                    li_ratelimited += 1
                break
            cards = re.findall(r"<li>(.*?)</li>", page, re.S)
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
                url = m_url.group(1)
                if url in li_seen:
                    continue
                li_seen.add(url)
                title = htmllib.unescape(re.sub(r"<[^>]+>", " ", m_t.group(1))).strip()
                title = " ".join(title.split())
                if li_block.search(title):
                    continue
                comp = " ".join(htmllib.unescape(re.sub(r"<[^>]+>", " ", m_c.group(1))).split())
                loc = " ".join(htmllib.unescape(re.sub(r"<[^>]+>", " ", m_l.group(1) if m_l else "")).split())
                posted = None
                if m_d:
                    try:
                        posted = time.mktime(time.strptime(m_d.group(1), "%Y-%m-%d"))
                    except Exception:
                        posted = None
                out.append({
                    "company": comp, "title": title, "loc": (loc or "United States")[:60],
                    "url": url, "posted_at": posted,
                    "src": "direct-linkedin", "fetched_at": NOW,
                })
            time.sleep(1.5)
    print(f"linkedin rows: {len(li_seen)}" + ("  (RATE LIMITED -- partial)" if li_ratelimited >= 2 else ""))

    result = {
        "generated_at": NOW,
        "generated_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW)),
        "jobs": out,
    }
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"wrote {len(out)} postings to data.json")


if __name__ == "__main__":
    main()
