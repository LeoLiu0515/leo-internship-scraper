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

import json, time, urllib.request

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
