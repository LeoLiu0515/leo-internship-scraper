#!/usr/bin/env python3
"""
screen.py -- pre-screens every dashboard job by reading its description, so Leo does not have to
open each posting to check eligibility. Runs on GitHub Actions after scan.py / daily_scan.py /
asia_scan.py (unrestricted network egress).

Verdicts (added as fields on each job):
  screen = "ok"     description read, nothing that blocks Leo
           "no"     description EXPLICITLY states a blocking requirement (evidence quoted)
           "unread" description could not be fetched -- never dropped, Leo decides
  why    = list of {"rule": ..., "quote": ...} (for "no")
  fit    = Leo's skills that the description mentions
  gaps   = skills the description asks for that Leo does not have

Rules are deliberately literal: a job is only marked "no" when the text says so. Leo's own
decisions (2026-10-11): US citizen / green card / US person / clearance / ITAR, Master's/PhD-only,
graduation window that excludes 2028, explicit non-summer term or >= 6-month duration, and
"no visa sponsorship now or in the future" all count as ineligible -- unless the text explicitly
welcomes F-1 / CPT students.

Results are cached per URL in screen_cache.json (re-checked after 7 days) so each daily run only
fetches descriptions for new postings.
"""
import html as htmllib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

NOW = time.time()
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
      "Accept-Language": "en-US,en;q=0.9,zh-TW;q=0.8"}
CACHE_FILE = "screen_cache.json"
RECHECK_DAYS = 7
MAX_FETCH = int(os.environ.get("SCREEN_MAX_FETCH", "900"))  # per run, keeps the workflow bounded


def get(url, headers=None, data=None, method=None, timeout=25):
    h = dict(UA)
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h, data=data, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace"), r


def strip_html(s):
    s = htmllib.unescape(s or "")
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</div>|</h\d>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = htmllib.unescape(s)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


# ---------------------------------------------------------------- description fetchers
_apple_hdr = None


def apple_headers():
    global _apple_hdr
    if _apple_hdr is None:
        hdr = {"Content-Type": "application/json", "Accept": "application/json",
               "Origin": "https://jobs.apple.com", "Referer": "https://jobs.apple.com/en-us/search"}
        _, r = get("https://jobs.apple.com/api/v1/CSRFToken", headers=hdr)
        hdr["x-apple-csrf-token"] = r.headers.get("x-apple-csrf-token")
        hdr["Cookie"] = "; ".join(c.split(";")[0] for c in (r.headers.get_all("Set-Cookie") or []))
        _apple_hdr = hdr
    return _apple_hdr


WORKDAY_SITES = {"vermeer": "externalcareersite", "nvidia": "NVIDIAExternalCareerSite", "cisco": "Cisco_Careers",
                 "micron": "External", "intel": "External", "analogdevices": "External", "jj": "jj",
                 "sbdinc": "Stanley_Black_Decker_Career_Site", "amat": "External", "hp": "ExternalCareerSite"}


