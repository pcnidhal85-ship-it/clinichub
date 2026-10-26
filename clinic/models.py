from django.db import models


class Department(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    is_deleted = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

class Doctor(models.Model):
    user = models.OneToOneField(
        'users.User',
        on_delete=models.CASCADE,
        related_name='doctor_profile'
    )

    department = models.ForeignKey(
        Department,
        on_delete=models.PROTECT,
        related_name='doctors'
    )

    specialization = models.CharField(max_length=100)
    license_number = models.CharField(max_length=100, unique=True)

    is_active = models.BooleanField(default=True)
    is_deleted = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        first = self.user.first_name.strip()
        if first.lower().startswith("dr."):
            first = first[3:].strip()
        elif first.lower().startswith("dr "):
            first = first[3:].strip()
        return f"Dr. {first} {self.user.last_name}"


class Staff(models.Model):
    user = models.OneToOneField(
        'users.User',
        on_delete=models.CASCADE,
        related_name='staff_profile'
    )

    department = models.ForeignKey(
        Department,
        on_delete=models.PROTECT,
        related_name='staff_members',
        null=True,
        blank=True
    )

    designation = models.CharField(max_length=100)

    is_active = models.BooleanField(default=True)
    is_deleted = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.first_name} {self.user.last_name}"


class Patient(models.Model):
    user = models.OneToOneField(
        'users.User',
        on_delete=models.CASCADE,
        related_name='patient_profile'
    )

    date_of_birth = models.DateField(null=True, blank=True)

    GENDER_CHOICES = [
        ('MALE', 'Male'),
        ('FEMALE', 'Female'),
        ('OTHER', 'Other'),
    ]

    gender = models.CharField(
        max_length=10,
        choices=GENDER_CHOICES,
        blank=True
    )

    blood_group = models.CharField(
        max_length=5,
        blank=True
    )

    address = models.TextField(blank=True)

    emergency_contact_name = models.CharField(
        max_length=100,
        blank=True
    )

    emergency_contact_phone = models.CharField(
        max_length=15,
        blank=True
    )

    is_active = models.BooleanField(default=True)
    is_deleted = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.first_name} {self.user.last_name}"

class DoctorAvailability(models.Model):
    doctor = models.ForeignKey(
        Doctor,
        on_delete=models.CASCADE,
        related_name='availabilities'
    )

    DAY_CHOICES = [
        ('MON', 'Monday'),
        ('TUE', 'Tuesday'),
        ('WED', 'Wednesday'),
        ('THU', 'Thursday'),
        ('FRI', 'Friday'),
        ('SAT', 'Saturday'),
        ('SUN', 'Sunday'),
    ]

    day = models.CharField(
        max_length=3,
        choices=DAY_CHOICES
    )

    start_time = models.TimeField()
    end_time = models.TimeField()

    slot_duration = models.PositiveIntegerField(
        default=15,
        help_text='Duration of each appointment slot in minutes'
    )

    tokens_per_slot = models.PositiveIntegerField(
        default=20,
        help_text='Maximum appointments/tokens per time slot'
    )

    is_active = models.BooleanField(default=True)
    is_deleted = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return (
            f"{self.doctor} - "
            f"{self.get_day_display()} "
            f"{self.start_time} to {self.end_time}"
        )


class Appointment(models.Model):

    STATUS_CHOICES = [
        ('BOOKED', 'Booked'),
        ('CHECKED_IN', 'Checked In'),
        ('WAITING', 'Waiting'),
        ('IN_CONSULTATION', 'In Consultation'),
        ('COMPLETED', 'Completed'),
        ('CANCELLED', 'Cancelled'),
        ('NO_SHOW', 'No Show'),
        ('RESCHEDULED', 'Rescheduled'),
    ]

    patient = models.ForeignKey(
        Patient,
        on_delete=models.PROTECT,
        related_name='appointments'
    )

    doctor = models.ForeignKey(
        Doctor,
        on_delete=models.PROTECT,
        related_name='appointments'
    )

    appointment_date = models.DateField()

    appointment_time = models.TimeField()

    slot_start_time = models.TimeField(null=True, blank=True)
    slot_end_time = models.TimeField(null=True, blank=True)
    time_slot = models.CharField(max_length=50, blank=True)

    status = models.CharField(
        max_length=30,
        choices=STATUS_CHOICES,
        default='BOOKED'
    )

    reason = models.TextField(blank=True)

    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        if not self.slot_start_time and self.appointment_time:
            self.slot_start_time = self.appointment_time.replace(second=0, microsecond=0)
        super().save(*args, **kwargs)

    @property
    def invoice(self):
        """Backwards-compatible accessor for primary consultation invoice or first bill."""
        return self.invoices.filter(is_deleted=False).first()

    def __str__(self):
        slot_str = f" ({self.time_slot})" if self.time_slot else ""
        return (
            f"{self.patient} - "
            f"Dr. {self.doctor.user.first_name} - "
            f"{self.appointment_date} "
            f"{self.appointment_time}{slot_str}"
        )



