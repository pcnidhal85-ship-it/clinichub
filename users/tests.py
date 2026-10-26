from django.test import TestCase
from django.urls import reverse
from users.models import User


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
