import sys
from django.core.management.base import BaseCommand
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone


class Command(BaseCommand):
    help = "Send a test email via the configured Django EMAIL_BACKEND to verify SMTP connectivity."

    def add_arguments(self, parser):
        parser.add_argument(
            "recipient",
            nargs="?",
            type=str,
            default="",
            help="Recipient email address. Defaults to EMAIL_HOST_USER if omitted.",
        )
        parser.add_argument(
            "--subject",
            type=str,
            default="ClinicHub SMTP Test Email",
            help="Custom subject line for the test email.",
        )

    def handle(self, *args, **options):
        recipient = options.get("recipient") or getattr(settings, "EMAIL_HOST_USER", "")
        if not recipient:
            self.stderr.write(
                self.style.ERROR(
                    "No recipient specified and EMAIL_HOST_USER is not set in settings/.env."
                )
            )
            sys.exit(1)

        subject = options.get("subject") or "ClinicHub SMTP Test Email"
        now_str = timezone.now().strftime("%Y-%m-%d %H:%M:%S UTC")

        self.stdout.write(self.style.NOTICE("--- ClinicHub Email Diagnostics ---"))
        self.stdout.write(f"EMAIL_BACKEND: {settings.EMAIL_BACKEND}")
        self.stdout.write(f"EMAIL_HOST:    {getattr(settings, 'EMAIL_HOST', 'N/A')}")
        self.stdout.write(f"EMAIL_PORT:    {getattr(settings, 'EMAIL_PORT', 'N/A')}")
        self.stdout.write(f"EMAIL_USE_TLS: {getattr(settings, 'EMAIL_USE_TLS', 'N/A')}")
        self.stdout.write(f"FROM:          {settings.DEFAULT_FROM_EMAIL}")
        self.stdout.write(f"TO:            {recipient}")
        self.stdout.write(self.style.NOTICE("------------------------------------"))

        body = (
            f"Hello from ClinicHub!\n\n"
            f"This is an automated test message sent at {now_str}.\n"
            f"Your SMTP configuration is active and functioning correctly.\n\n"
            f"Backend: {settings.EMAIL_BACKEND}\n"
            f"Host: {getattr(settings, 'EMAIL_HOST', 'N/A')}:{getattr(settings, 'EMAIL_PORT', 'N/A')}\n"
            f"Sender: {settings.DEFAULT_FROM_EMAIL}\n\n"
            f"Best regards,\nClinicHub System"
        )

        try:
            sent_count = send_mail(
                subject=subject,
                message=body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[recipient],
                fail_silently=False,
            )
            if sent_count > 0:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"SUCCESS: Test email successfully dispatched to {recipient}!"
                    )
                )
            else:
                self.stderr.write(
                    self.style.WARNING("WARNING: send_mail completed but reported 0 emails sent.")
                )
        except Exception as exc:
            self.stderr.write(
                self.style.ERROR(f"ERROR: Failed to dispatch test email: {exc}")
            )
            sys.exit(1)
