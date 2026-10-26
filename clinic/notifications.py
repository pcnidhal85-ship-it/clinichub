"""
ClinicHub Notification and Email Delivery Services.
Handles transactional emails, appointment confirmations, reminders, and notifications.
"""

import logging
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)


def _get_appointment_context(appointment, token=None, custom_reason=None):
    """
    Builds a consistent context dictionary for appointment email templates.
    """
    patient = getattr(appointment, 'patient', None)
    user = getattr(patient, 'user', None) if patient else None
    patient_name = (user.get_full_name() or user.username) if user else "Patient"

    if not token and appointment:
        token = getattr(appointment, 'token', None)
        if not token:
            from .models import Token
            token = Token.objects.filter(appointment=appointment, is_deleted=False).first()

    doctor = getattr(appointment, 'doctor', None)
    if doctor and getattr(doctor, 'user', None):
        doc_raw = doctor.user.get_full_name() or doctor.user.username
        if not (doc_raw.lower().startswith("dr.") or doc_raw.lower().startswith("dr ")):
            doctor_display = f"Dr. {doc_raw}"
        else:
            doctor_display = doc_raw
    else:
        doctor_display = "Doctor"

    department_name = (
        doctor.department.name
        if doctor and getattr(doctor, 'department', None)
        else "General Practice"
    )

    if appointment.appointment_date:
        formatted_date = appointment.appointment_date.strftime("%A, %B %d, %Y")
    else:
        formatted_date = "Scheduled Date"

    if appointment.time_slot:
        time_display = appointment.time_slot
    elif appointment.appointment_time:
        time_display = appointment.appointment_time.strftime("%I:%M %p")
    else:
        time_display = "To be advised"

    token_number_str = token.formatted_token_number if token else "Assigned at Clinic"
    support_email = getattr(settings, "EMAIL_HOST_USER", "cliniichub@gmail.com")

    return {
        "appointment": appointment,
        "token": token,
        "patient_name": patient_name,
        "doctor_display": doctor_display,
        "department_name": department_name,
        "formatted_date": formatted_date,
        "time_display": time_display,
        "token_number": token_number_str,
        "reason": custom_reason or appointment.reason or "",
        "support_email": support_email,
        "recipient_email": user.email if user else None,
    }


def send_appointment_confirmation_email(appointment, token=None, fail_silently=True):
    """
    Dispatches a branded HTML and plain-text confirmation email to the patient
    with appointment details, queue token number, doctor info, and instructions.
    """
    try:
        ctx = _get_appointment_context(appointment, token=token)
        recipient_email = ctx.get("recipient_email")
        if not recipient_email:
            logger.warning(
                "Skipping appointment confirmation email: No recipient email for appointment %s.",
                appointment.id
            )
            return False

        subject = (
            f"Appointment Confirmed: {ctx['formatted_date']} with "
            f"{ctx['doctor_display']} (Token #{ctx['token_number']})"
        )

        html_content = render_to_string("clinic/emails/appointment_confirmation.html", ctx)
        try:
            text_content = render_to_string("clinic/emails/appointment_confirmation.txt", ctx)
        except Exception:
            text_content = strip_tags(html_content)

        from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "ClinicHub <noreply@clinichub.local>")
        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_content,
            from_email=from_email,
            to=[recipient_email],
        )
        msg.attach_alternative(html_content, "text/html")
        msg.send(fail_silently=fail_silently)

        logger.info(
            "Appointment confirmation email dispatched to %s for appointment %s",
            recipient_email, appointment.id
        )
        return True

    except Exception as exc:
        logger.exception(
            "Failed to dispatch appointment confirmation email for appointment %s: %s",
            getattr(appointment, 'id', 'unknown'), exc
        )
        if not fail_silently:
            raise
        return False


def send_appointment_rescheduled_email(appointment, token=None, fail_silently=True):
    """
    Dispatches a notification email when an appointment is rescheduled.
    """
    try:
        ctx = _get_appointment_context(appointment, token=token)
        recipient_email = ctx.get("recipient_email")
        if not recipient_email:
            return False

        subject = (
            f"Appointment Rescheduled: {ctx['formatted_date']} with "
            f"{ctx['doctor_display']} (Token #{ctx['token_number']})"
        )

        html_content = render_to_string("clinic/emails/appointment_rescheduled.html", ctx)
        try:
            text_content = render_to_string("clinic/emails/appointment_rescheduled.txt", ctx)
        except Exception:
            text_content = strip_tags(html_content)

        from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "ClinicHub <noreply@clinichub.local>")
        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_content,
            from_email=from_email,
            to=[recipient_email],
        )
        msg.attach_alternative(html_content, "text/html")
        msg.send(fail_silently=fail_silently)

        logger.info(
            "Appointment rescheduled email dispatched to %s for appointment %s",
            recipient_email, appointment.id
        )
        return True

    except Exception as exc:
        logger.exception(
            "Failed to dispatch rescheduled email for appointment %s: %s",
            getattr(appointment, 'id', 'unknown'), exc
        )
        if not fail_silently:
            raise
        return False


def send_appointment_cancellation_email(appointment, reason=None, fail_silently=True):
    """
    Dispatches a notification email when an appointment is cancelled.
    """
    try:
        ctx = _get_appointment_context(appointment, custom_reason=reason)
        recipient_email = ctx.get("recipient_email")
        if not recipient_email:
            return False

        subject = f"Appointment Cancelled: {ctx['formatted_date']} with {ctx['doctor_display']}"

        html_content = render_to_string("clinic/emails/appointment_cancelled.html", ctx)
        try:
            text_content = render_to_string("clinic/emails/appointment_cancelled.txt", ctx)
        except Exception:
            text_content = strip_tags(html_content)

        from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "ClinicHub <noreply@clinichub.local>")
        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_content,
            from_email=from_email,
            to=[recipient_email],
        )
        msg.attach_alternative(html_content, "text/html")
        msg.send(fail_silently=fail_silently)

        logger.info(
            "Appointment cancellation email dispatched to %s for appointment %s",
            recipient_email, appointment.id
        )
        return True

    except Exception as exc:
        logger.exception(
            "Failed to dispatch cancellation email for appointment %s: %s",
            getattr(appointment, 'id', 'unknown'), exc
        )
        if not fail_silently:
            raise
        return False
