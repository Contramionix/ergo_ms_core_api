"""Маршруты OpenAPI (drf-spectacular): JSON/YAML, Swagger UI и ReDoc."""

from django.urls import path

from src.config.settings.swagger import SWAGGER_ENABLED

schema_view = None
urlpatterns = []

if SWAGGER_ENABLED:
    from rest_framework.permissions import AllowAny
    from rest_framework_simplejwt.authentication import JWTAuthentication
    from drf_spectacular.views import (
        SpectacularAPIView,
        SpectacularRedocView,
        SpectacularSwaggerView,
        SpectacularYAMLAPIView,
    )

    class SpectacularJSONView(SpectacularAPIView):
        permission_classes = (AllowAny,)
        authentication_classes = [JWTAuthentication]

    class SpectacularYAMLView(SpectacularYAMLAPIView):
        permission_classes = (AllowAny,)
        authentication_classes = [JWTAuthentication]

    class SpectacularSwaggerUiView(SpectacularSwaggerView):
        permission_classes = (AllowAny,)
        authentication_classes = [JWTAuthentication]

    class SpectacularRedocUiView(SpectacularRedocView):
        permission_classes = (AllowAny,)
        authentication_classes = [JWTAuthentication]

    urlpatterns = [
        path('swagger.json', SpectacularJSONView.as_view(), name='schema-json'),
        path('swagger.yaml', SpectacularYAMLView.as_view(), name='schema-yaml'),
        path(
            'swagger/',
            SpectacularSwaggerUiView.as_view(url_name='schema-json'),
            name='schema-swagger-ui',
        ),
        path(
            'redoc/',
            SpectacularRedocUiView.as_view(url_name='schema-json'),
            name='schema-redoc',
        ),
    ]
