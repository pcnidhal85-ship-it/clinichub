import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.db.models import Max, Q, Value, Sum, Count
from django.db.models.functions import Concat
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from users.models import User
from users.decorators import patient_required, staff_required, doctor_required, admin_required
from django.core.exceptions import ValidationError
from .forms import (
    AppointmentForm,
    DoctorAvailabilityForm,
    ConsultationNotesForm,
    CancelAppointmentForm,
    WalkInAppointmentForm,
    StaffPatientRegistrationForm,
    DepartmentForm,
    AdminDoctorForm,
    AdminStaffForm,
    PatientVitalForm,
    PatientMedicalHistoryForm,
    MedicalRecordForm,
    PrescriptionForm,
    PrescriptionItemForm,
    LabTestOrderForm,
    BillableServiceForm,
    InvoiceCreateForm,
    InvoiceLineItemForm,
    PaymentForm,
)
from .models import (
    Doctor,
    Department,
    Patient,
    Appointment,
    Token,
    DoctorAvailability,
    Staff,
    PatientMedicalHistory,
    PatientVital,
    MedicalRecord,
    Prescription,
    PrescriptionItem,
    LabTestOrder,
    BillableService,
    Invoice,
    InvoiceLineItem,
    Payment,
)
from .services import (
    generate_appointment_token,
    call_next_patient_for_doctor,
    generate_doctor_time_slots,
    get_slot_tokens_availability,
    book_slot_appointment,
    generate_invoice_number,
    generate_payment_number,
    generate_or_get_appointment_invoice,
    generate_or_get_consultation_invoice,
    generate_or_get_lab_order_invoice,
    backfill_all_pending_invoices,
    seed_default_billable_services,
)


# --------------------------------
# --------------------------------
# HELPER: SAFE PATIENT PROFILE RESOLUTION
# --------------------------------

def get_or_create_patient(user):
    """
    Safely retrieves or auto-creates an active Patient profile for a user.
    Ensures that users with user_type='PATIENT' (or testing admins)
    always have a valid, active Patient profile and never encounter a 404.
    """
    patient, created = Patient.objects.get_or_create(
        user=user,
        defaults={"is_active": True, "is_deleted": False}
    )
    if not patient.is_active or patient.is_deleted:
        patient.is_active = True
        patient.is_deleted = False
        patient.save(update_fields=["is_active", "is_deleted"])
    return patient


# --------------------------------
# CREATE APPOINTMENT + TOKEN (TIME-SLOT SYSTEM)
# --------------------------------

@patient_required
def create_appointment(request):
    patient = get_or_create_patient(request.user)

    departments = Department.objects.filter(
        is_active=True,
        is_deleted=False,
    ).order_by("name")

    if request.method == "POST":
        form = AppointmentForm(request.POST)

        if form.is_valid():
            doctor = form.cleaned_data["doctor"]
            appointment_date = form.cleaned_data["appointment_date"]
            slot_start_time = form.cleaned_data.get("slot_start_time") or form.cleaned_data.get("appointment_time")
            slot_end_time = form.cleaned_data.get("slot_end_time")
            time_slot = form.cleaned_data.get("time_slot") or ""
            token_number = form.cleaned_data.get("token_number")
            reason = form.cleaned_data.get("reason", "").strip()

            try:
                appointment, token = book_slot_appointment(
                    patient=patient,
                    doctor=doctor,
                    appointment_date=appointment_date,
                    slot_start_time=slot_start_time,
                    slot_end_time=slot_end_time,
                    time_slot=time_slot,
                    token_number=token_number,
                    reason=reason,
                )
                return redirect("appointment-success", appointment_id=appointment.id)

            except ValidationError as e:
                err_msg = e.message if hasattr(e, "message") else str(e)
                form.add_error(None, err_msg)
            except IntegrityError:
                form.add_error(
                    None,
                    "The selected token number in this time slot was just booked by another patient. Please choose an available token.",
                )
            except Exception as e:
                form.add_error(
                    None,
                    f"An unexpected error occurred while booking: {e}. Please try again.",
                )
    else:
        initial = {}
        doctor_id = request.GET.get("doctor_id")
        department_id = request.GET.get("department_id")
        if doctor_id:
            doc = Doctor.objects.filter(
                id=doctor_id,
                is_active=True,
                is_deleted=False,
                department__is_active=True,
                department__is_deleted=False,
            ).first()
            if doc:
                initial["doctor"] = doc.id
                initial["department"] = doc.department_id
        elif department_id:
            dept = Department.objects.filter(id=department_id, is_active=True, is_deleted=False).first()
            if dept:
                initial["department"] = dept.id

        form = AppointmentForm(initial=initial)

    context = {
        "form": form,
        "departments": departments,
        "today": timezone.localdate(),
        "patient": patient,
    }
    return render(
        request,
        "clinic/create_appointment.html",
        context,
    )


# --------------------------------
# GET DOCTORS BY DEPARTMENT
# --------------------------------

def get_doctors(request):
    department_id = request.GET.get("department_id")
    if not department_id:
        return JsonResponse({"doctors": []})

    doctors = Doctor.objects.filter(
        department_id=department_id,
        is_active=True,
        is_deleted=False,
        department__is_active=True,
        department__is_deleted=False,
        user__is_active=True,
        user__is_delete=False,
    ).select_related("user", "department").order_by("user__first_name")

    doctor_list = []
    for doctor in doctors:
        avails = doctor.availabilities.filter(is_active=True, is_deleted=False).order_by("start_time")
        if avails.exists():
            days = list(avails.values_list("day", flat=True).distinct())
            sched_texts = [f"{a.get_day_display()} ({a.start_time.strftime('%I:%M %p')}–{a.end_time.strftime('%I:%M %p')})" for a in avails]
            sched_str = ", ".join(sched_texts)
        else:
            days = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT']
            sched_str = "Mon–Sat 09:00 AM – 01:00 PM (Regular OPD)"

        doctor_list.append({
            "id": doctor.id,
            "name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
            "specialization": doctor.specialization or "Specialist Physician",
            "license_number": doctor.license_number,
            "available_days": days,
            "schedule_summary": sched_str,
        })

    return JsonResponse({"doctors": doctor_list})


# --------------------------------
# API: GET AVAILABLE SLOTS FOR DOCTOR & DATE
# --------------------------------

def get_available_slots(request):
    doctor_id = request.GET.get("doctor_id")
    date_str = request.GET.get("date")

    if not doctor_id or not date_str:
        return JsonResponse({"error": "Doctor and appointment date are required."}, status=400)

    try:
        doctor = Doctor.objects.select_related("user", "department").get(
            id=doctor_id,
            is_active=True,
            is_deleted=False,
            user__is_active=True,
            user__is_delete=False,
            department__is_active=True,
            department__is_deleted=False,
        )
    except Doctor.DoesNotExist:
        return JsonResponse({"error": "Selected doctor is not available."}, status=404)

    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return JsonResponse({"error": "Invalid date format. Expected YYYY-MM-DD."}, status=400)

    if target_date < timezone.localdate():
        return JsonResponse({"error": "Consultation slots cannot be viewed or booked for past dates."}, status=400)

    slots = generate_doctor_time_slots(doctor, target_date)

    avails = doctor.availabilities.filter(is_active=True, is_deleted=False).order_by("start_time")
    if avails.exists():
        days = list(avails.values_list("day", flat=True).distinct())
        sched_summary = ", ".join([f"{a.get_day_display()} ({a.start_time.strftime('%I:%M %p')}–{a.end_time.strftime('%I:%M %p')})" for a in avails])
    else:
        days = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT']
        sched_summary = "Monday – Saturday (09:00 AM – 01:00 PM)"

    return JsonResponse({
        "success": True,
        "doctor": {
            "id": doctor.id,
            "name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
            "department": doctor.department.name,
            "specialization": doctor.specialization,
            "available_days": days,
            "schedule_summary": sched_summary,
        },
        "date": target_date.isoformat(),
        "day_name": target_date.strftime("%A, %B %d, %Y"),
        "slots": slots,
        "total_slots": len(slots),
    })


# --------------------------------
# API: GET TOKENS IN A SPECIFIC SLOT
# --------------------------------

def get_slot_tokens(request):
    doctor_id = request.GET.get("doctor_id")
    date_str = request.GET.get("date")
    slot_start_time = request.GET.get("slot_start_time")

    if not doctor_id or not date_str or not slot_start_time:
        return JsonResponse({"error": "Doctor, date, and slot_start_time are required."}, status=400)

    try:
        doctor = Doctor.objects.get(
            id=doctor_id,
            is_active=True,
            is_deleted=False,
            user__is_active=True,
            user__is_delete=False,
        )
    except Doctor.DoesNotExist:
        return JsonResponse({"error": "Doctor not found or inactive."}, status=404)

    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return JsonResponse({"error": "Invalid date format."}, status=400)

    if target_date < timezone.localdate():
        return JsonResponse({"error": "Date cannot be in the past."}, status=400)

    tokens_data = get_slot_tokens_availability(doctor, target_date, slot_start_time)

    return JsonResponse({
        "success": True,
        "slot_start_time": slot_start_time,
        **tokens_data,
    })


# --------------------------------
# APPOINTMENT SUCCESS
# --------------------------------

@patient_required
def appointment_success(request, appointment_id):
    patient = get_or_create_patient(request.user)

    appointment = get_object_or_404(
        Appointment,
        id=appointment_id,
        patient=patient,
        is_deleted=False,
    )

    token = Token.objects.filter(
        appointment=appointment,
        is_deleted=False,
    ).first()

    return render(
        request,
        "clinic/appointment_success.html",
        {
            "appointment": appointment,
            "token": token,
        },
    )


# --------------------------------
# PATIENT DASHBOARD
# --------------------------------

@patient_required
def patient_dashboard(request):
    patient = get_or_create_patient(request.user)

    appointments = Appointment.objects.filter(
        patient=patient,
        is_deleted=False,
        doctor__is_deleted=False,
        doctor__department__is_deleted=False,
    ).exclude(
        doctor__user__is_delete=True
    )

    upcoming_appointment = appointments.filter(
        status="BOOKED",
        appointment_date__gte=timezone.localdate(),
    ).order_by(
        "appointment_date",
        "appointment_time",
    ).first()

    context = {
        "patient": patient,
        "upcoming_appointment": upcoming_appointment,
        "appointment_count": appointments.count(),
        "completed_count": appointments.filter(
            status="COMPLETED"
        ).count(),
        "prescription_count": patient.prescriptions.filter(is_deleted=False).count(),
    }

    return render(
        request,
        "clinic/patient_dashboard.html",
        context,
    )


# --------------------------------
# MY APPOINTMENTS
# --------------------------------

@patient_required
def my_appointments(request):
    patient = get_or_create_patient(request.user)

    appointments = Appointment.objects.filter(
        patient=patient,
        is_deleted=False,
        doctor__is_deleted=False,
        doctor__department__is_deleted=False,
    ).exclude(
        doctor__user__is_delete=True
    ).select_related(
        "doctor__user",
        "doctor__department",
    ).prefetch_related(
        "token",
    ).order_by(
        "-appointment_date",
        "-appointment_time",
    )

    return render(
        request,
        "clinic/my_appointments.html",
        {"appointments": appointments},
    )


# --------------------------------
# RESCHEDULE APPOINTMENT
# --------------------------------

@patient_required
def reschedule_appointment(request, appointment_id):
    patient = get_or_create_patient(request.user)

    appointment = get_object_or_404(
        Appointment.objects.select_related(
            "doctor",
            "doctor__user",
            "doctor__department",
            "patient",
        ),
        id=appointment_id,
        patient=patient,
        is_deleted=False,
    )

    # Only booked appointments can be rescheduled
    if appointment.status != "BOOKED":
        messages.error(
            request,
            f"This appointment cannot be rescheduled because its current status is '{appointment.get_status_display()}'.",
        )
        return redirect("my-appointments")

    token = Token.objects.filter(
        appointment=appointment,
        is_deleted=False,
    ).first()

    old_date = appointment.appointment_date
    old_time = appointment.appointment_time

    if request.method == "POST":
        form = AppointmentForm(request.POST, instance=appointment)

        if form.is_valid():
            new_date = form.cleaned_data["appointment_date"]
            new_time = form.cleaned_data["appointment_time"]
            new_doctor = form.cleaned_data["doctor"]
            new_reason = form.cleaned_data.get("reason", "")
            new_slot_start = form.cleaned_data.get("slot_start_time") or new_time
            new_slot_end = form.cleaned_data.get("slot_end_time")
            new_time_slot = form.cleaned_data.get("time_slot") or ""
            new_token_num = form.cleaned_data.get("token_number")

            try:
                with transaction.atomic():
                    # Validate doctor availability
                    if new_doctor.is_deleted or not new_doctor.is_active or new_doctor.user.is_delete or not new_doctor.user.is_active:
                        form.add_error("doctor", "The selected doctor is currently unavailable.")
                        return render(
                            request,
                            "clinic/reschedule_appointment.html",
                            {
                                "form": form,
                                "appointment": appointment,
                                "token": token,
                            },
                        )

                    appointment.doctor = new_doctor
                    appointment.appointment_date = new_date
                    appointment.appointment_time = new_time
                    appointment.slot_start_time = new_slot_start
                    appointment.slot_end_time = new_slot_end
                    appointment.time_slot = new_time_slot
                    appointment.reason = new_reason
                    appointment.status = "BOOKED"

                    reschedule_log = f"[Rescheduled on {timezone.now().strftime('%Y-%m-%d %H:%M')}: moved from {old_date} {old_time.strftime('%H:%M')} to {new_date} {new_time.strftime('%H:%M')}]"
                    if appointment.notes:
                        appointment.notes += f"\n{reschedule_log}"
                    else:
                        appointment.notes = reschedule_log

                    appointment.save()

                    if new_token_num:
                        # If a specific token in the slot was chosen
                        if token:
                            token.doctor = new_doctor
                            token.token_date = new_date
                            token.slot_start_time = new_slot_start
                            token.slot_end_time = new_slot_end
                            token.time_slot = new_time_slot
                            token.token_number = new_token_num
                            token.status = "BOOKED"
                            token.save()
                        else:
                            token = Token.objects.create(
                                appointment=appointment,
                                doctor=new_doctor,
                                token_date=new_date,
                                token_number=new_token_num,
                                slot_start_time=new_slot_start,
                                slot_end_time=new_slot_end,
                                time_slot=new_time_slot,
                                status="BOOKED",
                            )
                    else:
                        token = generate_appointment_token(
                            appointment=appointment,
                            status="BOOKED",
                        )

                try:
                    from .notifications import send_appointment_rescheduled_email
                    send_appointment_rescheduled_email(appointment, token=token, fail_silently=True)
                except Exception as exc:
                    logger.warning("Could not dispatch reschedule email: %s", exc)

                messages.success(
                    request,
                    f"Your consultation with Dr. {appointment.doctor.user.first_name} {appointment.doctor.user.last_name} has been rescheduled to {new_date.strftime('%B %d, %Y')} at {new_time.strftime('%I:%M %p')}. Queue Token #{token.formatted_token_number}.",
                )
                return redirect("appointment-success", appointment_id=appointment.id)

            except Exception:
                form.add_error(None, "Could not update queue token for this date. Please try again.")

    else:
        form = AppointmentForm(instance=appointment)

    context = {
        "form": form,
        "appointment": appointment,
        "token": token,
    }
    return render(request, "clinic/reschedule_appointment.html", context)


