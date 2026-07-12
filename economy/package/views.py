"""Interactive views for the economy package."""

from typing import TYPE_CHECKING

import discord

from bd_models.models import BallInstance, Player

from ..models import EconomySettings
from .helpers import compute_quicksell_price_for, estimate_quicksell_range, fmt

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot


class BulkQuicksellView(discord.ui.View):
    """Paginated select for selling multiple balls at once."""

    def __init__(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        balls: list[BallInstance],
        cfg: EconomySettings,
        price_cache: dict[int, tuple[int, int]],
    ):
        super().__init__(timeout=120)
        self.interaction = interaction
        self.all_balls = balls
        self.cfg = cfg
        self.price_cache = price_cache
        self.page = 0
        self.per_page = 25
        self.selected_pks: set[int] = set()
        self._build_select()

    def _build_select(self) -> None:
        # Remove old select if exists
        for child in list(self.children):
            if isinstance(child, discord.ui.Select):
                self.remove_item(child)

        start = self.page * self.per_page
        page_balls = self.all_balls[start : start + self.per_page]

        options = []
        for bi in page_balls:
            est_min, est_max = estimate_quicksell_range(bi, self.cfg, self.price_cache)
            special_tag = f" [{bi.special.name}]" if bi.special_id and bi.special else ""
            label = f"{bi.ball.country}{special_tag}"[:100]
            desc = f"ATK {bi.attack_bonus:+}% HP {bi.health_bonus:+}% | Est. {est_min}–{est_max}"[:100]
            options.append(
                discord.SelectOption(
                    label=label,
                    value=str(bi.pk),
                    description=desc,
                    default=bi.pk in self.selected_pks,
                )
            )

        select = discord.ui.Select(
            placeholder=f"Select balls to sell (page {self.page + 1}/{self._max_pages()})",
            options=options,
            min_values=0,
            max_values=len(options),
        )
        select.callback = self._on_select
        self.add_item(select)

    def _max_pages(self) -> int:
        return max(1, (len(self.all_balls) + self.per_page - 1) // self.per_page)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        # Update selection
        start = self.page * self.per_page
        page_balls = self.all_balls[start : start + self.per_page]
        page_pks = {bi.pk for bi in page_balls}
        # Remove deselected from this page
        self.selected_pks -= page_pks
        # Add newly selected
        for v in interaction.data.get("values", []):
            self.selected_pks.add(int(v))
        self._build_select()
        await interaction.edit_original_response(embed=self._build_embed(), view=self)

    def _build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="Bulk Quick Sell",
            description=(
                f"Select balls to sell across pages, then click **Confirm**.\n"
                f"**{len(self.selected_pks)}** ball(s) selected."
            ),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"Page {self.page + 1}/{self._max_pages()} • {len(self.all_balls)} eligible balls")
        return embed

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary, row=1)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        if self.page > 0:
            self.page -= 1
            self._build_select()
        await interaction.edit_original_response(embed=self._build_embed(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary, row=1)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        if self.page < self._max_pages() - 1:
            self.page += 1
            self._build_select()
        await interaction.edit_original_response(embed=self._build_embed(), view=self)

    @discord.ui.button(label="Confirm Sell", style=discord.ButtonStyle.danger, row=1)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        if not self.selected_pks:
            await interaction.followup.send("No balls selected.", ephemeral=True)
            return

        self.stop()
        # Process the sell
        total_price = 0
        count = 0
        for bi in self.all_balls:
            if bi.pk not in self.selected_pks:
                continue
            price = await compute_quicksell_price_for(bi, self.cfg, self.price_cache)
            total_price += price
            count += 1
            bi.deleted = True
            await bi.asave(update_fields=["deleted"])

        player = await Player.objects.aget(discord_id=interaction.user.id)
        await player.add_money(total_price)
        await player.arefresh_from_db(fields=["money"])

        embed = discord.Embed(title="Bulk Sell Complete", color=discord.Color.green())
        embed.add_field(name="Balls Sold", value=str(count), inline=True)
        embed.add_field(name="Total Received", value=fmt(total_price, interaction.client), inline=True)
        embed.add_field(name="New Balance", value=fmt(player.money, interaction.client), inline=True)
        await interaction.edit_original_response(embed=embed, view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, row=1)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        self.stop()
        await interaction.edit_original_response(
            embed=discord.Embed(title="Cancelled", color=discord.Color.orange()),
            view=None,
        )
