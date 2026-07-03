from django.apps import AppConfig


class EconomyConfig(AppConfig):
    name = "economy"
    dpy_package = "economy.package"

    def ready(self) -> None:
        from django.db.models.signals import post_migrate
        post_migrate.connect(_create_default_config, sender=self)


def _create_default_config(sender, **kwargs) -> None:
    from .models import EconomySettings
    import logging
    log = logging.getLogger(__name__)
    if not EconomySettings.objects.exists():
        EconomySettings.objects.create()
        log.info(
            "Economy: created default EconomySettings record with default values. "
            "Adjust rates in the admin panel under Economy > Economy Settings."
        )