# --------------------------------
# CANCEL APPOINTMENT
# --------------------------------

@patient_required
def cancel_appointment(request, appointment_id):
    patient = get_or_create_patient(request.user)

    appointment = get_object_or_404(
        Appointment.objects.select_related(
            "doctor",
            "doctor__user",
            "doctor__department",
            "patient",
        ),
        id=appointment_id,
        patient=patient,
        is_deleted=False,
    )

    # Only booked or waiting appointments can be cancelled by patient
    if appointment.status not in ["BOOKED", "WAITING"]:
        messages.error(
            request,
            f"This appointment cannot be cancelled because its current status is '{appointment.get_status_display()}'.",
        )
        return redirect("my-appointments")

    token = Token.objects.filter(
        appointment=appointment,
        is_deleted=False,
    ).first()

    if request.method == "POST":
        form = CancelAppointmentForm(request.POST)
        if form.is_valid():
            reason = form.cleaned_data["cancellation_reason"]
            additional_notes = form.cleaned_data.get("notes", "").strip()

            cancellation_log = f"[Cancelled on {timezone.now().strftime('%Y-%m-%d %H:%M')}: {reason}"
            if additional_notes:
                cancellation_log += f" - {additional_notes}"
            cancellation_log += "]"

            with transaction.atomic():
                appointment.status = "CANCELLED"
                if appointment.notes:
                    appointment.notes += f"\n{cancellation_log}"
                else:
                    appointment.notes = cancellation_log
                appointment.save(update_fields=["status", "notes", "updated_at"])

                if token:
                    token.status = "CANCELLED"
                    token.save(update_fields=["status"])

            try:
                from .notifications import send_appointment_cancellation_email
                send_appointment_cancellation_email(appointment, reason=reason, fail_silently=True)
            except Exception as exc:
                logger.warning("Could not dispatch cancellation email: %s", exc)

            messages.warning(
                request,
                f"Your appointment with Dr. {appointment.doctor.user.first_name} {appointment.doctor.user.last_name} on {appointment.appointment_date} has been cancelled.",
            )
            return redirect("my-appointments")
    else:
        form = CancelAppointmentForm()

    context = {
        "form": form,
        "appointment": appointment,
        "token": token,
    }
    return render(request, "clinic/cancel_appointment.html", context)


# --------------------------------
# TODAY'S APPOINTMENTS & RECEPTION DESK
# --------------------------------

@staff_required
def today_appointments(request):
    today = timezone.localdate()
    status_filter = request.GET.get("status", "BOOKED").strip().upper()
    doctor_filter = request.GET.get("doctor", "").strip()
    search_query = request.GET.get("search", "").strip()

    base_qs = Appointment.objects.filter(
        appointment_date=today,
        is_deleted=False,
        doctor__is_deleted=False,
        doctor__department__is_active=True,
        doctor__department__is_deleted=False,
        patient__is_deleted=False,
        doctor__is_active=True,
        patient__is_active=True,
        doctor__user__is_active=True,
    ).exclude(
        doctor__user__is_delete=True
    ).select_related(
        "patient",
        "patient__user",
        "doctor",
        "doctor__user",
        "doctor__department",
        "token",
    )

    # Status counts for filter pills
    count_all = base_qs.count()
    count_booked = base_qs.filter(status="BOOKED").count()
    count_checked_in = base_qs.filter(status="CHECKED_IN").count()
    count_in_consult = base_qs.filter(status="IN_CONSULTATION").count()
    count_completed = base_qs.filter(status="COMPLETED").count()
    count_cancelled = base_qs.filter(status="CANCELLED").count()

    filtered_qs = base_qs
    if status_filter != "ALL":
        valid_statuses = dict(Appointment.STATUS_CHOICES)
        if status_filter in valid_statuses:
            filtered_qs = filtered_qs.filter(status=status_filter)
        else:
            status_filter = "BOOKED"
            filtered_qs = filtered_qs.filter(status="BOOKED")

    if doctor_filter and doctor_filter.isdigit():
        filtered_qs = filtered_qs.filter(doctor_id=int(doctor_filter))

    if search_query:
        filtered_qs = filtered_qs.filter(
            Q(patient__user__first_name__icontains=search_query) |
            Q(patient__user__last_name__icontains=search_query) |
            Q(patient__user__phone_number__icontains=search_query) |
            Q(token__token_number__icontains=search_query)
        )

    appointments = filtered_qs.order_by("appointment_time")

    all_doctors = Doctor.objects.filter(
        is_active=True,
        is_deleted=False,
        department__is_active=True,
        department__is_deleted=False,
        user__is_active=True,
        user__is_delete=False,
    ).select_related("user", "department").order_by("user__first_name")

    context = {
        "appointments": appointments,
        "status_filter": status_filter,
        "doctor_filter": doctor_filter,
        "search_query": search_query,
        "all_doctors": all_doctors,
        "today": today,
        "count_all": count_all,
        "count_booked": count_booked,
        "count_checked_in": count_checked_in,
        "count_in_consult": count_in_consult,
        "count_completed": count_completed,
        "count_cancelled": count_cancelled,
    }

    return render(
        request,
        "clinic/today_appointments.html",
        context,
    )


# --------------------------------
# RECEPTIONIST CHECK-IN
# --------------------------------

# --------------------------------
# RECEPTIONIST WALK-IN BOOKING
# --------------------------------

@staff_required
def walk_in_booking(request):
    today = timezone.localdate()

    if request.method == "POST":
        form = WalkInAppointmentForm(request.POST)
        if form.is_valid():
            mode = form.cleaned_data["patient_mode"]
            doctor = form.cleaned_data["doctor"]
            reason = form.cleaned_data.get("reason", "").strip()

            try:
                with transaction.atomic():
                    # Resolve patient
                    if mode == "EXISTING":
                        patient = form.cleaned_data["existing_patient"]
                    else:
                        first_name = form.cleaned_data["first_name"].strip()
                        last_name = form.cleaned_data["last_name"].strip()
                        phone_number = form.cleaned_data.get("phone_number", "").strip()
                        gender = form.cleaned_data.get("gender", "")
                        blood_group = form.cleaned_data.get("blood_group", "").strip()

                        username_base = f"walkin_{phone_number}" if phone_number else f"walkin_{uuid.uuid4().hex[:8]}"
                        username = username_base
                        counter = 1
                        while User.objects.filter(username=username).exists():
                            username = f"{username_base}_{counter}"
                            counter += 1

                        email = f"{username}@walkin.clinichub.local"
                        temp_pass = uuid.uuid4().hex

                        user = User.objects.create_user(
                            username=username,
                            email=email,
                            password=temp_pass,
                            first_name=first_name,
                            last_name=last_name,
                            phone_number=phone_number,
                            user_type="PATIENT",
                        )
                        user.is_email_verified = True
                        user.save(update_fields=["is_email_verified"])

                        patient = user.patient_profile
                        needs_profile_save = False
                        if gender:
                            patient.gender = gender
                            needs_profile_save = True
                        if blood_group:
                            patient.blood_group = blood_group
                            needs_profile_save = True
                        if needs_profile_save:
                            patient.save()

                    # Create walk-in appointment (marked CHECKED_IN)
                    now_time = timezone.localtime().time()
                    appointment = Appointment.objects.create(
                        patient=patient,
                        doctor=doctor,
                        appointment_date=today,
                        appointment_time=now_time,
                        status="CHECKED_IN",
                        reason=reason,
                    )

                    # Generate token (marked WAITING with checked_in_at)
                    token = generate_appointment_token(
                        appointment=appointment,
                        status="WAITING",
                        checked_in_at=timezone.now(),
                    )

                    try:
                        from .notifications import send_appointment_confirmation_email
                        send_appointment_confirmation_email(appointment, token=token, fail_silently=True)
                    except Exception as exc:
                        logger.warning("Could not dispatch walk-in confirmation email: %s", exc)

                messages.success(
                    request,
                    f"Walk-in appointment registered for {patient}. Assigned Queue Token #{token.formatted_token_number} for Dr. {doctor.user.last_name}."
                )
                return redirect("queue")

            except Exception as e:
                form.add_error(None, f"Could not complete walk-in booking: {e}")

    else:
        initial_dept = request.GET.get("department")
        initial_doc = request.GET.get("doctor")
        initial_patient = request.GET.get("patient_id")
        initial = {}
        if initial_dept:
            initial["department"] = initial_dept
        if initial_doc:
            initial["doctor"] = initial_doc
        if initial_patient:
            initial["existing_patient"] = initial_patient
            initial["patient_mode"] = "EXISTING"
        form = WalkInAppointmentForm(initial=initial)

    return render(
        request,
        "clinic/walk_in.html",
        {"form": form, "today": today},
    )


# --------------------------------
# RECEPTIONIST CHECK-IN
# --------------------------------

@staff_required
def check_in_patient(request, appointment_id):
    if request.method != "POST":
        return redirect("today-appointments")

    with transaction.atomic():
        appointment = get_object_or_404(
            Appointment.objects.select_for_update(),
            id=appointment_id,
            appointment_date=timezone.localdate(),
            is_deleted=False,
            doctor__is_deleted=False,
            patient__is_deleted=False,
        )

        if appointment.status in ["BOOKED", "CHECKED_IN"]:
            appointment.status = "CHECKED_IN"
            appointment.save(update_fields=["status", "updated_at"])

            token = generate_appointment_token(
                appointment=appointment,
                status="WAITING",
                checked_in_at=timezone.now(),
            )

            # Auto-generate pending consultation fee for billing
            try:
                generate_or_get_consultation_invoice(appointment)
            except Exception:
                pass

            messages.success(
                request,
                f"Patient {appointment.patient} checked in. Token #{token.formatted_token_number} for Dr. {appointment.doctor.user.last_name}."
            )
        else:
            messages.warning(
                request,
                f"Cannot check in appointment with status '{appointment.get_status_display()}'."
            )

    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER")
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)

    return redirect("queue")


# --------------------------------
# PATIENT QUEUE (DOCTOR-ISOLATED & STAFF GROUPED)
# --------------------------------

