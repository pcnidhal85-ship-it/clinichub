
import uuid
from django import forms
from django.db import transaction

from users.models import User
from .models import (
    Doctor,
    Staff,
    Appointment,
    Department,
    DoctorAvailability,
    Patient,
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


# --------------------------------
# DOCTOR FORM
# --------------------------------

class DoctorForm(forms.ModelForm):
    existing_user = forms.ModelChoiceField(
        queryset=User.objects.none(),
        required=False,
        label="Link Existing Doctor User",
        help_text="Select an existing doctor user without a profile, or leave empty to create a new user account below.",
    )
    email = forms.EmailField(required=False)
    username = forms.CharField(max_length=100, required=False)
    password = forms.CharField(
        widget=forms.PasswordInput,
        required=False,
        help_text="Required when creating a new user account.",
    )
    first_name = forms.CharField(max_length=100, required=False)
    last_name = forms.CharField(max_length=100, required=False)
    phone_number = forms.CharField(max_length=15, required=False)

    doctor_role = forms.ChoiceField(
        choices=User.DOCTOR_ROLE_CHOICES,
        required=False,
    )

    class Meta:
        model = Doctor
        fields = [
            "existing_user",
            "email",
            "username",
            "password",
            "first_name",
            "last_name",
            "phone_number",
            "doctor_role",
            "department",
            "specialization",
            "license_number",
            "is_active",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        unlinked_qs = User.objects.filter(
            user_type='DOCTOR',
            doctor_profile__isnull=True,
        ).order_by('username')

        if self.instance and self.instance.pk and hasattr(self.instance, 'user') and self.instance.user:
            self.fields['existing_user'].queryset = User.objects.filter(pk=self.instance.user.pk)
            self.fields['existing_user'].initial = self.instance.user
            self.fields['existing_user'].disabled = True
            self.fields['email'].initial = self.instance.user.email
            self.fields['username'].initial = self.instance.user.username
            self.fields['first_name'].initial = self.instance.user.first_name
            self.fields['last_name'].initial = self.instance.user.last_name
            self.fields['phone_number'].initial = self.instance.user.phone_number
            self.fields['doctor_role'].initial = self.instance.user.doctor_role
            self.fields['password'].help_text = "Leave blank to keep current password."
        else:
            self.fields['existing_user'].queryset = unlinked_qs

    def clean(self):
        cleaned_data = super().clean()
        existing_user = cleaned_data.get('existing_user')
        is_editing = bool(self.instance and self.instance.pk)

        if is_editing:
            return cleaned_data

        if existing_user:
            if existing_user.user_type != 'DOCTOR':
                self.add_error('existing_user', 'Selected user must have user_type="DOCTOR".')
            if Doctor.objects.filter(user=existing_user, is_deleted=False).exists():
                self.add_error('existing_user', 'This user already has an active Doctor profile.')
        else:
            email = cleaned_data.get('email')
            username = cleaned_data.get('username')
            password = cleaned_data.get('password')
            first_name = cleaned_data.get('first_name')
            last_name = cleaned_data.get('last_name')

            if not email:
                self.add_error('email', 'Email is required when creating a new user account.')
            elif User.objects.filter(email=email).exists():
                self.add_error('email', 'A user with this email already exists. Link them above if they are a doctor.')

            if not username:
                self.add_error('username', 'Username is required when creating a new user account.')
            elif User.objects.filter(username=username).exists():
                self.add_error('username', 'A user with this username already exists.')

            if not password:
                self.add_error('password', 'Password is required when creating a new user account.')

            if not first_name:
                self.add_error('first_name', 'First name is required.')

            if not last_name:
                self.add_error('last_name', 'Last name is required.')

        return cleaned_data

    @transaction.atomic
    def save(self, commit=True):
        doctor = super().save(commit=False)
        existing_user = self.cleaned_data.get('existing_user')
        is_editing = bool(self.instance and self.instance.pk)

        if is_editing:
            user = self.instance.user
            if self.cleaned_data.get('first_name'):
                user.first_name = self.cleaned_data['first_name']
            if self.cleaned_data.get('last_name'):
                user.last_name = self.cleaned_data['last_name']
            if 'phone_number' in self.cleaned_data:
                user.phone_number = self.cleaned_data['phone_number']
            if self.cleaned_data.get('doctor_role'):
                user.doctor_role = self.cleaned_data['doctor_role']
            if self.cleaned_data.get('password'):
                user.set_password(self.cleaned_data['password'])
            user.save()
            doctor.user = user
        elif existing_user:
            user = existing_user
            if self.cleaned_data.get('first_name'):
                user.first_name = self.cleaned_data['first_name']
            if self.cleaned_data.get('last_name'):
                user.last_name = self.cleaned_data['last_name']
            if self.cleaned_data.get('phone_number'):
                user.phone_number = self.cleaned_data['phone_number']
            if self.cleaned_data.get('doctor_role'):
                user.doctor_role = self.cleaned_data['doctor_role']
            user.save()
            doctor.user = user
        else:
            user = User.objects.create_user(
                email=self.cleaned_data["email"],
                username=self.cleaned_data["username"],
                password=self.cleaned_data["password"],
                first_name=self.cleaned_data["first_name"],
                last_name=self.cleaned_data["last_name"],
                phone_number=self.cleaned_data.get("phone_number"),
                user_type="DOCTOR",
                doctor_role=self.cleaned_data.get("doctor_role") or "GENERAL",
            )
            doctor.user = user

        if commit:
            doctor.save()

        return doctor


# --------------------------------
# STAFF FORM
# --------------------------------

class StaffForm(forms.ModelForm):
    email = forms.EmailField()
    username = forms.CharField(max_length=100)
    password = forms.CharField(widget=forms.PasswordInput)
    first_name = forms.CharField(max_length=100)
    last_name = forms.CharField(max_length=100)
    phone_number = forms.CharField(max_length=15, required=False)

    staff_role = forms.ChoiceField(
        choices=User.STAFF_ROLE_CHOICES
    )

    class Meta:
        model = Staff
        fields = [
            "email",
            "username",
            "password",
            "first_name",
            "last_name",
            "phone_number",
            "staff_role",
            "department",
            "designation",
            "is_active",
        ]

    @transaction.atomic
    def save(self, commit=True):
        staff = super().save(commit=False)

        user = User.objects.create_user(
            email=self.cleaned_data["email"],
            username=self.cleaned_data["username"],
            password=self.cleaned_data["password"],
            first_name=self.cleaned_data["first_name"],
            last_name=self.cleaned_data["last_name"],
            phone_number=self.cleaned_data["phone_number"],
            user_type="STAFF",
            staff_role=self.cleaned_data["staff_role"],
        )

        staff.user = user

        if commit:
            staff.save()

        return staff


# --------------------------------
# APPOINTMENT FORM
# --------------------------------

class AppointmentForm(forms.ModelForm):

    department = forms.ModelChoiceField(
        queryset=Department.objects.filter(
            is_active=True,
            is_deleted=False,
        ),
        empty_label="Select Department",
    )

    slot_start_time = forms.TimeField(
        required=False,
        widget=forms.HiddenInput(attrs={"id": "id_slot_start_time"}),
    )
    slot_end_time = forms.TimeField(
        required=False,
        widget=forms.HiddenInput(attrs={"id": "id_slot_end_time"}),
    )
    time_slot = forms.CharField(
        max_length=50,
        required=False,
        widget=forms.HiddenInput(attrs={"id": "id_time_slot"}),
    )
    token_number = forms.IntegerField(
        min_value=1,
        required=False,
        widget=forms.HiddenInput(attrs={"id": "id_token_number"}),
    )

    class Meta:
        model = Appointment
        fields = [
            "department",
            "doctor",
            "appointment_date",
            "appointment_time",
            "slot_start_time",
            "slot_end_time",
            "time_slot",
            "reason",
        ]

        widgets = {
            "appointment_date": forms.DateInput(
                attrs={"type": "date", "class": "form-control"}
            ),
            "appointment_time": forms.TimeInput(
                attrs={"type": "time", "class": "form-control"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["doctor"].queryset = Doctor.objects.none()
        # Make appointment_time optional since slot_start_time can provide it
        self.fields["appointment_time"].required = False

        if self.is_bound:
            department_id = self.data.get("department")

            if department_id:
                try:
                    department_id = int(department_id)

                    self.fields["doctor"].queryset = Doctor.objects.filter(
                        department_id=department_id,
                        department__is_active=True,
                        department__is_deleted=False,
                        is_active=True,
                        is_deleted=False,
                        user__is_active=True,
                        user__is_delete=False,
                    ).order_by("user__first_name")

                except (ValueError, TypeError):
                    pass
        elif self.instance and self.instance.pk and hasattr(self.instance, "doctor") and self.instance.doctor:
            self.fields["department"].initial = self.instance.doctor.department
            self.fields["doctor"].queryset = Doctor.objects.filter(
                department=self.instance.doctor.department,
                department__is_active=True,
                department__is_deleted=False,
                is_active=True,
                is_deleted=False,
                user__is_active=True,
                user__is_delete=False,
            ).order_by("user__first_name")

    def clean_appointment_date(self):
        date = self.cleaned_data.get("appointment_date")
        from django.utils import timezone
        if date and date < timezone.localdate():
            raise forms.ValidationError("Appointment date cannot be in the past.")
        return date

    def clean(self):
        cleaned_data = super().clean()
        doctor = cleaned_data.get("doctor")
        department = cleaned_data.get("department")
        slot_start = cleaned_data.get("slot_start_time")
        slot_end = cleaned_data.get("slot_end_time")
        appt_time = cleaned_data.get("appointment_time")
        time_slot = cleaned_data.get("time_slot")
        token_num = cleaned_data.get("token_number")

        # Validate doctor belongs to selected department
        if doctor and department and doctor.department_id != department.id:
            self.add_error("doctor", "Selected doctor does not belong to the selected department.")

        # Ensure appointment_time is set
        if not appt_time and slot_start:
            cleaned_data["appointment_time"] = slot_start
            appt_time = slot_start

        if not appt_time:
            self.add_error("appointment_time", "Please select an appointment time slot.")

        # Ensure time_slot string is set
        if not time_slot and slot_start and slot_end:
            cleaned_data["time_slot"] = f"{slot_start.strftime('%I:%M %p')} – {slot_end.strftime('%I:%M %p')}"

        return cleaned_data


# --------------------------------
# CANCEL APPOINTMENT FORM
# --------------------------------

class CancelAppointmentForm(forms.Form):
    CANCELLATION_REASONS = [
        ("Schedule conflict / timing issue", "Schedule conflict / timing issue"),
        ("Symptoms resolved / feeling better", "Symptoms resolved / feeling better"),
        ("Need to see a different specialist", "Need to see a different specialist"),
        ("Personal emergency", "Personal emergency"),
        ("Patient requested cancellation (phone/in-person)", "Patient requested cancellation (phone/in-person)"),
        ("Doctor unavailable / clinical emergency", "Doctor unavailable / clinical emergency"),
        ("Patient no-show / departed queue", "Patient no-show / departed queue"),
        ("Other reason", "Other reason"),
    ]

    cancellation_reason = forms.ChoiceField(
        choices=CANCELLATION_REASONS,
        widget=forms.Select(attrs={"class": "form-select"}),
        label="Reason for Cancellation",
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "class": "form-control",
            "rows": 3,
            "placeholder": "Additional comments or details (optional)..."
        }),
        label="Additional Details (Optional)",
    )


# --------------------------------
# CONSULTATION NOTES FORM
# --------------------------------

class ConsultationNotesForm(forms.ModelForm):
    class Meta:
        model = Appointment
        fields = ["notes"]
        widgets = {
            "notes": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 6,
                    "placeholder": "Enter clinical observations, diagnosis, treatment prescribed, and follow-up advice...",
                    "required": True,
                }
            )
        }


# --------------------------------
# DOCTOR AVAILABILITY FORM
# --------------------------------

class DoctorAvailabilityForm(forms.ModelForm):
    tokens_per_slot = forms.IntegerField(
        min_value=1,
        max_value=100,
        initial=20,
        required=False,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": 1, "max": 100}),
    )

    class Meta:
        model = DoctorAvailability
        fields = [
            "day",
            "start_time",
            "end_time",
            "slot_duration",
            "tokens_per_slot",
            "is_active",
        ]
        widgets = {
            "start_time": forms.TimeInput(attrs={"type": "time", "class": "form-control"}),
            "end_time": forms.TimeInput(attrs={"type": "time", "class": "form-control"}),
            "slot_duration": forms.NumberInput(attrs={"class": "form-control", "min": 5, "max": 180}),
            "day": forms.Select(attrs={"class": "form-select"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def clean(self):
        cleaned_data = super().clean()
        start_time = cleaned_data.get("start_time")
        end_time = cleaned_data.get("end_time")

        if start_time and end_time and start_time >= end_time:
            raise forms.ValidationError("End time must be later than start time.")

        if not cleaned_data.get("tokens_per_slot"):
            cleaned_data["tokens_per_slot"] = 20

        return cleaned_data


# --------------------------------
# WALK-IN APPOINTMENT FORM
# --------------------------------

class WalkInAppointmentForm(forms.Form):
    patient_mode = forms.ChoiceField(
        choices=[
            ("EXISTING", "Existing Registered Patient"),
            ("NEW", "New Walk-In Patient Registration"),
        ],
        initial="EXISTING",
        widget=forms.RadioSelect(attrs={"class": "patient-mode-radio"}),
        label="Patient Type",
    )

    # Existing patient
    existing_patient = forms.ModelChoiceField(
        queryset=Patient.objects.none(),
        required=False,
        empty_label="Select Registered Patient",
        label="Existing Patient",
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    # New walk-in patient fields
    first_name = forms.CharField(
        max_length=100,
        required=False,
        label="First Name",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "First name"}),
    )
    last_name = forms.CharField(
        max_length=100,
        required=False,
        label="Last Name",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Last name"}),
    )
    phone_number = forms.CharField(
        max_length=15,
        required=False,
        label="Phone Number",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Contact number"}),
    )
    gender = forms.ChoiceField(
        choices=[("", "Select Gender")] + Patient.GENDER_CHOICES,
        required=False,
        label="Gender",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    blood_group = forms.CharField(
        max_length=5,
        required=False,
        label="Blood Group",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. O+, A+, B+"}),
    )

    # Consultation fields
    department = forms.ModelChoiceField(
        queryset=Department.objects.filter(is_active=True, is_deleted=False),
        empty_label="Select Department",
        label="Department",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    doctor = forms.ModelChoiceField(
        queryset=Doctor.objects.none(),
        empty_label="Select Attending Doctor",
        label="Attending Doctor",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    reason = forms.CharField(
        required=False,
        label="Reason for Consultation / Symptoms",
        widget=forms.Textarea(attrs={
            "class": "form-control",
            "rows": 3,
            "placeholder": "Brief description of walk-in patient complaints or symptoms...",
        }),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["existing_patient"].queryset = Patient.objects.filter(
            is_active=True,
            is_deleted=False,
            user__is_active=True,
            user__is_delete=False,
        ).select_related("user").order_by("user__first_name", "user__last_name")

        self.fields["doctor"].queryset = Doctor.objects.none()

        if self.is_bound:
            dept_id = self.data.get("department")
            if dept_id:
                try:
                    dept_id = int(dept_id)
                    self.fields["doctor"].queryset = Doctor.objects.filter(
                        department_id=dept_id,
                        department__is_active=True,
                        department__is_deleted=False,
                        is_active=True,
                        is_deleted=False,
                        user__is_active=True,
                        user__is_delete=False,
                    ).order_by("user__first_name")
                except (ValueError, TypeError):
                    pass
        elif self.initial.get("department"):
            self.fields["doctor"].queryset = Doctor.objects.filter(
                department=self.initial.get("department"),
                is_active=True,
                is_deleted=False,
                user__is_active=True,
                user__is_delete=False,
            ).order_by("user__first_name")

    def clean(self):
        cleaned_data = super().clean()
        mode = cleaned_data.get("patient_mode")

        if mode == "EXISTING":
            if not cleaned_data.get("existing_patient"):
                self.add_error("existing_patient", "Please select a registered patient.")
        elif mode == "NEW":
            if not cleaned_data.get("first_name"):
                self.add_error("first_name", "First name is required for new walk-in patient.")
            if not cleaned_data.get("last_name"):
                self.add_error("last_name", "Last name is required for new walk-in patient.")

        # Validate doctor belongs to selected department
        dept = cleaned_data.get("department")
        doc = cleaned_data.get("doctor")
        if doc and dept and doc.department_id != dept.id:
            self.add_error("doctor", "Selected doctor does not belong to the selected department.")

        return cleaned_data


# --------------------------------
# STAFF PATIENT REGISTRATION FORM
# --------------------------------

class StaffPatientRegistrationForm(forms.Form):
    first_name = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "First name", "required": True}),
        label="First Name",
    )
    last_name = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Last name", "required": True}),
        label="Last Name",
    )
    phone_number = forms.CharField(
        max_length=15,
        required=True,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Mobile / Contact number", "required": True}),
        label="Phone Number",
    )
    email = forms.EmailField(
        required=False,
        widget=forms.EmailInput(attrs={"class": "form-control", "placeholder": "patient@example.com (optional)"}),
        label="Email Address",
        help_text="Optional. A unique clinic account will be auto-generated if omitted.",
    )
    date_of_birth = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
        label="Date of Birth",
    )
    gender = forms.ChoiceField(
        choices=[("", "Select Gender")] + Patient.GENDER_CHOICES,
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
        label="Gender",
    )
    blood_group = forms.CharField(
        max_length=5,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. O+, A+, B+, AB-"}),
        label="Blood Group",
    )
    emergency_contact_name = forms.CharField(
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Contact person name"}),
        label="Emergency Contact Name",
    )
    emergency_contact_phone = forms.CharField(
        max_length=15,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Emergency phone number"}),
        label="Emergency Contact Phone",
    )
    address = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "Residential address / city..."}),
        label="Address",
    )

    def clean_email(self):
        email = self.cleaned_data.get("email", "").strip().lower()
        if email and User.objects.filter(email=email).exists():
            raise forms.ValidationError("A user account with this email address already exists.")
        return email

    @transaction.atomic
    def save(self):
        first_name = self.cleaned_data["first_name"].strip()
        last_name = self.cleaned_data["last_name"].strip()
        phone_number = self.cleaned_data.get("phone_number", "").strip()
        email = self.cleaned_data.get("email", "").strip().lower()

        clean_phone = "".join(filter(str.isalnum, phone_number))
        base_username = f"patient_{clean_phone}" if clean_phone else f"patient_{uuid.uuid4().hex[:8]}"
        username = base_username
        counter = 1
        while User.objects.filter(username=username).exists():
            username = f"{base_username}_{counter}"
            counter += 1

        if not email:
            email = f"{username}@patient.clinichub.local"

        temp_password = uuid.uuid4().hex

        user = User.objects.create_user(
            username=username,
            email=email,
            password=temp_password,
            first_name=first_name,
            last_name=last_name,
            phone_number=phone_number,
            user_type="PATIENT",
        )
        user.is_email_verified = True
        user.save(update_fields=["is_email_verified"])

        patient = user.patient_profile
        patient.date_of_birth = self.cleaned_data.get("date_of_birth")
        patient.gender = self.cleaned_data.get("gender") or ""
        patient.blood_group = self.cleaned_data.get("blood_group", "").strip().upper()
        patient.emergency_contact_name = self.cleaned_data.get("emergency_contact_name", "").strip()
        patient.emergency_contact_phone = self.cleaned_data.get("emergency_contact_phone", "").strip()
        patient.address = self.cleaned_data.get("address", "").strip()
        patient.save()

        return patient


