from __future__ import annotations

import logging
import random
from datetime import timedelta
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks
from django.db.models import Sum
from django.utils import timezone

from ballsdex.core.utils.transformers import BallInstanceTransformer
from ballsdex.core.utils.utils import is_staff
from ballsdex.packages.countryballs.countryball import BallSpawnView
from bd_models.models import BallInstance, Player
from settings.models import settings
from settings.utils import format_currency

from ..models import BallListing, BallSellPrice, BallShopPrice, EconomySettings, PassiveIncomePool

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

log = logging.getLogger(__name__)

BallInstanceTransform = app_commands.Transform[BallInstance, BallInstanceTransformer]

# ── Monkeypatch ───────────────────────────────────────────────────────────────

_original_catch_ball = BallSpawnView.catch_ball


async def _patched_catch_ball(
    self: BallSpawnView,
    user: discord.User | discord.Member,
    *,
    player: Player | None,
    guild: discord.Guild | None,
):
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

    earned = random.randint(cfg.catch_base_min, cfg.catch_base_max)
    earned += int(ball.ball.rarity * cfg.catch_rarity_multiplier)
    if ball.special_id is not None:
        earned += cfg.catch_special_bonus

    await player.add_money(earned)
    log.debug("Catch income: player %s earned %d (%s)", user.id, earned, ball.ball.country)

    return ball, is_new


# ── Helpers ───────────────────────────────────────────────────────────────────

def fmt(amount: int) -> str:
    return format_currency(amount)


def error_embed(title: str, desc: str) -> discord.Embed:
    return discord.Embed(title=title, description=desc, color=discord.Color.red())


def disabled_embed(reason: str) -> discord.Embed:
    return discord.Embed(title="Command Disabled", description=reason, color=discord.Color.red())


# ── Cog ───────────────────────────────────────────────────────────────────────

