import uuid
from django.db import transaction
from django.db.models import Max
from django.core.exceptions import ValidationError
from django.utils import timezone
from decimal import Decimal
from users.models import User
from .models import (
    Doctor,
    Department,
    Token,
    Appointment,
    DoctorDailyTokenSequence,
    DoctorAvailability,
    BillableService,
    Invoice,
    InvoiceLineItem,
    Payment,
)


# Mapping of User.doctor_role to default Department name in ClinicHub
DOCTOR_ROLE_TO_DEPARTMENT_MAP = {
    'CARDIOLOGIST': 'Cardiology',
    'ORTHOPEDIST': 'Orthopedics',
    'DERMATOLOGIST': 'Dermatology',
    'ENT': 'ENT',
    'PEDIATRICIAN': 'Pediatrics',
    'GENERAL': 'General Medicine',
}


def get_unlinked_doctor_users():
    """
    Returns a QuerySet of User records with user_type='DOCTOR' that
    either have no linked Doctor profile or have a soft-deleted profile.
    """
    return User.objects.filter(
        user_type='DOCTOR'
    ).filter(
        doctor_profile__isnull=True
    ).order_by('username')


def resolve_doctor_department(user, department=None):
    """
    Safely resolves a valid existing Department instance for a doctor.
    Prioritizes explicit department argument; otherwise infers from user.doctor_role.
    Never guesses arbitrarily; raises ValueError if no valid department exists.
    """
    if isinstance(department, Department):
        if department.is_deleted or not department.is_active:
            raise ValueError(f"Department '{department.name}' is inactive or deleted.")
        return department

    if isinstance(department, int):
        dept = Department.objects.filter(pk=department, is_deleted=False, is_active=True).first()
        if not dept:
            raise ValueError(f"No active Department found with ID {department}.")
        return dept

    if isinstance(department, str) and department.strip():
        dept = Department.objects.filter(name__iexact=department.strip(), is_deleted=False, is_active=True).first()
        if not dept:
            raise ValueError(f"No active Department found matching name '{department}'.")
        return dept

    # Infer from user.doctor_role
    if user.doctor_role:
        expected_name = DOCTOR_ROLE_TO_DEPARTMENT_MAP.get(user.doctor_role)
        if expected_name:
            dept = Department.objects.filter(name__iexact=expected_name, is_deleted=False, is_active=True).first()
            if dept:
                return dept

    # If general or fallback, check if Cardiology or any active department exists
    first_active = Department.objects.filter(is_active=True, is_deleted=False).first()
    if first_active and user.doctor_role == 'GENERAL':
        return first_active

    raise ValueError(
        f"Unable to safely determine Department for doctor '{user.username}' (role: {user.doctor_role}). "
        f"Please provide an explicit valid existing Department."
    )


def generate_unique_license_number(user, license_number=None):
    """
    Validates or generates a clearly marked development license number for testing/dev accounts.
    Guarantees uniqueness across the Doctor table.
    """
    if license_number and str(license_number).strip():
        lic = str(license_number).strip()
        if Doctor.objects.filter(license_number=lic).exclude(user=user).exists():
            raise ValueError(f"License number '{lic}' is already in use by another doctor.")
        return lic

    # Generate development-safe license format: DEV-DOC-{USERNAME}
    base_code = f"DEV-DOC-{user.username.upper().replace(' ', '')}"
    candidate = base_code
    counter = 1

    while Doctor.objects.filter(license_number=candidate).exclude(user=user).exists():
        candidate = f"{base_code}-{counter}"
        counter += 1

    return candidate