# --------------------------------
# PHASE 4: ADMIN MANAGEMENT FORMS
# --------------------------------

class DepartmentForm(forms.ModelForm):
    class Meta:
        model = Department
        fields = ["name", "description", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Cardiology"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Overview of department clinical scope..."}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        qs = Department.objects.filter(name__iexact=name, is_deleted=False)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("A department with this name already exists.")
        return name


class AdminDoctorForm(forms.Form):
    first_name = forms.CharField(max_length=100, widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "First Name"}))
    last_name = forms.CharField(max_length=100, widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Last Name"}))
    email = forms.EmailField(widget=forms.EmailInput(attrs={"class": "form-control", "placeholder": "doctor@clinichub.com"}))
    username = forms.CharField(max_length=100, required=False, widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Auto-generated if left blank"}))
    phone_number = forms.CharField(max_length=15, required=False, widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. +1 555-0144"}))
    password = forms.CharField(required=False, widget=forms.PasswordInput(attrs={"class": "form-control", "placeholder": "Password (leave blank to keep existing)"}))
    department = forms.ModelChoiceField(queryset=Department.objects.filter(is_deleted=False).order_by("name"), widget=forms.Select(attrs={"class": "form-select"}))
    specialization = forms.CharField(max_length=100, widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Pediatric Cardiology"}))
    doctor_role = forms.ChoiceField(choices=User.DOCTOR_ROLE_CHOICES, widget=forms.Select(attrs={"class": "form-select"}))
    license_number = forms.CharField(max_length=100, widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. MED-83941"}))
    is_active = forms.BooleanField(required=False, initial=True, widget=forms.CheckboxInput(attrs={"class": "form-check-input"}))

    def __init__(self, *args, doctor=None, **kwargs):
        self.doctor = doctor
        super().__init__(*args, **kwargs)
        if self.doctor:
            user = self.doctor.user
            self.fields["first_name"].initial = user.first_name
            self.fields["last_name"].initial = user.last_name
            self.fields["email"].initial = user.email
            self.fields["username"].initial = user.username
            self.fields["phone_number"].initial = user.phone_number
            self.fields["department"].initial = self.doctor.department
            self.fields["specialization"].initial = self.doctor.specialization
            self.fields["doctor_role"].initial = user.doctor_role
            self.fields["license_number"].initial = self.doctor.license_number
            self.fields["is_active"].initial = self.doctor.is_active and user.is_active
        else:
            self.fields["password"].required = True
            self.fields["password"].widget.attrs["placeholder"] = "Temporary login password"

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        qs = User.objects.filter(email=email)
        if self.doctor:
            qs = qs.exclude(pk=self.doctor.user.pk)
        if qs.exists():
            raise forms.ValidationError("A user with this email address already exists.")
        return email

    def clean_license_number(self):
        license_number = self.cleaned_data["license_number"].strip().upper()
        qs = Doctor.objects.filter(license_number=license_number, is_deleted=False)
        if self.doctor:
            qs = qs.exclude(pk=self.doctor.pk)
        if qs.exists():
            raise forms.ValidationError("A doctor with this medical license number already exists.")
        return license_number

    @transaction.atomic
    def save(self):
        data = self.cleaned_data
        is_active = data.get("is_active", True)
        if self.doctor:
            user = self.doctor.user
            user.first_name = data["first_name"].strip()
            user.last_name = data["last_name"].strip()
            user.email = data["email"]
            if data.get("username"):
                user.username = data["username"].strip()
            user.phone_number = data.get("phone_number", "").strip()
            user.doctor_role = data["doctor_role"]
            user.is_active = is_active
            if data.get("password"):
                user.set_password(data["password"])
            user.save()

            doctor = self.doctor
            doctor.department = data["department"]
            doctor.specialization = data["specialization"].strip()
            doctor.license_number = data["license_number"]
            doctor.is_active = is_active
            doctor.save()
            return doctor
        else:
            username = data.get("username", "").strip()
            if not username:
                base_username = f"dr_{data['last_name'].lower().replace(' ', '')}"
                username = base_username
                c = 1
                while User.objects.filter(username=username).exists():
                    username = f"{base_username}_{c}"
                    c += 1

            user = User.objects.create_user(
                username=username,
                email=data["email"],
                password=data["password"],
                first_name=data["first_name"].strip(),
                last_name=data["last_name"].strip(),
                phone_number=data.get("phone_number", "").strip(),
                user_type="DOCTOR",
            )
            user.doctor_role = data["doctor_role"]
            user.is_active = is_active
            user.is_email_verified = True
            user.save()

            doctor = Doctor.objects.create(
                user=user,
                department=data["department"],
                specialization=data["specialization"].strip(),
                license_number=data["license_number"],
                is_active=is_active,
            )
            return doctor


class AdminStaffForm(forms.Form):
    first_name = forms.CharField(max_length=100, widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "First Name"}))
    last_name = forms.CharField(max_length=100, widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Last Name"}))
    email = forms.EmailField(widget=forms.EmailInput(attrs={"class": "form-control", "placeholder": "staff@clinichub.com"}))
    username = forms.CharField(max_length=100, required=False, widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Auto-generated if left blank"}))
    phone_number = forms.CharField(max_length=15, required=False, widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. +1 555-0188"}))
    password = forms.CharField(required=False, widget=forms.PasswordInput(attrs={"class": "form-control", "placeholder": "Password (leave blank to keep existing)"}))
    department = forms.ModelChoiceField(queryset=Department.objects.filter(is_deleted=False).order_by("name"), required=False, widget=forms.Select(attrs={"class": "form-select"}))
    designation = forms.CharField(max_length=100, widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Front-Desk Receptionist / Triage Nurse"}))
    staff_role = forms.ChoiceField(choices=User.STAFF_ROLE_CHOICES, widget=forms.Select(attrs={"class": "form-select"}))
    is_active = forms.BooleanField(required=False, initial=True, widget=forms.CheckboxInput(attrs={"class": "form-check-input"}))

    def __init__(self, *args, staff=None, **kwargs):
        self.staff = staff
        super().__init__(*args, **kwargs)
        if self.staff:
            user = self.staff.user
            self.fields["first_name"].initial = user.first_name
            self.fields["last_name"].initial = user.last_name
            self.fields["email"].initial = user.email
            self.fields["username"].initial = user.username
            self.fields["phone_number"].initial = user.phone_number
            self.fields["department"].initial = self.staff.department
            self.fields["designation"].initial = self.staff.designation
            self.fields["staff_role"].initial = user.staff_role
            self.fields["is_active"].initial = self.staff.is_active and user.is_active
        else:
            self.fields["password"].required = True
            self.fields["password"].widget.attrs["placeholder"] = "Temporary login password"

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        qs = User.objects.filter(email=email)
        if self.staff:
            qs = qs.exclude(pk=self.staff.user.pk)
        if qs.exists():
            raise forms.ValidationError("A user with this email address already exists.")
        return email

    @transaction.atomic
    def save(self):
        data = self.cleaned_data
        is_active = data.get("is_active", True)
        if self.staff:
            user = self.staff.user
            user.first_name = data["first_name"].strip()
            user.last_name = data["last_name"].strip()
            user.email = data["email"]
            if data.get("username"):
                user.username = data["username"].strip()
            user.phone_number = data.get("phone_number", "").strip()
            user.staff_role = data["staff_role"]
            user.is_active = is_active
            if data.get("password"):
                user.set_password(data["password"])
            user.save()

            staff = self.staff
            staff.department = data.get("department")
            staff.designation = data["designation"].strip()
            staff.is_active = is_active
            staff.save()
            return staff
        else:
            username = data.get("username", "").strip()
            if not username:
                base_username = f"staff_{data['first_name'].lower().replace(' ', '')}"
                username = base_username
                c = 1
                while User.objects.filter(username=username).exists():
                    username = f"{base_username}_{c}"
                    c += 1

            user = User.objects.create_user(
                username=username,
                email=data["email"],
                password=data["password"],
                first_name=data["first_name"].strip(),
                last_name=data["last_name"].strip(),
                phone_number=data.get("phone_number", "").strip(),
                user_type="STAFF",
            )
            user.staff_role = data["staff_role"]
            user.is_active = is_active
            user.is_email_verified = True
            user.save()

            staff = Staff.objects.create(
                user=user,
                department=data.get("department"),
                designation=data["designation"].strip(),
                is_active=is_active,
            )
            return staff


# ========================================================
# PHASE 5: MEDICAL RECORDS (EMR LITE) FORMS
# ========================================================

class PatientVitalForm(forms.ModelForm):
    """
    Form for nursing staff or physicians to record / update patient vitals.
    """
    class Meta:
        model = PatientVital
        fields = [
            'systolic_bp',
            'diastolic_bp',
            'pulse_rate',
            'temperature',
            'weight_kg',
            'height_cm',
            'spo2',
            'respiratory_rate',
            'notes',
        ]
        widgets = {
            'systolic_bp': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': '120',
                'min': '40',
                'max': '260',
            }),
            'diastolic_bp': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': '80',
                'min': '30',
                'max': '180',
            }),
            'pulse_rate': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': '72',
                'min': '30',
                'max': '220',
            }),
            'temperature': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': '98.6',
                'step': '0.1',
                'min': '90.0',
                'max': '110.0',
            }),
            'weight_kg': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': '70.0',
                'step': '0.1',
                'min': '1.0',
                'max': '350.0',
            }),
            'height_cm': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': '175.0',
                'step': '0.5',
                'min': '30.0',
                'max': '250.0',
            }),
            'spo2': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': '98',
                'min': '50',
                'max': '100',
            }),
            'respiratory_rate': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': '16',
                'min': '8',
                'max': '60',
            }),
            'notes': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g. Patient rested 5 mins before BP check',
            }),
        }