@login_required
def queue(request):
    user = request.user
    if not (user.is_superuser or user.user_type in ["ADMIN", "STAFF", "DOCTOR"]):
        return HttpResponseForbidden("You do not have permission to view the waiting queue.")

    today = timezone.localdate()

    # Doctor queue isolation: doctors ONLY see their own queue
    if user.user_type == "DOCTOR":
        doctor = get_object_or_404(Doctor, user=user, is_deleted=False)
        tokens = Token.objects.filter(
            doctor=doctor,
            token_date=today,
            status="WAITING",
            is_deleted=False,
            appointment__is_deleted=False,
        ).select_related(
            "appointment__patient__user",
            "appointment__doctor__user",
            "appointment__doctor__department",
        ).order_by("token_number")

        doctor_queues = [{
            "doctor": doctor,
            "tokens": list(tokens),
            "waiting_count": tokens.count(),
        }]

        context = {
            "is_doctor_view": True,
            "doctor": doctor,
            "tokens": tokens,
            "doctor_queues": doctor_queues,
            "total_waiting": tokens.count(),
            "today": today,
        }
        return render(request, "clinic/queue.html", context)

    # Receptionist / Staff view: grouped by doctor
    selected_doctor_id = request.GET.get("doctor", "").strip()

    all_doctors = Doctor.objects.filter(
        is_active=True,
        is_deleted=False,
        department__is_active=True,
        department__is_deleted=False,
        user__is_active=True,
        user__is_delete=False,
    ).select_related("user", "department").order_by("user__first_name")

    base_tokens_qs = Token.objects.filter(
        token_date=today,
        status="WAITING",
        is_deleted=False,
        appointment__is_deleted=False,
        doctor__is_active=True,
        doctor__is_deleted=False,
        appointment__patient__is_deleted=False,
    ).select_related(
        "doctor",
        "doctor__user",
        "doctor__department",
        "appointment__patient__user",
    ).order_by("doctor__user__first_name", "token_number")

    if selected_doctor_id:
        try:
            base_tokens_qs = base_tokens_qs.filter(doctor_id=int(selected_doctor_id))
        except (ValueError, TypeError):
            pass

    tokens_list = list(base_tokens_qs)

    doctor_queues = []
    doctors_to_show = all_doctors.filter(id=int(selected_doctor_id)) if (selected_doctor_id and selected_doctor_id.isdigit()) else all_doctors

    for doc in doctors_to_show:
        doc_tokens = [t for t in tokens_list if t.doctor_id == doc.id]
        if doc_tokens or selected_doctor_id:
            doctor_queues.append({
                "doctor": doc,
                "tokens": doc_tokens,
                "waiting_count": len(doc_tokens),
            })

    # Include any doctors not in all_doctors that might have waiting tokens
    covered_token_ids = {t.id for q in doctor_queues for t in q["tokens"]}
    orphan_tokens = [t for t in tokens_list if t.id not in covered_token_ids]
    if orphan_tokens:
        orphan_docs = {t.doctor for t in orphan_tokens}
        for doc in orphan_docs:
            doc_tokens = [t for t in orphan_tokens if t.doctor_id == doc.id]
            doctor_queues.append({
                "doctor": doc,
                "tokens": doc_tokens,
                "waiting_count": len(doc_tokens),
            })

    total_waiting = len(tokens_list)

    context = {
        "is_doctor_view": False,
        "tokens": tokens_list,
        "doctor_queues": doctor_queues,
        "all_doctors": all_doctors,
        "selected_doctor_id": selected_doctor_id,
        "total_waiting": total_waiting,
        "today": today,
    }
    return render(request, "clinic/queue.html", context)


# --------------------------------
# CALL NEXT PATIENT (DOCTOR ONLY)
# --------------------------------

@doctor_required
def call_next_patient(request):
    if request.method != "POST":
        return redirect("doctor-dashboard")

    doctor = get_object_or_404(
        Doctor,
        user=request.user,
        is_deleted=False,
    )

    token, appointment = call_next_patient_for_doctor(doctor)

    if not token or not appointment:
        messages.info(request, "No waiting patients in your queue.")
        return redirect("doctor-dashboard")

    messages.success(
        request,
        f"Calling Token #{token.formatted_token_number}: {appointment.patient}. Consultation started."
    )
    return redirect("consultation-workspace", appointment_id=appointment.id)


# ========================================================
# PHASE 2: DOCTOR WORKFLOW VIEWS
# ========================================================

# --------------------------------
# 2.1 DOCTOR DASHBOARD
# --------------------------------

@doctor_required
def doctor_dashboard(request):
    doctor = get_object_or_404(
        Doctor,
        user=request.user,
        is_deleted=False,
    )

    today = timezone.localdate()

    today_appointments = Appointment.objects.filter(
        doctor=doctor,
        appointment_date=today,
        is_deleted=False,
    ).select_related(
        "patient",
        "patient__user",
        "token",
    ).order_by("appointment_time")

    total_today = today_appointments.count()
    completed_today = today_appointments.filter(status="COMPLETED").count()
    in_consultation = today_appointments.filter(status="IN_CONSULTATION").first()

    waiting_tokens = Token.objects.filter(
        doctor=doctor,
        token_date=today,
        status="WAITING",
        is_deleted=False,
        appointment__is_deleted=False,
    ).select_related(
        "appointment",
        "appointment__patient__user",
    ).order_by("token_number")

    waiting_count = waiting_tokens.count()
    next_token = waiting_tokens.first()
    next_patient = next_token.appointment if next_token else None

    context = {
        "doctor": doctor,
        "today": today,
        "today_appointments": today_appointments,
        "total_today": total_today,
        "completed_today": completed_today,
        "in_consultation": in_consultation,
        "waiting_count": waiting_count,
        "next_token": next_token,
        "next_patient": next_patient,
    }

    return render(
        request,
        "clinic/doctor/doctor_dashboard.html",
        context,
    )


# --------------------------------
# 2.2 DOCTOR'S APPOINTMENT LIST
# --------------------------------

@doctor_required
def doctor_appointments(request):
    doctor = get_object_or_404(
        Doctor,
        user=request.user,
        is_deleted=False,
    )

    today = timezone.localdate()
    raw_date_filter = request.GET.get("date_filter", "").strip()
    raw_custom_date = request.GET.get("custom_date", "").strip()
    status_filter = request.GET.get("status", "all").strip()
    search_query = request.GET.get("search", "").strip()

    # Priority 1: If custom_date query param provided, validate and parse
    # Priority 2: If date_filter is a date string (YYYY-MM-DD), parse
    # Priority 3: If date_filter is 'today', 'upcoming', 'past', or 'all'
    # Default: 'today'
    custom_date = ""
    parsed_date = None
    date_filter = "today"

    candidate_date_str = raw_custom_date or (raw_date_filter if raw_date_filter not in ["today", "upcoming", "past", "all", ""] else None)

    if candidate_date_str:
        try:
            parsed_date = datetime.strptime(candidate_date_str, "%Y-%m-%d").date()
            custom_date = parsed_date.strftime("%Y-%m-%d")
            date_filter = custom_date
        except (ValueError, TypeError):
            parsed_date = None
            date_filter = raw_date_filter if raw_date_filter in ["today", "upcoming", "past", "all"] else "today"
    elif raw_date_filter in ["today", "upcoming", "past", "all"]:
        date_filter = raw_date_filter
    else:
        date_filter = "today"

    appointments = Appointment.objects.filter(
        doctor=doctor,
        is_deleted=False,
    ).select_related(
        "patient",
        "patient__user",
        "token",
    )

    if date_filter == "today":
        appointments = appointments.filter(appointment_date=today)
    elif date_filter == "upcoming":
        appointments = appointments.filter(appointment_date__gt=today)
    elif date_filter == "past":
        appointments = appointments.filter(appointment_date__lt=today)
    elif date_filter == "all":
        pass  # Show all dates without restriction
    elif parsed_date:
        appointments = appointments.filter(appointment_date=parsed_date)

    valid_statuses = dict(Appointment.STATUS_CHOICES)
    if status_filter in valid_statuses:
        appointments = appointments.filter(status=status_filter)
    else:
        status_filter = "all"

    if search_query:
        appointments = appointments.annotate(
            patient_full_name=Concat(
                "patient__user__first_name",
                Value(" "),
                "patient__user__last_name",
            )
        ).filter(
            Q(patient__user__first_name__icontains=search_query)
            | Q(patient__user__last_name__icontains=search_query)
            | Q(patient_full_name__icontains=search_query)
            | Q(patient__user__phone_number__icontains=search_query)
            | Q(patient__user__username__icontains=search_query)
            | Q(reason__icontains=search_query)
            | Q(token__token_number__icontains=search_query)
        )

    appointments = appointments.order_by("-appointment_date", "appointment_time")

    # If 0 appointments found for this doctor on a specific date / today,
    # check if appointments exist for this date under other doctors in the clinic
    # to help when a patient booked with another specialist (e.g. ENT vs Cardiology).
    other_clinic_appointments = []
    if date_filter not in ["upcoming", "past", "all"]:
        check_date = today if date_filter == "today" else parsed_date
        if check_date:
            other_apts = Appointment.objects.filter(
                appointment_date=check_date,
                is_deleted=False,
            ).exclude(doctor=doctor).select_related(
                "doctor__user",
                "doctor__department",
                "patient__user",
            ).order_by("appointment_time")
            if status_filter in valid_statuses:
                other_apts = other_apts.filter(status=status_filter)
            other_clinic_appointments = list(other_apts[:5])

    context = {
        "doctor": doctor,
        "today": today,
        "appointments": appointments,
        "date_filter": date_filter,
        "custom_date": custom_date,
        "parsed_date": parsed_date,
        "status_filter": status_filter,
        "search_query": search_query,
        "status_choices": Appointment.STATUS_CHOICES,
        "other_clinic_appointments": other_clinic_appointments,
    }

    return render(
        request,
        "clinic/doctor/doctor_appointments.html",
        context,
    )


# --------------------------------
# 2.3 START CONSULTATION
# --------------------------------

@doctor_required
def start_consultation(request, appointment_id):
    if request.method != "POST":
        return redirect("doctor-dashboard")

    with transaction.atomic():
        appointment = get_object_or_404(
            Appointment.objects.select_for_update(),
            id=appointment_id,
            doctor__user=request.user,
            is_deleted=False,
        )

        appointment.status = "IN_CONSULTATION"
        appointment.save(update_fields=["status", "updated_at"])

        if hasattr(appointment, "token") and appointment.token:
            token = appointment.token
            if token.status in ["BOOKED", "WAITING"]:
                token.status = "CALLED"
                token.save(update_fields=["status"])

    messages.info(
        request,
        f"Consultation started for {appointment.patient}."
    )
    return redirect("consultation-workspace", appointment_id=appointment.id)


# --------------------------------
# 2.4 CONSULTATION WORKSPACE
# --------------------------------

@doctor_required
def consultation_workspace(request, appointment_id):
    doctor = get_object_or_404(
        Doctor,
        user=request.user,
        is_deleted=False,
    )

    appointment = get_object_or_404(
        Appointment.objects.select_related(
            "patient",
            "patient__user",
            "token",
        ),
        id=appointment_id,
        doctor=doctor,
        is_deleted=False,
    )

    # 1. Past completed consultations
    past_appointments = Appointment.objects.filter(
        patient=appointment.patient,
        status="COMPLETED",
        is_deleted=False,
    ).exclude(
        id=appointment.id
    ).select_related(
        "doctor",
        "doctor__department",
        "medical_record",
        "prescription",
    ).prefetch_related(
        "prescription__items",
    ).order_by("-appointment_date")[:10]

    # 2. Existing clinical records for this consultation
    medical_record = MedicalRecord.objects.filter(appointment=appointment).first()
    prescription = Prescription.objects.filter(appointment=appointment).prefetch_related('items').first()
    lab_orders = appointment.lab_orders.filter(is_deleted=False)

    # 3. Patient Vitals
    vitals_history = appointment.patient.vitals.select_related("recorded_by").all()[:10]
    latest_vital = vitals_history.first() if vitals_history.exists() else None

    # 4. Patient Medical History (allergies, chronic conditions)
    medical_history, _ = PatientMedicalHistory.objects.get_or_create(
        patient=appointment.patient,
        defaults={'blood_group': appointment.patient.blood_group}
    )

    # Forms initialization
    initial_chief_complaints = ""
    if not medical_record and appointment.reason:
        initial_chief_complaints = appointment.reason

    record_form = MedicalRecordForm(
        instance=medical_record,
        prefix="record",
        initial={'chief_complaints': initial_chief_complaints} if initial_chief_complaints else None
    )
    prescription_form = PrescriptionForm(instance=prescription, prefix="rx")
    vital_form = PatientVitalForm(prefix="vital")
    medical_history_form = PatientMedicalHistoryForm(instance=medical_history, prefix="history")
    lab_order_form = LabTestOrderForm(prefix="lab")
    legacy_form = ConsultationNotesForm(instance=appointment)

    # Preset quick lab investigations
    common_lab_tests = [
        "Complete Blood Count (CBC)",
        "Fasting Blood Sugar (FBS)",
        "Postprandial Blood Sugar (PPBS)",
        "HbA1c (Glycated Hemoglobin)",
        "Lipid Profile",
        "Liver Function Test (LFT)",
        "Renal / Kidney Function Test (KFT)",
        "Thyroid Profile (T3, T4, TSH)",
        "Serum Electrolytes",
        "Urine Routine & Microscopic",
        "Chest X-Ray (PA View)",
        "ECG / EKG (12 Lead)",
    ]

    context = {
        "doctor": doctor,
        "appointment": appointment,
        "patient": appointment.patient,
        "past_appointments": past_appointments,
        "medical_record": medical_record,
        "prescription": prescription,
        "lab_orders": lab_orders,
        "vitals_history": vitals_history,
        "latest_vital": latest_vital,
        "medical_history": medical_history,
        "record_form": record_form,
        "prescription_form": prescription_form,
        "vital_form": vital_form,
        "medical_history_form": medical_history_form,
        "lab_order_form": lab_order_form,
        "form": legacy_form,
        "common_lab_tests": common_lab_tests,
    }

    return render(
        request,
        "clinic/doctor/consultation_workspace.html",
        context,
    )


# --------------------------------
# 2.4 COMPLETE CONSULTATION (EMR LITE)
# --------------------------------

