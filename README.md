# SBI TT Buying Rates - Somu & Associates

Daily archive of State Bank of India forex card rates, used by the
**SBI TT Buying Rate Finder** on [casomu.com](https://casomu.com/learning/sbi-tt-buying-rate-finder/).

Rule 206 of the Income-tax Rules, 2026 (earlier Rule 115) and Rule 207 (earlier
Rule 26) convert foreign currency amounts at SBI's telegraphic transfer (TT)
buying rate. SBI publishes only the current day's card, so this repository
keeps every card.

## How it works

- `.github/workflows/fetch-rates.yml` runs every hour from 9:30 AM to 5:30 PM IST,
  Monday to Saturday.
- `fetch_rates.py` downloads SBI's card, reads the reference-rate table
  (transactions between Rs. 10 and 20 lakhs), checks the figures, and saves:
  - `rates/<CCY>.csv` - one file per currency, one row per SBI card
  - `pdf_files/YYYY/MM/YYYY-MM-DD_HHMM.pdf` - the original SBI card
  - `site/summary.json` - month-end rate of every month and the latest 7 rates
    per currency, read by the casomu.com page twice a day
- If SBI has not issued a new card since the last run, nothing is committed.

## CSV format

`DATE,TIME,TT BUY,TT SELL,BILL BUY,BILL SELL,CARD BUY,CARD SELL,CN BUY,CN SELL,PDF`

Dates are YYYY-MM-DD, times are IST as printed on the card. A rate of 0 means
SBI did not quote it. JPY, THB and KRW are quoted per 100 units.

## History

Rates from January 2020 up to the date of import were imported once with
`import_history.py`. The source and its licence are recorded in `NOTICE`,
which must be kept in this repository.

## Running by hand

```
pip install -r requirements.txt
python fetch_rates.py                  # fetch today's card from SBI
python fetch_rates.py --file card.pdf  # process a saved SBI PDF
```

Rates are indicative; always check against the linked SBI card before filing.
