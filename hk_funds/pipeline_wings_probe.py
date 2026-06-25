"""
WINGS API endpoint probe.

Usage (paste fresh cookies from browser):
    python3 hk_funds/pipeline_wings_probe.py \
      --session "NWU0MWIwYjktNjhkNy00NmRlLTg4YjItNGU3MzI2ZGZkNmQ4" \
      --xsrf "42ce07ff-b393-4fa1-8b41-df98572e546a" \
      --ts "01ee710898ad9e6e0853f3372cd0c729ae74d1fed14fd536f8c065dcadfd1dc6b60718a5791cc4acd0f3954de5612fd593042c7261" \
      --token-a "eb8ff7a8528b99f84d16e41efb56920d" \
      --token-b "2517128528648d941540423dfa0f12b4" \
      --token-c "38f68415e7e4393addad8b36ada626a3"
"""

import argparse
import requests
import json

BASE = "https://wings.sfc.hk"

PROBES = [
    # REST-style
    ("GET", "/api/licensed-corporations", None),
    ("GET", "/api/v1/licensed-corporations", None),
    ("GET", "/api/public-register/licensed-corporations", None),
    ("GET", "/api/public-register/search", None),
    ("GET", "/api/corporations", None),
    ("GET", "/api/intermediaries/licensed-corporations", None),
    ("GET", "/api/intermediaries/corporations", None),
    ("GET", "/api/v1/intermediaries", None),
    ("GET", "/api/intermediaries", None),
    ("GET", "/api/lc", None),
    ("GET", "/api/lc/search?type=9&status=active&limit=10", None),
    # Spring Data REST
    ("GET", "/api/licensedCorporations", None),
    ("GET", "/api/licensedCorporations/search/findByLicenseType?type=9", None),
    # Old-style POST
    ("POST", "/api/searchByRa", {"licstatus": "active", "roleType": "corporation", "ratype": "9", "start": 0, "limit": 10}),
    ("POST", "/publicregWeb/searchByRaJson", {"licstatus": "active", "roleType": "corporation", "ratype": "9", "start": 0, "limit": 10}),
    # GraphQL
    ("POST", "/api/graphql", {"query": "{ licensedCorporations { id ceNumber companyName } }"}),
    ("POST", "/graphql", {"query": "{ licensedCorporations { id ceNumber companyName } }"}),
    # Possible SPA data endpoints
    ("GET", "/api/data/licensed-corporations", None),
    ("GET", "/api/data/corporations", None),
    ("GET", "/api/registers/licensed-corporations", None),
    ("GET", "/api/registers/intermediaries", None),
    ("GET", "/api/registers/lc", None),
    ("GET", "/api/public/lc", None),
    ("GET", "/api/public/corporations", None),
    # WINGS SPA routing
    ("GET", "/api/findByRaType?raType=9", None),
    ("GET", "/api/licensedCorporations/findByRaType?raType=9", None),
]