@doctor_required
def complete_consultation(request, appointment_id):
    if request.method != "POST":
        return redirect("consultation-workspace", appointment_id=appointment_id)

    action = request.POST.get("action", "complete").strip()

    with transaction.atomic():
        appointment = get_object_or_404(
            Appointment.objects.select_for_update(),
            id=appointment_id,
            doctor__user=request.user,
            is_deleted=False,
        )
        doctor = appointment.doctor
        patient = appointment.patient

        # 1. Gather Clinical Note / Medical Record data
        legacy_notes = request.POST.get("notes", "").strip()
        chief_complaints = request.POST.get("record-chief_complaints", "").strip() or appointment.reason or legacy_notes or "Routine Consultation"
        clinical_findings = request.POST.get("record-clinical_findings", "").strip()
        diagnosis = request.POST.get("record-diagnosis", "").strip() or request.POST.get("diagnosis", "").strip() or legacy_notes or "General Consultation"
        treatment_plan = request.POST.get("record-treatment_plan", "").strip()
        record_follow_up = request.POST.get("record-follow_up_date") or None

        if not legacy_notes and not diagnosis and not request.POST.get("record-diagnosis"):
            messages.error(request, "Please enter a diagnosis or consultation notes.")
            return redirect("consultation-workspace", appointment_id=appointment_id)

        # Update or create MedicalRecord
        medical_record, _ = MedicalRecord.objects.get_or_create(
            appointment=appointment,
            defaults={
                'patient': patient,
                'doctor': doctor,
                'chief_complaints': chief_complaints,
                'clinical_findings': clinical_findings,
                'diagnosis': diagnosis,
                'treatment_plan': treatment_plan,
                'follow_up_date': record_follow_up if record_follow_up else None,
            }
        )
        medical_record.chief_complaints = chief_complaints
        medical_record.clinical_findings = clinical_findings
        medical_record.diagnosis = diagnosis
        medical_record.treatment_plan = treatment_plan
        if record_follow_up:
            medical_record.follow_up_date = record_follow_up
        medical_record.save()

        # 2. Gather Prescription data
        general_advice = request.POST.get("rx-general_advice", "").strip()
        rx_follow_up = request.POST.get("rx-follow_up_date") or record_follow_up or None

        prescription, _ = Prescription.objects.get_or_create(
            appointment=appointment,
            defaults={
                'patient': patient,
                'doctor': doctor,
                'medical_record': medical_record,
                'general_advice': general_advice,
                'follow_up_date': rx_follow_up if rx_follow_up else None,
            }
        )
        prescription.medical_record = medical_record
        prescription.general_advice = general_advice
        if rx_follow_up:
            prescription.follow_up_date = rx_follow_up
        prescription.save()

        # 3. Process dynamic Prescription Items
        medicine_names = request.POST.getlist("medicine_name[]")
        dosages = request.POST.getlist("dosage[]")
        frequencies = request.POST.getlist("frequency[]")
        durations = request.POST.getlist("duration[]")
        timings = request.POST.getlist("timing[]")
        instructions = request.POST.getlist("instructions[]")

        if medicine_names:
            prescription.items.all().delete()
            for i, med_name in enumerate(medicine_names):
                med_name = med_name.strip()
                if med_name:
                    PrescriptionItem.objects.create(
                        prescription=prescription,
                        medicine_name=med_name,
                        dosage=dosages[i].strip() if i < len(dosages) and dosages[i].strip() else "1 dose",
                        frequency=frequencies[i].strip() if i < len(frequencies) and frequencies[i].strip() else "OD",
                        duration=durations[i].strip() if i < len(durations) and durations[i].strip() else "5 days",
                        timing=timings[i].strip() if i < len(timings) else "",
                        instructions=instructions[i].strip() if i < len(instructions) else "",
                    )

        # 4. Process Lab Orders
        quick_labs = request.POST.getlist("quick_lab_tests[]")
        for test in quick_labs:
            test = test.strip()
            if test:
                LabTestOrder.objects.get_or_create(
                    appointment=appointment,
                    patient=patient,
                    doctor=doctor,
                    test_name=test,
                )

        custom_lab_names = request.POST.getlist("custom_lab_name[]")
        custom_lab_indications = request.POST.getlist("custom_lab_indication[]")
        for i, test in enumerate(custom_lab_names):
            test = test.strip()
            if test:
                ind = custom_lab_indications[i].strip() if i < len(custom_lab_indications) else ""
                LabTestOrder.objects.create(
                    appointment=appointment,
                    patient=patient,
                    doctor=doctor,
                    test_name=test,
                    clinical_indication=ind,
                )

        # 5. Process Quick Vitals if submitted in consultation form
        systolic = request.POST.get("vital-systolic_bp")
        diastolic = request.POST.get("vital-diastolic_bp")
        pulse = request.POST.get("vital-pulse_rate")
        temp = request.POST.get("vital-temperature")
        weight = request.POST.get("vital-weight_kg")
        height = request.POST.get("vital-height_cm")
        spo2 = request.POST.get("vital-spo2")

        if any([systolic, diastolic, pulse, temp, weight, height, spo2]):
            PatientVital.objects.create(
                patient=patient,
                appointment=appointment,
                recorded_by=request.user,
                systolic_bp=int(systolic) if systolic and systolic.isdigit() else None,
                diastolic_bp=int(diastolic) if diastolic and diastolic.isdigit() else None,
                pulse_rate=int(pulse) if pulse and pulse.isdigit() else None,
                temperature=temp if temp else None,
                weight_kg=weight if weight else None,
                height_cm=height if height else None,
                spo2=int(spo2) if spo2 and spo2.isdigit() else None,
            )

        # 6. Synchronize Appointment notes
        if legacy_notes:
            appointment.notes = legacy_notes
        else:
            appointment.notes = f"Diagnosis: {diagnosis}\nChief Complaints: {chief_complaints}\nTreatment Plan: {treatment_plan}"

        if action == "save_draft":
            appointment.save(update_fields=["notes", "updated_at"])
            messages.success(request, f"Consultation notes draft saved for {patient}.")
            return redirect("consultation-workspace", appointment_id=appointment.id)

        # Action is "complete"
        appointment.status = "COMPLETED"
        appointment.save(update_fields=["notes", "status", "updated_at"])

        if hasattr(appointment, "token") and appointment.token:
            token = appointment.token
            token.status = "COMPLETED"
            token.save(update_fields=["status"])

        # Auto-generate or link itemized invoice for front-desk / billing
        try:
            from .services import generate_or_get_appointment_invoice
            generate_or_get_appointment_invoice(appointment)
        except Exception:
            pass

        messages.success(
            request,
            f"Consultation for {patient} completed successfully."
        )

        # If medicines were prescribed, take doctor directly to print/view prescription!
        if prescription.items.exists():
            return redirect("prescription-print", prescription_id=prescription.id)

        return redirect("doctor-dashboard")


# --------------------------------
# 2.5 DOCTOR AVAILABILITY SCHEDULE
# --------------------------------

@doctor_required
def doctor_availability(request):
    doctor = get_object_or_404(
        Doctor,
        user=request.user,
        is_deleted=False,
    )

    if request.method == "POST":
        form = DoctorAvailabilityForm(request.POST)
        if form.is_valid():
            slot = form.save(commit=False)
            slot.doctor = doctor
            slot.save()
            messages.success(
                request,
                f"Weekly schedule slot for {slot.get_day_display()} added."
            )
            return redirect("doctor-availability")
    else:
        form = DoctorAvailabilityForm()

    availabilities = doctor.availabilities.filter(
        is_deleted=False
    ).order_by("day", "start_time")

    context = {
        "doctor": doctor,
        "availabilities": availabilities,
        "form": form,
    }

    return render(
        request,
        "clinic/doctor/doctor_availability.html",
        context,
    )


@doctor_required
def toggle_availability(request, availability_id):
    if request.method != "POST":
        return redirect("doctor-availability")

    slot = get_object_or_404(
        DoctorAvailability,
        id=availability_id,
        doctor__user=request.user,
        is_deleted=False,
    )

    slot.is_active = not slot.is_active
    slot.save(update_fields=["is_active", "updated_at"])

    status_str = "Active" if slot.is_active else "Inactive"
    messages.info(
        request,
        f"{slot.get_day_display()} slot updated to {status_str}."
    )
    return redirect("doctor-availability")


@doctor_required
def delete_availability(request, availability_id):
    if request.method != "POST":
        return redirect("doctor-availability")

    slot = get_object_or_404(
        DoctorAvailability,
        id=availability_id,
        doctor__user=request.user,
        is_deleted=False,
    )

    slot.is_deleted = True
    slot.is_active = False
    slot.save(update_fields=["is_deleted", "is_active", "updated_at"])

    messages.success(request, "Availability slot removed.")
    return redirect("doctor-availability")


# --------------------------------
# 2.6 PATIENT HISTORY (FOR DOCTORS)
# --------------------------------

@doctor_required
def doctor_patient_history(request, patient_id):
    doctor = get_object_or_404(
        Doctor,
        user=request.user,
        is_deleted=False,
    )

    patient = get_object_or_404(
        Patient.objects.select_related("user"),
        id=patient_id,
        is_deleted=False,
    )

    appointments = Appointment.objects.filter(
        patient=patient,
        is_deleted=False,
    ).select_related(
        "doctor",
        "doctor__department",
        "token",
        "medical_record",
        "prescription",
    ).prefetch_related(
        "prescription__items",
    ).order_by("-appointment_date", "-appointment_time")

    medical_history, _ = PatientMedicalHistory.objects.get_or_create(
        patient=patient,
        defaults={'blood_group': patient.blood_group}
    )
    vitals = patient.vitals.select_related("recorded_by").all()[:10]

    context = {
        "doctor": doctor,
        "patient": patient,
        "appointments": appointments,
        "medical_history": medical_history,
        "vitals": vitals,
    }

    return render(
        request,
        "clinic/doctor/patient_history.html",
        context,
    )


# ========================================================
# PHASE 3: STAFF & RECEPTIONIST WORKFLOW VIEWS
# ========================================================

# --------------------------------
# 3.1 STAFF DASHBOARD
# --------------------------------

@staff_required
def staff_dashboard(request):
    today = timezone.localdate()

    # Core stats for today
    today_appointments = Appointment.objects.filter(
        appointment_date=today,
        is_deleted=False,
    ).select_related(
        "patient__user", "doctor__user", "doctor__department", "token"
    )

    total_today = today_appointments.count()
    pending_checkins = today_appointments.filter(status="BOOKED")
    pending_count = pending_checkins.count()
    checked_in_count = today_appointments.filter(status="CHECKED_IN").count()
    in_consultation_count = today_appointments.filter(status="IN_CONSULTATION").count()
    completed_count = today_appointments.filter(status="COMPLETED").count()
    cancelled_count = today_appointments.filter(status="CANCELLED").count()

    total_patients_count = Patient.objects.filter(is_active=True, is_deleted=False).count()

    # Live waiting tokens across clinic
    waiting_tokens = Token.objects.filter(
        token_date=today,
        status="WAITING",
        is_deleted=False,
        appointment__is_deleted=False,
    ).select_related(
        "appointment__patient__user",
        "doctor__user",
        "doctor__department",
    ).order_by("token_number")

    waiting_count = waiting_tokens.count()

    # Active doctors summary for reception overview
    active_doctors = Doctor.objects.filter(
        is_active=True,
        is_deleted=False,
        department__is_active=True,
        department__is_deleted=False,
        user__is_active=True,
        user__is_delete=False,
    ).select_related("user", "department").order_by("user__first_name")

    doctor_summaries = []
    for doc in active_doctors:
        doc_today_appts = [a for a in today_appointments if a.doctor_id == doc.id]
        doc_waiting = [t for t in waiting_tokens if t.doctor_id == doc.id]
        doc_in_consult = next((a for a in doc_today_appts if a.status == "IN_CONSULTATION"), None)
        doc_completed = len([a for a in doc_today_appts if a.status == "COMPLETED"])

        doctor_summaries.append({
            "doctor": doc,
            "total_today": len(doc_today_appts),
            "waiting_count": len(doc_waiting),
            "in_consultation": doc_in_consult,
            "completed_count": doc_completed,
        })

    # Immediate arrivals stream: top 8 booked appointments waiting for check-in
    urgent_arrivals = pending_checkins.order_by("appointment_time")[:8]

    # Recent 5 registered patients
    recent_patients = Patient.objects.filter(
        is_active=True,
        is_deleted=False,
    ).select_related("user").order_by("-created_at")[:5]

    context = {
        "today": today,
        "total_today": total_today,
        "pending_count": pending_count,
        "checked_in_count": checked_in_count,
        "waiting_count": waiting_count,
        "in_consultation_count": in_consultation_count,
        "completed_count": completed_count,
        "cancelled_count": cancelled_count,
        "total_patients_count": total_patients_count,
        "doctor_summaries": doctor_summaries,
        "urgent_arrivals": urgent_arrivals,
        "waiting_tokens": waiting_tokens[:6],
        "recent_patients": recent_patients,
    }

    return render(request, "clinic/staff/staff_dashboard.html", context)


# --------------------------------
# 3.7 PATIENT SEARCH & DIRECTORY
# --------------------------------

@staff_required
def staff_patient_search(request):
    query = request.GET.get("q", "").strip()
    selected_patient_id = request.GET.get("patient_id", "").strip()

    patients_qs = Patient.objects.filter(
        is_active=True,
        is_deleted=False,
    ).select_related("user").order_by("-created_at")

    if query:
        patients_qs = patients_qs.filter(
            Q(user__first_name__icontains=query) |
            Q(user__last_name__icontains=query) |
            Q(user__phone_number__icontains=query) |
            Q(user__email__icontains=query) |
            Q(blood_group__iexact=query)
        )

    patients_data = []
    today = timezone.localdate()
    for p in patients_qs[:50]:
        appts = p.appointments.filter(is_deleted=False)
        total_visits = appts.count()
        last_appt = appts.order_by("-appointment_date", "-appointment_time").first()
        upcoming = appts.filter(
            appointment_date__gte=today,
            status__in=["BOOKED", "CHECKED_IN", "IN_CONSULTATION"]
        ).order_by("appointment_date", "appointment_time").first()

        patients_data.append({
            "patient": p,
            "total_visits": total_visits,
            "last_appointment": last_appt,
            "upcoming_appointment": upcoming,
        })

    selected_patient = None
    patient_history = []
    if selected_patient_id and selected_patient_id.isdigit():
        selected_patient = Patient.objects.filter(id=int(selected_patient_id), is_deleted=False).select_related("user").first()
        if selected_patient:
            patient_history = selected_patient.appointments.filter(is_deleted=False).select_related(
                "doctor__user", "doctor__department", "token"
            ).order_by("-appointment_date", "-appointment_time")

    context = {
        "query": query,
        "patients_data": patients_data,
        "total_results": len(patients_data),
        "selected_patient": selected_patient,
        "patient_history": patient_history,
        "today": today,
    }
    return render(request, "clinic/staff/patient_search.html", context)