class PatientMedicalHistoryForm(forms.ModelForm):
    """
    Form for updating a patient's enduring medical history, allergies, and chronic conditions.
    """
    class Meta:
        model = PatientMedicalHistory
        fields = [
            'blood_group',
            'allergies',
            'chronic_conditions',
            'past_surgeries',
            'family_medical_history',
            'current_medications',
        ]
        widgets = {
            'blood_group': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g. O+, A+, B-',
            }),
            'allergies': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 2,
                'placeholder': 'e.g. Penicillin (anaphylaxis), Sulfa drugs, Peanuts',
            }),
            'chronic_conditions': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 2,
                'placeholder': 'e.g. Type 2 Diabetes (5 yrs), Essential Hypertension',
            }),
            'past_surgeries': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 2,
                'placeholder': 'e.g. Appendectomy (2019), Cholecystectomy (2022)',
            }),
            'family_medical_history': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 2,
                'placeholder': 'e.g. Father had myocardial infarction at 55',
            }),
            'current_medications': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 2,
                'placeholder': 'e.g. Metformin 500mg BID, Amlodipine 5mg OD',
            }),
        }


class MedicalRecordForm(forms.ModelForm):
    """
    Form for recording clinical consultation details: symptoms, clinical findings, diagnosis, and treatment plan.
    """
    class Meta:
        model = MedicalRecord
        fields = [
            'chief_complaints',
            'clinical_findings',
            'diagnosis',
            'treatment_plan',
            'follow_up_date',
        ]
        widgets = {
            'chief_complaints': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 2,
                'placeholder': 'e.g. Persistent dry cough for 5 days, low grade evening fever, mild fatigue',
                'required': True,
            }),
            'clinical_findings': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 2,
                'placeholder': 'e.g. Bilateral rhonchi present in lower lobes. Throat congested. Normal S1 S2.',
            }),
            'diagnosis': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 2,
                'placeholder': 'e.g. Acute Viral Bronchitis / Upper Respiratory Tract Infection',
                'required': True,
            }),
            'treatment_plan': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 2,
                'placeholder': 'e.g. Rest, adequate hydration, steam inhalation twice daily, symptomatic pharmacotherapy.',
            }),
            'follow_up_date': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date',
            }),
        }


