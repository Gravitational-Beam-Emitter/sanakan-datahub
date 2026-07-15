"""
hynix — SK Hynix cross-market arbitrage pipeline.

Tracks SK Hynix across markets:
  - KR stock (000660.KS)
  - US ADR (SKHY, Nasdaq, 10:1 ratio)
  - HK leveraged ETP (7709.HK, 2x daily)
  - KR single-stock ETFs (leveraged & unleveraged)

Computes premium/discount for equivalent equity exposure.
"""
