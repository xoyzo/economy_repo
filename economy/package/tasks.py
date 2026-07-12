"""
Background task loops for the Economy cog.

EconomyTasksMixin is mixed into the Economy cog (see cog.py) rather than kept
standalone, since discord.ext.tasks.loop methods need to live on the cog
instance to be start()/cancel()'d from cog_load/cog_unload. run_passive_tick()
is exposed separately so /economy admin forcepassive can trigger the same
logic on demand without waiting for the loop.
"""

import logging
import random

from discord.ext import tasks
from django.utils import timezone

from bd_models.models import BallInstance, Player

from ..models import BallListing, EconomySettings, PassiveIncomePool
from .helpers import compute_passive_tick_for, get_cfg

log = logging.getLogger(__name__)


async def run_passive_tick(cfg: EconomySettings) -> tuple[int, int]:
    """
    Run one passive income tick for every player, crediting their
    PassiveIncomePool. Returns (players_updated, total_generated).
    """
    now = timezone.now()
    players_updated = 0
    total_generated = 0

    async for player in Player.objects.all():
        total = 0
        async for bi in BallInstance.objects.filter(player=player, deleted=False).select_related("ball", "special"):
            if random.random() < cfg.passive_chance:
                total += await compute_passive_tick_for(bi, cfg)

        if total > 0:
            pool, _ = await PassiveIncomePool.objects.aget_or_create(
                player=player, defaults={"pending": 0, "total_earned": 0}
            )
            pool.pending += total
            pool.total_earned += total
            pool.last_tick = now
            await pool.asave(update_fields=["pending", "total_earned", "last_tick"])
            players_updated += 1
            total_generated += total

    return players_updated, total_generated


class EconomyTasksMixin:
    """Background loops. Started in Economy._post_init, cancelled in cog_unload."""

    @tasks.loop(minutes=10)
    async def passive_income_task(self) -> None:
        from settings.models import settings

        if not settings.currency_enabled:
            return
        cfg = await get_cfg()
        if cfg is None or not cfg.passive_enabled:
            return
        await run_passive_tick(cfg)

    @tasks.loop(minutes=15)
    async def expire_listings_task(self) -> None:
        now = timezone.now()
        async for listing in BallListing.objects.filter(sold=False, expires_at__lte=now):
            await listing.adelete()
            log.info("Listing #%d expired.", listing.pk)
