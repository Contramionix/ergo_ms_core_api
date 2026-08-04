"""Публичные auth-endpoint'ы: регистрация, сброс пароля, вход."""
import logging
import re

from django.contrib.auth import authenticate
from django.contrib.auth import get_user_model
from django.contrib.auth.models import update_last_login
from django.utils.translation import gettext as _

User = get_user_model()
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.serializers import ValidationError as DRFValidationError
from rest_framework.throttling import AnonRateThrottle, ScopedRateThrottle

from src.core.cms.adp.auth_cookies import (
    clear_auth_cookies,
    clear_prev_user_cookie,
    get_prev_user_id_from_request,
    refresh_cookie_max_age,
    set_refresh_cookie,
)
from src.core.cms.adp.models import UserDevice, UserProfile
from src.core.cms.adp.password_policy import validate_new_password_pair
from src.core.cms.adp.serializers import (
    UserLoginSerializer,
    UserRegistrationSerializer,
    UserRegistrationValidationSerializer,
)
from src.core.cms.adp.services.password_reset import PasswordResetService
from src.core.cms.adp.services.profile_settings import ProfileSettingsService
from src.core.cms.adp.services.session_devices import (
    attach_device_claim,
    attach_device_to_refresh_token,
    bind_device_to_refresh_token,
)
from src.core.cms.adp.services.user_deletion import revoke_user_auth
from src.core.cms.adp.session_context_tokens import ScopedSessionRefreshToken
from src.core.integrations import bridge
from src.core.integrations.module_contracts import SESSION_RESTORE_CLAIMS
from src.core.cms.adp.user_agent_utils import (
    build_device_display_name,
    detect_device_type,
    get_client_ip,
)
from src.core.utils.base.base_views import BaseAPIView
from src.core.utils.methods import parse_errors_to_dict, send_confirmation_email
from src.config.settings.auth import get_token_lifetime
from src.core.audit.shortcuts import audit_log

logger = logging.getLogger(__name__)


class UserRegistrationValidationView(BaseAPIView):
    @swagger_auto_schema(
        operation_description="Регистрация нового пользователя.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,

            properties={
                'first_name': openapi.Schema(
                    type=openapi.TYPE_STRING, 
                    description='Имя'
                ),
                'username': openapi.Schema(
                    type=openapi.TYPE_STRING, 
                    description='Логин'
                ),
                'email': openapi.Schema(
                    type=openapi.TYPE_STRING, 
                    format=openapi.FORMAT_EMAIL, 
                    description='Электронная почта'
                ),
                'password': openapi.Schema(
                    type=openapi.TYPE_STRING, 
                    format=openapi.FORMAT_PASSWORD, 
                    description='Пароль'
                ),
                'password_confirm': openapi.Schema(
                    type=openapi.TYPE_STRING, 
                    format=openapi.FORMAT_PASSWORD, 
                    description='Подтверждение пароля'
                ),
            },

            required=['first_name', 'username', 'email', 'password', 'password_confirm'],
        ),
        responses={
            201: "Пользователь успешно зарегистрирован.",
            400: "Регистрация не успешна."
        },
    )
    def post(self, request):
        serializer = UserRegistrationValidationSerializer(data=request.data)

        if serializer.is_valid():
            successful_response = Response(
                {"message": _("Валидация успешна.")},
                status=status.HTTP_200_OK
            )
            return successful_response
        
        errors = parse_errors_to_dict(serializer.errors)
        return Response(
            errors, 
            status=status.HTTP_400_BAD_REQUEST
        )

class PasswordResetSettingsView(BaseAPIView):
    """Публичные настройки восстановления пароля."""

    @swagger_auto_schema(
        operation_description='Получить настройки восстановления пароля',
        responses={200: openapi.Response(description='Настройки восстановления пароля')},
    )
    def get(self, request):
        return Response(PasswordResetService.get_public_settings())


