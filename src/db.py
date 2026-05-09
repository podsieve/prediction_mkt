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


def mark_inactive_models(client, seen_model_ids: Set[str], now: str):
    result = (
        client.table("models")
        .select("id, canonical_name")
        .eq("is_active", True)
        .execute()
    )
    active_models = result.data or []

    for model in active_models:
        if model["id"] not in seen_model_ids:
            logger.info("Model no longer on leaderboard: %s", model["canonical_name"])
            client.table("models").update(
                {"is_active": False, "last_seen_at": now}
            ).eq("id", model["id"]).execute()


def store_results(scrape_result: ScrapeResult):
    client = get_client()
    now = scrape_result.scraped_at.isoformat()

    snapshot = client.table("snapshots").insert(
        {
            "scraped_at": now,
            "source_url": scrape_result.source_url,
            "total_models": scrape_result.total_models,
            "total_votes": scrape_result.total_votes,
            "scrape_duration_ms": scrape_result.scrape_duration_ms,
            "status": "success",
            "raw_html_hash": scrape_result.raw_html_hash,
        }
    ).execute()
    snapshot_id = snapshot.data[0]["id"]
    logger.info("Created snapshot %s", snapshot_id)

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
        logger.info("New models detected: %s", [m["canonical_name"] for m in new_models])

    # Build rankings batch
    seen_model_ids: Set[str] = set()
    rankings_batch = []

    for scraped_model in scrape_result.models:
        name = scraped_model.model_name
        model_id = model_cache.get(name) or alias_cache.get(name)
        if not model_id:
            logger.warning("Could not resolve model_id for: %s", name)
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

    logger.info("Inserted %d rankings for snapshot %s", len(rankings_batch), snapshot_id)

    # Bulk update last_seen_at for all seen models
    seen_ids = list(seen_model_ids)
    for i in range(0, len(seen_ids), 100):
        batch_ids = seen_ids[i:i + 100]
        client.table("models").update({"last_seen_at": now}).in_("id", batch_ids).execute()

    mark_inactive_models(client, seen_model_ids, now)


def record_failed_scrape(error_message: str):
    try:
        client = get_client()
        client.table("snapshots").insert(
            {
                "scraped_at": datetime.now(timezone.utc).isoformat(),
                "source_url": settings.scrape_url,
                "status": "failed",
                "error_message": error_message[:1000],
            }
        ).execute()
        logger.info("Recorded failed scrape snapshot")
    except Exception as e:
        logger.error("Failed to record failed scrape: %s", e)
