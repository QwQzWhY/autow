"""Can a plain HTTP client with a Chrome TLS fingerprint get past the LZU WAF?

If yes, the crawler can run anywhere (including a phone) with no browser.
If no, a real browser engine is mandatory.
"""

from curl_cffi import requests

LIST = "https://ai.lzu.edu.cn/news?module=ACA011ZZHQ&categoryCode=ACA011ZZHQ"
HOME = "https://ai.lzu.edu.cn/"
MARKER = "通知公告"


def report(label: str, r) -> None:
    text = r.text or ""
    print(
        f"{label:<28} status={r.status_code} bytes={len(text)} "
        f"marker={MARKER in text} waf={'$_ts' in text}"
    )


print("== 1. single GET, impersonating chrome ==")
try:
    report("bare get", requests.get(LIST, impersonate="chrome", timeout=30))
except Exception as e:
    print("bare get failed:", type(e).__name__, e)

print("\n== 2. session: home page first, then list page ==")
try:
    s = requests.Session(impersonate="chrome")
    report("warm-up home", s.get(HOME, timeout=30))
    report("then list", s.get(LIST, timeout=30))
    print("cookies:", list(s.cookies.keys()))
except Exception as e:
    print("session failed:", type(e).__name__, e)
