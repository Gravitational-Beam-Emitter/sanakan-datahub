"""Backfill historical daily prices using yfinance bulk download.

yfinance supports batch download for KOSPI (.KS) and KOSDAQ (.KQ).
KONEX stocks are skipped (not available on yfinance).
"""
import duckdb
import time
import pandas as pd
import yfinance as yf

DB_PATH = "/Users/a80460/Desktop/cibo datahub/kr_stock/kr_stock.duckdb"
PERIOD = "60d"
BATCH_SIZE = 100


def main():
    conn = duckdb.connect(DB_PATH)

    stocks = conn.execute("""
        SELECT code, market FROM kr_listed_stocks
        WHERE is_active = true
        ORDER BY market_cap DESC
    """).fetchall()

    # Build ticker list with correct yfinance suffixes
    ticker_map = {}
    konex_codes = []
    for code, market in stocks:
        if market in ("KONEX",):
            konex_codes.append(code)
            continue
        suffix = ".KQ" if market in ("KOSDAQ", "KOSDAQ GLOBAL") else ".KS"
        ticker_map[f"{code}{suffix}"] = code

    all_tickers = list(ticker_map.keys())
    print(f"Fetching {PERIOD} for {len(all_tickers)} stocks (skipped {len(konex_codes)} KONEX)...")

    total_inserted = 0
    for i in range(0, len(all_tickers), BATCH_SIZE):
        batch = all_tickers[i : i + BATCH_SIZE]
        try:
            df = yf.download(batch, period=PERIOD, progress=False, auto_adjust=False)
            if df.empty:
                continue

            for ticker in batch:
                code = ticker_map[ticker]
                if ticker not in df["Close"].columns:
                    continue

                for idx, row in df.iterrows():
                    date_str = idx.strftime("%Y-%m-%d")
                    close_v = row["Close"].get(ticker)
                    if pd.isna(close_v) or close_v == 0:
                        continue

                    open_v = row["Open"].get(ticker)
                    high_v = row["High"].get(ticker)
                    low_v = row["Low"].get(ticker)
                    volume_v = row["Volume"].get(ticker)

                    conn.execute("""
                        INSERT OR REPLACE INTO kr_daily_prices (date, code, open, high, low, close, volume)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, [
                        date_str, code,
                        float(open_v) if not pd.isna(open_v) else None,
                        float(high_v) if not pd.isna(high_v) else None,
                        float(low_v) if not pd.isna(low_v) else None,
                        float(close_v),
                        int(volume_v) if not pd.isna(volume_v) else None,
                    ])

                total_inserted += 1
        except Exception as e:
            print(f"  Batch error at {i}: {e}")
            time.sleep(2)

        if (i // BATCH_SIZE) % 10 == 0:
            pct = min(i + BATCH_SIZE, len(all_tickers))
            print(f"  {pct}/{len(all_tickers)}")
            conn.commit()

        time.sleep(0.5)

    # Compute change_pct
    print("Computing change_pct...")
    conn.execute("""
        UPDATE kr_daily_prices
        SET change_pct = (close - prev_close) / prev_close * 100
        FROM (
            SELECT code, date, close,
                LAG(close) OVER (PARTITION BY code ORDER BY date) AS prev_close
            FROM kr_daily_prices
        ) sub
        WHERE kr_daily_prices.code = sub.code
          AND kr_daily_prices.date = sub.date
          AND kr_daily_prices.change_pct IS NULL
          AND sub.prev_close IS NOT NULL
          AND sub.prev_close != 0
    """)

    conn.close()

    # Report
    conn2 = duckdb.connect(DB_PATH, read_only=True)
    days = conn2.execute("SELECT COUNT(DISTINCT date) FROM kr_daily_prices").fetchone()[0]
    stocks = conn2.execute("SELECT COUNT(DISTINCT code) FROM kr_daily_prices").fetchone()[0]
    conn2.close()
    print(f"Done. {days} trading days, {stocks} stocks.")


if __name__ == "__main__":
    main()
