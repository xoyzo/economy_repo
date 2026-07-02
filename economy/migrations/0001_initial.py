import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("bd_models", "0014_alter_ball_options_alter_ballinstance_options_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="EconomyConfig",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("catch_income_enabled", models.BooleanField(default=True, help_text="Whether catching balls earns money.")),
                ("catch_base_min", models.IntegerField(default=1, help_text="Minimum base money earned per catch before rarity scaling.")),
                ("catch_base_max", models.IntegerField(default=10, help_text="Maximum base money earned per catch before rarity scaling.")),
                ("catch_rarity_multiplier", models.FloatField(default=100.0, help_text="Rarity is a float 0-1. Earned = random(base_min, base_max) + rarity * catch_rarity_multiplier.")),
                ("catch_special_bonus", models.IntegerField(default=25, help_text="Flat bonus money added when the caught ball has a special.")),
                ("sell_enabled", models.BooleanField(default=True, help_text="Whether players can sell balls.")),
                ("sell_base_min", models.IntegerField(default=5, help_text="Minimum base sell price before rarity and stat scaling.")),
                ("sell_base_max", models.IntegerField(default=20, help_text="Maximum base sell price before rarity and stat scaling.")),
                ("sell_rarity_multiplier", models.FloatField(default=200.0, help_text="Base sell price = random(sell_base_min, sell_base_max) + rarity * sell_rarity_multiplier.")),
                ("sell_special_multiplier", models.FloatField(default=1.5, help_text="Multiplier applied to sell price when the ball has a special.")),
                ("sell_stat_multiplier", models.FloatField(default=0.5, help_text="Each point of (attack_bonus + health_bonus) adds sell_stat_multiplier money to sell price.")),
                ("listing_platform_fee", models.FloatField(default=0.05, help_text="Fraction of sale price taken as platform fee on player listings.")),
                ("listing_expiry_hours", models.IntegerField(default=48, help_text="Hours before an unsold listing expires and ball is returned.")),
                ("passive_enabled", models.BooleanField(default=True, help_text="Whether balls generate passive income.")),
                ("passive_chance", models.FloatField(default=0.05, help_text="Chance (0.0-1.0) each ball generates passive income per interval.")),
                ("passive_interval_minutes", models.IntegerField(default=10, help_text="How often in minutes the passive income task runs.")),
                ("passive_min", models.IntegerField(default=1, help_text="Minimum currency generated per ball per successful passive tick.")),
                ("passive_max", models.IntegerField(default=20, help_text="Maximum currency generated per ball per successful passive tick.")),
                ("passive_rarity_multiplier", models.FloatField(default=1.0, help_text="Passive income multiplied by (1 + rarity * passive_rarity_multiplier).")),
            ],
            options={"db_table": "economy_config", "managed": True, "verbose_name": "Economy Config", "verbose_name_plural": "Economy Config"},
        ),
        migrations.CreateModel(
            name="PassiveIncomePool",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("player", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="passive_pool", to="bd_models.player")),
                ("pending", models.PositiveBigIntegerField(default=0, help_text="Currency accumulated but not yet claimed.")),
                ("last_tick", models.DateTimeField(auto_now_add=True, help_text="Last time the passive task ran for this player.")),
            ],
            options={"db_table": "economy_passive_pool", "managed": True},
        ),
        migrations.CreateModel(
            name="BallListing",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("seller", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="listings", to="bd_models.player")),
                ("ball_instance", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="listing", to="bd_models.ballinstance", help_text="The ball instance being listed for sale.")),
                ("price", models.PositiveBigIntegerField(help_text="Price in currency set by the seller.")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField(help_text="When this listing expires and the ball is returned to the seller.")),
                ("sold", models.BooleanField(default=False)),
                ("buyer", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="purchases", to="bd_models.player")),
                ("sold_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={"db_table": "economy_ball_listing", "managed": True},
        ),
    ]
