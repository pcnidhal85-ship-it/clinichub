"""
ClinicHub Rate Limiting Framework
Cache-backed sliding/fixed window rate limiting protecting authentication
and API endpoints against brute-force attacks, credential stuffing, and DDoS.
"""

import time
import functools
from django.core.cache import cache
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render


def get_client_ip(request):
    """
    Extracts the client's actual IP address, honoring reverse proxies
    (e.g., Nginx, Cloudflare, load balancers).
    """
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        ip = x_forwarded_for.split(",")[0].strip()
    else:
        ip = request.META.get("REMOTE_ADDR", "127.0.0.1")
    return ip


def parse_rate(rate_string):
    """
    Parses a rate string such as '5/m', '10/s', '100/h', '1000/d', '5/10m'.
    Returns (count, period_in_seconds).
    """
    parts = rate_string.lower().split("/")
    if len(parts) != 2:
        raise ValueError(f"Invalid rate format: '{rate_string}'. Expected format: 'count/period' (e.g. '5/m').")

    count = int(parts[0])
    period_str = parts[1].strip()

    multipliers = {
        "s": 1,
        "sec": 1,
        "second": 1,
        "m": 60,
        "min": 60,
        "minute": 60,
        "h": 3600,
        "hr": 3600,
        "hour": 3600,
        "d": 86400,
        "day": 86400,
    }

    # Support prefix numbers in period e.g. '10m', '15m'
    digits = ""
    unit = ""
    for ch in period_str:
        if ch.isdigit():
            digits += ch
        else:
            unit += ch

    num_units = int(digits) if digits else 1
    unit = unit.strip()

    if unit not in multipliers:
        raise ValueError(f"Unknown time unit '{unit}' in rate string '{rate_string}'.")

    seconds = num_units * multipliers[unit]
    return count, seconds


def build_cache_key(request, group, key_type):
    """Generates a namespaced cache key based on the key type."""
    ip = get_client_ip(request)

    if key_type == "ip":
        identifier = ip
    elif key_type == "user":
        if hasattr(request, "user") and request.user.is_authenticated:
            identifier = f"user_{request.user.pk}"
        else:
            identifier = f"anon_{ip}"
    elif key_type.startswith("post:"):
        field = key_type.split(":", 1)[1]
        val = request.POST.get(field, "").strip().lower() if request.method == "POST" else ""
        identifier = f"{field}_{val}_{ip}" if val else ip
    else:
        identifier = ip

    return f"ratelimit:{group}:{identifier}"


def is_rate_limited(request, group="default", rate="60/m", key_type="ip", increment=True):
    """
    Checks if a request exceeds the specified rate limit.
    Returns (is_limited, retry_after_seconds).
    """
    limit, period = parse_rate(rate)
    cache_key = build_cache_key(request, group, key_type)

    current_time = time.time()
    record = cache.get(cache_key)

    if not record:
        if increment:
            cache.set(cache_key, {"count": 1, "reset_at": current_time + period}, timeout=period)
        return False, 0

    count = record.get("count", 0)
    reset_at = record.get("reset_at", current_time + period)
    retry_after = max(1, int(reset_at - current_time))

    if count >= limit:
        return True, retry_after

    if increment:
        record["count"] = count + 1
        cache.set(cache_key, record, timeout=retry_after)

    return False, 0


def reset_rate_limit(request, group="default", key_type="ip"):
    """Resets the rate limit counter for a given key (e.g. after successful login)."""
    cache_key = build_cache_key(request, group, key_type)
    cache.delete(cache_key)


def ratelimit(group=None, rate="60/m", key="ip", methods=None, block=True):
    """
    Decorator for views to enforce rate limiting.

    :param group: Unique group name for the limit pool (defaults to view name).
    :param rate: Rate limit string, e.g. '5/m', '3/15m', '60/m'.
    :param key: 'ip', 'user', or 'post:<field>'.
    :param methods: List of HTTP methods to apply to (e.g. ['POST'], ['GET', 'POST']), None for all.
    :param block: If True, returns HTTP 429 when limit is exceeded.
    """
    if methods is not None:
        methods = [m.upper() for m in methods]

    def decorator(view_func):
        nonlocal group
        if group is None:
            group = f"{view_func.__module__}.{view_func.__name__}"

        @functools.wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if methods is None or request.method in methods:
                limited, retry_after = is_rate_limited(
                    request,
                    group=group,
                    rate=rate,
                    key_type=key,
                    increment=True,
                )
                request.limited = limited
                request.rate_limit_retry_after = retry_after

                if limited and block:
                    return build_rate_limit_response(request, retry_after=retry_after)

            return view_func(request, *args, **kwargs)

        return _wrapped_view

    return decorator


def build_rate_limit_response(request, retry_after=60):
    """
    Constructs an HTTP 429 Too Many Requests response tailored to the client.
    Returns JSON for AJAX/API requests, or a rendered 429 HTML page for browser requests.
    """
    is_ajax = (
        request.headers.get("x-requested-with") == "XMLHttpRequest"
        or request.path.startswith("/api/")
        or request.path.startswith("/clinic/api/")
        or "application/json" in request.headers.get("Accept", "")
    )

    if is_ajax:
        response = JsonResponse(
            {
                "error": "Too many requests. Please slow down and try again later.",
                "retry_after": retry_after,
            },
            status=429,
        )
    else:
        try:
            response = render(
                request,
                "429.html",
                {
                    "retry_after": retry_after,
                    "request_path": request.path,
                },
                status=429,
            )
        except Exception:
            response = HttpResponse(
                f"<h1>429 Too Many Requests</h1><p>Please wait {retry_after} seconds before trying again.</p>",
                status=429,
                content_type="text/html",
            )

    response["Retry-After"] = str(retry_after)
    return response

