from rest_framework.exceptions import ValidationError

from src.core.utils.media_client import get_media_client


def validate_media_path(file_path: str, field_name: str = 'file') -> str:
    """
    Нормализует и проверяет путь к файлу внутри media-хранилища.
    Возвращает путь в формате storage (слэши /).
    """
    client = get_media_client()
    try:
        storage_name = client.normalize_path(file_path)
    except ValueError as exc:
        raise ValidationError({field_name: str(exc)}) from exc
    if not client.exists(storage_name):
        raise ValidationError({field_name: f'Файл не найден: {storage_name}'})
    return storage_name


def read_storage_file_bytes(storage_name: str) -> bytes:
    """Прочитать содержимое файла из media-хранилища по проверенному пути."""
    return get_media_client().read_bytes(storage_name)


class SwaggerSafeMixin:
    """Безопасный user/queryset при генерации схемы Swagger и для гостей."""

    def is_swagger_fake_view(self):
        return getattr(self, 'swagger_fake_view', False)

    def get_safe_user(self):
        """None при Swagger fake-view и для неаутентифицированного запроса."""
        if self.is_swagger_fake_view():
            return None
        user = getattr(getattr(self, 'request', None), 'user', None)
        if user is None or not getattr(user, 'is_authenticated', False):
            return None
        return user

    def restrict_queryset(self, queryset):
        """Пустая выборка для Swagger и гостей. Дальше фильтруй по владельцу/scope."""
        if self.get_safe_user() is None:
            return queryset.none()
        return queryset

    def get_safe_queryset(self, base_queryset):
        return self.restrict_queryset(base_queryset)


class SwaggerSafeSerializerMixin:
    """get_safe_user() из context['request'] — для SerializerMethodField."""

    def get_safe_user(self):
        request = self.context.get('request') if getattr(self, 'context', None) else None
        if request is None:
            return None
        user = getattr(request, 'user', None)
        if user is None or not getattr(user, 'is_authenticated', False):
            return None
        return user


class MediaApiFileMixin:
    """
    Миксин для views, принимающих как прямой upload (request.FILES),
    так и путь к файлу, загруженному через media_api (file_path).

    Использование во view:
        file, file_path = self.get_file_or_path('file')
        if file:
            instance.file.save(file.name, file)
        elif file_path:
            instance.file.name = file_path
            instance.save()
    """

    def get_file_or_path(self, field_name='file'):
        """
        Возвращает (file, None) при прямой загрузке
        или (None, path) при загрузке через media_api.
        Путь проверяется на path traversal — допустимы только пути внутри MEDIA_ROOT.
        """
        file_path = self.request.data.get(f'{field_name}_path')
        if file_path:
            return None, validate_media_path(file_path, field_name)
        return self.request.FILES.get(field_name), None

    @staticmethod
    def assign_file_field(instance, field_name, file=None, file_path=None):
        """
        Присваивает файл или путь в FileField/ImageField.
        Если file — сохраняет через storage. Если file_path — ставит name напрямую.
        """
        field = getattr(instance, field_name)
        if file:
            field.save(file.name, file, save=False)
        elif file_path:
            field.name = file_path