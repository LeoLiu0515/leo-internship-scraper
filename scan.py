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
            data = fetch_json(
                f"https://{tenant}.wd{wdnum}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs",
                method="POST", body={"limit": 20, "offset": 0, "searchText": "intern"},
            )
            for j in data.get("jobPostings", []):
                out.append({
                    "company": company, "title": j.get("title", ""),
                    "loc": j.get("locationsText", ""),
                    "url": "https://" + f"{tenant}.wd{wdnum}.myworkdayjobs.com" + j.get("externalPath", ""),
                    "src": "direct-workday", "fetched_at": NOW,
                })
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
            headers={"User-Agent": "leo-internship-scraper/1.0",
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
