"""
SBI TT Buying Rate collector - Somu & Associates (casomu.com)

Downloads the forex card rate PDF that State Bank of India publishes each
working day, reads the rates out of it, and stores them:

  rates/<CCY>.csv                         one file per currency, one row per SBI card
  pdf_files/YYYY/MM/YYYY-MM-DD_HHMM.pdf   the original SBI card, kept as evidence

Run by GitHub Actions every hour during Indian banking hours. If SBI has not
issued a new card since the last run, nothing changes and nothing is committed.

Usage:
  python fetch_rates.py                 download today's card from SBI
  python fetch_rates.py --file X.pdf    process a PDF already on disk
"""

import argparse
import csv
import io
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import requests
from pypdf import PdfReader

SBI_URLS = [
    "https://sbi.bank.in/documents/16012/1400784/FOREX_CARD_RATES.pdf",
    "https://bank.sbi/documents/16012/1400784/FOREX_CARD_RATES.pdf",
]
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
    ),
    "Accept": "application/pdf,*/*",
}
IST = timezone(timedelta(hours=5, minutes=30))

RATE_COLUMNS = [
    "TT BUY", "TT SELL", "BILL BUY", "BILL SELL",
    "CARD BUY", "CARD SELL", "CN BUY", "CN SELL",
]
CSV_HEADER = ["DATE", "TIME"] + RATE_COLUMNS + ["PDF"]

ROOT = os.path.dirname(os.path.abspath(__file__))
RATES_DIR = os.path.join(ROOT, "rates")
PDF_DIR = os.path.join(ROOT, "pdf_files")

# "UNITED STATES DOLLAR USD/INR 92.4 93.25 92.33 93.42 92.33 93.42 91.25 93.75"
ROW_RE = re.compile(r"\b([A-Z]{3})\s*/\s*INR((?:\s+\d+(?:\.\d+)?){8})")
DATE_RE = re.compile(r"Date\s*:?\s*(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})")
TIME_RE = re.compile(r"Time\s*:?\s*(\d{1,2})[:.](\d{2})\s*([AaPp]\.?[Mm]\.?)?")


class RateError(Exception):
    pass


def log(msg):
    stamp = datetime.now(IST).strftime("%d.%m.%Y %H:%M:%S")
    print(f"[{stamp} IST] {msg}", flush=True)


# ---------------------------------------------------------------- download

def download(attempts=3, wait=20):
    """Fetch the current SBI card. Tries each SBI address, a few times."""
    last_error = None
    for attempt in range(1, attempts + 1):
        for url in SBI_URLS:
            try:
                r = requests.get(url, headers=HEADERS, timeout=60)
                if r.status_code != 200:
                    raise RateError(f"HTTP {r.status_code}")
                if b"%PDF-" not in r.content[:1024]:
                    raise RateError("response is not a PDF (SBI may be under maintenance)")
                log(f"Downloaded {len(r.content):,} bytes from {url}")
                return r.content
            except (requests.RequestException, RateError) as e:
                last_error = e
                log(f"Attempt {attempt}: {url} failed - {e}")
        if attempt < attempts:
            time.sleep(wait)
    raise RateError(f"Could not download the SBI card: {last_error}")


# ---------------------------------------------------------------- parse

def parse(pdf_bytes, expect_date=None):
    """Return (published datetime, {currency: [8 rates]}) from an SBI card PDF.

    expect_date (a date) is used only for archived cards whose printed date is
    ambiguous (e.g. 1-9-2020): if day and month are swapped, it settles the order."""
    reader = PdfReader(io.BytesIO(pdf_bytes), strict=False)
    pages = [(page.extract_text() or "") for page in reader.pages[:4]]
    text = "\n".join(pages)

    d = DATE_RE.search(text)
    t = TIME_RE.search(text)
    if not d:
        raise RateError("publication date not found in the PDF")
    a, b, year = int(d.group(1)), int(d.group(2)), int(d.group(3))
    # Current cards print 23-11-2026 (day first); 2020-21 cards printed 1/13/2020 (month first)
    if "/" in d.group(0):
        month, day = a, b
    else:
        day, month = a, b
    if month > 12 and day <= 12:
        day, month = month, day
    if expect_date and (year, month, day) != (expect_date.year, expect_date.month, expect_date.day) \
            and (year, day, month) == (expect_date.year, expect_date.month, expect_date.day):
        day, month = month, day
    hour, minute = (int(t.group(1)), int(t.group(2))) if t else (0, 0)
    if t and t.group(3):
        pm = t.group(3).lower().startswith("p")
        if pm and hour < 12:
            hour += 12
        if not pm and hour == 12:
            hour = 0
    published = datetime(year, month, day, hour, minute)

    # Some cards carry two tables on separate pages: "below Rs. 10 lakhs" and
    # "between Rs. 10 lakhs and Rs. 20 lakhs (to be used as reference rates)".
    # Income-tax uses the reference table.
    def rates_on(page_text):
        found = {}
        for m in ROW_RE.finditer(page_text):
            found.setdefault(m.group(1), [float(x) for x in m.group(2).split()[:8]])
        return found

    tables = [(p, rates_on(p)) for p in pages]
    tables = [(p, r) for p, r in tables if len(r) >= 5]
    if not tables:
        raise RateError("no rate table found in the PDF")
    reference = [r for p, r in tables if re.search(r"BETWEEN|REFERENCE", p, re.I)]
    not_below = [r for p, r in tables if not re.search(r"BELOW\s+RS", p, re.I)]
    rates = (reference or not_below or [tables[0][1]])[0]

    validate(published, rates)
    return published, rates


