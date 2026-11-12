/**
 * ClinicHub — Splash Overlay Engine
 * Supports standalone redirect (/splash/) and first-time session intro on Home page.
 */
(function() {
    'use strict';

    function initSplash() {
        const overlay = document.getElementById('clinichubSplashOverlay');
        if (!overlay) return;

        const isStandalone = overlay.getAttribute('data-standalone') === 'true' || overlay.classList.contains('is-standalone');
        const targetUrl = overlay.getAttribute('data-target-url') || '/';
        const progressFill = document.getElementById('splashProgressFill');
        const skipBtn = document.getElementById('splashSkipBtn');

        // If on home/embedded page and already viewed in this session, hide immediately
        if (!isStandalone) {
            try {
                if (sessionStorage.getItem('clinichub_splash_shown') === 'true') {
                    overlay.classList.add('hidden');
                    return;
                }
            } catch (e) {
                // Storage access restricted (e.g. iframe or strict privacy)
            }
        }

        let isDismissed = false;

        function dismissSplash() {
            if (isDismissed) return;
            isDismissed = true;

            if (isStandalone) {
                window.location.href = targetUrl;
            } else {
                try {
                    sessionStorage.setItem('clinichub_splash_shown', 'true');
                } catch (e) {}

                overlay.classList.add('fade-out');
                setTimeout(function() {
                    overlay.classList.add('hidden');
                }, 460);
            }
        }

        if (skipBtn) {
            skipBtn.addEventListener('click', function(e) {
                e.preventDefault();
                dismissSplash();
            });
        }

        overlay.addEventListener('click', function(e) {
            if (e.target === overlay) {
                dismissSplash();
            }
        });

        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape' && !isDismissed) {
                dismissSplash();
            }
        });

        // Animate progress bar over 1200ms
        const duration = 1200;
        const intervalTime = 20;
        const totalSteps = duration / intervalTime;
        let currentStep = 0;

        const progressInterval = setInterval(function() {
            if (isDismissed) {
                clearInterval(progressInterval);
                return;
            }

            currentStep++;
            const pct = Math.min(100, Math.round((currentStep / totalSteps) * 100));

            if (progressFill) {
                progressFill.style.width = pct + '%';
            }

            if (currentStep >= totalSteps) {
                clearInterval(progressInterval);
                dismissSplash();
            }
        }, intervalTime);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initSplash);
    } else {
        initSplash();
    }
})();
