from __future__ import annotations

import logging
import random
from datetime import timedelta
from typing import TYPE_CHECKING, cast

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ballsdex.core.utils.transformers import BallInstanceTransformer
from ballsdex.packages.countryballs.countryball import BallSpawnView
from bd_models.models import BallInstance, Player
from settings.models import settings

from ..models import BallListing, EconomyConfig, PassiveIncomePool

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

log = logging.getLogger(__name__)

BallInstanceTransform = app_commands.Transform[BallInstance, BallInstanceTransformer]


def fmt_currency(amount: int) -> str:
    """Format an amount using the bot's configured currency symbol and name."""
    sym = settings.currency_symbol or ""
    if sym:
        if settings.currency_symbol_before:
            return f"{sym}{amount:,}"
        return f"{amount:,}{sym}"
    return f"{amount:,} {settings.currency_plural}"


async def get_config() -> EconomyConfig | None:
    return await EconomyConfig.objects.afirst()


def compute_sell_price(config: EconomyConfig, ball_instance: BallInstance) -> int:
    base = random.randint(config.sell_base_min, config.sell_base_max)
    price = base + ball_instance.ball.rarity * config.sell_rarity_multiplier
    if ball_instance.special_id is not None:
        price *= config.sell_special_multiplier
    price += (ball_instance.attack_bonus + ball_instance.health_bonus) * config.sell_stat_multiplier
    return max(1, int(price))