def validate(published, rates):
    now = datetime.now(IST).replace(tzinfo=None)
    if published > now + timedelta(days=1):
        raise RateError(f"card date {published} is in the future")
    if published.year < 2015:
        raise RateError(f"card date {published} is implausible")
    if len(rates) < 10:
        raise RateError(f"only {len(rates)} currencies found - layout may have changed")
    usd = rates.get("USD")
    if not usd or not any(40 < x < 250 for x in usd[:4]):
        raise RateError(f"USD rates look wrong: {usd}")
    if usd[0] and not (40 < usd[0] < 250):
        raise RateError(f"USD TT buying rate looks wrong: {usd[0]}")
    for ccy, r in rates.items():
        buy, sell = r[0], r[1]
        if buy > 0 and sell > 0 and buy > sell:
            raise RateError(f"{ccy}: TT buy {buy} is above TT sell {sell}")


# ---------------------------------------------------------------- save

def pdf_relpath(published):
    return "pdf_files/{:%Y}/{:%m}/{:%Y-%m-%d_%H%M}.pdf".format(published, published, published)


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows):
    rows.sort(key=lambda r: (r["DATE"], r["TIME"]))
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_HEADER)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def fmt_rate(x):
    return ("%.4f" % x).rstrip("0").rstrip(".") if x else "0"


def save(published, rates, pdf_bytes, pdf_path=None):
    """Add this card to every currency file. Returns False if it was already stored."""
    date_s, time_s = published.strftime("%Y-%m-%d"), published.strftime("%H:%M")
    rel = pdf_path if pdf_path is not None else pdf_relpath(published)

    usd_rows = read_csv(os.path.join(RATES_DIR, "USD.csv"))
    if any(r["DATE"] == date_s and r["TIME"] == time_s for r in usd_rows):
        return False

    if pdf_bytes is not None and rel:
        full = os.path.join(ROOT, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(pdf_bytes)

    os.makedirs(RATES_DIR, exist_ok=True)
    for ccy, r in sorted(rates.items()):
        path = os.path.join(RATES_DIR, f"{ccy}.csv")
        rows = [x for x in read_csv(path) if not (x["DATE"] == date_s and x["TIME"] == time_s)]
        row = {"DATE": date_s, "TIME": time_s, "PDF": rel or ""}
        row.update({col: fmt_rate(v) for col, v in zip(RATE_COLUMNS, r)})
        rows.append(row)
        write_csv(path, rows)
    return True


# ---------------------------------------------------------------- summary for the website

SUMMARY_PATH = os.path.join(ROOT, "site", "summary.json")


def build_summary():
    """Write site/summary.json: for every currency, the month-end TT buying rate
    of each month and the latest 7 rates. The website prints this as page text."""
    import calendar
    import json

    out = {"updated": datetime.now(IST).strftime("%Y-%m-%d %H:%M"), "currencies": {}}
    for name in sorted(os.listdir(RATES_DIR)):
        if not name.endswith(".csv"):
            continue
        ccy = name[:-4]
        days = {}                                   # date -> earliest card with a TT buying rate
        for r in read_csv(os.path.join(RATES_DIR, name)):
            try:
                tt = float(r["TT BUY"] or 0)
            except ValueError:
                continue
            if tt > 0 and r["DATE"] not in days:
                days[r["DATE"]] = (r["DATE"], r["TIME"], tt, r.get("PDF", ""))
        if len(days) < 100:                         # currency SBI rarely quotes - leave it out
            continue
        keys = sorted(days)
        first, last = keys[0], keys[-1]

        months = []
        y, m = int(last[:4]), int(last[5:7])
        while (y, m) >= (int(first[:4]), int(first[5:7])):
            month_end = "%04d-%02d-%02d" % (y, m, calendar.monthrange(y, m)[1])
            if month_end > last:
                months.append(["%04d-%02d" % (y, m), month_end, None, None, None, None])
            elif month_end >= first:
                # latest card on or before the month-end
                lo, hi = 0, len(keys) - 1
                while lo < hi:
                    mid = (lo + hi + 1) // 2
                    if keys[mid] <= month_end:
                        lo = mid
                    else:
                        hi = mid - 1
                d, t, tt, pdf = days[keys[lo]]
                months.append(["%04d-%02d" % (y, m), month_end, d, t, tt, pdf])
            m -= 1
            if m == 0:
                y, m = y - 1, 12

        out["currencies"][ccy] = {
            "first": first,
            "last": last,
            "latest": [list(days[k]) for k in keys[-7:][::-1]],
            "months": months,                      # newest first
        }

    # drop currencies SBI has stopped quoting (no rate in the last 90 days)
    if out["currencies"]:
        newest = max(v["last"] for v in out["currencies"].values())
        cutoff = (datetime.strptime(newest, "%Y-%m-%d") - timedelta(days=90)).strftime("%Y-%m-%d")
        out["currencies"] = {c: v for c, v in out["currencies"].items() if v["last"] >= cutoff}

    os.makedirs(os.path.dirname(SUMMARY_PATH), exist_ok=True)
    tmp = SUMMARY_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    os.replace(tmp, SUMMARY_PATH)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", help="process this PDF instead of downloading from SBI")
    args = ap.parse_args()

    try:
        if args.file:
            with open(args.file, "rb") as f:
                pdf_bytes = f.read()
        else:
            pdf_bytes = download()
        published, rates = parse(pdf_bytes)
        usd = rates["USD"][0]
        log(f"SBI card of {published:%d.%m.%Y %H:%M} - {len(rates)} currencies, USD TT buy {usd}")
        if save(published, rates, pdf_bytes):
            build_summary()
            log("New card saved.")
        else:
            log("This card is already stored - nothing to do.")
            if not os.path.exists(SUMMARY_PATH):
                build_summary()
        return 0
    except Exception as e:
        log(f"FAILED: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