def link_or_create_doctor_profile(user, department=None, specialization=None, license_number=None):
    """
    Safely creates or links a Doctor profile to an existing User account.
    Safe against duplicates, partial failures, and race conditions using transaction.atomic().

    Returns:
        tuple (Doctor instance, bool created)
    """
    if not isinstance(user, User):
        raise ValueError("Argument 'user' must be an instance of users.models.User.")

    if user.user_type != 'DOCTOR':
        raise ValueError(
            f"User '{user.username}' has user_type='{user.user_type}'. "
            f"Only accounts with user_type='DOCTOR' can be linked to a Doctor profile."
        )

    with transaction.atomic():
        # Check if already linked
        existing_profile = Doctor.objects.select_for_update().filter(user=user).first()

        if existing_profile:
            needs_save = False

            # Reactivate if it was soft-deleted
            if existing_profile.is_deleted or not existing_profile.is_active:
                existing_profile.is_deleted = False
                existing_profile.is_active = True
                needs_save = True

            # Update department if explicitly provided and different
            if department:
                resolved_dept = resolve_doctor_department(user, department)
                if existing_profile.department_id != resolved_dept.id:
                    existing_profile.department = resolved_dept
                    needs_save = True

            # Update specialization if explicitly provided and different
            if specialization and existing_profile.specialization != specialization:
                existing_profile.specialization = specialization
                needs_save = True

            if needs_save:
                existing_profile.save()

            return existing_profile, False

        # Resolve required attributes
        resolved_dept = resolve_doctor_department(user, department)
        resolved_spec = specialization or user.get_doctor_role_display() or "General Physician"
        resolved_lic = generate_unique_license_number(user, license_number)

        doctor = Doctor.objects.create(
            user=user,
            department=resolved_dept,
            specialization=resolved_spec,
            license_number=resolved_lic,
            is_active=True,
            is_deleted=False,
        )

        return doctor, True


def repair_unlinked_doctor_accounts(dry_run=False, username=None):
    """
    Finds and repairs existing User accounts with user_type='DOCTOR' that lack Doctor profiles.

    Args:
        dry_run (bool): If True, validates and previews actions without database modifications.
        username (str, optional): Target a specific username if provided.

    Returns:
        list of dicts containing repair details for each account.
    """
    queryset = User.objects.filter(user_type='DOCTOR')
    if username:
        queryset = queryset.filter(username=username)

    results = []

    for user in queryset:
        has_profile = Doctor.objects.filter(user=user, is_deleted=False).exists()

        if has_profile:
            results.append({
                'username': user.username,
                'email': user.email,
                'status': 'already_linked',
                'doctor_id': user.doctor_profile.id,
                'department': user.doctor_profile.department.name,
                'license_number': user.doctor_profile.license_number,
                'message': 'Account already has an active, valid Doctor profile.',
            })
            continue

        try:
            # Check what data would be used
            dept = resolve_doctor_department(user)
            spec = user.get_doctor_role_display() or "General Physician"
            lic = generate_unique_license_number(user)

            if dry_run:
                results.append({
                    'username': user.username,
                    'email': user.email,
                    'status': 'dry_run_ready',
                    'department': dept.name,
                    'specialization': spec,
                    'license_number': lic,
                    'message': f"Would create Doctor profile linked to Department '{dept.name}' with license '{lic}'.",
                })
            else:
                doctor, created = link_or_create_doctor_profile(
                    user=user,
                    department=dept,
                    specialization=spec,
                    license_number=lic,
                )
                results.append({
                    'username': user.username,
                    'email': user.email,
                    'status': 'repaired',
                    'doctor_id': doctor.id,
                    'department': doctor.department.name,
                    'specialization': doctor.specialization,
                    'license_number': doctor.license_number,
                    'message': f"Successfully created and linked Doctor profile #{doctor.id}.",
                })

        except Exception as e:
            results.append({
                'username': user.username,
                'email': user.email,
                'status': 'error',
                'message': str(e),
            })

    return results


# ========================================================
# REUSABLE TOKEN GENERATION & QUEUE SERVICE
# ========================================================

def get_next_token_number_for_doctor(doctor, token_date):
    """
    Atomically increments and returns the next token number for a specific doctor and date.
    Uses PostgreSQL-safe concurrency handling with row locking (select_for_update)
    on the dedicated DoctorDailyTokenSequence table.

    Guarantees:
    - Each doctor starts at 001 for each new date.
    - Multiple doctors have independent queues (Dr. A #001, Dr. B #001).
    - Never allocates duplicate token numbers for the same doctor on the same date.
    - Cancelled token numbers are not reused.
    """
    with transaction.atomic():
        seq, _ = DoctorDailyTokenSequence.objects.get_or_create(
            doctor=doctor,
            date=token_date,
            defaults={'last_token_number': 0}
        )

        # Acquire exclusive row lock on this doctor's daily sequence record
        locked_seq = DoctorDailyTokenSequence.objects.select_for_update().get(id=seq.id)

        # Guard against out-of-band token creation by ensuring counter is at least max token number
        max_existing = Token.objects.filter(
            doctor=doctor,
            token_date=token_date
        ).aggregate(m=Max('token_number'))['m'] or 0

        if locked_seq.last_token_number < max_existing:
            locked_seq.last_token_number = max_existing

        locked_seq.last_token_number += 1
        locked_seq.save(update_fields=['last_token_number', 'updated_at'])
        return locked_seq.last_token_number


