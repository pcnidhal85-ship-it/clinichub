"""
ClinicHub Audit Logging Utility
Provides high-level helpers to record structured audit events
from HTTP requests, management commands, or business workflows.
"""

from ClinicHub.ratelimit import get_client_ip


def log_audit_event(
    request=None,
    actor=None,
    action="SYSTEM",
    description="",
    target_object=None,
    target_model=None,
    target_id=None,
    extra_data=None,
):
    """
    Safely creates an immutable AuditLog entry.
    Failsafe: Never raises an uncaught exception to prevent interrupting clinical operations.
    """
    try:
        from clinic.models import AuditLog

        # Resolve actor
        if actor is None and request is not None:
            if hasattr(request, "user") and request.user.is_authenticated:
                actor = request.user

        actor_email = None
        actor_role = None
        if actor:
            actor_email = getattr(actor, "email", str(actor))
            actor_role = getattr(actor, "user_type", None) or (
                "SUPERUSER" if getattr(actor, "is_superuser", False) else "USER"
            )

        # Resolve target model & ID
        if target_object is not None:
            target_model = target_object.__class__.__name__
            target_id = str(getattr(target_object, "pk", getattr(target_object, "id", "")))
        else:
            target_model = target_model or ""
            target_id = str(target_id) if target_id is not None else ""

        # Resolve IP address and User-Agent from request
        ip_address = None
        user_agent = None
        if request is not None:
            ip_address = get_client_ip(request)
            user_agent = request.META.get("HTTP_USER_AGENT", "")[:490]

        log_entry = AuditLog.objects.create(
            actor=actor if actor and getattr(actor, "pk", None) else None,
            actor_email=actor_email,
            actor_role=actor_role,
            action=action,
            description=description[:490],
            target_model=target_model[:100] if target_model else None,
            target_id=target_id[:100] if target_id else None,
            ip_address=ip_address,
            user_agent=user_agent,
            extra_data=extra_data or {},
        )
        return log_entry
    except Exception as e:
        # Failsafe: never disrupt critical business logic
        import logging
        logger = logging.getLogger("clinichub.audit")
        logger.error(f"Failed to record audit log: {e}", exc_info=True)
        return None

