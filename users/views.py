from django.shortcuts import render,redirect
from .forms import SignupForm,LoginForm
from django.core.signing import TimestampSigner,BadSignature,SignatureExpired
from django.core.mail import send_mail
from .models import User
from django.contrib.auth import authenticate,login,logout
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from django.conf import settings
from clinic.models import Patient




signer = TimestampSigner()
@login_required()
def home(request):
    return render(request, 'users/home.html')

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

            return redirect('verification-sent')

    else:
        form = SignupForm()

    return render(
        request,
        'users/signup.html',
        {'form': form}
    )

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


