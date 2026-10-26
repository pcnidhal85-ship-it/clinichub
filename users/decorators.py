from functools import wraps
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.contrib import messages


def role_required(allowed_roles):
    """
    Decorator to ensure user is logged in and has one of the allowed user_types.
    Superusers and ADMIN user_type are automatically authorized.
    """
    if isinstance(allowed_roles, str):
        allowed_roles = [allowed_roles]

    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def wrapper(request, *args, **kwargs):
            user = request.user

            if user.is_superuser or user.user_type == "ADMIN":
                return view_func(request, *args, **kwargs)

            if user.user_type in allowed_roles:
                return view_func(request, *args, **kwargs)

            return HttpResponseForbidden(
                "You do not have permission to access this page."
            )

        return wrapper

    return decorator


def patient_required(view_func):
    """Restricts access to users with user_type='PATIENT' (and admins who have a patient profile)."""
    @wraps(view_func)
    @login_required
    def wrapper(request, *args, **kwargs):
        user = request.user
        if user.user_type == "PATIENT":
            return view_func(request, *args, **kwargs)

        if user.is_superuser or user.user_type == "ADMIN":
            from clinic.models import Patient
            if Patient.objects.filter(user=user, is_deleted=False).exists():
                return view_func(request, *args, **kwargs)
            messages.info(request, "Redirected to the Admin Command Center.")
            return redirect("admin-dashboard")

        if user.user_type == "DOCTOR":
            messages.info(request, "Redirected to Doctor Workspace.")
            return redirect("doctor-dashboard")

        if user.user_type == "STAFF":
            messages.info(request, "Redirected to Staff Reception Desk.")
            return redirect("staff-dashboard")

        messages.warning(request, "This page is reserved for patients.")
        return redirect("home")

    return wrapper


def doctor_required(view_func):
    """Restricts access to users with user_type='DOCTOR'."""
    @wraps(view_func)
    @login_required
    def wrapper(request, *args, **kwargs):
        user = request.user
        if user.is_superuser or user.user_type == "ADMIN" or user.user_type == "DOCTOR":
            return view_func(request, *args, **kwargs)

        return HttpResponseForbidden(
            "Access restricted to medical doctors."
        )

    return wrapper


def staff_required(view_func=None, allowed_staff_roles=None):
    """
    Restricts access to users with user_type='STAFF' (or ADMIN/Superuser).
    Optionally filters by specific staff_role (e.g. ['RECEPTIONIST']).
    Can be used as @staff_required or @staff_required(allowed_staff_roles=['RECEPTIONIST']).
    """
    def decorator(func):
        @wraps(func)
        @login_required
        def wrapper(request, *args, **kwargs):
            user = request.user

            if user.is_superuser or user.user_type == "ADMIN":
                return func(request, *args, **kwargs)

            if user.user_type == "STAFF":
                if allowed_staff_roles is None or user.staff_role in allowed_staff_roles:
                    return func(request, *args, **kwargs)

            return HttpResponseForbidden(
                "You do not have permission to access this page."
            )

        return wrapper

    if view_func is not None:
        return decorator(view_func)
    return decorator


def admin_required(view_func):
    """Restricts access to admins or superusers."""
    @wraps(view_func)
    @login_required
    def wrapper(request, *args, **kwargs):
        user = request.user
        if user.is_superuser or user.user_type == "ADMIN" or user.is_staff:
            return view_func(request, *args, **kwargs)

        return HttpResponseForbidden(
            "Administrative privileges required."
        )

    return wrapper
