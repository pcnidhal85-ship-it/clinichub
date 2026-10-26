from django.contrib import admin
from .models import User
from clinic.models import Doctor


class DoctorInline(admin.StackedInline):
    model = Doctor
    can_delete = False
    verbose_name_plural = 'Doctor Profile'
    fk_name = 'user'
    extra = 0
    fields = ('department', 'specialization', 'license_number', 'is_active')


@admin.register(User)
class CustomUserAdmin(admin.ModelAdmin):
    list_display = (
        'username',
        'email',
        'first_name',
        'last_name',
        'user_type',
        'doctor_role',
        'doctor_profile_status',
        'is_active',
        'date_joined',
    )
    list_filter = ('user_type', 'doctor_role', 'is_active', 'is_email_verified')
    search_fields = ('username', 'email', 'first_name', 'last_name')

    def get_inline_instances(self, request, obj=None):
        if obj and obj.user_type == 'DOCTOR':
            return [DoctorInline(self.model, self.admin_site)]
        if not obj:
            return [DoctorInline(self.model, self.admin_site)]
        return []

    @admin.display(description='Doctor Profile')
    def doctor_profile_status(self, obj):
        if obj.user_type == 'DOCTOR':
            if hasattr(obj, 'doctor_profile') and obj.doctor_profile and not obj.doctor_profile.is_deleted:
                return f"✅ Linked ({obj.doctor_profile.department.name})"
            return "❌ Missing Profile"
        return "—"