class DoctorDailyTokenSequence(models.Model):
    doctor = models.ForeignKey(
        Doctor,
        on_delete=models.CASCADE,
        related_name='daily_token_sequences'
    )
    date = models.DateField()
    last_token_number = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['doctor', 'date'],
                name='unique_doctor_daily_sequence'
            )
        ]

    def __str__(self):
        return f"Sequence - Dr. {self.doctor.user.last_name} ({self.date}): #{self.last_token_number:03d}"


class Token(models.Model):

    STATUS_CHOICES = [
        ('BOOKED', 'Booked'),
        ('WAITING', 'Waiting'),
        ('CALLED', 'Called'),
        ('COMPLETED', 'Completed'),
        ('CANCELLED', 'Cancelled'),
    ]

    appointment = models.OneToOneField(
        Appointment,
        on_delete=models.CASCADE,
        related_name='token'
    )

    doctor = models.ForeignKey(
        Doctor,
        on_delete=models.CASCADE,
        related_name='tokens'
    )

    token_number = models.PositiveIntegerField()

    token_date = models.DateField()

    slot_start_time = models.TimeField(null=True, blank=True)
    slot_end_time = models.TimeField(null=True, blank=True)
    time_slot = models.CharField(max_length=50, blank=True)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='BOOKED'
    )

    checked_in_at = models.DateTimeField(
        null=True,
        blank=True
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )
    is_deleted = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['doctor', 'token_date', 'slot_start_time', 'token_number'],
                condition=models.Q(is_deleted=False) & ~models.Q(status='CANCELLED'),
                name='unique_active_token_per_doctor_slot'
            )
        ]

    @property
    def formatted_token_number(self):
        return f"{self.token_number:03d}"

    def clean(self):
        from django.core.exceptions import ValidationError
        super().clean()
        if hasattr(self, 'appointment') and self.appointment and self.doctor_id:
            if self.doctor_id != self.appointment.doctor_id:
                raise ValidationError("Token doctor must match appointment doctor.")

    def save(self, *args, **kwargs):
        if hasattr(self, 'appointment') and self.appointment:
            if not self.doctor_id:
                self.doctor = self.appointment.doctor
            if not self.slot_start_time:
                raw_slot = getattr(self.appointment, 'slot_start_time', None) or self.appointment.appointment_time
                if raw_slot:
                    self.slot_start_time = raw_slot.replace(second=0, microsecond=0)
            if not self.slot_end_time:
                self.slot_end_time = getattr(self.appointment, 'slot_end_time', None)
            if not self.time_slot:
                self.time_slot = getattr(self.appointment, 'time_slot', '')
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        slot_display = f" [{self.time_slot}]" if self.time_slot else ""
        return f"Token #{self.token_number:03d}{slot_display} - Dr. {self.doctor.user.last_name} ({self.appointment.patient})"


# ========================================================
# PHASE 5: MEDICAL RECORDS (EMR LITE) MODELS
# ========================================================

