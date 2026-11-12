"""
Context processors for ClinicHub notification system.
Provides global notification count and recent notification list across all templates.
"""

def notification_context(request):
    """
    Supplies real-time unread notification count and recent notifications
    to all templates across ClinicHub for authenticated users.
    """
    if not getattr(request, 'user', None) or not request.user.is_authenticated:
        return {
            'unread_notifications_count': 0,
            'recent_notifications': [],
        }

    try:
        from .models import Notification
        user_notifs = Notification.objects.filter(
            recipient=request.user,
            is_deleted=False
        )
        unread_count = user_notifs.filter(is_read=False).count()
        recent = list(user_notifs.order_by('-created_at')[:8])
        return {
            'unread_notifications_count': unread_count,
            'recent_notifications': recent,
        }
    except Exception:
        return {
            'unread_notifications_count': 0,
            'recent_notifications': [],
        }
