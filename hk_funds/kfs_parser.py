"""
KFS (Key Facts Statement) PDF parser for HK SFC-authorized funds.

Extracts fund manager and portfolio manager names from SFC-mandated
KFS PDFs. KFS follows a standardized template, making extraction
more reliable than parsing free-form Fact Sheets.

Key sections parsed:
  - Fund name
  - Fund manager / Investment manager
  - Portfolio manager(s) / Sub-manager(s)
  - Investment objective (brief)
  - Ongoing charges
"""

from __future__ import annotations

import logging
import re
from io import BytesIO
from typing import Any, Dict, List, Optional

logger = logging.getLogger("hk_funds.kfs_parser")

# Section headers commonly found in KFS PDFs (bilingual EN/ZH)
SECTION_MARKERS = [
    # Management section markers
    "Who is managing",
    "Management Company",
    "Fund Manager",
    "Investment Manager",
    "Portfolio Manager",
    "Sub-Manager",
    "Investment Adviser",
    "Manager",
    # Chinese variants
    "管理公司",
    "基金管理人",
    "投資經理",
    "基金經理",
    "副經理",
    "投資顧問",
    "谁管理",
    # Fee section markers
    "Ongoing charges",
    "Management Fee",
    "持續費用",
    "管理費",
]

# Lines that clearly are NOT manager names
SKIP_LINES = re.compile(
    r'^\s*(page|www\.|http[s]?://|tel[:]?|fax[:]?|email[:]?|'
    r'©|all rights reserved|key facts statement|'
    r'[0-9]+%|company registration|incorporated|'
    r'sfc authorization|授權|證監會|'
    r'ongoing charges|fees and charges|收費|費用|'
    r'•|||o\s{2,})\s*$',
    re.IGNORECASE
)


def parse_kfs_pdf(pdf_content: bytes) -> Optional[Dict[str, Any]]:
    """Parse a KFS PDF and extract fund/manager info.

    Args:
        pdf_content: Raw PDF bytes.

    Returns:
        {
            fund_name: str,
            fund_manager_name_en: str,
            portfolio_manager_name: str,
            investment_objective: str,
            ongoing_charges_pct: float,
            kfs_date: str,
            raw_sections: {section_header: [lines]},
        }
        or None if parsing fails completely.
    """
    try:
        from PyPDF2 import PdfReader
    except ImportError:
        logger.error("PyPDF2 not installed. pip install PyPDF2")
        return None

    try:
        reader = PdfReader(BytesIO(pdf_content))
    except Exception as e:
        logger.error(f"Failed to open PDF: {e}")
        return None

    # Extract all text pages
    full_text = []
    for page in reader.pages:
        try:
            text = page.extract_text()
            if text:
                full_text.append(text)
        except Exception:
            continue

    if not full_text:
        logger.warning("No text extracted from KFS PDF")
        return None

    combined = "\n".join(full_text)
    lines = [l.strip() for l in combined.split("\n") if l.strip()]

    # Parse sections
    sections = _parse_sections(lines)

    # Extract fund name (typically first meaningful line or after "Product Key Facts")
    fund_name = ""
    for i, line in enumerate(lines[:20]):
        line_clean = line.strip()
        if line_clean and len(line_clean) > 10 and not line_clean.startswith("http"):
            # Skip obvious non-name lines
            if re.match(r'^\d+$|^page|^www\.|^key facts|^product key|^重要資料|^產品資料概要', line_clean, re.IGNORECASE):
                continue
            fund_name = line_clean[:200]
            break

    # Extract portfolio manager from relevant sections
    portfolio_manager = _extract_portfolio_manager(sections, lines)

    # Extract fund manager
    fund_manager = _extract_fund_manager(sections, lines)

    # Extract ongoing charges
    ongoing_charges = _extract_ongoing_charges(lines)

    # Extract investment objective (brief)
    investment_objective = _extract_investment_objective(sections, lines)

    return {
        "fund_name": fund_name,
        "fund_manager_name_en": fund_manager or "",
        "portfolio_manager_name": portfolio_manager or "",
        "investment_objective": investment_objective or "",
        "ongoing_charges_pct": ongoing_charges,
        "kfs_date": "",
        "page_count": len(reader.pages),
    }


def _parse_sections(lines: List[str]) -> Dict[str, List[str]]:
    """Parse the KFS text into rough sections based on header markers.

    KFS sections are typically bold/larger text followed by detail lines.
    """
    sections: Dict[str, List[str]] = {}
    current_section = "_preamble"
    sections[current_section] = []

    for line in lines:
        # Check if this line is a section header
        is_header = False
        for marker in SECTION_MARKERS:
            if marker.lower() in line.lower() and len(line) < 100:
                is_header = True
                current_section = marker
                if current_section not in sections:
                    sections[current_section] = []
                sections[current_section].append(line)
                break

        if not is_header:
            sections[current_section].append(line)

    return sections