class PatientMedicalHistory(models.Model):
    """
    Long-term clinical background for a patient: allergies, chronic illnesses, surgeries, and family history.
    """
    patient = models.OneToOneField(
        Patient,
        on_delete=models.CASCADE,
        related_name='medical_history'
    )
    allergies = models.TextField(blank=True, help_text="Drug, food, or environmental allergies")
    chronic_conditions = models.TextField(blank=True, help_text="e.g. Hypertension, Diabetes, Asthma, Thyroid")
    past_surgeries = models.TextField(blank=True, help_text="Past surgical procedures & dates")
    family_medical_history = models.TextField(blank=True, help_text="Relevant hereditary or family medical conditions")
    current_medications = models.TextField(blank=True, help_text="Regular daily medications")
    blood_group = models.CharField(max_length=10, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Patient Medical Histories"

    def __str__(self):
        return f"Medical History - {self.patient}"


class PatientVital(models.Model):
    """
    Patient vital signs recorded by staff/nurse or physician prior to or during consultation.
    """
    patient = models.ForeignKey(
        Patient,
        on_delete=models.CASCADE,
        related_name='vitals'
    )
    appointment = models.ForeignKey(
        Appointment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='vitals'
    )
    recorded_by = models.ForeignKey(
        'users.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='recorded_vitals'
    )
    systolic_bp = models.PositiveIntegerField(null=True, blank=True, help_text="Systolic Blood Pressure (mmHg)")
    diastolic_bp = models.PositiveIntegerField(null=True, blank=True, help_text="Diastolic Blood Pressure (mmHg)")
    pulse_rate = models.PositiveIntegerField(null=True, blank=True, help_text="Pulse / Heart Rate (bpm)")
    temperature = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True, help_text="Body Temperature (°F)")
    weight_kg = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, help_text="Weight in kilograms (kg)")
    height_cm = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, help_text="Height in centimeters (cm)")
    spo2 = models.PositiveIntegerField(null=True, blank=True, help_text="Oxygen Saturation (% SpO2)")
    respiratory_rate = models.PositiveIntegerField(null=True, blank=True, help_text="Respiratory Rate (breaths/min)")
    notes = models.CharField(max_length=255, blank=True)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-recorded_at']

    @property
    def blood_pressure_display(self):
        if self.systolic_bp and self.diastolic_bp:
            return f"{self.systolic_bp}/{self.diastolic_bp} mmHg"
        return "-"

    @property
    def bmi(self):
        if self.weight_kg and self.height_cm and self.height_cm > 0:
            height_m = float(self.height_cm) / 100.0
            return round(float(self.weight_kg) / (height_m * height_m), 1)
        return None

    def __str__(self):
        return f"Vitals: {self.patient} ({self.recorded_at.strftime('%Y-%m-%d %H:%M')})"


class MedicalRecord(models.Model):
    """
    Clinical consultation record containing chief complaints, physical exam findings, diagnosis, and treatment plan.
    """
    appointment = models.OneToOneField(
        Appointment,
        on_delete=models.CASCADE,
        related_name='medical_record'
    )
    patient = models.ForeignKey(
        Patient,
        on_delete=models.CASCADE,
        related_name='medical_records'
    )
    doctor = models.ForeignKey(
        Doctor,
        on_delete=models.PROTECT,
        related_name='medical_records'
    )
    chief_complaints = models.TextField(help_text="Primary symptoms or complaints presented by the patient")
    clinical_findings = models.TextField(blank=True, help_text="Clinical examination observations & vital checks")
    diagnosis = models.TextField(help_text="Clinical diagnosis or diagnostic assessment")
    treatment_plan = models.TextField(blank=True, help_text="Therapeutic approach & general recommendations")
    follow_up_date = models.DateField(null=True, blank=True, help_text="Recommended follow-up appointment date")
    is_deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Record #{self.id:04d} - {self.patient} - {self.diagnosis[:30]}"


class Prescription(models.Model):
    """
    Official medical prescription issued for an appointment.
    """
    appointment = models.OneToOneField(
        Appointment,
        on_delete=models.CASCADE,
        related_name='prescription'
    )
    patient = models.ForeignKey(
        Patient,
        on_delete=models.CASCADE,
        related_name='prescriptions'
    )
    doctor = models.ForeignKey(
        Doctor,
        on_delete=models.PROTECT,
        related_name='prescriptions'
    )
    medical_record = models.OneToOneField(
        MedicalRecord,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='prescription'
    )
    general_advice = models.TextField(blank=True, help_text="Dietary, lifestyle, or behavioral instructions")
    follow_up_date = models.DateField(null=True, blank=True, help_text="Next review date")
    is_deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Rx #{self.id:05d} - {self.patient} (Dr. {self.doctor.user.last_name})"