@transaction.atomic
def generate_appointment_token(appointment, status='BOOKED', checked_in_at=None):
    """
    Generates or safely synchronizes a Token for an appointment.

    Guarantees:
    - Exactly one token per appointment.
    - Token's doctor matches appointment's doctor.
    - Token's date matches appointment's date.
    - If appointment already has a token for this doctor and date:
      updates status/checked_in_at without generating a duplicate token number.
    - If rescheduled to a new doctor or date:
      allocates the next token number for the new doctor/date sequence.
    """
    doctor = appointment.doctor
    token_date = appointment.appointment_date

    # Check for existing token linked to this appointment
    existing_token = Token.objects.select_for_update().filter(appointment=appointment).first()

    if existing_token:
        if existing_token.doctor_id == doctor.id and existing_token.token_date == token_date:
            # Same doctor and same date: do NOT re-generate token number
            fields_to_update = []
            if status and existing_token.status != status:
                existing_token.status = status
                fields_to_update.append('status')
            if checked_in_at and existing_token.checked_in_at != checked_in_at:
                existing_token.checked_in_at = checked_in_at
                fields_to_update.append('checked_in_at')
            if existing_token.is_deleted:
                existing_token.is_deleted = False
                fields_to_update.append('is_deleted')

            if fields_to_update:
                existing_token.save(update_fields=fields_to_update)
            return existing_token
        else:
            # Date or doctor changed (e.g. via rescheduling): allocate new sequential token number
            new_number = get_next_token_number_for_doctor(doctor, token_date)
            existing_token.doctor = doctor
            existing_token.token_date = token_date
            existing_token.token_number = new_number
            if status:
                existing_token.status = status
            if checked_in_at:
                existing_token.checked_in_at = checked_in_at
            existing_token.is_deleted = False
            existing_token.save()
            return existing_token

    # No token exists yet: allocate next token number for this doctor and date
    token_number = get_next_token_number_for_doctor(doctor, token_date)
    token = Token.objects.create(
        appointment=appointment,
        doctor=doctor,
        token_date=token_date,
        token_number=token_number,
        status=status,
        checked_in_at=checked_in_at,
    )
    return token


WEEKDAY_MAP = {
    0: 'MON',
    1: 'TUE',
    2: 'WED',
    3: 'THU',
    4: 'FRI',
    5: 'SAT',
    6: 'SUN',
}


