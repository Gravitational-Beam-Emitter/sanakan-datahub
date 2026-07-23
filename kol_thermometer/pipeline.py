"""
Pipeline — KOL discovery, post fetching, stock mention extraction, rating compute.

Data sources:
  - Reddit: stock subreddits via PRAW
  - YouTube: stock analysis channels via Data API v3
  - 东方财富股吧: via AKShare (P1)

Usage:
  python -m kol_thermometer.pipeline           # daily fetch
  python -m kol_thermometer.pipeline --init    # backfill (wider scan)
  python -m kol_thermometer.pipeline --source reddit   # single source
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
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
            req = youtube.search().list(
                q=query,
                part="snippet",
                maxResults=max_results,
                type="video",
                order="relevance" if not is_init else "date",
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
        source: optionally run only one source ("reddit" or "youtube")
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
    parser.add_argument("--source", type=str, default=None, choices=["reddit", "youtube"],
                        help="Run a single source only")
    args = parser.parse_args()

    if args.init:
        result = init()
    else:
        result = fetch_daily(source=args.source)

    print(json.dumps(result, indent=2, default=str))