class PrescriptionItem(models.Model):
    """
    Individual medication item prescribed with strength, dosing frequency, and duration.
    """
    prescription = models.ForeignKey(
        Prescription,
        on_delete=models.CASCADE,
        related_name='items'
    )
    medicine_name = models.CharField(max_length=200, help_text="Brand or generic medication name")
    dosage = models.CharField(max_length=100, help_text="e.g. 500mg, 1 tablet, 10ml")
    frequency = models.CharField(
        max_length=100,
        help_text="e.g. 1-0-1, Once daily (OD), Twice daily (BD), TID, SOS"
    )
    duration = models.CharField(max_length=100, help_text="e.g. 5 days, 1 week, 1 month")
    timing = models.CharField(
        max_length=100,
        blank=True,
        help_text="e.g. After meals, Before breakfast, At bedtime"
    )
    instructions = models.CharField(max_length=255, blank=True, help_text="Special instructions (e.g. with warm water)")

    def __str__(self):
        return f"{self.medicine_name} ({self.dosage}) - {self.frequency} x {self.duration}"


class LabTestOrder(models.Model):
    """
    Laboratory diagnostic test or imaging ordered for a patient.
    """
    STATUS_CHOICES = [
        ('ORDERED', 'Ordered'),
        ('SAMPLE_COLLECTED', 'Sample Collected'),
        ('IN_PROGRESS', 'In Progress'),
        ('COMPLETED', 'Completed'),
        ('CANCELLED', 'Cancelled'),
    ]

    appointment = models.ForeignKey(
        Appointment,
        on_delete=models.CASCADE,
        related_name='lab_orders'
    )
    patient = models.ForeignKey(
        Patient,
        on_delete=models.CASCADE,
        related_name='lab_orders'
    )
    doctor = models.ForeignKey(
        Doctor,
        on_delete=models.PROTECT,
        related_name='ordered_lab_tests'
    )
    test_name = models.CharField(max_length=200, help_text="e.g. Complete Blood Count (CBC), Fasting Blood Sugar")
    clinical_indication = models.CharField(max_length=255, blank=True, help_text="Clinical justification / reason")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='ORDERED')
    results_summary = models.TextField(blank=True, help_text="Lab findings or test report summary")
    ordered_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    is_deleted = models.BooleanField(default=False)

    class Meta:
        ordering = ['-ordered_at']

    def __str__(self):
        return f"Lab: {self.test_name} - {self.patient} [{self.get_status_display()}]"


# ========================================================
# PHASE 6: BILLING & PAYMENTS MODELS
# ========================================================

class BillableService(models.Model):
    """
    Catalog of standard services, consultation charges, tests, and clinical procedures with base pricing.
    """
    SERVICE_CATEGORY_CHOICES = [
        ('CONSULTATION', 'Doctor Consultation'),
        ('LAB_TEST', 'Diagnostic / Lab Investigation'),
        ('PROCEDURE', 'Clinical Procedure / Dressing'),
        ('NURSING', 'Nursing / Care Service'),
        ('OTHER', 'General Service / Facility Fee'),
    ]

    name = models.CharField(max_length=200, unique=True, help_text="e.g. General Consultation, CBC Test, ECG")
    category = models.CharField(max_length=30, choices=SERVICE_CATEGORY_CHOICES, default='CONSULTATION')
    department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='billable_services'
    )
    doctor = models.ForeignKey(
        Doctor,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='custom_services',
        help_text="Optional custom consultation fee for a specific physician"
    )
    code = models.CharField(max_length=50, blank=True, help_text="CPT or internal billing code")
    base_fee = models.DecimalField(max_digits=10, decimal_places=2, help_text="Base price in currency units")
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    is_deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['category', 'name']

    def __str__(self):
        return f"{self.name} (${self.base_fee:.2f})"