def generate_doctor_time_slots(doctor, target_date):
    """
    Generates time slots for a doctor on a specific date based on DoctorAvailability.
    If doctor has no DoctorAvailability records in the system, provides standard clinic hours (09:00 - 13:00, 60min slots, 20 capacity, Mon-Sat).
    If doctor has DoctorAvailability configured, strictly respects their schedule for that weekday.

    Returns a list of slot dictionaries:
    - slot_id: e.g. "09:00-10:00"
    - start_time: "09:00"
    - end_time: "10:00"
    - label: "09:00 AM – 10:00 AM"
    - tokens_per_slot: int
    - booked_count: int
    - available_count: int
    - is_full: bool
    """
    import datetime

    if isinstance(target_date, str):
        target_date = datetime.date.fromisoformat(target_date)

    day_code = WEEKDAY_MAP.get(target_date.weekday())

    availabilities = DoctorAvailability.objects.filter(
        doctor=doctor,
        day=day_code,
        is_active=True,
        is_deleted=False,
    ).order_by('start_time')

    has_any_availability = DoctorAvailability.objects.filter(
        doctor=doctor,
        is_deleted=False,
    ).exists()

    slots_windows = []

    if availabilities.exists():
        for avail in availabilities:
            duration = avail.slot_duration or 60
            tokens_capacity = avail.tokens_per_slot or 20
            curr = datetime.datetime.combine(target_date, avail.start_time)
            end = datetime.datetime.combine(target_date, avail.end_time)

            if end <= curr:
                end += datetime.timedelta(days=1)

            while curr + datetime.timedelta(minutes=duration) <= end:
                slot_start = curr.time().replace(second=0, microsecond=0)
                slot_end = (curr + datetime.timedelta(minutes=duration)).time().replace(second=0, microsecond=0)
                slots_windows.append((slot_start, slot_end, tokens_capacity))
                curr += datetime.timedelta(minutes=duration)
    elif not has_any_availability and target_date.weekday() < 6:
        # Standard clinic schedule for doctors without explicit availability (Mon-Sat 9AM-1PM, 60min slots, 20 tokens each)
        for h in range(9, 13):
            slots_windows.append((
                datetime.time(h, 0),
                datetime.time(h + 1, 0),
                20
            ))
    else:
        # Doctor is not available on this day
        return []

    # Query active bookings for doctor on this date
    active_tokens = Token.objects.filter(
        doctor=doctor,
        token_date=target_date,
        is_deleted=False,
    ).exclude(status='CANCELLED')

    booked_counts = {}
    for tok in active_tokens:
        st = tok.slot_start_time or (tok.appointment.slot_start_time if hasattr(tok, 'appointment') else None) or tok.appointment.appointment_time
        if st:
            norm_st = st.replace(second=0, microsecond=0)
            booked_counts[norm_st] = booked_counts.get(norm_st, 0) + 1

    result = []
    for slot_start, slot_end, capacity in slots_windows:
        norm_start = slot_start.replace(second=0, microsecond=0)
        booked = booked_counts.get(norm_start, 0)
        avail = max(0, capacity - booked)
        slot_label = f"{slot_start.strftime('%I:%M %p')} – {slot_end.strftime('%I:%M %p')}"
        result.append({
            'slot_id': f"{slot_start.strftime('%H:%M')}-{slot_end.strftime('%H:%M')}",
            'start_time': slot_start.strftime('%H:%M'),
            'end_time': slot_end.strftime('%H:%M'),
            'label': slot_label,
            'tokens_per_slot': capacity,
            'booked_count': booked,
            'available_count': avail,
            'is_full': avail <= 0,
        })

    return result


def get_slot_tokens_availability(doctor, target_date, slot_start_time):
    """
    Returns list of tokens for a given doctor, date, and slot_start_time.
    Each time slot has its own independent token sequence starting from 1 to tokens_per_slot.
    Identifies which tokens are booked vs available.
    """
    import datetime

    if isinstance(target_date, str):
        target_date = datetime.date.fromisoformat(target_date)
    if isinstance(slot_start_time, str):
        parts = slot_start_time.split(':')
        slot_start_time = datetime.time(int(parts[0]), int(parts[1]))

    norm_slot_start = slot_start_time.replace(second=0, microsecond=0)

    # Determine tokens_per_slot for this doctor and slot
    day_code = WEEKDAY_MAP.get(target_date.weekday())
    avail = DoctorAvailability.objects.filter(
        doctor=doctor,
        day=day_code,
        is_active=True,
        is_deleted=False,
        start_time__lte=norm_slot_start,
        end_time__gt=norm_slot_start,
    ).first()

    tokens_capacity = avail.tokens_per_slot if avail and avail.tokens_per_slot else 20

    # Query currently active tokens for this doctor, date, and slot
    active_tokens = Token.objects.filter(
        doctor=doctor,
        token_date=target_date,
        slot_start_time=norm_slot_start,
        is_deleted=False,
    ).exclude(status='CANCELLED')

    booked_numbers = set(active_tokens.values_list('token_number', flat=True))

    tokens = []
    for num in range(1, tokens_capacity + 1):
        is_booked = num in booked_numbers
        tokens.append({
            'token_number': num,
            'formatted_token_number': f"{num:03d}",
            'is_booked': is_booked,
            'is_available': not is_booked,
            'status': 'BOOKED' if is_booked else 'AVAILABLE',
        })

    return {
        'tokens': tokens,
        'tokens_per_slot': tokens_capacity,
        'booked_count': len(booked_numbers),
        'available_count': max(0, tokens_capacity - len(booked_numbers)),
        'is_full': len(booked_numbers) >= tokens_capacity,
    }


