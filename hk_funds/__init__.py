"""
HK Fund KYP Module — SFC-authorized fund product & manager due diligence.

Covers:
  - SFC authorized unit trusts & mutual funds list
  - Multi-layer classification: ordinary / complex / derivatives / structured
    per SFC product classification rules
  - SFC licensed corporations (fund managers/advisers)
  - Manager-fund linkage + regulatory enforcement cross-referencing
  - HKEX-listed fund products (ETFs, leveraged/inverse products)

Data sources:
  - SFC public register (authorized funds + licensed corporations)
  - SFC e-Distribution enforcement data (via existing name_screening table)
  - HKEX listed securities

Usage:
  python -m hk_funds.pipeline_funds --init
  python -m hk_funds.pipeline_managers --init
  python -m uvicorn hk_funds.api:app --host 127.0.0.1 --port 8004
  python -m hk_funds.scheduler
"""
