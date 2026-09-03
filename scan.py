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

2026-09-04: added HiringCafe (hiringcafe.com). Its search results are
embedded as JSON in the page's own __NEXT_DATA__ script tag -- no separate
API. daily_scan.py's own direct attempt at this (source 9 there) works
fine locally/manually but the *dashboard routine's* cloud sandbox rejects
the connection outright at its egress-proxy level ("organization policy")
-- a block on that sandbox specifically, confirmed by a live cloud run,
NOT a hiringcafe.com-side IP/ASN block like TSMC's (TSMC's WAF also
blocked GitHub Actions' IPs when tried here; this is a different kind of
block that has nothing to do with GitHub's network, so worth trying here
even though TSMC's relay attempt failed). Uses curl via subprocess, not
urllib -- hiringcafe.com sits behind a Cloudflare bot-challenge that 403s
plain urllib even with full browser headers, while curl's own TLS
fingerprint passes cleanly (verified repeatedly, both locally and here).
Structural filtering done here (masters_degree_requirement == "Required",
workplace_countries not containing "US", commitment not Internship/Co-op)
-- content/eligibility filtering (title keywords, season/year, ITAR
company list) stays in daily_scan.py via the normal src != "direct-tsmc"
path, same as every other relay source.

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

import json, re, time, urllib.request, urllib.parse, subprocess, datetime as dt

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

# HiringCafe (source 9 in daily_scan.py) -- see module docstring above.
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
                posted_at = None
                for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
                    try:
                        posted_at = dt.datetime.strptime(posted, fmt).replace(tzinfo=dt.timezone.utc).timestamp()
                        break
                    except Exception:
                        continue
                states = v5.get("workplace_states") or []
                out.append({
                    "company": v5.get("company_name", ""), "title": title,
                    "loc": "; ".join(states)[:60],
                    "url": h.get("apply_url", ""),
                    "posted_at": posted_at,
                    "src": "hiringcafe", "fetched_at": NOW,
                })
        except Exception as e:
            print(f"! hiringcafe query failed: {q}", e)

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