@transaction.atomic
def book_slot_appointment(patient, doctor, appointment_date, slot_start_time, slot_end_time=None, time_slot="", token_number=None, reason=""):
    """
    Atomically books an appointment and assigns a token number in the doctor's time slot.
    Uses PostgreSQL row locking (select_for_update) on DoctorDailyTokenSequence
    and enforces database constraints to guarantee no double booking.
    """
    import datetime

    if isinstance(appointment_date, str):
        appointment_date = datetime.date.fromisoformat(appointment_date)
    if isinstance(slot_start_time, str):
        parts = slot_start_time.split(':')
        slot_start_time = datetime.time(int(parts[0]), int(parts[1]))
    if isinstance(slot_end_time, str) and slot_end_time.strip():
        parts = slot_end_time.split(':')
        slot_end_time = datetime.time(int(parts[0]), int(parts[1]))
    else:
        slot_end_time = None

    norm_slot_start = slot_start_time.replace(second=0, microsecond=0)
    if slot_end_time:
        norm_slot_end = slot_end_time.replace(second=0, microsecond=0)
    else:
        norm_slot_end = (datetime.datetime.combine(appointment_date, norm_slot_start) + datetime.timedelta(hours=1)).time()

    if not time_slot:
        time_slot = f"{norm_slot_start.strftime('%I:%M %p')} – {norm_slot_end.strftime('%I:%M %p')}"

    # 1. Validate date in past
    if appointment_date < timezone.localdate():
        raise ValidationError("Appointment date cannot be in the past.")

    # 2. Validate doctor & department
    if doctor.is_deleted or not doctor.is_active or doctor.user.is_delete or not doctor.user.is_active:
        raise ValidationError("The selected doctor is currently unavailable.")
    if doctor.department.is_deleted or not doctor.department.is_active:
        raise ValidationError("The selected department is currently unavailable.")

    # 3. Lock doctor daily sequence row to serialize concurrent booking transactions for this doctor and date
    DoctorDailyTokenSequence.objects.select_for_update().get_or_create(
        doctor=doctor,
        date=appointment_date,
        defaults={'last_token_number': 0}
    )

    # 4. Check slot availability & capacity
    slot_info = get_slot_tokens_availability(doctor, appointment_date, norm_slot_start)
    max_tokens = slot_info['tokens_per_slot']

    if token_number is None or token_number < 1 or token_number > max_tokens:
        available_nums = [t['token_number'] for t in slot_info['tokens'] if t['is_available']]
        if not available_nums:
            raise ValidationError(f"Time slot {time_slot} is fully booked. Please choose another time slot.")
        token_number = available_nums[0]

    # 5. Check if token is already booked for this doctor, date, and slot
    conflict = Token.objects.filter(
        doctor=doctor,
        token_date=appointment_date,
        slot_start_time=norm_slot_start,
        token_number=token_number,
        is_deleted=False,
    ).exclude(status='CANCELLED').exists()

    if conflict:
        raise ValidationError(f"Token #{token_number:03d} for {time_slot} is already reserved. Please select another token.")

    # 6. Create Appointment
    appointment = Appointment.objects.create(
        patient=patient,
        doctor=doctor,
        appointment_date=appointment_date,
        appointment_time=norm_slot_start,
        slot_start_time=norm_slot_start,
        slot_end_time=norm_slot_end,
        time_slot=time_slot,
        status='BOOKED',
        reason=reason or "",
    )

    # 7. Create Token
    token = Token.objects.create(
        appointment=appointment,
        doctor=doctor,
        token_date=appointment_date,
        token_number=token_number,
        slot_start_time=norm_slot_start,
        slot_end_time=norm_slot_end,
        time_slot=time_slot,
        status='BOOKED',
    )

    # 8. Dispatch Appointment Confirmation Email (Phase 7.2)
    try:
        from .notifications import send_appointment_confirmation_email
        send_appointment_confirmation_email(appointment, token=token, fail_silently=True)
    except Exception as exc:
        logger.warning("Could not send confirmation email for appointment %s: %s", appointment.id, exc)

    return appointment, token


@transaction.atomic
def call_next_patient_for_doctor(doctor, date=None):
    """
    Atomically selects the next eligible WAITING token for a specific doctor and date.
    Ordered by slot_start_time then token_number so earlier time slots are served first.
    Transitions token status to CALLED and appointment status to IN_CONSULTATION.
    Returns (token, appointment) tuple, or (None, None) if no waiting patient exists.
    """
    if date is None:
        date = timezone.localdate()

    next_token = (
        Token.objects.select_for_update()
        .filter(
            doctor=doctor,
            token_date=date,
            status='WAITING',
            is_deleted=False,
            appointment__is_deleted=False,
        )
        .select_related('appointment', 'appointment__patient__user')
        .order_by('slot_start_time', 'token_number')
        .first()
    )

    if not next_token:
        return None, None

    appointment = (
        Appointment.objects.select_for_update()
        .get(id=next_token.appointment_id)
    )

    next_token.status = 'CALLED'
    next_token.save(update_fields=['status'])

    appointment.status = 'IN_CONSULTATION'
    appointment.save(update_fields=['status', 'updated_at'])

    return next_token, appointment


