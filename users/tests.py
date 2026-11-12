import os
import json
from django.test import TestCase
from django.urls import reverse
from users.models import User
from clinic.models import Patient, Department, Doctor
from io import BytesIO
from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile


class UserLoginTest(TestCase):

    def setUp(self):
        self.password = "TestPassword123!"

        self.user = User.objects.create_user(
            username="testpatient",
            email="testpatient@example.com",
            password=self.password,
            user_type="PATIENT",
        )
        self.user.is_email_verified = True
        self.user.save()

    def test_login_page_loads(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)

    def test_patient_can_login(self):
        response = self.client.post(
            reverse("login"),
            {
                "email": "testpatient@example.com",
                "password": self.password,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.wsgi_request.user.is_authenticated)

    def test_unverified_patient_cannot_login(self):
        self.user.is_email_verified = False
        self.user.save()

        response = self.client.post(
            reverse("login"),
            {
                "email": "testpatient@example.com",
                "password": self.password,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please verify your email first.")

    def test_invalid_credentials_rejected(self):
        response = self.client.post(
            reverse("login"),
            {
                "email": "testpatient@example.com",
                "password": "WrongPassword!",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invalid email or password.")


def create_test_image():
    file = BytesIO()
    image = Image.new("RGB", (100, 100), color="teal")
    image.save(file, "JPEG")
    file.seek(0)
    return SimpleUploadedFile("avatar.jpg", file.read(), content_type="image/jpeg")


class Phase8UXAndProfileTests(TestCase):
    def setUp(self):
        self.password = "Secr3tPass!123"
        self.patient_user = User.objects.create_user(
            username="sarahsmith",
            email="sarah.smith@example.com",
            first_name="Sarah",
            last_name="Smith",
            phone_number="555-0101",
            user_type="PATIENT",
            password=self.password,
            is_email_verified=True,
        )
        self.patient = Patient.objects.get(user=self.patient_user)

    def test_user_initials_and_avatar_url(self):
        # Initial check without avatar
        self.assertEqual(self.patient_user.get_initials(), "SS")
        self.assertEqual(self.patient_user.get_avatar_url(), "")

        # Fallback initials if names missing
        temp_user = User(username="johndoe")
        self.assertEqual(temp_user.get_initials(), "J")

        # Upload avatar
        test_img = create_test_image()
        self.patient_user.profile_picture = test_img
        self.patient_user.save()
        self.assertTrue(bool(self.patient_user.get_avatar_url()))

    def test_profile_view_requires_authentication(self):
        response = self.client.get(reverse("user-profile"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_profile_view_get_authenticated(self):
        self.client.login(email="sarah.smith@example.com", password=self.password)
        response = self.client.get(reverse("user-profile"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "users/profile.html")
        self.assertContains(response, "Sarah")
        self.assertContains(response, "Smith")
        self.assertContains(response, "sarah.smith@example.com")
        self.assertContains(response, "skip-to-content")
        self.assertContains(response, "themeToggleBtn")
        self.assertContains(response, "mobileNavDrawer")

    def test_profile_update_successful(self):
        self.client.login(email="sarah.smith@example.com", password=self.password)
        response = self.client.post(
            reverse("user-profile"),
            {
                "first_name": "Sarah Jane",
                "last_name": "Smith-Johnson",
                "phone_number": "555-9999",
                "blood_group": "O+",
                "gender": "FEMALE",
                "address": "123 Health Ave, Suite 4B",
                "emergency_contact_name": "John Smith",
                "emergency_contact_phone": "555-8888",
            },
            follow=True
        )
        self.assertEqual(response.status_code, 200)
        self.patient_user.refresh_from_db()
        self.patient.refresh_from_db()
        self.assertEqual(self.patient_user.first_name, "Sarah Jane")
        self.assertEqual(self.patient_user.last_name, "Smith-Johnson")
        self.assertEqual(self.patient_user.phone_number, "555-9999")
        self.assertEqual(self.patient.blood_group, "O+")
        self.assertEqual(self.patient.gender, "FEMALE")
        self.assertEqual(self.patient.address, "123 Health Ave, Suite 4B")
        self.assertContains(response, "Your profile has been updated successfully!")

    def test_profile_picture_upload_and_removal(self):
        self.client.login(email="sarah.smith@example.com", password=self.password)
        test_img = create_test_image()
        response = self.client.post(
            reverse("user-profile"),
            {
                "first_name": "Sarah",
                "last_name": "Smith",
                "phone_number": "555-0101",
                "profile_picture": test_img,
            },
            follow=True
        )
        self.assertEqual(response.status_code, 200)
        self.patient_user.refresh_from_db()
        self.assertTrue(bool(self.patient_user.profile_picture))

        # Test remove_avatar
        response_remove = self.client.post(
            reverse("user-profile"),
            {"remove_avatar": "1"},
            follow=True
        )
        self.assertContains(response_remove, "Profile picture removed successfully.")


class HumanizedHomePageTests(TestCase):
    """
    Tests for the Humanized Clinic Theme and Animated Video Homepage:
    - Public accessibility without login requirement
    - Careplus / ClinicHub humanized theme elements & headlines
    - Animated video showcase element and static asset linking
    - Floating glassmorphism appointment booking bar
    - Departments, compassionate care section & featured services
    """

    def setUp(self):
        self.dept = Department.objects.create(name="Emergency Care", is_active=True)
        self.dept2 = Department.objects.create(name="Pediatric Department", is_active=True)
        self.dept3 = Department.objects.create(name="Cardiology", is_active=True)

        self.doc_user = User.objects.create_user(
            username="hero_doctor",
            email="herodoctor@example.com",
            first_name="Marcus",
            last_name="Vance",
            password="TestPassword123!",
            user_type="DOCTOR",
        )
        self.doctor = Doctor.objects.create(
            user=self.doc_user,
            department=self.dept3,
            license_number="DOC-HERO-001",
            specialization="Cardiologist",
            is_active=True,
        )

    def test_home_page_accessible_anonymously(self):
        """Public visitors can view the landing page and virtual clinic tour."""
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "users/home.html")
        self.assertIn("departments", response.context)
        self.assertIn("doctors", response.context)
        self.assertContains(response, "Your Health, Our Priority")
        self.assertContains(response, "Compassionate Care for You and Your Family")

    def test_home_page_animated_video_and_glassmorphism(self):
        """Landing page includes the animated video element and glass quick-booking bar."""
        response = self.client.get(reverse("home"))
        self.assertContains(response, "clinic_tour.webp")
        self.assertContains(response, "hero-video-frame")
        self.assertContains(response, "glass-booking-bar")
        self.assertContains(response, "Emergency Care")
        self.assertContains(response, "Cardiology")
        self.assertContains(response, "clinic_care_nurse.jpg")
        self.assertContains(response, "service_diagnostics.jpg")
        self.assertContains(response, "clinicTourModal")



class SplashAndLandingPageTests(TestCase):
    """
    Tests for the animated logo splash screen and landing page transition:
    - Landing page embeds full animated logo overlay and script/css assets
    - Standalone /splash/, /welcome/, and /intro/ views render dedicated animated logo page
    - Contains skip button, ECG trace, heartbeat badge, and target redirect to home
    """

    def test_landing_page_embeds_animated_logo_splash(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "users/home.html")
        self.assertContains(response, "clinichubSplashOverlay")
        self.assertContains(response, "splash-logo-card")
        self.assertContains(response, "splash-ecg-path")
        self.assertContains(response, "splash-progress-fill")
        self.assertContains(response, "splashSkipBtn")
        self.assertContains(response, "clinic_splash.css")
        self.assertContains(response, "clinic_splash.js")

    def test_standalone_splash_page_loads(self):
        response = self.client.get(reverse("splash"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "users/splash.html")
        self.assertContains(response, "clinichubSplashOverlay")
        self.assertContains(response, 'data-standalone="true"')
        self.assertContains(response, 'data-target-url="/"')
        self.assertContains(response, "splashSkipBtn")
        self.assertContains(response, "clinic_splash.css")
        self.assertContains(response, "clinic_splash.js")

    def test_welcome_and_intro_alias_routes(self):
        for route_name in ["welcome", "intro"]:
            response = self.client.get(reverse(route_name))
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, "users/splash.html")



class Phase9ModularSettingsTests(TestCase):
    """
    Verification tests for Task 9.1: Modular Settings Split.
    Validates base, dev, and prod modules, directory resolutions,
    and production security configurations.
    """

    def test_base_dir_resolution(self):
        from ClinicHub.settings import base
        self.assertTrue(base.BASE_DIR.exists())
        self.assertTrue((base.BASE_DIR / "templates").exists())
        self.assertTrue((base.BASE_DIR / "static").exists())
        self.assertTrue((base.BASE_DIR / "manage.py").exists())

    def test_base_settings_configuration(self):
        from ClinicHub.settings import base
        self.assertEqual(base.AUTH_USER_MODEL, "users.User")
        self.assertEqual(base.LOGIN_URL, "/login/")
        self.assertIn("users", base.INSTALLED_APPS)
        self.assertIn("clinic", base.INSTALLED_APPS)
        self.assertIn("channels", base.INSTALLED_APPS)
        self.assertIn("daphne", base.INSTALLED_APPS)
        self.assertIn("django.middleware.security.SecurityMiddleware", base.MIDDLEWARE)
        self.assertIn("django.middleware.csrf.CsrfViewMiddleware", base.MIDDLEWARE)

    def test_dev_settings_configuration(self):
        from ClinicHub.settings import dev
        self.assertTrue(dev.DEBUG)
        self.assertEqual(dev.ALLOWED_HOSTS, ["*"])
        self.assertEqual(
            dev.CHANNEL_LAYERS["default"]["BACKEND"],
            "channels.layers.InMemoryChannelLayer",
        )
        self.assertFalse(dev.SECURE_SSL_REDIRECT)
        self.assertFalse(dev.SESSION_COOKIE_SECURE)
        self.assertFalse(dev.CSRF_COOKIE_SECURE)

    def test_prod_settings_configuration(self):
        from ClinicHub.settings import prod
        self.assertFalse(prod.DEBUG)
        self.assertTrue(prod.SESSION_COOKIE_SECURE)
        self.assertTrue(prod.CSRF_COOKIE_SECURE)
        self.assertTrue(prod.SESSION_COOKIE_HTTPONLY)
        self.assertTrue(prod.CSRF_COOKIE_HTTPONLY)
        self.assertTrue(prod.SECURE_BROWSER_XSS_FILTER)
        self.assertTrue(prod.SECURE_CONTENT_TYPE_NOSNIFF)
        self.assertEqual(prod.X_FRAME_OPTIONS, "DENY")
        self.assertEqual(prod.SECURE_HSTS_SECONDS, 31536000)
        self.assertTrue(prod.SECURE_HSTS_INCLUDE_SUBDOMAINS)
        self.assertTrue(prod.SECURE_HSTS_PRELOAD)
        self.assertEqual(prod.SECURE_REFERRER_POLICY, "same-origin")

    def test_env_loader_delegation(self):
        from ClinicHub import settings
        # By default in dev environment:
        self.assertTrue(settings.DEBUG)
        self.assertEqual(settings.ALLOWED_HOSTS, ["*"])



class Phase9SecurityHardeningTests(TestCase):
    """
    Verification tests for Task 9.2: CSRF, XSS, and Clickjacking Hardening.
    Validates security middleware, HTTP defense headers, CSRF enforcement,
    and template auto-escaping against malicious payloads.
    """

    def test_security_headers_present_on_response(self):
        """Verify comprehensive defensive headers are attached to HTTP responses."""
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)

        # Clickjacking defense
        self.assertEqual(response.headers.get("X-Frame-Options"), "DENY")

        # MIME-sniffing / XSS defense
        self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")

        # Referrer & cross-origin policies
        self.assertIn("origin", response.headers.get("Referrer-Policy", "").lower())
        self.assertEqual(response.headers.get("Cross-Origin-Opener-Policy"), "same-origin")

        # Content-Security-Policy (CSP)
        csp = response.headers.get("Content-Security-Policy")
        self.assertIsNotNone(csp, "Content-Security-Policy header must be present")
        self.assertIn("default-src 'self'", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertIn("script-src", csp)
        self.assertIn("style-src", csp)
        self.assertIn("connect-src", csp)
        self.assertIn("ws:", csp)

        # Permissions-Policy
        permissions_policy = response.headers.get("Permissions-Policy")
        self.assertIsNotNone(permissions_policy, "Permissions-Policy header must be present")
        self.assertIn("camera=()", permissions_policy)
        self.assertIn("microphone=()", permissions_policy)

    def test_csrf_protection_rejects_missing_token(self):
        """Verify CsrfViewMiddleware blocks untrusted POST requests with HTTP 403."""
        from django.test import Client
        csrf_client = Client(enforce_csrf_checks=True)
        # Attempt POST without CSRF token
        response = csrf_client.post(
            reverse("login"),
            {"email": "untrusted@attacker.com", "password": "any"},
        )
        self.assertEqual(response.status_code, 403)

    def test_csrf_protection_accepts_valid_token(self):
        """Verify POST requests with valid CSRF token are accepted."""
        from django.test import Client
        csrf_client = Client(enforce_csrf_checks=True)
        get_res = csrf_client.get(reverse("login"))
        csrf_token = get_res.cookies.get("csrftoken").value
        post_res = csrf_client.post(
            reverse("login"),
            {
                "csrfmiddlewaretoken": csrf_token,
                "email": "nonexistent@example.com",
                "password": "wrongpassword",
            },
        )
        # Valid token submitted: rejects on invalid credentials (200), not CSRF (403)
        self.assertEqual(post_res.status_code, 200)

    def test_clickjacking_defense_frame_ancestors(self):
        """Verify iframe embedding is prohibited via both legacy and CSP directives."""
        response = self.client.get(reverse("login"))
        self.assertEqual(response.headers.get("X-Frame-Options"), "DENY")
        csp = response.headers.get("Content-Security-Policy", "")
        self.assertIn("frame-ancestors 'none'", csp)

    def test_xss_template_auto_escaping(self):
        """Verify that user-supplied input is escaped to prevent stored XSS attacks."""
        malicious_input = "<script>alert('xss_attack');</script>"
        user = User.objects.create_user(
            username="xss_test_patient",
            email="xss@example.com",
            first_name=malicious_input,
            password="TestPassword123!",
            user_type="PATIENT",
        )
        user.is_email_verified = True
        user.save()

        self.client.force_login(user)
        response = self.client.get(reverse("home"))
        # Script tag must be escaped (&lt;script&gt;) and NEVER present unescaped
        self.assertNotContains(response, "<script>alert('xss_attack');</script>")
        self.assertContains(response, "&lt;script&gt;alert(&#x27;xss_attack&#x27;);&lt;/script&gt;")

    def test_cookie_security_attributes(self):
        """Verify session cookie security flags."""
        from django.conf import settings
        self.assertTrue(settings.SESSION_COOKIE_HTTPONLY)
        self.assertEqual(settings.SESSION_COOKIE_SAMESITE, "Lax")
        self.assertEqual(settings.CSRF_COOKIE_SAMESITE, "Lax")



class Phase9RateLimitingTests(TestCase):
    """
    Verification tests for Task 9.3: Rate Limiting.
    Validates sliding-window / cache-backed rate limiting on login,
    password reset, and API endpoints, ensuring protection against brute-force and DDoS.
    """

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.password = "ValidPassword123!"
        self.user = User.objects.create_user(
            username="ratelimit_patient",
            email="ratelimit_patient@example.com",
            password=self.password,
            user_type="PATIENT",
        )
        self.user.is_email_verified = True
        self.user.save()

    def tearDown(self):
        from django.core.cache import cache
        cache.clear()

    def test_parse_rate_utility(self):
        """Verify rate string parsing into count and seconds."""
        from ClinicHub.ratelimit import parse_rate
        self.assertEqual(parse_rate("5/s"), (5, 1))
        self.assertEqual(parse_rate("5/m"), (5, 60))
        self.assertEqual(parse_rate("10/min"), (10, 60))
        self.assertEqual(parse_rate("100/h"), (100, 3600))
        self.assertEqual(parse_rate("500/d"), (500, 86400))
        self.assertEqual(parse_rate("3/15m"), (3, 900))

    def test_login_rate_limiting_blocks_after_threshold(self):
        """Verify that more than 5 login attempts within a minute trigger HTTP 429."""
        from django.test import Client
        client = Client()

        # Send 5 failed login attempts (allowed threshold)
        for i in range(5):
            res = client.post(
                reverse("login"),
                {"email": "ratelimit_patient@example.com", "password": "wrong_password"},
            )
            self.assertEqual(res.status_code, 200, f"Attempt {i+1} should be processed")

        # 6th attempt must be blocked by rate limiter
        blocked_res = client.post(
            reverse("login"),
            {"email": "ratelimit_patient@example.com", "password": "wrong_password"},
        )
        self.assertEqual(blocked_res.status_code, 429)
        self.assertIn("Retry-After", blocked_res.headers)
        self.assertContains(blocked_res, "HTTP 429", status_code=429)
        self.assertContains(blocked_res, "Please Slow Down", status_code=429)
        self.assertContains(blocked_res, "rateCountdown", status_code=429)

    def test_login_rate_limiting_resets_on_success(self):
        """Verify successful login resets rate limit counter for legitimate users."""
        from django.test import Client
        client = Client()

        # 2 failed attempts
        for _ in range(2):
            client.post(
                reverse("login"),
                {"email": "ratelimit_patient@example.com", "password": "wrong_password"},
            )

        # 1 successful login
        success_res = client.post(
            reverse("login"),
            {"email": "ratelimit_patient@example.com", "password": self.password},
        )
        self.assertIn(success_res.status_code, (200, 302))

        # Check that cache for user_login has been cleared
        from django.core.cache import cache
        from ClinicHub.ratelimit import build_cache_key
        from django.test import RequestFactory
        factory = RequestFactory()
        req = factory.post(reverse("login"))
        cache_key = build_cache_key(req, "user_login", "ip")
        self.assertIsNone(cache.get(cache_key))

    def test_api_rate_limiting_returns_json_429(self):
        """Verify API endpoints return structured JSON with retry_after on rate limit."""
        from django.test import RequestFactory
        from ClinicHub.ratelimit import ratelimit
        from django.http import JsonResponse

        factory = RequestFactory()

        @ratelimit(group="test_api", rate="2/m", key="ip", block=True)
        def mock_api_view(request):
            return JsonResponse({"status": "ok"})

        req1 = factory.get("/api/test/", HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        res1 = mock_api_view(req1)
        self.assertEqual(res1.status_code, 200)

        req2 = factory.get("/api/test/", HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        res2 = mock_api_view(req2)
        self.assertEqual(res2.status_code, 200)

        # 3rd request exceeds rate of 2/m
        req3 = factory.get("/api/test/", HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        res3 = mock_api_view(req3)
        self.assertEqual(res3.status_code, 429)
        self.assertIn("Retry-After", res3.headers)
        import json
        data = json.loads(res3.content.decode())
        self.assertIn("error", data)
        self.assertIn("retry_after", data)
        self.assertGreater(data["retry_after"], 0)

    def test_custom_429_template_renders_properly(self):
        """Verify the 429.html template renders with luxury design tokens and countdown."""
        from django.test import RequestFactory
        from ClinicHub.ratelimit import build_rate_limit_response

        factory = RequestFactory()
        request = factory.get("/some-page/")
        response = build_rate_limit_response(request, retry_after=45)

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers.get("Retry-After"), "45")
        content = response.content.decode()
        self.assertIn("HTTP 429", content)
        self.assertIn("Please Slow Down", content)
        self.assertIn("45", content)



class Phase9AuditLoggingTests(TestCase):
    """
    Verification tests for Task 9.4: Audit Logging.
    Validates immutable audit logging for authentication, appointments,
    consultations, and administrator audit explorer.
    """

    def setUp(self):
        self.password = "AuditPass123!"
        self.admin_user = User.objects.create_user(
            username="audit_admin",
            email="audit_admin@example.com",
            password=self.password,
            user_type="ADMIN",
        )
        self.admin_user.is_email_verified = True
        self.admin_user.save()

        self.patient_user = User.objects.create_user(
            username="audit_patient",
            email="audit_patient@example.com",
            password=self.password,
            user_type="PATIENT",
        )
        self.patient_user.is_email_verified = True
        self.patient_user.save()

    def test_audit_log_model_properties(self):
        """Verify model representation and UI badge properties."""
        from clinic.models import AuditLog
        log = AuditLog.objects.create(
            actor=self.admin_user,
            actor_email=self.admin_user.email,
            actor_role="ADMIN",
            action="LOGIN",
            description="Admin logged in successfully",
        )
        self.assertIn("LOGIN", str(log))
        self.assertIn("audit_admin@example.com", str(log))
        self.assertEqual(log.badge_color, "#16a34a")  # Green for success
        self.assertIn("fa-solid", log.action_icon)

        # Danger color for failed login
        fail_log = AuditLog.objects.create(
            action="LOGIN_FAILED",
            description="Failed login attempt",
        )
        self.assertEqual(fail_log.badge_color, "#dc2626")

    def test_log_audit_event_helper(self):
        """Verify log_audit_event captures request context and metadata."""
        from clinic.audit import log_audit_event
        from django.test import RequestFactory
        factory = RequestFactory()
        req = factory.get("/test-path/", HTTP_USER_AGENT="AntigravityTestRunner/1.0")
        req.user = self.admin_user

        entry = log_audit_event(
            request=req,
            action="CONSULTATION_START",
            description="Test consultation started",
            target_object=self.patient_user,
            extra_data={"doctor_room": "101"},
        )
        self.assertIsNotNone(entry)
        self.assertEqual(entry.actor, self.admin_user)
        self.assertEqual(entry.actor_email, "audit_admin@example.com")
        self.assertEqual(entry.actor_role, "ADMIN")
        self.assertEqual(entry.target_model, "User")
        self.assertEqual(entry.target_id, str(self.patient_user.pk))
        self.assertEqual(entry.extra_data.get("doctor_room"), "101")
        self.assertIn("AntigravityTestRunner", entry.user_agent)

    def test_login_and_logout_audit_logging(self):
        """Verify user login success, failure, and logout are logged automatically."""
        from clinic.models import AuditLog
        from django.test import Client
        client = Client()

        # 1. Failed login
        client.post(reverse("login"), {"email": "audit_patient@example.com", "password": "wrong"})
        failed_entry = AuditLog.objects.filter(action="LOGIN_FAILED").first()
        self.assertIsNotNone(failed_entry)
        self.assertEqual(failed_entry.extra_data.get("attempted_email"), "audit_patient@example.com")

        # 2. Successful login
        client.post(reverse("login"), {"email": "audit_patient@example.com", "password": self.password})
        login_entry = AuditLog.objects.filter(action="LOGIN", actor=self.patient_user).first()
        self.assertIsNotNone(login_entry)
        self.assertIn("logged in", login_entry.description.lower())

        # 3. Logout
        client.get(reverse("logout"))
        logout_entry = AuditLog.objects.filter(action="LOGOUT", actor=self.patient_user).first()
        self.assertIsNotNone(logout_entry)

    def test_admin_audit_trail_view_access(self):
        """Verify RBAC and rendering for the administrator audit logs trail."""
        from clinic.models import AuditLog
        AuditLog.objects.create(
            actor=self.admin_user,
            actor_email=self.admin_user.email,
            action="APPOINTMENT_CREATE",
            description="Appointment booked for testing",
        )

        # 1. Anonymous user cannot access
        anon_res = self.client.get(reverse("admin-audit-logs"))
        self.assertIn(anon_res.status_code, (302, 403))

        # 2. Patient user cannot access
        self.client.force_login(self.patient_user)
        patient_res = self.client.get(reverse("admin-audit-logs"))
        self.assertEqual(patient_res.status_code, 403)

        # 3. Admin user has full access
        self.client.force_login(self.admin_user)
        admin_res = self.client.get(reverse("admin-audit-logs"))
        self.assertEqual(admin_res.status_code, 200)
        self.assertContains(admin_res, "Audit Trail & Compliance Logs")
        self.assertContains(admin_res, "Appointment booked for testing")
        self.assertContains(admin_res, "Total Audit Events")

    def test_audit_log_admin_read_only(self):
        """Verify Django Admin registers AuditLog as read-only to preserve immutability."""
        from django.contrib.admin.sites import site
        from clinic.models import AuditLog
        from django.test import RequestFactory
        factory = RequestFactory()
        req = factory.get("/admin/clinic/auditlog/")
        req.user = self.admin_user

        model_admin = site._registry[AuditLog]
        self.assertFalse(model_admin.has_add_permission(req))
        self.assertEqual(len(model_admin.readonly_fields), len(AuditLog._meta.fields))



class Phase9DataBackupTests(TestCase):
    """
    Verification tests for Task 9.5: Data Backup Strategy.
    Validates management commands (backup_db, restore_db),
    integrity verification, gzip decompression, and automated retention rotation.
    """

    def setUp(self):
        import tempfile
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_find_postgres_binary_resolution(self):
        """Verify binary discovery locates pg_dump executable."""
        from clinic.management.commands.backup_db import find_postgres_binary
        bin_path = find_postgres_binary("pg_dump")
        self.assertIsNotNone(bin_path)
        self.assertTrue(os.path.isfile(bin_path))

    def test_backup_db_execution_and_audit(self):
        """Verify backup_db management command produces valid archive and audit record."""
        from django.core.management import call_command
        from io import StringIO
        from clinic.models import AuditLog

        out = StringIO()
        call_command("backup_db", output_dir=self.temp_dir, tag="unit-test", stdout=out)
        output_text = out.getvalue()

        self.assertIn("[OK] Database backup completed successfully!", output_text)

        # Verify archive file on disk
        dump_files = [f for f in os.listdir(self.temp_dir) if f.startswith("clinichub_backup_") and f.endswith(".dump")]
        self.assertEqual(len(dump_files), 1)
        backup_file = os.path.join(self.temp_dir, dump_files[0])
        self.assertGreater(os.path.getsize(backup_file), 1024)  # Greater than 1 KB

        # Verify AuditLog entry was recorded
        log_entry = AuditLog.objects.filter(description__icontains=dump_files[0]).first()
        self.assertIsNotNone(log_entry)
        self.assertEqual(log_entry.action, "SYSTEM")
        self.assertTrue(log_entry.extra_data.get("verified"))

    def test_backup_db_plain_gzip_format(self):
        """Verify plain format produces valid gzip compressed SQL."""
        from django.core.management import call_command
        from io import StringIO
        import gzip

        out = StringIO()
        call_command("backup_db", output_dir=self.temp_dir, format="plain", tag="plain-test", stdout=out)
        output_text = out.getvalue()
        self.assertIn("[OK] Database backup completed successfully!", output_text)

        sql_gz_files = [f for f in os.listdir(self.temp_dir) if f.endswith(".sql.gz")]
        self.assertEqual(len(sql_gz_files), 1)

        # Test decompression
        full_path = os.path.join(self.temp_dir, sql_gz_files[0])
        with gzip.open(full_path, "rt", encoding="utf-8", errors="ignore") as gz:
            content_start = gz.read(200)
            self.assertIn("PostgreSQL database dump", content_start)

    def test_restore_db_verify_only(self):
        """Verify restore_db command validates archive structure without destructive modification."""
        from django.core.management import call_command
        from io import StringIO

        out_backup = StringIO()
        call_command("backup_db", output_dir=self.temp_dir, tag="restore-test", stdout=out_backup)
        dump_file = [os.path.join(self.temp_dir, f) for f in os.listdir(self.temp_dir) if f.endswith(".dump")][0]

        out_restore = StringIO()
        call_command("restore_db", dump_file, verify_only=True, stdout=out_restore)
        restore_text = out_restore.getvalue()

        self.assertIn("[PASS] Custom archive TOC is valid!", restore_text)
        self.assertIn("Table Datasets", restore_text)

    def test_backup_retention_rotation(self):
        """Verify backups older than retention_days are pruned while new ones are kept."""
        from django.core.management import call_command
        import time

        # Create simulated old backup (15 days old)
        old_file = os.path.join(self.temp_dir, "clinichub_backup_20200101_000000_ancient.dump")
        with open(old_file, "wb") as f:
            f.write(b"SIMULATED_OLD_DUMP_CONTENT")
        past_timestamp = time.time() - (15 * 86400)
        os.utime(old_file, (past_timestamp, past_timestamp))

        self.assertTrue(os.path.exists(old_file))

        # Run backup with retention of 7 days
        call_command("backup_db", output_dir=self.temp_dir, retention_days=7, tag="new-run")

        # Old file must be pruned
        self.assertFalse(os.path.exists(old_file))

        # New file must exist
        new_files = [f for f in os.listdir(self.temp_dir) if "new-run" in f]
        self.assertEqual(len(new_files), 1)


class Phase9HealthCheckMonitoringTests(TestCase):
    """
    Verification tests for Task 9.6: Health Check & Monitoring Endpoints.
    Validates /health/ readiness probe, /healthz/ k8s liveness heartbeat,
    subsystem metrics (database latency, cache R/W, disk storage), and
    error status handling (503 Service Unavailable on database outage).
    """

    def setUp(self):
        from django.test import Client
        self.client = Client()

    def test_health_check_endpoint_returns_200_json(self):
        """Verify /health/ returns HTTP 200 with comprehensive subsystem health JSON."""
        response = self.client.get('/health/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/json')

        data = json.loads(response.content.decode('utf-8'))
        self.assertEqual(data.get('status'), 'healthy')
        self.assertIn('timestamp', data)
        self.assertIn('checks', data)

        checks = data['checks']
        # Database verification
        self.assertIn('database', checks)
        self.assertEqual(checks['database']['status'], 'ok')
        self.assertIn('latency_ms', checks['database'])

        # Cache verification
        self.assertIn('cache', checks)
        self.assertEqual(checks['cache']['status'], 'ok')
        self.assertIn('latency_ms', checks['cache'])

        # Storage verification
        self.assertIn('storage', checks)
        self.assertEqual(checks['storage']['status'], 'ok')
        self.assertIn('free_gb', checks['storage'])

    def test_health_check_text_format(self):
        """Verify /health/?format=text returns plain text OK for simple load-balancer monitors."""
        response = self.client.get('/health/?format=text')
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/plain', response['Content-Type'])
        self.assertEqual(response.content.decode('utf-8').strip(), 'OK')

    def test_healthz_liveness_probe(self):
        """Verify /healthz/ returns instant 200 OK for Kubernetes liveness probes."""
        response = self.client.get('/healthz/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/plain', response['Content-Type'])
        self.assertEqual(response.content.decode('utf-8').strip(), 'OK')

    def test_health_check_db_failure_returns_503(self):
        """Verify /health/ returns HTTP 503 Unhealthy when database is unreachable."""
        from unittest import mock

        with mock.patch('django.db.connection.cursor', side_effect=Exception('PostgreSQL database unreachable')):
            response = self.client.get('/health/')
            self.assertEqual(response.status_code, 503)

            data = json.loads(response.content.decode('utf-8'))
            self.assertEqual(data.get('status'), 'unhealthy')
            self.assertEqual(data['checks']['database']['status'], 'critical')
            self.assertIn('unreachable', data['checks']['database']['error'])
