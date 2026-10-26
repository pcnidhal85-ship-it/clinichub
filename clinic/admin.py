from django.contrib import admin
from .models import (
    Department,
    Doctor,
    Staff,
    Patient,
    DoctorAvailability,
    Appointment,
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
from .forms import DoctorForm, StaffForm



@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active', 'created_at', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('name',)


@admin.register(Doctor)
class DoctorAdmin(admin.ModelAdmin):
    form = DoctorForm

    list_display = (
        'get_doctor_name',
        'get_email',
        'department',
        'specialization',
        'license_number',
        'is_active',
    )

    list_filter = ('department', 'is_active', 'specialization')

    search_fields = (
        'user__first_name',
        'user__last_name',
        'user__email',
        'license_number',
    )

    @admin.display(description='Doctor')
    def get_doctor_name(self, obj):
        return f"Dr. {obj.user.first_name} {obj.user.last_name}"

    @admin.display(description='Email')
    def get_email(self, obj):
        return obj.user.email


@admin.register(Staff)
class StaffAdmin(admin.ModelAdmin):
    form = StaffForm

    list_display = (
        'get_staff_name',
        'department',
        'designation',
        'is_active',
    )

    list_filter = (
        'department',
        'is_active',
        'designation',
    )

    search_fields = (
        'user__first_name',
        'user__last_name',
        'user__email',
    )

    @admin.display(description='Staff')
    def get_staff_name(self, obj):
        return f"{obj.user.first_name} {obj.user.last_name}"



@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = (
        'get_patient_name',
        'get_email',
        'gender',
        'blood_group',
        'is_active',
        'created_at',
    )

    list_filter = (
        'gender',
        'blood_group',
        'is_active',
    )

    search_fields = (
        'user__first_name',
        'user__last_name',
        'user__email',
        'user__phone_number',
    )

    @admin.display(description='Patient')
    def get_patient_name(self, obj):
        return f"{obj.user.first_name} {obj.user.last_name}"

    @admin.display(description='Email')
    def get_email(self, obj):
        return obj.user.email

@admin.register(DoctorAvailability)
class DoctorAvailabilityAdmin(admin.ModelAdmin):
    list_display = (
        'doctor',
        'day',
        'start_time',
        'end_time',
        'slot_duration',
        'is_active',
    )

    list_filter = (
        'day',
        'is_active',
        'doctor',
    )

    search_fields = (
        'doctor__user__first_name',
        'doctor__user__last_name',
        'doctor__user__email',
    )


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):

    list_display = (
        'patient',
        'doctor',
        'appointment_date',
        'appointment_time',
        'status',
    )

    list_filter = (
        'doctor',
        'status',
        'appointment_date',
    )

    search_fields = (
        'patient__user__first_name',
        'patient__user__last_name',
        'patient__user__email',
        'doctor__user__first_name',
        'doctor__user__last_name',
    )

    ordering = (
        'appointment_date',
        'appointment_time',
    )


@admin.register(PatientMedicalHistory)
class PatientMedicalHistoryAdmin(admin.ModelAdmin):
    list_display = ('patient', 'blood_group', 'updated_at')
    search_fields = ('patient__user__first_name', 'patient__user__last_name', 'allergies', 'chronic_conditions')


@admin.register(PatientVital)
class PatientVitalAdmin(admin.ModelAdmin):
    list_display = (
        'patient',
        'blood_pressure_display',
        'pulse_rate',
        'temperature',
        'weight_kg',
        'spo2',
        'recorded_at',
    )
    list_filter = ('recorded_at',)
    search_fields = ('patient__user__first_name', 'patient__user__last_name')


@admin.register(MedicalRecord)
class MedicalRecordAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'patient',
        'doctor',
        'diagnosis',
        'follow_up_date',
        'created_at',
    )
    list_filter = ('created_at', 'doctor')
    search_fields = (
        'patient__user__first_name',
        'patient__user__last_name',
        'diagnosis',
        'chief_complaints',
    )


class PrescriptionItemInline(admin.TabularInline):
    model = PrescriptionItem
    extra = 1


@admin.register(Prescription)
class PrescriptionAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'patient',
        'doctor',
        'follow_up_date',
        'created_at',
    )
    list_filter = ('created_at', 'doctor')
    search_fields = (
        'patient__user__first_name',
        'patient__user__last_name',
        'doctor__user__first_name',
        'doctor__user__last_name',
    )
    inlines = [PrescriptionItemInline]


@admin.register(LabTestOrder)
class LabTestOrderAdmin(admin.ModelAdmin):
    list_display = (
        'test_name',
        'patient',
        'doctor',
        'status',
        'ordered_at',
        'completed_at',
    )
    list_filter = ('status', 'ordered_at', 'doctor')
    search_fields = (
        'test_name',
        'patient__user__first_name',
        'patient__user__last_name',
    )


@admin.register(BillableService)
class BillableServiceAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'code', 'base_fee', 'department', 'doctor', 'is_active')
    list_filter = ('category', 'is_active', 'department')
    search_fields = ('name', 'code')


class InvoiceLineItemInline(admin.TabularInline):
    model = InvoiceLineItem
    extra = 1


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    readonly_fields = ('payment_number', 'payment_date')


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = (
        'invoice_number',
        'patient',
        'doctor',
        'total_amount',
        'amount_paid',
        'amount_due',
        'status',
        'issued_at',
    )
    list_filter = ('status', 'issued_at')
    search_fields = (
        'invoice_number',
        'patient__user__first_name',
        'patient__user__last_name',
    )
    inlines = [InvoiceLineItemInline, PaymentInline]


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        'payment_number',
        'invoice',
        'amount',
        'payment_method',
        'transaction_reference',
        'payment_date',
    )
    list_filter = ('payment_method', 'payment_date')
    search_fields = ('payment_number', 'invoice__invoice_number', 'transaction_reference')