def _extract_portfolio_manager(
    sections: Dict[str, List[str]], lines: List[str]
) -> str:
    """Extract portfolio manager name from KFS.

    Looks for:
      - "Portfolio Manager" section
      - "Sub-Manager" section
      - Lines after "Investment Manager" mentioning a different entity
    """
    candidates = []

    # Method 1: Look for explicit portfolio manager markers
    for marker in ["Portfolio Manager", "Sub-Manager", "基金經理", "副經理"]:
        if marker in sections:
            for line in sections[marker][1:10]:  # First few lines after header
                line = line.strip()
                if not line or len(line) < 5:
                    continue
                if SKIP_LINES.match(line):
                    continue
                if any(kw in line.lower() for kw in [
                    "portfolio manager", "investment manager",
                    "fund manager", "management company",
                ]):
                    continue
                name = _clean_company_name(line)
                if name and len(name) > 3:
                    candidates.append(name)
                    break

    # Method 2: Look for "managed by" patterns
    managed_patterns = [
        r'(?:managed|sub-managed|advised)\s+by\s+([A-Z][A-Za-z\s&.,()]+?)(?:\.|,|\n|and|\.$)',
        r'(?:portfolio\s+manager|investment\s+manager)[:\s]+([A-Z][A-Za-z\s&.,()]+?)(?:\.|,|\n|$)',
    ]
    for pattern in managed_patterns:
        text = "\n".join(lines[:200])  # Search first 200 lines
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            name = _clean_company_name(match.group(1).strip())
            if name and len(name) > 3:
                candidates.append(name)
                break

    return candidates[0] if candidates else ""


def _extract_fund_manager(
    sections: Dict[str, List[str]], lines: List[str]
) -> str:
    """Extract fund manager from KFS.

    Looks for management company name in relevant sections.
    """
    for marker in [
        "Management Company", "Fund Manager", "Investment Manager",
        "Manager", "管理公司", "基金管理人", "投資經理",
    ]:
        if marker in sections:
            for line in sections[marker][1:15]:
                line = line.strip()
                if not line or len(line) < 5:
                    continue
                if SKIP_LINES.match(line):
                    continue
                if any(kw in line.lower() for kw in [
                    "management company", "fund manager",
                    "investment manager", "sfc", "registered",
                ]):
                    continue
                name = _clean_company_name(line)
                if name and len(name) > 3:
                    return name

    return ""


def _extract_ongoing_charges(lines: List[str]) -> Optional[float]:
    """Extract ongoing charges figure from KFS."""
    patterns = [
        r'(?:ongoing\s+charges|持續費用|management\s+fee)[^\d]*?(\d+\.?\d*)\s*%',
        r'(?:total\s+expense\s+ratio|ter)[^\d]*?(\d+\.?\d*)\s*%',
    ]
    text = "\n".join(lines[:100])
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                pass
    return None


def _extract_investment_objective(
    sections: Dict[str, List[str]], lines: List[str]
) -> str:
    """Extract a brief investment objective from KFS."""
    for marker in [
        "Investment Objective", "What does it invest",
        "投資目標", "投資於",
    ]:
        if marker in sections:
            for line in sections[marker][1:5]:
                line = line.strip()
                if line and len(line) > 20 and not line.startswith("http"):
                    return line[:500]

    return ""


def _clean_company_name(text: str) -> str:
    """Clean up extracted company name."""
    text = re.sub(r'\s+', ' ', text).strip()
    # Remove trailing punctuation but preserve common abbreviations
    # (e.g., "S.A.", "Ltd.", "Co.," are valid parts of company names)
    text = re.sub(r'[,:]+$', '', text)  # Only strip comma/colon, not period
    # Remove common suffixes that aren't part of the name
    text = re.sub(
        r'\s+is the (fund manager|investment manager|manager|sub-manager|'
        r'portfolio manager|management company)\.?$',
        '', text, flags=re.IGNORECASE
    )
    return text.strip()


def extract_kfs_url_from_connector(base_url: str, fund_name: str = "") -> Optional[str]:
    """Try to discover KFS PDF URL from a manager's fund page.

    This is a heuristic method — many managers have predictable URL patterns
    for their KFS documents.
    """
    # Common patterns for KFS URLs
    # Most managers host KFS PDFs at predictable paths
    patterns = [
        f"{base_url.rstrip('/')}/documents/kfs",
        f"{base_url.rstrip('/')}/literature/kfs",
        f"{base_url.rstrip('/')}/fund-documents",
        f"{base_url.rstrip('/')}/en/documents",
    ]
    # This requires HTTP fetch to confirm; return None for now
    # Individual connectors can override with site-specific logic
    return None
