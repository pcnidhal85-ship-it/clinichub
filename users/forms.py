from django import forms
from .models import User
from clinic.models import Patient


class SignupForm(forms.ModelForm):
    password = forms.CharField(
        widget=forms.PasswordInput
    )

    confirm_password = forms.CharField(
        widget=forms.PasswordInput
    )

    date_of_birth = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date'})
    )

    gender = forms.ChoiceField(
        choices=Patient.GENDER_CHOICES,
        required=False
    )

    blood_group = forms.CharField(
        max_length=5,
        required=False
    )

    address = forms.CharField(
        widget=forms.Textarea,
        required=False
    )

    emergency_contact_name = forms.CharField(
        max_length=100,
        required=False
    )

    emergency_contact_phone = forms.CharField(
        max_length=15,
        required=False
    )

    class Meta:
        model = User
        fields = [
            'email',
            'username',
            'first_name',
            'last_name',
            'phone_number',
            'password',
        ]

    def clean(self):
        cleaned_data = super().clean()

        password = cleaned_data.get('password')
        confirm_password = cleaned_data.get('confirm_password')

        if password != confirm_password:
            raise forms.ValidationError(
                "Passwords do not match."
            )

        return cleaned_data

class LoginForm(forms.Form):
    email = forms.EmailField()
    password = forms.CharField(
        widget=forms.PasswordInput
    )