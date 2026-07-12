import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("bd_models", "0014_alter_ball_options_alter_ballinstance_options_and_more"),
        ("economy", "0002_ballshopprice"),
    ]

    operations = [
        migrations.CreateModel(
            name="SpecialEconomyBonus",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "special",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="economy_bonus",
                        to="bd_models.special",
                    ),
                ),
                ("quicksell_multiplier_enabled", models.BooleanField(default=False)),
                ("quicksell_multiplier", models.FloatField(default=2.0)),
                ("passive_multiplier_enabled", models.BooleanField(default=False)),
                ("passive_multiplier", models.FloatField(default=2.0)),
            ],
            options={
                "verbose_name": "Special Economy Bonus",
                "verbose_name_plural": "Special Economy Bonuses",
                "db_table": "economy_special_bonus",
                "managed": True,
            },
        ),
    ]
