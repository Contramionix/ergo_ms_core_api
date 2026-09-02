"""
API для приглашений на регистрацию и публичных настроек регистрации.
"""

from django.utils.translation import gettext as _
from src.core.search.mixins import parse_search_pagination
from src.core.utils.swagger.yasg_compat import swagger_auto_schema, openapi
from rest_framework import status
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, ScopedRateThrottle

from src.core.utils.base.base_views import BaseAPIView, BaseAPIViewGlobalAdminMixin, BaseAPIViewPublicMixin
from src.core.cms.adp.models import RegistrationInvitation
from src.core.cms.adp.serializers import (
    RegistrationInvitationSerializer,
    CreateRegistrationInvitationSerializer,
    BulkCreateRegistrationInvitationsSerializer,
    BulkSendRegistrationInvitationsSerializer,
    ClearRegistrationInvitationsSerializer,
    ValidateInvitationSerializer,
)
from src.core.cms.adp.services.registration import RegistrationService
from src.core.audit.shortcuts import audit_log

def _serialize_invitation(invitation):
    return RegistrationInvitationSerializer(invitation).data


def _parse_pagination(request, default_page_size=12):
    page, page_size, search = parse_search_pagination(
        request,
        default_page_size=default_page_size,
        max_page_size=100,
    )
    status = (request.query_params.get('status') or '').strip().lower()
    return page, page_size, search, status


class RegistrationSettingsView(BaseAPIViewPublicMixin):
    """Публичные настройки режима регистрации."""

    @swagger_auto_schema(
        operation_description='Получить настройки режима регистрации',
        responses={200: openapi.Response(description='Настройки регистрации')},
    )
    def get(self, request):
        return Response(RegistrationService.get_public_settings())


class ValidateInvitationView(BaseAPIViewPublicMixin):
    """Проверка токена приглашения (публичный endpoint)."""

    throttle_classes = [AnonRateThrottle, ScopedRateThrottle]
    throttle_scope = 'registration'

    @swagger_auto_schema(
        operation_description='Проверить токен приглашения',
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'token': openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description='Токен приглашения',
                ),
            },
            required=['token'],
        ),
        responses={200: ValidateInvitationSerializer()},
    )
    def post(self, request):
        closed = RegistrationService.reject_if_registration_closed()
        if closed:
            return closed

        token = (request.data.get('token') or '').strip()
        invitation = RegistrationService.get_invitation_by_token(token)

        if not invitation:
            return Response({
                'valid': False,
                'email': None,
                'expires_at': None,
                'status': None,
            })

        invitation_status = RegistrationService.get_invitation_status(invitation)
        return Response({
            'valid': invitation_status == 'pending',
            'email': invitation.email,
            'expires_at': invitation.expires_at,
            'status': invitation_status,
        })


class RegistrationInvitationListView(BaseAPIViewGlobalAdminMixin, BaseAPIView):
    """Список и создание приглашений (только глобальные администраторы)."""

    @swagger_auto_schema(
        operation_description='Получить список приглашений',
        responses={200: RegistrationInvitationSerializer(many=True)},
    )
    def get(self, request):
        page, page_size, search, status_filter = _parse_pagination(request)
        queryset = RegistrationInvitation.objects.select_related('invited_by').order_by('-created_at')
        queryset = RegistrationService.filter_invitations_queryset_by_status(queryset, status_filter)

        from src.core.search.core_indexes import INDEX_INVITATIONS
        from src.core.search.service import search_queryset

        queryset, search_result = search_queryset(
            INDEX_INVITATIONS,
            search,
            queryset,
            page=page,
            page_size=page_size,
        )

        total = search_result.total
        total_all = RegistrationInvitation.objects.count()
        invitations = list(queryset)
        serializer = RegistrationInvitationSerializer(invitations, many=True)

        inactive_count = RegistrationService.get_inactive_invitations_queryset().count()
        pending_count = RegistrationService.get_pending_invitations_queryset().count()

        return Response({
            'invitations': serializer.data,
            'total': total,
            'total_all': total_all,
            'pending_count': pending_count,
            'page': page,
            'page_size': page_size,
            'inactive_count': inactive_count,
            'registration_mode': RegistrationService.get_mode(),
        })

    @swagger_auto_schema(
        operation_description='Создать приглашение на регистрацию',
        request_body=CreateRegistrationInvitationSerializer,
        responses={201: RegistrationInvitationSerializer()},
    )
    def post(self, request):
        serializer = CreateRegistrationInvitationSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        invitation, error = RegistrationService.create_invitation(
            email=serializer.validated_data['email'],
            invited_by=request.user,
            note=serializer.validated_data.get('note', ''),
            send_email=serializer.validated_data.get('send_email', False),
        )
        if error and not invitation:
            return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

        audit_log('invitation.created', request=request,
               entity={'type': 'invitation', 'label': invitation.email})

        response_data = _serialize_invitation(invitation)
        if error:
            response_data['email_warning'] = error

        return Response(response_data, status=status.HTTP_201_CREATED)


