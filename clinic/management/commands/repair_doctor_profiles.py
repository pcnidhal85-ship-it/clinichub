from django.core.management.base import BaseCommand
from clinic.services import repair_unlinked_doctor_accounts, link_or_create_doctor_profile
from users.models import User
from clinic.models import Department


class Command(BaseCommand):
    help = "Finds and safely links Doctor profiles for existing User accounts with user_type='DOCTOR'."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Simulate the repair and preview actions without modifying the database.',
        )
        parser.add_argument(
            '--username',
            type=str,
            default=None,
            help='Target a specific username (e.g. doctor1 or doctor2).',
        )
        parser.add_argument(
            '--department',
            type=str,
            default=None,
            help='Specific department name or ID to assign (optional override).',
        )
        parser.add_argument(
            '--license-number',
            type=str,
            default=None,
            help='Specific license number to assign (optional override).',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        username = options['username']
        department = options['department']
        license_number = options['license_number']

        self.stdout.write(self.style.NOTICE(
            f"=== ClinicHub Doctor Profile Audit & Repair {'(DRY RUN)' if dry_run else ''} ==="
        ))

        if username and (department or license_number):
            # Targeted single user repair with explicit parameters
            try:
                user = User.objects.get(username=username)
            except User.DoesNotExist:
                self.stderr.write(self.style.ERROR(f"User with username '{username}' does not exist."))
                return

            if dry_run:
                self.stdout.write(f"[DRY RUN] Would link '{username}' with Department: {department}, License: {license_number}")
                return

            try:
                doctor, created = link_or_create_doctor_profile(
                    user=user,
                    department=department,
                    license_number=license_number,
                )
                action = "Created" if created else "Updated"
                self.stdout.write(self.style.SUCCESS(
                    f"[SUCCESS] {action} Doctor profile #{doctor.id} for user '{username}': "
                    f"Department='{doctor.department.name}', License='{doctor.license_number}'"
                ))
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"[ERROR] Failed to link profile: {e}"))
            return

        # General or single username batch scan
        results = repair_unlinked_doctor_accounts(dry_run=dry_run, username=username)

        if not results:
            self.stdout.write(self.style.SUCCESS("No accounts with user_type='DOCTOR' found."))
            return

        repaired_count = 0
        already_linked_count = 0
        error_count = 0

        for r in results:
            status = r['status']
            user_label = f"{r['username']} ({r['email']})"

            if status == 'already_linked':
                already_linked_count += 1
                self.stdout.write(self.style.SUCCESS(
                    f"  [OK] {user_label} -> Already linked to Doctor #{r['doctor_id']} ({r['department']}, License: {r['license_number']})"
                ))
            elif status == 'dry_run_ready':
                repaired_count += 1
                self.stdout.write(self.style.WARNING(
                    f"  [NEEDS PROFILE] {user_label} -> Would assign Department='{r['department']}', Specialization='{r['specialization']}', License='{r['license_number']}'"
                ))
            elif status == 'repaired':
                repaired_count += 1
                self.stdout.write(self.style.SUCCESS(
                    f"  [REPAIRED] {user_label} -> Created Doctor #{r['doctor_id']} ({r['department']}, License: {r['license_number']})"
                ))
            elif status == 'error':
                error_count += 1
                self.stderr.write(self.style.ERROR(
                    f"  [ERROR] {user_label} -> {r['message']}"
                ))

        self.stdout.write("")
        summary_msg = f"Audit Complete. Already Linked: {already_linked_count}, Repaired/Actionable: {repaired_count}, Errors: {error_count}"
        if error_count > 0:
            self.stdout.write(self.style.WARNING(summary_msg))
        else:
            self.stdout.write(self.style.SUCCESS(summary_msg))
