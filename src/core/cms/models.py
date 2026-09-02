from django.db import models

# CMSPage — активная модель (права на URL, синхронизация маршрутов).

PAGE_TYPE_WITH_LIMITATIONS = 'withliminations'
PAGE_TYPE_WITHOUT_LIMITATIONS = 'withoutliminations'
PAGE_TYPE_CLOSED = 'closepage'

PAGE_TYPE_CHOICES = [
    (PAGE_TYPE_WITH_LIMITATIONS, 'Страница с ограничениями'),
    (PAGE_TYPE_WITHOUT_LIMITATIONS, 'Страница без ограничений'),
    (PAGE_TYPE_CLOSED, 'Закрытая страница'),
]


class CMSPage(models.Model):
    path = models.CharField(max_length=255, default='')
    page_type = models.CharField(
        max_length=255,
        choices=PAGE_TYPE_CHOICES,
        default=PAGE_TYPE_WITHOUT_LIMITATIONS,
    )


class ApiEndpoint(models.Model):
    """Каталог HTTP API path для picker политик policy_type=api."""

    path = models.CharField(max_length=500, unique=True)
    name = models.CharField(max_length=255, blank=True, default='')
    module_name = models.CharField(max_length=100, blank=True, default='core')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'API-эндпоинт'
        verbose_name_plural = 'API-эндпоинты'
        ordering = ['path']

    def __str__(self):
        return self.path
