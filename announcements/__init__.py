"""
Company Announcements — multi-market filing & announcement pipeline.

Covers:
  - US: SEC EDGAR (8-K, 10-K, 10-Q filings + EX-99 exhibits)
  - HK: HKEXnews PDF announcements
  - CN: CNINFO A-share company announcements

Data sources:
  - SEC EDGAR submissions API + filing detail pages
  - HKEXnews title search / PDF download
  - CNINFO announcement query API + PDF download

Usage:
  python -m announcements.pipeline --init
  python -m announcements.pipeline
  python -m uvicorn announcements.api:app --host 127.0.0.1 --port 8005
  python -m announcements.scheduler
"""
