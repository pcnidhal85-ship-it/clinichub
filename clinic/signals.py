import logging
from django.db.models.signals import post_save
from django.dispatch import receiver

from users.models import User
from .models import Patient, Appointment, LabTestOrder

logger = logging.getLogger(__name__)


@receiver(post_save, sender=User)
def create_patient_profile(sender, instance, created, **kwargs):
    if instance.user_type == 'PATIENT':
        Patient.objects.get_or_create(user=instance)


@receiver(post_save, sender=Appointment)
def auto_generate_appointment_consultation_bill(sender, instance, created, **kwargs):
    """
    Automatically creates a separate pending consultation bill whenever an appointment
    is booked, checked-in, or completed.
    """
    if instance.is_deleted or instance.status == 'CANCELLED':
        return
    try:
        from .services import generate_or_get_consultation_invoice
        generate_or_get_consultation_invoice(instance)
    except Exception as exc:
        logger.exception("Failed to auto-generate consultation invoice for appointment %s: %s", instance.id, exc)


@receiver(post_save, sender=LabTestOrder)
def auto_generate_lab_order_bill(sender, instance, created, **kwargs):
    """
    Automatically creates a separate distinct pending bill for diagnostic laboratory investigations.
    """
    if instance.is_deleted:
        return
    try:
        from .services import generate_or_get_lab_order_invoice
        generate_or_get_lab_order_invoice(instance)
    except Exception as exc:
        logger.exception("Failed to auto-generate lab invoice for order %s: %s", instance.id, exc)