PAGE_PROBES = [
    "/main/",
    "/main/home",
    "/main/dashboard",
    "/main/licensed-corporations",
    "/main/intermediaries",
    "/main/public-register",
    "/main/search",
    "/main/registers",
    "/main/en/intermediaries",
    "/main/intermediaries/licensed-corporations",
    "/licensed-corporations",
    "/intermediaries",
    "/public-register",
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", required=True)
    parser.add_argument("--xsrf", required=True)
    parser.add_argument("--ts", default="")
    parser.add_argument("--token-a", default="")
    parser.add_argument("--token-b", default="")
    parser.add_argument("--token-c", default="")
    args = parser.parse_args()

    # Build session with all cookies
    s = requests.Session()
    s.cookies.set("SESSION", args.session, domain="wings.sfc.hk")
    s.cookies.set("__Host-XSRF-TOKEN", args.xsrf, domain="wings.sfc.hk")
    if args.ts:
        s.cookies.set("TS0124a5db", args.ts, domain="wings.sfc.hk")

    # The hash-named cookies (Spring Security context)
    # We need to find their names from the browser
    extra_cookies = {
        "291255c29e7ab2cdf9bf9dcf3ba96d24": args.token_a,
        "6410fdddf84632cbe7b0e2b2f34ff259": args.token_b,
        "e3a0e2c9efdc16c8e6f447068a18e5fd": args.token_c,
    }
    for name, val in extra_cookies.items():
        if val:
            s.cookies.set(name, val, domain="wings.sfc.hk")

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json",
        "X-XSRF-TOKEN": args.xsrf,
        "Origin": "https://wings.sfc.hk",
        "Referer": "https://wings.sfc.hk/main/",
    }

    print("=" * 60)
    print("Probing API endpoints...")
    print("=" * 60)

    hits = []
    for method, url, body in PROBES:
        full_url = f"{BASE}{url}"
        try:
            if method == "GET":
                r = s.get(full_url, headers=headers, timeout=15, allow_redirects=False)
            else:
                h = {**headers, "Content-Type": "application/json"}
                r = s.post(full_url, headers=h, json=body, timeout=15, allow_redirects=False)

            ct = r.headers.get("Content-Type", "")
            length = len(r.text)

            if r.status_code == 200:
                is_json = "json" in ct or (r.text.strip().startswith(("{", "[")))
                if is_json:
                    data = r.json()
                    preview = json.dumps(data, indent=2, ensure_ascii=False)[:300]
                    print(f"  ✅ {method} {url} → JSON ({len(r.text)}B)")
                    print(f"     {preview}")
                    hits.append((method, url, data))
                else:
                    print(f"  ❓ {method} {url} → {r.status_code} HTML ({length}B)")
            elif r.status_code == 403:
                print(f"  🔒 {method} {url} → 403 Forbidden")
            elif r.status_code == 401:
                print(f"  🔑 {method} {url} → 401 Unauthorized")
            elif r.status_code == 404:
                pass  # skip noise
            elif r.status_code in (302, 301):
                print(f"  ↩  {method} {url} → {r.status_code} → {r.headers.get('Location', '')[:80]}")
            else:
                print(f"  ❌ {method} {url} → {r.status_code}")
        except Exception as e:
            print(f"  💥 {method} {url} → {e}")

    # If no API endpoints found, probe pages to find SPA routes
    if not hits:
        print()
        print("=" * 60)
        print("No API hits. Probing HTML pages for SPA routes...")
        print("=" * 60)
        html_headers = {**headers, "Accept": "text/html,application/xhtml+xml"}
        for path in PAGE_PROBES:
            full_url = f"{BASE}{path}"
            try:
                r = s.get(full_url, headers=html_headers, timeout=15, allow_redirects=False)
                if r.status_code == 200:
                    print(f"  ✅ GET {path} → {r.status_code} ({len(r.text)}B)")
                    # Look for JS bundles that hint at API routes
                    import re
                    js_files = re.findall(r'src="([^"]+\.js)"', r.text)
                    api_urls = re.findall(r'["\'](/api/[^"\']+)["\']', r.text)
                    if js_files:
                        print(f"     JS files: {js_files[:5]}")
                    if api_urls:
                        print(f"     API refs: {api_urls[:5]}")
                elif r.status_code in (302, 301):
                    print(f"  ↩  GET {path} → {r.status_code} → {r.headers.get('Location', '')[:80]}")
                elif r.status_code == 404:
                    pass
                else:
                    print(f"  ❌ GET {path} → {r.status_code}")
            except Exception as e:
                print(f"  💥 GET {path} → {e}")

    print()
    if hits:
        print(f"🏆 Found {len(hits)} working endpoints!")
    else:
        print("😞 No API endpoints found. Manual investigation needed.")
        print("   Open WINGS in browser, go to the licensed corp search page,")
        print("   open DevTools → Network tab, and look for XHR/fetch requests.")


if __name__ == "__main__":
    main()