class Economy(commands.GroupCog, group_name="economy"):
    """Economy — earn currency, sell balls and manage your balance."""

    admin = app_commands.Group(name="admin", description="Economy management tools for staff.")

    def __init__(self, bot: "BallsDexBot") -> None:
        self.bot = bot
        super().__init__()

    async def cog_load(self) -> None:
        BallSpawnView.catch_ball = _patched_catch_ball  # type: ignore[method-assign]
        log.info("Economy: monkeypatch applied.")
        self.bot.loop.create_task(self._post_init())

    async def _post_init(self) -> None:
        await self.bot.wait_until_ready()

        if not settings.currency_enabled:
            log.warning("Economy: currency not configured in settings.")

        try:
            cfg = await EconomySettings.objects.afirst()
            if cfg is None:
                cfg = await EconomySettings.objects.acreate()
                log.info("Economy: created default EconomySettings.")
        except Exception:
            log.error("Economy: failed to load settings — run migrations.", exc_info=True)
            cfg = None

        interval = cfg.passive_interval_minutes if cfg else 10
        self.passive_income_task.change_interval(minutes=interval)
        self.passive_income_task.start()
        self.expire_listings_task.start()

    def cog_unload(self) -> None:
        BallSpawnView.catch_ball = _original_catch_ball  # type: ignore[method-assign]
        self.passive_income_task.cancel()
        self.expire_listings_task.cancel()

    # ── Guards ────────────────────────────────────────────────────────────────

    async def _guard_currency(self, interaction: discord.Interaction) -> bool:
        if not settings.currency_enabled:
            await interaction.response.send_message(
                embed=disabled_embed(
                    "Currency is not enabled. An administrator must set a currency name in bot settings."
                ),
                ephemeral=True,
            )
            return False
        return True

    async def _guard_command(self, interaction: discord.Interaction, enabled: bool, name: str) -> bool:
        if not enabled:
            await interaction.response.send_message(
                embed=disabled_embed(f"The `{name}` command is currently disabled."),
                ephemeral=True,
            )
            return False
        return True

    async def _get_player(self, interaction: discord.Interaction) -> Player | None:
        try:
            return await Player.objects.aget(discord_id=interaction.user.id)
        except Player.DoesNotExist:
            send = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message
            await send(
                embed=error_embed("No Account", f"You don't have a player profile yet. Catch some {settings.plural_collectible_name} first!"),
                ephemeral=True,
            )
            return None

    async def _get_cfg(self) -> EconomySettings | None:
        try:
            return await EconomySettings.objects.afirst()
        except Exception:
            return None

    # ── Background tasks ──────────────────────────────────────────────────────

    @tasks.loop(minutes=10)
    async def passive_income_task(self) -> None:
        if not settings.currency_enabled:
            return
        cfg = await self._get_cfg()
        if cfg is None or not cfg.passive_enabled:
            return

        now = timezone.now()
        async for player in Player.objects.all():
            total = 0
            async for bi in BallInstance.objects.filter(player=player, deleted=False).select_related("ball"):
                if random.random() < cfg.passive_chance:
                    base = random.randint(cfg.passive_min, cfg.passive_max)
                    total += max(1, base + int(bi.ball.rarity * cfg.passive_rarity_bonus))
            if total > 0:
                pool, _ = await PassiveIncomePool.objects.aget_or_create(
                    player=player, defaults={"pending": 0, "total_earned": 0}
                )
                pool.pending += total
                pool.total_earned += total
                pool.last_tick = now
                await pool.asave(update_fields=["pending", "total_earned", "last_tick"])

    @tasks.loop(minutes=15)
    async def expire_listings_task(self) -> None:
        now = timezone.now()
        async for listing in BallListing.objects.filter(sold=False, expires_at__lte=now).select_related("ball_instance", "seller"):
            await listing.adelete()
            log.info("Listing #%d expired.", listing.pk)

    # ── /economy quicksell ────────────────────────────────────────────────────

    @app_commands.command(name="quicksell")
    @app_commands.describe(ball="The ball you want to sell to the system instantly.")
    async def quicksell(self, interaction: discord.Interaction["BallsDexBot"], ball: BallInstanceTransform) -> None:
        """Sell a ball instantly to the system. Price is set per-ball in the admin panel."""
        if not await self._guard_currency(interaction):
            return
        cfg = await self._get_cfg()
        if cfg is None or not cfg.quicksell_enabled:
            await interaction.response.send_message(embed=disabled_embed("Quicksell is currently disabled."), ephemeral=True)
            return

        player = await self._get_player(interaction)
        if player is None:
            return

        if ball.player_id != player.pk:
            await interaction.response.send_message(embed=error_embed("Not Your Ball", f"You don't own that {settings.collectible_name}."), ephemeral=True)
            return
        if ball.favorite:
            await interaction.response.send_message(embed=error_embed("Ball Favourited", "Unfavourite this ball before selling."), ephemeral=True)
            return
        if await BallListing.objects.filter(ball_instance=ball, sold=False).aexists():
            await interaction.response.send_message(embed=error_embed("Ball Listed", "Delist this ball before quick selling."), ephemeral=True)
            return

        try:
            price_cfg = await BallSellPrice.objects.aget(ball=ball.ball)
            ball_min, ball_max = price_cfg.min_price, price_cfg.max_price
        except BallSellPrice.DoesNotExist:
            ball_min, ball_max = cfg.quicksell_default_min, cfg.quicksell_default_max

        await ball.arefresh_from_db()
        price = random.randint(ball_min, ball_max)
        if ball.special_id is not None:
            price = int(price * cfg.quicksell_special_multiplier)
        if ball.attack_bonus > 0 and ball.health_bonus > 0:
            price += cfg.quicksell_high_roll_bonus
        price = max(1, price)

        special_name = ball.specialcard.name if ball.special_id and ball.specialcard else None
        ball.deleted = True
        await ball.asave(update_fields=["deleted"])
        await player.add_money(price)
        await player.arefresh_from_db(fields=["money"])

        embed = discord.Embed(title=f"{settings.collectible_name.title()} Sold", color=discord.Color.green())
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.add_field(name=settings.collectible_name.title(), value=f"**{ball.ball.country}**" + (f"\n*{special_name}*" if special_name else ""), inline=True)
        embed.add_field(name="Stats", value=f"ATK: {ball.attack_bonus:+}% | HP: {ball.health_bonus:+}%", inline=True)
        embed.add_field(name="You Received", value=fmt(price), inline=True)
        embed.add_field(name="New Balance", value=fmt(player.money), inline=False)
        embed.set_footer(text="Use /economy list to sell to other players instead.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /economy list ─────────────────────────────────────────────────────────

    @app_commands.command(name="list")
    @app_commands.describe(ball="The ball to list on the market.", price="The price you want to sell it for.")
    async def list_ball(self, interaction: discord.Interaction["BallsDexBot"], ball: BallInstanceTransform, price: int) -> None:
        """List a ball on the player market at a price you set."""
        if not await self._guard_currency(interaction):
            return
        cfg = await self._get_cfg()
        if cfg is None or not cfg.listings_enabled:
            await interaction.response.send_message(embed=disabled_embed("The player market is currently disabled."), ephemeral=True)
            return
        if price < (cfg.listing_min_price if cfg else 1):
            await interaction.response.send_message(embed=error_embed("Price Too Low", f"Minimum listing price is {fmt(cfg.listing_min_price)}."), ephemeral=True)
            return

        player = await self._get_player(interaction)
        if player is None:
            return

        if ball.player_id != player.pk:
            await interaction.response.send_message(embed=error_embed("Not Your Ball", f"You don't own that {settings.collectible_name}."), ephemeral=True)
            return
        if ball.favorite:
            await interaction.response.send_message(embed=error_embed("Ball Favourited", "Unfavourite this ball before listing."), ephemeral=True)
            return
        if await BallListing.objects.filter(ball_instance=ball, sold=False).aexists():
            await interaction.response.send_message(embed=error_embed("Already Listed", f"That {settings.collectible_name} is already on the market."), ephemeral=True)
            return

        active_count = await BallListing.objects.filter(seller=player, sold=False).acount()
        if active_count >= cfg.listing_max_per_player:
            await interaction.response.send_message(embed=error_embed("Listing Limit", f"You can only have {cfg.listing_max_per_player} active listings. Use `/economy delist` to remove one."), ephemeral=True)
            return

        expires_at = timezone.now() + timedelta(hours=cfg.listing_expiry_hours)
        listing = await BallListing.objects.acreate(seller=player, ball_instance=ball, price=price, expires_at=expires_at)

        special_name = ball.specialcard.name if ball.special_id and ball.specialcard else None
        fee_amount = int(price * cfg.listing_platform_fee)

        embed = discord.Embed(title=f"{settings.collectible_name.title()} Listed", color=discord.Color.blurple())
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.add_field(name=settings.collectible_name.title(), value=f"**{ball.ball.country}**" + (f"\n*{special_name}*" if special_name else ""), inline=True)
        embed.add_field(name="Stats", value=f"ATK: {ball.attack_bonus:+}% | HP: {ball.health_bonus:+}%", inline=True)
        embed.add_field(name="Listed Price", value=fmt(price), inline=True)
        embed.add_field(name="You'll Receive", value=fmt(price - fee_amount), inline=True)
        embed.add_field(name="Platform Fee", value=f"{cfg.listing_platform_fee * 100:.0f}% ({fmt(fee_amount)})", inline=True)
        embed.add_field(name="Expires", value=f"<t:{int(expires_at.timestamp())}:R>", inline=True)
        embed.add_field(name="Listing ID", value=f"`#{listing.pk}`", inline=True)
        embed.set_footer(text="Use /economy delist to remove this listing.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /economy listings ─────────────────────────────────────────────────────

    @app_commands.command(name="listings")
    @app_commands.describe(page="Page number (10 listings per page).")
    async def listings(self, interaction: discord.Interaction["BallsDexBot"], page: int = 1) -> None:
        """Browse all active ball listings on the player market."""
        if not await self._guard_currency(interaction):
            return
        cfg = await self._get_cfg()
        if cfg and not cfg.listings_enabled:
            await interaction.response.send_message(embed=disabled_embed("The player market is currently disabled."), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        per_page = 10
        offset = (max(1, page) - 1) * per_page
        total = await BallListing.objects.filter(sold=False).acount()

        if total == 0:
            await interaction.followup.send(embed=discord.Embed(title=f"{settings.plural_collectible_name.title()} Market", description=f"No active listings right now.\nUse `/economy list` to sell your {settings.plural_collectible_name}!", color=discord.Color.blurple()), ephemeral=True)
            return

        total_pages = max(1, (total + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))
        active = [obj async for obj in BallListing.objects.filter(sold=False).select_related("ball_instance__ball", "ball_instance__special", "seller").order_by("price")[offset:offset + per_page]]

        embed = discord.Embed(
            title=f"{settings.plural_collectible_name.title()} Market",
            description=f"**{total}** listing{'s' if total != 1 else ''} • Page **{page}/{total_pages}** • Sorted by price\nUse `/economy buy <id>` to purchase",
            color=discord.Color.blurple(),
        )
        for listing in active:
            bi = listing.ball_instance
            special_tag = f" *[{bi.specialcard.name}]*" if bi.special_id and bi.specialcard else ""
            atk = f"{bi.attack_bonus:+}%" if bi.attack_bonus != 0 else "±0%"
            hp = f"{bi.health_bonus:+}%" if bi.health_bonus != 0 else "±0%"
            embed.add_field(
                name=f"`#{listing.pk}` {bi.ball.country}{special_tag}",
                value=f"💰 **{fmt(listing.price)}**\nATK {atk} | HP {hp}\n<@{listing.seller.discord_id}>\nExpires <t:{int(listing.expires_at.timestamp())}:R>",
                inline=True,
            )
        embed.set_footer(text=f"Showing {offset + 1}–{min(offset + per_page, total)} of {total} listings.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /economy buy ──────────────────────────────────────────────────────────

    @app_commands.command(name="buy")
    @app_commands.describe(listing_id="The listing ID from /economy listings.")
    async def buy(self, interaction: discord.Interaction["BallsDexBot"], listing_id: int) -> None:
        """Purchase a ball from another player's listing."""
        if not await self._guard_currency(interaction):
            return
        cfg = await self._get_cfg()
        if cfg and not cfg.listings_enabled:
            await interaction.response.send_message(embed=disabled_embed("The player market is currently disabled."), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            listing = await BallListing.objects.select_related("ball_instance__ball", "ball_instance__special", "seller").aget(pk=listing_id, sold=False)
        except BallListing.DoesNotExist:
            await interaction.followup.send(embed=error_embed("Not Found", "That listing doesn't exist or has already been sold."), ephemeral=True)
            return

        buyer = await self._get_player(interaction)
        if buyer is None:
            return
        if listing.seller_id == buyer.pk:
            await interaction.followup.send(embed=error_embed("Own Listing", "You can't buy your own listing."), ephemeral=True)
            return

        await buyer.arefresh_from_db(fields=["money"])
        if not buyer.can_afford(listing.price):
            await interaction.followup.send(embed=error_embed("Insufficient Funds", f"Balance: **{fmt(buyer.money)}**\nRequired: **{fmt(listing.price)}**\nShort by: **{fmt(listing.price - buyer.money)}**"), ephemeral=True)
            return

        fee = int(listing.price * (cfg.listing_platform_fee if cfg else 0.05))
        seller_receives = listing.price - fee
        seller = await Player.objects.aget(pk=listing.seller_id)

        await buyer.remove_money(listing.price)
        await seller.add_money(seller_receives)

        bi = listing.ball_instance
        bi.player = buyer
        bi.trade_player_id = listing.seller_id
        await bi.asave(update_fields=["player", "trade_player"])

        listing.sold = True
        listing.buyer = buyer
        listing.sold_at = timezone.now()
        await listing.asave(update_fields=["sold", "buyer", "sold_at"])

        await buyer.arefresh_from_db(fields=["money"])
        special_tag = f"\n*{bi.specialcard.name}*" if bi.special_id and bi.specialcard else ""

        embed = discord.Embed(title="Purchase Complete! 🎉", color=discord.Color.green())
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.add_field(name=f"{settings.collectible_name.title()} Acquired", value=f"**{bi.ball.country}**{special_tag}", inline=True)
        embed.add_field(name="Stats", value=f"ATK: {bi.attack_bonus:+}% | HP: {bi.health_bonus:+}%", inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)
        embed.add_field(name="You Paid", value=fmt(listing.price), inline=True)
        embed.add_field(name="New Balance", value=fmt(buyer.money), inline=True)
        embed.set_footer(text=f"Platform fee: {fmt(fee)} ({(cfg.listing_platform_fee if cfg else 0.05) * 100:.0f}%). Seller received: {fmt(seller_receives)}.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /economy delist ───────────────────────────────────────────────────────

    @app_commands.command(name="delist")
    @app_commands.describe(listing_id="The listing ID to remove.")
    async def delist(self, interaction: discord.Interaction["BallsDexBot"], listing_id: int) -> None:
        """Remove one of your active listings. The ball stays in your collection."""
        if not await self._guard_currency(interaction):
            return
        player = await self._get_player(interaction)
        if player is None:
            return
        try:
            listing = await BallListing.objects.select_related("ball_instance__ball").aget(pk=listing_id, sold=False, seller=player)
        except BallListing.DoesNotExist:
            await interaction.response.send_message(embed=error_embed("Not Found", "That listing doesn't exist or doesn't belong to you."), ephemeral=True)
            return

        bi = listing.ball_instance
        await listing.adelete()
        embed = discord.Embed(title="Listing Removed", color=discord.Color.orange())
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.add_field(name=f"{settings.collectible_name.title()} Returned", value=f"**{bi.ball.country}**", inline=True)
        embed.set_footer(text=f"Your {settings.collectible_name} is still in your collection.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /economy mylistings ───────────────────────────────────────────────────

    @app_commands.command(name="mylistings")
    async def mylistings(self, interaction: discord.Interaction["BallsDexBot"]) -> None:
        """View all of your active listings on the market."""
        if not await self._guard_currency(interaction):
            return
        player = await self._get_player(interaction)
        if player is None:
            return

        cfg = await self._get_cfg()
        active = [obj async for obj in BallListing.objects.filter(seller=player, sold=False).select_related("ball_instance__ball", "ball_instance__special").order_by("price")]

        if not active:
            await interaction.response.send_message(embed=discord.Embed(title="Your Listings", description=f"You have no active listings.\nUse `/economy list <ball> <price>` to sell a {settings.collectible_name}!", color=discord.Color.blurple()), ephemeral=True)
            return

        max_listings = cfg.listing_max_per_player if cfg else 10
        embed = discord.Embed(title="Your Listings", description=f"**{len(active)}/{max_listings}** active listing{'s' if len(active) != 1 else ''}.\nUse `/economy delist <id>` to remove a listing.", color=discord.Color.blurple())
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        for listing in active:
            bi = listing.ball_instance
            special_tag = f" *[{bi.specialcard.name}]*" if bi.special_id and bi.specialcard else ""
            embed.add_field(
                name=f"`#{listing.pk}` {bi.ball.country}{special_tag}",
                value=f"💰 **{fmt(listing.price)}**\nATK: {bi.attack_bonus:+}% | HP: {bi.health_bonus:+}%\nExpires <t:{int(listing.expires_at.timestamp())}:R>",
                inline=True,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /economy shop ─────────────────────────────────────────────────────────

    @app_commands.command(name="shop")
    async def shop(self, interaction: discord.Interaction["BallsDexBot"]) -> None:
        """Browse balls available to buy directly from the shop."""
        if not await self._guard_currency(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        items = [obj async for obj in BallShopPrice.objects.filter(enabled=True).select_related("ball", "special").order_by("price")]

        if not items:
            await interaction.followup.send(embed=discord.Embed(title="Ball Shop", description="The shop is empty right now. Check back later!", color=discord.Color.blurple()), ephemeral=True)
            return

        embed = discord.Embed(
            title="Ball Shop",
            description=f"Buy {settings.plural_collectible_name} directly with {settings.currency_plural}. Use `/economy buy_ball <id>` to purchase.",
            color=discord.Color.blurple(),
        )
        for item in items[:20]:
            special_tag = f" *[{item.special.name}]*" if item.special_id and item.special else ""
            stock_text = f"Stock: {item.stock}" if item.stock >= 0 else "Stock: ∞"
            embed.add_field(name=f"`#{item.pk}` {item.ball.country}{special_tag}", value=f"💰 **{fmt(item.price)}**\n{stock_text}", inline=True)
        embed.set_footer(text="Prices set by server administrators.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /economy buy_ball ─────────────────────────────────────────────────────

    @app_commands.command(name="buy_ball")
    @app_commands.describe(shop_id="The shop item ID from /economy shop.")
    async def buy_ball(self, interaction: discord.Interaction["BallsDexBot"], shop_id: int) -> None:
        """Buy a ball directly from the shop at the listed price."""
        if not await self._guard_currency(interaction):
            return
        await interaction.response.defer(ephemeral=True)

        try:
            item = await BallShopPrice.objects.select_related("ball", "special").aget(pk=shop_id, enabled=True)
        except BallShopPrice.DoesNotExist:
            await interaction.followup.send(embed=error_embed("Not Found", "That shop item doesn't exist or is no longer available."), ephemeral=True)
            return

        if item.stock == 0:
            await interaction.followup.send(embed=error_embed("Out of Stock", f"**{item.ball.country}** is sold out."), ephemeral=True)
            return

        player = await self._get_player(interaction)
        if player is None:
            return

        await player.arefresh_from_db(fields=["money"])
        if not player.can_afford(item.price):
            await interaction.followup.send(embed=error_embed("Insufficient Funds", f"This costs {fmt(item.price)} but you only have {fmt(player.money)}."), ephemeral=True)
            return

        await player.remove_money(item.price)
        new_ball = await BallInstance.objects.acreate(ball=item.ball, player=player, special=item.special, server_id=interaction.guild_id)

        if item.stock > 0:
            item.stock -= 1
            if item.stock == 0:
                item.enabled = False
            await item.asave(update_fields=["stock", "enabled"])

        await player.arefresh_from_db(fields=["money"])
        special_tag = f"\n*{item.special.name}*" if item.special_id and item.special else ""

        embed = discord.Embed(title=f"{settings.collectible_name.title()} Purchased!", color=discord.Color.green())
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.add_field(name="Received", value=f"**{item.ball.country}**{special_tag}", inline=True)
        embed.add_field(name="You Paid", value=fmt(item.price), inline=True)
        embed.add_field(name="New Balance", value=fmt(player.money), inline=True)
        embed.set_footer(text="Check your collection with /balls.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /economy pending ──────────────────────────────────────────────────────

    @app_commands.command(name="pending")
    async def pending(self, interaction: discord.Interaction["BallsDexBot"]) -> None:
        """Check how much passive income is waiting to be claimed."""
        if not await self._guard_currency(interaction):
            return
        cfg = await self._get_cfg()
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
        embed = discord.Embed(title="Passive Income", color=discord.Color.gold())
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.add_field(name="⏳ Ready to Claim", value=fmt(pending), inline=True)
        embed.add_field(name="💰 Current Balance", value=fmt(player.money), inline=True)
        embed.add_field(name="📦 Balls Generating", value=str(ball_count), inline=True)
        if last_tick:
            embed.add_field(name="Last Tick", value=f"<t:{int(last_tick.timestamp())}:R>", inline=True)
        if total_earned:
            embed.add_field(name="Total Ever Earned", value=fmt(total_earned), inline=True)
        if cfg:
            embed.set_footer(text=f"Each {settings.collectible_name} has a {cfg.passive_chance * 100:.0f}% chance to generate {cfg.passive_min}–{cfg.passive_max}+ {settings.currency_plural} every {cfg.passive_interval_minutes} minutes.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /economy claim ────────────────────────────────────────────────────────

    @app_commands.command(name="claim")
    async def claim(self, interaction: discord.Interaction["BallsDexBot"]) -> None:
        """Collect all accumulated passive income into your balance."""
        if not await self._guard_currency(interaction):
            return
        cfg = await self._get_cfg()
        if cfg and not await self._guard_command(interaction, cfg.claim_enabled, "/economy claim"):
            return

        player = await self._get_player(interaction)
        if player is None:
            return

        try:
            pool = await PassiveIncomePool.objects.aget(player=player)
        except PassiveIncomePool.DoesNotExist:
            await interaction.response.send_message(embed=error_embed("Nothing to Claim", f"You have no passive income yet. Own some {settings.plural_collectible_name} and wait for the next tick!"), ephemeral=True)
            return

        if pool.pending == 0:
            await interaction.response.send_message(embed=error_embed("Nothing to Claim", "No passive income to claim right now. Check back later!"), ephemeral=True)
            return

        earned = pool.pending
        pool.pending = 0
        await pool.asave(update_fields=["pending"])
        await player.add_money(earned)
        await player.arefresh_from_db(fields=["money"])

        embed = discord.Embed(title=f"{settings.currency_name} Claimed!", color=discord.Color.green())
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.add_field(name="Claimed", value=fmt(earned), inline=True)
        embed.add_field(name="New Balance", value=fmt(player.money), inline=True)
        embed.set_footer(text=f"Your {settings.plural_collectible_name} continue generating passive {settings.currency_plural} automatically.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /economy admin view ───────────────────────────────────────────────────

    @admin.command(name="view")
    @app_commands.check(is_staff)
    @app_commands.describe(user="The user to inspect.")
    async def admin_view(self, interaction: discord.Interaction["BallsDexBot"], user: discord.User) -> None:
        """View the full economy profile of a player."""
        await interaction.response.defer(ephemeral=True)
        player = await Player.objects.aget_or_none(discord_id=user.id)
        if not player:
            await interaction.followup.send(embed=error_embed("No Account", f"{user} does not have a {settings.bot_name} account."), ephemeral=True)
            return

        try:
            pool = await PassiveIncomePool.objects.aget(player=player)
            pending, total_earned, last_tick = pool.pending, pool.total_earned, pool.last_tick
        except PassiveIncomePool.DoesNotExist:
            pending, total_earned, last_tick = 0, 0, None

        ball_count = await BallInstance.objects.filter(player=player, deleted=False).acount()
        active_listings = await BallListing.objects.filter(seller=player, sold=False).acount()
        total_sold = await BallListing.objects.filter(seller=player, sold=True).acount()
        total_bought = await BallListing.objects.filter(buyer=player, sold=True).acount()

        embed = discord.Embed(title=f"Economy Profile — {user.display_name}", color=discord.Color.blurple())
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="💰 Balance", value=fmt(player.money), inline=True)
        embed.add_field(name="⏳ Pending Passive", value=fmt(pending), inline=True)
        embed.add_field(name="📈 Total Passive Earned", value=fmt(total_earned), inline=True)
        embed.add_field(name="📦 Balls Owned", value=str(ball_count), inline=True)
        embed.add_field(name="📋 Active Listings", value=str(active_listings), inline=True)
        embed.add_field(name="🛒 Sold / Bought", value=f"{total_sold} / {total_bought}", inline=True)
        if last_tick:
            embed.add_field(name="Last Passive Tick", value=f"<t:{int(last_tick.timestamp())}:R>", inline=True)
        embed.set_footer(text=f"Discord ID: {user.id}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /economy admin give ───────────────────────────────────────────────────

    @admin.command(name="give")
    @app_commands.check(is_staff)
    @app_commands.describe(user="The user to give currency to.", amount="The amount to give.")
    async def admin_give(self, interaction: discord.Interaction["BallsDexBot"], user: discord.User, amount: int) -> None:
        """Give currency to a player."""
        if amount <= 0:
            await interaction.response.send_message(embed=error_embed("Invalid Amount", "Amount must be greater than zero."), ephemeral=True)
            return
        player = await Player.objects.aget_or_none(discord_id=user.id)
        if not player:
            await interaction.response.send_message(embed=error_embed("No Account", f"{user} does not have a {settings.bot_name} account."), ephemeral=True)
            return
        await player.add_money(amount)
        await player.arefresh_from_db(fields=["money"])
        await interaction.response.send_message(embed=discord.Embed(title="Currency Given", description=f"Gave {fmt(amount)} to {user.mention}.\nNew balance: {fmt(player.money)}", color=discord.Color.green()), ephemeral=True)
        log.info("%s gave %d to %s (%d)", interaction.user, amount, user, user.id, extra={"webhook": True})

    # ── /economy admin take ───────────────────────────────────────────────────

    @admin.command(name="take")
    @app_commands.check(is_staff)
    @app_commands.describe(user="The user to remove currency from.", amount="The amount to remove.")
    async def admin_take(self, interaction: discord.Interaction["BallsDexBot"], user: discord.User, amount: int) -> None:
        """Remove currency from a player."""
        if amount <= 0:
            await interaction.response.send_message(embed=error_embed("Invalid Amount", "Amount must be greater than zero."), ephemeral=True)
            return
        player = await Player.objects.aget_or_none(discord_id=user.id)
        if not player:
            await interaction.response.send_message(embed=error_embed("No Account", f"{user} does not have a {settings.bot_name} account."), ephemeral=True)
            return
        if not player.can_afford(amount):
            await interaction.response.send_message(embed=error_embed("Insufficient Funds", f"{user.mention} only has {fmt(player.money)}."), ephemeral=True)
            return
        await player.remove_money(amount)
        await player.arefresh_from_db(fields=["money"])
        await interaction.response.send_message(embed=discord.Embed(title="Currency Taken", description=f"Removed {fmt(amount)} from {user.mention}.\nNew balance: {fmt(player.money)}", color=discord.Color.orange()), ephemeral=True)
        log.info("%s removed %d from %s (%d)", interaction.user, amount, user, user.id, extra={"webhook": True})

    # ── /economy admin set ────────────────────────────────────────────────────

    @admin.command(name="set")
    @app_commands.check(is_staff)
    @app_commands.describe(user="The user whose balance to set.", amount="The exact amount to set.")
    async def admin_set(self, interaction: discord.Interaction["BallsDexBot"], user: discord.User, amount: int) -> None:
        """Set a player's balance to an exact amount."""
        if amount < 0:
            await interaction.response.send_message(embed=error_embed("Invalid Amount", "Amount must be zero or greater."), ephemeral=True)
            return
        player = await Player.objects.aget_or_none(discord_id=user.id)
        if not player:
            await interaction.response.send_message(embed=error_embed("No Account", f"{user} does not have a {settings.bot_name} account."), ephemeral=True)
            return
        old = player.money
        player.money = amount
        await player.asave(update_fields=["money"])
        await interaction.response.send_message(embed=discord.Embed(title="Balance Set", description=f"Set {user.mention}'s balance from {fmt(old)} to {fmt(amount)}.", color=discord.Color.blurple()), ephemeral=True)
        log.info("%s set balance of %s (%d) from %d to %d", interaction.user, user, user.id, old, amount, extra={"webhook": True})

    # ── /economy admin giveall ────────────────────────────────────────────────

    @admin.command(name="giveall")
    @app_commands.check(is_staff)
    @app_commands.describe(amount="The amount to give every player.")
    async def admin_giveall(self, interaction: discord.Interaction["BallsDexBot"], amount: int) -> None:
        """Give currency to every player in the database."""
        if amount <= 0:
            await interaction.response.send_message(embed=error_embed("Invalid Amount", "Amount must be greater than zero."), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        count = 0
        async for player in Player.objects.all():
            await player.add_money(amount)
            count += 1
        await interaction.followup.send(embed=discord.Embed(title="Mass Payout", description=f"Gave {fmt(amount)} to **{count:,}** players.", color=discord.Color.green()), ephemeral=True)
        log.info("%s gave %d to all %d players", interaction.user, amount, count, extra={"webhook": True})

    # ── /economy admin stats ──────────────────────────────────────────────────

    @admin.command(name="stats")
    @app_commands.check(is_staff)
    async def admin_stats(self, interaction: discord.Interaction["BallsDexBot"]) -> None:
        """View server-wide economy statistics."""
        await interaction.response.defer(ephemeral=True)
        total_players = await Player.objects.acount()
        money_agg = await Player.objects.aaggregate(total=Sum("money"))
        total_money = money_agg["total"] or 0
        passive_agg = await PassiveIncomePool.objects.aaggregate(total=Sum("pending"))
        total_pending = passive_agg["total"] or 0
        earned_agg = await PassiveIncomePool.objects.aaggregate(total=Sum("total_earned"))
        total_earned = earned_agg["total"] or 0
        active_listings = await BallListing.objects.filter(sold=False).acount()
        total_sold = await BallListing.objects.filter(sold=True).acount()
        sold_agg = await BallListing.objects.filter(sold=True).aaggregate(total=Sum("price"))
        total_volume = sold_agg["total"] or 0
        top_players = [obj async for obj in Player.objects.order_by("-money").values_list("discord_id", "money")[:5]]

        embed = discord.Embed(title="Economy Statistics", color=discord.Color.gold())
        embed.add_field(name="💰 Total In Circulation", value=fmt(total_money), inline=True)
        embed.add_field(name="⏳ Total Pending", value=fmt(total_pending), inline=True)
        embed.add_field(name="📈 Total Passive Paid Out", value=fmt(total_earned), inline=True)
        embed.add_field(name="📋 Active Listings", value=str(active_listings), inline=True)
        embed.add_field(name="✅ Total Listings Sold", value=str(total_sold), inline=True)
        embed.add_field(name="💸 Total Market Volume", value=fmt(total_volume), inline=True)
        embed.add_field(name="👥 Total Players", value=str(total_players), inline=True)
        if top_players:
            embed.add_field(name="🏆 Top 5 Wealthiest", value="\n".join(f"{i+1}. <@{did}> — {fmt(m)}" for i, (did, m) in enumerate(top_players)), inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /economy admin resetpassive ───────────────────────────────────────────

    @admin.command(name="resetpassive")
    @app_commands.check(is_staff)
    @app_commands.describe(user="The user whose passive pool to reset.")
    async def admin_resetpassive(self, interaction: discord.Interaction["BallsDexBot"], user: discord.User) -> None:
        """Reset a player's pending passive income pool to 0."""
        player = await Player.objects.aget_or_none(discord_id=user.id)
        if not player:
            await interaction.response.send_message(embed=error_embed("No Account", f"{user} does not have a {settings.bot_name} account."), ephemeral=True)
            return
        try:
            pool = await PassiveIncomePool.objects.aget(player=player)
            old = pool.pending
            pool.pending = 0
            await pool.asave(update_fields=["pending"])
            await interaction.response.send_message(embed=discord.Embed(title="Passive Pool Reset", description=f"Reset {user.mention}'s passive pool from {fmt(old)} to 0.", color=discord.Color.orange()), ephemeral=True)
            log.info("%s reset passive pool for %s (%d), was %d", interaction.user, user, user.id, old, extra={"webhook": True})
        except PassiveIncomePool.DoesNotExist:
            await interaction.response.send_message(embed=error_embed("No Pool", f"{user.mention} has no passive pool."), ephemeral=True)

    # ── /economy admin clearlistings ──────────────────────────────────────────

    @admin.command(name="clearlistings")
    @app_commands.check(is_staff)
    @app_commands.describe(user="The user whose listings to clear.")
    async def admin_clearlistings(self, interaction: discord.Interaction["BallsDexBot"], user: discord.User) -> None:
        """Remove all active listings from a player and return their balls."""
        player = await Player.objects.aget_or_none(discord_id=user.id)
        if not player:
            await interaction.response.send_message(embed=error_embed("No Account", f"{user} does not have a {settings.bot_name} account."), ephemeral=True)
            return
        listings = [obj async for obj in BallListing.objects.filter(seller=player, sold=False)]
        if not listings:
            await interaction.response.send_message(embed=error_embed("No Listings", f"{user.mention} has no active listings."), ephemeral=True)
            return
        count = len(listings)
        for listing in listings:
            await listing.adelete()
        await interaction.response.send_message(embed=discord.Embed(title="Listings Cleared", description=f"Cleared **{count}** listing{'s' if count != 1 else ''} from {user.mention}.", color=discord.Color.orange()), ephemeral=True)
        log.info("%s cleared %d listings for %s (%d)", interaction.user, count, user, user.id, extra={"webhook": True})

    # ── /economy admin removelisting ──────────────────────────────────────────

    @admin.command(name="removelisting")
    @app_commands.check(is_staff)
    @app_commands.describe(listing_id="The listing ID to remove.")
    async def admin_removelisting(self, interaction: discord.Interaction["BallsDexBot"], listing_id: int) -> None:
        """Forcibly remove any specific listing by ID."""
        try:
            listing = await BallListing.objects.select_related("ball_instance__ball", "seller").aget(pk=listing_id, sold=False)
        except BallListing.DoesNotExist:
            await interaction.response.send_message(embed=error_embed("Not Found", "That listing doesn't exist or is already sold."), ephemeral=True)
            return
        ball_name = listing.ball_instance.ball.country
        seller_id = listing.seller.discord_id
        await listing.adelete()
        await interaction.response.send_message(embed=discord.Embed(title="Listing Removed", description=f"Removed listing `#{listing_id}` (**{ball_name}**) — returned to <@{seller_id}>.", color=discord.Color.orange()), ephemeral=True)
        log.info("%s removed listing #%d (%s) from player %d", interaction.user, listing_id, ball_name, seller_id, extra={"webhook": True})

    # ── /economy admin forcepassive ───────────────────────────────────────────

    @admin.command(name="forcepassive")
    @app_commands.check(is_staff)
    async def admin_forcepassive(self, interaction: discord.Interaction["BallsDexBot"]) -> None:
        """Manually trigger a passive income tick for all players right now."""
        await interaction.response.defer(ephemeral=True)
        cfg = await self._get_cfg()
        if cfg is None or not cfg.passive_enabled:
            await interaction.followup.send(embed=error_embed("Disabled", "Passive income is not configured or is disabled."), ephemeral=True)
            return

        now = timezone.now()
        players_updated = 0
        total_generated = 0
        async for player in Player.objects.all():
            total = 0
            async for bi in BallInstance.objects.filter(player=player, deleted=False).select_related("ball"):
                if random.random() < cfg.passive_chance:
                    base = random.randint(cfg.passive_min, cfg.passive_max)
                    total += max(1, base + int(bi.ball.rarity * cfg.passive_rarity_bonus))
            if total > 0:
                pool, _ = await PassiveIncomePool.objects.aget_or_create(player=player, defaults={"pending": 0, "total_earned": 0})
                pool.pending += total
                pool.total_earned += total
                pool.last_tick = now
                await pool.asave(update_fields=["pending", "total_earned", "last_tick"])
                players_updated += 1
                total_generated += total

        await interaction.followup.send(embed=discord.Embed(title="Passive Tick Complete", description=f"Generated {fmt(total_generated)} across **{players_updated}** players.", color=discord.Color.green()), ephemeral=True)
        log.info("%s triggered manual passive tick: %d for %d players", interaction.user, total_generated, players_updated, extra={"webhook": True})

    # ── /economy admin history ────────────────────────────────────────────────

    @admin.command(name="history")
    @app_commands.check(is_staff)
    @app_commands.describe(user="The user whose history to view.")
    async def admin_history(self, interaction: discord.Interaction["BallsDexBot"], user: discord.User) -> None:
        """View a player's recent market sale and purchase history."""
        await interaction.response.defer(ephemeral=True)
        player = await Player.objects.aget_or_none(discord_id=user.id)
        if not player:
            await interaction.followup.send(embed=error_embed("No Account", f"{user} does not have a {settings.bot_name} account."), ephemeral=True)
            return

        sold = [obj async for obj in BallListing.objects.filter(seller=player, sold=True).select_related("ball_instance__ball", "ball_instance__special").order_by("-sold_at")[:10]]
        bought = [obj async for obj in BallListing.objects.filter(buyer=player, sold=True).select_related("ball_instance__ball", "ball_instance__special").order_by("-sold_at")[:10]]

        embed = discord.Embed(title=f"Market History — {user.display_name}", color=discord.Color.blurple())
        embed.set_thumbnail(url=user.display_avatar.url)

        def fmt_listing(l: BallListing) -> str:
            bi = l.ball_instance
            sp = f" [{bi.specialcard.name}]" if bi.special_id and bi.specialcard else ""
            ts = f"<t:{int(l.sold_at.timestamp())}:R>" if l.sold_at else ""
            return f"**{bi.ball.country}**{sp} — {fmt(l.price)} {ts}"

        embed.add_field(name="📤 Recent Sales", value="\n".join(fmt_listing(l) for l in sold) or "None.", inline=False)
        embed.add_field(name="📥 Recent Purchases", value="\n".join(fmt_listing(l) for l in bought) or "None.", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)