# --------------------------------
# 3.8 REGISTER NEW PATIENT (STAFF)
# --------------------------------

@staff_required
def staff_register_patient(request):
    if request.method == "POST":
        form = StaffPatientRegistrationForm(request.POST)
        if form.is_valid():
            try:
                patient = form.save()
                messages.success(
                    request,
                    f"Patient {patient.user.first_name} {patient.user.last_name} registered successfully (ID #{patient.id})."
                )
                action = request.POST.get("action")
                if action == "book_walkin":
                    return redirect(f"{reverse('walk-in-booking')}?patient_id={patient.id}")
                elif action == "book_regular":
                    return redirect(f"{reverse('create-appointment')}?patient_id={patient.id}")
                return redirect(f"{reverse('staff-patient-search')}?patient_id={patient.id}")
            except Exception as e:
                form.add_error(None, f"Could not register patient: {e}")
    else:
        form = StaffPatientRegistrationForm()

    return render(request, "clinic/staff/register_patient.html", {"form": form})


# --------------------------------
# 3.6 STAFF CANCEL APPOINTMENT
# --------------------------------

@staff_required
def staff_cancel_appointment(request, appointment_id):
    appointment = get_object_or_404(
        Appointment.objects.select_related("doctor__user", "patient__user", "token"),
        id=appointment_id,
        is_deleted=False,
    )

    token = getattr(appointment, "token", None)

    if appointment.status in ["COMPLETED", "CANCELLED"]:
        messages.error(
            request,
            f"Cannot cancel appointment with status '{appointment.get_status_display()}'."
        )
        return redirect("today-appointments")

    if request.method == "POST":
        form = CancelAppointmentForm(request.POST)
        if form.is_valid():
            reason = form.cleaned_data["cancellation_reason"]
            notes = form.cleaned_data.get("notes", "").strip()

            cancellation_log = (
                f"[Cancelled by Reception Staff {request.user.first_name} {request.user.last_name} "
                f"on {timezone.now().strftime('%Y-%m-%d %H:%M')}: {reason}"
            )
            if notes:
                cancellation_log += f" - {notes}"
            cancellation_log += "]"

            with transaction.atomic():
                appointment.status = "CANCELLED"
                if appointment.notes:
                    appointment.notes += f"\n{cancellation_log}"
                else:
                    appointment.notes = cancellation_log
                appointment.save(update_fields=["status", "notes", "updated_at"])

                if token:
                    token.status = "CANCELLED"
                    token.save(update_fields=["status"])

            try:
                from .notifications import send_appointment_cancellation_email
                send_appointment_cancellation_email(appointment, reason=reason, fail_silently=True)
            except Exception as exc:
                logger.warning("Could not dispatch staff cancellation email: %s", exc)

            messages.warning(
                request,
                f"Appointment for {appointment.patient} with Dr. {appointment.doctor.user.last_name} on {appointment.appointment_date} has been cancelled."
            )
            return redirect("today-appointments")
    else:
        form = CancelAppointmentForm()

    context = {
        "form": form,
        "appointment": appointment,
        "token": token,
        "is_staff_view": True,
    }
    return render(request, "clinic/cancel_appointment.html", context)


# --------------------------------
# 3.6 STAFF RESCHEDULE APPOINTMENT
# --------------------------------

@staff_required
def staff_reschedule_appointment(request, appointment_id):
    appointment = get_object_or_404(
        Appointment.objects.select_related(
            "doctor",
            "doctor__user",
            "doctor__department",
            "patient",
            "patient__user",
        ),
        id=appointment_id,
        is_deleted=False,
    )

    if appointment.status not in ["BOOKED", "CHECKED_IN"]:
        messages.error(
            request,
            f"This appointment cannot be rescheduled because its current status is '{appointment.get_status_display()}'."
        )
        return redirect("today-appointments")

    token = getattr(appointment, "token", None)
    old_date = appointment.appointment_date
    old_time = appointment.appointment_time

    if request.method == "POST":
        form = AppointmentForm(request.POST, instance=appointment)
        if form.is_valid():
            new_date = form.cleaned_data["appointment_date"]
            new_time = form.cleaned_data["appointment_time"]
            new_doctor = form.cleaned_data["doctor"]
            new_slot_start = form.cleaned_data.get("slot_start_time") or new_time
            new_slot_end = form.cleaned_data.get("slot_end_time")
            new_time_slot = form.cleaned_data.get("time_slot") or ""
            new_token_num = form.cleaned_data.get("token_number")
            reason = form.cleaned_data.get("reason", "")

            try:
                with transaction.atomic():
                    # Validate doctor availability
                    if new_doctor.is_deleted or not new_doctor.is_active or new_doctor.user.is_delete or not new_doctor.user.is_active:
                        form.add_error("doctor", "The selected doctor is currently unavailable.")
                        return render(
                            request,
                            "clinic/reschedule_appointment.html",
                            {"form": form, "appointment": appointment, "token": token, "is_staff_view": True},
                        )

                    norm_slot_start = new_slot_start.replace(second=0, microsecond=0)
                    if new_slot_end:
                        norm_slot_end = new_slot_end.replace(second=0, microsecond=0)
                    else:
                        norm_slot_end = (datetime.combine(new_date, norm_slot_start) + timedelta(hours=1)).time()

                    if not new_time_slot:
                        new_time_slot = f"{norm_slot_start.strftime('%I:%M %p')} – {norm_slot_end.strftime('%I:%M %p')}"

                    slot_info = get_slot_tokens_availability(new_doctor, new_date, norm_slot_start)
                    max_tokens = slot_info['tokens_per_slot']

                    if new_token_num is None or new_token_num < 1 or new_token_num > max_tokens:
                        available_nums = [t['token_number'] for t in slot_info['tokens'] if t['is_available']]
                        if not available_nums:
                            form.add_error(None, f"Time slot {new_time_slot} is fully booked. Please select another slot.")
                            return render(request, "clinic/reschedule_appointment.html", {"form": form, "appointment": appointment, "token": token, "is_staff_view": True})
                        new_token_num = available_nums[0]

                    # Token conflict check
                    conflict = Token.objects.filter(
                        doctor=new_doctor,
                        token_date=new_date,
                        slot_start_time=norm_slot_start,
                        token_number=new_token_num,
                        is_deleted=False,
                    ).exclude(status='CANCELLED').exclude(appointment=appointment).exists()

                    if conflict:
                        form.add_error(None, f"Token #{new_token_num:03d} for {new_time_slot} is already reserved. Please select another token.")
                        return render(request, "clinic/reschedule_appointment.html", {"form": form, "appointment": appointment, "token": token, "is_staff_view": True})

                    reschedule_log = (
                        f"[Rescheduled by Reception Staff {request.user.first_name} {request.user.last_name} "
                        f"on {timezone.now().strftime('%Y-%m-%d %H:%M')}: "
                        f"From {old_date} {old_time.strftime('%I:%M %p')} to {new_date} {norm_slot_start.strftime('%I:%M %p')} "
                        f"with Dr. {new_doctor.user.last_name}]"
                    )

                    appointment.appointment_date = new_date
                    appointment.appointment_time = norm_slot_start
                    appointment.slot_start_time = norm_slot_start
                    appointment.slot_end_time = norm_slot_end
                    appointment.time_slot = new_time_slot
                    appointment.doctor = new_doctor
                    appointment.status = "BOOKED"
                    appointment.reason = reason
                    if appointment.notes:
                        appointment.notes += f"\n{reschedule_log}"
                    else:
                        appointment.notes = reschedule_log
                    appointment.save()

                    if token:
                        token.doctor = new_doctor
                        token.token_date = new_date
                        token.slot_start_time = norm_slot_start
                        token.slot_end_time = norm_slot_end
                        token.time_slot = new_time_slot
                        token.token_number = new_token_num
                        token.status = "BOOKED"
                        token.checked_in_at = None
                        token.save()
                    else:
                        token = Token.objects.create(
                            appointment=appointment,
                            doctor=new_doctor,
                            token_date=new_date,
                            token_number=new_token_num,
                            slot_start_time=norm_slot_start,
                            slot_end_time=norm_slot_end,
                            time_slot=new_time_slot,
                            status="BOOKED",
                        )

                try:
                    from .notifications import send_appointment_rescheduled_email
                    send_appointment_rescheduled_email(appointment, token=token, fail_silently=True)
                except Exception as exc:
                    logger.warning("Could not dispatch staff reschedule email: %s", exc)

                messages.success(
                    request,
                    f"Appointment for {appointment.patient} successfully rescheduled to {new_date} at {new_time_slot} with Dr. {new_doctor.user.last_name}. New Token #{token.formatted_token_number}."
                )
                return redirect("today-appointments")
            except Exception as e:
                form.add_error(None, f"Reschedule failed: {e}")
    else:
        initial = {
            "department": appointment.doctor.department_id,
            "doctor": appointment.doctor_id,
            "appointment_date": appointment.appointment_date,
            "appointment_time": appointment.appointment_time,
            "slot_start_time": appointment.slot_start_time or appointment.appointment_time,
            "slot_end_time": appointment.slot_end_time,
            "time_slot": appointment.time_slot,
            "token_number": token.token_number if token else None,
            "reason": appointment.reason,
        }
        form = AppointmentForm(instance=appointment, initial=initial)

    context = {
        "form": form,
        "appointment": appointment,
        "token": token,
        "is_staff_view": True,
    }
    return render(request, "clinic/reschedule_appointment.html", context)


# ==============================================================================
# PHASE 4: CUSTOM ADMIN MANAGEMENT PORTAL
# ==============================================================================

# --------------------------------
# 4.1 ADMIN DASHBOARD & COMMAND CENTER
# --------------------------------

@admin_required
def admin_dashboard(request):
    today = timezone.localdate()

    # Core Entity Counts
    total_patients = Patient.objects.filter(is_deleted=False).count()
    active_doctors = Doctor.objects.filter(is_active=True, is_deleted=False).count()
    total_doctors = Doctor.objects.filter(is_deleted=False).count()
    active_staff = Staff.objects.filter(is_active=True, is_deleted=False).count()
    total_departments = Department.objects.filter(is_deleted=False).count()

    # Today's Operational Stats
    today_appts = Appointment.objects.filter(appointment_date=today, is_deleted=False)
    today_total = today_appts.count()
    today_booked = today_appts.filter(status="BOOKED").count()
    today_waiting = today_appts.filter(status="CHECKED_IN").count()
    today_consulting = today_appts.filter(status="IN_CONSULTATION").count()
    today_completed = today_appts.filter(status="COMPLETED").count()
    today_cancelled = today_appts.filter(status="CANCELLED").count()

    # Active Consultations In Progress
    active_consultations = Appointment.objects.filter(
        appointment_date=today,
        status="IN_CONSULTATION",
        is_deleted=False,
    ).select_related("doctor__user", "doctor__department", "patient__user", "token")

    # Department Capacities & Load
    dept_stats = []
    for dept in Department.objects.filter(is_deleted=False).order_by("name"):
        doc_count = dept.doctors.filter(is_deleted=False, is_active=True).count()
        today_dept_appts = today_appts.filter(doctor__department=dept).count()
        waiting_dept = today_appts.filter(doctor__department=dept, status="CHECKED_IN").count()
        dept_stats.append({
            "department": dept,
            "doctor_count": doc_count,
            "today_appointments": today_dept_appts,
            "waiting_count": waiting_dept,
        })

    # Recent 7 Days Appointment Trend
    seven_days_ago = today - timedelta(days=6)
    daily_trend = []
    for i in range(7):
        day = seven_days_ago + timedelta(days=i)
        day_qs = Appointment.objects.filter(appointment_date=day, is_deleted=False)
        daily_trend.append({
            "date": day,
            "day_name": day.strftime("%a"),
            "total": day_qs.count(),
            "completed": day_qs.filter(status="COMPLETED").count(),
            "cancelled": day_qs.filter(status="CANCELLED").count(),
        })

    # Recent Records
    recent_patients = Patient.objects.filter(is_deleted=False).select_related("user").order_by("-id")[:5]
    recent_doctors = Doctor.objects.filter(is_deleted=False).select_related("user", "department").order_by("-id")[:5]

    context = {
        "today": today,
        "total_patients": total_patients,
        "active_doctors": active_doctors,
        "total_doctors": total_doctors,
        "active_staff": active_staff,
        "total_departments": total_departments,
        "today_total": today_total,
        "today_booked": today_booked,
        "today_waiting": today_waiting,
        "today_consulting": today_consulting,
        "today_completed": today_completed,
        "today_cancelled": today_cancelled,
        "active_consultations": active_consultations,
        "dept_stats": dept_stats,
        "daily_trend": daily_trend,
        "recent_patients": recent_patients,
        "recent_doctors": recent_doctors,
    }
    return render(request, "clinic/admin/admin_dashboard.html", context)


# --------------------------------
# 4.2 DOCTOR DIRECTORY & MANAGEMENT
# --------------------------------

