"""
US Listings & Crypto Products — track US stock IPOs / direct listings / SPACs
and maintain a comprehensive list of crypto-related US-listed products.

Data sources:
  - NASDAQ IPO Calendar API (upcoming + recently priced IPOs)
  - SEC EDGAR S-1 / F-1 filings (pre-IPO registration statements)
  - SEC company_tickers.json (daily snapshot, diff for new listings)
  - yfinance (IPO dates, company info, market cap, AUM for ETFs)

Two main tables:
  - new_listings    — incremental: new stock listings (IPO, direct, SPAC)
  - crypto_products — full refresh: all crypto ETFs, ETPs, and crypto stocks
"""
