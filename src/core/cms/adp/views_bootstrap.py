from src.core.utils.swagger.yasg_compat import swagger_auto_schema, openapi
from rest_framework import status
from rest_framework.response import Response

from src.core.cms.adp.services.session_bootstrap import build_session_bootstrap_payload
from src.core.cms.adp.services.session_devices import touch_device_activity
from src.core.integrations.session_context import session_claims_from_request
from src.core.utils.base.base_views import BaseAPIView, BaseAPIViewAuthMixin


class SessionBootstrapView(BaseAPIViewAuthMixin, BaseAPIView):
    """
    Агрегированные данные сессии для холодного старта клиента.

    Агрегирует данные сессии (пользователь, меню, профиль, аватар,
    realtime/config и т.п.) одним ответом, включая access_to_panel.
    """

    @swagger_auto_schema(
        operation_description=(
            'Данные сессии для инициализации клиента: пользователь, меню, '
            'профиль, аватар, права, доступ к админ-панели, realtime-конфиг.'
        ),
        responses={
            200: openapi.Response(
                description='Данные сессии',
                schema=openapi.Schema(type=openapi.TYPE_OBJECT),
            ),
            401: 'Не авторизован',
        },
        security=[{'Bearer': []}],
    )
    def get(self, request):
        touch_device_activity(request)
        return Response(
            build_session_bootstrap_payload(
                request.user,
                session_claims=session_claims_from_request(request),
            ),
            status=status.HTTP_200_OK,
        )