@admin_required
def admin_doctor_list(request):
    dept_id = request.GET.get("department")
    status_filter = request.GET.get("status", "all")
    query = request.GET.get("q", "").strip()

    doctors = Doctor.objects.filter(is_deleted=False).select_related("user", "department")

    if dept_id and dept_id.isdigit():
        doctors = doctors.filter(department_id=int(dept_id))

    if status_filter == "active":
        doctors = doctors.filter(is_active=True, user__is_active=True)
    elif status_filter == "inactive":
        doctors = doctors.filter(Q(is_active=False) | Q(user__is_active=False))

    if query:
        doctors = doctors.filter(
            Q(user__first_name__icontains=query) |
            Q(user__last_name__icontains=query) |
            Q(user__email__icontains=query) |
            Q(license_number__icontains=query) |
            Q(specialization__icontains=query)
        )

    doctors = doctors.order_by("user__first_name", "user__last_name")
    departments = Department.objects.filter(is_deleted=False).order_by("name")

    total_docs = Doctor.objects.filter(is_deleted=False).count()
    active_docs = Doctor.objects.filter(is_deleted=False, is_active=True, user__is_active=True).count()
    inactive_docs = total_docs - active_docs

    context = {
        "doctors": doctors,
        "departments": departments,
        "selected_dept": int(dept_id) if dept_id and dept_id.isdigit() else "",
        "status_filter": status_filter,
        "query": query,
        "total_docs": total_docs,
        "active_docs": active_docs,
        "inactive_docs": inactive_docs,
    }
    return render(request, "clinic/admin/doctor_list.html", context)


@admin_required
def admin_doctor_create(request):
    if request.method == "POST":
        form = AdminDoctorForm(request.POST)
        if form.is_valid():
            try:
                doctor = form.save()
                messages.success(request, f"Dr. {doctor.user.first_name} {doctor.user.last_name} onboarded successfully.")
                return redirect("admin-doctors")
            except Exception as e:
                form.add_error(None, f"Error creating doctor: {e}")
    else:
        form = AdminDoctorForm()

    return render(request, "clinic/admin/doctor_form.html", {"form": form, "is_edit": False})


@admin_required
def admin_doctor_edit(request, doctor_id):
    doctor = get_object_or_404(Doctor.objects.select_related("user", "department"), id=doctor_id, is_deleted=False)
    if request.method == "POST":
        form = AdminDoctorForm(request.POST, doctor=doctor)
        if form.is_valid():
            try:
                doctor = form.save()
                messages.success(request, f"Profile for Dr. {doctor.user.first_name} {doctor.user.last_name} updated successfully.")
                return redirect("admin-doctors")
            except Exception as e:
                form.add_error(None, f"Error updating doctor: {e}")
    else:
        form = AdminDoctorForm(doctor=doctor)

    return render(request, "clinic/admin/doctor_form.html", {"form": form, "doctor": doctor, "is_edit": True})


@admin_required
def admin_doctor_toggle(request, doctor_id):
    if request.method != "POST":
        return redirect("admin-doctors")

    doctor = get_object_or_404(Doctor.objects.select_related("user"), id=doctor_id, is_deleted=False)
    new_state = not doctor.is_active
    doctor.is_active = new_state
    doctor.user.is_active = new_state
    doctor.user.save(update_fields=["is_active"])
    doctor.save(update_fields=["is_active"])

    status_str = "activated" if new_state else "deactivated"
    messages.success(request, f"Dr. {doctor.user.last_name} has been {status_str}.")
    return redirect("admin-doctors")


# --------------------------------
# 4.3 STAFF ROSTER & MANAGEMENT
# --------------------------------

@admin_required
def admin_staff_list(request):
    role_filter = request.GET.get("role", "all")
    dept_id = request.GET.get("department")
    status_filter = request.GET.get("status", "all")
    query = request.GET.get("q", "").strip()

    staff_qs = Staff.objects.filter(is_deleted=False).select_related("user", "department")

    if role_filter != "all":
        staff_qs = staff_qs.filter(user__staff_role=role_filter)

    if dept_id and dept_id.isdigit():
        staff_qs = staff_qs.filter(department_id=int(dept_id))

    if status_filter == "active":
        staff_qs = staff_qs.filter(is_active=True, user__is_active=True)
    elif status_filter == "inactive":
        staff_qs = staff_qs.filter(Q(is_active=False) | Q(user__is_active=False))

    if query:
        staff_qs = staff_qs.filter(
            Q(user__first_name__icontains=query) |
            Q(user__last_name__icontains=query) |
            Q(user__email__icontains=query) |
            Q(designation__icontains=query)
        )

    staff_members = staff_qs.order_by("user__first_name", "user__last_name")
    departments = Department.objects.filter(is_deleted=False).order_by("name")

    total_staff = Staff.objects.filter(is_deleted=False).count()
    active_staff = Staff.objects.filter(is_deleted=False, is_active=True, user__is_active=True).count()
    inactive_staff = total_staff - active_staff

    context = {
        "staff_members": staff_members,
        "departments": departments,
        "role_choices": User.STAFF_ROLE_CHOICES,
        "selected_role": role_filter,
        "selected_dept": int(dept_id) if dept_id and dept_id.isdigit() else "",
        "status_filter": status_filter,
        "query": query,
        "total_staff": total_staff,
        "active_staff": active_staff,
        "inactive_staff": inactive_staff,
    }
    return render(request, "clinic/admin/staff_list.html", context)


@admin_required
def admin_staff_create(request):
    if request.method == "POST":
        form = AdminStaffForm(request.POST)
        if form.is_valid():
            try:
                staff = form.save()
                messages.success(request, f"Staff member {staff.user.first_name} {staff.user.last_name} registered successfully.")
                return redirect("admin-staff")
            except Exception as e:
                form.add_error(None, f"Error registering staff: {e}")
    else:
        form = AdminStaffForm()

    return render(request, "clinic/admin/staff_form.html", {"form": form, "is_edit": False})


@admin_required
def admin_staff_edit(request, staff_id):
    staff = get_object_or_404(Staff.objects.select_related("user", "department"), id=staff_id, is_deleted=False)
    if request.method == "POST":
        form = AdminStaffForm(request.POST, staff=staff)
        if form.is_valid():
            try:
                staff = form.save()
                messages.success(request, f"Profile for {staff.user.first_name} {staff.user.last_name} updated successfully.")
                return redirect("admin-staff")
            except Exception as e:
                form.add_error(None, f"Error updating staff: {e}")
    else:
        form = AdminStaffForm(staff=staff)

    return render(request, "clinic/admin/staff_form.html", {"form": form, "staff": staff, "is_edit": True})


@admin_required
def admin_staff_toggle(request, staff_id):
    if request.method != "POST":
        return redirect("admin-staff")

    staff = get_object_or_404(Staff.objects.select_related("user"), id=staff_id, is_deleted=False)
    new_state = not staff.is_active
    staff.is_active = new_state
    staff.user.is_active = new_state
    staff.user.save(update_fields=["is_active"])
    staff.save(update_fields=["is_active"])

    status_str = "activated" if new_state else "deactivated"
    messages.success(request, f"Staff member {staff.user.first_name} {staff.user.last_name} has been {status_str}.")
    return redirect("admin-staff")


# --------------------------------
# 4.4 DEPARTMENT MANAGEMENT
# --------------------------------

@admin_required
def admin_department_list(request):
    departments_qs = Department.objects.filter(is_deleted=False).order_by("name")
    dept_data = []
    for d in departments_qs:
        doc_count = d.doctors.filter(is_deleted=False).count()
        active_doc_count = d.doctors.filter(is_deleted=False, is_active=True).count()
        staff_count = d.staff_members.filter(is_deleted=False).count()
        dept_data.append({
            "department": d,
            "doctor_count": doc_count,
            "active_doctor_count": active_doc_count,
            "staff_count": staff_count,
        })

    context = {
        "dept_data": dept_data,
        "total_departments": len(dept_data),
        "active_departments": sum(1 for item in dept_data if item["department"].is_active),
    }
    return render(request, "clinic/admin/department_list.html", context)


@admin_required
def admin_department_create(request):
    if request.method == "POST":
        form = DepartmentForm(request.POST)
        if form.is_valid():
            dept = form.save()
            messages.success(request, f"Department '{dept.name}' created successfully.")
            return redirect("admin-departments")
    else:
        form = DepartmentForm()

    return render(request, "clinic/admin/department_form.html", {"form": form, "is_edit": False})


@admin_required
def admin_department_edit(request, department_id):
    dept = get_object_or_404(Department, id=department_id, is_deleted=False)
    if request.method == "POST":
        form = DepartmentForm(request.POST, instance=dept)
        if form.is_valid():
            dept = form.save()
            messages.success(request, f"Department '{dept.name}' updated successfully.")
            return redirect("admin-departments")
    else:
        form = DepartmentForm(instance=dept)

    return render(request, "clinic/admin/department_form.html", {"form": form, "department": dept, "is_edit": True})


@admin_required
def admin_department_toggle(request, department_id):
    if request.method != "POST":
        return redirect("admin-departments")

    dept = get_object_or_404(Department, id=department_id, is_deleted=False)
    dept.is_active = not dept.is_active
    dept.save(update_fields=["is_active"])
    status_str = "activated" if dept.is_active else "deactivated"
    messages.success(request, f"Department '{dept.name}' has been {status_str}.")
    return redirect("admin-departments")


# --------------------------------
# 4.5 CLINIC ANALYTICS & REPORTS
# --------------------------------

@admin_required
def admin_reports(request):
    timeframe = request.GET.get("timeframe", "30d")
    today = timezone.localdate()

    if timeframe == "7d":
        start_date = today - timedelta(days=6)
        label = "Last 7 Days"
    elif timeframe == "30d":
        start_date = today - timedelta(days=29)
        label = "Last 30 Days"
    elif timeframe == "month":
        start_date = today.replace(day=1)
        label = f"This Month ({today.strftime('%B %Y')})"
    else:
        start_date = None
        label = "All Time History"

    appts = Appointment.objects.filter(is_deleted=False)
    if start_date:
        appts = appts.filter(appointment_date__gte=start_date, appointment_date__lte=today)

    total_volume = appts.count()
    completed_count = appts.filter(status="COMPLETED").count()
    cancelled_count = appts.filter(status="CANCELLED").count()
    no_show_count = appts.filter(status="NO_SHOW").count()
    active_in_flow = appts.filter(status__in=["BOOKED", "CHECKED_IN", "IN_CONSULTATION"]).count()

    completion_rate = round((completed_count / total_volume * 100), 1) if total_volume > 0 else 0
    cancellation_rate = round((cancelled_count / total_volume * 100), 1) if total_volume > 0 else 0

    # Doctor Performance Table
    doctor_performance = []
    doctors = Doctor.objects.filter(is_deleted=False).select_related("user", "department")
    for d in doctors:
        doc_appts = appts.filter(doctor=d)
        doc_total = doc_appts.count()
        doc_completed = doc_appts.filter(status="COMPLETED").count()
        doc_cancelled = doc_appts.filter(status="CANCELLED").count()
        rate = round((doc_completed / doc_total * 100), 1) if doc_total > 0 else 0
        doctor_performance.append({
            "doctor": d,
            "total": doc_total,
            "completed": doc_completed,
            "cancelled": doc_cancelled,
            "completion_rate": rate,
        })
    doctor_performance.sort(key=lambda x: x["total"], reverse=True)

    # Department Breakdown Table
    department_stats = []
    for dept in Department.objects.filter(is_deleted=False):
        dept_appts = appts.filter(doctor__department=dept)
        dept_total = dept_appts.count()
        dept_completed = dept_appts.filter(status="COMPLETED").count()
        share = round((dept_total / total_volume * 100), 1) if total_volume > 0 else 0
        department_stats.append({
            "department": dept,
            "total": dept_total,
            "completed": dept_completed,
            "share_percentage": share,
        })
    department_stats.sort(key=lambda x: x["total"], reverse=True)

    # Billing & Financial Analytics
    invoices = Invoice.objects.filter(is_deleted=False).exclude(status="CANCELLED")
    if start_date:
        invoices = invoices.filter(issued_at__date__gte=start_date, issued_at__date__lte=today)
    revenue_billed = invoices.aggregate(s=Sum("total_amount"))["s"] or Decimal("0.00")
    revenue_collected = invoices.aggregate(s=Sum("amount_paid"))["s"] or Decimal("0.00")
    revenue_due = invoices.aggregate(s=Sum("amount_due"))["s"] or Decimal("0.00")

    context = {
        "timeframe": timeframe,
        "timeframe_label": label,
        "total_volume": total_volume,
        "completed_count": completed_count,
        "cancelled_count": cancelled_count,
        "no_show_count": no_show_count,
        "active_in_flow": active_in_flow,
        "completion_rate": completion_rate,
        "cancellation_rate": cancellation_rate,
        "doctor_performance": doctor_performance,
        "department_stats": department_stats,
        "revenue_billed": revenue_billed,
        "revenue_collected": revenue_collected,
        "revenue_due": revenue_due,
    }
    return render(request, "clinic/admin/admin_reports.html", context)


# ========================================================
# PHASE 5: MEDICAL RECORDS (EMR LITE) VIEWS
# ========================================================

# --------------------------------
# 5.1 RECORD PATIENT VITALS
# --------------------------------

