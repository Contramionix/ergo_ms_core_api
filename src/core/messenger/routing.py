from django.urls import path

from .consumers import MessengerConsumer

websocket_urlpatterns = [
    path(
        'ws/messenger/<str:content_type>/<str:object_id>/',
        MessengerConsumer.as_asgi(),
    ),
]
