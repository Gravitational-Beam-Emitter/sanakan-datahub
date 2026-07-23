"""
Playwright-based community scrapers for KOL discovery.

Platforms:
  - Twitter/X  — search for $CASHTAG tweets
  - Weibo      — search for stock names/codes
  - Seeking Alpha — latest news/analysis headlines
  - Moomoo     — stock discussion community
  - WeChat     — Official Account articles via Sogou WeChat Search

All functions return lists of post dicts + KOL candidate dicts.
"""

from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from kol_thermometer.config import (
    PLAYWRIGHT_HEADLESS,
    PLAYWRIGHT_TIMEOUT,
    TWITTER_SEARCH_QUERIES,
    TWITTER_TWEETS_PER_QUERY,
    WEIBO_SEARCH_QUERIES,
    WEIBO_POSTS_PER_QUERY,
    SEEKINGALPHA_NEWS_LIMIT,
    MOOMOO_SYMBOLS,
    MOOMOO_POSTS_PER_SYMBOL,
    WECHAT_KOLS,
    WECHAT_ARTICLES_PER_KOL,
    WECHAT_RATE_LIMIT,
)

logger = logging.getLogger("kol_thermometer.scraper")

_has_playwright: bool | None = None


def _check_playwright() -> bool:
    """Check if playwright + stealth are installed and browser is available."""
    global _has_playwright
    if _has_playwright is not None:
        return _has_playwright
    try:
        from playwright.sync_api import sync_playwright
        _has_playwright = True
    except ImportError:
        logger.warning("playwright not installed. pip install playwright && python3 -m playwright install chromium")
        _has_playwright = False
    return _has_playwright


def get_browser():
    """Get a Playwright browser instance with stealth. Returns (playwright, browser) or (None, None)."""
    if not _check_playwright():
        return None, None
    try:
        from playwright.sync_api import sync_playwright
        from playwright_stealth import StealthConfig, stealth_sync
    except ImportError:
        return None, None

    pw = sync_playwright().start()
    browser = pw.chromium.launch(
        headless=PLAYWRIGHT_HEADLESS,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    )
    return pw, browser


def _new_page(browser, timeout: int = PLAYWRIGHT_TIMEOUT):
    """Create a stealth-patched page."""
    from playwright_stealth import StealthConfig, stealth_sync

    ctx = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 800},
        locale="en-US",
    )
    page = ctx.new_page()
    try:
        stealth_sync(page, StealthConfig())
    except Exception:
        pass
    page.set_default_timeout(timeout)
    return page, ctx


# ═══════════════════════════════════════════════════════════════
# Twitter/X Scraper
# ═══════════════════════════════════════════════════════════════

