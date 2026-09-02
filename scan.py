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
"""

import json, time, urllib.request

NOW = time.time()

GREENHOUSE_BOARDS = {
    "flyzipline": "Zipline",
    "axontalentcommunity": "Axon",
    "verkada": "Verkada",
}
WORKDAY_TENANTS = [
    # (tenant, host_wd_number, site, display_name)
    ("nvidia", 5, "NVIDIAExternalCareerSite", "NVIDIA"),
    ("vermeer", 5, "externalcareersite", "Vermeer"),
    ("sbdinc", 1, "Stanley_Black_Decker_Career_Site", "Stanley Black & Decker"),
    ("jj", 5, "jj", "Johnson & Johnson"),
]
LEVER_BOARDS = {
    "voleon": "The Voleon Group",
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
