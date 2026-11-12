from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'^ws/queue/live/?$', consumers.LiveQueueConsumer.as_asgi()),
]