@login_required
def record_patient_vitals(request, patient_id):
    """
    Allows nurses, front-desk staff, or physicians to log vitals before or during consultation.
    """
    if request.user.user_type not in ["DOCTOR", "STAFF", "ADMIN"] and not request.user.is_superuser:
        return HttpResponseForbidden("Only clinical staff and physicians can record vitals.")

    patient = get_object_or_404(
        Patient.objects.select_related("user"),
        id=patient_id,
        is_deleted=False,
    )
    today = timezone.localdate()
    today_appointment = Appointment.objects.filter(
        patient=patient,
        appointment_date=today,
        is_deleted=False,
    ).order_by("-appointment_time").first()

    if request.method == "POST":
        form = PatientVitalForm(request.POST)
        if form.is_valid():
            vital = form.save(commit=False)
            vital.patient = patient
            vital.recorded_by = request.user
            if today_appointment:
                vital.appointment = today_appointment
            vital.save()
            messages.success(request, f"Vital signs recorded for {patient}.")

            next_url = request.POST.get("next") or request.GET.get("next")
            if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
                return redirect(next_url)

            if request.user.user_type == "STAFF":
                return redirect("staff-dashboard")
            return redirect("patient-vitals-history", patient_id=patient.id)
    else:
        form = PatientVitalForm()

    past_vitals = patient.vitals.select_related("recorded_by").all()[:10]

    context = {
        "patient": patient,
        "form": form,
        "past_vitals": past_vitals,
        "today_appointment": today_appointment,
        "next_url": request.GET.get("next", ""),
    }
    return render(request, "clinic/staff/record_vitals.html", context)


# --------------------------------
# 5.2 PATIENT VITALS HISTORY
# --------------------------------

@login_required
def patient_vitals_history(request, patient_id):
    """
    Comprehensive historical log of all recorded vital signs for a patient.
    """
    if request.user.user_type == "PATIENT":
        patient = get_or_create_patient(request.user)
        if patient.id != patient_id:
            return HttpResponseForbidden("Unauthorized to view other patient's records.")
    elif request.user.user_type not in ["DOCTOR", "STAFF", "ADMIN"] and not request.user.is_superuser:
        return HttpResponseForbidden("Unauthorized access.")
    else:
        patient = get_object_or_404(Patient.objects.select_related("user"), id=patient_id, is_deleted=False)

    vitals = patient.vitals.select_related("recorded_by").all().order_by("-recorded_at")

    context = {
        "patient": patient,
        "vitals": vitals,
    }
    return render(request, "clinic/emr/patient_vitals_history.html", context)


# --------------------------------
# 5.3 EDIT PATIENT MEDICAL HISTORY
# --------------------------------

@login_required
def edit_patient_medical_history(request, patient_id):
    """
    Update enduring clinical background: allergies, chronic conditions, surgeries, and family history.
    """
    if request.user.user_type not in ["DOCTOR", "STAFF", "ADMIN"] and not request.user.is_superuser:
        return HttpResponseForbidden("Unauthorized to edit medical history.")

    patient = get_object_or_404(Patient.objects.select_related("user"), id=patient_id, is_deleted=False)
    history, _ = PatientMedicalHistory.objects.get_or_create(
        patient=patient,
        defaults={'blood_group': patient.blood_group}
    )

    if request.method == "POST":
        form = PatientMedicalHistoryForm(request.POST, instance=history)
        if form.is_valid():
            hist = form.save()
            if hist.blood_group and patient.blood_group != hist.blood_group:
                patient.blood_group = hist.blood_group
                patient.save(update_fields=["blood_group"])
            messages.success(request, f"Medical history and allergy profile updated for {patient}.")

            next_url = request.POST.get("next") or request.GET.get("next")
            if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
                return redirect(next_url)

            if request.user.user_type == "DOCTOR":
                return redirect("doctor-patient-history", patient_id=patient.id)
            return redirect("staff-patient-search")
    else:
        form = PatientMedicalHistoryForm(instance=history)

    context = {
        "patient": patient,
        "history": history,
        "form": form,
        "next_url": request.GET.get("next", ""),
    }
    return render(request, "clinic/emr/medical_history_form.html", context)


# --------------------------------
# 5.4 PRESCRIPTION PRINT / PDF VIEW
# --------------------------------

@login_required
def prescription_print(request, prescription_id):
    """
    Official medical prescription view formatted for printing and PDF export.
    """
    prescription = get_object_or_404(
        Prescription.objects.select_related(
            "patient",
            "patient__user",
            "doctor",
            "doctor__user",
            "doctor__department",
            "appointment",
            "appointment__token",
            "medical_record",
        ).prefetch_related("items"),
        id=prescription_id,
        is_deleted=False,
    )

    # Access control
    if request.user.user_type == "PATIENT":
        if prescription.patient.user != request.user:
            return HttpResponseForbidden("You are not authorized to view this prescription.")
    elif request.user.user_type not in ["DOCTOR", "STAFF", "ADMIN"] and not request.user.is_superuser:
        return HttpResponseForbidden("Unauthorized access.")

    patient = prescription.patient
    latest_vital = patient.vitals.first()
    lab_orders = prescription.appointment.lab_orders.filter(is_deleted=False)
    medical_history = getattr(patient, "medical_history", None)

    context = {
        "prescription": prescription,
        "patient": patient,
        "doctor": prescription.doctor,
        "appointment": prescription.appointment,
        "items": prescription.items.all(),
        "latest_vital": latest_vital,
        "lab_orders": lab_orders,
        "medical_record": prescription.medical_record,
        "medical_history": medical_history,
    }
    return render(request, "clinic/emr/prescription_print.html", context)


# --------------------------------
# 5.5 PATIENT PRESCRIPTIONS LIST
# --------------------------------

@patient_required
def patient_prescriptions(request):
    """
    Patient portal view of all prescriptions issued by clinic physicians.
    """
    patient = get_or_create_patient(request.user)
    prescriptions = Prescription.objects.filter(
        patient=patient,
        is_deleted=False,
    ).select_related(
        "doctor",
        "doctor__user",
        "doctor__department",
        "appointment",
        "medical_record",
    ).prefetch_related("items").order_by("-created_at")

    context = {
        "patient": patient,
        "prescriptions": prescriptions,
    }
    return render(request, "clinic/patient/patient_prescriptions.html", context)


# --------------------------------
# 5.6 PATIENT PRESCRIPTION DETAIL
# --------------------------------

@patient_required
def patient_prescription_detail(request, prescription_id):
    """
    Detailed prescription display with dosage schedule, instructions, and print option.
    """
    patient = get_or_create_patient(request.user)
    prescription = get_object_or_404(
        Prescription.objects.select_related(
            "doctor",
            "doctor__user",
            "doctor__department",
            "appointment",
            "medical_record",
        ).prefetch_related("items"),
        id=prescription_id,
        patient=patient,
        is_deleted=False,
    )
    lab_orders = prescription.appointment.lab_orders.filter(is_deleted=False)

    context = {
        "patient": patient,
        "prescription": prescription,
        "items": prescription.items.all(),
        "lab_orders": lab_orders,
    }
    return render(request, "clinic/patient/patient_prescription_detail.html", context)


# --------------------------------
# 5.7 LAB TEST ORDERS MANAGEMENT
# --------------------------------

@login_required
def lab_orders_list(request):
    """
    List and status tracker for laboratory diagnostic orders.
    """
    if request.user.user_type not in ["STAFF", "DOCTOR", "ADMIN"] and not request.user.is_superuser:
        return HttpResponseForbidden("Unauthorized access.")

    status_filter = request.GET.get("status", "all").strip()
    search_query = request.GET.get("search", "").strip()

    orders = LabTestOrder.objects.filter(
        is_deleted=False,
    ).select_related(
        "patient",
        "patient__user",
        "doctor",
        "doctor__user",
        "appointment",
    )

    valid_statuses = dict(LabTestOrder.STATUS_CHOICES)
    if status_filter in valid_statuses:
        orders = orders.filter(status=status_filter)
    else:
        status_filter = "all"

    if search_query:
        orders = orders.annotate(
            patient_full_name=Concat(
                "patient__user__first_name",
                Value(" "),
                "patient__user__last_name",
            )
        ).filter(
            Q(test_name__icontains=search_query)
            | Q(patient__user__first_name__icontains=search_query)
            | Q(patient__user__last_name__icontains=search_query)
            | Q(patient_full_name__icontains=search_query)
            | Q(doctor__user__first_name__icontains=search_query)
            | Q(doctor__user__last_name__icontains=search_query)
        )

    orders = orders.order_by("-ordered_at")

    counts = {
        "all": LabTestOrder.objects.filter(is_deleted=False).count(),
        "ordered": LabTestOrder.objects.filter(status="ORDERED", is_deleted=False).count(),
        "sample": LabTestOrder.objects.filter(status="SAMPLE_COLLECTED", is_deleted=False).count(),
        "in_progress": LabTestOrder.objects.filter(status="IN_PROGRESS", is_deleted=False).count(),
        "completed": LabTestOrder.objects.filter(status="COMPLETED", is_deleted=False).count(),
    }

    context = {
        "orders": orders,
        "status_filter": status_filter,
        "search_query": search_query,
        "status_choices": LabTestOrder.STATUS_CHOICES,
        "counts": counts,
    }
    return render(request, "clinic/emr/lab_orders_list.html", context)


# --------------------------------
# 5.8 UPDATE LAB ORDER STATUS
# --------------------------------

@login_required
def update_lab_order_status(request, order_id):
    """
    Update status of a diagnostic test (e.g. SAMPLE_COLLECTED, COMPLETED) with optional result notes.
    """
    if request.user.user_type not in ["STAFF", "DOCTOR", "ADMIN"] and not request.user.is_superuser:
        return HttpResponseForbidden("Unauthorized access.")

    if request.method != "POST":
        return redirect("lab-orders-list")

    order = get_object_or_404(LabTestOrder, id=order_id, is_deleted=False)
    new_status = request.POST.get("status", "").strip()
    results = request.POST.get("results_summary", "").strip()

    valid_statuses = dict(LabTestOrder.STATUS_CHOICES)
    if new_status in valid_statuses:
        order.status = new_status
        if results:
            order.results_summary = results
        if new_status == "COMPLETED" and not order.completed_at:
            order.completed_at = timezone.now()
        order.save(update_fields=["status", "results_summary", "completed_at"])
        messages.success(request, f"Lab order #{order.id} status updated to {order.get_status_display()}.")
    else:
        messages.error(request, "Invalid status choice.")

    next_url = request.POST.get("next")
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)
    return redirect("lab-orders-list")


# ========================================================
# PHASE 6: BILLING & PAYMENT VIEWS
# ========================================================

# --------------------------------
# 6.1 INVOICES DIRECTORY (STAFF & ADMIN)
# --------------------------------

@login_required
def billing_invoice_list(request):
    """
    Central billing registry with metrics, filter by payment status, and patient search.
    """
    if request.user.user_type not in ["STAFF", "ADMIN"] and not request.user.is_superuser:
        return HttpResponseForbidden("Unauthorized access to clinic billing.")

    seed_default_billable_services()
    backfill_all_pending_invoices()

    status_filter = request.GET.get("status", "all").strip().upper()
    type_filter = request.GET.get("type", "all").strip().upper()
    search_query = request.GET.get("search", "").strip()

    invoices = Invoice.objects.filter(is_deleted=False).select_related(
        "patient",
        "patient__user",
        "doctor",
        "doctor__user",
        "appointment",
        "lab_order",
    ).prefetch_related("items", "payments")

    valid_statuses = [s[0] for s in Invoice.STATUS_CHOICES]
    if status_filter in valid_statuses:
        invoices = invoices.filter(status=status_filter)
    else:
        status_filter = "all"

    valid_types = [t[0] for t in Invoice.INVOICE_TYPE_CHOICES]
    if type_filter in valid_types:
        invoices = invoices.filter(invoice_type=type_filter)
    else:
        type_filter = "all"

    if search_query:
        invoices = invoices.annotate(
            patient_full_name=Concat(
                "patient__user__first_name",
                Value(" "),
                "patient__user__last_name",
            )
        ).filter(
            Q(invoice_number__icontains=search_query)
            | Q(patient__user__first_name__icontains=search_query)
            | Q(patient__user__last_name__icontains=search_query)
            | Q(patient_full_name__icontains=search_query)
            | Q(doctor__user__first_name__icontains=search_query)
            | Q(doctor__user__last_name__icontains=search_query)
        )

    invoices = invoices.order_by("-issued_at")

    # Financial Analytics Aggregation
    all_active_invoices = Invoice.objects.filter(is_deleted=False).exclude(status="CANCELLED")
    total_billed = all_active_invoices.aggregate(s=Sum("total_amount"))["s"] or Decimal("0.00")
    total_paid = all_active_invoices.aggregate(s=Sum("amount_paid"))["s"] or Decimal("0.00")
    total_due = all_active_invoices.aggregate(s=Sum("amount_due"))["s"] or Decimal("0.00")
    unpaid_count = Invoice.objects.filter(is_deleted=False, status__in=["UNPAID", "PARTIAL"]).count()

    metrics = {
        "total_billed": total_billed,
        "total_paid": total_paid,
        "total_due": total_due,
        "unpaid_count": unpaid_count,
        "total_invoices": Invoice.objects.filter(is_deleted=False).count(),
    }

    context = {
        "invoices": invoices,
        "status_filter": status_filter,
        "type_filter": type_filter,
        "search_query": search_query,
        "metrics": metrics,
        "status_choices": Invoice.STATUS_CHOICES,
        "invoice_type_choices": Invoice.INVOICE_TYPE_CHOICES,
    }
    return render(request, "clinic/billing/invoice_list.html", context)


# --------------------------------
# 6.2 INVOICE DETAIL & PAYMENT PROCESSING
# --------------------------------

