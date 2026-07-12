"""
Shared helpers for the economy package: formatting, common embeds, cfg/player
lookups, and pricing calculations.

Pricing note: EconomySettings has compute_quicksell_price() and
compute_passive_tick() model methods, but neither knows about per-special
overrides (SpecialEconomyBonus). compute_quicksell_price_for() and
compute_passive_tick_for() below are the single source of truth for that —
they defer to the model methods for the base roll, then apply a
SpecialEconomyBonus override in place of the global multiplier when one is
configured and enabled. Nothing else in this package should reimplement this
logic; cog.py, tasks.py, and views.py all call through here.
"""

import logging
import random
from typing import TYPE_CHECKING

import discord

from bd_models.models import BallInstance, Player
from settings.models import settings as bot_settings
from settings.utils import format_currency

from ..models import BallSellPrice, EconomySettings, SpecialEconomyBonus

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

log = logging.getLogger(__name__)


# ── Formatting / embeds ──────────────────────────────────────────────────────

def fmt(amount: int, bot: "BallsDexBot | None" = None) -> str:
    """Format currency using bot emoji if available, falling back to symbol."""
    return format_currency(amount, shortened=True, bot=bot)


def error_embed(title: str, desc: str) -> discord.Embed:
    return discord.Embed(title=title, description=desc, color=discord.Color.red())


def disabled_embed(reason: str) -> discord.Embed:
    return discord.Embed(title="Command Disabled", description=reason, color=discord.Color.red())


# ── Config / player lookups ──────────────────────────────────────────────────

async def get_cfg() -> EconomySettings | None:
    try:
        return await EconomySettings.objects.afirst()
    except Exception:
        return None


async def get_player(interaction: discord.Interaction) -> Player | None:
    """Fetch the Player for this interaction's user, sending a 'No Account' embed if missing."""
    try:
        return await Player.objects.aget(discord_id=interaction.user.id)
    except Player.DoesNotExist:
        send = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message
        await send(
            embed=error_embed(
                "No Account",
                f"You don't have a player profile yet. Catch some {bot_settings.plural_collectible_name} first!",
            ),
            ephemeral=True,
        )
        return None


# ── Pricing ───────────────────────────────────────────────────────────────────

async def _special_quicksell_override(special_id: int | None) -> float | None:
    """Return the per-special quicksell multiplier if one is set and enabled, else None."""
    if special_id is None:
        return None
    try:
        sbonus = await SpecialEconomyBonus.objects.aget(special_id=special_id)
    except SpecialEconomyBonus.DoesNotExist:
        return None
    return sbonus.quicksell_multiplier if sbonus.quicksell_multiplier_enabled else None


async def _special_passive_override(special_id: int | None) -> float | None:
    """Return the per-special passive multiplier if one is set and enabled, else None."""
    if special_id is None:
        return None
    try:
        sbonus = await SpecialEconomyBonus.objects.aget(special_id=special_id)
    except SpecialEconomyBonus.DoesNotExist:
        return None
    return sbonus.passive_multiplier if sbonus.passive_multiplier_enabled else None


async def compute_quicksell_price_for(
    bi: BallInstance,
    cfg: EconomySettings,
    price_cache: dict[int, tuple[int, int]] | None = None,
) -> int:
    """
    Quick sell price for a single ball instance.

    price_cache, if given, is a pre-fetched {ball_id: (min_price, max_price)} map
    (used by BulkQuicksellView to avoid one BallSellPrice query per ball). Falls
    back to a single BallSellPrice lookup when no cache is passed.
    """
    if price_cache is not None:
        ball_min, ball_max = price_cache.get(bi.ball_id, (None, None))
    else:
        try:
            price_cfg = await BallSellPrice.objects.aget(ball_id=bi.ball_id)
            ball_min, ball_max = price_cfg.min_price, price_cfg.max_price
        except BallSellPrice.DoesNotExist:
            ball_min, ball_max = None, None

    has_special = bi.special_id is not None
    override = await _special_quicksell_override(bi.special_id) if has_special else None

    if override is not None:
        # Same roll + high-roll-bonus order as EconomySettings.compute_quicksell_price,
        # but with the per-special multiplier in place of the global one.
        min_price = ball_min if ball_min is not None else cfg.quicksell_default_min
        max_price = ball_max if ball_max is not None else cfg.quicksell_default_max
        price = random.randint(min_price, max_price)
        price = int(price * override)
        if bi.attack_bonus > 0 and bi.health_bonus > 0:
            price += cfg.quicksell_high_roll_bonus
        return max(1, price)

    return cfg.compute_quicksell_price(
        rarity=bi.ball.rarity,
        has_special=has_special,
        attack_bonus=bi.attack_bonus,
        health_bonus=bi.health_bonus,
        ball_min=ball_min,
        ball_max=ball_max,
    )


def estimate_quicksell_range(
    bi: BallInstance,
    cfg: EconomySettings,
    price_cache: dict[int, tuple[int, int]],
) -> tuple[int, int]:
    """
    Display-only min/max estimate for a ball, without rolling — used by
    BulkQuicksellView's select options. Does NOT apply per-special overrides
    (those require a DB lookup); it uses the global special multiplier as a
    reasonable estimate.
    """
    ball_min, ball_max = price_cache.get(bi.ball_id, (cfg.quicksell_default_min, cfg.quicksell_default_max))
    est_min, est_max = ball_min, ball_max
    if bi.special_id:
        est_min = int(est_min * cfg.quicksell_special_multiplier)
        est_max = int(est_max * cfg.quicksell_special_multiplier)
    return est_min, est_max


async def compute_passive_tick_for(bi: BallInstance, cfg: EconomySettings) -> int:
    """Passive income tick amount for a single ball instance, with special override applied."""
    tick = cfg.compute_passive_tick(bi.ball.rarity)
    override = await _special_passive_override(bi.special_id) if bi.special_id else None
    if override is not None:
        tick = int(tick * override)
    return tick