class PrescriptionForm(forms.ModelForm):
    """
    Form for prescription advice and follow-up scheduling.
    """
    class Meta:
        model = Prescription
        fields = [
            'general_advice',
            'follow_up_date',
        ]
        widgets = {
            'general_advice': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 2,
                'placeholder': 'e.g. Drink warm fluids, avoid cold/chilled beverages, review if symptoms persist beyond 3 days.',
            }),
            'follow_up_date': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date',
            }),
        }


class PrescriptionItemForm(forms.ModelForm):
    """
    Form for an individual medication entry.
    """
    class Meta:
        model = PrescriptionItem
        fields = [
            'medicine_name',
            'dosage',
            'frequency',
            'duration',
            'timing',
            'instructions',
        ]
        widgets = {
            'medicine_name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g. Tab. Paracetamol 650mg or Amoxicillin',
            }),
            'dosage': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g. 650mg, 1 tablet, 10ml',
            }),
            'frequency': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g. 1-0-1, Once daily, TID, SOS',
            }),
            'duration': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g. 5 days, 1 week',
            }),
            'timing': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g. After meals, Before breakfast',
            }),
            'instructions': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g. Take with plenty of water',
            }),
        }


class LabTestOrderForm(forms.ModelForm):
    """
    Form for ordering lab investigations during or after consultation.
    """
    class Meta:
        model = LabTestOrder
        fields = [
            'test_name',
            'clinical_indication',
        ]
        widgets = {
            'test_name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g. Complete Blood Count (CBC) or Fasting Blood Sugar',
                'required': True,
            }),
            'clinical_indication': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g. Evaluate for persistent fever / rule out anemia',
            }),
        }


