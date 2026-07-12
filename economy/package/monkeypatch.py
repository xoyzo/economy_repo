"""
Monkeypatches BallSpawnView.catch_ball to award catch income (economy layer 1).

Isolated from cog.py so the patch target and its lifecycle (apply on cog_load,
restore on cog_unload) are easy to audit in one place.
"""

import logging
import random

import discord
from ballsdex.packages.countryballs.countryball import BallSpawnView
from bd_models.models import Player

from ..models import EconomySettings

log = logging.getLogger(__name__)

# Keep a reference to the un-patched method so it can be restored on cog_unload,
# and so the patched version can still call through to original catch behaviour.
_original_catch_ball = BallSpawnView.catch_ball


async def _patched_catch_ball(
    self: BallSpawnView,
    user: discord.User | discord.Member,
    *,
    player: Player | None,
    guild: discord.Guild | None,
):
    from settings.models import settings  # imported here to always read current settings

    ball, is_new = await _original_catch_ball(self, user, player=player, guild=guild)

    if not settings.currency_enabled:
        return ball, is_new

    try:
        cfg = await EconomySettings.objects.afirst()
    except Exception:
        return ball, is_new

    if cfg is None or not cfg.catch_income_enabled:
        return ball, is_new

    if player is None:
        try:
            player = await Player.objects.aget(discord_id=user.id)
        except Player.DoesNotExist:
            return ball, is_new

    earned = cfg.compute_catch_income(rarity=ball.ball.rarity, has_special=ball.special_id is not None)
    await player.add_money(earned)
    log.debug("Catch income: player %s earned %d (%s)", user.id, earned, ball.ball.country)
    return ball, is_new


def apply_patch() -> None:
    """Install the patched catch_ball. Call from Economy.cog_load()."""
    BallSpawnView.catch_ball = _patched_catch_ball  # type: ignore[method-assign]
    log.info("Economy: monkeypatch applied.")


def restore_patch() -> None:
    """Restore the original catch_ball. Call from Economy.cog_unload()."""
    BallSpawnView.catch_ball = _original_catch_ball  # type: ignore[method-assign]
    log.info("Economy: monkeypatch restored.")
