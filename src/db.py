from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from supabase import create_client

from src.config import settings
from src.models import ScrapedModel, ScrapeResult

logger = logging.getLogger(__name__)


def get_client():
    return create_client(settings.supabase_url, settings.supabase_key)


def load_caches(client):
    """Preload all models and aliases into memory to avoid per-model HTTP calls."""
    model_cache = {}
    alias_cache = {}

    result = client.table("models").select("id, canonical_name").execute()
    for row in (result.data or []):
        model_cache[row["canonical_name"]] = row["id"]

    result = client.table("model_aliases").select("model_id, alias_name").execute()
    for row in (result.data or []):
        alias_cache[row["alias_name"]] = row["model_id"]

    logger.info("Loaded %d models and %d aliases into cache", len(model_cache), len(alias_cache))
    return model_cache, alias_cache


def bulk_insert_new_models(client, new_models: List[Dict], model_cache: Dict[str, str]) -> Dict[str, str]:
    """Insert all new models in one batch and return updated cache."""
    if not new_models:
        return model_cache

    # Deduplicate by canonical_name
    seen = set()
    unique = []
    for m in new_models:
        if m["canonical_name"] not in seen:
            seen.add(m["canonical_name"])
            unique.append(m)

    batch_size = 100
    for i in range(0, len(unique), batch_size):
        batch = unique[i:i + batch_size]
        result = client.table("models").insert(batch).execute()
        for row in result.data:
            model_cache[row["canonical_name"]] = row["id"]

    logger.info("Bulk inserted %d new models", len(unique))
    return model_cache


def _get_category_model_ids(client, category: str) -> Set[str]:
    """Get model IDs that have appeared in any successful snapshot for a category."""
    result = (
        client.table("snapshots")
        .select("id")
        .eq("status", "success")
        .eq("category", category)
        .order("scraped_at", desc=True)
        .limit(1)
        .execute()
    )
    if not result.data:
        return set()

    prev_snap_id = result.data[0]["id"]
    rankings = (
        client.table("rankings")
        .select("model_id")
        .eq("snapshot_id", prev_snap_id)
        .execute()
    )
    return {r["model_id"] for r in (rankings.data or [])}


def mark_inactive_models(client, seen_model_ids: Set[str], now: str, category: str):
    """Mark models inactive only if they were in this category's previous
    snapshot but are no longer present. Models appearing in other categories
    are not affected."""
    previously_in_category = _get_category_model_ids(client, category)
    if not previously_in_category:
        return

    disappeared = previously_in_category - seen_model_ids
    if not disappeared:
        return

    # Only mark inactive if the model isn't active in ANY other category.
    # Check if these models appear in the latest snapshot of any other category.
    other_categories = [
        cat for cat in settings.scrape_categories if cat != category
    ]
    still_active_elsewhere: Set[str] = set()
    for other_cat in other_categories:
        still_active_elsewhere |= _get_category_model_ids(client, other_cat)

    to_deactivate = disappeared - still_active_elsewhere

    for model_id in to_deactivate:
        client.table("models").update(
            {"is_active": False, "last_seen_at": now}
        ).eq("id", model_id).execute()

    if to_deactivate:
        logger.info(
            "[%s] Marked %d models inactive (disappeared from all categories)",
            category,
            len(to_deactivate),
        )


def store_results(scrape_result: ScrapeResult):
    client = get_client()
    now = scrape_result.scraped_at.isoformat()
    category = scrape_result.category

    snapshot = client.table("snapshots").insert(
        {
            "scraped_at": now,
            "source_url": scrape_result.source_url,
            "category": category,
            "total_models": scrape_result.total_models,
            "total_votes": scrape_result.total_votes,
            "scrape_duration_ms": scrape_result.scrape_duration_ms,
            "status": "success",
            "raw_html_hash": scrape_result.raw_html_hash,
        }
    ).execute()
    snapshot_id = snapshot.data[0]["id"]
    logger.info("[%s] Created snapshot %s", category, snapshot_id)

    model_cache, alias_cache = load_caches(client)

    # Identify new models that need inserting
    new_models = []
    for scraped_model in scrape_result.models:
        name = scraped_model.model_name
        if name not in model_cache and name not in alias_cache:
            new_models.append({
                "canonical_name": name,
                "organization": scraped_model.organization,
                "license_type": scraped_model.license_type,
                "first_seen_at": now,
                "last_seen_at": now,
                "is_active": True,
            })

    if new_models:
        model_cache = bulk_insert_new_models(client, new_models, model_cache)
        logger.info("[%s] New models detected: %s", category,
                    [m["canonical_name"] for m in new_models])

    # Build rankings batch
    seen_model_ids: Set[str] = set()
    rankings_batch = []

    for scraped_model in scrape_result.models:
        name = scraped_model.model_name
        model_id = model_cache.get(name) or alias_cache.get(name)
        if not model_id:
            logger.warning("[%s] Could not resolve model_id for: %s", category, name)
            continue

        seen_model_ids.add(model_id)
        rankings_batch.append({
            "snapshot_id": snapshot_id,
            "model_id": model_id,
            "rank": scraped_model.rank,
            "rank_upper": scraped_model.rank_upper,
            "rank_lower": scraped_model.rank_lower,
            "score": float(scraped_model.score),
            "score_ci": float(scraped_model.score_ci) if scraped_model.score_ci is not None else None,
            "votes": scraped_model.votes,
            "raw_model_name": scraped_model.model_name,
            "raw_organization": scraped_model.organization,
        })

    # Bulk insert rankings
    batch_size = 100
    for i in range(0, len(rankings_batch), batch_size):
        batch = rankings_batch[i:i + batch_size]
        client.table("rankings").insert(batch).execute()

    logger.info("[%s] Inserted %d rankings for snapshot %s",
                category, len(rankings_batch), snapshot_id)

    # Bulk update last_seen_at for all seen models
    seen_ids = list(seen_model_ids)
    for i in range(0, len(seen_ids), 100):
        batch_ids = seen_ids[i:i + 100]
        client.table("models").update({"last_seen_at": now}).in_("id", batch_ids).execute()

    mark_inactive_models(client, seen_model_ids, now, category)


def record_failed_scrape(
    error_message: str,
    category: str = "overall",
    source_url: Optional[str] = None,
):
    try:
        client = get_client()
        client.table("snapshots").insert(
            {
                "scraped_at": datetime.now(timezone.utc).isoformat(),
                "source_url": source_url or settings.scrape_url,
                "category": category,
                "status": "failed",
                "error_message": error_message[:1000],
            }
        ).execute()
        logger.info("[%s] Recorded failed scrape snapshot", category)
    except Exception as e:
        logger.error("[%s] Failed to record failed scrape: %s", category, e)
