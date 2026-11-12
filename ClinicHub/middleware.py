"""
ClinicHub Security Middleware
Provides Content Security Policy (CSP), Permissions Policy, and HTTP security headers
to protect against Cross-Site Scripting (XSS), clickjacking, MIME-confusion,
and unauthorized resource access across all views.
"""

from django.conf import settings


class SecurityHeadersMiddleware:
    """
    Middleware that attaches comprehensive security headers to all HTTP responses,
    including Content-Security-Policy (CSP) and Permissions-Policy.
    """

    DEFAULT_CSP = {
        "default-src": ["'self'"],
        "script-src": [
            "'self'",
            "'unsafe-inline'",
            "'unsafe-eval'",
            "https://cdnjs.cloudflare.com",
            "https://cdn.jsdelivr.net",
        ],
        "style-src": [
            "'self'",
            "'unsafe-inline'",
            "https://fonts.googleapis.com",
            "https://cdnjs.cloudflare.com",
            "https://cdn.jsdelivr.net",
        ],
        "font-src": [
            "'self'",
            "data:",
            "https://fonts.gstatic.com",
            "https://cdnjs.cloudflare.com",
        ],
        "img-src": ["'self'", "data:", "blob:", "https:"],
        "media-src": ["'self'", "data:", "blob:"],
        "connect-src": ["'self'", "ws:", "wss:"],
        "frame-ancestors": ["'none'"],
        "base-uri": ["'self'"],
        "form-action": ["'self'"],
    }

    DEFAULT_PERMISSIONS_POLICY = (
        "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    )

    def __init__(self, get_response):
        self.get_response = get_response
        self.csp_enabled = getattr(settings, "CSP_ENABLED", True)
        self.permissions_policy = getattr(
            settings, "PERMISSIONS_POLICY", self.DEFAULT_PERMISSIONS_POLICY
        )
        self.csp_header_value = self._build_csp_string()

    def _build_csp_string(self):
        policy_dict = getattr(settings, "CSP_POLICY", self.DEFAULT_CSP)
        directives = []
        for directive, sources in policy_dict.items():
            sources_str = " ".join(sources)
            directives.append(f"{directive} {sources_str}")
        return "; ".join(directives)

    def __call__(self, request):
        response = self.get_response(request)

        # Enforce Content-Security-Policy (CSP)
        if self.csp_enabled and "Content-Security-Policy" not in response:
            response["Content-Security-Policy"] = self.csp_header_value

        # Enforce Permissions-Policy
        if self.permissions_policy and "Permissions-Policy" not in response:
            response["Permissions-Policy"] = self.permissions_policy

        # Modern Cross-Origin Policies
        if "Cross-Origin-Opener-Policy" not in response:
            response["Cross-Origin-Opener-Policy"] = getattr(
                settings, "SECURE_CROSS_ORIGIN_OPENER_POLICY", "same-origin"
            )
        if "Cross-Origin-Resource-Policy" not in response:
            response["Cross-Origin-Resource-Policy"] = "same-origin"

        # Explicit fallback for X-Content-Type-Options
        if "X-Content-Type-Options" not in response:
            response["X-Content-Type-Options"] = "nosniff"

        # Explicit fallback for X-Frame-Options (Clickjacking defense)
        if "X-Frame-Options" not in response:
            response["X-Frame-Options"] = getattr(settings, "X_FRAME_OPTIONS", "DENY")

        return response