def fetch_description(url, depth=0):
    u = urllib.parse.urlparse(url)
    host, path = u.netloc.lower(), u.path

    # redirectors (community-list short links) -> follow to the real ATS URL, then dispatch again
    if depth == 0 and re.search(r"zapply\.jobs|simplify\.jobs/p/|bit\.ly|lnkd\.in", host + path):
        _, r = get(url)
        final = r.geturl()
        if final and final != url:
            return fetch_description(final, 1)

    m = re.search(r"withwaymo\.com/.*[?&]gh_jid=(\d+)", url)
    if m:
        t, _ = get(f"https://boards-api.greenhouse.io/v1/boards/waymo/jobs/{m.group(1)}")
        return strip_html(json.loads(t).get("content", ""))

    m = re.search(r"(https://[^/]*oraclecloud\.com)/hcmUI/CandidateExperience/[a-z]{2}/sites/([\w-]+)/job/(\d+)", url)
    if m:
        api = (m.group(1) + "/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?expand=all&onlyData=true"
               "&finder=ById;Id=%22" + m.group(3) + "%22,siteNumber=" + m.group(2))
        t, _ = get(api, headers={"Accept": "application/json"})
        items = json.loads(t).get("items", [])
        if items:
            it = items[0]
            return strip_html("\n".join(str(it.get(k) or "") for k in
                                        ("ExternalDescriptionStr", "ExternalResponsibilitiesStr",
                                         "ExternalQualificationsStr", "CorporateDescriptionStr")))
        return ""

    m = re.search(r"linkedin\.com/jobs/view/.*?(\d{8,})", url)
    if m:
        t, _ = get("https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/" + m.group(1))
        mm = re.search(r'show-more-less-html__markup[^>]*>(.*?)</div>', t, re.S)
        return strip_html(mm.group(1) if mm else t)

    m = re.search(r"greenhouse\.io/(?:embed/job_app\?for=)?([\w-]+)/jobs/(\d+)", url) or \
        re.search(r"greenhouse\.io/.*?[?&]for=([\w-]+).*?[?&]token=(\d+)", url)
    if m:
        t, _ = get(f"https://boards-api.greenhouse.io/v1/boards/{m.group(1)}/jobs/{m.group(2)}")
        return strip_html(json.loads(t).get("content", ""))
    m = re.search(r"[?&]gh_jid=(\d+)", url)
    if m and "greenhouse" not in host:
        pass  # company-hosted greenhouse page; fall through to generic fetch

    if "myworkdayjobs.com" in host:
        tenant = host.split(".")[0]
        parts = [p for p in path.split("/") if p]
        if parts and re.fullmatch(r"[a-z]{2}-[A-Z]{2}", parts[0]):
            parts = parts[1:]
        if parts and parts[0] == "job":
            parts = [WORKDAY_SITES.get(tenant, "External")] + parts
        site, rest = parts[0], "/".join(parts[1:])
        t, _ = get(f"https://{host}/wday/cxs/{tenant}/{site}/{rest}", headers={"Accept": "application/json"})
        info = json.loads(t).get("jobPostingInfo", {})
        return strip_html(info.get("jobDescription", ""))

    m = re.search(r"jobs\.lever\.co/([\w.-]+)/([0-9a-f-]{36})", url)
    if m:
        t, _ = get(f"https://api.lever.co/v0/postings/{m.group(1)}/{m.group(2)}")
        j = json.loads(t)
        return strip_html(j.get("description", "") + "\n" + "\n".join(
            l.get("text", "") + "\n" + l.get("content", "") for l in j.get("lists", [])) + "\n" + j.get("additional", ""))

    m = re.search(r"jobs\.ashbyhq\.com/([^/]+)/([0-9a-f-]{36})", url)
    if m:
        t, _ = get(f"https://api.ashbyhq.com/posting-api/job-board/{m.group(1)}")
        for j in json.loads(t).get("jobs", []):
            if j.get("id") == m.group(2):
                return strip_html(j.get("descriptionHtml", ""))
        return ""

    m = re.search(r"jobs\.apple\.com/.*?/details/(\d+)", url)
    if m:
        t, _ = get(f"https://jobs.apple.com/api/v1/jobDetails/{m.group(1)}?locale=en-us", headers=apple_headers())
        r = json.loads(t).get("res", {})
        return strip_html("\n".join(str(r.get(k, "")) for k in
                                    ("jobSummary", "description", "minimumQualifications", "preferredQualifications")))

    m = re.search(r"amazon\.jobs/\w+/jobs/(\d+)", url)
    if m:
        t, _ = get(f"https://www.amazon.jobs/en/jobs/{m.group(1)}")
        mm = re.findall(r'<div class="section[^"]*">(.*?)</div>\s*</div>', t, re.S)
        return strip_html(" ".join(mm) if mm else t)

    m = re.search(r"104\.com\.tw/job/(\w+)", url)
    if m:
        t, _ = get(f"https://www.104.com.tw/job/ajax/content/{m.group(1)}",
                   headers={"Referer": f"https://www.104.com.tw/job/{m.group(1)}", "Accept": "application/json"})
        d = json.loads(t).get("data", {})
        jd, cond = d.get("jobDetail", {}), d.get("condition", {})
        return strip_html("\n".join([jd.get("jobDescription", ""), cond.get("other", ""),
                                     " ".join(str(x) for x in cond.get("edu", []) if x) if isinstance(cond.get("edu"), list) else str(cond.get("edu", "")),
                                     str(cond.get("major", ""))]))

    m = re.search(r"(?:careers\.qualcomm\.com|qualcomm\.eightfold\.ai)/careers/job/(\d+)", url)
    if m:
        t, _ = get(f"https://qualcomm.eightfold.ai/api/pcsx/position_details?position_id={m.group(1)}&domain=qualcomm.com&hl=en")
        d = json.loads(t).get("data", {})
        return strip_html(d.get("jobDescription") or d.get("job_description") or json.dumps(d))

    m = re.search(r"jobs\.smartrecruiters\.com/([^/]+)/(\d+)", url)
    if m:
        t, _ = get(f"https://api.smartrecruiters.com/v1/companies/{m.group(1)}/postings/{m.group(2)}")
        sec = json.loads(t).get("jobAd", {}).get("sections", {})
        return strip_html(" ".join(v.get("text", "") for v in sec.values() if isinstance(v, dict)))

    # generic: plain HTML page (works for many company-hosted / iCIMS / Taleo-lite pages)
    t, _ = get(url)
    m = re.search(r'<script type="application/ld\+json">(.*?)</script>', t, re.S)
    if m:
        try:
            ld = json.loads(m.group(1))
            ld = ld[0] if isinstance(ld, list) else ld
            if isinstance(ld, dict) and ld.get("description"):
                return strip_html(ld["description"])
        except Exception:
            pass
    return strip_html(t)