# ========================================================
# PHASE 6: BILLING & PAYMENT FORMS
# ========================================================

class BillableServiceForm(forms.ModelForm):
    """
    Form to add or edit billable services in the clinical fee catalog.
    """
    class Meta:
        model = BillableService
        fields = [
            'name',
            'category',
            'code',
            'base_fee',
            'department',
            'doctor',
            'description',
            'is_active',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Specialist Consultation, Chest X-Ray'}),
            'category': forms.Select(attrs={'class': 'form-select'}),
            'code': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. CPT-99213, LAB-001'}),
            'base_fee': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
            'department': forms.Select(attrs={'class': 'form-select'}),
            'doctor': forms.Select(attrs={'class': 'form-select'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Service description, scope or billing notes'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['department'].required = False
        self.fields['doctor'].required = False
        self.fields['code'].required = False
        self.fields['description'].required = False


class InvoiceCreateForm(forms.ModelForm):
    """
    Form for creating a new patient invoice (standalone or appointment-linked).
    """
    class Meta:
        model = Invoice
        fields = [
            'patient',
            'doctor',
            'appointment',
            'discount',
            'tax_rate',
            'due_date',
            'notes',
        ]
        widgets = {
            'patient': forms.Select(attrs={'class': 'form-select select2-patient', 'required': True}),
            'doctor': forms.Select(attrs={'class': 'form-select'}),
            'appointment': forms.Select(attrs={'class': 'form-select'}),
            'discount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0', 'value': '0.00'}),
            'tax_rate': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0', 'value': '0.00'}),
            'due_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Payment terms, insurance remarks, or special instructions'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['doctor'].required = False
        self.fields['appointment'].required = False
        self.fields['due_date'].required = False
        self.fields['notes'].required = False
        self.fields['patient'].queryset = Patient.objects.filter(is_active=True, is_deleted=False).select_related('user')
        self.fields['doctor'].queryset = Doctor.objects.filter(is_active=True, is_deleted=False).select_related('user')


class InvoiceLineItemForm(forms.ModelForm):
    """
    Form for individual line items added to an invoice.
    """
    class Meta:
        model = InvoiceLineItem
        fields = [
            'service',
            'description',
            'quantity',
            'unit_price',
        ]
        widgets = {
            'service': forms.Select(attrs={'class': 'form-select service-selector'}),
            'description': forms.TextInput(attrs={'class': 'form-control item-desc', 'placeholder': 'Item or service description'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-control item-qty', 'min': '1', 'value': '1'}),
            'unit_price': forms.NumberInput(attrs={'class': 'form-control item-price', 'step': '0.01', 'min': '0'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['service'].required = False


class PaymentForm(forms.ModelForm):
    """
    Form to record settlement/payment against an invoice.
    """
    class Meta:
        model = Payment
        fields = [
            'amount',
            'payment_method',
            'transaction_reference',
            'notes',
        ]
        widgets = {
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0.01', 'required': True}),
            'payment_method': forms.Select(attrs={'class': 'form-select', 'required': True}),
            'transaction_reference': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Card Auth Code, UPI Ref ID, Cheque No.'}),
            'notes': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Optional payment note'}),
        }

    def __init__(self, *args, **kwargs):
        max_amount = kwargs.pop('max_amount', None)
        super().__init__(*args, **kwargs)
        self.fields['transaction_reference'].required = False
        self.fields['notes'].required = False
        if max_amount is not None:
            self.fields['amount'].widget.attrs['max'] = str(max_amount)
            self.fields['amount'].help_text = f"Maximum payable balance: ${max_amount:.2f}"
