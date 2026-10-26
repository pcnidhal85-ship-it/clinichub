import concurrent.futures
from io import StringIO
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from django.db import IntegrityError, connection
from django.core.management import call_command

import datetime
from django.core.exceptions import ValidationError
from decimal import Decimal
from .models import (
    Department,
    Doctor,
    Patient,
    Appointment,
    Token,
    DoctorDailyTokenSequence,
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
from .forms import DoctorForm
from .services import (
    link_or_create_doctor_profile,
    repair_unlinked_doctor_accounts,
    get_next_token_number_for_doctor,
    generate_appointment_token,
    call_next_patient_for_doctor,
    generate_doctor_time_slots,
    get_slot_tokens_availability,
    book_slot_appointment,
    generate_invoice_number,
    generate_payment_number,
    generate_or_get_appointment_invoice,
    seed_default_billable_services,
)
from users.models import User


class DepartmentModelTest(TestCase):

    def test_department_creation(self):
        department = Department.objects.create(
            name="Cardiology",
            description="Heart care department",
        )

        self.assertEqual(department.name, "Cardiology")
        self.assertTrue(department.is_active)
        self.assertFalse(department.is_deleted)


class AppointmentTokenTest(TestCase):

    def setUp(self):
        self.department = Department.objects.create(
            name="Cardiology",
        )

        self.doctor_user = User.objects.create_user(
            username="doctor_test",
            email="doctor_test@example.com",
            password="TestPassword123!",
            user_type="DOCTOR",
        )

        self.doctor = Doctor.objects.create(
            user=self.doctor_user,
            department=self.department,
            specialization="Cardiology",
            license_number="TEST-LICENSE-001",
        )

        self.patient_user = User.objects.create_user(
            username="patient_test",
            email="patient_test@example.com",
            password="TestPassword123!",
            user_type="PATIENT",
        )
        self.patient_user.is_email_verified = True
        self.patient_user.save()

        self.patient, created = Patient.objects.get_or_create(
            user=self.patient_user,
        )

        self.staff_user = User.objects.create_user(
            username="staff_test",
            email="staff_test@example.com",
            password="TestPassword123!",
            user_type="STAFF",
            staff_role="RECEPTIONIST",
        )
        self.staff_user.is_email_verified = True
        self.staff_user.save()

    def test_appointment_creation(self):
        appointment = Appointment.objects.create(
            patient=self.patient,
            doctor=self.doctor,
            appointment_date=timezone.localdate(),
            appointment_time=timezone.localtime().time(),
        )

        self.assertEqual(appointment.status, "BOOKED")
        self.assertFalse(appointment.is_deleted)

    def test_token_creation(self):
        appointment = Appointment.objects.create(
            patient=self.patient,
            doctor=self.doctor,
            appointment_date=timezone.localdate(),
            appointment_time=timezone.localtime().time(),
        )

        token = Token.objects.create(
            appointment=appointment,
            token_number=1,
            token_date=timezone.localdate(),
        )

        self.assertEqual(token.token_number, 1)
        self.assertEqual(token.status, "BOOKED")
        self.assertFalse(token.is_deleted)

    def test_token_unique_constraint(self):
        today = timezone.localdate()
        apt1 = Appointment.objects.create(
            patient=self.patient,
            doctor=self.doctor,
            appointment_date=today,
            appointment_time=timezone.localtime().time(),
        )
        apt2 = Appointment.objects.create(
            patient=self.patient,
            doctor=self.doctor,
            appointment_date=today,
            appointment_time=timezone.localtime().time(),
        )

        Token.objects.create(
            appointment=apt1,
            token_number=1,
            token_date=today,
        )

        with self.assertRaises(IntegrityError):
            Token.objects.create(
                appointment=apt2,
                token_number=1,
                token_date=today,
            )

    def test_patient_views(self):
        self.client.force_login(self.patient_user)
        response = self.client.get(reverse("patient-dashboard"))
        self.assertEqual(response.status_code, 200)

        response = self.client.get(reverse("my-appointments"))
        self.assertEqual(response.status_code, 200)

    def test_staff_views(self):
        self.client.force_login(self.staff_user)
        response = self.client.get(reverse("today-appointments"))
        self.assertEqual(response.status_code, 200)

        response = self.client.get(reverse("queue"))
        self.assertEqual(response.status_code, 200)

    def test_role_enforcement(self):
        # Patient attempting to view staff page should be 403 Forbidden
        self.client.force_login(self.patient_user)
        response = self.client.get(reverse("today-appointments"))
        self.assertEqual(response.status_code, 403)

        response = self.client.get(reverse("queue"))
        self.assertEqual(response.status_code, 403)

    def test_doctor_dashboard(self):
        self.client.force_login(self.doctor_user)
        response = self.client.get(reverse("doctor-dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.doctor.specialization)

    def test_doctor_appointments(self):
        self.client.force_login(self.doctor_user)
        response = self.client.get(reverse("doctor-appointments"))
        self.assertEqual(response.status_code, 200)

    def test_consultation_workflow(self):
        today = timezone.localdate()
        apt = Appointment.objects.create(
            patient=self.patient,
            doctor=self.doctor,
            appointment_date=today,
            appointment_time=timezone.localtime().time(),
            status="WAITING",
        )
        token = Token.objects.create(
            appointment=apt,
            token_number=1,
            token_date=today,
            status="WAITING",
        )

        self.client.force_login(self.doctor_user)

        # 1. Start consultation
        response = self.client.post(reverse("start-consultation", kwargs={"appointment_id": apt.id}))
        self.assertEqual(response.status_code, 302)
        apt.refresh_from_db()
        token.refresh_from_db()
        self.assertEqual(apt.status, "IN_CONSULTATION")
        self.assertEqual(token.status, "CALLED")

        # 2. View workspace
        response = self.client.get(reverse("consultation-workspace", kwargs={"appointment_id": apt.id}))
        self.assertEqual(response.status_code, 200)

        # 3. Complete consultation with notes
        response = self.client.post(
            reverse("complete-consultation", kwargs={"appointment_id": apt.id}),
            {"notes": "Patient diagnosed with mild hypertension. Prescribed Lisinopril 10mg daily."},
        )
        self.assertEqual(response.status_code, 302)
        apt.refresh_from_db()
        token.refresh_from_db()
        self.assertEqual(apt.status, "COMPLETED")
        self.assertEqual(token.status, "COMPLETED")
        self.assertIn("Lisinopril", apt.notes)

    def test_doctor_availability_workflow(self):
        self.client.force_login(self.doctor_user)

        # View availability page
        response = self.client.get(reverse("doctor-availability"))
        self.assertEqual(response.status_code, 200)

        # Create slot
        response = self.client.post(
            reverse("doctor-availability"),
            {
                "day": "MON",
                "start_time": "09:00",
                "end_time": "13:00",
                "slot_duration": 15,
                "is_active": True,
            },
        )
        self.assertEqual(response.status_code, 302)
        slot = self.doctor.availabilities.first()
        self.assertIsNotNone(slot)
        self.assertEqual(slot.day, "MON")

        # Toggle slot
        response = self.client.post(reverse("toggle-availability", kwargs={"availability_id": slot.id}))
        self.assertEqual(response.status_code, 302)
        slot.refresh_from_db()
        self.assertFalse(slot.is_active)

        # Delete slot
        response = self.client.post(reverse("delete-availability", kwargs={"availability_id": slot.id}))
        self.assertEqual(response.status_code, 302)
        slot.refresh_from_db()
        self.assertTrue(slot.is_deleted)

    def test_doctor_patient_history(self):
        self.client.force_login(self.doctor_user)
        response = self.client.get(reverse("doctor-patient-history", kwargs={"patient_id": self.patient.id}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.patient.user.first_name)

    def test_patient_cannot_access_doctor_views(self):
        self.client.force_login(self.patient_user)
        response = self.client.get(reverse("doctor-dashboard"))
        self.assertEqual(response.status_code, 403)

        response = self.client.get(reverse("doctor-availability"))
        self.assertEqual(response.status_code, 403)


class DoctorProfileCreationAndRepairTest(TestCase):

    def setUp(self):
        self.department = Department.objects.create(
            name="Cardiology",
            description="Cardiology Department",
        )
        self.pediatrics = Department.objects.create(
            name="Pediatrics",
            description="Pediatrics Department",
        )

    def test_unlinked_doctor_dashboard_returns_404(self):
        """Verify that an active DOCTOR user without a Doctor profile receives a 404."""
        unlinked_user = User.objects.create_user(
            username="unlinked_doctor",
            email="unlinked@example.com",
            password="TestPassword123!",
            user_type="DOCTOR",
        )
        self.client.force_login(unlinked_user)
        response = self.client.get(reverse("doctor-dashboard"))
        self.assertEqual(response.status_code, 404)

    def test_linked_doctor_dashboard_returns_200(self):
        """Verify that once linked, the doctor dashboard returns 200 OK."""
        doctor_user = User.objects.create_user(
            username="linked_doctor",
            email="linked@example.com",
            password="TestPassword123!",
            user_type="DOCTOR",
        )
        link_or_create_doctor_profile(
            user=doctor_user,
            department=self.department,
            specialization="Cardiology",
            license_number="LIC-LINKED-001",
        )
        self.client.force_login(doctor_user)
        response = self.client.get(reverse("doctor-dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cardiology")

    def test_link_or_create_doctor_profile_idempotent_and_safe(self):
        """Test service function creates profile and prevents duplicates safely."""
        doc_user = User.objects.create_user(
            username="service_doc",
            email="service_doc@example.com",
            password="TestPassword123!",
            user_type="DOCTOR",
            doctor_role="CARDIOLOGIST",
        )
        profile, created = link_or_create_doctor_profile(user=doc_user)
        self.assertTrue(created)
        self.assertEqual(profile.user, doc_user)
        self.assertEqual(profile.department, self.department)
        self.assertTrue(profile.license_number.startswith("DEV-DOC-"))

        # Second call must be idempotent: return existing, created=False
        profile_again, created_again = link_or_create_doctor_profile(user=doc_user)
        self.assertFalse(created_again)
        self.assertEqual(profile.id, profile_again.id)
        self.assertEqual(Doctor.objects.filter(user=doc_user).count(), 1)

    def test_doctor_form_creates_new_user_and_profile(self):
        """DoctorForm creates both new user and linked doctor profile."""
        form_data = {
            "username": "dr_alice",
            "first_name": "Alice",
            "last_name": "Smith",
            "email": "alice.smith@example.com",
            "password": "SecurePassword123!",
            "doctor_role": "CARDIOLOGIST",
            "department": self.department.id,
            "specialization": "Cardiologist",
            "license_number": "LIC-ALICE-123",
            "consultation_fee": "100.00",
            "is_available": True,
        }
        form = DoctorForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)
        doctor = form.save()
        self.assertIsNotNone(doctor.pk)
        self.assertEqual(doctor.user.username, "dr_alice")
        self.assertEqual(doctor.user.email, "alice.smith@example.com")
        self.assertEqual(doctor.user.user_type, "DOCTOR")
        self.assertEqual(doctor.license_number, "LIC-ALICE-123")

    def test_doctor_form_links_existing_unlinked_user(self):
        """DoctorForm links an existing unlinked doctor account without duplicating user."""
        existing_user = User.objects.create_user(
            username="existing_unlinked",
            email="existing_unlinked@example.com",
            password="TestPassword123!",
            user_type="DOCTOR",
            first_name="Bob",
            last_name="Jones",
        )
        form_data = {
            "existing_user": str(existing_user.id),
            "department": self.department.id,
            "specialization": "Heart Surgeon",
            "license_number": "LIC-BOB-999",
            "consultation_fee": "150.00",
            "is_available": True,
        }
        form = DoctorForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)
        doctor = form.save()
        self.assertEqual(doctor.user, existing_user)
        self.assertEqual(User.objects.filter(username="existing_unlinked").count(), 1)
        self.assertEqual(Doctor.objects.filter(user=existing_user).count(), 1)

    def test_doctor_form_updates_existing_doctor(self):
        """DoctorForm edits an existing doctor instance cleanly without user creation."""
        doc_user = User.objects.create_user(
            username="edit_doc",
            email="edit_doc@example.com",
            password="TestPassword123!",
            user_type="DOCTOR",
        )
        doctor = Doctor.objects.create(
            user=doc_user,
            department=self.department,
            specialization="General",
            license_number="LIC-ORIGINAL",
        )

        form_data = {
            "first_name": "EditedName",
            "last_name": doc_user.last_name,
            "email": doc_user.email,
            "department": self.pediatrics.id,
            "specialization": "Pediatric Cardiology",
            "license_number": "LIC-UPDATED",
            "consultation_fee": "200.00",
            "is_available": True,
        }
        form = DoctorForm(data=form_data, instance=doctor)
        self.assertTrue(form.is_valid(), form.errors)
        updated_doc = form.save()
        self.assertEqual(updated_doc.id, doctor.id)
        self.assertEqual(updated_doc.department, self.pediatrics)
        self.assertEqual(updated_doc.specialization, "Pediatric Cardiology")
        self.assertEqual(updated_doc.license_number, "LIC-UPDATED")
        doc_user.refresh_from_db()
        self.assertEqual(doc_user.first_name, "EditedName")

    def test_repair_doctor_profiles_management_command(self):
        """Test repair_doctor_profiles management command in dry-run and live modes."""
        orphan_doc = User.objects.create_user(
            username="orphan_doc",
            email="orphan@example.com",
            password="TestPassword123!",
            user_type="DOCTOR",
            doctor_role="CARDIOLOGIST",
        )

        # 1. Dry run
        out_dry = StringIO()
        call_command("repair_doctor_profiles", dry_run=True, username="orphan_doc", stdout=out_dry)
        self.assertIn("(DRY RUN)", out_dry.getvalue())
        self.assertIn("[NEEDS PROFILE]", out_dry.getvalue())
        self.assertFalse(Doctor.objects.filter(user=orphan_doc).exists())

        # 2. Live execution
        out_live = StringIO()
        call_command("repair_doctor_profiles", username="orphan_doc", stdout=out_live)
        self.assertIn("[REPAIRED]", out_live.getvalue())
        self.assertTrue(Doctor.objects.filter(user=orphan_doc).exists())
        profile = Doctor.objects.get(user=orphan_doc)
        self.assertEqual(profile.department, self.department)


class PatientRescheduleAndCancelTest(TestCase):

    def setUp(self):
        self.department = Department.objects.create(
            name="General Medicine",
            description="General care",
        )
        self.doctor_user = User.objects.create_user(
            username="doctor_resched",
            email="doc_resched@example.com",
            password="TestPassword123!",
            user_type="DOCTOR",
            doctor_role="GENERAL",
        )
        self.doctor = Doctor.objects.create(
            user=self.doctor_user,
            department=self.department,
            specialization="General Medicine",
            license_number="LIC-RESCHED-001",
        )

        self.patient_user = User.objects.create_user(
            username="patient_resched",
            email="patient_resched@example.com",
            password="TestPassword123!",
            user_type="PATIENT",
        )
        self.patient_user.is_email_verified = True
        self.patient_user.save()
        self.patient = self.patient_user.patient_profile

        self.other_patient_user = User.objects.create_user(
            username="other_patient",
            email="other_patient@example.com",
            password="TestPassword123!",
            user_type="PATIENT",
        )
        self.other_patient_user.is_email_verified = True
        self.other_patient_user.save()
        self.other_patient = self.other_patient_user.patient_profile

        self.today = timezone.localdate()
        self.appointment = Appointment.objects.create(
            patient=self.patient,
            doctor=self.doctor,
            appointment_date=self.today + timezone.timedelta(days=2),
            appointment_time=timezone.datetime.strptime("10:30", "%H:%M").time(),
            status="BOOKED",
            reason="Checkup",
        )
        self.token = Token.objects.create(
            appointment=self.appointment,
            token_number=1,
            token_date=self.appointment.appointment_date,
            status="BOOKED",
        )

    def test_cancel_appointment_page_loads(self):
        self.client.force_login(self.patient_user)
        response = self.client.get(reverse("cancel-appointment", kwargs={"appointment_id": self.appointment.id}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cancel Consultation?")
        self.assertContains(response, self.doctor.user.last_name)

    def test_cancel_appointment_success(self):
        self.client.force_login(self.patient_user)
        response = self.client.post(
            reverse("cancel-appointment", kwargs={"appointment_id": self.appointment.id}),
            {
                "cancellation_reason": "Schedule conflict / timing issue",
                "notes": "Travelling out of town",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("my-appointments"))

        self.appointment.refresh_from_db()
        self.token.refresh_from_db()
        self.assertEqual(self.appointment.status, "CANCELLED")
        self.assertEqual(self.token.status, "CANCELLED")
        self.assertIn("Schedule conflict", self.appointment.notes)
        self.assertIn("Travelling out of town", self.appointment.notes)

    def test_cancel_appointment_forbidden_for_other_patient(self):
        self.client.force_login(self.other_patient_user)
        response = self.client.post(
            reverse("cancel-appointment", kwargs={"appointment_id": self.appointment.id}),
            {"cancellation_reason": "Personal emergency"},
        )
        self.assertEqual(response.status_code, 404)
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.status, "BOOKED")

    def test_cancel_completed_appointment_fails(self):
        self.appointment.status = "COMPLETED"
        self.appointment.save()

        self.client.force_login(self.patient_user)
        response = self.client.post(
            reverse("cancel-appointment", kwargs={"appointment_id": self.appointment.id}),
            {"cancellation_reason": "Personal emergency"},
        )
        self.assertEqual(response.status_code, 302)
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.status, "COMPLETED")

    def test_reschedule_appointment_page_loads(self):
        self.client.force_login(self.patient_user)
        response = self.client.get(reverse("reschedule-appointment", kwargs={"appointment_id": self.appointment.id}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reschedule Your Consultation")
        self.assertContains(response, self.doctor.user.last_name)

    def test_reschedule_appointment_success_generates_new_token(self):
        self.client.force_login(self.patient_user)
        new_date = self.today + timezone.timedelta(days=5)
        response = self.client.post(
            reverse("reschedule-appointment", kwargs={"appointment_id": self.appointment.id}),
            {
                "department": self.department.id,
                "doctor": self.doctor.id,
                "appointment_date": new_date.strftime("%Y-%m-%d"),
                "appointment_time": "14:15",
                "reason": "Updated reason for visit",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("appointment-success", kwargs={"appointment_id": self.appointment.id}))

        self.appointment.refresh_from_db()
        self.token.refresh_from_db()
        self.assertEqual(self.appointment.appointment_date, new_date)
        self.assertEqual(self.appointment.appointment_time, timezone.datetime.strptime("14:15", "%H:%M").time())
        self.assertEqual(self.appointment.reason, "Updated reason for visit")
        self.assertEqual(self.token.token_date, new_date)
        self.assertEqual(self.token.token_number, 1)
        self.assertEqual(self.appointment.status, "BOOKED")
        self.assertIn("[Rescheduled", self.appointment.notes)

    def test_reschedule_past_date_rejected(self):
        self.client.force_login(self.patient_user)
        past_date = self.today - timezone.timedelta(days=1)
        response = self.client.post(
            reverse("reschedule-appointment", kwargs={"appointment_id": self.appointment.id}),
            {
                "department": self.department.id,
                "doctor": self.doctor.id,
                "appointment_date": past_date.strftime("%Y-%m-%d"),
                "appointment_time": "14:15",
                "reason": "Past date check",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cannot be in the past")
        self.appointment.refresh_from_db()
        self.assertNotEqual(self.appointment.appointment_date, past_date)

    def test_reschedule_forbidden_for_other_patient(self):
        self.client.force_login(self.other_patient_user)
        new_date = self.today + timezone.timedelta(days=4)
        response = self.client.post(
            reverse("reschedule-appointment", kwargs={"appointment_id": self.appointment.id}),
            {
                "department": self.department.id,
                "doctor": self.doctor.id,
                "appointment_date": new_date.strftime("%Y-%m-%d"),
                "appointment_time": "14:15",
            },
        )
        self.assertEqual(response.status_code, 404)
        self.appointment.refresh_from_db()
        self.assertNotEqual(self.appointment.appointment_date, new_date)


class DoctorIndependentTokenQueueTests(TestCase):
    """
    Test suite for independent doctor token queues, walk-in booking,
    status transitions, repeated check-in idempotency, queue isolation,
    and cancellation/rescheduling logic.
    """

    def setUp(self):
        self.cardiology = Department.objects.create(name="Cardiology")
        self.pediatrics = Department.objects.create(name="Pediatrics")
        self.ent = Department.objects.create(name="ENT")

        # Dr. Arun (Cardiology)
        self.user_arun = User.objects.create_user(
            username="dr_arun",
            email="dr_arun@clinichub.test",
            password="Password123!",
            first_name="Arun",
            last_name="Kumar",
            user_type="DOCTOR",
            doctor_role="CARDIOLOGIST",
        )
        self.doctor_arun = Doctor.objects.create(
            user=self.user_arun,
            department=self.cardiology,
            specialization="Cardiology",
            license_number="LIC-ARUN-001",
        )

        # Dr. Rahul (Pediatrics)
        self.user_rahul = User.objects.create_user(
            username="dr_rahul",
            email="dr_rahul@clinichub.test",
            password="Password123!",
            first_name="Rahul",
            last_name="Sharma",
            user_type="DOCTOR",
            doctor_role="PEDIATRICIAN",
        )
        self.doctor_rahul = Doctor.objects.create(
            user=self.user_rahul,
            department=self.pediatrics,
            specialization="Pediatrics",
            license_number="LIC-RAHUL-002",
        )

        # Dr. Meera (ENT)
        self.user_meera = User.objects.create_user(
            username="dr_meera",
            email="dr_meera@clinichub.test",
            password="Password123!",
            first_name="Meera",
            last_name="Nair",
            user_type="DOCTOR",
            doctor_role="ENT",
        )
        self.doctor_meera = Doctor.objects.create(
            user=self.user_meera,
            department=self.ent,
            specialization="ENT",
            license_number="LIC-MEERA-003",
        )

        # Patients
        self.user_pat1 = User.objects.create_user(
            username="patient_one",
            email="pat1@clinichub.test",
            password="Password123!",
            first_name="Alice",
            last_name="Smith",
            user_type="PATIENT",
        )
        self.user_pat1.is_email_verified = True
        self.user_pat1.save()
        self.patient_1 = self.user_pat1.patient_profile

        self.user_pat2 = User.objects.create_user(
            username="patient_two",
            email="pat2@clinichub.test",
            password="Password123!",
            first_name="Bob",
            last_name="Jones",
            user_type="PATIENT",
        )
        self.user_pat2.is_email_verified = True
        self.user_pat2.save()
        self.patient_2 = self.user_pat2.patient_profile

        self.user_pat3 = User.objects.create_user(
            username="patient_three",
            email="pat3@clinichub.test",
            password="Password123!",
            first_name="Charlie",
            last_name="Brown",
            user_type="PATIENT",
        )
        self.user_pat3.is_email_verified = True
        self.user_pat3.save()
        self.patient_3 = self.user_pat3.patient_profile

        # Receptionist Staff
        self.user_receptionist = User.objects.create_user(
            username="receptionist_mary",
            email="receptionist@clinichub.test",
            password="Password123!",
            first_name="Mary",
            last_name="Watson",
            user_type="STAFF",
            staff_role="RECEPTIONIST",
        )
        self.user_receptionist.is_email_verified = True
        self.user_receptionist.save()

        self.today = timezone.localdate()

    def test_different_doctors_receive_token_001_on_same_date(self):
        """
        Verify that Dr. Arun, Dr. Rahul, and Dr. Meera can each receive token 001
        on the exact same date without conflict.
        """
        apt_arun = Appointment.objects.create(
            patient=self.patient_1,
            doctor=self.doctor_arun,
            appointment_date=self.today,
            appointment_time=timezone.now().time(),
        )
        apt_rahul = Appointment.objects.create(
            patient=self.patient_2,
            doctor=self.doctor_rahul,
            appointment_date=self.today,
            appointment_time=timezone.now().time(),
        )
        apt_meera = Appointment.objects.create(
            patient=self.patient_3,
            doctor=self.doctor_meera,
            appointment_date=self.today,
            appointment_time=timezone.now().time(),
        )

        token_arun = generate_appointment_token(apt_arun, status="BOOKED")
        token_rahul = generate_appointment_token(apt_rahul, status="BOOKED")
        token_meera = generate_appointment_token(apt_meera, status="BOOKED")

        # All 3 tokens have number 1 and format "001"
        self.assertEqual(token_arun.token_number, 1)
        self.assertEqual(token_rahul.token_number, 1)
        self.assertEqual(token_meera.token_number, 1)

        self.assertEqual(token_arun.formatted_token_number, "001")
        self.assertEqual(token_rahul.formatted_token_number, "001")
        self.assertEqual(token_meera.formatted_token_number, "001")

        # Each token's doctor matches appointment's doctor
        self.assertEqual(token_arun.doctor, self.doctor_arun)
        self.assertEqual(token_rahul.doctor, self.doctor_rahul)
        self.assertEqual(token_meera.doctor, self.doctor_meera)

    def test_same_doctor_receives_sequential_numbers_on_same_date(self):
        """
        Verify that the same doctor receives sequential token numbers 001, 002, 003...
        on the same date.
        """
        apt1 = Appointment.objects.create(
            patient=self.patient_1,
            doctor=self.doctor_arun,
            appointment_date=self.today,
            appointment_time=timezone.now().time(),
        )
        apt2 = Appointment.objects.create(
            patient=self.patient_2,
            doctor=self.doctor_arun,
            appointment_date=self.today,
            appointment_time=timezone.now().time(),
        )
        apt3 = Appointment.objects.create(
            patient=self.patient_3,
            doctor=self.doctor_arun,
            appointment_date=self.today,
            appointment_time=timezone.now().time(),
        )

        tok1 = generate_appointment_token(apt1)
        tok2 = generate_appointment_token(apt2)
        tok3 = generate_appointment_token(apt3)

        self.assertEqual(tok1.token_number, 1)
        self.assertEqual(tok2.token_number, 2)
        self.assertEqual(tok3.token_number, 3)

        self.assertEqual(tok1.formatted_token_number, "001")
        self.assertEqual(tok2.formatted_token_number, "002")
        self.assertEqual(tok3.formatted_token_number, "003")

    def test_each_doctor_starts_new_sequence_on_new_date(self):
        """
        Verify that token numbers start again at 001 for each new date.
        """
        tomorrow = self.today + timezone.timedelta(days=1)

        apt_today = Appointment.objects.create(
            patient=self.patient_1,
            doctor=self.doctor_arun,
            appointment_date=self.today,
            appointment_time=timezone.now().time(),
        )
        apt_tomorrow = Appointment.objects.create(
            patient=self.patient_2,
            doctor=self.doctor_arun,
            appointment_date=tomorrow,
            appointment_time=timezone.now().time(),
        )

        tok_today = generate_appointment_token(apt_today)
        tok_tomorrow = generate_appointment_token(apt_tomorrow)

        self.assertEqual(tok_today.token_number, 1)
        self.assertEqual(tok_today.formatted_token_number, "001")

        self.assertEqual(tok_tomorrow.token_number, 1)
        self.assertEqual(tok_tomorrow.formatted_token_number, "001")

    def test_database_level_uniqueness_enforced(self):
        """
        Verify that database constraint prevents duplicate (doctor, token_date, token_number).
        """
        apt1 = Appointment.objects.create(
            patient=self.patient_1,
            doctor=self.doctor_arun,
            appointment_date=self.today,
            appointment_time=timezone.now().time(),
        )
        apt2 = Appointment.objects.create(
            patient=self.patient_2,
            doctor=self.doctor_arun,
            appointment_date=self.today,
            appointment_time=timezone.now().time(),
        )

        Token.objects.create(
            appointment=apt1,
            doctor=self.doctor_arun,
            token_date=self.today,
            token_number=1,
            status="BOOKED",
        )

        with self.assertRaises(IntegrityError):
            Token.objects.create(
                appointment=apt2,
                doctor=self.doctor_arun,
                token_date=self.today,
                token_number=1,
                status="BOOKED",
            )

    def test_walk_in_booking_generates_waiting_token_and_checked_in_appointment(self):
        """
        Verify that walk-in booking by staff creates a CHECKED_IN appointment
        and a WAITING token with checked_in_at set.
        """
        self.client.force_login(self.user_receptionist)
        response = self.client.post(
            reverse("walk-in-booking"),
            {
                "patient_mode": "EXISTING",
                "existing_patient": self.patient_1.id,
                "department": self.cardiology.id,
                "doctor": self.doctor_arun.id,
                "reason": "Chest tightness and palpitations",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("queue"))

        apt = Appointment.objects.filter(patient=self.patient_1, doctor=self.doctor_arun).first()
        self.assertIsNotNone(apt)
        self.assertEqual(apt.status, "CHECKED_IN")
        self.assertEqual(apt.appointment_date, self.today)

        token = apt.token
        self.assertIsNotNone(token)
        self.assertEqual(token.status, "WAITING")
        self.assertIsNotNone(token.checked_in_at)
        self.assertEqual(token.token_number, 1)
        self.assertEqual(token.formatted_token_number, "001")
        self.assertEqual(token.doctor, self.doctor_arun)

    def test_walk_in_booking_with_new_patient_registration(self):
        """
        Verify that walk-in booking allows quick registration of a new walk-in patient.
        """
        self.client.force_login(self.user_receptionist)
        response = self.client.post(
            reverse("walk-in-booking"),
            {
                "patient_mode": "NEW",
                "first_name": "Daniel",
                "last_name": "Craig",
                "phone_number": "5551234567",
                "gender": "MALE",
                "blood_group": "A+",
                "department": self.pediatrics.id,
                "doctor": self.doctor_rahul.id,
                "reason": "Ear pain",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("queue"))

        user = User.objects.filter(first_name="Daniel", last_name="Craig").first()
        self.assertIsNotNone(user)
        self.assertEqual(user.user_type, "PATIENT")
        self.assertEqual(user.phone_number, "5551234567")

        patient = user.patient_profile
        self.assertEqual(patient.gender, "MALE")
        self.assertEqual(patient.blood_group, "A+")

        apt = Appointment.objects.filter(patient=patient, doctor=self.doctor_rahul).first()
        self.assertEqual(apt.status, "CHECKED_IN")
        self.assertEqual(apt.token.status, "WAITING")
        self.assertEqual(apt.token.formatted_token_number, "001")

    def test_repeated_check_in_does_not_generate_duplicate_tokens(self):
        """
        Verify that repeatedly checking in an appointment updates its existing token
        and never generates duplicate tokens or consumes sequence numbers.
        """
        apt = Appointment.objects.create(
            patient=self.patient_1,
            doctor=self.doctor_arun,
            appointment_date=self.today,
            appointment_time=timezone.now().time(),
            status="BOOKED",
        )
        tok = generate_appointment_token(apt, status="BOOKED")
        self.assertEqual(tok.token_number, 1)
        self.assertEqual(tok.status, "BOOKED")

        self.client.force_login(self.user_receptionist)

        # First check-in
        res1 = self.client.post(reverse("check-in-patient", kwargs={"appointment_id": apt.id}))
        self.assertEqual(res1.status_code, 302)

        apt.refresh_from_db()
        tok.refresh_from_db()
        self.assertEqual(apt.status, "CHECKED_IN")
        self.assertEqual(tok.status, "WAITING")
        self.assertEqual(tok.token_number, 1)
        self.assertIsNotNone(tok.checked_in_at)
        self.assertEqual(Token.objects.filter(appointment=apt).count(), 1)

        # Second check-in (repeated / duplicate attempt)
        res2 = self.client.post(reverse("check-in-patient", kwargs={"appointment_id": apt.id}))
        self.assertEqual(res2.status_code, 302)

        apt.refresh_from_db()
        tok.refresh_from_db()
        self.assertEqual(apt.status, "CHECKED_IN")
        self.assertEqual(tok.status, "WAITING")
        self.assertEqual(tok.token_number, 1)
        self.assertEqual(Token.objects.filter(appointment=apt).count(), 1)

        # Ensure subsequent patient gets token #2, not #3
        apt2 = Appointment.objects.create(
            patient=self.patient_2,
            doctor=self.doctor_arun,
            appointment_date=self.today,
            appointment_time=timezone.now().time(),
            status="BOOKED",
        )
        tok2 = generate_appointment_token(apt2, status="BOOKED")
        self.assertEqual(tok2.token_number, 2)

    def test_cancellation_and_rescheduling_work_correctly(self):
        """
        Verify that cancelling an appointment keeps the cancelled token record,
        does not reuse the cancelled token number, and rescheduling assigns
        the correct sequence for the target date/doctor.
        """
        apt1 = Appointment.objects.create(
            patient=self.patient_1,
            doctor=self.doctor_arun,
            appointment_date=self.today,
            appointment_time=timezone.now().time(),
            status="BOOKED",
        )
        tok1 = generate_appointment_token(apt1, status="BOOKED")
        self.assertEqual(tok1.token_number, 1)

        # Cancel apt1
        self.client.force_login(self.user_pat1)
        cancel_res = self.client.post(
            reverse("cancel-appointment", kwargs={"appointment_id": apt1.id}),
            {"cancellation_reason": "Personal emergency"},
        )
        self.assertEqual(cancel_res.status_code, 302)

        apt1.refresh_from_db()
        tok1.refresh_from_db()
        self.assertEqual(apt1.status, "CANCELLED")
        self.assertEqual(tok1.status, "CANCELLED")

        # Book apt2 for same doctor and date -> must receive #002 (never reuse #001)
        apt2 = Appointment.objects.create(
            patient=self.patient_2,
            doctor=self.doctor_arun,
            appointment_date=self.today,
            appointment_time=timezone.now().time(),
            status="BOOKED",
        )
        tok2 = generate_appointment_token(apt2, status="BOOKED")
        self.assertEqual(tok2.token_number, 2)
        self.assertEqual(tok2.formatted_token_number, "002")

        # Reschedule apt2 to next week with Dr. Rahul
        next_week = self.today + timezone.timedelta(days=7)
        self.client.force_login(self.user_pat2)
        resched_res = self.client.post(
            reverse("reschedule-appointment", kwargs={"appointment_id": apt2.id}),
            {
                "department": self.pediatrics.id,
                "doctor": self.doctor_rahul.id,
                "appointment_date": next_week.strftime("%Y-%m-%d"),
                "appointment_time": "11:00",
                "reason": "Rescheduled to pediatrician",
            },
        )
        self.assertEqual(resched_res.status_code, 302)

        apt2.refresh_from_db()
        tok2.refresh_from_db()
        self.assertEqual(apt2.doctor, self.doctor_rahul)
        self.assertEqual(apt2.appointment_date, next_week)
        self.assertEqual(tok2.doctor, self.doctor_rahul)
        self.assertEqual(tok2.token_date, next_week)
        # Dr. Rahul has no tokens on next_week yet, so tok2 receives #1
        self.assertEqual(tok2.token_number, 1)
        self.assertEqual(tok2.formatted_token_number, "001")

    def test_doctor_queue_isolation(self):
        """
        Verify:
        - Each doctor sees only their own waiting queue.
        - Calling next patient atomically selects the next eligible WAITING token for that doctor.
        - Doctor cannot call or access other doctors' patients.
        - Staff can view all queues grouped by doctor.
        """
        # Dr. Arun waiting patient
        apt_arun = Appointment.objects.create(
            patient=self.patient_1,
            doctor=self.doctor_arun,
            appointment_date=self.today,
            appointment_time=timezone.now().time(),
            status="CHECKED_IN",
        )
        tok_arun = generate_appointment_token(apt_arun, status="WAITING", checked_in_at=timezone.now())

        # Dr. Rahul waiting patient
        apt_rahul = Appointment.objects.create(
            patient=self.patient_2,
            doctor=self.doctor_rahul,
            appointment_date=self.today,
            appointment_time=timezone.now().time(),
            status="CHECKED_IN",
        )
        tok_rahul = generate_appointment_token(apt_rahul, status="WAITING", checked_in_at=timezone.now())

        # Doctor Arun logs in
        self.client.force_login(self.user_arun)

        # Dr. Arun accesses queue: only sees Dr. Arun's tokens
        response = self.client.get(reverse("queue"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.patient_1.user.last_name)
        self.assertNotContains(response, self.patient_2.user.last_name)

        # Dr. Arun tries to start consultation on Dr. Rahul's appointment -> 404 forbidden
        cross_res = self.client.post(reverse("start-consultation", kwargs={"appointment_id": apt_rahul.id}))
        self.assertEqual(cross_res.status_code, 404)

        # Dr. Arun calls next patient: atomically transitions Dr. Arun's patient
        call_res = self.client.post(reverse("call-next-patient"))
        self.assertEqual(call_res.status_code, 302)
        self.assertRedirects(call_res, reverse("consultation-workspace", kwargs={"appointment_id": apt_arun.id}))

        apt_arun.refresh_from_db()
        tok_arun.refresh_from_db()
        self.assertEqual(apt_arun.status, "IN_CONSULTATION")
        self.assertEqual(tok_arun.status, "CALLED")

        # Dr. Rahul's patient remains WAITING and untouched
        apt_rahul.refresh_from_db()
        tok_rahul.refresh_from_db()
        self.assertEqual(apt_rahul.status, "CHECKED_IN")
        self.assertEqual(tok_rahul.status, "WAITING")

        # Receptionist views queue: sees both Dr. Arun and Dr. Rahul
        self.client.force_login(self.user_receptionist)
        staff_q_res = self.client.get(reverse("queue"))
        self.assertEqual(staff_q_res.status_code, 200)
        self.assertContains(staff_q_res, "Rahul")

    def test_existing_appointment_and_consultation_workflows_still_work(self):
        """
        End-to-end integration test:
        1. Patient books appointment.
        2. Receptionist checks in patient.
        3. Doctor calls next patient and enters consultation workspace.
        4. Doctor completes consultation with clinical notes.
        """
        # 1. Patient booking
        self.client.force_login(self.user_pat1)
        book_res = self.client.post(
            reverse("create-appointment"),
            {
                "department": self.cardiology.id,
                "doctor": self.doctor_arun.id,
                "appointment_date": self.today.strftime("%Y-%m-%d"),
                "appointment_time": "10:30",
                "reason": "Routine hypertension check",
            },
        )
        self.assertEqual(book_res.status_code, 302)

        appointment = Appointment.objects.filter(patient=self.patient_1, doctor=self.doctor_arun).first()
        self.assertIsNotNone(appointment)
        self.assertEqual(appointment.status, "BOOKED")
        self.assertEqual(appointment.token.status, "BOOKED")
        self.assertEqual(appointment.token.formatted_token_number, "001")

        # 2. Receptionist check-in
        self.client.force_login(self.user_receptionist)
        checkin_res = self.client.post(reverse("check-in-patient", kwargs={"appointment_id": appointment.id}))
        self.assertEqual(checkin_res.status_code, 302)

        appointment.refresh_from_db()
        self.assertEqual(appointment.status, "CHECKED_IN")
        self.assertEqual(appointment.token.status, "WAITING")

        # 3. Doctor calls next patient
        self.client.force_login(self.user_arun)
        call_res = self.client.post(reverse("call-next-patient"))
        self.assertEqual(call_res.status_code, 302)
        self.assertRedirects(call_res, reverse("consultation-workspace", kwargs={"appointment_id": appointment.id}))

        appointment.refresh_from_db()
        self.assertEqual(appointment.status, "IN_CONSULTATION")
        self.assertEqual(appointment.token.status, "CALLED")

        # 4. Doctor completes consultation
        comp_res = self.client.post(
            reverse("complete-consultation", kwargs={"appointment_id": appointment.id}),
            {"notes": "Blood pressure 120/80 mmHg. Prescribed lifestyle modification."},
        )
        self.assertEqual(comp_res.status_code, 302)
        self.assertRedirects(comp_res, reverse("doctor-dashboard"))

        appointment.refresh_from_db()
        self.assertEqual(appointment.status, "COMPLETED")
        self.assertEqual(appointment.token.status, "COMPLETED")
        self.assertIn("Prescribed lifestyle modification", appointment.notes)


class DoctorConcurrentTokenGenerationTests(TransactionTestCase):
    """
    Tests PostgreSQL-safe concurrency handling with select_for_update.
    Ensures that concurrent token generation requests for the same doctor
    on the same date never allocate duplicate numbers.
    """

    def setUp(self):
        self.department = Department.objects.create(name="ConcurrencyDept")
        self.doc_user = User.objects.create_user(
            username="doc_concurrent",
            email="concurrent@clinichub.test",
            password="Password123!",
            first_name="Concurrency",
            last_name="Doctor",
            user_type="DOCTOR",
            doctor_role="GENERAL",
        )
        self.doctor = Doctor.objects.create(
            user=self.doc_user,
            department=self.department,
            specialization="General",
            license_number="LIC-CONCURRENT-001",
        )
        self.today = timezone.localdate()

    def test_concurrent_requests_cannot_generate_duplicate_numbers(self):
        """
        Launch concurrent requests to allocate token numbers for the same doctor on the same date.
        Verify that all generated token numbers are distinct and sequential.
        """
        num_requests = 10
        allocated_numbers = []

        def allocate_token():
            try:
                return get_next_token_number_for_doctor(self.doctor, self.today)
            finally:
                connection.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(allocate_token) for _ in range(num_requests)]
            for future in concurrent.futures.as_completed(futures):
                allocated_numbers.append(future.result())
            executor.shutdown(wait=True)

        # All numbers must be distinct
        self.assertEqual(len(allocated_numbers), num_requests)
        self.assertEqual(len(set(allocated_numbers)), num_requests, "Duplicate token numbers detected!")
        # Must be sequential 1 through num_requests
        self.assertEqual(sorted(allocated_numbers), list(range(1, num_requests + 1)))


class TimeSlotTokenBookingTests(TestCase):
    """
    Comprehensive test suite for Time-Slot-Based Token Booking System:
    1. Department & doctor filtering
    2. Time-slot generation from DoctorAvailability
    3. Token availability grid (1..20)
    4. Successful booking with slot and token selection
    5. Full time slots detection and rejection
    6. Invalid dates (past dates) and invalid doctors
    7. Duplicate token booking prevention
    8. Cancellation & token reuse without history loss
    9. Patient data isolation
    10. Receptionist check-in and queue ordering by slot
    """

    def setUp(self):
        self.department_cardio = Department.objects.create(
            name="Cardiology",
            description="Cardiology Dept",
            is_active=True,
        )
        self.department_ortho = Department.objects.create(
            name="Orthopedics",
            description="Orthopedics Dept",
            is_active=True,
        )

        # Doctor Arun (Cardiology)
        self.user_doc_arun = User.objects.create_user(
            username="dr_arun_slot",
            email="arun.slot@example.com",
            password="TestPassword123!",
            first_name="Arun",
            last_name="Kumar",
            user_type="DOCTOR",
            doctor_role="CARDIOLOGIST",
        )
        self.doctor_arun = Doctor.objects.create(
            user=self.user_doc_arun,
            department=self.department_cardio,
            specialization="Cardiology",
            license_number="LIC-SLOT-ARUN-001",
            is_active=True,
        )

        # Doctor Rahul (Cardiology)
        self.user_doc_rahul = User.objects.create_user(
            username="dr_rahul_slot",
            email="rahul.slot@example.com",
            password="TestPassword123!",
            first_name="Rahul",
            last_name="Sharma",
            user_type="DOCTOR",
            doctor_role="CARDIOLOGIST",
        )
        self.doctor_rahul = Doctor.objects.create(
            user=self.user_doc_rahul,
            department=self.department_cardio,
            specialization="Cardiology",
            license_number="LIC-SLOT-RAHUL-002",
            is_active=True,
        )

        # Inactive Doctor (should be excluded)
        self.user_doc_inactive = User.objects.create_user(
            username="dr_inactive_slot",
            email="inactive.slot@example.com",
            password="TestPassword123!",
            first_name="Inactive",
            last_name="Doctor",
            user_type="DOCTOR",
        )
        self.doctor_inactive = Doctor.objects.create(
            user=self.user_doc_inactive,
            department=self.department_cardio,
            specialization="Cardiology",
            license_number="LIC-SLOT-INACTIVE-003",
            is_active=False,
        )

        # Patients
        self.user_patient_1 = User.objects.create_user(
            username="patient_slot_1",
            email="patient1.slot@example.com",
            password="TestPassword123!",
            first_name="Aarav",
            last_name="Patel",
            user_type="PATIENT",
        )
        self.user_patient_1.is_email_verified = True
        self.user_patient_1.save()
        self.patient_1, _ = Patient.objects.get_or_create(user=self.user_patient_1)

        self.user_patient_2 = User.objects.create_user(
            username="patient_slot_2",
            email="patient2.slot@example.com",
            password="TestPassword123!",
            first_name="Diya",
            last_name="Nair",
            user_type="PATIENT",
        )
        self.user_patient_2.is_email_verified = True
        self.user_patient_2.save()
        self.patient_2, _ = Patient.objects.get_or_create(user=self.user_patient_2)

        # Calculate a future Monday for availability tests
        today = timezone.localdate()
        days_to_monday = (0 - today.weekday()) % 7
        if days_to_monday == 0:
            days_to_monday = 7
        self.future_monday = today + datetime.timedelta(days=days_to_monday)

        # Set up DoctorAvailability for Dr. Arun: Monday 09:00 - 12:00, 60min slots, 20 tokens/slot
        self.avail_arun = DoctorAvailability.objects.create(
            doctor=self.doctor_arun,
            day='MON',
            start_time=datetime.time(9, 0),
            end_time=datetime.time(12, 0),
            slot_duration=60,
            tokens_per_slot=20,
            is_active=True,
        )

    def test_department_and_doctor_filtering(self):
        """Verify get_doctors only returns active doctors belonging to selected department."""
        # Query Cardiology doctors
        response = self.client.get(reverse("get-doctors"), {"department_id": self.department_cardio.id})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        doc_names = [d["name"] for d in data["doctors"]]

        # Dr. Arun and Dr. Rahul should be present
        self.assertIn("Dr. Arun Kumar", doc_names)
        self.assertIn("Dr. Rahul Sharma", doc_names)

        # Inactive doctor should be excluded
        self.assertNotIn("Dr. Inactive Doctor", doc_names)

        # Orthopedics has no doctors yet
        resp_ortho = self.client.get(reverse("get-doctors"), {"department_id": self.department_ortho.id})
        self.assertEqual(resp_ortho.status_code, 200)
        self.assertEqual(len(resp_ortho.json()["doctors"]), 0)

    def test_slot_generation_from_doctor_availability(self):
        """Verify generate_doctor_time_slots slices availability window into configurable slots."""
        slots = generate_doctor_time_slots(self.doctor_arun, self.future_monday)
        self.assertEqual(len(slots), 3)

        # Slot 1: 09:00 - 10:00
        self.assertEqual(slots[0]["start_time"], "09:00")
        self.assertEqual(slots[0]["end_time"], "10:00")
        self.assertEqual(slots[0]["tokens_per_slot"], 20)
        self.assertEqual(slots[0]["available_count"], 20)
        self.assertFalse(slots[0]["is_full"])

        # Slot 2: 10:00 - 11:00
        self.assertEqual(slots[1]["start_time"], "10:00")
        self.assertEqual(slots[1]["end_time"], "11:00")

        # Slot 3: 11:00 - 12:00
        self.assertEqual(slots[2]["start_time"], "11:00")
        self.assertEqual(slots[2]["end_time"], "12:00")

    def test_api_available_slots_endpoint(self):
        """Verify /api/available-slots/ returns slots via JSON for front-end rendering."""
        url = reverse("api-available-slots")
        response = self.client.get(url, {
            "doctor_id": self.doctor_arun.id,
            "date": self.future_monday.isoformat(),
        })
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(len(data["slots"]), 3)
        self.assertEqual(data["doctor"]["id"], self.doctor_arun.id)

    def test_token_availability_grid_and_endpoint(self):
        """Verify tokens 1..20 are returned with correct booked vs available status."""
        slot_start = datetime.time(9, 0)
        slot_end = datetime.time(10, 0)

        # Book token #1 and #3 in slot 09:00-10:00
        book_slot_appointment(
            patient=self.patient_1,
            doctor=self.doctor_arun,
            appointment_date=self.future_monday,
            slot_start_time=slot_start,
            slot_end_time=slot_end,
            time_slot="09:00 AM – 10:00 AM",
            token_number=1,
        )
        book_slot_appointment(
            patient=self.patient_2,
            doctor=self.doctor_arun,
            appointment_date=self.future_monday,
            slot_start_time=slot_start,
            slot_end_time=slot_end,
            time_slot="09:00 AM – 10:00 AM",
            token_number=3,
        )

        # Call service
        grid = get_slot_tokens_availability(self.doctor_arun, self.future_monday, slot_start)
        self.assertEqual(grid["tokens_per_slot"], 20)
        self.assertEqual(grid["booked_count"], 2)
        self.assertEqual(grid["available_count"], 18)
        self.assertFalse(grid["is_full"])

        tokens = {t["token_number"]: t for t in grid["tokens"]}
        # Token 1 and 3 are booked
        self.assertFalse(tokens[1]["is_available"])
        self.assertEqual(tokens[1]["status"], "BOOKED")
        self.assertFalse(tokens[3]["is_available"])
        self.assertEqual(tokens[3]["status"], "BOOKED")

        # Token 2 and 4 are available
        self.assertTrue(tokens[2]["is_available"])
        self.assertEqual(tokens[2]["status"], "AVAILABLE")
        self.assertTrue(tokens[4]["is_available"])
        self.assertEqual(tokens[4]["status"], "AVAILABLE")

        # Test endpoint
        response = self.client.get(reverse("api-slot-tokens"), {
            "doctor_id": self.doctor_arun.id,
            "date": self.future_monday.isoformat(),
            "slot_start_time": "09:00",
        })
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["available_count"], 18)

    def test_independent_token_sequences_per_doctor_and_slot(self):
        """
        Token sequence rules:
        - Each time slot starts at token 1.
        - Dr. Arun slot 1 (09:00-10:00) has Token 1.
        - Dr. Arun slot 2 (10:00-11:00) has Token 1.
        - Dr. Rahul slot 1 (09:00-10:00) also has Token 1 independently.
        """
        t9 = datetime.time(9, 0)
        t10 = datetime.time(10, 0)

        # Dr. Arun Slot 09:00-10:00 -> Token 1
        apt_a1, tok_a1 = book_slot_appointment(
            patient=self.patient_1,
            doctor=self.doctor_arun,
            appointment_date=self.future_monday,
            slot_start_time=t9,
            slot_end_time=t10,
            token_number=1,
        )
        self.assertEqual(tok_a1.token_number, 1)

        # Dr. Arun Slot 10:00-11:00 -> Token 1 (different slot, starts from 1)
        apt_a2, tok_a2 = book_slot_appointment(
            patient=self.patient_2,
            doctor=self.doctor_arun,
            appointment_date=self.future_monday,
            slot_start_time=t10,
            slot_end_time=datetime.time(11, 0),
            token_number=1,
        )
        self.assertEqual(tok_a2.token_number, 1)

        # Dr. Rahul Slot 09:00-10:00 -> Token 1 (different doctor, starts from 1)
        apt_r1, tok_r1 = book_slot_appointment(
            patient=self.patient_1,
            doctor=self.doctor_rahul,
            appointment_date=self.future_monday,
            slot_start_time=t9,
            slot_end_time=t10,
            token_number=1,
        )
        self.assertEqual(tok_r1.token_number, 1)

    def test_successful_booking_via_view(self):
        """Verify patient booking flow via POST create-appointment."""
        self.client.force_login(self.user_patient_1)
        response = self.client.post(
            reverse("create-appointment"),
            {
                "department": self.department_cardio.id,
                "doctor": self.doctor_arun.id,
                "appointment_date": self.future_monday.isoformat(),
                "slot_start_time": "09:00",
                "slot_end_time": "10:00",
                "time_slot": "09:00 AM – 10:00 AM",
                "token_number": 5,
                "reason": "Chest tightness and palpitations",
            },
        )
        self.assertEqual(response.status_code, 302)

        # Verify created record
        appointment = Appointment.objects.filter(patient=self.patient_1, doctor=self.doctor_arun).first()
        self.assertIsNotNone(appointment)
        self.assertEqual(appointment.status, "BOOKED")
        self.assertEqual(appointment.slot_start_time, datetime.time(9, 0))
        self.assertEqual(appointment.time_slot, "09:00 AM – 10:00 AM")

        token = Token.objects.filter(appointment=appointment).first()
        self.assertIsNotNone(token)
        self.assertEqual(token.token_number, 5)
        self.assertEqual(token.formatted_token_number, "005")

    def test_duplicate_token_in_same_slot_rejected(self):
        """Verify double booking of same doctor, date, slot, and token_number is rejected."""
        t9 = datetime.time(9, 0)
        t10 = datetime.time(10, 0)

        # Patient 1 books Token 2
        book_slot_appointment(
            patient=self.patient_1,
            doctor=self.doctor_arun,
            appointment_date=self.future_monday,
            slot_start_time=t9,
            slot_end_time=t10,
            token_number=2,
        )

        # Patient 2 attempts to book the same Token 2
        with self.assertRaises(ValidationError):
            book_slot_appointment(
                patient=self.patient_2,
                doctor=self.doctor_arun,
                appointment_date=self.future_monday,
                slot_start_time=t9,
                slot_end_time=t10,
                token_number=2,
            )

    def test_cannot_book_past_date(self):
        """Server-side validation must reject booking dates in the past."""
        past_date = timezone.localdate() - datetime.timedelta(days=2)
        with self.assertRaises(ValidationError):
            book_slot_appointment(
                patient=self.patient_1,
                doctor=self.doctor_arun,
                appointment_date=past_date,
                slot_start_time=datetime.time(9, 0),
                slot_end_time=datetime.time(10, 0),
                token_number=1,
            )

    def test_cancellation_preserves_history_and_releases_token(self):
        """
        Verify cancellation policy:
        - Cancelled appointment and token retain status='CANCELLED' (audit history preserved).
        - The token number becomes free and can be booked by another patient without error.
        """
        t9 = datetime.time(9, 0)
        t10 = datetime.time(10, 0)

        # Patient 1 books Token 4
        apt, tok = book_slot_appointment(
            patient=self.patient_1,
            doctor=self.doctor_arun,
            appointment_date=self.future_monday,
            slot_start_time=t9,
            slot_end_time=t10,
            token_number=4,
        )

        # Patient 1 cancels
        self.client.force_login(self.user_patient_1)
        resp_cancel = self.client.post(
            reverse("cancel-appointment", kwargs={"appointment_id": apt.id}),
            {"cancellation_reason": "Schedule conflict / timing issue", "notes": "Need to travel"},
        )
        self.assertEqual(resp_cancel.status_code, 302)

        # Verify historical record is retained
        apt.refresh_from_db()
        tok.refresh_from_db()
        self.assertEqual(apt.status, "CANCELLED")
        self.assertEqual(tok.status, "CANCELLED")

        # Verify Token 4 is now available again in slot
        grid = get_slot_tokens_availability(self.doctor_arun, self.future_monday, t9)
        tok_4_info = next(t for t in grid["tokens"] if t["token_number"] == 4)
        self.assertTrue(tok_4_info["is_available"])

        # Patient 2 can now book Token 4 without conflict
        apt2, tok2 = book_slot_appointment(
            patient=self.patient_2,
            doctor=self.doctor_arun,
            appointment_date=self.future_monday,
            slot_start_time=t9,
            slot_end_time=t10,
            token_number=4,
        )
        self.assertEqual(tok2.token_number, 4)
        self.assertEqual(tok2.status, "BOOKED")

    def test_patient_data_isolation(self):
        """Ensure a patient cannot view another patient's appointment confirmation page."""
        apt, _ = book_slot_appointment(
            patient=self.patient_1,
            doctor=self.doctor_arun,
            appointment_date=self.future_monday,
            slot_start_time=datetime.time(9, 0),
            token_number=1,
        )

        # Patient 2 logs in and tries to access Patient 1's appointment confirmation
        self.client.force_login(self.user_patient_2)
        resp = self.client.get(reverse("appointment-success", kwargs={"appointment_id": apt.id}))
        # Must return 404 Not Found (isolated)
        self.assertEqual(resp.status_code, 404)

        # Patient 1 accesses own appointment -> 200 OK
        self.client.force_login(self.user_patient_1)
        resp_own = self.client.get(reverse("appointment-success", kwargs={"appointment_id": apt.id}))
        self.assertEqual(resp_own.status_code, 200)

    def test_receptionist_checkin_and_queue_slot_ordering(self):
        """
        Verify queue ordering:
        Patients with earlier slot (09:00-10:00) must be called BEFORE patients with later slot (10:00-11:00),
        even if later slot patient has smaller token number.
        """
        today = timezone.localdate()
        t9 = datetime.time(9, 0)
        t10 = datetime.time(10, 0)

        # Patient 1 in Slot 10:00-11:00 Token #1
        apt_later, tok_later = book_slot_appointment(
            patient=self.patient_1,
            doctor=self.doctor_arun,
            appointment_date=today,
            slot_start_time=t10,
            slot_end_time=datetime.time(11, 0),
            token_number=1,
        )

        # Patient 2 in Slot 09:00-10:00 Token #2
        apt_earlier, tok_earlier = book_slot_appointment(
            patient=self.patient_2,
            doctor=self.doctor_arun,
            appointment_date=today,
            slot_start_time=t9,
            slot_end_time=t10,
            token_number=2,
        )

        # Check-in both patients
        tok_later.status = "WAITING"
        tok_later.save()
        tok_earlier.status = "WAITING"
        tok_earlier.save()

        # Doctor calls next patient
        called_tok, called_apt = call_next_patient_for_doctor(self.doctor_arun, today)

        # Earlier slot patient (09:00-10:00 Token #2) must be called first!
        self.assertEqual(called_tok.id, tok_earlier.id)
        self.assertEqual(called_tok.token_number, 2)
        self.assertEqual(called_tok.slot_start_time, t9)


class TimeSlotConcurrentBookingTests(TransactionTestCase):
    """
    Tests database concurrency and PostgreSQL row-locking protection:
    Multiple threads attempting to book the exact same token simultaneously.
    """

    def setUp(self):
        self.department = Department.objects.create(name="Cardiology", is_active=True)
        self.doc_user = User.objects.create_user(
            username="doc_concurrent_slot",
            email="doc.conc@example.com",
            password="TestPassword123!",
            first_name="Doctor",
            last_name="Concurrent",
            user_type="DOCTOR",
        )
        self.doctor = Doctor.objects.create(
            user=self.doc_user,
            department=self.department,
            specialization="Cardiology",
            license_number="LIC-CONC-SLOT-001",
            is_active=True,
        )
        self.user_p1 = User.objects.create_user(username="cp1", email="cp1@test.com", password="pass")
        self.p1, _ = Patient.objects.get_or_create(user=self.user_p1)
        self.user_p2 = User.objects.create_user(username="cp2", email="cp2@test.com", password="pass")
        self.p2, _ = Patient.objects.get_or_create(user=self.user_p2)

        self.today = timezone.localdate()

    def test_concurrent_same_token_booking_only_one_succeeds(self):
        """
        Two concurrent requests attempt to reserve the exact same token in the same slot.
        Exactly one must succeed, and the other must fail with ValidationError or IntegrityError.
        No duplicate active token is ever created in the database.
        """
        slot_start = datetime.time(9, 0)
        slot_end = datetime.time(10, 0)
        token_to_book = 7

        results = []

        def attempt_booking(patient):
            try:
                apt, tok = book_slot_appointment(
                    patient=patient,
                    doctor=self.doctor,
                    appointment_date=self.today,
                    slot_start_time=slot_start,
                    slot_end_time=slot_end,
                    time_slot="09:00 AM – 10:00 AM",
                    token_number=token_to_book,
                )
                return ("SUCCESS", tok.id)
            except (ValidationError, IntegrityError) as e:
                return ("FAILED", str(e))
            finally:
                connection.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            f1 = executor.submit(attempt_booking, self.p1)
            f2 = executor.submit(attempt_booking, self.p2)
            results.append(f1.result())
            results.append(f2.result())

        statuses = [r[0] for r in results]
        self.assertEqual(statuses.count("SUCCESS"), 1, f"Expected exactly 1 success, got: {results}")
        self.assertEqual(statuses.count("FAILED"), 1, f"Expected exactly 1 failure, got: {results}")

        # Verify only 1 token exists in DB for this slot and number
        active_tokens = Token.objects.filter(
            doctor=self.doctor,
            token_date=self.today,
            slot_start_time=slot_start,
            token_number=token_to_book,
            is_deleted=False,
        ).exclude(status="CANCELLED")
        self.assertEqual(active_tokens.count(), 1)


class Phase3StaffReceptionWorkflowTests(TestCase):
    """
    Test suite for Phase 3: Staff & Receptionist Front-Desk Workflows.
    Validates staff dashboard metrics, patient search directory, direct patient
    registration without email verification, receptionist check-in, and staff-assisted
    rescheduling and cancellation with audit logging.
    """

    def setUp(self):
        self.today = timezone.localdate()
        self.dept = Department.objects.create(name="Cardiology", description="Heart care")

        # Create Doctor
        self.doc_user = User.objects.create_user(
            username="dr_house",
            email="house@clinichub.test",
            password="password123",
            user_type="DOCTOR",
            doctor_role="CARDIOLOGIST",
            first_name="Gregory",
            last_name="House",
        )
        self.doc_user.is_email_verified = True
        self.doc_user.save()
        self.doctor = Doctor.objects.create(
            user=self.doc_user,
            department=self.dept,
            specialization="Cardiologist",
            license_number="LIC-HSE-001",
        )

        DoctorAvailability.objects.create(
            doctor=self.doctor,
            day="MON",
            start_time=datetime.time(9, 0),
            end_time=datetime.time(13, 0),
            slot_duration=60,
            tokens_per_slot=20,
        )

        # Create Patient 1
        self.patient_user1 = User.objects.create_user(
            username="john_doe",
            email="john@example.com",
            password="password123",
            user_type="PATIENT",
            first_name="John",
            last_name="Doe",
            phone_number="5551234",
        )
        self.patient_user1.is_email_verified = True
        self.patient_user1.save()
        self.p1 = self.patient_user1.patient_profile
        self.p1.blood_group = "O+"
        self.p1.save()

        # Create Patient 2
        self.patient_user2 = User.objects.create_user(
            username="jane_smith",
            email="jane@example.com",
            password="password123",
            user_type="PATIENT",
            first_name="Jane",
            last_name="Smith",
            phone_number="5555678",
        )
        self.patient_user2.is_email_verified = True
        self.patient_user2.save()
        self.p2 = self.patient_user2.patient_profile
        self.p2.blood_group = "A+"
        self.p2.save()

        # Create Staff Receptionist
        self.staff_user = User.objects.create_user(
            username="receptionist_mary",
            email="mary@clinichub.test",
            password="password123",
            user_type="STAFF",
            staff_role="RECEPTIONIST",
            first_name="Mary",
            last_name="Watson",
        )
        self.staff_user.is_email_verified = True
        self.staff_user.save()
        self.staff = Staff.objects.create(
            user=self.staff_user,
            department=self.dept,
            designation="Lead Receptionist",
        )

        # Sample Appointment for today
        self.sample_appt = Appointment.objects.create(
            patient=self.p1,
            doctor=self.doctor,
            appointment_date=self.today,
            appointment_time=datetime.time(9, 0),
            slot_start_time=datetime.time(9, 0),
            slot_end_time=datetime.time(10, 0),
            time_slot="09:00 AM – 10:00 AM",
            status="BOOKED",
            reason="Chest palpitations",
        )
        self.sample_token = Token.objects.create(
            appointment=self.sample_appt,
            doctor=self.doctor,
            token_date=self.today,
            token_number=1,
            slot_start_time=datetime.time(9, 0),
            slot_end_time=datetime.time(10, 0),
            time_slot="09:00 AM – 10:00 AM",
            status="BOOKED",
        )

    def test_staff_dashboard_access_and_metrics(self):
        """Staff can view dashboard with real-time metrics; Patients are rejected."""
        # Patient access denied
        self.client.login(email="john@example.com", password="password123")
        res_patient = self.client.get(reverse("staff-dashboard"))
        self.assertEqual(res_patient.status_code, 403)
        self.client.logout()

        # Staff access allowed
        self.client.login(email="mary@clinichub.test", password="password123")
        res_staff = self.client.get(reverse("staff-dashboard"))
        self.assertEqual(res_staff.status_code, 200)
        self.assertTemplateUsed(res_staff, "clinic/staff/staff_dashboard.html")

        # Verify context metrics
        context = res_staff.context
        self.assertEqual(context["total_today"], 1)
        self.assertEqual(context["pending_count"], 1)
        self.assertEqual(context["waiting_count"], 0)
        self.assertEqual(context["completed_count"], 0)
        self.assertEqual(len(context["doctor_summaries"]), 1)
        self.assertEqual(context["doctor_summaries"][0]["doctor"], self.doctor)

    def test_staff_patient_search_by_name_phone_and_blood(self):
        """Staff can search patients by name, phone, or blood group and inspect history."""
        self.client.login(email="mary@clinichub.test", password="password123")

        # Search by name
        res_name = self.client.get(reverse("staff-patient-search"), {"q": "John"})
        self.assertEqual(res_name.status_code, 200)
        self.assertEqual(res_name.context["total_results"], 1)
        self.assertEqual(res_name.context["patients_data"][0]["patient"], self.p1)

        # Search by phone number
        res_phone = self.client.get(reverse("staff-patient-search"), {"q": "5555678"})
        self.assertEqual(res_phone.status_code, 200)
        self.assertEqual(res_phone.context["total_results"], 1)
        self.assertEqual(res_phone.context["patients_data"][0]["patient"], self.p2)

        # Search by blood group
        res_blood = self.client.get(reverse("staff-patient-search"), {"q": "O+"})
        self.assertEqual(res_blood.status_code, 200)
        self.assertTrue(any(item["patient"] == self.p1 for item in res_blood.context["patients_data"]))

        # View specific patient detail file
        res_detail = self.client.get(reverse("staff-patient-search"), {"patient_id": str(self.p1.id)})
        self.assertEqual(res_detail.status_code, 200)
        self.assertEqual(res_detail.context["selected_patient"], self.p1)
        self.assertEqual(len(res_detail.context["patient_history"]), 1)

    def test_staff_register_patient_instant_verification(self):
        """Receptionist registers a new patient without email verification."""
        self.client.login(email="mary@clinichub.test", password="password123")

        data = {
            "first_name": "Robert",
            "last_name": "Plant",
            "phone_number": "5559876",
            "email": "robert@rock.test",
            "date_of_birth": "1990-05-15",
            "gender": "MALE",
            "blood_group": "B+",
            "emergency_contact_name": "Jimmy Page",
            "emergency_contact_phone": "5550000",
            "address": "42 Stairway Way",
            "action": "save",
        }

        res = self.client.post(reverse("staff-register-patient"), data)
        self.assertEqual(res.status_code, 302)

        user = User.objects.get(email="robert@rock.test")
        self.assertEqual(user.first_name, "Robert")
        self.assertEqual(user.user_type, "PATIENT")
        self.assertTrue(user.is_email_verified)
        self.assertTrue(hasattr(user, "patient_profile"))
        self.assertEqual(user.patient_profile.blood_group, "B+")
        self.assertEqual(user.patient_profile.emergency_contact_name, "Jimmy Page")

    def test_staff_cancel_appointment_audit_trail(self):
        """Staff can cancel an appointment and record reception audit log in notes."""
        self.client.login(email="mary@clinichub.test", password="password123")

        cancel_data = {
            "cancellation_reason": "Patient requested cancellation (phone/in-person)",
            "notes": "Patient called saying they are stuck in traffic.",
        }

        res = self.client.post(reverse("staff-cancel-appointment", args=[self.sample_appt.id]), cancel_data)
        self.assertEqual(res.status_code, 302)

        self.sample_appt.refresh_from_db()
        self.sample_token.refresh_from_db()

        self.assertEqual(self.sample_appt.status, "CANCELLED")
        self.assertEqual(self.sample_token.status, "CANCELLED")
        self.assertIn("Cancelled by Reception Staff Mary Watson", self.sample_appt.notes)
        self.assertIn("Patient called saying they are stuck in traffic", self.sample_appt.notes)

    def test_staff_reschedule_appointment(self):
        """Staff can reschedule an appointment for a patient to another date/slot."""
        self.client.login(email="mary@clinichub.test", password="password123")

        new_date = self.today + datetime.timedelta(days=2)
        reschedule_data = {
            "department": self.dept.id,
            "doctor": self.doctor.id,
            "appointment_date": new_date.isoformat(),
            "appointment_time": "10:00",
            "slot_start_time": "10:00",
            "slot_end_time": "11:00",
            "time_slot": "10:00 AM – 11:00 AM",
            "token_number": 2,
            "reason": "Rescheduled follow-up",
        }

        res = self.client.post(reverse("staff-reschedule-appointment", args=[self.sample_appt.id]), reschedule_data)
        self.assertEqual(res.status_code, 302)

        self.sample_appt.refresh_from_db()
        self.sample_token.refresh_from_db()

        self.assertEqual(self.sample_appt.appointment_date, new_date)
        self.assertEqual(self.sample_token.token_date, new_date)
        self.assertEqual(self.sample_token.token_number, 2)
        self.assertIn("Rescheduled by Reception Staff Mary Watson", self.sample_appt.notes)

    def test_today_appointments_filtering(self):
        """Reception desk table filters by status, doctor, and search query."""
        self.client.login(email="mary@clinichub.test", password="password123")

        # Default: shows BOOKED appointments
        res_default = self.client.get(reverse("today-appointments"))
        self.assertEqual(res_default.status_code, 200)
        self.assertEqual(len(res_default.context["appointments"]), 1)

        # Filter by CHECKED_IN: currently 0
        res_checked_in = self.client.get(reverse("today-appointments"), {"status": "CHECKED_IN"})
        self.assertEqual(res_checked_in.status_code, 200)
        self.assertEqual(len(res_checked_in.context["appointments"]), 0)

        # Check-in patient
        self.client.post(reverse("check-in-patient", args=[self.sample_appt.id]))

        # Filter by CHECKED_IN now shows 1
        res_checked_in_after = self.client.get(reverse("today-appointments"), {"status": "CHECKED_IN"})
        self.assertEqual(len(res_checked_in_after.context["appointments"]), 1)

        # Filter by search query
        res_search = self.client.get(reverse("today-appointments"), {"status": "ALL", "search": "John"})
        self.assertEqual(len(res_search.context["appointments"]), 1)

        res_search_miss = self.client.get(reverse("today-appointments"), {"status": "ALL", "search": "NonExistent"})
        self.assertEqual(len(res_search_miss.context["appointments"]), 0)

    def test_check_in_patient_preserves_next_url(self):
        """Check-in respects next redirect parameter so receptionists stay on their working view."""
        self.client.login(email="mary@clinichub.test", password="password123")
        target_next = reverse("today-appointments") + "?status=BOOKED"
        res = self.client.post(
            reverse("check-in-patient", args=[self.sample_appt.id]),
            {"next": target_next},
        )
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res.url, target_next)

    def test_staff_patient_search_includes_today_in_context(self):
        """Staff patient search must pass today in context for appointment check-in comparison."""
        self.client.login(email="mary@clinichub.test", password="password123")
        res = self.client.get(reverse("staff-patient-search"), {"patient_id": str(self.p1.id)})
        self.assertEqual(res.status_code, 200)
        self.assertIn("today", res.context)
        self.assertEqual(res.context["today"], timezone.localdate())

    def test_staff_register_patient_action_redirects_with_proper_prefix(self):
        """Staff patient registration correctly redirects to walk-in with reverse url and patient id."""
        self.client.login(email="mary@clinichub.test", password="password123")
        data = {
            "first_name": "Freddie",
            "last_name": "Mercury",
            "phone_number": "5558888",
            "email": "freddie@queen.test",
            "date_of_birth": "1946-09-05",
            "gender": "MALE",
            "blood_group": "O+",
            "emergency_contact_name": "Brian May",
            "emergency_contact_phone": "5551111",
            "action": "book_walkin",
        }
        res = self.client.post(reverse("staff-register-patient"), data)
        self.assertEqual(res.status_code, 302)
        patient = Patient.objects.get(user__email="freddie@queen.test")
        expected_url = f"{reverse('walk-in-booking')}?patient_id={patient.id}"
        self.assertEqual(res.url, expected_url)
        self.assertTrue(expected_url.startswith("/clinic/walk-in/"))


# --------------------------------
# PHASE 4: ADMIN PORTAL & MANAGEMENT TESTS
# --------------------------------

class Phase4AdminManagementTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()

        # Admin user
        self.user_admin = User.objects.create_user(
            username="super_admin",
            email="admin@clinichub.test",
            password="adminpassword123",
            first_name="Director",
            last_name="Fury",
            user_type="ADMIN",
        )
        self.user_admin.is_staff = True
        self.user_admin.is_email_verified = True
        self.user_admin.save()

        # Regular Patient user
        self.user_patient = User.objects.create_user(
            username="test_patient",
            email="patient@test.com",
            password="password123",
            first_name="Jane",
            last_name="Doe",
            user_type="PATIENT",
        )
        self.user_patient.is_email_verified = True
        self.user_patient.save()
        self.patient = self.user_patient.patient_profile

        # Department
        self.dept_cardio = Department.objects.create(
            name="Cardiology",
            description="Heart and vascular specialty division",
            is_active=True,
        )

        # Doctor
        self.user_doc = User.objects.create_user(
            username="dr_house",
            email="house@clinichub.test",
            password="password123",
            first_name="Gregory",
            last_name="House",
            user_type="DOCTOR",
            doctor_role="GENERAL",
        )
        self.user_doc.is_email_verified = True
        self.user_doc.save()

        self.doctor = Doctor.objects.create(
            user=self.user_doc,
            department=self.dept_cardio,
            specialization="Diagnostics & Nephrology",
            license_number="LIC-HOUSE-001",
            is_active=True,
        )

        # Staff
        self.user_staff = User.objects.create_user(
            username="staff_clara",
            email="clara@clinichub.test",
            password="password123",
            first_name="Clara",
            last_name="Oswald",
            user_type="STAFF",
            staff_role="RECEPTIONIST",
        )
        self.user_staff.is_email_verified = True
        self.user_staff.save()

        self.staff_member = Staff.objects.create(
            user=self.user_staff,
            department=self.dept_cardio,
            designation="Front-Desk Reception Lead",
            is_active=True,
        )

        # Sample Appointment
        self.appt = Appointment.objects.create(
            patient=self.patient,
            doctor=self.doctor,
            appointment_date=self.today,
            appointment_time=timezone.now().time(),
            status="COMPLETED",
            reason="Chest evaluation",
        )

    def test_admin_dashboard_metrics_and_access(self):
        """Admin dashboard renders metrics and restricts unauthorized users."""
        # Non-admin access rejected
        self.client.login(email="patient@test.com", password="password123")
        res_forbidden = self.client.get(reverse("admin-dashboard"))
        self.assertEqual(res_forbidden.status_code, 403)

        # Admin access authorized
        self.client.login(email="admin@clinichub.test", password="adminpassword123")
        res = self.client.get(reverse("admin-dashboard"))
        self.assertEqual(res.status_code, 200)

        # Check context metrics
        self.assertEqual(res.context["total_patients"], 1)
        self.assertEqual(res.context["total_doctors"], 1)
        self.assertEqual(res.context["active_doctors"], 1)
        self.assertEqual(res.context["active_staff"], 1)
        self.assertEqual(res.context["total_departments"], 1)
        self.assertEqual(res.context["today_completed"], 1)
        self.assertEqual(len(res.context["dept_stats"]), 1)
        self.assertEqual(len(res.context["daily_trend"]), 7)

    def test_admin_doctor_crud_and_toggle(self):
        """Admin can list, onboard, edit, and toggle doctors."""
        self.client.login(email="admin@clinichub.test", password="adminpassword123")

        # 1. List
        res_list = self.client.get(reverse("admin-doctors"))
        self.assertEqual(res_list.status_code, 200)
        self.assertEqual(len(res_list.context["doctors"]), 1)

        # 2. Create New Doctor
        res_create = self.client.post(reverse("admin-doctor-create"), {
            "first_name": "Stephen",
            "last_name": "Strange",
            "email": "strange@clinichub.test",
            "phone_number": "555-0999",
            "password": "magicpassword123",
            "department": self.dept_cardio.id,
            "specialization": "Neurosurgery",
            "doctor_role": "GENERAL",
            "license_number": "LIC-STRANGE-999",
            "is_active": True,
        })
        self.assertEqual(res_create.status_code, 302)
        created_doc = Doctor.objects.get(license_number="LIC-STRANGE-999")
        self.assertEqual(created_doc.user.first_name, "Stephen")
        self.assertEqual(created_doc.specialization, "Neurosurgery")
        self.assertTrue(created_doc.user.is_email_verified)

        # 3. Edit Doctor
        res_edit = self.client.post(reverse("admin-doctor-edit", args=[created_doc.id]), {
            "first_name": "Stephen",
            "last_name": "Strange",
            "email": "strange@clinichub.test",
            "phone_number": "555-1111",
            "department": self.dept_cardio.id,
            "specialization": "Chief of Neurosurgery",
            "doctor_role": "GENERAL",
            "license_number": "LIC-STRANGE-999",
            "is_active": True,
        })
        self.assertEqual(res_edit.status_code, 302)
        created_doc.refresh_from_db()
        self.assertEqual(created_doc.specialization, "Chief of Neurosurgery")

        # 4. Toggle Active Status
        res_toggle = self.client.post(reverse("admin-doctor-toggle", args=[created_doc.id]))
        self.assertEqual(res_toggle.status_code, 302)
        created_doc.refresh_from_db()
        self.assertFalse(created_doc.is_active)
        self.assertFalse(created_doc.user.is_active)

    def test_admin_staff_crud_and_toggle(self):
        """Admin can list, register, edit, and toggle clinic staff."""
        self.client.login(email="admin@clinichub.test", password="adminpassword123")

        # 1. List
        res_list = self.client.get(reverse("admin-staff"))
        self.assertEqual(res_list.status_code, 200)
        self.assertEqual(len(res_list.context["staff_members"]), 1)

        # 2. Register Staff Member
        res_create = self.client.post(reverse("admin-staff-create"), {
            "first_name": "Florence",
            "last_name": "Nightingale",
            "email": "florence@clinichub.test",
            "phone_number": "555-0888",
            "password": "nurserolepass123",
            "department": self.dept_cardio.id,
            "designation": "Senior Ward Nurse",
            "staff_role": "NURSE",
            "is_active": True,
        })
        self.assertEqual(res_create.status_code, 302)
        new_staff = Staff.objects.get(user__email="florence@clinichub.test")
        self.assertEqual(new_staff.designation, "Senior Ward Nurse")
        self.assertEqual(new_staff.user.staff_role, "NURSE")

        # 3. Edit Staff Member
        res_edit = self.client.post(reverse("admin-staff-edit", args=[new_staff.id]), {
            "first_name": "Florence",
            "last_name": "Nightingale",
            "email": "florence@clinichub.test",
            "phone_number": "555-0888",
            "department": self.dept_cardio.id,
            "designation": "Head of Clinical Nursing",
            "staff_role": "NURSE",
            "is_active": True,
        })
        self.assertEqual(res_edit.status_code, 302)
        new_staff.refresh_from_db()
        self.assertEqual(new_staff.designation, "Head of Clinical Nursing")

        # 4. Toggle Active Status
        res_toggle = self.client.post(reverse("admin-staff-toggle", args=[new_staff.id]))
        self.assertEqual(res_toggle.status_code, 302)
        new_staff.refresh_from_db()
        self.assertFalse(new_staff.is_active)
        self.assertFalse(new_staff.user.is_active)

    def test_admin_department_crud_and_toggle(self):
        """Admin can list, create, edit, and toggle clinical departments."""
        self.client.login(email="admin@clinichub.test", password="adminpassword123")

        # 1. List
        res_list = self.client.get(reverse("admin-departments"))
        self.assertEqual(res_list.status_code, 200)

        # 2. Create Department
        res_create = self.client.post(reverse("admin-department-create"), {
            "name": "Neurology",
            "description": "Brain and nervous system disorders",
            "is_active": True,
        })
        self.assertEqual(res_create.status_code, 302)
        neuro = Department.objects.get(name="Neurology")
        self.assertEqual(neuro.description, "Brain and nervous system disorders")

        # 3. Edit Department
        res_edit = self.client.post(reverse("admin-department-edit", args=[neuro.id]), {
            "name": "Neurology & Neurosurgery",
            "description": "Advanced neurological and surgical care",
            "is_active": True,
        })
        self.assertEqual(res_edit.status_code, 302)
        neuro.refresh_from_db()
        self.assertEqual(neuro.name, "Neurology & Neurosurgery")

        # 4. Toggle Department
        res_toggle = self.client.post(reverse("admin-department-toggle", args=[neuro.id]))
        self.assertEqual(res_toggle.status_code, 302)
        neuro.refresh_from_db()
        self.assertFalse(neuro.is_active)

    def test_admin_reports_analytics(self):
        """Admin reports view calculates throughput and metrics across timeframes."""
        self.client.login(email="admin@clinichub.test", password="adminpassword123")

        # 7-day interval
        res_7d = self.client.get(reverse("admin-reports"), {"timeframe": "7d"})
        self.assertEqual(res_7d.status_code, 200)
        self.assertEqual(res_7d.context["total_volume"], 1)
        self.assertEqual(res_7d.context["completed_count"], 1)
        self.assertEqual(res_7d.context["completion_rate"], 100.0)
        self.assertEqual(len(res_7d.context["doctor_performance"]), 1)
        self.assertEqual(len(res_7d.context["department_stats"]), 1)

        # All-time interval
        res_all = self.client.get(reverse("admin-reports"), {"timeframe": "all"})
        self.assertEqual(res_all.status_code, 200)
        self.assertEqual(res_all.context["total_volume"], 1)

    def test_admin_login_redirect(self):
        """Admin users logging in are redirected directly to admin-dashboard."""
        res_login = self.client.post(reverse("login"), {
            "email": "admin@clinichub.test",
            "password": "adminpassword123",
        })
        self.assertEqual(res_login.status_code, 302)
        self.assertRedirects(res_login, reverse("admin-dashboard"))


# ========================================================
# PHASE 5: MEDICAL RECORDS (EMR LITE) TESTS
# ========================================================

class Phase5MedicalRecordsTests(TestCase):
    """
    Test suite covering EMR Lite features:
    - Vitals recording and BMI calculation
    - Enduring medical history & allergies profiling
    - Consultation workspace: clinical examination, diagnosis, dynamic prescription items, lab orders
    - Consultation draft save vs final completion
    - Prescription print view permissions and rendering
    - Patient prescriptions portal & detailed medicine schedule
    - Laboratory order tracking and status workflow
    """

    def setUp(self):
        self.department = Department.objects.create(
            name="Internal Medicine",
            description="General and internal medical care",
        )

        # 1. Doctor
        self.doctor_user = User.objects.create_user(
            email="dr.smith@clinichub.test",
            username="dr_smith",
            password="testpassword123",
            first_name="John",
            last_name="Smith",
            user_type="DOCTOR",
            doctor_role="GENERAL",
            is_active=True,
            is_email_verified=True,
        )
        self.doctor = Doctor.objects.create(
            user=self.doctor_user,
            department=self.department,
            specialization="Consultant Physician",
            license_number="LIC-MED-9988",
            is_active=True,
        )

        # 2. Staff / Nurse
        self.staff_user = User.objects.create_user(
            email="nurse.mary@clinichub.test",
            username="nurse_mary",
            password="testpassword123",
            first_name="Mary",
            last_name="Jenkins",
            user_type="STAFF",
            staff_role="NURSE",
            is_active=True,
            is_email_verified=True,
        )
        self.staff = Staff.objects.create(
            user=self.staff_user,
            department=self.department,
            designation="Triage Nurse",
            is_active=True,
        )

        # 3. Patients
        self.patient_user = User.objects.create_user(
            email="patient.alice@clinichub.test",
            username="patient_alice",
            password="testpassword123",
            first_name="Alice",
            last_name="Walker",
            user_type="PATIENT",
            is_active=True,
            is_email_verified=True,
        )
        self.patient = self.patient_user.patient_profile
        self.patient.date_of_birth = datetime.date(1992, 5, 14)
        self.patient.gender = "FEMALE"
        self.patient.blood_group = "A+"
        self.patient.address = "42 Elm Street"
        self.patient.is_active = True
        self.patient.save()

        self.other_patient_user = User.objects.create_user(
            email="patient.bob@clinichub.test",
            username="patient_bob",
            password="testpassword123",
            first_name="Bob",
            last_name="Taylor",
            user_type="PATIENT",
            is_active=True,
            is_email_verified=True,
        )
        self.other_patient = self.other_patient_user.patient_profile
        self.other_patient.date_of_birth = datetime.date(1985, 8, 20)
        self.other_patient.gender = "MALE"
        self.other_patient.blood_group = "O+"
        self.other_patient.is_active = True
        self.other_patient.save()

        self.today = timezone.localdate()

    def test_record_patient_vitals_and_bmi(self):
        """Staff/Nurse can record vitals for a patient and BMI is computed accurately."""
        self.client.login(email="nurse.mary@clinichub.test", password="testpassword123")

        url = reverse("record-patient-vitals", args=[self.patient.id])
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)

        # Post vitals: weight 70kg, height 175cm -> BMI = 70 / (1.75^2) = 22.86 -> 22.9
        post_data = {
            "systolic_bp": 120,
            "diastolic_bp": 80,
            "pulse_rate": 72,
            "temperature": 98.6,
            "weight_kg": 70.0,
            "height_cm": 175.0,
            "spo2": 99,
            "respiratory_rate": 16,
            "notes": "Patient calm, rested 5 mins.",
        }
        post_res = self.client.post(url, post_data)
        self.assertEqual(post_res.status_code, 302)

        vital = PatientVital.objects.filter(patient=self.patient).first()
        self.assertIsNotNone(vital)
        self.assertEqual(vital.systolic_bp, 120)
        self.assertEqual(vital.diastolic_bp, 80)
        self.assertEqual(vital.blood_pressure_display, "120/80 mmHg")
        self.assertEqual(vital.pulse_rate, 72)
        self.assertEqual(vital.bmi, 22.9)
        self.assertEqual(vital.recorded_by, self.staff_user)

    def test_patient_medical_history_and_allergies(self):
        """Physician or staff can update patient's medical history and allergy profile."""
        self.client.login(email="dr.smith@clinichub.test", password="testpassword123")

        url = reverse("edit-patient-medical-history", args=[self.patient.id])
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)

        post_data = {
            "blood_group": "AB+",
            "allergies": "Penicillin, Sulfa antibiotics",
            "chronic_conditions": "Mild Hypertension",
            "past_surgeries": "Appendectomy (2018)",
            "family_medical_history": "Maternal Type 2 Diabetes",
            "current_medications": "Amlodipine 5mg OD",
        }
        post_res = self.client.post(url, post_data)
        self.assertEqual(post_res.status_code, 302)

        history = PatientMedicalHistory.objects.get(patient=self.patient)
        self.assertEqual(history.allergies, "Penicillin, Sulfa antibiotics")
        self.assertEqual(history.chronic_conditions, "Mild Hypertension")

        # Patient's blood group is also kept synchronized
        self.patient.refresh_from_db()
        self.assertEqual(self.patient.blood_group, "AB+")

    def test_consultation_workspace_emr_flow(self):
        """Doctor completes consultation with diagnosis, dynamic prescription items, and lab tests."""
        self.client.login(email="dr.smith@clinichub.test", password="testpassword123")

        appointment = Appointment.objects.create(
            patient=self.patient,
            doctor=self.doctor,
            appointment_date=self.today,
            appointment_time=datetime.time(10, 0),
            status="IN_CONSULTATION",
            reason="High fever and persistent sore throat",
        )
        token = Token.objects.create(
            appointment=appointment,
            doctor=self.doctor,
            token_number=1,
            token_date=self.today,
            status="CALLED",
        )

        workspace_url = reverse("consultation-workspace", args=[appointment.id])
        ws_res = self.client.get(workspace_url)
        self.assertEqual(ws_res.status_code, 200)
        self.assertIn("record_form", ws_res.context)
        self.assertIn("prescription_form", ws_res.context)

        # Complete consultation with EMR payload
        complete_url = reverse("complete-consultation", args=[appointment.id])
        post_data = {
            "action": "complete",
            "record-chief_complaints": "Sore throat and fever for 3 days",
            "record-clinical_findings": "Bilateral tonsillar hypertrophy with exudates",
            "record-diagnosis": "Acute Streptococcal Pharyngitis",
            "record-treatment_plan": "10-day oral antibiotic course, saline gargles",
            "record-follow_up_date": (self.today + datetime.timedelta(days=7)).strftime("%Y-%m-%d"),
            "rx-general_advice": "Drink warm fluids, rest voice, complete the entire antibiotic course.",
            "rx-follow_up_date": (self.today + datetime.timedelta(days=7)).strftime("%Y-%m-%d"),
            # Dynamic Medication Rows
            "medicine_name[]": ["Amoxicillin 500mg", "Paracetamol 650mg", "Cetirizine 10mg"],
            "dosage[]": ["500mg", "650mg", "10mg"],
            "frequency[]": ["1-0-1 (BD)", "SOS", "0-0-1 (Night)"],
            "duration[]": ["7 days", "3 days", "5 days"],
            "timing[]": ["After meals", "After meals", "At bedtime"],
            "instructions[]": ["With full glass of water", "If temp > 100°F", "May cause drowsiness"],
            # Quick Lab Orders
            "quick_lab_tests[]": ["Complete Blood Count (CBC)"],
            # Quick Vitals
            "vital-systolic_bp": "118",
            "vital-diastolic_bp": "78",
            "vital-pulse_rate": "76",
            "vital-temperature": "100.2",
            "vital-weight_kg": "68.5",
        }

        post_res = self.client.post(complete_url, post_data)

        # Prescription was created with items, so redirects to printable Rx view
        prescription = Prescription.objects.get(appointment=appointment)
        self.assertRedirects(post_res, reverse("prescription-print", args=[prescription.id]))

        # Verify MedicalRecord
        record = MedicalRecord.objects.get(appointment=appointment)
        self.assertEqual(record.diagnosis, "Acute Streptococcal Pharyngitis")
        self.assertEqual(record.patient, self.patient)
        self.assertEqual(record.doctor, self.doctor)

        # Verify Prescription & PrescriptionItems
        self.assertEqual(prescription.items.count(), 3)
        item_names = list(prescription.items.values_list("medicine_name", flat=True))
        self.assertIn("Amoxicillin 500mg", item_names)
        self.assertIn("Paracetamol 650mg", item_names)

        # Verify LabTestOrder
        lab = LabTestOrder.objects.filter(appointment=appointment).first()
        self.assertIsNotNone(lab)
        self.assertEqual(lab.test_name, "Complete Blood Count (CBC)")
        self.assertEqual(lab.status, "ORDERED")

        # Verify Vital Signs recorded
        vital = PatientVital.objects.filter(appointment=appointment).first()
        self.assertIsNotNone(vital)
        self.assertEqual(vital.systolic_bp, 118)

        # Verify Appointment & Token status are COMPLETED
        appointment.refresh_from_db()
        token.refresh_from_db()
        self.assertEqual(appointment.status, "COMPLETED")
        self.assertEqual(token.status, "COMPLETED")

    def test_consultation_save_draft(self):
        """Doctor can save draft notes without closing or completing the appointment."""
        self.client.login(email="dr.smith@clinichub.test", password="testpassword123")

        appointment = Appointment.objects.create(
            patient=self.patient,
            doctor=self.doctor,
            appointment_date=self.today,
            appointment_time=datetime.time(11, 0),
            status="IN_CONSULTATION",
        )

        complete_url = reverse("complete-consultation", args=[appointment.id])
        post_data = {
            "action": "save_draft",
            "record-chief_complaints": "Preliminary evaluation for fatigue",
            "record-diagnosis": "Under Investigation",
            "medicine_name[]": ["Multivitamin"],
            "dosage[]": ["1 tab"],
            "frequency[]": ["OD"],
            "duration[]": ["10 days"],
        }
        res = self.client.post(complete_url, post_data)
        self.assertRedirects(res, reverse("consultation-workspace", args=[appointment.id]))

        appointment.refresh_from_db()
        self.assertEqual(appointment.status, "IN_CONSULTATION")  # Still in progress

        record = MedicalRecord.objects.get(appointment=appointment)
        self.assertEqual(record.diagnosis, "Under Investigation")

    def test_prescription_print_permissions(self):
        """Only attending clinical staff or the prescribed patient can access prescription print."""
        appointment = Appointment.objects.create(
            patient=self.patient,
            doctor=self.doctor,
            appointment_date=self.today,
            appointment_time=datetime.time(14, 0),
            status="COMPLETED",
        )
        record = MedicalRecord.objects.create(
            appointment=appointment,
            patient=self.patient,
            doctor=self.doctor,
            chief_complaints="Cough",
            diagnosis="Bronchitis",
        )
        prescription = Prescription.objects.create(
            appointment=appointment,
            patient=self.patient,
            doctor=self.doctor,
            medical_record=record,
        )
        PrescriptionItem.objects.create(
            prescription=prescription,
            medicine_name="Azithromycin 500mg",
            dosage="500mg",
            frequency="OD",
            duration="3 days",
        )

        print_url = reverse("prescription-print", args=[prescription.id])

        # 1. Attending Doctor -> 200 OK
        self.client.login(email="dr.smith@clinichub.test", password="testpassword123")
        res_doc = self.client.get(print_url)
        self.assertEqual(res_doc.status_code, 200)
        self.assertContains(res_doc, "Azithromycin 500mg")
        self.assertContains(res_doc, "Bronchitis")

        # 2. Patient Alice (prescribed to her) -> 200 OK
        self.client.login(email="patient.alice@clinichub.test", password="testpassword123")
        res_alice = self.client.get(print_url)
        self.assertEqual(res_alice.status_code, 200)
        self.assertContains(res_alice, "ClinicHub Medical Center")

        # 3. Patient Bob (another patient) -> 403 Forbidden
        self.client.login(email="patient.bob@clinichub.test", password="testpassword123")
        res_bob = self.client.get(print_url)
        self.assertEqual(res_bob.status_code, 403)

    def test_patient_prescriptions_portal(self):
        """Patient can view list of past prescriptions and detailed view."""
        self.client.login(email="patient.alice@clinichub.test", password="testpassword123")

        appointment = Appointment.objects.create(
            patient=self.patient,
            doctor=self.doctor,
            appointment_date=self.today,
            appointment_time=datetime.time(9, 30),
            status="COMPLETED",
        )
        prescription = Prescription.objects.create(
            appointment=appointment,
            patient=self.patient,
            doctor=self.doctor,
            general_advice="Avoid dust and smoke.",
        )
        PrescriptionItem.objects.create(
            prescription=prescription,
            medicine_name="Levocetirizine 5mg",
            dosage="5mg",
            frequency="OD",
            duration="5 days",
        )

        # 1. Prescriptions list
        list_res = self.client.get(reverse("patient-prescriptions"))
        self.assertEqual(list_res.status_code, 200)
        self.assertEqual(len(list_res.context["prescriptions"]), 1)
        self.assertContains(list_res, f"Rx #{prescription.id:05d}")

        # 2. Prescription detail
        detail_res = self.client.get(reverse("patient-prescription-detail", args=[prescription.id]))
        self.assertEqual(detail_res.status_code, 200)
        self.assertContains(detail_res, "Levocetirizine 5mg")
        self.assertContains(detail_res, "Avoid dust and smoke.")

    def test_lab_test_order_lifecycle(self):
        """Staff or Lab Technician can view orders and update status through its lifecycle."""
        self.client.login(email="nurse.mary@clinichub.test", password="testpassword123")

        appointment = Appointment.objects.create(
            patient=self.patient,
            doctor=self.doctor,
            appointment_date=self.today,
            appointment_time=datetime.time(15, 0),
            status="COMPLETED",
        )
        lab_order = LabTestOrder.objects.create(
            appointment=appointment,
            patient=self.patient,
            doctor=self.doctor,
            test_name="Lipid Profile",
            clinical_indication="Evaluate dyslipidemia",
            status="ORDERED",
        )

        # 1. Orders list view
        orders_res = self.client.get(reverse("lab-orders-list"))
        self.assertEqual(orders_res.status_code, 200)
        self.assertContains(orders_res, "Lipid Profile")

        # 2. Advance to SAMPLE_COLLECTED
        status_url = reverse("update-lab-order-status", args=[lab_order.id])
        res_sample = self.client.post(status_url, {"status": "SAMPLE_COLLECTED"})
        self.assertEqual(res_sample.status_code, 302)

        lab_order.refresh_from_db()
        self.assertEqual(lab_order.status, "SAMPLE_COLLECTED")

        # 3. Advance to COMPLETED with results
        res_comp = self.client.post(status_url, {
            "status": "COMPLETED",
            "results_summary": "Total Cholesterol: 185 mg/dL, HDL: 48 mg/dL, LDL: 110 mg/dL (Normal).",
        })
        self.assertEqual(res_comp.status_code, 302)

        lab_order.refresh_from_db()
        self.assertEqual(lab_order.status, "COMPLETED")
        self.assertIn("Total Cholesterol", lab_order.results_summary)
        self.assertIsNotNone(lab_order.completed_at)


class Phase6BillingTests(TestCase):
    """
    Test suite for Phase 6: 💰 Billing & Payments
    Covers fee catalog, itemized invoices, payment processing, auto-invoicing on consultation,
    print receipts, and patient portal access.
    """

    def setUp(self):
        self.department = Department.objects.create(
            name="General Medicine",
            description="Outpatient primary care",
        )

        # 1. Doctor user & profile
        self.doc_user = User.objects.create_user(
            username="dr_billing_tests",
            email="dr_billing@clinichub.care",
            password="StrongPassword123!",
            user_type="DOCTOR",
            first_name="Marcus",
            last_name="Welby",
        )
        self.doctor = Doctor.objects.create(
            user=self.doc_user,
            department=self.department,
            specialization="Family Medicine",
            license_number="LIC-BILLING-001",
        )

        # 2. Staff user & profile
        self.staff_user = User.objects.create_user(
            username="staff_cashier",
            email="cashier@clinichub.care",
            password="StrongPassword123!",
            user_type="STAFF",
            first_name="Penny",
            last_name="Counter",
        )
        self.staff = Staff.objects.create(
            user=self.staff_user,
            department=self.department,
            designation="Chief Cashier",
        )

        # 3. Admin user
        self.admin_user = User.objects.create_user(
            username="admin_billing",
            email="admin_billing@clinichub.care",
            password="StrongPassword123!",
            user_type="ADMIN",
            is_staff=True,
            first_name="Arthur",
            last_name="Director",
        )

        # 4. Patient users & profiles (Signal auto-creates Patient profile)
        self.patient_user = User.objects.create_user(
            username="patient_billable",
            email="patient_bill@example.com",
            password="StrongPassword123!",
            user_type="PATIENT",
            first_name="Oliver",
            last_name="Twist",
        )
        self.patient = self.patient_user.patient_profile

        self.patient_user_2 = User.objects.create_user(
            username="patient_billable_2",
            email="patient_bill2@example.com",
            password="StrongPassword123!",
            user_type="PATIENT",
            first_name="Jane",
            last_name="Eyre",
        )
        self.patient_2 = self.patient_user_2.patient_profile

        self.today = timezone.localdate()

    def test_seed_default_billable_services(self):
        """Verify default fee catalog items are seeded cleanly and idempotently."""
        services = seed_default_billable_services()
        self.assertGreaterEqual(len(services), 5)

        consultation = BillableService.objects.filter(category="CONSULTATION", name__icontains="General").first()
        self.assertIsNotNone(consultation)
        self.assertEqual(consultation.base_fee, Decimal("50.00"))

        cbc = BillableService.objects.filter(code="LAB-CBC").first()
        self.assertIsNotNone(cbc)
        self.assertEqual(cbc.base_fee, Decimal("25.00"))

        # Re-running seed does not duplicate entries
        seed_default_billable_services()
        self.assertEqual(BillableService.objects.filter(code="LAB-CBC").count(), 1)

    def test_invoice_totals_calculation_and_status_transitions(self):
        """Verify subtotal, tax, discount, balance, and status transitions on Invoice."""
        invoice = Invoice.objects.create(
            invoice_number=generate_invoice_number(),
            patient=self.patient,
            doctor=self.doctor,
            tax_rate=Decimal("10.00"),  # 10% tax
            discount=Decimal("10.00"),  # $10 discount
        )

        # Line items: 1x $100, 2x $25 = $150 subtotal
        InvoiceLineItem.objects.create(
            invoice=invoice,
            description="Consultation",
            quantity=1,
            unit_price=Decimal("100.00"),
        )
        InvoiceLineItem.objects.create(
            invoice=invoice,
            description="Diagnostic Test",
            quantity=2,
            unit_price=Decimal("25.00"),
        )

        invoice.calculate_totals()
        invoice.save()

        # Subtotal: $150.00
        # Taxable: $150 - $10 = $140.00
        # Tax (10%): $14.00
        # Total: $140 + $14 = $154.00
        self.assertEqual(invoice.subtotal, Decimal("150.00"))
        self.assertEqual(invoice.tax_amount, Decimal("14.00"))
        self.assertEqual(invoice.total_amount, Decimal("154.00"))
        self.assertEqual(invoice.amount_paid, Decimal("0.00"))
        self.assertEqual(invoice.amount_due, Decimal("154.00"))
        self.assertEqual(invoice.status, "UNPAID")
        self.assertFalse(invoice.is_fully_paid)

        # 1. Partial payment: $54.00
        Payment.objects.create(
            payment_number=generate_payment_number(),
            invoice=invoice,
            amount=Decimal("54.00"),
            payment_method="CASH",
            recorded_by=self.staff_user,
        )
        invoice.calculate_totals()
        invoice.save()

        self.assertEqual(invoice.amount_paid, Decimal("54.00"))
        self.assertEqual(invoice.amount_due, Decimal("100.00"))
        self.assertEqual(invoice.balance, Decimal("100.00"))
        self.assertEqual(invoice.status, "PARTIAL")

        # 2. Final settlement: $100.00
        Payment.objects.create(
            payment_number=generate_payment_number(),
            invoice=invoice,
            amount=Decimal("100.00"),
            payment_method="CARD",
            recorded_by=self.staff_user,
        )
        invoice.calculate_totals()
        invoice.save()

        self.assertEqual(invoice.amount_paid, Decimal("154.00"))
        self.assertEqual(invoice.amount_due, Decimal("0.00"))
        self.assertEqual(invoice.balance, Decimal("0.00"))
        self.assertEqual(invoice.status, "PAID")
        self.assertTrue(invoice.is_fully_paid)
        self.assertIsNotNone(invoice.paid_at)

    def test_auto_invoice_generation_for_appointment(self):
        """Verify generate_or_get_appointment_invoice automatically creates separate consultation and lab fee bills."""
        seed_default_billable_services()
        appointment = Appointment.objects.create(
            patient=self.patient,
            doctor=self.doctor,
            appointment_date=self.today,
            appointment_time=datetime.time(10, 0),
            reason="Chest pain & fever",
            status="BOOKED",
        )
        LabTestOrder.objects.create(
            appointment=appointment,
            patient=self.patient,
            doctor=self.doctor,
            test_name="Complete Blood Count (CBC)",
            status="ORDERED",
        )

        consult_inv = generate_or_get_appointment_invoice(appointment)
        self.assertIsNotNone(consult_inv)
        self.assertEqual(consult_inv.patient, self.patient)
        self.assertEqual(consult_inv.doctor, self.doctor)
        self.assertEqual(consult_inv.appointment, appointment)
        self.assertEqual(consult_inv.invoice_type, "CONSULTATION")

        # Consultation fee is $50.00 in its own distinct bill
        self.assertEqual(consult_inv.items.count(), 1)
        self.assertEqual(consult_inv.total_amount, Decimal("50.00"))
        self.assertEqual(consult_inv.status, "UNPAID")

        # Distinct separate bill created for Lab Investigation
        lab_inv = Invoice.objects.filter(invoice_type="LAB_TEST", patient=self.patient).first()
        self.assertIsNotNone(lab_inv)
        self.assertNotEqual(lab_inv.id, consult_inv.id)
        self.assertEqual(lab_inv.total_amount, Decimal("25.00"))
        self.assertEqual(lab_inv.status, "UNPAID")
        self.assertEqual(lab_inv.items.first().description, "Lab Investigation: Complete Blood Count (CBC)")

        # Calling again retrieves existing invoice without duplicating
        second_call = generate_or_get_appointment_invoice(appointment)
        self.assertEqual(second_call.id, consult_inv.id)
        self.assertEqual(Invoice.objects.filter(patient=self.patient).count(), 2)

    def test_complete_consultation_triggers_auto_invoice(self):
        """Doctor completing consultation automatically issues an itemized invoice for front desk."""
        appointment = Appointment.objects.create(
            patient=self.patient,
            doctor=self.doctor,
            appointment_date=self.today,
            appointment_time=datetime.time(11, 0),
            reason="Annual checkup",
            status="IN_CONSULTATION",
        )
        self.client.force_login(self.doc_user)

        url = reverse("complete-consultation", args=[appointment.id])
        response = self.client.post(url, {
            "action": "complete",
            "record-diagnosis": "Hypertension Stage 1",
            "record-chief_complaints": "Occasional headaches",
            "record-treatment_plan": "Low sodium diet and exercise",
            "rx-general_advice": "Check BP weekly",
        })
        self.assertEqual(response.status_code, 302)

        appointment.refresh_from_db()
        self.assertEqual(appointment.status, "COMPLETED")
        self.assertTrue(hasattr(appointment, "invoice"))
        self.assertIsNotNone(appointment.invoice)
        self.assertEqual(appointment.invoice.status, "UNPAID")
        self.assertGreater(appointment.invoice.total_amount, Decimal("0.00"))

    def test_signals_auto_create_separate_pending_bills(self):
        """Verify signals auto-create separate pending bills on appointment and lab test order creation."""
        seed_default_billable_services()
        # 1. Appointment creation auto-triggers Consultation invoice
        appt = Appointment.objects.create(
            patient=self.patient_2,
            doctor=self.doctor,
            appointment_date=self.today,
            appointment_time=datetime.time(15, 30),
            reason="Routine checkup",
            status="BOOKED",
        )
        consult_inv = Invoice.objects.filter(appointment=appt, invoice_type="CONSULTATION").first()
        self.assertIsNotNone(consult_inv)
        self.assertEqual(consult_inv.status, "UNPAID")
        self.assertEqual(consult_inv.total_amount, Decimal("50.00"))

        # 2. Lab test order creation auto-triggers separate Lab Test invoice
        lab_order = LabTestOrder.objects.create(
            appointment=appt,
            patient=self.patient_2,
            doctor=self.doctor,
            test_name="Complete Blood Count (CBC)",
            status="ORDERED",
        )
        lab_inv = Invoice.objects.filter(lab_order=lab_order, invoice_type="LAB_TEST").first()
        self.assertIsNotNone(lab_inv)
        self.assertEqual(lab_inv.status, "UNPAID")
        self.assertEqual(lab_inv.total_amount, Decimal("25.00"))
        # Must be distinct invoices
        self.assertNotEqual(consult_inv.id, lab_inv.id)

    def test_billing_invoice_list_view(self):
        """Verify staff and admin can access billing list with search and filters."""
        # Create an invoice
        invoice = Invoice.objects.create(
            invoice_number=generate_invoice_number(),
            patient=self.patient,
            doctor=self.doctor,
            status="UNPAID",
            total_amount=Decimal("120.00"),
            amount_due=Decimal("120.00"),
        )

        # Anonymous cannot access
        url = reverse("billing-invoice-list")
        resp_anon = self.client.get(url)
        self.assertEqual(resp_anon.status_code, 302)

        # Patient cannot access staff billing hub (403)
        self.client.force_login(self.patient_user)
        resp_patient = self.client.get(url)
        self.assertEqual(resp_patient.status_code, 403)

        # Staff can access
        self.client.force_login(self.staff_user)
        resp_staff = self.client.get(url)
        self.assertEqual(resp_staff.status_code, 200)
        self.assertContains(resp_staff, invoice.invoice_number)
        self.assertContains(resp_staff, "Total Billed")

        # Search filter
        resp_search = self.client.get(url, {"search": "Oliver"})
        self.assertEqual(resp_search.status_code, 200)
        self.assertContains(resp_search, invoice.invoice_number)

    def test_billing_invoice_detail_and_payment_recording(self):
        """Staff can view invoice details, add custom line item, and collect payment."""
        invoice = Invoice.objects.create(
            invoice_number=generate_invoice_number(),
            patient=self.patient,
            doctor=self.doctor,
        )
        InvoiceLineItem.objects.create(
            invoice=invoice,
            description="Clinical Dressing",
            quantity=1,
            unit_price=Decimal("40.00"),
        )
        invoice.calculate_totals()
        invoice.save()

        self.client.force_login(self.staff_user)
        detail_url = reverse("billing-invoice-detail", args=[invoice.id])

        # 1. View detail
        resp_view = self.client.get(detail_url)
        self.assertEqual(resp_view.status_code, 200)
        self.assertContains(resp_view, "Clinical Dressing")
        self.assertContains(resp_view, "$40.00")

        # 2. Add extra line item
        resp_add = self.client.post(detail_url, {
            "action": "add_line_item",
            "description": "Antiseptic Lotion Fee",
            "quantity": "1",
            "unit_price": "15.00",
        })
        self.assertEqual(resp_add.status_code, 302)

        invoice.refresh_from_db()
        self.assertEqual(invoice.items.count(), 2)
        self.assertEqual(invoice.total_amount, Decimal("55.00"))

        # 3. Collect payment
        resp_pay = self.client.post(detail_url, {
            "action": "record_payment",
            "amount": "55.00",
            "payment_method": "UPI",
            "transaction_reference": "UPI-TXN-998877",
            "notes": "QR payment at counter",
        })
        self.assertEqual(resp_pay.status_code, 302)

        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "PAID")
        self.assertEqual(invoice.amount_paid, Decimal("55.00"))
        self.assertEqual(invoice.payments.count(), 1)
        payment = invoice.payments.first()
        self.assertEqual(payment.payment_method, "UPI")
        self.assertEqual(payment.transaction_reference, "UPI-TXN-998877")
        self.assertEqual(payment.recorded_by, self.staff_user)

    def test_billing_invoice_create_manual(self):
        """Staff can create manual invoice with dynamic items."""
        seed_default_billable_services()
        self.client.force_login(self.staff_user)

        url = reverse("billing-invoice-create")
        resp_get = self.client.get(url)
        self.assertEqual(resp_get.status_code, 200)

        post_data = {
            "patient": self.patient.id,
            "doctor": self.doctor.id,
            "discount": "5.00",
            "tax_rate": "0.00",
            "due_date": self.today.isoformat(),
            "notes": "Manual counter invoice",
            "item_service[]": [""],
            "item_desc[]": ["Executive Health Checkup"],
            "item_qty[]": ["1"],
            "item_price[]": ["150.00"],
        }
        resp_post = self.client.post(url, post_data)
        self.assertEqual(resp_post.status_code, 302)

        created_invoice = Invoice.objects.filter(patient=self.patient, notes="Manual counter invoice").first()
        self.assertIsNotNone(created_invoice)
        self.assertEqual(created_invoice.subtotal, Decimal("150.00"))
        self.assertEqual(created_invoice.discount, Decimal("5.00"))
        self.assertEqual(created_invoice.total_amount, Decimal("145.00"))
        self.assertEqual(created_invoice.status, "UNPAID")

    def test_patient_billing_portal_access(self):
        """Patient can view their invoices but cannot access other patients' invoices."""
        invoice_oliver = Invoice.objects.create(
            invoice_number=generate_invoice_number(),
            patient=self.patient,
            doctor=self.doctor,
            total_amount=Decimal("50.00"),
            amount_due=Decimal("50.00"),
        )
        invoice_jane = Invoice.objects.create(
            invoice_number=generate_invoice_number(),
            patient=self.patient_2,
            doctor=self.doctor,
            total_amount=Decimal("85.00"),
            amount_due=Decimal("85.00"),
        )

        # Login as Oliver
        self.client.force_login(self.patient_user)

        # 1. Billing history list
        url_hist = reverse("patient-billing-history")
        resp_hist = self.client.get(url_hist)
        self.assertEqual(resp_hist.status_code, 200)
        self.assertContains(resp_hist, invoice_oliver.invoice_number)
        self.assertNotContains(resp_hist, invoice_jane.invoice_number)

        # 2. Detail of Oliver's own invoice -> 200
        url_own_detail = reverse("patient-invoice-detail", args=[invoice_oliver.id])
        resp_own = self.client.get(url_own_detail)
        self.assertEqual(resp_own.status_code, 200)
        self.assertContains(resp_own, invoice_oliver.invoice_number)

        # 3. Attempt to access Jane's invoice -> 404
        url_jane_detail = reverse("patient-invoice-detail", args=[invoice_jane.id])
        resp_forbidden = self.client.get(url_jane_detail)
        self.assertEqual(resp_forbidden.status_code, 404)

    def test_billing_invoice_print_view(self):
        """Verify printable tax invoice receipt renders cleanly with clinic letterhead."""
        invoice = Invoice.objects.create(
            invoice_number=generate_invoice_number(),
            patient=self.patient,
            doctor=self.doctor,
            total_amount=Decimal("75.00"),
            amount_due=Decimal("75.00"),
        )
        InvoiceLineItem.objects.create(
            invoice=invoice,
            description="General Medical Consultation",
            quantity=1,
            unit_price=Decimal("75.00"),
        )

        # Patient viewing their receipt
        self.client.force_login(self.patient_user)
        url_print = reverse("billing-invoice-print", args=[invoice.id])
        resp = self.client.get(url_print)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Tax Invoice / Receipt")
        self.assertContains(resp, invoice.invoice_number)
        self.assertContains(resp, "ClinicHub Healthcare")

    def test_service_catalog_management(self):
        """Admin can view, add, edit, and toggle fee catalog services."""
        self.client.force_login(self.admin_user)

        # 1. List catalog
        list_url = reverse("billing-service-list")
        res_list = self.client.get(list_url)
        self.assertEqual(res_list.status_code, 200)

        # 2. Create new service
        create_url = reverse("billing-service-create")
        res_create = self.client.post(create_url, {
            "name": "Ultrasound Abdomen Screening",
            "category": "LAB_TEST",
            "code": "US-ABD-01",
            "base_fee": "65.00",
            "description": "High resolution ultrasound imaging of abdominal cavity",
            "is_active": "on",
        })
        self.assertEqual(res_create.status_code, 302)

        service = BillableService.objects.filter(code="US-ABD-01").first()
        self.assertIsNotNone(service)
        self.assertEqual(service.base_fee, Decimal("65.00"))
        self.assertTrue(service.is_active)

        # 3. Edit service
        edit_url = reverse("billing-service-edit", args=[service.id])
        res_edit = self.client.post(edit_url, {
            "name": "Ultrasound Abdomen Screening",
            "category": "LAB_TEST",
            "code": "US-ABD-01",
            "base_fee": "70.00",
            "description": "Updated pricing for ultrasound",
            "is_active": "on",
        })
        self.assertEqual(res_edit.status_code, 302)
        service.refresh_from_db()
        self.assertEqual(service.base_fee, Decimal("70.00"))

        # 4. Toggle active status
        toggle_url = reverse("billing-service-toggle", args=[service.id])
        res_toggle = self.client.post(toggle_url)
        self.assertEqual(res_toggle.status_code, 302)
        service.refresh_from_db()
        self.assertFalse(service.is_active)


class Phase7EmailNotificationTests(TestCase):
    """
    Automated integration tests for Phase 7.1 SMTP Email Backend and Transactional Email Dispatches.
    """

    def setUp(self):
        from django.core import mail
        mail.outbox.clear()
        self.department = Department.objects.create(
            name="Cardiology",
            description="Cardiology Dept",
        )
        self.doctor_user = User.objects.create_user(
            username="dr_smith",
            email="dr.smith@example.com",
            first_name="John",
            last_name="Smith",
            password="DoctorPassword123!",
            user_type="DOCTOR",
        )
        self.doctor = Doctor.objects.create(
            user=self.doctor_user,
            department=self.department,
            specialization="Cardiologist",
            license_number="LIC-CAR-777",
        )
        self.patient_user = User.objects.create_user(
            username="emailtestpatient",
            email="patient.email.test@example.com",
            first_name="Alice",
            last_name="Wonder",
            password="SecurePassword123!",
            user_type="PATIENT",
        )
        self.patient_user.is_email_verified = True
        self.patient_user.save()
        self.patient, _ = Patient.objects.get_or_create(user=self.patient_user)

        self.staff_user = User.objects.create_user(
            username="staff_reception",
            email="staff.reception@example.com",
            first_name="Jane",
            last_name="Reception",
            password="StaffPassword123!",
            user_type="STAFF",
            staff_role="RECEPTIONIST",
        )
        self.staff_user.is_email_verified = True
        self.staff_user.save()
        self.staff, _ = Staff.objects.get_or_create(
            user=self.staff_user,
            department=self.department,
            designation="Front Desk",
        )

        self.today = timezone.localdate()
        day_codes = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN']
        DoctorAvailability.objects.create(
            doctor=self.doctor,
            day=day_codes[self.today.weekday()],
            start_time=datetime.time(9, 0),
            end_time=datetime.time(17, 0),
            slot_duration=30,
            tokens_per_slot=3,
            is_active=True,
        )

    def test_smtp_settings_configuration(self):
        """Verify settings.py SMTP parameters and default sender."""
        from ClinicHub import settings as project_settings
        from django.conf import settings
        self.assertIn("smtp", project_settings.EMAIL_BACKEND.lower())
        self.assertEqual(settings.EMAIL_HOST, "smtp.gmail.com")
        self.assertEqual(settings.EMAIL_PORT, 587)
        self.assertTrue(settings.EMAIL_USE_TLS)
        self.assertIn("ClinicHub", settings.DEFAULT_FROM_EMAIL)
        self.assertIn("@", settings.DEFAULT_FROM_EMAIL)

    def test_send_test_email_management_command(self):
        """Verify the send_test_email management command dispatches properly."""
        from django.core import mail
        from django.conf import settings

        out = StringIO()
        call_command(
            "send_test_email",
            "audit.recipient@example.com",
            subject="Automated Verification Check",
            stdout=out,
        )

        self.assertEqual(len(mail.outbox), 1)
        sent_msg = mail.outbox[0]
        self.assertEqual(sent_msg.to, ["audit.recipient@example.com"])
        self.assertEqual(sent_msg.subject, "Automated Verification Check")
        self.assertEqual(sent_msg.from_email, settings.DEFAULT_FROM_EMAIL)
        self.assertIn("ClinicHub", sent_msg.body)
        self.assertIn("SUCCESS", out.getvalue())

    def test_signup_dispatches_verification_email(self):
        """Verify signup flow sends a verification link via email."""
        from django.core import mail

        signup_url = reverse("signup")
        response = self.client.post(
            signup_url,
            {
                "username": "newpatient99",
                "email": "newpatient99@example.com",
                "first_name": "Test",
                "last_name": "Patient",
                "phone_number": "9876543210",
                "password": "StrongPassword123!",
                "confirm_password": "StrongPassword123!",
                "date_of_birth": "1995-05-15",
                "gender": "MALE",
                "blood_group": "O+",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("verification-sent"))

        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ["newpatient99@example.com"])
        self.assertIn("Verify your ClinicHub email", email.subject)
        self.assertIn("/verify-email/", email.body)

    def test_forgot_password_dispatches_reset_email(self):
        """Verify password reset request sends a reset email to the user."""
        from django.core import mail

        forgot_url = reverse("forgot-password")
        response = self.client.post(
            forgot_url,
            {
                "email": self.patient_user.email,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("password_reset_done"))

        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, [self.patient_user.email])
        self.assertIn("reset", email.subject.lower())
        self.assertIn("reset-password", email.body)

    def test_book_slot_appointment_dispatches_confirmation_email(self):
        """Verify booking an appointment automatically sends a rich HTML confirmation email."""
        from django.core import mail

        mail.outbox.clear()
        appointment, token = book_slot_appointment(
            patient=self.patient,
            doctor=self.doctor,
            appointment_date=self.today,
            slot_start_time=datetime.time(10, 0),
            slot_end_time=datetime.time(10, 30),
            time_slot="10:00 AM – 10:30 AM",
            token_number=1,
            reason="Cardio follow-up check",
        )

        self.assertGreaterEqual(len(mail.outbox), 1)
        sent_email = mail.outbox[-1]
        self.assertEqual(sent_email.to, [self.patient_user.email])
        self.assertIn("Appointment Confirmed", sent_email.subject)
        self.assertIn("Token #001", sent_email.subject)
        self.assertIn("John Smith", sent_email.subject)
        self.assertIn("#001", sent_email.body)
        self.assertIn("Cardiology", sent_email.body)

        # Verify multipart HTML alternative is present and properly styled
        self.assertEqual(len(sent_email.alternatives), 1)
        html_content, mimetype = sent_email.alternatives[0]
        self.assertEqual(mimetype, "text/html")
        self.assertIn("Queue Token", html_content)
        self.assertIn("#001", html_content)
        self.assertIn("Cardio follow-up check", html_content)
        self.assertIn("Important Clinic Visit Guidelines", html_content)

    def test_walk_in_appointment_dispatches_confirmation_email(self):
        """Verify staff walk-in registration sends a confirmation email to the patient."""
        from django.core import mail

        self.client.login(email=self.staff_user.email, password="StaffPassword123!")
        mail.outbox.clear()

        res = self.client.post(
            reverse("walk-in-booking"),
            {
                "patient_mode": "EXISTING",
                "existing_patient": str(self.patient.id),
                "department": str(self.department.id),
                "doctor": str(self.doctor.id),
                "reason": "Emergency walk-in consult",
            },
        )
        self.assertEqual(res.status_code, 302)
        self.assertGreaterEqual(len(mail.outbox), 1)
        sent_email = mail.outbox[-1]
        self.assertEqual(sent_email.to, [self.patient_user.email])
        self.assertIn("Appointment Confirmed", sent_email.subject)

    def test_reschedule_appointment_dispatches_email(self):
        """Verify rescheduling an appointment dispatches an updated notification email."""
        from django.core import mail

        appointment, token = book_slot_appointment(
            patient=self.patient,
            doctor=self.doctor,
            appointment_date=self.today,
            slot_start_time=datetime.time(10, 0),
            slot_end_time=datetime.time(10, 30),
            time_slot="10:00 AM – 10:30 AM",
            token_number=1,
            reason="Routine checkup",
        )

        mail.outbox.clear()
        self.client.login(email=self.patient_user.email, password="SecurePassword123!")
        res = self.client.post(
            reverse("reschedule-appointment", args=[appointment.id]),
            {
                "department": self.department.id,
                "doctor": self.doctor.id,
                "appointment_date": self.today,
                "appointment_time": "11:00",
                "slot_start_time": "11:00",
                "slot_end_time": "11:30",
                "time_slot": "11:00 AM – 11:30 AM",
                "token_number": 2,
                "reason": "Shifted to 11 AM",
            },
        )
        self.assertEqual(res.status_code, 302)
        self.assertGreaterEqual(len(mail.outbox), 1)
        sent_email = mail.outbox[-1]
        self.assertEqual(sent_email.to, [self.patient_user.email])
        self.assertIn("Appointment Rescheduled", sent_email.subject)
        self.assertIn("Token #002", sent_email.subject)

    def test_cancel_appointment_dispatches_email(self):
        """Verify cancelling an appointment dispatches a cancellation confirmation email."""
        from django.core import mail

        appointment, token = book_slot_appointment(
            patient=self.patient,
            doctor=self.doctor,
            appointment_date=self.today,
            slot_start_time=datetime.time(10, 0),
            slot_end_time=datetime.time(10, 30),
            time_slot="10:00 AM – 10:30 AM",
            token_number=1,
            reason="Routine checkup",
        )

        mail.outbox.clear()
        self.client.login(email=self.patient_user.email, password="SecurePassword123!")
        res = self.client.post(
            reverse("cancel-appointment", args=[appointment.id]),
            {
                "cancellation_reason": "Symptoms resolved / feeling better",
                "notes": "No longer needed",
            },
        )
        self.assertEqual(res.status_code, 302)
        self.assertGreaterEqual(len(mail.outbox), 1)
        sent_email = mail.outbox[-1]
        self.assertEqual(sent_email.to, [self.patient_user.email])
        self.assertIn("Appointment Cancelled", sent_email.subject)
        self.assertIn("Symptoms resolved / feeling better", sent_email.body)











