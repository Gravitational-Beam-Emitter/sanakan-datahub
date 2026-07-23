"""
Pipeline — KOL discovery, post fetching, stock mention extraction, rating compute.

Data sources:
  - Reddit: stock subreddits via PRAW
  - YouTube: stock analysis channels via Data API v3
  - Twitter/X, Weibo, Seeking Alpha, Moomoo: Playwright scraping
  - StockTwits, Finnhub: REST APIs
  - WeChat Official Accounts (微信公众号): via Sogou WeChat Search

Usage:
  python -m kol_thermometer.pipeline           # daily fetch
  python -m kol_thermometer.pipeline --init    # backfill (wider scan)
  python -m kol_thermometer.pipeline --source reddit   # single source
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from kol_thermometer.config import (
    STOCK_SUBREDDITS,
    YOUTUBE_SEARCH_QUERIES,
    REDDIT_POSTS_PER_SUB,
    REDDIT_RATE_LIMIT,
    YOUTUBE_RATE_LIMIT,
    YOUTUBE_MAX_RESULTS,
    KOL_MIN_REDDIT_KARMA,
    KOL_MIN_REDDIT_POSTS,
    KOL_MIN_YOUTUBE_SUBS,
    STOCKTWITS_ACCESS_TOKEN,
    STOCKTWITS_SYMBOLS_LIMIT,
    STOCKTWITS_MESSAGES_PER_SYMBOL,
    STOCKTWITS_RATE_LIMIT,
    FINNHUB_API_KEY,
    FINNHUB_NEWS_LIMIT,
    FINNHUB_SENTIMENT_LIMIT,
    FINNHUB_RATE_LIMIT,
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
    THERMOMETER_LOOKBACK_DAYS,
)
from kol_thermometer.storage import (
    init_db,
    upsert_kol,
    upsert_kols_batch,
    upsert_posts_batch,
    upsert_mentions_batch,
    upsert_thermometer,
    get_kols,
    get_posts_without_mentions,
    get_mentions_for_thermometer,
    get_past_heats,
    get_max_followers,
    deactivate_inactive_kols,
    touch_kol_activity,
    log_fetch_start,
    log_fetch_end,
    get_stats,
)
from kol_thermometer.thermometer import (
    compute_kol_score,
    assign_tier,
    compute_kol_weight,
    compute_heat_score,
    compute_momentum,
    apply_tier_decay,
)

logger = logging.getLogger("kol_thermometer.pipeline")

TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ═══════════════════════════════════════════════════════════════
# Reddit Fetcher (PRAW)
# ═══════════════════════════════════════════════════════════════

def _get_reddit_client():
    """Create a PRAW Reddit instance. Returns None if not configured."""
    from kol_thermometer.config import (
        REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT,
    )
    if not REDDIT_CLIENT_ID or not REDDIT_CLIENT_SECRET:
        logger.warning("Reddit API credentials not configured. Set REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET in .env")
        return None
    try:
        import praw
        return praw.Reddit(
            client_id=REDDIT_CLIENT_ID,
            client_secret=REDDIT_CLIENT_SECRET,
            user_agent=REDDIT_USER_AGENT,
        )
    except ImportError:
        logger.warning("praw not installed. pip install praw")
        return None
    except Exception as e:
        logger.error(f"Failed to create Reddit client: {e}")
        return None


def fetch_reddit(conn, subreddits: Optional[List[str]] = None,
                 posts_per_sub: int = REDDIT_POSTS_PER_SUB,
                 is_init: bool = False) -> Dict[str, Any]:
    """Fetch posts from stock subreddits. Auto-discovers KOLs from posters.

    Returns summary dict.
    """
    reddit = _get_reddit_client()
    if reddit is None:
        return {"source": "reddit", "status": "skipped", "reason": "no credentials or praw not installed"}

    subs = subreddits or STOCK_SUBREDDITS
    total_posts = 0
    total_kols = 0
    errors = []

    for sub_name in subs:
        try:
            logger.info(f"Fetching r/{sub_name} ...")
            subreddit = reddit.subreddit(sub_name)
            posts_data = []
            kol_candidates: Dict[str, Dict[str, Any]] = {}

            for post in subreddit.hot(limit=posts_per_sub):
                author = post.author
                if author is None:
                    continue

                author_name = str(author)
                posted_ts = datetime.fromtimestamp(post.created_utc, tz=timezone.utc)
                posted_str = posted_ts.strftime("%Y-%m-%d %H:%M:%S")

                posts_data.append({
                    "platform": "reddit",
                    "post_id": post.id,
                    "post_url": f"https://reddit.com{post.permalink}",
                    "title": post.title,
                    "content": post.selftext or "",
                    "posted_at": posted_str,
                    "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                    "likes": post.score,
                    "comments": post.num_comments,
                    "shares": getattr(post, "num_crossposts", 0) or 0,
                    "views": 0,
                    "kol_id": None,  # filled after KOL upsert
                })

                # Aggregate KOL info from this poster
                if author_name not in kol_candidates:
                    try:
                        author_karma = author.link_karma + author.comment_karma
                        author_age = (datetime.now(timezone.utc).timestamp() - author.created_utc) / 86400.0
                    except Exception:
                        author_karma = 0
                        author_age = 0

                    kol_candidates[author_name] = {
                        "platform": "reddit",
                        "username": author_name,
                        "display_name": getattr(author, "name", author_name),
                        "profile_url": f"https://reddit.com/user/{author_name}",
                        "followers": author_karma,
                        "account_age_days": int(author_age),
                        "post_count": 1,
                        "total_likes": post.score,
                        "total_comments": post.num_comments,
                    }
                else:
                    kc = kol_candidates[author_name]
                    kc["post_count"] += 1
                    kc["total_likes"] += post.score
                    kc["total_comments"] += post.num_comments

            # Upsert posts
            if posts_data:
                count = upsert_posts_batch(conn, posts_data)
                total_posts += count

            # Process KOL candidates
            max_followers = get_max_followers(conn, "reddit")
            for username, kc in kol_candidates.items():
                pc = kc["post_count"]
                if pc < KOL_MIN_REDDIT_POSTS and not is_init:
                    continue
                if kc["followers"] < KOL_MIN_REDDIT_KARMA and not is_init:
                    continue

                posts_per_week = pc / max(is_init and 30 or 7, 1) * 7
                avg_likes = kc["total_likes"] / max(pc, 1)
                avg_comments = kc["total_comments"] / max(pc, 1)

                scores = compute_kol_score(
                    followers=kc["followers"],
                    max_followers=max(max_followers, kc["followers"]),
                    avg_likes=avg_likes,
                    avg_comments=avg_comments,
                    avg_shares=0,
                    posts_per_week=posts_per_week,
                    account_age_days=kc["account_age_days"],
                    stock_mention_ratio=0.5,  # estimated from stock sub context
                )
                tier = assign_tier(scores["total_score"])
                weight = compute_kol_weight(tier, "reddit")

                kc.update({
                    "avg_likes": avg_likes,
                    "avg_comments": avg_comments,
                    "avg_shares": 0,
                    "avg_views": 0,
                    "posts_per_week": round(posts_per_week, 2),
                    "stock_mention_ratio": 0.5,
                    "total_score": scores["total_score"],
                    "score_reach": scores["reach"],
                    "score_engagement": scores["engagement"],
                    "score_consistency": scores["consistency"],
                    "score_relevance": scores["stock_relevance"],
                    "score_impact": scores["impact"],
                    "tier": tier,
                    "base_weight": weight,
                    "first_seen_date": TODAY,
                    "last_active_date": TODAY,
                    "is_active": 1,
                })
                kid = upsert_kol(conn, kc)
                if kid:
                    total_kols += 1
                    # Link posts to this KOL
                    conn.execute(
                        "UPDATE kol_posts SET kol_id = ? WHERE platform = 'reddit' AND kol_id IS NULL",
                        [kid],
                    )

            logger.info(f"  r/{sub_name}: {len(posts_data)} posts, {len(kol_candidates)} users")
            time.sleep(REDDIT_RATE_LIMIT)

        except Exception as e:
            msg = f"r/{sub_name}: {e}"
            logger.error(msg)
            errors.append(msg)

    return {
        "source": "reddit",
        "status": "ok" if not errors else "partial",
        "subreddits_scanned": len(subs),
        "posts_fetched": total_posts,
        "kols_discovered": total_kols,
        "errors": errors,
    }


# ═══════════════════════════════════════════════════════════════
# YouTube Fetcher (Data API v3)
# ═══════════════════════════════════════════════════════════════

def _get_youtube_client():
    """Create a YouTube Data API client. Returns None if not configured."""
    from kol_thermometer.config import YOUTUBE_API_KEY
    if not YOUTUBE_API_KEY:
        logger.warning("YouTube API key not configured. Set YOUTUBE_API_KEY in .env")
        return None
    try:
        from googleapiclient.discovery import build
        return build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
    except ImportError:
        logger.warning("google-api-python-client not installed. pip install google-api-python-client")
        return None
    except Exception as e:
        logger.error(f"Failed to create YouTube client: {e}")
        return None


def fetch_youtube(conn, queries: Optional[List[str]] = None,
                  max_results: int = YOUTUBE_MAX_RESULTS,
                  is_init: bool = False) -> Dict[str, Any]:
    """Search YouTube for stock-related videos. Auto-discovers channels as KOLs.

    Returns summary dict.
    """
    youtube = _get_youtube_client()
    if youtube is None:
        return {"source": "youtube", "status": "skipped", "reason": "no API key or client not installed"}

    search_queries = queries or YOUTUBE_SEARCH_QUERIES
    total_posts = 0
    total_kols = 0
    errors = []

    for query in search_queries:
        try:
            logger.info(f"YouTube search: '{query}'")
            published_after = (datetime.now(timezone.utc) - timedelta(days=THERMOMETER_LOOKBACK_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
            req = youtube.search().list(
                q=query,
                part="snippet",
                maxResults=max_results,
                type="video",
                order="relevance" if not is_init else "date",
                publishedAfter=published_after,
            )
            resp = req.execute()
            posts_data = []
            channel_ids = set()

            for item in resp.get("items", []):
                snippet = item.get("snippet", {})
                video_id = item["id"]["videoId"]
                channel_id = snippet.get("channelId", "")
                channel_title = snippet.get("channelTitle", "")
                channel_ids.add((channel_id, channel_title))

                posts_data.append({
                    "platform": "youtube",
                    "post_id": video_id,
                    "post_url": f"https://youtube.com/watch?v={video_id}",
                    "title": snippet.get("title", ""),
                    "content": snippet.get("description", ""),
                    "posted_at": snippet.get("publishedAt", "").replace("T", " ").replace("Z", ""),
                    "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                    "likes": 0,
                    "comments": 0,
                    "shares": 0,
                    "views": 0,
                    "kol_id": None,
                })

            # Upsert posts
            if posts_data:
                count = upsert_posts_batch(conn, posts_data)
                total_posts += count

            # Fetch channel stats for KOL discovery
            if channel_ids:
                channel_id_list = [cid for cid, _ in channel_ids]
                chunks = [channel_id_list[i:i + 50] for i in range(0, len(channel_id_list), 50)]

                for chunk in chunks:
                    try:
                        ch_resp = youtube.channels().list(
                            id=",".join(chunk),
                            part="statistics,snippet",
                        ).execute()

                        for ch in ch_resp.get("items", []):
                            stats = ch.get("statistics", {})
                            ch_snippet = ch.get("snippet", {})
                            sub_count = int(stats.get("subscriberCount", 0))

                            if sub_count < KOL_MIN_YOUTUBE_SUBS and not is_init:
                                continue

                            ch_id = ch["id"]
                            ch_title = ch_snippet.get("title", "")
                            ch_created = ch_snippet.get("publishedAt", "")
                            try:
                                ch_dt = datetime.fromisoformat(ch_created.replace("Z", "+00:00"))
                                ch_age_days = (datetime.now(timezone.utc) - ch_dt).days
                            except Exception:
                                ch_age_days = 0

                            video_count = int(stats.get("videoCount", 0))
                            view_count = int(stats.get("viewCount", 0))
                            avg_views = view_count / max(video_count, 1)

                            max_followers = get_max_followers(conn, "youtube")
                            posts_per_week = min(video_count / max(ch_age_days / 7, 1), 50)

                            scores = compute_kol_score(
                                followers=sub_count,
                                max_followers=max(max_followers, sub_count),
                                avg_likes=avg_views * 0.03,
                                avg_comments=avg_views * 0.002,
                                avg_shares=0,
                                posts_per_week=posts_per_week,
                                account_age_days=ch_age_days,
                                stock_mention_ratio=0.3 if "stock" in query.lower() or "股" in query else 0.1,
                            )
                            tier = assign_tier(scores["total_score"])
                            weight = compute_kol_weight(tier, "youtube")

                            kol_data = {
                                "platform": "youtube",
                                "username": ch_id,
                                "display_name": ch_title,
                                "profile_url": f"https://youtube.com/channel/{ch_id}",
                                "followers": sub_count,
                                "avg_likes": round(avg_views * 0.03, 1),
                                "avg_comments": round(avg_views * 0.002, 1),
                                "avg_shares": 0,
                                "avg_views": round(avg_views, 1),
                                "posts_per_week": round(posts_per_week, 2),
                                "account_age_days": ch_age_days,
                                "stock_mention_ratio": scores["stock_relevance"] / 15.0,
                                "total_score": scores["total_score"],
                                "score_reach": scores["reach"],
                                "score_engagement": scores["engagement"],
                                "score_consistency": scores["consistency"],
                                "score_relevance": scores["stock_relevance"],
                                "score_impact": scores["impact"],
                                "tier": tier,
                                "base_weight": weight,
                                "first_seen_date": TODAY,
                                "last_active_date": TODAY,
                                "is_active": 1,
                            }
                            kid = upsert_kol(conn, kol_data)
                            if kid:
                                total_kols += 1
                                # Link posts to this KOL
                                conn.execute(
                                    "UPDATE kol_posts SET kol_id = ? WHERE platform = 'youtube' AND kol_id IS NULL",
                                    [kid],
                                )

                        time.sleep(0.3)
                    except Exception as e:
                        logger.error(f"YouTube channel fetch error: {e}")

            logger.info(f"  query='{query}': {len(posts_data)} videos, {len(channel_ids)} channels")
            time.sleep(YOUTUBE_RATE_LIMIT)

        except Exception as e:
            msg = f"query='{query}': {e}"
            logger.error(msg)
            errors.append(msg)

    return {
        "source": "youtube",
        "status": "ok" if not errors else "partial",
        "queries_searched": len(search_queries),
        "posts_fetched": total_posts,
        "kols_discovered": total_kols,
        "errors": errors,
    }


# ═══════════════════════════════════════════════════════════════
# Playwright availability check
# ═══════════════════════════════════════════════════════════════

def _check_playwright_available() -> bool:
    """Check if Playwright scraping is available."""
    from kol_thermometer.scraper import _check_playwright
    return _check_playwright()


# ═══════════════════════════════════════════════════════════════
# Twitter/X Fetcher (Playwright)
# ═══════════════════════════════════════════════════════════════

def fetch_twitter(conn, queries: Optional[List[str]] = None,
                  tweets_per_query: int = TWITTER_TWEETS_PER_QUERY,
                  is_init: bool = False) -> Dict[str, Any]:
    """Scrape Twitter/X search results for $CASHTAG queries via Playwright.

    Auto-discovers KOLs from tweet authors. Posts go through LLM tagging.
    Returns summary dict.
    """
    from kol_thermometer.scraper import scrape_twitter_all

    if not _check_playwright_available():
        return {"source": "twitter", "status": "skipped", "reason": "playwright not installed"}

    search_queries = queries or TWITTER_SEARCH_QUERIES
    total_posts = 0
    total_kols = 0

    try:
        logger.info("Twitter/X: scraping search results via Playwright...")
        all_posts, all_kols = scrape_twitter_all(queries=search_queries)

        if all_posts:
            total_posts = upsert_posts_batch(conn, all_posts)

        max_followers = get_max_followers(conn, "twitter")
        for kc in all_kols:
            if kc["followers"] < 500 and not is_init:
                continue
            pc = kc["post_count"]
            posts_per_week = pc / max(is_init and 30 or 7, 1) * 7
            avg_likes = kc["total_likes"] / max(pc, 1)
            avg_comments = kc["total_comments"] / max(pc, 1)

            scores = compute_kol_score(
                followers=kc["followers"],
                max_followers=max(max_followers, kc["followers"]),
                avg_likes=avg_likes,
                avg_comments=avg_comments,
                avg_shares=0,
                posts_per_week=posts_per_week,
                account_age_days=kc["account_age_days"],
                stock_mention_ratio=0.5,
            )
            tier = assign_tier(scores["total_score"])
            weight = compute_kol_weight(tier, "twitter")

            kc.update({
                "avg_likes": avg_likes,
                "avg_comments": avg_comments,
                "avg_shares": 0,
                "avg_views": 0,
                "posts_per_week": round(posts_per_week, 2),
                "stock_mention_ratio": 0.5,
                "total_score": scores["total_score"],
                "score_reach": scores["reach"],
                "score_engagement": scores["engagement"],
                "score_consistency": scores["consistency"],
                "score_relevance": scores["stock_relevance"],
                "score_impact": scores["impact"],
                "tier": tier,
                "base_weight": weight,
                "first_seen_date": TODAY,
                "last_active_date": TODAY,
                "is_active": 1,
            })
            kid = upsert_kol(conn, kc)
            if kid:
                total_kols += 1
                conn.execute(
                    "UPDATE kol_posts SET kol_id = ? WHERE platform = 'twitter' AND kol_id IS NULL",
                    [kid],
                )

            logger.info(f"Twitter: {total_posts} tweets, {total_kols} KOLs")

    except Exception as e:
        logger.error(f"Twitter fetch failed: {e}")
        return {"source": "twitter", "status": "error", "reason": str(e)}

    return {
        "source": "twitter",
        "status": "ok",
        "queries": len(search_queries),
        "posts_fetched": total_posts,
        "kols_discovered": total_kols,
    }


# ═══════════════════════════════════════════════════════════════
# Weibo Fetcher (Playwright)
# ═══════════════════════════════════════════════════════════════

def fetch_weibo(conn, queries: Optional[List[str]] = None,
                posts_per_query: int = WEIBO_POSTS_PER_QUERY,
                is_init: bool = False) -> Dict[str, Any]:
    """Scrape Weibo search results for stock names via Playwright.

    Auto-discovers KOLs from Weibo users. Posts go through LLM tagging.
    Returns summary dict.
    """
    from kol_thermometer.scraper import scrape_weibo_all

    if not _check_playwright_available():
        return {"source": "weibo", "status": "skipped", "reason": "playwright not installed"}

    search_queries = queries or WEIBO_SEARCH_QUERIES
    total_posts = 0
    total_kols = 0

    try:
        logger.info("Weibo: scraping search results via Playwright...")
        all_posts, all_kols = scrape_weibo_all(queries=search_queries)

        if all_posts:
            total_posts = upsert_posts_batch(conn, all_posts)

        max_followers = get_max_followers(conn, "weibo")
        for kc in all_kols:
            if kc["followers"] < 500 and not is_init:
                continue
            pc = kc["post_count"]
            posts_per_week = pc / max(is_init and 30 or 7, 1) * 7
            avg_likes = kc["total_likes"] / max(pc, 1)

            scores = compute_kol_score(
                followers=kc["followers"],
                max_followers=max(max_followers, kc["followers"]),
                avg_likes=avg_likes,
                avg_comments=kc.get("total_comments", 0) / max(pc, 1),
                avg_shares=0,
                posts_per_week=posts_per_week,
                account_age_days=kc["account_age_days"],
                stock_mention_ratio=0.4,
            )
            tier = assign_tier(scores["total_score"])
            weight = compute_kol_weight(tier, "weibo")

            kc.update({
                "avg_likes": avg_likes,
                "avg_comments": kc.get("total_comments", 0) / max(pc, 1),
                "avg_shares": 0,
                "avg_views": 0,
                "posts_per_week": round(posts_per_week, 2),
                "stock_mention_ratio": 0.4,
                "total_score": scores["total_score"],
                "score_reach": scores["reach"],
                "score_engagement": scores["engagement"],
                "score_consistency": scores["consistency"],
                "score_relevance": scores["stock_relevance"],
                "score_impact": scores["impact"],
                "tier": tier,
                "base_weight": weight,
                "first_seen_date": TODAY,
                "last_active_date": TODAY,
                "is_active": 1,
            })
            kid = upsert_kol(conn, kc)
            if kid:
                total_kols += 1
                conn.execute(
                    "UPDATE kol_posts SET kol_id = ? WHERE platform = 'weibo' AND kol_id IS NULL",
                    [kid],
                )

        logger.info(f"Weibo: {total_posts} posts, {total_kols} KOLs")

    except Exception as e:
        logger.error(f"Weibo fetch failed: {e}")
        return {"source": "weibo", "status": "error", "reason": str(e)}

    return {
        "source": "weibo",
        "status": "ok",
        "queries": len(search_queries),
        "posts_fetched": total_posts,
        "kols_discovered": total_kols,
    }


# ═══════════════════════════════════════════════════════════════
# Seeking Alpha Fetcher (Playwright)
# ═══════════════════════════════════════════════════════════════

def fetch_seekingalpha(conn, news_limit: int = SEEKINGALPHA_NEWS_LIMIT) -> Dict[str, Any]:
    """Scrape Seeking Alpha news/analysis headlines via Playwright.

    Authors are treated as KOL candidates. Posts go through LLM tagging.
    Returns summary dict.
    """
    from kol_thermometer.scraper import scrape_seekingalpha_news

    if not _check_playwright_available():
        return {"source": "seekingalpha", "status": "skipped", "reason": "playwright not installed"}

    total_posts = 0
    total_kols = 0

    try:
        logger.info("Seeking Alpha: scraping via Playwright...")
        all_posts, all_kols = scrape_seekingalpha_news(limit=news_limit)

        if all_posts:
            total_posts = upsert_posts_batch(conn, all_posts)

        max_followers = get_max_followers(conn, "seekingalpha")
        for kc in all_kols:
            if kc["followers"] < 500:
                continue
            pc = kc["post_count"]
            posts_per_week = pc / 7.0 * 7

            scores = compute_kol_score(
                followers=kc["followers"],
                max_followers=max(max_followers, kc["followers"]),
                avg_likes=0,
                avg_comments=0,
                avg_shares=0,
                posts_per_week=posts_per_week,
                account_age_days=kc["account_age_days"],
                stock_mention_ratio=0.9,  # Seeking Alpha is entirely stock-focused
            )
            tier = assign_tier(scores["total_score"])
            weight = compute_kol_weight(tier, "seekingalpha")

            kc.update({
                "avg_likes": 0,
                "avg_comments": 0,
                "avg_shares": 0,
                "avg_views": 0,
                "posts_per_week": round(posts_per_week, 2),
                "stock_mention_ratio": 0.9,
                "total_score": scores["total_score"],
                "score_reach": scores["reach"],
                "score_engagement": scores["engagement"],
                "score_consistency": scores["consistency"],
                "score_relevance": scores["stock_relevance"],
                "score_impact": scores["impact"],
                "tier": tier,
                "base_weight": weight,
                "first_seen_date": TODAY,
                "last_active_date": TODAY,
                "is_active": 1,
            })
            kid = upsert_kol(conn, kc)
            if kid:
                total_kols += 1
                conn.execute(
                    "UPDATE kol_posts SET kol_id = ? WHERE platform = 'seekingalpha' AND kol_id IS NULL",
                    [kid],
                )

        logger.info(f"Seeking Alpha: {total_posts} articles, {total_kols} KOLs")

    except Exception as e:
        logger.error(f"Seeking Alpha fetch failed: {e}")
        return {"source": "seekingalpha", "status": "error", "reason": str(e)}

    return {
        "source": "seekingalpha",
        "status": "ok",
        "posts_fetched": total_posts,
        "kols_discovered": total_kols,
    }


# ═══════════════════════════════════════════════════════════════
# Moomoo (富途) Fetcher (Playwright)
# ═══════════════════════════════════════════════════════════════

def fetch_moomoo(conn, symbols: Optional[List[str]] = None,
                 posts_per_symbol: int = MOOMOO_POSTS_PER_SYMBOL,
                 is_init: bool = False) -> Dict[str, Any]:
    """Scrape Moomoo (富途牛牛) community stock discussions via Playwright.

    Auto-discovers KOLs from community users. Posts go through LLM tagging.
    Returns summary dict.
    """
    from kol_thermometer.scraper import scrape_moomoo_all

    if not _check_playwright_available():
        return {"source": "moomoo", "status": "skipped", "reason": "playwright not installed"}

    syms = symbols or MOOMOO_SYMBOLS
    total_posts = 0
    total_kols = 0

    try:
        logger.info(f"Moomoo: scraping {len(syms)} symbols via Playwright...")
        all_posts, all_kols = scrape_moomoo_all(symbols=syms)

        if all_posts:
            total_posts = upsert_posts_batch(conn, all_posts)

        max_followers = get_max_followers(conn, "moomoo")
        for kc in all_kols:
            if kc["followers"] < 100 and not is_init:
                continue
            pc = kc["post_count"]
            posts_per_week = pc / max(is_init and 30 or 7, 1) * 7
            avg_likes = kc["total_likes"] / max(pc, 1)

            scores = compute_kol_score(
                followers=kc["followers"],
                max_followers=max(max_followers, kc["followers"]),
                avg_likes=avg_likes,
                avg_comments=kc.get("total_comments", 0) / max(pc, 1),
                avg_shares=0,
                posts_per_week=posts_per_week,
                account_age_days=kc["account_age_days"],
                stock_mention_ratio=0.7,
            )
            tier = assign_tier(scores["total_score"])
            weight = compute_kol_weight(tier, "moomoo")

            kc.update({
                "avg_likes": avg_likes,
                "avg_comments": kc.get("total_comments", 0) / max(pc, 1),
                "avg_shares": 0,
                "avg_views": 0,
                "posts_per_week": round(posts_per_week, 2),
                "stock_mention_ratio": 0.7,
                "total_score": scores["total_score"],
                "score_reach": scores["reach"],
                "score_engagement": scores["engagement"],
                "score_consistency": scores["consistency"],
                "score_relevance": scores["stock_relevance"],
                "score_impact": scores["impact"],
                "tier": tier,
                "base_weight": weight,
                "first_seen_date": TODAY,
                "last_active_date": TODAY,
                "is_active": 1,
            })
            kid = upsert_kol(conn, kc)
            if kid:
                total_kols += 1
                conn.execute(
                    "UPDATE kol_posts SET kol_id = ? WHERE platform = 'moomoo' AND kol_id IS NULL",
                    [kid],
                )

        logger.info(f"Moomoo: {total_posts} posts, {total_kols} KOLs")

    except Exception as e:
        logger.error(f"Moomoo fetch failed: {e}")
        return {"source": "moomoo", "status": "error", "reason": str(e)}

    return {
        "source": "moomoo",
        "status": "ok",
        "symbols_scanned": len(syms),
        "posts_fetched": total_posts,
        "kols_discovered": total_kols,
    }


# ═══════════════════════════════════════════════════════════════
# StockTwits Fetcher
# ═══════════════════════════════════════════════════════════════

def _get_stocktwits_headers():
    """Build StockTwits API headers. Returns None if not configured."""
    if not STOCKTWITS_ACCESS_TOKEN:
        logger.warning("StockTwits access token not configured. Set STOCKTWITS_ACCESS_TOKEN in .env")
        return None
    return {"Authorization": f"Bearer {STOCKTWITS_ACCESS_TOKEN}"}


def fetch_stocktwits(conn, symbols_limit: int = STOCKTWITS_SYMBOLS_LIMIT,
                     messages_per_symbol: int = STOCKTWITS_MESSAGES_PER_SYMBOL) -> Dict[str, Any]:
    """Fetch messages from StockTwits trending symbols. Auto-discovers KOLs.

    StockTwits messages have built-in sentiment (bullish/bearish), so we bypass
    LLM tagging for this source and store sentiment directly.

    Returns summary dict.
    """
    import requests as req

    headers = _get_stocktwits_headers()
    if headers is None:
        return {"source": "stocktwits", "status": "skipped", "reason": "no access token configured"}

    base = "https://api.stocktwits.com/api/2"
    total_posts = 0
    total_kols = 0
    total_mentions = 0
    errors = []

    try:
        # Step 1: Get trending symbols
        logger.info("StockTwits: fetching trending symbols...")
        resp = req.get(f"{base}/trending/symbols.json", headers=headers, timeout=30)
        resp.raise_for_status()
        trending_data = resp.json()
        symbols = [s["symbol"] for s in trending_data.get("symbols", [])[:symbols_limit]]
        logger.info(f"StockTwits: {len(symbols)} trending symbols")
    except Exception as e:
        return {"source": "stocktwits", "status": "error", "reason": f"trending fetch failed: {e}"}

    max_followers = get_max_followers(conn, "stocktwits")

    for symbol in symbols:
        try:
            resp = req.get(
                f"{base}/streams/symbol/{symbol}.json",
                headers=headers,
                params={"limit": messages_per_symbol},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

            posts_data = []
            kol_candidates: Dict[int, Dict[str, Any]] = {}

            for msg in data.get("messages", []):
                user = msg.get("user", {})
                user_id = user.get("id")
                if not user_id:
                    continue

                username = user.get("username", "")
                created = msg.get("created_at", "").replace("T", " ").replace("Z", "")

                post = {
                    "platform": "stocktwits",
                    "post_id": str(msg.get("id", "")),
                    "post_url": f"https://stocktwits.com/{username}/message/{msg.get('id', '')}",
                    "title": (msg.get("body", "") or "")[:200],
                    "content": msg.get("body", "") or "",
                    "posted_at": created,
                    "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                    "likes": msg.get("likes", {}).get("total", 0),
                    "comments": msg.get("conversation", {}).get("total", 0),
                    "shares": msg.get("reshare_count", 0),
                    "views": msg.get("impressions", 0),
                    "kol_id": None,
                }
                posts_data.append(post)

                # Aggregate KOL info
                if user_id not in kol_candidates:
                    followers = user.get("followers", 0)
                    ideas = user.get("ideas", 0)
                    likes_received = user.get("like_count", 0)

                    kol_candidates[user_id] = {
                        "platform": "stocktwits",
                        "username": username,
                        "display_name": user.get("name", username),
                        "profile_url": user.get("avatar_url", f"https://stocktwits.com/{username}"),
                        "followers": followers,
                        "account_age_days": 0,
                        "post_count": ideas,
                        "total_likes": likes_received,
                        "total_comments": 0,
                    }
                else:
                    kol_candidates[user_id]["total_likes"] += msg.get("likes", {}).get("total", 0)

            # Upsert posts
            if posts_data:
                count = upsert_posts_batch(conn, posts_data)
                total_posts += count

                # StockTwits messages have built-in sentiment → store directly as mentions
                for msg in data.get("messages", []):
                    body = msg.get("body", "")
                    if not body:
                        continue

                    entities = msg.get("entities", {})
                    sentiment_raw = entities.get("sentiment")
                    if sentiment_raw:
                        sentiment_label = str(sentiment_raw.get("basic", "neutral")).lower()
                        sentiment_score = {"bullish": 0.7, "bearish": -0.7, "moderate": 0.0}.get(
                            sentiment_label, 0.0
                        )
                    else:
                        sentiment_label = "neutral"
                        sentiment_score = 0.0

                    # Find matching post to get post_id
                    post_row = conn.execute(
                        "SELECT id, kol_id FROM kol_posts WHERE platform='stocktwits' AND post_id = ?",
                        [str(msg.get("id", ""))],
                    ).fetchone()
                    if not post_row:
                        continue

                    # Determine market from symbol mentions in the message
                    market = "US"
                    symbols_in_msg = [s["symbol"] for s in entities.get("symbols", [])]

                    for sym in symbols_in_msg:
                        mention = {
                            "post_id": post_row[0],
                            "kol_id": post_row[1],
                            "stock_code": sym.upper(),
                            "stock_name": "",
                            "market": market,
                            "mention_context": body[:100],
                            "sentiment_score": sentiment_score,
                            "sentiment_label": sentiment_label,
                            "confidence": 0.8,
                        }
                        upsert_mentions_batch(conn, [mention])
                        total_mentions += 1

            # Process KOL candidates
            for user_id, kc in kol_candidates.items():
                if kc["followers"] < 100:
                    continue

                pc = kc["post_count"]
                avg_likes = kc["total_likes"] / max(pc, 1)
                posts_per_week = min(pc / max(30, 1) * 7, 50)

                scores = compute_kol_score(
                    followers=kc["followers"],
                    max_followers=max(max_followers, kc["followers"]),
                    avg_likes=avg_likes,
                    avg_comments=0,
                    avg_shares=0,
                    posts_per_week=posts_per_week,
                    account_age_days=kc["account_age_days"],
                    stock_mention_ratio=0.8,  # StockTwits is inherently stock-focused
                )
                tier = assign_tier(scores["total_score"])
                weight = compute_kol_weight(tier, "stocktwits")

                kc.update({
                    "avg_likes": avg_likes,
                    "avg_comments": 0,
                    "avg_shares": 0,
                    "avg_views": 0,
                    "posts_per_week": round(posts_per_week, 2),
                    "stock_mention_ratio": 0.8,
                    "total_score": scores["total_score"],
                    "score_reach": scores["reach"],
                    "score_engagement": scores["engagement"],
                    "score_consistency": scores["consistency"],
                    "score_relevance": scores["stock_relevance"],
                    "score_impact": scores["impact"],
                    "tier": tier,
                    "base_weight": weight,
                    "first_seen_date": TODAY,
                    "last_active_date": TODAY,
                    "is_active": 1,
                })
                kid = upsert_kol(conn, kc)
                if kid:
                    total_kols += 1
                    conn.execute(
                        "UPDATE kol_posts SET kol_id = ? WHERE platform = 'stocktwits' AND kol_id IS NULL",
                        [kid],
                    )

            logger.info(f"  ${symbol}: {len(posts_data)} msgs, {len(kol_candidates)} users")
            time.sleep(STOCKTWITS_RATE_LIMIT)

        except Exception as e:
            msg = f"StockTwits ${symbol}: {e}"
            logger.error(msg)
            errors.append(msg)

    return {
        "source": "stocktwits",
        "status": "ok" if not errors else "partial",
        "symbols_scanned": len(symbols),
        "posts_fetched": total_posts,
        "mentions_tagged": total_mentions,
        "kols_discovered": total_kols,
        "errors": errors,
    }


# ═══════════════════════════════════════════════════════════════
# Finnhub Fetcher
# ═══════════════════════════════════════════════════════════════

def fetch_finnhub(conn, news_limit: int = FINNHUB_NEWS_LIMIT,
                  sentiment_limit: int = FINNHUB_SENTIMENT_LIMIT) -> Dict[str, Any]:
    """Fetch market news + social sentiment from Finnhub.

    News articles go through LLM tagging like other posts. Social sentiment
    data (pre-computed Reddit/Twitter metrics) is stored directly as mentions.

    Returns summary dict.
    """
    import requests as req

    if not FINNHUB_API_KEY:
        return {"source": "finnhub", "status": "skipped", "reason": "no API key configured"}

    base = "https://finnhub.io/api/v1"
    total_posts = 0
    total_mentions = 0
    errors = []

    try:
        # Step 1: Fetch general market news
        logger.info("Finnhub: fetching market news...")
        resp = req.get(
            f"{base}/news",
            params={"category": "general", "token": FINNHUB_API_KEY},
            timeout=30,
        )
        resp.raise_for_status()
        articles = resp.json()[:news_limit]

        posts_data = []
        for art in articles:
            posts_data.append({
                "platform": "finnhub",
                "post_id": str(art.get("id", "")),
                "post_url": art.get("url", ""),
                "title": art.get("headline", "")[:200],
                "content": (art.get("summary", "") or "")[:600],
                "posted_at": datetime.fromtimestamp(
                    art.get("datetime", 0), tz=timezone.utc
                ).strftime("%Y-%m-%d %H:%M:%S") if art.get("datetime") else "",
                "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "likes": 0,
                "comments": 0,
                "shares": 0,
                "views": 0,
                "kol_id": None,
            })

        if posts_data:
            count = upsert_posts_batch(conn, posts_data)
            total_posts += count
            logger.info(f"Finnhub: {count} news articles")

        # Step 2: Fetch social sentiment for active symbols
        logger.info("Finnhub: fetching social sentiment...")
        active_symbols = conn.execute("""
            SELECT DISTINCT stock_code FROM stock_mentions WHERE market = 'US'
            UNION
            SELECT 'AAPL' UNION SELECT 'TSLA' UNION SELECT 'MSFT' UNION SELECT 'NVDA'
            UNION SELECT 'GOOGL' UNION SELECT 'AMZN' UNION SELECT 'META' UNION SELECT 'SPY'
            UNION SELECT 'QQQ'
        """).fetchall()
        active_symbols = [r[0] for r in active_symbols][:sentiment_limit]

        for sym in active_symbols:
            try:
                resp = req.get(
                    f"{base}/stock/social-sentiment",
                    params={"symbol": sym, "token": FINNHUB_API_KEY},
                    timeout=30,
                )
                resp.raise_for_status()
                sent_data = resp.json()

                reddit_data = sent_data.get("reddit", [])
                twitter_data = sent_data.get("twitter", [])

                for entry in reddit_data[:5]:
                    if entry.get("mention", 0) > 0:
                        mention = {
                            "post_id": None,  # no specific post, aggregated
                            "kol_id": None,
                            "stock_code": sym.upper(),
                            "stock_name": "",
                            "market": "US",
                            "mention_context": f"Reddit: {entry.get('mention', 0)} mentions in 24h",
                            "sentiment_score": entry.get("positiveScore", 0) / 100.0 - entry.get("negativeScore", 0) / 100.0,
                            "sentiment_label": "positive" if entry.get("positiveScore", 0) > entry.get("negativeScore", 0) else "neutral",
                            "confidence": 0.5,
                        }
                        upsert_mentions_batch(conn, [mention])
                        total_mentions += 1

                for entry in twitter_data[:5]:
                    if entry.get("mention", 0) > 0:
                        mention = {
                            "post_id": None,
                            "kol_id": None,
                            "stock_code": sym.upper(),
                            "stock_name": "",
                            "market": "US",
                            "mention_context": f"Twitter: {entry.get('mention', 0)} mentions in 24h",
                            "sentiment_score": entry.get("positiveScore", 0) / 100.0 - entry.get("negativeScore", 0) / 100.0,
                            "sentiment_label": "positive" if entry.get("positiveScore", 0) > entry.get("negativeScore", 0) else "neutral",
                            "confidence": 0.4,
                        }
                        upsert_mentions_batch(conn, [mention])
                        total_mentions += 1

                time.sleep(0.15)

            except Exception as e:
                logger.error(f"Finnhub sentiment ${sym}: {e}")

        logger.info(f"Finnhub: {total_mentions} social sentiment mentions processed")

    except Exception as e:
        msg = f"Finnhub fetch failed: {e}"
        logger.error(msg)
        errors.append(msg)

    return {
        "source": "finnhub",
        "status": "ok" if not errors else "partial",
        "posts_fetched": total_posts,
        "sentiment_mentions": total_mentions,
        "errors": errors,
    }


# ═══════════════════════════════════════════════════════════════
# WeChat Official Accounts Fetcher (Playwright via Sogou)
# ═══════════════════════════════════════════════════════════════

def fetch_wechat(conn, kols: Optional[List[Dict[str, str]]] = None,
                 articles_per_kol: int = WECHAT_ARTICLES_PER_KOL,
                 is_init: bool = False) -> Dict[str, Any]:
    """Scrape WeChat Official Account articles via Sogou WeChat Search.

    Uses pre-defined KOL list. Each KOL's recent articles are scraped and
    go through standard LLM tagging for stock mention extraction.

    Since these are pre-verified top financial KOLs, they start with high
    initial scores (Tier A minimum).

    Returns summary dict.
    """
    from kol_thermometer.scraper import scrape_wechat_kol

    if not _check_playwright_available():
        return {"source": "wechat", "status": "skipped", "reason": "playwright not installed"}

    kol_list = kols or WECHAT_KOLS
    total_posts = 0
    total_kols = 0
    errors = []

    for k in kol_list:
        try:
            account_name = k["name"]
            logger.info(f"WeChat: scraping {account_name} ...")

            all_posts, all_kols = scrape_wechat_kol(account_name, limit=articles_per_kol)

            if all_posts:
                count = upsert_posts_batch(conn, all_posts)
                total_posts += count

            max_followers = get_max_followers(conn, "wechat")

            for kc in all_kols:
                pc = kc["post_count"]
                if pc == 0:
                    continue

                # Pre-verified KOLs — conservative estimates based on known reach
                followers = kc.get("followers", 500000)
                posts_per_week = pc / max(is_init and 30 or 7, 1) * 7

                scores = compute_kol_score(
                    followers=followers,
                    max_followers=max(max_followers, followers),
                    avg_likes=kc.get("total_likes", 0) / max(pc, 1),
                    avg_comments=kc.get("total_comments", 0) / max(pc, 1),
                    avg_shares=0,
                    posts_per_week=posts_per_week,
                    account_age_days=kc.get("account_age_days", 365 * 3),
                    stock_mention_ratio=0.7,  # financial accounts, high relevance
                )
                tier = assign_tier(scores["total_score"])
                # Pre-verified KOLs get at least Tier B
                tier_rank = {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1}
                if tier_rank.get(tier, 0) < tier_rank["B"]:
                    tier = "B"
                weight = compute_kol_weight(tier, "wechat")

                kc.update({
                    "avg_likes": kc.get("total_likes", 0) / max(pc, 1),
                    "avg_comments": kc.get("total_comments", 0) / max(pc, 1),
                    "avg_shares": 0,
                    "avg_views": 0,
                    "posts_per_week": round(posts_per_week, 2),
                    "stock_mention_ratio": 0.7,
                    "total_score": scores["total_score"],
                    "score_reach": scores["reach"],
                    "score_engagement": scores["engagement"],
                    "score_consistency": scores["consistency"],
                    "score_relevance": scores["stock_relevance"],
                    "score_impact": scores["impact"],
                    "tier": tier,
                    "base_weight": weight,
                    "first_seen_date": TODAY,
                    "last_active_date": TODAY,
                    "is_active": 1,
                })
                kid = upsert_kol(conn, kc)
                if kid:
                    total_kols += 1
                    conn.execute(
                        "UPDATE kol_posts SET kol_id = ? WHERE platform = 'wechat' AND kol_id IS NULL",
                        [kid],
                    )

            logger.info(f"  {account_name}: {len(all_posts)} articles")
            time.sleep(WECHAT_RATE_LIMIT)

        except Exception as e:
            msg = f"WeChat '{k['name']}': {e}"
            logger.error(msg)
            errors.append(msg)

    return {
        "source": "wechat",
        "status": "ok" if not errors else "partial",
        "accounts_scanned": len(kol_list),
        "posts_fetched": total_posts,
        "kols_discovered": total_kols,
        "errors": errors,
    }


# ═══════════════════════════════════════════════════════════════
# Stock Mention Tagging
# ═══════════════════════════════════════════════════════════════

def tag_untagged_posts(conn, batch_size: int = 20) -> int:
    """Run LLM tagging on posts without stock mentions. Returns count of new mentions."""
    from kol_thermometer.llm_tagger import extract_stock_mentions, needs_llm

    if not needs_llm():
        logger.warning("No LLM configured for stock mention extraction")
        return 0

    untagged = get_posts_without_mentions(conn, limit=batch_size * 2)
    if untagged.empty:
        logger.info("No untagged posts to process")
        return 0

    total_mentions = 0
    posts_list = untagged.to_dict(orient="records")

    for i in range(0, len(posts_list), batch_size):
        batch = posts_list[i:i + batch_size]
        mentions = extract_stock_mentions(batch)
        if mentions:
            # Assign kol_id if missing (get from post's kol_id)
            post_map = {p["id"]: p for p in batch}
            for m in mentions:
                if m.get("post_id") and m["post_id"] in post_map:
                    m["kol_id"] = m.get("kol_id") or post_map[m["post_id"]].get("kol_id")
            count = upsert_mentions_batch(conn, mentions)
            total_mentions += count
            logger.info(f"Tagged {count} new mentions from {len(batch)} posts")

        time.sleep(0.5)  # gentle on LLM API

    return total_mentions


# ═══════════════════════════════════════════════════════════════
# KOL Rating Maintenance
# ═══════════════════════════════════════════════════════════════

def update_kol_ratings(conn) -> int:
    """Recompute scores and tiers for all active KOLs. Returns count."""
    kols_df = get_kols(conn, is_active=1, limit=5000)
    if kols_df.empty:
        return 0

    updated = 0
    for _, row in kols_df.iterrows():
        platform = row["platform"]
        max_followers = get_max_followers(conn, platform)
        scores = compute_kol_score(
            followers=row["followers"],
            max_followers=max(max_followers, row["followers"]),
            avg_likes=row["avg_likes"],
            avg_comments=row["avg_comments"],
            avg_shares=row["avg_shares"],
            posts_per_week=row["posts_per_week"],
            account_age_days=row["account_age_days"],
            stock_mention_ratio=row["stock_mention_ratio"],
            mention_price_corr=row.get("mention_price_corr", 0),
        )
        tier = assign_tier(scores["total_score"])

        # Apply inactivity decay
        if row["last_active_date"]:
            try:
                last_date = datetime.strptime(str(row["last_active_date"])[:10], "%Y-%m-%d")
                days_since = (datetime.now() - last_date).days
                tier = apply_tier_decay(tier, days_since)
            except Exception:
                pass

        if tier == "REMOVE":
            conn.execute("UPDATE kols SET is_active = 0 WHERE id = ?", [row["id"]])
            updated += 1
            continue

        weight = compute_kol_weight(tier, platform)
        conn.execute("""
            UPDATE kols SET
                total_score = ?, score_reach = ?, score_engagement = ?,
                score_consistency = ?, score_relevance = ?, score_impact = ?,
                tier = ?, base_weight = ?
            WHERE id = ?
        """, [
            scores["total_score"], scores["reach"], scores["engagement"],
            scores["consistency"], scores["stock_relevance"], scores["impact"],
            tier, weight, row["id"],
        ])
        updated += 1

    return updated


# ═══════════════════════════════════════════════════════════════
# Thermometer Computation
# ═══════════════════════════════════════════════════════════════

def compute_daily_thermometer(conn) -> int:
    """Aggregate all recent mentions into daily thermometer records. Returns count."""
    from kol_thermometer.thermometer import compute_heat_score, compute_momentum

    mentions = get_mentions_for_thermometer(conn)
    if not mentions:
        logger.info("No recent mentions for thermometer computation")
        return 0

    # Group mentions by stock_code
    by_stock: Dict[str, List[Dict[str, Any]]] = {}
    stock_names: Dict[str, str] = {}
    stock_markets: Dict[str, str] = {}

    for m in mentions:
        code = m["stock_code"]
        by_stock.setdefault(code, []).append(m)
        # Get stock name and market from mentions table
        if code not in stock_names:
            row = conn.execute(
                "SELECT stock_name, market FROM stock_mentions WHERE stock_code = ? LIMIT 1",
                [code],
            ).fetchone()
            if row:
                stock_names[code] = row[0] or ""
                stock_markets[code] = row[1] or "unknown"

    records = []
    for stock_code, stock_mentions in by_stock.items():
        heat = compute_heat_score(stock_mentions)
        past = get_past_heats(conn, stock_code, TODAY)
        momentum = compute_momentum(heat["heat_score"], past)

        # Top KOLs for this stock
        kol_counts: Dict[int, int] = {}
        for m in stock_mentions:
            kid = m.get("kol_id")
            if kid:
                kol_counts[kid] = kol_counts.get(kid, 0) + 1
        top_kols = sorted(kol_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        top_kols_json = json.dumps([{"kol_id": k, "mentions": c} for k, c in top_kols])

        records.append({
            "date": TODAY,
            "stock_code": stock_code,
            "stock_name": stock_names.get(stock_code, ""),
            "market": stock_markets.get(stock_code, "unknown"),
            "mention_count": heat["mention_count"],
            "unique_kols": heat["unique_kols"],
            "heat_score": heat["heat_score"],
            "raw_heat": heat["raw_heat"],
            "sentiment_bias": heat["sentiment_bias"],
            "positive_count": heat["positive_count"],
            "negative_count": heat["negative_count"],
            "momentum": momentum,
            "top_kols": top_kols_json,
        })

    count = upsert_thermometer(conn, records)
    logger.info(f"Thermometer computed: {count} stocks, {len(mentions)} total mentions")
    return count


# ═══════════════════════════════════════════════════════════════
# Orchestrators
# ═══════════════════════════════════════════════════════════════

def fetch_daily(source: Optional[str] = None) -> Dict[str, Any]:
    """Daily fetch: posts → tag mentions → update ratings → compute thermometer.

    Args:
        source: optionally run only one source ("reddit", "youtube", "stocktwits",
                "finnhub", "twitter", "weibo", "seekingalpha", "moomoo", "wechat")
    """
    conn = init_db()
    results = {}
    all_errors = []

    try:
        # 1. Fetch posts
        if not source or source == "reddit":
            log_id = log_fetch_start(conn, "reddit")
            result = fetch_reddit(conn)
            results["reddit"] = result
            log_fetch_end(conn, log_id, items_checked=result.get("posts_fetched", 0),
                          new_items=result.get("kols_discovered", 0))
            if result.get("errors"):
                all_errors.extend(result["errors"])

        if not source or source == "youtube":
            log_id = log_fetch_start(conn, "youtube")
            result = fetch_youtube(conn)
            results["youtube"] = result
            log_fetch_end(conn, log_id, items_checked=result.get("posts_fetched", 0),
                          new_items=result.get("kols_discovered", 0))
            if result.get("errors"):
                all_errors.extend(result["errors"])

        if not source or source == "twitter":
            log_id = log_fetch_start(conn, "twitter")
            result = fetch_twitter(conn)
            results["twitter"] = result
            log_fetch_end(conn, log_id, items_checked=result.get("posts_fetched", 0),
                          new_items=result.get("kols_discovered", 0))
            if result.get("errors"):
                all_errors.extend(result["errors"])

        if not source or source == "weibo":
            log_id = log_fetch_start(conn, "weibo")
            result = fetch_weibo(conn)
            results["weibo"] = result
            log_fetch_end(conn, log_id, items_checked=result.get("posts_fetched", 0),
                          new_items=result.get("kols_discovered", 0))
            if result.get("errors"):
                all_errors.extend(result["errors"])

        if not source or source == "seekingalpha":
            log_id = log_fetch_start(conn, "seekingalpha")
            result = fetch_seekingalpha(conn)
            results["seekingalpha"] = result
            log_fetch_end(conn, log_id, items_checked=result.get("posts_fetched", 0),
                          new_items=result.get("kols_discovered", 0))
            if result.get("errors"):
                all_errors.extend(result["errors"])

        if not source or source == "moomoo":
            log_id = log_fetch_start(conn, "moomoo")
            result = fetch_moomoo(conn)
            results["moomoo"] = result
            log_fetch_end(conn, log_id, items_checked=result.get("posts_fetched", 0),
                          new_items=result.get("kols_discovered", 0))
            if result.get("errors"):
                all_errors.extend(result["errors"])

        if not source or source == "stocktwits":
            log_id = log_fetch_start(conn, "stocktwits")
            result = fetch_stocktwits(conn)
            results["stocktwits"] = result
            log_fetch_end(conn, log_id, items_checked=result.get("posts_fetched", 0),
                          new_items=result.get("kols_discovered", 0))
            if result.get("errors"):
                all_errors.extend(result["errors"])

        if not source or source == "finnhub":
            log_id = log_fetch_start(conn, "finnhub")
            result = fetch_finnhub(conn)
            results["finnhub"] = result
            log_fetch_end(conn, log_id, items_checked=result.get("posts_fetched", 0),
                          new_items=0)
            if result.get("errors"):
                all_errors.extend(result["errors"])

        if not source or source == "wechat":
            log_id = log_fetch_start(conn, "wechat")
            result = fetch_wechat(conn)
            results["wechat"] = result
            log_fetch_end(conn, log_id, items_checked=result.get("posts_fetched", 0),
                          new_items=result.get("kols_discovered", 0))
            if result.get("errors"):
                all_errors.extend(result["errors"])

        # 2. Tag untagged posts with LLM
        mention_count = tag_untagged_posts(conn)
        results["mentions_tagged"] = mention_count

        # 3. Update KOL ratings
        kol_updated = update_kol_ratings(conn)
        results["kols_rated"] = kol_updated

        # 4. Deactivate old KOLs
        deactivated = deactivate_inactive_kols(conn)
        results["kols_deactivated"] = deactivated

        # 5. Compute thermometer
        thermo_count = compute_daily_thermometer(conn)
        results["thermometer_stocks"] = thermo_count

        status = "ok" if not all_errors else "partial"
        return {"status": status, "results": results, "errors": all_errors}

    except Exception as e:
        logger.error(f"fetch_daily failed: {e}")
        return {"status": "error", "error": str(e), "results": results}
    finally:
        conn.close()


def init() -> Dict[str, Any]:
    """Backfill mode — wider scan with lower thresholds for initial KOL discovery.

    Fetches from all sources with relaxed KOL thresholds.
    """
    conn = init_db()
    try:
        logger.info("Starting init (backfill) mode ...")

        # Scan more posts per sub for initial KOL discovery
        reddit_result = fetch_reddit(conn, posts_per_sub=100, is_init=True)
        youtube_result = fetch_youtube(conn, max_results=50, is_init=True)

        logger.info("Tagging posts with LLM ...")
        mention_count = tag_untagged_posts(conn)

        logger.info("Rating KOLs ...")
        kol_updated = update_kol_ratings(conn)

        logger.info("Computing initial thermometer ...")
        thermo_count = compute_daily_thermometer(conn)

        stats = get_stats(conn)
        result = {
            "status": "ok",
            "reddit": reddit_result,
            "youtube": youtube_result,
            "mentions_tagged": mention_count,
            "kols_rated": kol_updated,
            "thermometer_stocks": thermo_count,
            "stats": stats,
        }
        logger.info(f"Init complete: {stats}")
        return result
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="KOL Thermometer Pipeline")
    parser.add_argument("--init", action="store_true", help="Backfill mode (wider scan)")
    parser.add_argument("--source", type=str, default=None,
                        choices=["reddit", "youtube", "stocktwits", "finnhub",
                                 "twitter", "weibo", "seekingalpha", "moomoo", "wechat"],
                        help="Run a single source only")
    args = parser.parse_args()

    if args.init:
        result = init()
    else:
        result = fetch_daily(source=args.source)

    print(json.dumps(result, indent=2, default=str))
