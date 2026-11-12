from django.shortcuts import render, redirect
from django.contrib import messages
from .forms import SignupForm, LoginForm, UserProfileForm, PatientProfileUpdateForm
from django.core.signing import TimestampSigner, BadSignature, SignatureExpired
from django.core.mail import send_mail
from .models import User
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from django.conf import settings
from clinic.models import Patient




from ClinicHub.ratelimit import ratelimit, reset_rate_limit
from clinic.audit import log_audit_event

signer = TimestampSigner()

def splash_intro(request):
    return render(request, 'users/splash.html', {'is_standalone': True})


def home(request):
    from clinic.models import Department, Doctor
    departments = Department.objects.filter(is_active=True, is_deleted=False).order_by('name')
    doctors = Doctor.objects.filter(is_active=True, is_deleted=False).select_related('user', 'department').order_by('user__first_name')
    return render(request, 'users/home.html', {
        'departments': departments,
        'doctors': doctors,
    })


@ratelimit(group='signup', rate='10/h', key='ip', methods=['POST'], block=True)
def signup(request):
    if request.method == 'POST':
        form = SignupForm(request.POST)

        if form.is_valid():

            # Create User
            user = form.save(commit=False)

            user.set_password(form.cleaned_data['password'])
            user.user_type = "PATIENT"
            user.is_email_verified = False
            user.save()

            # Get/Create Patient profile
            patient = Patient.objects.get(user=user)

            patient.date_of_birth = form.cleaned_data.get('date_of_birth')
            patient.gender = form.cleaned_data.get('gender')
            patient.blood_group = form.cleaned_data.get('blood_group')
            patient.address = form.cleaned_data.get('address')
            patient.emergency_contact_name = form.cleaned_data.get(
                'emergency_contact_name'
            )
            patient.emergency_contact_phone = form.cleaned_data.get(
                'emergency_contact_phone'
            )

            patient.save()

            # Email verification
            token = signer.sign(str(user.pk))

            verification_path = reverse(
                'verify-email',
                kwargs={'token': token}
            )

            verification_link = request.build_absolute_uri(
                verification_path
            )

            send_mail(
                subject="Verify your ClinicHub email",
                message=(
                    f"Click this link to verify your email:\n\n"
                    f"{verification_link}"
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
            )

            log_audit_event(
                request=request,
                actor=user,
                action="PATIENT_REGISTER",
                target_object=user,
                description=f"Patient {user.email} registered",
            )
            return redirect('verification-sent')

    else:
        form = SignupForm()

    return render(
        request,
        'users/signup.html',
        {'form': form}
    )

@ratelimit(group='user_login', rate='5/m', key='ip', methods=['POST'], block=True)
def user_login(request):
    if request.method == 'POST':
        form = LoginForm(request.POST)
        if form.is_valid():
            email_value = form.cleaned_data['email']
            password_value = form.cleaned_data['password']
            user = authenticate(request,username=email_value,password=password_value)
            if user is not None:
                if not user.is_email_verified:
                    return render(request,
                        'users/login.html',
                        {
                            'form': form,
                            'error': 'Please verify your email first.'
                        }
                    )
                login(request, user)
                reset_rate_limit(request, group='user_login', key_type='ip')
                log_audit_event(
                    request=request,
                    actor=user,
                    action="LOGIN",
                    description=f"User {user.email} ({user.user_type}) logged in successfully",
                    extra_data={"user_type": user.user_type},
                )

                # Ensure superusers have ADMIN user_type if unset
                if user.is_superuser and not user.user_type:
                    user.user_type = "ADMIN"
                    user.save(update_fields=["user_type"])

                # Auto-ensure Patient profile exists for patient users
                if user.user_type == "PATIENT":
                    from clinic.models import Patient
                    Patient.objects.get_or_create(
                        user=user,
                        defaults={"is_active": True, "is_deleted": False}
                    )

                next_url = request.GET.get('next') or request.POST.get('next')
                if next_url:
                    # Filter out incompatible next_url destinations for staff, doctors, and admins
                    if ('/patient' in next_url or '/book-appointment' in next_url) and user.user_type in ['DOCTOR', 'STAFF', 'ADMIN']:
                        from clinic.models import Patient
                        if not Patient.objects.filter(user=user, is_deleted=False).exists():
                            next_url = None
                    elif '/doctor' in next_url and user.user_type != 'DOCTOR' and not user.is_superuser:
                        next_url = None
                    elif '/staff' in next_url and user.user_type != 'STAFF' and not user.is_superuser:
                        next_url = None
                    elif '/admin' in next_url and user.user_type != 'ADMIN' and not user.is_superuser:
                        next_url = None

                if next_url:
                    return redirect(next_url)

                if user.user_type == "PATIENT":
                    return redirect('patient-dashboard')
                elif user.user_type == "DOCTOR":
                    return redirect('doctor-dashboard')
                elif user.user_type == "STAFF":
                    return redirect('staff-dashboard')
                elif user.user_type == "ADMIN" or user.is_superuser:
                    return redirect('admin-dashboard')

                return redirect('home')
            else:
                log_audit_event(
                    request=request,
                    actor=None,
                    action="LOGIN_FAILED",
                    description=f"Failed login attempt for {email_value}",
                    extra_data={"attempted_email": email_value},
                )
                return render(request,'users/login.html',
                    {
                        'form': form,
                        'error': 'Invalid email or password.'
                    }
                )

    else:
        form = LoginForm()
    return render(request, 'users/login.html', {'form': form})


def user_logout(request):
    if request.user.is_authenticated:
        log_audit_event(
            request=request,
            actor=request.user,
            action="LOGOUT",
            description=f"User {request.user.email} logged out",
        )
    logout(request)

    return redirect('login')

def verification_sent(request):
    return render(request, 'users/verification_sent.html')

def verify_email(request, token):
    try:
        user_id = signer.unsign(token, max_age=86400)
        user = User.objects.get(pk=user_id)
        user.is_email_verified = True
        user.save()
        return render(request, 'users/email_verified.html')

    except (BadSignature, SignatureExpired):
        return render(request, 'users/verification_failed.html')


@login_required
def user_profile(request):
    user = request.user
    patient_profile = None
    patient_form = None

    if user.user_type == "PATIENT":
        patient_profile, _ = Patient.objects.get_or_create(
            user=user,
            defaults={"is_active": True, "is_deleted": False}
        )

    if request.method == "POST":
        # Check if user requested removing avatar
        if request.POST.get("remove_avatar") == "1":
            if user.profile_picture:
                user.profile_picture.delete(save=False)
                user.profile_picture = None
                user.save(update_fields=["profile_picture"])
                messages.success(request, "Profile picture removed successfully.")
                return redirect("user-profile")

        user_form = UserProfileForm(request.POST, request.FILES, instance=user)
        if patient_profile:
            patient_form = PatientProfileUpdateForm(request.POST, instance=patient_profile)

        is_valid = user_form.is_valid()
        if patient_form:
            is_valid = is_valid and patient_form.is_valid()

        if is_valid:
            user_form.save()
            if patient_form:
                patient_form.save()
            messages.success(request, "Your profile has been updated successfully!")
            return redirect("user-profile")
        else:
            messages.error(request, "Please correct the errors indicated below.")
    else:
        user_form = UserProfileForm(instance=user)
        if patient_profile:
            patient_form = PatientProfileUpdateForm(instance=patient_profile)

    context = {
        "user_form": user_form,
        "patient_form": patient_form,
        "patient_profile": patient_profile,
    }
    return render(request, "users/profile.html", context)



