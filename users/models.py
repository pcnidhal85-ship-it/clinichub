from django.db import models
from django.contrib.auth.models import AbstractBaseUser,BaseUserManager,PermissionsMixin
import uuid

# Create your models here.
class UserManager(BaseUserManager):
    def create_user(self,email,password=None,**extra_fields):
        if not email:
            raise ValueError("Email is required")
        user = self.model(email = self.normalize_email(email),**extra_fields)

        user.set_password(password)
        user.save(using=self._db)

        return user

    def create_superuser(self,email,password=None,**extra_fields):
        user = self.create_user(email=email,password=password,**extra_fields)

        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        user.is_email_verified = True

        user.save(using=self._db)

        return user


class User(AbstractBaseUser,PermissionsMixin):
    id = models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    email = models.EmailField(unique=True)
    username = models.CharField(max_length=100,unique=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    phone_number = models.CharField(max_length=15,blank=True,null=True)


    USER_TYPE_CHOICES = [
        ('ADMIN','Admin'),
        ('DOCTOR','Doctor'),
        ('STAFF','Staff'),
        ('PATIENT','Patient')
    ]


    user_type = models.CharField(max_length=20,choices=USER_TYPE_CHOICES)

    DOCTOR_ROLE_CHOICES = [
        ('GENERAL', 'General Physician'),
        ('CARDIOLOGIST', 'Cardiologist'),
        ('ORTHOPEDIST', 'Orthopedist'),
        ('DERMATOLOGIST', 'Dermatologist'),
        ('ENT', 'ENT Specialist'),
        ('OPHTHALMOLOGIST', 'Ophthalmologist'),
        ('PEDIATRICIAN', 'Pediatrician')]

    doctor_role = models.CharField(max_length=30,choices=DOCTOR_ROLE_CHOICES,null=True,blank=True)

    STAFF_ROLE_CHOICES = [
        ('RECEPTIONIST', 'Receptionist'),
        ('NURSE', 'Nurse'),
        ('LAB_TECHNICIAN', 'Lab Technician'),
        ('PHARMACIST', 'Pharmacist'),
        ('ACCOUNTANT', 'Accountant'),
    ]

    staff_role = models.CharField(max_length=30,choices=STAFF_ROLE_CHOICES,null=True,blank=True)


    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    is_delete = models.BooleanField(default=False)
    is_email_verified = models.BooleanField(default=False)

    date_joined =  models.DateTimeField(auto_now_add=True)

    objects=UserManager()

    USERNAME_FIELD ='email'
    REQUIRED_FIELDS = ['username']

    def get_full_name(self):
        full_name = f"{self.first_name} {self.last_name}".strip()
        return full_name if full_name else self.username

    def get_short_name(self):
        return self.first_name or self.username

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.user_type == 'DOCTOR' and not self.doctor_role:
            raise ValidationError("Doctor role is required for doctors.")

        if self.user_type == 'STAFF' and not self.staff_role:
            raise ValidationError("Staff role is required for staff")

        if self.user_type in ['ADMIN','PATIENT']:
            self.doctor_role = None
            self.staff_role = None