class Economy(commands.Cog):
    """
    Economy system — catch income, ball selling and passive income.
    All commands are disabled if currency is not configured in bot settings.
    """

    def __init__(self, bot: "BallsDexBot") -> None:
        self.bot = bot
        self._config: EconomyConfig | None = None

    async def cog_load(self) -> None:
        self._config = await get_config()
        if not settings.currency_enabled:
            log.warning(
                "Economy: currency_name is not set in bot settings. "
                "All economy commands will show a disabled message until it is configured."
            )
        await self._monkeypatch()
        self.passive_income_task.change_interval(
            minutes=self._config.passive_interval_minutes if self._config else 10
        )
        self.passive_income_task.start()
        self.expire_listings_task.start()

    def cog_unload(self) -> None:
        self.passive_income_task.cancel()
        self.expire_listings_task.cancel()

    # ── Currency guard ────────────────────────────────────────────────────────

    async def _check_currency(self, interaction: discord.Interaction) -> bool:
        if not settings.currency_enabled:
            await interaction.response.send_message(
                f"Currency is not enabled on this bot. "
                f"An administrator must set a currency name in the bot settings first.",
                ephemeral=True,
            )
            return False
        return True

    # ── Monkeypatch — Layer 1: Catch income ───────────────────────────────────

    async def _monkeypatch(self) -> None:
        from ballsdex.packages.countryballs import CountryBallsSpawner
        cog = cast("CountryBallsSpawner", self.bot.get_cog("CountryBallsSpawner"))
        if cog is None:
            log.warning("Economy: CountryBallsSpawner not found, catch income will not work.")
            return

        economy_cog = self

        class BallSpawnViewOverride(BallSpawnView):
            async def catch_ball(
                self,
                user: discord.User | discord.Member,
                *,
                player: Player | None,
                guild: discord.Guild | None,
            ):
                ball, is_new = await super().catch_ball(user, player=player, guild=guild)

                if not settings.currency_enabled:
                    return ball, is_new

                config = economy_cog._config
                if config is None or not config.catch_income_enabled:
                    return ball, is_new
                if player is None:
                    return ball, is_new

                earned = random.randint(config.catch_base_min, config.catch_base_max)
                earned += int(ball.ball.rarity * config.catch_rarity_multiplier)
                if ball.special_id is not None:
                    earned += config.catch_special_bonus

                await Player.objects.filter(pk=player.pk).aupdate(
                    money=player.money + earned
                )
                log.debug(
                    "Catch income: player %s earned %d (%s)",
                    player.discord_id, earned, ball.ball.country,
                )
                return ball, is_new

        cog.countryball_cls = BallSpawnViewOverride
        log.info("Economy: catch income monkeypatch applied.")

    # ── Background tasks ──────────────────────────────────────────────────────

    @tasks.loop(minutes=10)
    async def passive_income_task(self) -> None:
        if not settings.currency_enabled:
            return
        config = await get_config()
        if config is None or not config.passive_enabled:
            return

        async for player in Player.objects.all():
            total = 0
            async for bi in BallInstance.objects.filter(
                player=player, deleted=False
            ).select_related("ball"):
                if random.random() < config.passive_chance:
                    amount = random.randint(config.passive_min, config.passive_max)
                    multiplier = 1.0 + bi.ball.rarity * config.passive_rarity_multiplier
                    total += int(amount * multiplier)

            if total > 0:
                pool, _ = await PassiveIncomePool.objects.aget_or_create(
                    player=player, defaults={"pending": 0}
                )
                pool.pending += total
                await pool.asave(update_fields=["pending", "last_tick"])

    @passive_income_task.before_loop
    async def before_passive(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=30)
    async def expire_listings_task(self) -> None:
        from django.utils import timezone
        now = timezone.now()
        async for listing in BallListing.objects.filter(
            sold=False, expires_at__lte=now
        ).select_related("ball_instance", "seller"):
            listing.ball_instance.player = listing.seller
            await listing.ball_instance.asave(update_fields=["player"])
            await listing.adelete()
            log.info(
                "Listing expired: ball %s returned to player %s",
                listing.ball_instance_id,
                listing.seller.discord_id,
            )

    @expire_listings_task.before_loop
    async def before_expire(self) -> None:
        await self.bot.wait_until_ready()

    # ── Command group ─────────────────────────────────────────────────────────

    economy_group = app_commands.Group(
        name="economy",
        description="Earn, sell and manage your currency.",
    )

    # ── Balance ───────────────────────────────────────────────────────────────

    @economy_group.command(name="balance")
    async def balance(self, interaction: discord.Interaction["BallsDexBot"]) -> None:
        """
        Check your current balance and pending passive income.
        """
        if not await self._check_currency(interaction):
            return

        try:
            player = await Player.objects.aget(discord_id=interaction.user.id)
        except Player.DoesNotExist:
            await interaction.response.send_message(
                f"You don't have a player profile yet. "
                f"Catch some {settings.plural_collectible_name} first!",
                ephemeral=True,
            )
            return

        try:
            pool = await PassiveIncomePool.objects.aget(player=player)
            pending = pool.pending
        except PassiveIncomePool.DoesNotExist:
            pending = 0

        embed = discord.Embed(
            title=f"{interaction.user.display_name}'s {settings.currency_name} Balance",
            color=discord.Color.gold(),
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.add_field(
            name="💰 Balance",
            value=fmt_currency(player.money),
            inline=True,
        )
        embed.add_field(
            name="⏳ Pending",
            value=fmt_currency(pending),
            inline=True,
        )
        embed.set_footer(
            text=f"Use /economy claim to collect your pending {settings.currency_plural}."
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── Layer 2: Quick sell ───────────────────────────────────────────────────

    @economy_group.command(name="quicksell")
    @app_commands.describe(ball="The ball you want to sell to the system instantly.")
    async def quicksell(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        ball: BallInstanceTransform,
    ) -> None:
        """
        Sell a ball instantly to the system for currency.
        Price is based on rarity, special and stat rolls.
        """
        if not await self._check_currency(interaction):
            return

        config = self._config or await get_config()
        if config is None or not config.sell_enabled:
            await interaction.response.send_message(
                "Selling is currently disabled.", ephemeral=True
            )
            return

        try:
            player = await Player.objects.aget(discord_id=interaction.user.id)
        except Player.DoesNotExist:
            await interaction.response.send_message(
                f"You don't have a player profile yet. "
                f"Catch some {settings.plural_collectible_name} first!",
                ephemeral=True,
            )
            return

        if ball.player_id != player.pk:
            await interaction.response.send_message(
                f"You don't own that {settings.collectible_name}.", ephemeral=True
            )
            return

        if ball.favorite:
            await interaction.response.send_message(
                f"You can't sell a favourited {settings.collectible_name}. "
                "Unfavourite it first.",
                ephemeral=True,
            )
            return

        if await BallListing.objects.filter(ball_instance=ball, sold=False).aexists():
            await interaction.response.send_message(
                f"That {settings.collectible_name} is currently listed for sale. "
                "Use `/economy delist` to remove it first.",
                ephemeral=True,
            )
            return

        await ball.arefresh_from_db()
        price = compute_sell_price(config, ball)
        special_name = ball.specialcard.name if ball.special_id and ball.specialcard else None

        ball.deleted = True
        await ball.asave(update_fields=["deleted"])
        new_balance = player.money + price
        await Player.objects.filter(pk=player.pk).aupdate(money=new_balance)

        embed = discord.Embed(
            title=f"{settings.collectible_name.title()} Sold",
            color=discord.Color.green(),
        )
        embed.add_field(
            name=settings.collectible_name.title(),
            value=f"**{ball.ball.country}**" + (f" — *{special_name}*" if special_name else ""),
            inline=True,
        )
        embed.add_field(
            name="You Received",
            value=fmt_currency(price),
            inline=True,
        )
        embed.add_field(
            name="New Balance",
            value=fmt_currency(new_balance),
            inline=True,
        )
        embed.set_footer(
            text=f"Quick sell prices are based on rarity, special and stat rolls. "
                 f"Use /economy list to sell to other players instead."
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── Layer 2: Player listing ───────────────────────────────────────────────

    @economy_group.command(name="list")
    @app_commands.describe(
        ball="The ball you want to list for other players to buy.",
        price="The price you want to sell it for.",
    )
    async def list_ball(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        ball: BallInstanceTransform,
        price: int,
    ) -> None:
        """
        List a ball for sale at a price you set.
        Other players can browse with /economy listings and buy with /economy buy.
        """
        if not await self._check_currency(interaction):
            return

        if price < 1:
            await interaction.response.send_message(
                "Price must be at least 1.", ephemeral=True
            )
            return

        config = self._config or await get_config()
        if config is None or not config.sell_enabled:
            await interaction.response.send_message(
                "Selling is currently disabled.", ephemeral=True
            )
            return

        try:
            player = await Player.objects.aget(discord_id=interaction.user.id)
        except Player.DoesNotExist:
            await interaction.response.send_message(
                f"You don't have a player profile yet. "
                f"Catch some {settings.plural_collectible_name} first!",
                ephemeral=True,
            )
            return

        if ball.player_id != player.pk:
            await interaction.response.send_message(
                f"You don't own that {settings.collectible_name}.", ephemeral=True
            )
            return

        if ball.favorite:
            await interaction.response.send_message(
                f"You can't list a favourited {settings.collectible_name}. "
                "Unfavourite it first.",
                ephemeral=True,
            )
            return

        if await BallListing.objects.filter(ball_instance=ball, sold=False).aexists():
            await interaction.response.send_message(
                f"That {settings.collectible_name} is already listed for sale.",
                ephemeral=True,
            )
            return

        from django.utils import timezone
        expires_at = timezone.now() + timedelta(hours=config.listing_expiry_hours)

        await BallListing.objects.acreate(
            seller=player,
            ball_instance=ball,
            price=price,
            expires_at=expires_at,
        )

        embed = discord.Embed(
            title=f"{settings.collectible_name.title()} Listed",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name=settings.collectible_name.title(),
            value=f"**{ball.ball.country}**",
            inline=True,
        )
        embed.add_field(
            name="Listed Price",
            value=fmt_currency(price),
            inline=True,
        )
        embed.add_field(
            name="Expires",
            value=f"<t:{int(expires_at.timestamp())}:R>",
            inline=True,
        )
        embed.set_footer(
            text=f"A {config.listing_platform_fee * 100:.0f}% platform fee is deducted on sale. "
                 f"Use /economy delist #{ball.pk:X} to cancel."
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @economy_group.command(name="listings")
    async def listings(self, interaction: discord.Interaction["BallsDexBot"]) -> None:
        """
        Browse all active ball listings from other players.
        """
        if not await self._check_currency(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        active = await BallListing.objects.filter(
            sold=False
        ).select_related(
            "ball_instance__ball",
            "ball_instance__special",
            "seller",
        ).order_by("price").alist()

        if not active:
            await interaction.followup.send(
                f"There are no active listings right now.\n"
                f"Use `/economy list` to sell your {settings.plural_collectible_name}!",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"{settings.plural_collectible_name.title()} Market",
            description=(
                f"**{len(active)}** active listing{'s' if len(active) != 1 else ''}  •  "
                f"Sorted by price  •  Use `/economy buy <id>` to purchase"
            ),
            color=discord.Color.blurple(),
        )

        for listing in active[:20]:
            bi = listing.ball_instance
            special_tag = f" *{bi.specialcard.name}*" if bi.special_id and bi.specialcard else ""
            atk = f"{bi.attack_bonus:+}%" if bi.attack_bonus else "—"
            hp = f"{bi.health_bonus:+}%" if bi.health_bonus else "—"
            embed.add_field(
                name=f"`#{listing.pk}` {bi.ball.country}{special_tag}",
                value=(
                    f"💰 **{fmt_currency(listing.price)}**\n"
                    f"ATK: {atk} | HP: {hp}\n"
                    f"Seller: <@{listing.seller.discord_id}>\n"
                    f"Expires: <t:{int(listing.expires_at.timestamp())}:R>"
                ),
                inline=True,
            )

        if len(active) > 20:
            embed.set_footer(text=f"Showing 20 of {len(active)} listings.")

        await interaction.followup.send(embed=embed, ephemeral=True)

    @economy_group.command(name="buy")
    @app_commands.describe(listing_id="The listing ID from /economy listings.")
    async def buy(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        listing_id: int,
    ) -> None:
        """
        Buy a ball from another player's listing.
        """
        if not await self._check_currency(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        config = self._config or await get_config()
        if config is None:
            await interaction.followup.send(
                "Economy is not configured. Contact an administrator.", ephemeral=True
            )
            return

        try:
            listing = await BallListing.objects.select_related(
                "ball_instance__ball",
                "ball_instance__special",
                "seller",
            ).aget(pk=listing_id, sold=False)
        except BallListing.DoesNotExist:
            await interaction.followup.send(
                "That listing doesn't exist or has already been sold.", ephemeral=True
            )
            return

        try:
            buyer = await Player.objects.aget(discord_id=interaction.user.id)
        except Player.DoesNotExist:
            await interaction.followup.send(
                f"You don't have a player profile yet. "
                f"Catch some {settings.plural_collectible_name} first!",
                ephemeral=True,
            )
            return

        if listing.seller_id == buyer.pk:
            await interaction.followup.send(
                "You can't buy your own listing. Use `/economy delist` to remove it.",
                ephemeral=True,
            )
            return

        if not buyer.can_afford(listing.price):
            await interaction.followup.send(
                f"You can't afford this listing.\n"
                f"Your balance: **{fmt_currency(buyer.money)}**\n"
                f"Listing price: **{fmt_currency(listing.price)}**",
                ephemeral=True,
            )
            return

        fee = int(listing.price * config.listing_platform_fee)
        seller_receives = listing.price - fee

        await Player.objects.filter(pk=buyer.pk).aupdate(money=buyer.money - listing.price)
        seller = listing.seller
        await Player.objects.filter(pk=seller.pk).aupdate(money=seller.money + seller_receives)

        bi = listing.ball_instance
        bi.player = buyer
        await bi.asave(update_fields=["player"])

        from django.utils import timezone
        listing.sold = True
        listing.buyer = buyer
        listing.sold_at = timezone.now()
        await listing.asave(update_fields=["sold", "buyer", "sold_at"])

        special_tag = (
            f" — *{bi.specialcard.name}*" if bi.special_id and bi.specialcard else ""
        )

        embed = discord.Embed(
            title="Purchase Successful",
            color=discord.Color.green(),
        )
        embed.add_field(
            name=f"{settings.collectible_name.title()} Purchased",
            value=f"**{bi.ball.country}**{special_tag}",
            inline=True,
        )
        embed.add_field(
            name="You Paid",
            value=fmt_currency(listing.price),
            inline=True,
        )
        embed.add_field(
            name="New Balance",
            value=fmt_currency(buyer.money - listing.price),
            inline=True,
        )
        embed.set_footer(
            text=f"Seller received {fmt_currency(seller_receives)} after the "
                 f"{config.listing_platform_fee * 100:.0f}% platform fee."
        )

        await interaction.followup.send(embed=embed, ephemeral=True)

    @economy_group.command(name="delist")
    @app_commands.describe(listing_id="The listing ID to remove.")
    async def delist(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        listing_id: int,
    ) -> None:
        """
        Remove one of your active listings and get the ball back.
        """
        if not await self._check_currency(interaction):
            return

        try:
            player = await Player.objects.aget(discord_id=interaction.user.id)
        except Player.DoesNotExist:
            await interaction.response.send_message(
                "You don't have a player profile yet.", ephemeral=True
            )
            return

        try:
            listing = await BallListing.objects.select_related(
                "ball_instance__ball"
            ).aget(pk=listing_id, sold=False, seller=player)
        except BallListing.DoesNotExist:
            await interaction.response.send_message(
                "That listing doesn't exist or doesn't belong to you.", ephemeral=True
            )
            return

        bi = listing.ball_instance
        await listing.adelete()

        embed = discord.Embed(
            title="Listing Removed",
            color=discord.Color.orange(),
        )
        embed.add_field(
            name=f"{settings.collectible_name.title()} Returned",
            value=f"**{bi.ball.country}**",
            inline=True,
        )
        embed.set_footer(
            text="Your ball has been returned to your collection."
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @economy_group.command(name="mylistings")
    async def mylistings(self, interaction: discord.Interaction["BallsDexBot"]) -> None:
        """
        View all of your active listings.
        """
        if not await self._check_currency(interaction):
            return

        try:
            player = await Player.objects.aget(discord_id=interaction.user.id)
        except Player.DoesNotExist:
            await interaction.response.send_message(
                "You don't have a player profile yet.", ephemeral=True
            )
            return

        active = await BallListing.objects.filter(
            seller=player, sold=False
        ).select_related("ball_instance__ball", "ball_instance__special").alist()

        if not active:
            await interaction.response.send_message(
                f"You have no active listings. "
                f"Use `/economy list` to sell a {settings.collectible_name}!",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="Your Active Listings",
            description=f"You have **{len(active)}** active listing{'s' if len(active) != 1 else ''}.",
            color=discord.Color.blurple(),
        )
        for listing in active:
            bi = listing.ball_instance
            special_tag = (
                f" *{bi.specialcard.name}*" if bi.special_id and bi.specialcard else ""
            )
            embed.add_field(
                name=f"`#{listing.pk}` {bi.ball.country}{special_tag}",
                value=(
                    f"💰 **{fmt_currency(listing.price)}**\n"
                    f"Expires: <t:{int(listing.expires_at.timestamp())}:R>"
                ),
                inline=True,
            )
        embed.set_footer(text="Use /economy delist <id> to remove a listing.")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── Layer 3: Passive income claiming ─────────────────────────────────────

    @economy_group.command(name="pending")
    async def pending(self, interaction: discord.Interaction["BallsDexBot"]) -> None:
        """
        Check how much passive income is waiting to be claimed.
        """
        if not await self._check_currency(interaction):
            return

        try:
            player = await Player.objects.aget(discord_id=interaction.user.id)
        except Player.DoesNotExist:
            await interaction.response.send_message(
                f"You don't have a player profile yet. "
                f"Catch some {settings.plural_collectible_name} first!",
                ephemeral=True,
            )
            return

        try:
            pool = await PassiveIncomePool.objects.aget(player=player)
            pending = pool.pending
            last_tick = pool.last_tick
        except PassiveIncomePool.DoesNotExist:
            pending = 0
            last_tick = None

        config = self._config or await get_config()

        embed = discord.Embed(
            title="Passive Income",
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="⏳ Pending",
            value=fmt_currency(pending),
            inline=True,
        )
        embed.add_field(
            name="💰 Current Balance",
            value=fmt_currency(player.money),
            inline=True,
        )
        if last_tick:
            embed.add_field(
                name="Last Tick",
                value=f"<t:{int(last_tick.timestamp())}:R>",
                inline=True,
            )
        if config:
            embed.set_footer(
                text=(
                    f"Each {settings.collectible_name} you own has a "
                    f"{config.passive_chance * 100:.0f}% chance to generate "
                    f"{config.passive_min}–{config.passive_max} {settings.currency_plural} "
                    f"every {config.passive_interval_minutes} minutes."
                )
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @economy_group.command(name="claim")
    async def claim(self, interaction: discord.Interaction["BallsDexBot"]) -> None:
        """
        Claim all of your accumulated passive income.
        """
        if not await self._check_currency(interaction):
            return

        try:
            player = await Player.objects.aget(discord_id=interaction.user.id)
        except Player.DoesNotExist:
            await interaction.response.send_message(
                f"You don't have a player profile yet. "
                f"Catch some {settings.plural_collectible_name} first!",
                ephemeral=True,
            )
            return

        try:
            pool = await PassiveIncomePool.objects.aget(player=player)
        except PassiveIncomePool.DoesNotExist:
            await interaction.response.send_message(
                f"You have no passive income to claim yet. "
                f"Own some {settings.plural_collectible_name} and check back later!",
                ephemeral=True,
            )
            return

        if pool.pending == 0:
            await interaction.response.send_message(
                "No passive income to claim right now. Check back later!",
                ephemeral=True,
            )
            return

        earned = pool.pending
        pool.pending = 0
        await pool.asave(update_fields=["pending"])
        new_balance = player.money + earned
        await Player.objects.filter(pk=player.pk).aupdate(money=new_balance)

        embed = discord.Embed(
            title=f"{settings.currency_name} Claimed!",
            color=discord.Color.green(),
        )
        embed.add_field(
            name="💰 Claimed",
            value=fmt_currency(earned),
            inline=True,
        )
        embed.add_field(
            name="New Balance",
            value=fmt_currency(new_balance),
            inline=True,
        )
        embed.set_footer(
            text=f"Your {settings.plural_collectible_name} continue generating "
                 f"passive {settings.currency_plural} automatically."
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)