class RegistrationInvitationDetailView(BaseAPIViewGlobalAdminMixin, BaseAPIView):
    def _get_invitation(self, invitation_id):
        try:
            return RegistrationInvitation.objects.select_related('invited_by').get(pk=invitation_id)
        except RegistrationInvitation.DoesNotExist:
            return None

    @swagger_auto_schema(
        operation_description='Отозвать приглашение',
        responses={204: 'Приглашение отозвано'},
    )
    def delete(self, request, invitation_id):
        invitation = self._get_invitation(invitation_id)
        if not invitation:
            return Response({'error': _('Приглашение не найдено')}, status=status.HTTP_404_NOT_FOUND)

        invitation_email = invitation.email
        success, error = RegistrationService.revoke_invitation(invitation)
        if not success:
            return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

        audit_log('invitation.revoked', request=request,
               entity={'type': 'invitation', 'label': invitation_email})
        return Response(status=status.HTTP_204_NO_CONTENT)


class RegistrationInvitationResendView(BaseAPIViewGlobalAdminMixin, BaseAPIView):
    @swagger_auto_schema(
        operation_description='Повторно отправить email с приглашением',
        responses={200: RegistrationInvitationSerializer()},
    )
    def post(self, request, invitation_id):
        try:
            invitation = RegistrationInvitation.objects.select_related('invited_by').get(pk=invitation_id)
        except RegistrationInvitation.DoesNotExist:
            return Response({'error': _('Приглашение не найдено')}, status=status.HTTP_404_NOT_FOUND)

        success, error = RegistrationService.send_invitation_email(invitation)
        if not success:
            return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

        return Response(_serialize_invitation(invitation))


class RegistrationInvitationBulkCreateView(BaseAPIViewGlobalAdminMixin, BaseAPIView):
    @swagger_auto_schema(
        operation_description='Массовое создание приглашений по списку email',
        request_body=BulkCreateRegistrationInvitationsSerializer,
    )
    def post(self, request):
        serializer = BulkCreateRegistrationInvitationsSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        result = RegistrationService.bulk_create_invitations(
            emails=serializer.validated_data['emails'],
            invited_by=request.user,
            note=serializer.validated_data.get('note', ''),
            send_email=serializer.validated_data.get('send_email', False),
        )
        audit_log('invitation.bulk_created', request=request,
               meta={'requested': len(serializer.validated_data['emails']),
                     'created': result.get('created')})
        return Response(result, status=status.HTTP_201_CREATED)


class RegistrationInvitationClearView(BaseAPIViewGlobalAdminMixin, BaseAPIView):
    """Массовая очистка приглашений (только глобальные администраторы)."""

    @swagger_auto_schema(
        operation_description='Удалить приглашения: inactive — использованные, истёкшие и отозванные; all — все записи',
        request_body=ClearRegistrationInvitationsSerializer,
        responses={200: 'Количество удалённых записей'},
    )
    def post(self, request):
        serializer = ClearRegistrationInvitationsSerializer(data=request.data or {})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        scope = serializer.validated_data.get(
            'scope',
            ClearRegistrationInvitationsSerializer.SCOPE_INACTIVE,
        )
        result = RegistrationService.clear_invitations(scope=scope)

        if result.get('error'):
            return Response({'error': result['error']}, status=status.HTTP_400_BAD_REQUEST)

        if result['deleted'] == 0:
            return Response({
                'deleted': 0,
                'scope': scope,
                'message': _('Нет приглашений для удаления'),
            })

        audit_log('invitation.cleared', request=request, severity='security',
               meta={'scope': scope, 'deleted': result.get('deleted')})
        return Response(result)


class RegistrationInvitationBulkSendView(BaseAPIViewGlobalAdminMixin, BaseAPIView):
    @swagger_auto_schema(
        operation_description='Массовая отправка email с приглашениями',
        request_body=BulkSendRegistrationInvitationsSerializer,
    )
    def post(self, request):
        serializer = BulkSendRegistrationInvitationsSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        result = RegistrationService.bulk_send_invitation_emails(
            invitation_ids=serializer.validated_data['invitation_ids'],
        )
        return Response(result)
