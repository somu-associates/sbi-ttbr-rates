"""
One-time import of the 2020 onwards SBI rate archive from the open-source
SBI FX RateKeeper project by Sahil Gupta (MIT licence):
https://github.com/sahilgupta/sbi-fx-ratekeeper

Run once by the "Import history (one-time)" workflow. It expects a clone of
that repository in the folder given as the first argument, and converts its
data into this repository's format:

  - each archived SBI PDF is read with our own parser and copied into
    pdf_files/, named by the date and time printed on the card
  - every rate row is copied into rates/<CCY>.csv; a row is linked to a PDF
    when its USD rates are identical to that card's, and then takes the
    card's printed time (the archive recorded some times without minutes)

Rows already present in rates/ are kept, so it is safe to run more than once.
"""

import csv
import glob
import os
import re
import shutil
import sys
from datetime import date

import fetch_rates as fr

COLUMN_MAP = {
    "TT BUY": "TT BUY", "TT SELL": "TT SELL",
    "BILL BUY": "BILL BUY", "BILL SELL": "BILL SELL",
    "FOREX TRAVEL CARD BUY": "CARD BUY", "FOREX TRAVEL CARD SELL": "CARD SELL",
    "CN BUY": "CN BUY", "CN SELL": "CN SELL",
}
SRC_COLS = list(COLUMN_MAP)


def num(x):
    try:
        return round(float(x or 0), 4)
    except ValueError:
        return 0.0


def main(src):
    if not os.path.isdir(os.path.join(src, "csv_files")):
        print(f"No csv_files folder in {src}")
        return 1

    # 1. Read every archived PDF with our own parser and copy it in
    cards = {}                      # date string -> list of (time, rel path, USD rates)
    copied = unreadable = 0
    for path in sorted(glob.glob(os.path.join(src, "pdf_files", "**", "*.pdf"), recursive=True)):
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})\.pdf$", path)
        hint = date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None
        with open(path, "rb") as f:
            data = f.read()
        try:
            published, rates = fr.parse(data, expect_date=hint)
        except Exception:
            unreadable += 1
            continue
        rel = fr.pdf_relpath(published)
        dest = os.path.join(fr.ROOT, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if not os.path.exists(dest):
            shutil.copyfile(path, dest)
            copied += 1
        usd = tuple(round(x, 4) for x in rates.get("USD", []))
        cards.setdefault(published.strftime("%Y-%m-%d"), []).append((published.strftime("%H:%M"), rel, usd))
    print(f"PDFs: {copied} copied, {unreadable} unreadable (skipped)")

    # 2. Work out, from the USD file, which archive row belongs to which card
    remap = {}                      # (date, archive time) -> (card time, rel path)
    with open(os.path.join(src, "csv_files", "SBI_REFERENCE_RATES_USD.csv"), newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            stamp = (row.get("DATE") or "").strip()
            if len(stamp) < 16:
                continue
            d, t = stamp[:10], stamp[11:16]
            usd = tuple(num(row.get(c)) for c in SRC_COLS)
            for card_time, rel, card_usd in cards.get(d, []):
                if card_usd == usd:
                    remap[(d, t)] = (card_time, rel)
                    break

    # 3. Convert every currency file
    os.makedirs(fr.RATES_DIR, exist_ok=True)
    total = linked = 0
    for path in sorted(glob.glob(os.path.join(src, "csv_files", "SBI_REFERENCE_RATES_*.csv"))):
        ccy = os.path.basename(path)[len("SBI_REFERENCE_RATES_"):-len(".csv")]
        out_path = os.path.join(fr.RATES_DIR, f"{ccy}.csv")
        rows = {(r["DATE"], r["TIME"]): r for r in fr.read_csv(out_path)}

        with open(path, newline="", encoding="utf-8") as f:
            for src_row in csv.DictReader(f):
                stamp = (src_row.get("DATE") or "").strip()
                if len(stamp) < 16:
                    continue
                d, t = stamp[:10], stamp[11:16]
                card_time, rel = remap.get((d, t), (t, ""))
                key = (d, card_time)
                if key in rows:
                    continue
                row = {"DATE": d, "TIME": card_time, "PDF": rel}
                for src_col, col in COLUMN_MAP.items():
                    row[col] = fr.fmt_rate(num(src_row.get(src_col)))
                rows[key] = row
                total += 1
                linked += bool(rel)

        fr.write_csv(out_path, list(rows.values()))
    print(f"Rate rows imported: {total} across all currencies ({linked} linked to an SBI PDF)")
    fr.build_summary()
    print("Website summary written to site/summary.json")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "upstream"))