# ---------------------------------------------------------------- rules
def sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?。；;])\s+|\n", text) if s.strip()]


F1_FRIENDLY = re.compile(r"\bCPT\b|curricular practical training|\bF-?1\b[^.]{0,60}(eligible|welcome|encouraged|accepted|can apply)", re.I)

RULES = [
    ("限美國公民/綠卡/US person", re.compile(
        r"must be (a )?(u\.?s\.?|united states) citizen|(u\.?s\.?|united states) citizenship (is )?(required|needed)|"
        r"requires? (u\.?s\.?|united states) citizenship|(u\.?s\.?|us) citizens? only|"
        r"(u\.?s\.?|us) (citizen|person)s?\b[^.]{0,40}(required|only|must)|must (be|qualify as) (a )?(u\.?s\.?|us) person|"
        r"green card|lawful permanent resident[^.]{0,30}(required|only)|permanent residents? only", re.I)),
    ("需要安全許可 (clearance)", re.compile(
        r"(active|current|interim|obtain|maintain|eligib\w+ (for|to obtain)|ability to obtain)[^.]{0,30}"
        r"(secret|top secret|ts/sci|security|dod|government) clearance|clearance (is )?required", re.I)),
    ("ITAR / 出口管制", re.compile(r"\bITAR\b|international traffic in arms|export administration regulations[^.]{0,60}(u\.?s\.? person|citizen)", re.I)),
    ("不提供簽證擔保", re.compile(
        r"(not|unable to|will not|won't|cannot|can't|does not|do not)\s+(be able to\s+)?(provide|offer|support|sponsor)[^.]{0,30}sponsor|"
        r"(not|unable to|will not|won't|cannot|does not|do not)\s+sponsor|"
        r"without (the need for |requiring )?(current or future |now or in the future )?(employer |visa |immigration )?sponsorship|"
        r"not (be )?eligible for (visa |immigration )?sponsorship|sponsorship (is )?not (available|offered|provided)|"
        r"(now or in the future|currently or in the future)[^.]{0,40}sponsorship|sponsorship[^.]{0,40}(now or in the future)", re.I)),
    ("限碩士/博士", re.compile(
        r"(must be|currently|actively)?\s*(enrolled|pursuing|working towards?)[^.]{0,25}\b(master'?s|ms|m\.s\.|phd|ph\.d\.?|doctoral|graduate)\b[^.]{0,20}(degree|program|student)", re.I)),
    ("不是暑期 / 長期", re.compile(
        r"\b(6|six|7|seven|8|eight|9|nine|10|ten|11|eleven|12|twelve|16|sixteen)[- ]?(\+\s*)?months?\b[^.]{0,30}(internship|co-?op|program|assignment|duration)|"
        r"(internship|co-?op|assignment)[^.]{0,30}\b(6|six|8|eight|12|twelve|16|sixteen)[- ]?months?\b|"
        r"\b(spring|fall|autumn|winter) (2026|2027) (co-?op|internship|semester)\b|"
        r"學期制|學年制|一年期|實習期間[^。]{0,10}(一年|半年|六個月|6個月)|每週[^。]{0,6}(4|四|5|五)天", re.I)),
    ("當地學生限定", re.compile(r"singapore(an)? citizens?( and| or|/) ?(prs?|permanent residents?)[^.]{0,30}only|"
                               r"(enrolled|studying) (in|at) (a )?(local|singapore(an)?|japanese|korean) universit|"
                               r"限(本國籍|新加坡籍)", re.I)),
]
DEGREE_OK = re.compile(r"bachelor|undergrad|b\.?s\.?\b|bs/ms|bs or ms|bs, ms|大學", re.I)
GRAD_SENT = re.compile(r"graduat|class of|畢業", re.I)
YEAR = re.compile(r"\b(20[2-3]\d)\b")