# ========================================================
# PHASE 6: BILLING & PAYMENT SERVICES
# ========================================================

def generate_invoice_number():
    """
    Generates a unique invoice number like INV-20261022-XXXX.
    """
    today_str = timezone.localdate().strftime('%Y%m%d')
    suffix = uuid.uuid4().hex[:6].upper()
    return f"INV-{today_str}-{suffix}"


def generate_payment_number():
    """
    Generates a unique receipt number like REC-20261022-XXXX.
    """
    today_str = timezone.localdate().strftime('%Y%m%d')
    suffix = uuid.uuid4().hex[:6].upper()
    return f"REC-{today_str}-{suffix}"


def seed_default_billable_services():
    """
    Ensures standard clinic services and pricing exist in the catalog.
    """
    defaults = [
        {"name": "General Physician Consultation", "category": "CONSULTATION", "code": "CPT-99213", "base_fee": Decimal("50.00"), "description": "Standard outpatient physician consultation and assessment."},
        {"name": "Specialist Physician Consultation", "category": "CONSULTATION", "code": "CPT-99214", "base_fee": Decimal("85.00"), "description": "Specialist department consultation with comprehensive diagnostic evaluation."},
        {"name": "Complete Blood Count (CBC)", "category": "LAB_TEST", "code": "LAB-CBC", "base_fee": Decimal("25.00"), "description": "Comprehensive hematological panel including RBC, WBC, and Platelets."},
        {"name": "Fasting Blood Sugar (FBS)", "category": "LAB_TEST", "code": "LAB-FBS", "base_fee": Decimal("15.00"), "description": "Serum blood glucose screening after 8-hour fasting."},
        {"name": "Lipid Profile Panel", "category": "LAB_TEST", "code": "LAB-LIPID", "base_fee": Decimal("40.00"), "description": "Serum cholesterol, HDL, LDL, and triglycerides breakdown."},
        {"name": "Standard 12-Lead ECG", "category": "LAB_TEST", "code": "DX-ECG", "base_fee": Decimal("35.00"), "description": "Resting electrocardiogram recording with rhythm analysis."},
        {"name": "Wound Dressing & Antiseptic Care", "category": "PROCEDURE", "code": "PRC-DRS", "base_fee": Decimal("20.00"), "description": "Sterile minor wound cleaning, antiseptic dressing, and bandage change."},
        {"name": "Intravenous (IV) Therapy Administration", "category": "NURSING", "code": "NUR-IV", "base_fee": Decimal("30.00"), "description": "IV catheter insertion and hydration or medication fluid infusion."},
        {"name": "Clinical Facility & Registration Fee", "category": "OTHER", "code": "FAC-REG", "base_fee": Decimal("10.00"), "description": "Standard outpatient administrative processing and facility charge."},
    ]
    created_services = []
    for item in defaults:
        service, _ = BillableService.objects.get_or_create(
            name=item["name"],
            defaults=item,
        )
        created_services.append(service)
    return created_services