class SendConfirmationCodeView(BaseAPIView):
    # ScopedRateThrottle — единственный класс, который читает throttle_scope;
    # без него действовал только общий anon-лимит (см. security-audit.md, В1).
    throttle_classes = [AnonRateThrottle, ScopedRateThrottle]
    throttle_scope = 'password_reset'

    @swagger_auto_schema(
        operation_description="Отправка кода подтверждения.",
    )   
    def post(self, request):
        purpose = request.data.get('purpose', '')
        is_password_reset = PasswordResetService.is_password_reset_purpose(purpose)
        if is_password_reset and not PasswordResetService.is_enabled():
            return Response(
                {'error': PasswordResetService.get_disabled_message()},
                status=status.HTTP_403_FORBIDDEN,
            )
        if is_password_reset and not PasswordResetService.is_email_delivery_ready():
            return Response(
                {'error': PasswordResetService.get_unavailable_message()},
                status=status.HTTP_403_FORBIDDEN,
            )

        email = request.data.get("email")
        if not email:
            return Response({"error": _("Отсутствует Email")}, status=status.HTTP_400_BAD_REQUEST)

        user_exists = User.objects.filter(email=email).exists()
        if not user_exists:
            return Response(
                {"message": _("Если пользователь с таким email существует, код будет отправлен")},
                status=status.HTTP_200_OK,
            )

        code = PasswordResetService.issue_code(email)
        success, error_message = send_confirmation_email(email, code)

        if not success:
            return Response(
                {
                    "error": _("Не удалось отправить письмо с кодом восстановления."),
                    "detail": error_message or _("Проверьте настройки SMTP."),
                    "email_sent": False,
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {"message": _("Код подтверждения отправлен"), "email_sent": True},
            status=status.HTTP_200_OK,
        )

class VerifyConfirmationCodeView(BaseAPIView):
    throttle_classes = [AnonRateThrottle, ScopedRateThrottle]
    throttle_scope = 'password_reset'

    @swagger_auto_schema(
        operation_description="Проверка кода подтверждения.",
    )
    def post(self, request):
        email = request.data.get("email")
        code = request.data.get("code")

        if not email or not code:
            return Response({"error": _("Email и код обязательны")}, status=status.HTTP_400_BAD_REQUEST)

        ok, error_message = PasswordResetService.verify_code(email, code, consume=False)
        if not ok:
            return Response({"error": error_message}, status=status.HTTP_400_BAD_REQUEST)

        return Response({"message": _("Код успешно подтвержден")}, status=status.HTTP_200_OK)

class ResetPasswordView(BaseAPIView):
    throttle_classes = [AnonRateThrottle, ScopedRateThrottle]
    throttle_scope = 'password_reset'

    @swagger_auto_schema(
        operation_description="Сброс пароля по коду подтверждения.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'email': openapi.Schema(
                    type=openapi.TYPE_STRING,
                    format=openapi.FORMAT_EMAIL,
                    description='Email пользователя'
                ),
                'code': openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description='Код подтверждения'
                ),
                'new_password': openapi.Schema(
                    type=openapi.TYPE_STRING,
                    format=openapi.FORMAT_PASSWORD,
                    description='Новый пароль'
                ),
                'confirm_password': openapi.Schema(
                    type=openapi.TYPE_STRING,
                    format=openapi.FORMAT_PASSWORD,
                    description='Подтверждение нового пароля'
                ),
            },
            required=['email', 'code', 'new_password', 'confirm_password'],
        ),
        responses={
            200: "Пароль успешно изменён.",
            400: "Ошибка валидации данных."
        },
    )
    def post(self, request):
        if not PasswordResetService.is_enabled():
            return Response(
                {'error': PasswordResetService.get_disabled_message()},
                status=status.HTTP_403_FORBIDDEN,
            )

        email = request.data.get("email")
        code = request.data.get("code")
        new_password = request.data.get("new_password")
        confirm_password = request.data.get("confirm_password")

        if not email or not code or not new_password or not confirm_password:
            return Response(
                {"error": _("Все поля обязательны для заполнения")},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            validate_new_password_pair({
                'new_password': new_password,
                'confirm_password': confirm_password,
            })
        except DRFValidationError as exc:
            detail = exc.detail
            if isinstance(detail, list):
                error_message = str(detail[0])
            elif isinstance(detail, dict):
                first_value = next(iter(detail.values()))
                error_message = str(first_value[0] if isinstance(first_value, list) else first_value)
            else:
                error_message = str(detail)
            return Response(
                {'error': error_message},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ok, error_message = PasswordResetService.verify_code(email, code, consume=True)
        if not ok:
            return Response(
                {"error": error_message},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            return Response(
                {"error": _("Пользователь с таким email не найден")},
                status=status.HTTP_400_BAD_REQUEST
            )

        user.set_password(new_password)
        user.save()
        revoke_user_auth(user)

        audit_log('user.password_reset', request=request, actor=user, severity='security',
                     entity={'type': 'user', 'label': user.get_full_name() or user.username})

        return Response(
            {"message": _("Пароль успешно изменён")},
            status=status.HTTP_200_OK
        )
        
class UserRegistrationView(BaseAPIView):
    @swagger_auto_schema(
        operation_description="Проверка регистрации.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,

            properties={
                'first_name': openapi.Schema(
                    type=openapi.TYPE_STRING, 
                    description='Имя'
                ),
                'username': openapi.Schema(
                    type=openapi.TYPE_STRING, 
                    description='Логин'
                ),
                'email': openapi.Schema(
                    type=openapi.TYPE_STRING, 
                    format=openapi.FORMAT_EMAIL, 
                    description='Электронная почта'
                ),
                'password': openapi.Schema(
                    type=openapi.TYPE_STRING, 
                    format=openapi.FORMAT_PASSWORD, 
                    description='Пароль'
                ),
                'password_confirm': openapi.Schema(
                    type=openapi.TYPE_STRING, 
                    format=openapi.FORMAT_PASSWORD, 
                    description='Подтверждение пароля'
                ),
            },

            required=['first_name', 'username', 'email', 'password', 'password_confirm'],
        ),
        responses={
            201: "Пользователь успешно зарегистрирован.",
            400: "Регистрация не успешна."
        },
    )
    def post(self, request):
        serializer = UserRegistrationSerializer(data=request.data)

        if serializer.is_valid():
            user = serializer.save()

            successful_response = Response(
                {
                    "message": _("Регистрация успешна."),
                    "user_id": user.id,
                    "username": user.username
                },
                status=status.HTTP_201_CREATED
            )
            return successful_response

        errors = parse_errors_to_dict(serializer.errors)
        return Response(
            errors, 
            status=status.HTTP_400_BAD_REQUEST
        )

class UserAuthorizationView(BaseAPIView):
    # ScopedRateThrottle — единственный класс, который читает throttle_scope;
    # без него действовал только общий anon-лимит (см. security-audit.md, В1).
    throttle_classes = [AnonRateThrottle, ScopedRateThrottle]
    throttle_scope = 'login'

    @swagger_auto_schema(
        operation_description="Авторизация пользователя.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'username': openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description='Логин'
                ),
                'password': openapi.Schema(
                    type=openapi.TYPE_STRING,
                    format=openapi.FORMAT_PASSWORD,
                    description='Пароль'
                ),
                'remember_me': openapi.Schema(
                    type=openapi.TYPE_BOOLEAN,
                    description='Запомнить меня (увеличенное время жизни токенов)',
                    default=False
                ),
            },
            required=['username', 'password'],
        ),
            responses={
                200: openapi.Response(
                    description="Пользователь успешно авторизован.",
                    examples={
                        "application/json": {
                            "refresh": "your_refresh_token",
                            "access": "your_access_token"
                        }
                    }
                ),
                400: "Авторизация не успешна."
            },
    )
    def post(self, request):
        serializer = UserLoginSerializer(data=request.data)

        if serializer.is_valid():
            username = serializer.validated_data['username']
            password = serializer.validated_data['password']
            remember_me = request.data.get('remember_me', False)

            user = authenticate(request, username=username, password=password)

            if user is not None:
                update_last_login(None, user)
                device = self._create_or_update_device(request, user)

                access_lifetime, refresh_lifetime = get_token_lifetime(remember_me)

                # Смена аккаунта: cookie ergo_prev_user со прошлого logout.
                previous_user_id = get_prev_user_id_from_request(request)
                skip_restore = (
                    previous_user_id is not None
                    and previous_user_id != user.id
                )

                if skip_restore:
                    restore_claims = {}
                else:
                    restore_claims = bridge.call(SESSION_RESTORE_CLAIMS, user=user) or {}
                    if not isinstance(restore_claims, dict):
                        restore_claims = {}

                refresh = ScopedSessionRefreshToken.for_user_with_claims(
                    user,
                    **restore_claims,
                )
                refresh.set_exp(lifetime=refresh_lifetime)

                access_token = refresh.access_token
                access_token.set_exp(lifetime=access_lifetime)
                bind_device_to_refresh_token(device, refresh)
                attach_device_to_refresh_token(refresh, device)
                attach_device_claim(access_token, device)

                response = Response(
                    {'access': str(access_token)},
                    status=status.HTTP_200_OK,
                )
                set_refresh_cookie(
                    response,
                    str(refresh),
                    refresh_cookie_max_age(refresh_lifetime),
                )
                clear_prev_user_cookie(response)
                audit_log(
                    'auth.login',
                    request=request,
                    actor=user,
                    severity='security',
                )
                return response

            inactive_user = (
                User.objects
                .filter(username=username, is_active=False)
                .first()
            )
            if inactive_user is not None and inactive_user.check_password(password):
                audit_log(
                    'auth.login_failed',
                    request=request,
                    severity='security',
                    meta={'username': username, 'reason': 'account_suspended'},
                )
                return Response(
                    {
                        'message': _('Аккаунт приостановлен. Обратитесь к администратору.'),
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

            audit_log(
                'auth.login_failed',
                request=request,
                severity='security',
                meta={'username': username},
            )
            return Response(
                {
                    "message": _("Неверные учетные данные.")
                },
                status=status.HTTP_400_BAD_REQUEST
            )
        
        errors = parse_errors_to_dict(serializer.errors)
        return Response(
            errors,
            status=status.HTTP_400_BAD_REQUEST
        )
    
    def _create_or_update_device(self, request, user):
        """Создаёт или обновляет сессию устройства (отдельно для каждого браузера)."""
        ip_address = get_client_ip(request)
        user_agent = request.META.get('HTTP_USER_AGENT', '')
        device_type = detect_device_type(user_agent)
        device_name = build_device_display_name(user_agent, device_type)

        from src.core.utils.geoip import resolve_ip_location

        city, country = resolve_ip_location(ip_address)

        device, _created = UserDevice.objects.update_or_create(
            user=user,
            device_name=device_name,
            ip_address=ip_address,
            defaults={
                'device_type': device_type,
                'user_agent': user_agent,
                'city': city,
                'country': country,
                'is_active': True,
            },
        )
        return device


