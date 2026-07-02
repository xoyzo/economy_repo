from django.apps import AppConfig


class EconomyConfig(AppConfig):
    name = "economy"
    dpy_package = "economy.package"

    def ready(self) -> None:
        from django.db.models.signals import post_migrate
        post_migrate.connect(_create_default_config, sender=self)


def _create_default_config(sender, **kwargs) -> None:
    from .models import EconomyConfig
    if not EconomyConfig.objects.exists():
        EconomyConfig.objects.create()
        import logging
        logging.getLogger(__name__).info(
            "Economy: created default EconomyConfig record. "
            "Adjust rates in the admin panel."
        )