@transaction.atomic
def generate_or_get_consultation_invoice(appointment):
    """
    Creates or retrieves the dedicated distinct invoice for a doctor consultation fee.
    """
    existing = Invoice.objects.filter(
        appointment=appointment,
        invoice_type='CONSULTATION',
        is_deleted=False,
    ).first()
    if existing:
        return existing

    seed_default_billable_services()

    invoice_num = generate_invoice_number()
    while Invoice.objects.filter(invoice_number=invoice_num).exists():
        invoice_num = generate_invoice_number()

    today = timezone.localdate()
    invoice = Invoice.objects.create(
        invoice_number=invoice_num,
        invoice_type='CONSULTATION',
        patient=appointment.patient,
        appointment=appointment,
        doctor=appointment.doctor,
        due_date=today + timezone.timedelta(days=7),
        status='UNPAID',
        notes="Doctor Consultation Fee",
    )

    consultation_service = None
    if appointment.doctor:
        consultation_service = BillableService.objects.filter(
            doctor=appointment.doctor,
            category='CONSULTATION',
            is_active=True,
            is_deleted=False,
        ).first()

    if not consultation_service:
        consultation_service = BillableService.objects.filter(
            category='CONSULTATION',
            is_active=True,
            is_deleted=False,
        ).first()

    unit_fee = consultation_service.base_fee if consultation_service else Decimal("50.00")
    if appointment.doctor and appointment.doctor.user:
        doc_user = appointment.doctor.user
        doc_name = doc_user.get_full_name() if hasattr(doc_user, "get_full_name") else f"{doc_user.first_name} {doc_user.last_name}".strip()
        desc = f"Doctor Consultation - Dr. {doc_name}"
    else:
        desc = "Outpatient Consultation"

    InvoiceLineItem.objects.create(
        invoice=invoice,
        service=consultation_service,
        description=desc,
        quantity=1,
        unit_price=unit_fee,
        total_price=unit_fee,
    )

    invoice.calculate_totals()
    invoice.save()
    return invoice


@transaction.atomic
def generate_or_get_lab_order_invoice(lab_order):
    """
    Creates or retrieves a separate distinct bill for a diagnostic laboratory investigation.
    """
    existing = Invoice.objects.filter(
        lab_order=lab_order,
        is_deleted=False,
    ).first()
    if existing:
        return existing

    seed_default_billable_services()

    invoice_num = generate_invoice_number()
    while Invoice.objects.filter(invoice_number=invoice_num).exists():
        invoice_num = generate_invoice_number()

    today = timezone.localdate()
    invoice = Invoice.objects.create(
        invoice_number=invoice_num,
        invoice_type='LAB_TEST',
        patient=lab_order.patient,
        appointment=lab_order.appointment,
        lab_order=lab_order,
        doctor=lab_order.doctor,
        due_date=today + timezone.timedelta(days=7),
        status='UNPAID',
        notes=f"Diagnostic Investigation: {lab_order.test_name}",
    )

    matching_service = BillableService.objects.filter(
        name__icontains=lab_order.test_name,
        is_active=True,
        is_deleted=False,
    ).first()
    lab_fee = matching_service.base_fee if matching_service else Decimal("25.00")

    InvoiceLineItem.objects.create(
        invoice=invoice,
        service=matching_service,
        description=f"Lab Investigation: {lab_order.test_name}",
        quantity=1,
        unit_price=lab_fee,
        total_price=lab_fee,
    )

    invoice.calculate_totals()
    invoice.save()
    return invoice


@transaction.atomic
def generate_or_get_appointment_invoice(appointment):
    """
    Ensures separate distinct bills are generated for the appointment:
    1) Separate bill for Doctor Consultation.
    2) Separate bill for each ordered Diagnostic Lab Investigation.
    Returns the primary consultation invoice.
    """
    consult_inv = generate_or_get_consultation_invoice(appointment)

    # Auto-create separate bills for any diagnostic lab orders linked to this visit
    for lab_order in appointment.lab_orders.filter(is_deleted=False):
        generate_or_get_lab_order_invoice(lab_order)

    return consult_inv


@transaction.atomic
def backfill_all_pending_invoices(patient=None):
    """
    Scans all past and active appointments and lab test orders in the system,
    automatically generating separate pending bills for any that do not have one yet.
    """
    created_count = 0
    # 1. Appointments -> Consultation bills
    active_appts = Appointment.objects.filter(
        is_deleted=False,
    ).exclude(status='CANCELLED').select_related('patient', 'doctor')

    if patient is not None:
        active_appts = active_appts.filter(patient=patient)

    for appt in active_appts:
        if not Invoice.objects.filter(appointment=appt, invoice_type='CONSULTATION', is_deleted=False).exists():
            generate_or_get_consultation_invoice(appt)
            created_count += 1

    # 2. Lab Orders -> Lab test bills
    from .models import LabTestOrder
    active_labs = LabTestOrder.objects.filter(
        is_deleted=False,
    ).select_related('patient', 'doctor', 'appointment')

    if patient is not None:
        active_labs = active_labs.filter(patient=patient)

    for lab in active_labs:
        if not Invoice.objects.filter(lab_order=lab, is_deleted=False).exists():
            generate_or_get_lab_order_invoice(lab)
            created_count += 1

    return created_count