FIT = [("C", r"\bC\b(?!\+|#)|\bC/C\+\+"), ("C++", r"C\+\+"), ("Python", r"\bpython\b"), ("embedded", r"embedded"),
       ("firmware", r"firmware"), ("MCU", r"microcontroller|\bMCU\b|\bARM\b|cortex"), ("CAN", r"\bCAN\b(?: bus)?"),
       ("UART/I2C/SPI", r"\bUART\b|\bI2C\b|\bI²C\b|\bSPI\b"), ("PCB", r"\bPCB\b|printed circuit"), ("KiCad/EDA", r"kicad|altium|orcad|schematic"),
       ("lab tools", r"oscilloscope|logic analyzer|multimeter|soldering"), ("Verilog/FPGA", r"\bverilog\b|\bFPGA\b"),
       ("ML/PyTorch", r"pytorch|machine learning|deep learning|neural network"), ("Git", r"\bgit\b"), ("Arduino/RPi", r"arduino|raspberry pi"),
       ("signals", r"signal processing|\bDSP\b")]
GAPS = [("SystemVerilog/UVM", r"systemverilog|\bUVM\b"), ("RTOS", r"\bRTOS\b|freertos|zephyr"), ("Linux kernel/drivers", r"linux kernel|device driver|kernel module"),
        ("Rust", r"\brust\b"), ("Cadence/Synopsys", r"cadence|synopsys|virtuoso|innovus"), ("RF", r"\bRF\b|radio frequency|antenna"),
        ("analog IC", r"analog (ic|circuit) design|mixed-signal design"), ("MATLAB/Simulink", r"simulink"), ("LabVIEW", r"labview")]


