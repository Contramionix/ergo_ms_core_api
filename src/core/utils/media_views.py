from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from src.core.utils.media_signing import get_upload_info
from src.core.utils.media_upload_quota import resolve_upload_quota


class MediaUploadTokenView(APIView):
    """
    Генерирует upload-токен для загрузки файла в media_api.

    POST /api/utils/media/upload-token/
    На процессе модуля также: POST /api/<name>/media/upload-token/
    Body: {target_dir, max_size?, allowed_types?}
    Response: {upload_url, token}

    Параметры проверяются на сервере (см. media_upload_validation).
    Класс квоты: политика модуля по target_dir или user/admin.
    Поле quota в теле запроса игнорируется.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        target_dir = request.data.get('target_dir', '')
        max_size = request.data.get('max_size')
        allowed_types = request.data.get('allowed_types')
        resolved = resolve_upload_quota(user=request.user, target_dir=target_dir)

        upload_info = get_upload_info(
            user_id=request.user.id,
            target_dir=target_dir,
            max_size=max_size,
            allowed_types=allowed_types,
            quota=resolved.quota,
            rate=resolved.rate,
        )
        return Response(upload_info)
