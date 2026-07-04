from __future__ import annotations

import logging
import random
from datetime import timedelta
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks
from django.utils import timezone

from ballsdex.core.utils.transformers import BallInstanceTransformer
from ballsdex.packages.countryballs.countryball import BallSpawnView
from bd_models.models import BallInstance, Player
from settings.models import settings
from settings.utils import format_currency

from ..models import BallListing, BallSellPrice, EconomySettings, PassiveIncomePool

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

log = logging.getLogger(__name__)

BallInstanceTransform = app_commands.Transform[BallInstance, BallInstanceTransformer]

# Cache the settings in memory — refreshed on cog load and when admin saves
_settings_cache: EconomySettings | None = None


async def get_economy_settings() -> EconomySettings | None:
    global _settings_cache
    if _settings_cache is None:
        _settings_cache = await EconomySettings.objects.afirst()
    return _settings_cache


def invalidate_settings_cache() -> None:
    global _settings_cache
    _settings_cache = None


# ── Helpers ──────────────────────────────────────────────────────────────────

def currency_enabled() -> bool:
    return bool(settings.currency_name)


def fmt(amount: int) -> str:
    """Format an amount using the bot's currency settings."""
    return format_currency(amount)


def disabled_embed(reason: str) -> discord.Embed:
    return discord.Embed(
        title="Command Disabled",
        description=reason,
        color=discord.Color.red(),
    )


def error_embed(title: str, description: str) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=discord.Color.red())


# ── Monkeypatch ───────────────────────────────────────────────────────────────

_original_catch_ball = BallSpawnView.catch_ball


async def _patched_catch_ball(
    self: BallSpawnView,
    user: discord.User | discord.Member,
    *,
    player: Player | None,
    guild: discord.Guild | None,
):
    """
    Replacement for BallSpawnView.catch_ball that adds catch income.
    Patches at the class level so it works regardless of how the cog spawns balls.
    """
    ball, is_new = await _original_catch_ball(self, user, player=player, guild=guild)

    if not currency_enabled():
        return ball, is_new

    cfg = await get_economy_settings()
    if cfg is None or not cfg.catch_income_enabled:
        return ball, is_new

    # player is set by the original catch_ball — re-fetch from ball if needed
    if player is None:
        try:
            player = await Player.objects.aget(discord_id=user.id)
        except Player.DoesNotExist:
            return ball, is_new

    earned = cfg.compute_catch_income(
        rarity=ball.ball.rarity,
        has_special=ball.special_id is not None,
    )

    await player.add_money(earned)
    log.debug(
        "Catch income: player %s earned %s for catching %s",
        user.id, earned, ball.ball.country,
    )

    return ball, is_new