def screen_text(text):
    why = []
    # application-form question blocks ("Are you a U.S. person, which means ...?") ask for info; not requirements
    text = re.sub(r"(?is)\b(are|do|will|would|have) you\b[^?]{0,1500}\?", " ", text)
    f1_ok = bool(F1_FRIENDLY.search(text))
    for s in sentences(text):
        if len(s) > 600:
            s = s[:600]
        # application-form questions ("Are you a U.S. person ...?", "Will you require sponsorship?")
        # ask for information; they are not requirements, so they never count as evidence
        if "?" in s or re.match(r"\s*(are|do|will|would|have|can) you\b", s, re.I):
            continue
        if re.search(r"certain (positions|roles|jobs)|some (positions|roles)|may (be required|require)", s, re.I):
            continue  # generic company boilerplate, not a stated requirement of this posting
        for name, rx in RULES:
            if not rx.search(s):
                continue
            if name == "不提供簽證擔保" and f1_ok:
                continue
            if name in ("限美國公民/綠卡/US person",) and re.search(r"regard to|without regard|protected|discriminat|status as", s, re.I):
                continue  # EEO boilerplate ("... without regard to citizenship status")
            if name == "限碩士/博士" and DEGREE_OK.search(s):
                continue
            if name == "不是暑期 / 長期" and re.search(r"summer", s, re.I) and not re.search(r"\b(6|six|12|twelve|16|sixteen)[- ]?months?\b", s, re.I):
                continue
            if name == "不是暑期 / 長期" and re.search(r"\b(2|3|two|three)[- ]?months?\b|\b1[0-4][- ]?weeks?\b|\b(10|11|12)[- ]week", s, re.I):
                continue  # a short / summer-length option is offered alongside the long one
            why.append({"rule": name, "quote": s[:220]})
        if GRAD_SENT.search(s):
            ys = sorted({int(y) for y in YEAR.findall(s)})
            # only an explicit upper bound / window can exclude a 2028 graduate. Lower bounds
            # ("December 2027 or later", "must not have graduated before Sept 2027") never exclude him.
            lower_bound = re.search(r"or (later|after)|and (later|after|beyond)|\bafter\b|not[^.]{0,30}before|at least|no earlier", s, re.I)
            if ys and 2028 not in ys and not lower_bound:
                lo, hi = ys[0], ys[-1]
                window = len(ys) >= 2 and re.search(r"between|from|through|[-–]|\bto\b", s, re.I)
                excluded = (window and not (lo <= 2028 <= hi)) or (
                    len(ys) == 1 and hi < 2028 and re.search(
                        r"(graduat\w*|graduation)[^.]{0,25}\b(in|by|before|no later than|date of|between)\b|"
                        r"expected (to )?graduat\w*[^.]{0,20}(in|by)|(december|may|june|spring|fall|winter) %d grad" % hi, s, re.I))
                if excluded:
                    why.append({"rule": "畢業時間不含 2028", "quote": s[:220]})
    seen, uniq = set(), []
    for w in why:
        if w["rule"] not in seen:
            seen.add(w["rule"]); uniq.append(w)
    fit = [n for n, p in FIT if re.search(p, text, re.I if n not in ("C", "CAN") else 0)]
    gaps = [n for n, p in GAPS if re.search(p, text, re.I if n != "RF" else 0)]
    return uniq, fit, gaps


def screen_job(url, cache, budget):
    c = cache.get(url)
    if c and NOW - c.get("checked_at", 0) < RECHECK_DAYS * 86400:
        return c, budget
    if budget <= 0:
        return (c or {"screen": "unread", "why": [], "fit": [], "gaps": [], "checked_at": 0}), budget
    try:
        text = fetch_description(url)
        time.sleep(0.4)
    except Exception as e:
        text = ""
        print("  ! desc fetch failed:", url[:90], str(e)[:80], file=sys.stderr)
    if len(text) < 300 or re.search(r"enable javascript|please wait while|access denied", text[:500], re.I):
        res = {"screen": "unread", "why": [], "fit": [], "gaps": [], "checked_at": NOW}
    else:
        why, fit, gaps = screen_text(text)
        res = {"screen": "no" if why else "ok", "why": why, "fit": fit, "gaps": gaps, "checked_at": NOW}
    cache[url] = res
    return res, budget - 1


def main():
    files = sys.argv[1:] or ["us_final.json", "asia.json"]
    try:
        cache = json.load(open(CACHE_FILE, encoding="utf-8"))
    except Exception:
        cache = {}
    budget = MAX_FETCH
    for fn in files:
        if not os.path.exists(fn):
            print("skip (missing):", fn)
            continue
        doc = json.load(open(fn, encoding="utf-8"))
        jobs = doc["jobs"] if isinstance(doc, dict) else doc
        counts = {}
        for j in jobs:
            res, budget = screen_job(j["url"], cache, budget)
            j["screen"], j["why"], j["fit"], j["gaps"] = res["screen"], res["why"], res["fit"], res["gaps"]
            counts[res["screen"]] = counts.get(res["screen"], 0) + 1
        json.dump(doc, open(fn, "w", encoding="utf-8"), ensure_ascii=False, indent=1 if fn != "asia.json" else None)
        print(fn, counts)
    # keep the cache bounded: drop entries not checked for 60 days
    cache = {k: v for k, v in cache.items() if NOW - v.get("checked_at", 0) < 60 * 86400 or v.get("checked_at", 0) == 0}
    json.dump(cache, open(CACHE_FILE, "w", encoding="utf-8"), ensure_ascii=False)


if __name__ == "__main__":
    main()
