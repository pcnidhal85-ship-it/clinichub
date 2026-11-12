"""
ClinicHub Production Health Check & Monitoring Probes
Supports Kubernetes liveness/readiness, Docker healthchecks, and cloud load balancers.
"""
import time
import shutil
import logging
from django.http import JsonResponse, HttpResponse
from django.db import connection
from django.core.cache import cache
from django.conf import settings
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger('clinichub.health')


@csrf_exempt
def health_check(request):
    """
    Comprehensive system readiness & health check probe.
    Returns HTTP 200 if all critical components are operational.
    Returns HTTP 503 Service Unavailable if any critical component (e.g. database) fails.
    """
    checks = {}
    is_healthy = True

    # 1. Database Connectivity & Query Latency Check
    t0 = time.perf_counter()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1;")
            cursor.fetchone()
        db_latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        checks['database'] = {
            'status': 'ok',
            'latency_ms': db_latency_ms,
            'engine': connection.settings_dict.get('ENGINE', 'unknown').split('.')[-1],
        }
    except Exception as exc:
        is_healthy = False
        db_latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        logger.error(f"Health check DB probe failed: {exc}")
        checks['database'] = {
            'status': 'critical',
            'latency_ms': db_latency_ms,
            'error': str(exc),
        }

    # 2. Cache Connectivity & R/W Check
    t0 = time.perf_counter()
    try:
        test_key = f"_health_probe_{int(time.time())}"
        cache.set(test_key, "ok", timeout=15)
        val = cache.get(test_key)
        cache.delete(test_key)
        cache_latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        if val == "ok":
            checks['cache'] = {
                'status': 'ok',
                'latency_ms': cache_latency_ms,
                'backend': settings.CACHES.get('default', {}).get('BACKEND', 'unknown').split('.')[-1],
            }
        else:
            checks['cache'] = {
                'status': 'warning',
                'latency_ms': cache_latency_ms,
                'message': 'Cache write succeeded but read returned unexpected value',
            }
    except Exception as exc:
        logger.warning(f"Health check cache probe warning: {exc}")
        checks['cache'] = {
            'status': 'warning',
            'error': str(exc),
        }

    # 3. Disk Space & Storage Check
    try:
        check_path = getattr(settings, 'MEDIA_ROOT', None) or getattr(settings, 'BASE_DIR', '.')
        total, used, free = shutil.disk_usage(check_path)
        free_gb = round(free / (1024 ** 3), 2)
        total_gb = round(total / (1024 ** 3), 2)
        percent_free = round((free / total) * 100, 1)

        checks['storage'] = {
            'status': 'ok' if percent_free > 5.0 else 'warning',
            'free_gb': free_gb,
            'total_gb': total_gb,
            'percent_free': percent_free,
        }
    except Exception as exc:
        checks['storage'] = {
            'status': 'warning',
            'error': str(exc),
        }

    status_code = 200 if is_healthy else 503
    payload = {
        'status': 'healthy' if is_healthy else 'unhealthy',
        'timestamp': timezone.now().isoformat(),
        'environment': 'development' if settings.DEBUG else 'production',
        'version': '1.0.0',
        'checks': checks,
    }

    # Plain text format probe for simple uptime checkers
    if request.GET.get('format') == 'text':
        return HttpResponse('OK\n' if is_healthy else 'FAIL\n', status=status_code, content_type='text/plain')

    return JsonResponse(payload, status=status_code)


@csrf_exempt
def healthz_liveness(request):
    """
    Lightweight Kubernetes liveness probe (Heartbeat).
    Returns instant HTTP 200 OK without running heavy subsystem probes.
    """
    return HttpResponse('OK\n', status=200, content_type='text/plain')