class Economy(commands.Cog):
    """
    Economy package — catch income, quick sell, player market, passive income.
    Currency must be configured in bot settings for any commands to work.
    """

    def __init__(self, bot: "BallsDexBot") -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        # Apply monkeypatch immediately — no DB access here so cog always loads
        # and commands always register with Discord successfully.
        BallSpawnView.catch_ball = _patched_catch_ball  # type: ignore[method-assign]
        log.info("Economy: BallSpawnView.catch_ball monkeypatched for catch income.")
        # All DB work happens in _post_init after bot is ready
        self.bot.loop.create_task(self._post_init())

    async def _post_init(self) -> None:
        """Runs after bot is ready. Safe to do DB work here."""
        await self.bot.wait_until_ready()
        invalidate_settings_cache()

        if not currency_enabled():
            log.warning(
                "Economy: currency_name is not set in bot settings. "
                "All economy commands will be disabled until it is configured."
            )

        try:
            cfg = await get_economy_settings()
            if cfg is None:
                from ..models import EconomySettings as ES
                cfg = await ES.objects.acreate()
                log.info("Economy: created default EconomySettings record.")
        except Exception:
            log.error(
                "Economy: failed to read EconomySettings — table may not exist. "
                "Run: docker compose run --rm migration python3 -m django migrate economy zero --fake"
                " && docker compose run --rm migration python3 -m django migrate economy",
                exc_info=True,
            )
            cfg = None

        interval = cfg.passive_interval_minutes if cfg else 10
        self.passive_income_task.change_interval(minutes=interval)
        self.passive_income_task.start()
        self.expire_listings_task.start()

    def cog_unload(self) -> None:
        # Restore original method
        BallSpawnView.catch_ball = _original_catch_ball  # type: ignore[method-assign]
        self.passive_income_task.cancel()
        self.expire_listings_task.cancel()
        log.info("Economy: BallSpawnView.catch_ball restored.")

    # ── Guards ────────────────────────────────────────────────────────────────

    async def _guard_currency(self, interaction: discord.Interaction) -> bool:
        if not currency_enabled():
            await interaction.response.send_message(
                embed=disabled_embed(
                    "Currency is not enabled on this bot.\n"
                    "An administrator must set a **Currency Name** in bot settings first."
                ),
                ephemeral=True,
            )
            return False
        return True

    async def _guard_command(
        self,
        interaction: discord.Interaction,
        enabled: bool,
        command_name: str,
    ) -> bool:
        if not enabled:
            await interaction.response.send_message(
                embed=disabled_embed(f"The `{command_name}` command is currently disabled."),
                ephemeral=True,
            )
            return False
        return True

    async def _get_player(self, interaction: discord.Interaction) -> Player | None:
        try:
            return await Player.objects.aget(discord_id=interaction.user.id)
        except Player.DoesNotExist:
            send = (
                interaction.followup.send
                if interaction.response.is_done()
                else interaction.response.send_message
            )
            await send(
                embed=error_embed(
                    "No Account",
                    f"You don't have a player profile yet. "
                    f"Catch some {settings.plural_collectible_name} first!",
                ),
                ephemeral=True,
            )
            return None

    # ── Background tasks ──────────────────────────────────────────────────────

    @tasks.loop(minutes=10)
    async def passive_income_task(self) -> None:
        if not currency_enabled():
            return
        cfg = await get_economy_settings()
        if cfg is None or not cfg.passive_enabled:
            return

        async for player in Player.objects.all():
            total = 0
            async for bi in BallInstance.objects.filter(
                player=player, deleted=False
            ).select_related("ball"):
                if random.random() < cfg.passive_chance:
                    total += cfg.compute_passive_tick(bi.ball.rarity)

            if total > 0:
                pool, _ = await PassiveIncomePool.objects.aget_or_create(
                    player=player, defaults={"pending": 0, "total_earned": 0}
                )
                pool.pending += total
                pool.total_earned += total
                pool.last_tick = timezone.now()
                await pool.asave(update_fields=["pending", "total_earned", "last_tick"])



    @tasks.loop(minutes=15)
    async def expire_listings_task(self) -> None:
        now = timezone.now()
        expired = await BallListing.objects.filter(
            sold=False, expires_at__lte=now
        ).select_related("ball_instance", "seller").alist()

        for listing in expired:
            # Ball stays with the seller since they still own it — just delete the listing
            await listing.adelete()
            log.info(
                "Economy: listing #%d expired, ball %d returned to player %d",
                listing.pk,
                listing.ball_instance_id,
                listing.seller.discord_id,
            )



    # ── Command group ─────────────────────────────────────────────────────────

    economy_group = app_commands.Group(
        name="economy",
        description="Currency commands — earn, sell and manage your balance.",
    )

    # ── /economy balance ──────────────────────────────────────────────────────

    # ── /economy quicksell ────────────────────────────────────────────────────

    @economy_group.command(name="quicksell")
    @app_commands.describe(ball="The ball you want to sell to the system instantly.")
    async def quicksell(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        ball: BallInstanceTransform,
    ) -> None:
        """
        Sell a ball instantly to the system for currency.
        Price is set per-ball in the admin panel, with a default fallback.
        """
        if not await self._guard_currency(interaction):
            return

        cfg = await get_economy_settings()
        if cfg is None:
            await interaction.response.send_message(
                embed=error_embed("Not Configured", "Economy settings not found. Contact an administrator."),
                ephemeral=True,
            )
            return
        if not await self._guard_command(interaction, cfg.quicksell_enabled, "/economy quicksell"):
            return

        player = await self._get_player(interaction)
        if player is None:
            return

        if ball.player_id != player.pk:
            await interaction.response.send_message(
                embed=error_embed("Not Your Ball", f"You don't own that {settings.collectible_name}."),
                ephemeral=True,
            )
            return

        if ball.favorite:
            await interaction.response.send_message(
                embed=error_embed(
                    "Ball is Favourited",
                    f"Unfavourite this {settings.collectible_name} before selling it.",
                ),
                ephemeral=True,
            )
            return

        if await BallListing.objects.filter(ball_instance=ball, sold=False).aexists():
            await interaction.response.send_message(
                embed=error_embed(
                    "Ball is Listed",
                    f"That {settings.collectible_name} is currently on the market. "
                    "Use `/economy delist` to remove it first.",
                ),
                ephemeral=True,
            )
            return

        # Fetch per-ball price config if it exists
        try:
            price_config = await BallSellPrice.objects.aget(ball=ball.ball)
            ball_min = price_config.min_price
            ball_max = price_config.max_price
        except BallSellPrice.DoesNotExist:
            ball_min = None
            ball_max = None

        await ball.arefresh_from_db()
        price = cfg.compute_quicksell_price(
            rarity=ball.ball.rarity,
            has_special=ball.special_id is not None,
            attack_bonus=ball.attack_bonus,
            health_bonus=ball.health_bonus,
            ball_min=ball_min,
            ball_max=ball_max,
        )

        special_name = ball.specialcard.name if ball.special_id and ball.specialcard else None

        # Delete the ball — mark as deleted, same as core trade logic
        ball.deleted = True
        await ball.asave(update_fields=["deleted"])
        await player.add_money(price)

        embed = discord.Embed(
            title=f"{settings.collectible_name.title()} Sold",
            color=discord.Color.green(),
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.add_field(
            name=settings.collectible_name.title(),
            value=f"**{ball.ball.country}**" + (f"\n*{special_name}*" if special_name else ""),
            inline=True,
        )
        embed.add_field(
            name="Stats",
            value=f"ATK: {ball.attack_bonus:+}% | HP: {ball.health_bonus:+}%",
            inline=True,
        )
        embed.add_field(name="You Received", value=fmt(price), inline=True)
        embed.add_field(name="New Balance", value=fmt(player.money + price), inline=False)
        embed.set_footer(
            text=(
                "Quick sell prices are set per ball in the admin panel. "
                "Use /economy list to sell to other players for potentially more."
            )
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /economy list ─────────────────────────────────────────────────────────

    @economy_group.command(name="list")
    @app_commands.describe(
        ball="The ball you want to list on the market.",
        price="The price in currency you want to sell it for.",
    )
    async def list_ball(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        ball: BallInstanceTransform,
        price: int,
    ) -> None:
        """
        List a ball on the player market at a price you set.
        Other players can browse with /economy listings and buy with /economy buy.
        """
        if not await self._guard_currency(interaction):
            return

        cfg = await get_economy_settings()
        if cfg is None:
            await interaction.response.send_message(
                embed=error_embed("Not Configured", "Economy settings not found."), ephemeral=True
            )
            return
        if not await self._guard_command(interaction, cfg.listings_enabled, "/economy list"):
            return

        if price < cfg.listing_min_price:
            await interaction.response.send_message(
                embed=error_embed(
                    "Price Too Low",
                    f"Minimum listing price is {fmt(cfg.listing_min_price)}.",
                ),
                ephemeral=True,
            )
            return

        player = await self._get_player(interaction)
        if player is None:
            return

        if ball.player_id != player.pk:
            await interaction.response.send_message(
                embed=error_embed("Not Your Ball", f"You don't own that {settings.collectible_name}."),
                ephemeral=True,
            )
            return

        if ball.favorite:
            await interaction.response.send_message(
                embed=error_embed(
                    "Ball is Favourited",
                    f"Unfavourite this {settings.collectible_name} before listing it.",
                ),
                ephemeral=True,
            )
            return

        if await BallListing.objects.filter(ball_instance=ball, sold=False).aexists():
            await interaction.response.send_message(
                embed=error_embed("Already Listed", f"That {settings.collectible_name} is already on the market."),
                ephemeral=True,
            )
            return

        active_count = await BallListing.objects.filter(seller=player, sold=False).acount()
        if active_count >= cfg.listing_max_per_player:
            await interaction.response.send_message(
                embed=error_embed(
                    "Listing Limit Reached",
                    f"You can only have **{cfg.listing_max_per_player}** active listings at once. "
                    "Use `/economy delist` to remove one first.",
                ),
                ephemeral=True,
            )
            return

        expires_at = timezone.now() + timedelta(hours=cfg.listing_expiry_hours)
        listing = await BallListing.objects.acreate(
            seller=player,
            ball_instance=ball,
            price=price,
            expires_at=expires_at,
        )

        special_name = ball.specialcard.name if ball.special_id and ball.specialcard else None
        fee_amount = int(price * cfg.listing_platform_fee)

        embed = discord.Embed(
            title=f"{settings.collectible_name.title()} Listed",
            color=discord.Color.blurple(),
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.add_field(
            name=settings.collectible_name.title(),
            value=f"**{ball.ball.country}**" + (f"\n*{special_name}*" if special_name else ""),
            inline=True,
        )
        embed.add_field(
            name="Stats",
            value=f"ATK: {ball.attack_bonus:+}% | HP: {ball.health_bonus:+}%",
            inline=True,
        )
        embed.add_field(name="Listed Price", value=fmt(price), inline=True)
        embed.add_field(name="You'll Receive", value=fmt(price - fee_amount), inline=True)
        embed.add_field(name="Platform Fee", value=f"{cfg.listing_platform_fee * 100:.0f}% ({fmt(fee_amount)})", inline=True)
        embed.add_field(name="Expires", value=f"<t:{int(expires_at.timestamp())}:R>", inline=True)
        embed.add_field(name="Listing ID", value=f"`#{listing.pk}`", inline=True)
        embed.set_footer(text="Use /economy delist to remove this listing at any time.")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /economy listings ─────────────────────────────────────────────────────

    @economy_group.command(name="listings")
    @app_commands.describe(page="Page number to view (25 listings per page).")
    async def listings(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        page: int = 1,
    ) -> None:
        """Browse all active ball listings on the player market."""
        if not await self._guard_currency(interaction):
            return

        cfg = await get_economy_settings()
        if cfg and not await self._guard_command(interaction, cfg.listings_enabled, "/economy listings"):
            return

        await interaction.response.defer(ephemeral=True)

        per_page = 10
        offset = (max(1, page) - 1) * per_page

        total = await BallListing.objects.filter(sold=False).acount()
        if total == 0:
            await interaction.followup.send(
                embed=discord.Embed(
                    title=f"{settings.plural_collectible_name.title()} Market",
                    description=(
                        "No active listings right now.\n"
                        f"Use `/economy list` to sell your {settings.plural_collectible_name}!"
                    ),
                    color=discord.Color.blurple(),
                ),
                ephemeral=True,
            )
            return

        active = await BallListing.objects.filter(
            sold=False
        ).select_related(
            "ball_instance__ball",
            "ball_instance__special",
            "seller",
        ).order_by("price")[offset:offset + per_page].alist()

        total_pages = max(1, (total + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))

        embed = discord.Embed(
            title=f"{settings.plural_collectible_name.title()} Market",
            description=(
                f"**{total}** active listing{'s' if total != 1 else ''}  •  "
                f"Page **{page}/{total_pages}**  •  Sorted by price\n"
                f"Use `/economy buy <id>` to purchase  •  `/economy listings <page>` to browse"
            ),
            color=discord.Color.blurple(),
        )

        for listing in active:
            bi = listing.ball_instance
            special_tag = f" *[{bi.specialcard.name}]*" if bi.special_id and bi.specialcard else ""
            atk = f"{bi.attack_bonus:+}%" if bi.attack_bonus != 0 else "±0%"
            hp = f"{bi.health_bonus:+}%" if bi.health_bonus != 0 else "±0%"
            embed.add_field(
                name=f"`#{listing.pk}` {bi.ball.country}{special_tag}",
                value=(
                    f"💰 **{fmt(listing.price)}**\n"
                    f"ATK {atk} | HP {hp}\n"
                    f"<@{listing.seller.discord_id}>\n"
                    f"Expires <t:{int(listing.expires_at.timestamp())}:R>"
                ),
                inline=True,
            )

        embed.set_footer(
            text=f"Showing {offset + 1}–{min(offset + per_page, total)} of {total} listings."
        )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /economy buy ──────────────────────────────────────────────────────────

    @economy_group.command(name="buy")
    @app_commands.describe(listing_id="The listing ID from /economy listings.")
    async def buy(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        listing_id: int,
    ) -> None:
        """Purchase a ball from another player's listing."""
        if not await self._guard_currency(interaction):
            return

        cfg = await get_economy_settings()
        if cfg and not await self._guard_command(interaction, cfg.listings_enabled, "/economy buy"):
            return

        await interaction.response.defer(ephemeral=True)

        try:
            listing = await BallListing.objects.select_related(
                "ball_instance__ball",
                "ball_instance__special",
                "seller",
            ).aget(pk=listing_id, sold=False)
        except BallListing.DoesNotExist:
            await interaction.followup.send(
                embed=error_embed(
                    "Listing Not Found",
                    "That listing doesn't exist or has already been sold.",
                ),
                ephemeral=True,
            )
            return

        buyer = await self._get_player(interaction)
        if buyer is None:
            return

        if listing.seller_id == buyer.pk:
            await interaction.followup.send(
                embed=error_embed(
                    "Can't Buy Your Own",
                    "You can't buy your own listing. Use `/economy delist` to remove it.",
                ),
                ephemeral=True,
            )
            return

        await buyer.arefresh_from_db(fields=["money"])
        if not buyer.can_afford(listing.price):
            await interaction.followup.send(
                embed=error_embed(
                    "Insufficient Funds",
                    f"Your balance: **{fmt(buyer.money)}**\n"
                    f"Listing price: **{fmt(listing.price)}**\n\n"
                    f"You need **{fmt(listing.price - buyer.money)}** more.",
                ),
                ephemeral=True,
            )
            return

        fee = int(listing.price * (cfg.listing_platform_fee if cfg else 0.05))
        seller_receives = listing.price - fee
        seller = await Player.objects.aget(pk=listing.seller_id)

        # Transfer money
        await buyer.remove_money(listing.price)
        await seller.add_money(seller_receives)

        # Transfer ball ownership
        bi = listing.ball_instance
        bi.player = buyer
        bi.trade_player_id = listing.seller_id
        await bi.asave(update_fields=["player", "trade_player"])

        # Mark listing as sold
        listing.sold = True
        listing.buyer = buyer
        listing.sold_at = timezone.now()
        await listing.asave(update_fields=["sold", "buyer", "sold_at"])

        special_tag = (
            f"\n*{bi.specialcard.name}*" if bi.special_id and bi.specialcard else ""
        )
        atk = f"{bi.attack_bonus:+}%"
        hp = f"{bi.health_bonus:+}%"

        embed = discord.Embed(
            title="Purchase Complete! 🎉",
            color=discord.Color.green(),
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.add_field(
            name=f"{settings.collectible_name.title()} Acquired",
            value=f"**{bi.ball.country}**{special_tag}",
            inline=True,
        )
        embed.add_field(
            name="Stats",
            value=f"ATK: {atk} | HP: {hp}",
            inline=True,
        )
        embed.add_field(name="\u200b", value="\u200b", inline=True)
        embed.add_field(name="You Paid", value=fmt(listing.price), inline=True)
        embed.add_field(name="New Balance", value=fmt(buyer.money - listing.price), inline=True)
        embed.set_footer(
            text=f"Platform fee: {fmt(fee)} ({(cfg.listing_platform_fee if cfg else 0.05) * 100:.0f}%). "
                 f"Seller received: {fmt(seller_receives)}."
        )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /economy delist ───────────────────────────────────────────────────────

    @economy_group.command(name="delist")
    @app_commands.describe(listing_id="The listing ID to remove.")
    async def delist(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        listing_id: int,
    ) -> None:
        """Remove one of your active listings. The ball stays in your collection."""
        if not await self._guard_currency(interaction):
            return

        player = await self._get_player(interaction)
        if player is None:
            return

        try:
            listing = await BallListing.objects.select_related(
                "ball_instance__ball"
            ).aget(pk=listing_id, sold=False, seller=player)
        except BallListing.DoesNotExist:
            await interaction.response.send_message(
                embed=error_embed(
                    "Listing Not Found",
                    "That listing doesn't exist or doesn't belong to you.",
                ),
                ephemeral=True,
            )
            return

        bi = listing.ball_instance
        await listing.adelete()

        embed = discord.Embed(
            title="Listing Removed",
            color=discord.Color.orange(),
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.add_field(
            name=f"{settings.collectible_name.title()} Returned",
            value=f"**{bi.ball.country}**",
            inline=True,
        )
        embed.set_footer(
            text=f"Your {settings.collectible_name} is still in your collection."
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /economy mylistings ───────────────────────────────────────────────────

    @economy_group.command(name="mylistings")
    async def mylistings(self, interaction: discord.Interaction["BallsDexBot"]) -> None:
        """View all of your active listings on the market."""
        if not await self._guard_currency(interaction):
            return

        player = await self._get_player(interaction)
        if player is None:
            return

        active = await BallListing.objects.filter(
            seller=player, sold=False
        ).select_related("ball_instance__ball", "ball_instance__special").order_by("price").alist()

        if not active:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Your Listings",
                    description=(
                        "You have no active listings.\n"
                        f"Use `/economy list <ball> <price>` to list a {settings.collectible_name}!"
                    ),
                    color=discord.Color.blurple(),
                ),
                ephemeral=True,
            )
            return

        cfg = await get_economy_settings()
        max_listings = cfg.listing_max_per_player if cfg else 10

        embed = discord.Embed(
            title="Your Listings",
            description=(
                f"**{len(active)}/{max_listings}** active listing{'s' if len(active) != 1 else ''}.\n"
                "Use `/economy delist <id>` to remove a listing."
            ),
            color=discord.Color.blurple(),
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)

        for listing in active:
            bi = listing.ball_instance
            special_tag = f" *[{bi.specialcard.name}]*" if bi.special_id and bi.specialcard else ""
            embed.add_field(
                name=f"`#{listing.pk}` {bi.ball.country}{special_tag}",
                value=(
                    f"💰 **{fmt(listing.price)}**\n"
                    f"ATK: {bi.attack_bonus:+}% | HP: {bi.health_bonus:+}%\n"
                    f"Expires <t:{int(listing.expires_at.timestamp())}:R>"
                ),
                inline=True,
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /economy pending ──────────────────────────────────────────────────────

    @economy_group.command(name="pending")
    async def pending(self, interaction: discord.Interaction["BallsDexBot"]) -> None:
        """Check how much passive income is waiting to be claimed."""
        if not await self._guard_currency(interaction):
            return

        cfg = await get_economy_settings()
        if cfg and not await self._guard_command(interaction, cfg.pending_enabled, "/economy pending"):
            return

        player = await self._get_player(interaction)
        if player is None:
            return

        try:
            pool = await PassiveIncomePool.objects.aget(player=player)
            pending = pool.pending
            last_tick = pool.last_tick
            total_earned = pool.total_earned
        except PassiveIncomePool.DoesNotExist:
            pending = 0
            last_tick = None
            total_earned = 0

        ball_count = await BallInstance.objects.filter(player=player, deleted=False).acount()

        embed = discord.Embed(
            title="Passive Income",
            color=discord.Color.gold(),
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.add_field(name="⏳ Ready to Claim", value=fmt(pending), inline=True)
        embed.add_field(name="💰 Current Balance", value=fmt(player.money), inline=True)
        embed.add_field(name="📦 Balls Generating", value=str(ball_count), inline=True)
        if last_tick:
            embed.add_field(name="Last Tick", value=f"<t:{int(last_tick.timestamp())}:R>", inline=True)
        if total_earned:
            embed.add_field(name="Total Ever Earned", value=fmt(total_earned), inline=True)
        if cfg:
            embed.set_footer(
                text=(
                    f"Each {settings.collectible_name} has a {cfg.passive_chance * 100:.0f}% chance "
                    f"to generate {cfg.passive_min}–{cfg.passive_max}+ {settings.currency_plural} "
                    f"every {cfg.passive_interval_minutes} minutes. "
                    f"Use /economy claim to collect."
                )
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /economy claim ────────────────────────────────────────────────────────

    @economy_group.command(name="claim")
    async def claim(self, interaction: discord.Interaction["BallsDexBot"]) -> None:
        """Collect all accumulated passive income into your balance."""
        if not await self._guard_currency(interaction):
            return

        cfg = await get_economy_settings()
        if cfg and not await self._guard_command(interaction, cfg.claim_enabled, "/economy claim"):
            return

        player = await self._get_player(interaction)
        if player is None:
            return

        try:
            pool = await PassiveIncomePool.objects.aget(player=player)
        except PassiveIncomePool.DoesNotExist:
            await interaction.response.send_message(
                embed=error_embed(
                    "Nothing to Claim",
                    f"You have no passive income yet. "
                    f"Own some {settings.plural_collectible_name} and wait for the next tick!",
                ),
                ephemeral=True,
            )
            return

        if pool.pending == 0:
            await interaction.response.send_message(
                embed=error_embed(
                    "Nothing to Claim",
                    "No passive income to claim right now. Check back later!",
                ),
                ephemeral=True,
            )
            return

        earned = pool.pending
        pool.pending = 0
        await pool.asave(update_fields=["pending"])
        await player.add_money(earned)

        embed = discord.Embed(
            title=f"{settings.currency_name} Claimed! 💰",
            color=discord.Color.green(),
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.add_field(name="Claimed", value=fmt(earned), inline=True)
        embed.add_field(name="New Balance", value=fmt(player.money + earned), inline=True)
        embed.set_footer(
            text=f"Your {settings.plural_collectible_name} continue generating "
                 f"passive {settings.currency_plural} automatically."
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)
