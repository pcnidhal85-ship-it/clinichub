"""
ClinicHub Notification and Email Delivery Services.
Handles transactional emails, appointment confirmations, reminders, and notifications.
"""

import logging
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.utils import timezone

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


def send_appointment_reminder_email(appointment, token=None, fail_silently=True, update_record=True):
    """
    Dispatches a 24-hour appointment reminder email to the patient with clinic instructions,
    queue token number, scheduled time window, and preparation checklist.
    On successful dispatch, updates appointment.reminder_sent and appointment.reminder_sent_at.
    """
    try:
        ctx = _get_appointment_context(appointment, token=token)
        recipient_email = ctx.get("recipient_email")
        if not recipient_email:
            logger.warning(
                "Skipping appointment reminder email: No recipient email for appointment %s.",
                appointment.id
            )
            return False

        subject = (
            f"Appointment Reminder: Tomorrow ({ctx['formatted_date']}) with "
            f"{ctx['doctor_display']} (Token #{ctx['token_number']})"
        )

        html_content = render_to_string("clinic/emails/appointment_reminder.html", ctx)
        try:
            text_content = render_to_string("clinic/emails/appointment_reminder.txt", ctx)
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

        if update_record:
            appointment.reminder_sent = True
            appointment.reminder_sent_at = timezone.now()
            appointment.save(update_fields=["reminder_sent", "reminder_sent_at"])

        logger.info(
            "Appointment reminder email dispatched to %s for appointment %s",
            recipient_email, appointment.id
        )
        return True

    except Exception as exc:
        logger.exception(
            "Failed to dispatch reminder email for appointment %s: %s",
            getattr(appointment, 'id', 'unknown'), exc
        )
        if not fail_silently:
            raise
        return False


def notify_user(recipient, title, message, notification_type='SYSTEM', level='INFO', link='', sender=None):
    """
    Creates an in-app persistent notification for a given User.
    Recipient can be a User instance or an object with a .user attribute (Patient, Doctor, Staff).
    """
    try:
        user = getattr(recipient, 'user', recipient)
        if not user or not hasattr(user, 'id'):
            logger.warning("Cannot deliver notification: Invalid recipient %s", recipient)
            return None

        from .models import Notification
        notification = Notification.objects.create(
            recipient=user,
            sender=getattr(sender, 'user', sender) if sender else None,
            title=title,
            message=message,
            notification_type=notification_type,
            level=level,
            link=link or '',
        )
        logger.info(
            "Notification #%s delivered to user %s [%s]",
            notification.id, user.username, notification_type
        )
        return notification
    except Exception as exc:
        logger.exception("Failed to deliver notification to %s: %s", recipient, exc)
        return None


def notify_appointment_booked(appointment, token=None):
    """Notify patient and doctor when an appointment is booked."""
    patient_user = getattr(getattr(appointment, 'patient', None), 'user', None)
    doctor = getattr(appointment, 'doctor', None)
    doctor_user = getattr(doctor, 'user', None) if doctor else None
    token_str = f" Token #{token.formatted_token_number}" if token else ""
    date_str = appointment.appointment_date.strftime("%b %d, %Y") if appointment.appointment_date else ""

    if patient_user:
        notify_user(
            recipient=patient_user,
            title="Appointment Confirmed",
            message=f"Your visit with Dr. {doctor_user.last_name if doctor_user else 'Doctor'} on {date_str} is confirmed.{token_str}",
            notification_type='APPOINTMENT',
            level='SUCCESS',
            link='/clinic/my-appointments/',
        )

    if doctor_user:
        p_name = patient_user.get_full_name() if patient_user else "Patient"
        notify_user(
            recipient=doctor_user,
            title="New Appointment Scheduled",
            message=f"{p_name} booked an appointment for {date_str}.{token_str}",
            notification_type='APPOINTMENT',
            level='INFO',
            link='/clinic/doctor/appointments/',
        )


def notify_patient_checked_in(appointment, token=None):
    """Notify consulting doctor when a patient checks in at reception."""
    doctor_user = getattr(getattr(appointment, 'doctor', None), 'user', None)
    patient_user = getattr(getattr(appointment, 'patient', None), 'user', None)
    name_str = patient_user.get_full_name() if patient_user else "Patient"
    token_str = f"Token #{token.formatted_token_number}" if token else "Patient"

    if doctor_user:
        notify_user(
            recipient=doctor_user,
            title="Patient Checked In & Waiting",
            message=f"{token_str} ({name_str}) has arrived and is waiting in the queue.",
            notification_type='QUEUE',
            level='INFO',
            link='/clinic/doctor/queue/',
        )


def notify_patient_called(appointment, token=None):
    """Notify patient when their token is called to the consultation room."""
    patient_user = getattr(getattr(appointment, 'patient', None), 'user', None)
    doctor = getattr(appointment, 'doctor', None)
    doctor_name = f"Dr. {doctor.user.last_name}" if doctor and getattr(doctor, 'user', None) else "Doctor"
    token_str = f"Token #{token.formatted_token_number}" if token else "Your token"

    if patient_user:
        notify_user(
            recipient=patient_user,
            title="You're Being Called!",
            message=f"{token_str} is now being called by {doctor_name}. Please proceed to the consultation room.",
            notification_type='QUEUE',
            level='WARNING',
            link='/clinic/patient-dashboard/',
        )


def notify_prescription_issued(prescription):
    """Notify patient when a new prescription is issued."""
    patient_user = getattr(getattr(prescription, 'patient', None), 'user', None)
    doctor = getattr(prescription, 'doctor', None)
    doctor_name = f"Dr. {doctor.user.last_name}" if doctor and getattr(doctor, 'user', None) else "Your doctor"

    if patient_user:
        notify_user(
            recipient=patient_user,
            title="New Prescription Issued",
            message=f"{doctor_name} has generated a new digital prescription for your consultation.",
            notification_type='PRESCRIPTION',
            level='SUCCESS',
            link=f'/clinic/patient/prescriptions/{prescription.id}/',
        )


def notify_invoice_generated(invoice):
    """Notify patient when a new invoice is generated."""
    patient_user = getattr(getattr(invoice, 'patient', None), 'user', None)
    if patient_user:
        notify_user(
            recipient=patient_user,
            title="New Invoice Available",
            message=f"Invoice #{invoice.invoice_number} for ${invoice.total_amount:.2f} has been generated.",
            notification_type='BILLING',
            level='INFO',
            link=f'/clinic/patient/billing/{invoice.id}/',
        )


def notify_payment_received(payment):
    """Notify patient when a payment is processed."""
    invoice = getattr(payment, 'invoice', None)
    patient_user = getattr(getattr(invoice, 'patient', None), 'user', None) if invoice else None
    if patient_user:
        notify_user(
            recipient=patient_user,
            title="Payment Received",
            message=f"Payment #{payment.payment_number} of ${payment.amount:.2f} recorded for Invoice #{invoice.invoice_number}.",
            notification_type='BILLING',
            level='SUCCESS',
            link=f'/clinic/patient/billing/{invoice.id}/',
        )