@login_required
def billing_invoice_detail(request, invoice_id):
    """
    Detailed itemized breakdown of an invoice, settlement history, and payment collection.
    """
    if request.user.user_type not in ["STAFF", "ADMIN", "DOCTOR"] and not request.user.is_superuser:
        return HttpResponseForbidden("Unauthorized access.")

    invoice = get_object_or_404(
        Invoice.objects.select_related(
            "patient",
            "patient__user",
            "doctor",
            "doctor__user",
            "appointment",
            "appointment__token",
        ).prefetch_related("items", "items__service", "payments", "payments__recorded_by"),
        id=invoice_id,
        is_deleted=False,
    )

    # Recalculate totals to ensure integrity
    invoice.calculate_totals()
    invoice.save()

    payment_form = PaymentForm(max_amount=invoice.amount_due)
    line_item_form = InvoiceLineItemForm()
    services = BillableService.objects.filter(is_active=True, is_deleted=False)

    if request.method == "POST":
        action = request.POST.get("action", "").strip()

        if action == "record_payment":
            if invoice.status == "PAID":
                messages.warning(request, "This invoice is already settled in full.")
                return redirect("billing-invoice-detail", invoice_id=invoice.id)

            p_form = PaymentForm(request.POST, max_amount=invoice.amount_due)
            if p_form.is_valid():
                amount = p_form.cleaned_data["amount"]
                if amount > invoice.amount_due:
                    messages.error(request, f"Payment amount (${amount:.2f}) cannot exceed amount due (${invoice.amount_due:.2f}).")
                    return redirect("billing-invoice-detail", invoice_id=invoice.id)

                pay_num = generate_payment_number()
                payment = p_form.save(commit=False)
                payment.invoice = invoice
                payment.payment_number = pay_num
                payment.recorded_by = request.user
                payment.save()

                invoice.calculate_totals()
                invoice.save()

                messages.success(request, f"Payment #{pay_num} of ${amount:.2f} recorded successfully via {payment.get_payment_method_display()}!")
                return redirect("billing-invoice-detail", invoice_id=invoice.id)
            else:
                messages.error(request, "Failed to record payment. Please check input errors.")

        elif action == "add_line_item":
            if invoice.status == "PAID":
                messages.error(request, "Cannot modify line items on a paid invoice.")
                return redirect("billing-invoice-detail", invoice_id=invoice.id)

            service_id = request.POST.get("service")
            description = request.POST.get("description", "").strip()
            quantity = int(request.POST.get("quantity", 1))
            unit_price_val = request.POST.get("unit_price", "").strip()

            service = None
            if service_id:
                service = BillableService.objects.filter(id=service_id).first()
                if not description and service:
                    description = service.name
                if not unit_price_val and service:
                    unit_price = service.base_fee
                else:
                    unit_price = Decimal(unit_price_val) if unit_price_val else Decimal("0.00")
            else:
                unit_price = Decimal(unit_price_val) if unit_price_val else Decimal("0.00")

            if not description:
                messages.error(request, "Line item description is required.")
                return redirect("billing-invoice-detail", invoice_id=invoice.id)

            InvoiceLineItem.objects.create(
                invoice=invoice,
                service=service,
                description=description,
                quantity=max(quantity, 1),
                unit_price=unit_price,
            )
            invoice.calculate_totals()
            invoice.save()
            messages.success(request, f"Added line item: {description}.")
            return redirect("billing-invoice-detail", invoice_id=invoice.id)

        elif action == "delete_line_item":
            if invoice.status == "PAID":
                messages.error(request, "Cannot remove line items from a paid invoice.")
                return redirect("billing-invoice-detail", invoice_id=invoice.id)

            item_id = request.POST.get("item_id")
            item = get_object_or_404(InvoiceLineItem, id=item_id, invoice=invoice)
            item.delete()
            invoice.calculate_totals()
            invoice.save()
            messages.success(request, f"Removed line item: {item.description}.")
            return redirect("billing-invoice-detail", invoice_id=invoice.id)

        elif action == "cancel_invoice":
            if invoice.amount_paid > Decimal("0.00"):
                messages.error(request, "Cannot cancel an invoice with recorded payments. Please void or refund payments first.")
            else:
                invoice.status = "CANCELLED"
                invoice.save(update_fields=["status", "updated_at"])
                messages.success(request, f"Invoice {invoice.invoice_number} has been cancelled.")
            return redirect("billing-invoice-detail", invoice_id=invoice.id)

    context = {
        "invoice": invoice,
        "items": invoice.items.all(),
        "payments": invoice.payments.all(),
        "payment_form": payment_form,
        "line_item_form": line_item_form,
        "services": services,
    }
    return render(request, "clinic/billing/invoice_detail.html", context)


# --------------------------------
# 6.3 CREATE INVOICE (MANUAL / WALK-IN / COMPREHENSIVE)
# --------------------------------

@login_required
def billing_invoice_create(request):
    """
    Form to generate a new custom or walk-in patient invoice with itemized line items.
    """
    if request.user.user_type not in ["STAFF", "ADMIN"] and not request.user.is_superuser:
        return HttpResponseForbidden("Unauthorized access.")

    seed_default_billable_services()
    services = BillableService.objects.filter(is_active=True, is_deleted=False)

    patient_id = request.GET.get("patient_id")
    appointment_id = request.GET.get("appointment_id")
    initial_patient = None
    initial_doctor = None
    initial_appointment = None

    if patient_id:
        initial_patient = Patient.objects.filter(id=patient_id, is_active=True).first()
    if appointment_id:
        initial_appointment = Appointment.objects.filter(id=appointment_id).first()
        if initial_appointment:
            initial_patient = initial_appointment.patient
            initial_doctor = initial_appointment.doctor

    if request.method == "POST":
        form = InvoiceCreateForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                invoice = form.save(commit=False)
                invoice_num = generate_invoice_number()
                while Invoice.objects.filter(invoice_number=invoice_num).exists():
                    invoice_num = generate_invoice_number()
                invoice.invoice_number = invoice_num
                invoice.status = 'UNPAID'
                invoice.save()

                # Process dynamic line items submitted
                item_services = request.POST.getlist("item_service[]")
                item_descriptions = request.POST.getlist("item_desc[]")
                item_quantities = request.POST.getlist("item_qty[]")
                item_prices = request.POST.getlist("item_price[]")

                has_items = False
                for i, desc in enumerate(item_descriptions):
                    desc = desc.strip()
                    if desc:
                        srv_id = item_services[i] if i < len(item_services) and item_services[i] else None
                        srv = BillableService.objects.filter(id=srv_id).first() if srv_id else None
                        qty = int(item_quantities[i]) if i < len(item_quantities) and item_quantities[i].isdigit() else 1
                        try:
                            price = Decimal(item_prices[i]) if i < len(item_prices) and item_prices[i] else Decimal("0.00")
                        except Exception:
                            price = Decimal("0.00")

                        InvoiceLineItem.objects.create(
                            invoice=invoice,
                            service=srv,
                            description=desc,
                            quantity=max(qty, 1),
                            unit_price=price,
                        )
                        has_items = True

                # If no items were added via dynamic rows, add consultation item if doctor selected
                if not has_items:
                    fee = Decimal("50.00")
                    InvoiceLineItem.objects.create(
                        invoice=invoice,
                        description="General Medical Consultation",
                        quantity=1,
                        unit_price=fee,
                    )

                invoice.calculate_totals()
                invoice.save()

                messages.success(request, f"Invoice {invoice.invoice_number} generated successfully!")
                return redirect("billing-invoice-detail", invoice_id=invoice.id)
        else:
            messages.error(request, "Failed to create invoice. Please check the fields below.")
    else:
        initial_data = {}
        if initial_patient:
            initial_data["patient"] = initial_patient
        if initial_doctor:
            initial_data["doctor"] = initial_doctor
        if initial_appointment:
            initial_data["appointment"] = initial_appointment
        form = InvoiceCreateForm(initial=initial_data)

    context = {
        "form": form,
        "services": services,
        "initial_patient": initial_patient,
    }
    return render(request, "clinic/billing/invoice_form.html", context)


# --------------------------------
# 6.4 PRINTABLE TAX INVOICE & RECEIPT
# --------------------------------

@login_required
def billing_invoice_print(request, invoice_id):
    """
    Clean, high-fidelity printable tax invoice and payment receipt.
    """
    invoice = get_object_or_404(
        Invoice.objects.select_related(
            "patient",
            "patient__user",
            "doctor",
            "doctor__user",
            "doctor__department",
            "appointment",
            "appointment__token",
        ).prefetch_related("items", "payments", "payments__recorded_by"),
        id=invoice_id,
        is_deleted=False,
    )

    # Authorization: Patient can only view their own invoice
    if request.user.user_type == "PATIENT":
        if invoice.patient.user != request.user:
            return HttpResponseForbidden("You are not authorized to view this invoice.")
    elif request.user.user_type not in ["STAFF", "DOCTOR", "ADMIN"] and not request.user.is_superuser:
        return HttpResponseForbidden("Unauthorized access.")

    invoice.calculate_totals()

    context = {
        "invoice": invoice,
        "items": invoice.items.all(),
        "payments": invoice.payments.all(),
        "now": timezone.now(),
    }
    return render(request, "clinic/billing/invoice_print.html", context)


# --------------------------------
# 6.5 SERVICE & FEE CATALOG MANAGEMENT
# --------------------------------

@login_required
def billing_service_list(request):
    """
    Fee catalog directory showing all billable clinical procedures and consultations.
    """
    if request.user.user_type not in ["STAFF", "ADMIN"] and not request.user.is_superuser:
        return HttpResponseForbidden("Unauthorized access.")

    seed_default_billable_services()

    category_filter = request.GET.get("category", "all").strip().upper()
    search_query = request.GET.get("search", "").strip()

    services = BillableService.objects.filter(is_deleted=False).select_related("department", "doctor", "doctor__user")

    valid_cats = [c[0] for c in BillableService.SERVICE_CATEGORY_CHOICES]
    if category_filter in valid_cats:
        services = services.filter(category=category_filter)
    else:
        category_filter = "all"

    if search_query:
        services = services.filter(
            Q(name__icontains=search_query)
            | Q(code__icontains=search_query)
            | Q(description__icontains=search_query)
        )

    services = services.order_by("category", "name")

    context = {
        "services": services,
        "category_filter": category_filter,
        "search_query": search_query,
        "category_choices": BillableService.SERVICE_CATEGORY_CHOICES,
        "total_count": services.count(),
    }
    return render(request, "clinic/billing/service_catalog.html", context)


@admin_required
def billing_service_create(request):
    """
    Admin view to add a new billable service to the catalog.
    """
    if request.method == "POST":
        form = BillableServiceForm(request.POST)
        if form.is_valid():
            service = form.save()
            messages.success(request, f"Service '{service.name}' created with base fee ${service.base_fee:.2f}.")
            return redirect("billing-service-list")
    else:
        form = BillableServiceForm()

    return render(request, "clinic/billing/service_form.html", {"form": form, "is_edit": False})


@admin_required
def billing_service_edit(request, service_id):
    """
    Admin view to modify pricing or details of an existing catalog service.
    """
    service = get_object_or_404(BillableService, id=service_id, is_deleted=False)
    if request.method == "POST":
        form = BillableServiceForm(request.POST, instance=service)
        if form.is_valid():
            srv = form.save()
            messages.success(request, f"Service '{srv.name}' updated successfully.")
            return redirect("billing-service-list")
    else:
        form = BillableServiceForm(instance=service)

    return render(request, "clinic/billing/service_form.html", {"form": form, "service": service, "is_edit": True})


@admin_required
def billing_service_toggle(request, service_id):
    """
    Toggle active status of a service.
    """
    if request.method != "POST":
        return redirect("billing-service-list")

    service = get_object_or_404(BillableService, id=service_id, is_deleted=False)
    service.is_active = not service.is_active
    service.save(update_fields=["is_active"])
    status_str = "activated" if service.is_active else "deactivated"
    messages.success(request, f"Service '{service.name}' has been {status_str}.")
    return redirect("billing-service-list")


# --------------------------------
# 6.6 PATIENT BILLING PORTAL
# --------------------------------

@patient_required
def patient_billing_history(request):
    """
    Patient portal overview of all invoices, settlements, and outstanding balances.
    """
    patient = get_or_create_patient(request.user)
    backfill_all_pending_invoices(patient=patient)
    invoices = Invoice.objects.filter(
        patient=patient,
        is_deleted=False,
    ).select_related("doctor", "doctor__user", "appointment", "lab_order").prefetch_related("items", "payments").order_by("-issued_at")

    total_billed = invoices.exclude(status="CANCELLED").aggregate(s=Sum("total_amount"))["s"] or Decimal("0.00")
    total_paid = invoices.exclude(status="CANCELLED").aggregate(s=Sum("amount_paid"))["s"] or Decimal("0.00")
    total_due = invoices.exclude(status="CANCELLED").aggregate(s=Sum("amount_due"))["s"] or Decimal("0.00")

    context = {
        "patient": patient,
        "invoices": invoices,
        "total_billed": total_billed,
        "total_paid": total_paid,
        "total_due": total_due,
    }
    return render(request, "clinic/patient/patient_invoices.html", context)


@patient_required
def patient_invoice_detail(request, invoice_id):
    """
    Patient view of an itemized invoice and payments.
    """
    patient = get_or_create_patient(request.user)
    invoice = get_object_or_404(
        Invoice.objects.select_related(
            "doctor",
            "doctor__user",
            "appointment",
            "appointment__token",
        ).prefetch_related("items", "payments"),
        id=invoice_id,
        patient=patient,
        is_deleted=False,
    )
    invoice.calculate_totals()

    context = {
        "patient": patient,
        "invoice": invoice,
        "items": invoice.items.all(),
        "payments": invoice.payments.all(),
    }
    return render(request, "clinic/patient/patient_invoice_detail.html", context)
