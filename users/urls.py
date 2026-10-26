from django.urls import path
from .views import home,signup,verification_sent,verify_email,user_login,user_logout
from django.contrib.auth import views as auth_views

urlpatterns = [
    path('', home, name='home'),
    path('signup/', signup, name='signup'),
    path('verification-sent/',verification_sent,name='verification-sent'),
    path('verify-email/<str:token>/', verify_email, name='verify-email'),
    path('login/', user_login, name='login'),
    path('logout/', user_logout, name='logout'),


    path('forgot-password/',auth_views.PasswordResetView.as_view(template_name='users/forgot_password.html',success_url='/password-reset-sent/'),
        name='forgot-password' ),
    path('password-reset-sent/',auth_views.PasswordResetDoneView.as_view(template_name='users/password_reset_sent.html'),
         name='password_reset_done'),
    path('reset-password/<uidb64>/<token>/',
    auth_views.PasswordResetConfirmView.as_view(template_name='users/password_reset_confirm.html',success_url='/password-reset-complete/'),
         name='password_reset_confirm'),
    path('password-reset-complete/',
    auth_views.PasswordResetCompleteView.as_view(template_name='users/password_reset_complete.html'),
         name='password_reset_complete'),
]