class Invoice(models.Model):
    """
    Itemized patient billing invoice linked to an appointment, walk-in, consultation, or diagnostic lab order.
    Supports separate bills for consultations, lab investigations, procedures, etc.
    """
    STATUS_CHOICES = [
        ('UNPAID', 'Unpaid'),
        ('PARTIAL', 'Partially Paid'),
        ('PAID', 'Paid in Full'),
        ('CANCELLED', 'Cancelled'),
    ]

    INVOICE_TYPE_CHOICES = [
        ('CONSULTATION', 'Doctor Consultation Fee'),
        ('LAB_TEST', 'Diagnostic Lab Test Fee'),
        ('PROCEDURE', 'Clinical Procedure Fee'),
        ('PHARMACY', 'Prescription / Medication Fee'),
        ('GENERAL', 'General / Other Facility Fee'),
    ]

    invoice_number = models.CharField(max_length=32, unique=True)
    invoice_type = models.CharField(
        max_length=30,
        choices=INVOICE_TYPE_CHOICES,
        default='CONSULTATION',
        help_text="Categorization for separate billing: Consultation, Lab Test, Procedure, etc."
    )
    patient = models.ForeignKey(
        Patient,
        on_delete=models.CASCADE,
        related_name='invoices'
    )
    appointment = models.ForeignKey(
        Appointment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='invoices'
    )
    lab_order = models.ForeignKey(
        'LabTestOrder',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='invoices'
    )
    doctor = models.ForeignKey(
        Doctor,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='invoices'
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='UNPAID')
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0.00, help_text="Tax percentage (e.g. 5.0 for 5%)")
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    amount_due = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    notes = models.TextField(blank=True, help_text="Billing terms, insurance details, or notes")
    due_date = models.DateField(null=True, blank=True)
    issued_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    is_deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-issued_at']

    @property
    def is_fully_paid(self):
        return self.status == 'PAID' or (self.total_amount > 0 and self.amount_paid >= self.total_amount)

    @property
    def balance(self):
        from decimal import Decimal
        return max(self.total_amount - self.amount_paid, Decimal("0.00"))

    def calculate_totals(self):
        """
        Recalculates subtotal, tax amount, total amount, amount paid, and amount due from line items and payments.
        """
        from decimal import Decimal
        if hasattr(self, '_prefetched_objects_cache'):
            self._prefetched_objects_cache.clear()
        sub = sum((item.total_price for item in self.items.all()), Decimal("0.00"))
        self.subtotal = sub
        disc = self.discount or Decimal("0.00")
        taxable = max(sub - disc, Decimal("0.00"))
        if self.tax_rate and self.tax_rate > Decimal("0.00"):
            self.tax_amount = round(taxable * (self.tax_rate / Decimal("100.0")), 2)
        else:
            self.tax_amount = Decimal("0.00")
        self.total_amount = max(taxable + self.tax_amount, Decimal("0.00"))

        paid = sum((p.amount for p in self.payments.all()), Decimal("0.00"))
        self.amount_paid = paid
        self.amount_due = max(self.total_amount - paid, Decimal("0.00"))

        if self.total_amount > Decimal("0.00") and paid >= self.total_amount:
            self.status = 'PAID'
            if not self.paid_at:
                from django.utils import timezone
                self.paid_at = timezone.now()
        elif paid > Decimal("0.00"):
            self.status = 'PARTIAL'
        elif self.status != 'CANCELLED':
            self.status = 'UNPAID'

    def __str__(self):
        return f"{self.invoice_number} - {self.patient} (${self.total_amount:.2f}) [{self.get_status_display()}]"


class InvoiceLineItem(models.Model):
    """
    Individual billable line item within an invoice.
    """
    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name='items'
    )
    service = models.ForeignKey(
        BillableService,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    description = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    total_price = models.DecimalField(max_digits=10, decimal_places=2)

    def save(self, *args, **kwargs):
        from decimal import Decimal
        self.total_price = Decimal(str(self.unit_price)) * Decimal(str(self.quantity))
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.description} x {self.quantity} = ${self.total_price:.2f}"


class Payment(models.Model):
    """
    Payment transaction recording settlement against an invoice.
    """
    PAYMENT_METHOD_CHOICES = [
        ('CASH', 'Cash'),
        ('CARD', 'Credit / Debit Card'),
        ('UPI', 'UPI / QR Code'),
        ('NET_BANKING', 'Net Banking / Wire'),
        ('INSURANCE', 'Health Insurance'),
    ]

    payment_number = models.CharField(max_length=32, unique=True)
    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name='payments'
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.CharField(
        max_length=20,
        choices=PAYMENT_METHOD_CHOICES,
        default='CASH'
    )
    transaction_reference = models.CharField(
        max_length=100,
        blank=True,
        help_text="Card authorization #, UPI transaction ID, or bank reference"
    )
    recorded_by = models.ForeignKey(
        'users.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='recorded_payments'
    )
    payment_date = models.DateTimeField(auto_now_add=True)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-payment_date']

    def __str__(self):
        return f"{self.payment_number}: ${self.amount:.2f} via {self.get_payment_method_display()} for {self.invoice.invoice_number}"