def scrape_twitter_search(
    query: str,
    limit: int = TWITTER_TWEETS_PER_QUERY,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Search Twitter/X for a query (e.g. '$TSLA') and return posts + KOL candidates.

    Uses the x.com search page. Extracts tweets, usernames, follower counts.
    """
    pw, browser = get_browser()
    if browser is None:
        return [], []

    posts = []
    kols = {}

    try:
        page, ctx = _new_page(browser)

        url = f"https://x.com/search?q={query}&src=typed_query&f=live"
        logger.info(f"Twitter scraping: {query}")
        page.goto(url, wait_until="domcontentloaded")
        time.sleep(random.uniform(2, 4))

        # Scroll to load more tweets
        for _ in range(min(limit // 10, 5)):
            page.keyboard.press("End")
            time.sleep(random.uniform(1, 2))

        # Extract tweet articles
        articles = page.query_selector_all("article[data-testid='tweet']")

        for article in articles[:limit]:
            try:
                # Tweet text
                text_el = article.query_selector("div[data-testid='tweetText']")
                text = text_el.inner_text() if text_el else ""

                # User info
                user_link = article.query_selector("a[role='link']")
                username = ""
                if user_link:
                    href = user_link.get_attribute("href") or ""
                    username = href.strip("/")

                display_name_el = article.query_selector("div[data-testid='User-Name']")
                display_name = ""
                if display_name_el:
                    spans = display_name_el.query_selector_all("span")
                    if spans:
                        display_name = spans[0].inner_text()

                # Time
                time_el = article.query_selector("time")
                posted_at = ""
                if time_el:
                    posted_at = time_el.get_attribute("datetime") or ""

                # Stats
                likes = 0
                replies = 0
                retweets = 0
                stats_els = article.query_selector_all("button[data-testid]")
                for el in stats_els:
                    aria = el.get_attribute("aria-label") or ""
                    text_inner = el.inner_text() or ""
                    num = 0
                    try:
                        num = int(text_inner.replace(",", "").replace("K", "000").replace("M", "000000"))
                    except ValueError:
                        num = 0

                    testid = el.get_attribute("data-testid") or ""
                    if "like" in testid.lower() or "favorite" in testid.lower():
                        likes = num
                    elif "reply" in testid.lower():
                        replies = num
                    elif "retweet" in testid.lower():
                        retweets = num

                tweet_id = article.get_attribute("aria-labelledby") or ""
                post_url = f"https://x.com/{username}/status/{tweet_id}" if username and tweet_id else ""

                if text:
                    posts.append({
                        "platform": "twitter",
                        "post_id": tweet_id,
                        "post_url": post_url,
                        "title": text[:200],
                        "content": text,
                        "posted_at": posted_at.replace("T", " ").replace("Z", "") if posted_at else "",
                        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                        "likes": likes,
                        "comments": replies,
                        "shares": retweets,
                        "views": 0,
                        "kol_id": None,
                    })

                if username and username not in kols:
                    followers = 0
                    try:
                        follower_el = article.query_selector("a[href*='/verified_followers']")
                        if not follower_el:
                            follower_el = article.query_selector("span:has-text('Followers')")
                        if follower_el:
                            ft = follower_el.inner_text()
                            import re
                            m = re.search(r"([\d,.]+[KMB]?)\s*Follower", ft)
                            if m:
                                num_str = m.group(1).replace(",", "")
                                if "K" in num_str:
                                    followers = int(float(num_str.replace("K", "")) * 1000)
                                elif "M" in num_str:
                                    followers = int(float(num_str.replace("M", "")) * 1000000)
                                else:
                                    followers = int(num_str)
                    except Exception:
                        followers = 0

                    kols[username] = {
                        "platform": "twitter",
                        "username": username,
                        "display_name": display_name,
                        "profile_url": f"https://x.com/{username}",
                        "followers": max(followers, 100),
                        "account_age_days": 0,
                        "post_count": 1,
                        "total_likes": likes,
                        "total_comments": replies,
                    }
                elif username in kols:
                    kols[username]["post_count"] += 1
                    kols[username]["total_likes"] += likes
                    kols[username]["total_comments"] += replies

            except Exception as e:
                logger.debug(f"Twitter tweet parse error: {e}")
                continue

        ctx.close()
        logger.info(f"Twitter {query}: {len(posts)} tweets, {len(kols)} users")

    except Exception as e:
        logger.error(f"Twitter scrape failed for '{query}': {e}")
    finally:
        browser.close()
        pw.stop()

    return posts, list(kols.values())


# ═══════════════════════════════════════════════════════════════
# Weibo Scraper
# ═══════════════════════════════════════════════════════════════

def scrape_weibo_search(
    query: str,
    limit: int = WEIBO_POSTS_PER_QUERY,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Search Weibo for stock-related content and return posts + KOL candidates."""
    pw, browser = get_browser()
    if browser is None:
        return [], []

    posts = []
    kols = {}

    try:
        page, ctx = _new_page(browser)

        url = f"https://s.weibo.com/weibo?q={query}&typeall=1&suball=1&timescope=custom:1d&Refer=g"
        logger.info(f"Weibo scraping: {query}")
        page.goto(url, wait_until="domcontentloaded")
        time.sleep(random.uniform(2, 4))

        # Scroll
        for _ in range(min(limit // 5, 4)):
            page.keyboard.press("End")
            time.sleep(random.uniform(1, 2))

        cards = page.query_selector_all("div.card-wrap")

        for card in cards[:limit]:
            try:
                # Content
                content_el = card.query_selector("p.txt")
                text = content_el.inner_text() if content_el else ""
                text = text.strip().replace("\n", " ")

                # Author
                name_el = card.query_selector("a.name")
                username = name_el.inner_text() if name_el else ""
                profile_url = name_el.get_attribute("href") if name_el else ""

                # Time
                time_el = card.query_selector("a[date]") or card.query_selector("span.from a")
                posted_at = time_el.inner_text() if time_el else ""

                # Stats
                acts = card.query_selector_all("a[action-type]")
                reposts = likes = comments = 0
                for a in acts:
                    inner = (a.inner_text() or "").strip()
                    action = a.get_attribute("action-type") or ""
                    try:
                        val = int(inner) if inner.isdigit() else 0
                    except ValueError:
                        val = sum(1 for c in inner if c.isdigit())

                    if "fl_forward" in action or "repost" in action.lower():
                        reposts = val
                    elif "fl_comment" in action or "comment" in action.lower():
                        comments = val
                    elif "fl_like" in action or "like" in action.lower():
                        likes = val

                if text:
                    posts.append({
                        "platform": "weibo",
                        "post_id": username + "_" + str(hash(text))[:16],
                        "post_url": profile_url,
                        "title": text[:200],
                        "content": text,
                        "posted_at": posted_at,
                        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                        "likes": likes,
                        "comments": comments,
                        "shares": reposts,
                        "views": 0,
                        "kol_id": None,
                    })

                if username and username not in kols:
                    kols[username] = {
                        "platform": "weibo",
                        "username": username,
                        "display_name": username,
                        "profile_url": profile_url or f"https://weibo.com/{username}",
                        "followers": 1000,
                        "account_age_days": 0,
                        "post_count": 1,
                        "total_likes": likes,
                        "total_comments": comments,
                    }
                elif username in kols:
                    kols[username]["post_count"] += 1
                    kols[username]["total_likes"] += likes
                    kols[username]["total_comments"] += comments

            except Exception as e:
                logger.debug(f"Weibo card parse error: {e}")
                continue

        ctx.close()
        logger.info(f"Weibo '{query}': {len(posts)} posts, {len(kols)} users")

    except Exception as e:
        logger.error(f"Weibo scrape failed for '{query}': {e}")
    finally:
        browser.close()
        pw.stop()

    return posts, list(kols.values())


# ═══════════════════════════════════════════════════════════════
# Seeking Alpha Scraper
# ═══════════════════════════════════════════════════════════════

def scrape_seekingalpha_news(
    limit: int = SEEKINGALPHA_NEWS_LIMIT,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Scrape Seeking Alpha latest news + analysis headlines."""
    pw, browser = get_browser()
    if browser is None:
        return [], []

    posts = []
    kols = {}

    try:
        page, ctx = _new_page(browser)

        logger.info("Seeking Alpha scraping: latest news")
        page.goto("https://seekingalpha.com/market-news", wait_until="domcontentloaded")
        time.sleep(random.uniform(2, 4))

        # Scroll
        for _ in range(3):
            page.keyboard.press("End")
            time.sleep(random.uniform(1, 2))

        # Extract articles
        articles = page.query_selector_all("article, div[data-test-id='post-list-item']")

        for article in articles[:limit]:
            try:
                title_el = article.query_selector("a[data-test-id='post-list-item-title'], h2 a, h3 a")
                title = title_el.inner_text() if title_el else ""
                url = title_el.get_attribute("href") if title_el else ""

                summary_el = article.query_selector("p, div[data-test-id='post-list-item-summary']")
                summary = summary_el.inner_text() if summary_el else ""

                author_el = article.query_selector("span[data-test-id='post-list-item-author'], a[data-test-id='author-name']")
                author = author_el.inner_text() if author_el else ""

                if url and not url.startswith("http"):
                    url = "https://seekingalpha.com" + url

                if title:
                    posts.append({
                        "platform": "seekingalpha",
                        "post_id": url or str(hash(title))[:16],
                        "post_url": url,
                        "title": title[:200],
                        "content": summary or title,
                        "posted_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                        "likes": 0,
                        "comments": 0,
                        "shares": 0,
                        "views": 0,
                        "kol_id": None,
                    })

                if author and author not in kols:
                    kols[author] = {
                        "platform": "seekingalpha",
                        "username": author.lower().replace(" ", "_"),
                        "display_name": author,
                        "profile_url": "",
                        "followers": 5000,
                        "account_age_days": 365,
                        "post_count": 1,
                        "total_likes": 0,
                        "total_comments": 0,
                    }
                elif author in kols:
                    kols[author]["post_count"] += 1

            except Exception as e:
                logger.debug(f"Seeking Alpha article parse error: {e}")
                continue

        ctx.close()
        logger.info(f"Seeking Alpha: {len(posts)} articles, {len(kols)} authors")

    except Exception as e:
        logger.error(f"Seeking Alpha scrape failed: {e}")
    finally:
        browser.close()
        pw.stop()

    return posts, list(kols.values())


# ═══════════════════════════════════════════════════════════════
# Moomoo (富途) Community Scraper
# ═══════════════════════════════════════════════════════════════

def scrape_moomoo_community(
    symbol: str,
    limit: int = MOOMOO_POSTS_PER_SYMBOL,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Scrape Moomoo community stock discussion for a symbol."""
    pw, browser = get_browser()
    if browser is None:
        return [], []

    posts = []
    kols = {}

    try:
        page, ctx = _new_page(browser)

        url = f"https://www.moomoo.com/community/stock/{symbol}"
        logger.info(f"Moomoo scraping: ${symbol}")
        page.goto(url, wait_until="domcontentloaded")
        time.sleep(random.uniform(2, 4))

        # Scroll
        for _ in range(3):
            page.keyboard.press("End")
            time.sleep(random.uniform(1, 2))

        # Extract posts
        post_els = page.query_selector_all("div.post-item, div[class*='post'], div.feed-item, article")

        for el in post_els[:limit]:
            try:
                # Content
                text_el = el.query_selector("div.content, div.text, div[class*='content'], p")
                text = text_el.inner_text() if text_el else ""

                # Author
                user_el = el.query_selector("span.nickname, a.nickname, span[class*='nick'], div.user-name")
                username = user_el.inner_text() if user_el else ""

                # Time
                time_el = el.query_selector("span.time, time, div[class*='time']")
                posted_at = time_el.inner_text() if time_el else ""

                # Likes/comments
                likes_el = el.query_selector("span.like-count, span[class*='like']")
                likes = 0
                if likes_el:
                    try:
                        likes = int((likes_el.inner_text() or "0").replace(",", ""))
                    except ValueError:
                        likes = 0

                comments_el = el.query_selector("span.comment-count, span[class*='comment']")
                comments = 0
                if comments_el:
                    try:
                        comments = int((comments_el.inner_text() or "0").replace(",", ""))
                    except ValueError:
                        comments = 0

                if text:
                    posts.append({
                        "platform": "moomoo",
                        "post_id": str(hash(text))[:16],
                        "post_url": url,
                        "title": text[:200],
                        "content": text,
                        "posted_at": posted_at,
                        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                        "likes": likes,
                        "comments": comments,
                        "shares": 0,
                        "views": 0,
                        "kol_id": None,
                    })

                if username and username not in kols:
                    kols[username] = {
                        "platform": "moomoo",
                        "username": username.lower().replace(" ", "_"),
                        "display_name": username,
                        "profile_url": "",
                        "followers": 500,
                        "account_age_days": 90,
                        "post_count": 1,
                        "total_likes": likes,
                        "total_comments": comments,
                    }
                elif username in kols:
                    kols[username]["post_count"] += 1
                    kols[username]["total_likes"] += likes
                    kols[username]["total_comments"] += comments

            except Exception as e:
                logger.debug(f"Moomoo post parse error: {e}")
                continue

        ctx.close()
        logger.info(f"Moomoo ${symbol}: {len(posts)} posts, {len(kols)} users")

    except Exception as e:
        logger.error(f"Moomoo scrape failed for ${symbol}: {e}")
    finally:
        browser.close()
        pw.stop()

    return posts, list(kols.values())


# ═══════════════════════════════════════════════════════════════
# WeChat Official Accounts Scraper (via Sogou WeChat Search)
# ═══════════════════════════════════════════════════════════════

def scrape_wechat_kol(
    account_name: str,
    limit: int = WECHAT_ARTICLES_PER_KOL,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Scrape recent articles for a WeChat Official Account via Sogou WeChat Search.

    Searches for the account name on weixin.sogou.com and extracts articles
    from the search results that match the target account.

    Returns (posts, kols) where kols has one entry for the account itself.
    """
    pw, browser = get_browser()
    if browser is None:
        return [], []

    posts = []
    kol_data = None

    try:
        page, ctx = _new_page(browser)

        url = f"https://weixin.sogou.com/weixin?type=2&query={account_name}"
        logger.info(f"WeChat scraping: {account_name}")
        page.goto(url, wait_until="domcontentloaded")
        time.sleep(random.uniform(3, 6))

        # Scroll to load results
        for _ in range(2):
            page.keyboard.press("End")
            time.sleep(random.uniform(1, 2))

        # Sogou WeChat article list items
        items = page.query_selector_all("li.news-list-item, div.news-item, li[class*='news'], div.txt-box")

        if not items:
            # Fallback: try more generic selectors
            items = page.query_selector_all("ul.news-list li, div.results div.item, div.weixin-result")

        article_count = 0
        for item in items:
            if article_count >= limit:
                break

            try:
                # Title and URL
                title_el = item.query_selector("h3 a, a.tit, h3.tt a, a[href*='mp.weixin.qq.com']")
                if not title_el:
                    title_el = item.query_selector("a")
                title = (title_el.inner_text() or "").strip() if title_el else ""
                article_url = title_el.get_attribute("href") if title_el else ""

                # Summary
                summary_el = item.query_selector("p.txt-info, p.desc, div.txt-info, p[class*='txt']")
                summary = (summary_el.inner_text() or "").strip() if summary_el else ""

                # Date
                date_el = item.query_selector("span.s2, span.time, span[class*='time'], span[class*='date']")
                posted_at = (date_el.inner_text() or "").strip() if date_el else ""

                # Source account name in result
                account_el = item.query_selector("a.account, span.account, span.s1, a[class*='account']")
                result_account = (account_el.inner_text() or "").strip() if account_el else ""

                # Filter: only keep articles from our target account
                if result_account and account_name not in result_account:
                    continue

                if title and article_url:
                    posts.append({
                        "platform": "wechat",
                        "post_id": article_url.split("/")[-1].rstrip(".html")[:32] if "/" in article_url else str(hash(article_url))[:16],
                        "post_url": article_url,
                        "title": title[:200],
                        "content": summary or title,
                        "posted_at": posted_at,
                        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                        "likes": 0,
                        "comments": 0,
                        "shares": 0,
                        "views": 0,
                        "kol_id": None,
                    })
                    article_count += 1

            except Exception as e:
                logger.debug(f"WeChat article parse error: {e}")
                continue

        # Build KOL candidate entry for this account
        kol_data = {
            "platform": "wechat",
            "username": account_name,
            "display_name": account_name,
            "profile_url": url,
            "followers": 500000,  # conservative estimate for known KOLs
            "account_age_days": 365 * 3,  # most are established accounts
            "post_count": article_count,
            "total_likes": article_count * 5000,  # estimated reads per article
            "total_comments": article_count * 20,
        }

        ctx.close()
        logger.info(f"WeChat '{account_name}': {len(posts)} articles")

    except Exception as e:
        logger.error(f"WeChat scrape failed for '{account_name}': {e}")
    finally:
        browser.close()
        pw.stop()

    return posts, [kol_data] if kol_data else []


# ═══════════════════════════════════════════════════════════════
# Batch scrape helpers
# ═══════════════════════════════════════════════════════════════

def scrape_twitter_all(
    queries: Optional[List[str]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Scrape Twitter for all configured search queries."""
    all_posts = []
    all_kols: Dict[str, Dict[str, Any]] = {}

    for q in (queries or TWITTER_SEARCH_QUERIES):
        posts, kols_list = scrape_twitter_search(q)
        all_posts.extend(posts)
        for k in kols_list:
            uname = k["username"]
            if uname not in all_kols:
                all_kols[uname] = k
            else:
                all_kols[uname]["post_count"] += k["post_count"]
                all_kols[uname]["total_likes"] += k["total_likes"]
        time.sleep(random.uniform(2, 5))

    return all_posts, list(all_kols.values())


def scrape_weibo_all(
    queries: Optional[List[str]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Scrape Weibo for all configured search queries."""
    all_posts = []
    all_kols: Dict[str, Dict[str, Any]] = {}

    for q in (queries or WEIBO_SEARCH_QUERIES):
        posts, kols_list = scrape_weibo_search(q)
        all_posts.extend(posts)
        for k in kols_list:
            uname = k["username"]
            if uname not in all_kols:
                all_kols[uname] = k
            else:
                all_kols[uname]["post_count"] += k["post_count"]
                all_kols[uname]["total_likes"] += k["total_likes"]
        time.sleep(random.uniform(2, 5))

    return all_posts, list(all_kols.values())


def scrape_moomoo_all(
    symbols: Optional[List[str]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Scrape Moomoo for all configured symbols."""
    all_posts = []
    all_kols: Dict[str, Dict[str, Any]] = {}

    for s in (symbols or MOOMOO_SYMBOLS):
        posts, kols_list = scrape_moomoo_community(s)
        all_posts.extend(posts)
        for k in kols_list:
            uname = k["username"]
            if uname not in all_kols:
                all_kols[uname] = k
            else:
                all_kols[uname]["post_count"] += k["post_count"]
                all_kols[uname]["total_likes"] += k["total_likes"]
        time.sleep(random.uniform(1, 3))

    return all_posts, list(all_kols.values())


def scrape_wechat_all(
    kols: Optional[List[Dict[str, str]]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Scrape articles for all configured WeChat KOLs."""
    all_posts = []
    all_kols: Dict[str, Dict[str, Any]] = {}

    for k in (kols or WECHAT_KOLS):
        posts, kols_list = scrape_wechat_kol(k["name"], limit=WECHAT_ARTICLES_PER_KOL)
        all_posts.extend(posts)
        for kc in kols_list:
            uname = kc["username"]
            if uname not in all_kols:
                kc["display_name"] = k["name"]
                all_kols[uname] = kc
            else:
                all_kols[uname]["post_count"] += kc["post_count"]
                all_kols[uname]["total_likes"] += kc["total_likes"]
        time.sleep(random.uniform(3, 8))

    return all_posts, list(all_kols.values())
