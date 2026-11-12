/**
 * ClinicHub — Interactive Hero & Virtual Tour Controller
 * - Scene Switcher (Pill-based photo & tour switcher)
 * - Virtual Clinic Tour Modal (Multi-step guided tour carousel)
 * - Quick Booking Dock UX enhancements (Department-Doctor reactive filtering & min date)
 */
(function() {
    'use strict';

    let currentSlideIndex = 0;
    let tourSlides = [];
    let tourDots = [];
    let modalEl = null;
    let backdropEl = null;

    // Expose tour navigation functions globally for inline HTML event handlers
    window.goToTourSlide = function(index) {
        if (!tourSlides.length) {
            tourSlides = document.querySelectorAll('.tour-slide');
            tourDots = document.querySelectorAll('.tour-dot');
        }
        if (!tourSlides.length) return;

        if (index < 0) index = tourSlides.length - 1;
        if (index >= tourSlides.length) index = 0;
        currentSlideIndex = index;

        tourSlides.forEach(function(slide, i) {
            if (i === currentSlideIndex) {
                slide.style.display = 'block';
                slide.style.animation = 'fadeIn 0.3s ease';
            } else {
                slide.style.display = 'none';
            }
        });

        tourDots.forEach(function(dot, i) {
            if (i === currentSlideIndex) {
                dot.classList.add('active');
            } else {
                dot.classList.remove('active');
            }
        });
    };

    window.nextTourSlide = function() {
        goToTourSlide(currentSlideIndex + 1);
    };

    window.prevTourSlide = function() {
        goToTourSlide(currentSlideIndex - 1);
    };

    window.openClinicTourModal = function() {
        if (!modalEl) modalEl = document.getElementById('clinicTourModal');
        if (!backdropEl) backdropEl = document.getElementById('clinicTourBackdrop');

        if (backdropEl) backdropEl.classList.add('show');
        if (modalEl) {
            modalEl.classList.add('show');
            modalEl.setAttribute('aria-hidden', 'false');
        }
        document.body.style.overflow = 'hidden';
        goToTourSlide(0);
    };

    window.closeClinicTourModal = function() {
        if (!modalEl) modalEl = document.getElementById('clinicTourModal');
        if (!backdropEl) backdropEl = document.getElementById('clinicTourBackdrop');

        if (backdropEl) backdropEl.classList.remove('show');
        if (modalEl) {
            modalEl.classList.remove('show');
            modalEl.setAttribute('aria-hidden', 'true');
        }
        document.body.style.overflow = '';
    };

    function initVideoAndTour() {
        modalEl = document.getElementById('clinicTourModal');
        backdropEl = document.getElementById('clinicTourBackdrop');
        tourSlides = document.querySelectorAll('.tour-slide');
        tourDots = document.querySelectorAll('.tour-dot');

        // 1. Scene Switcher Pills
        const heroVideo = document.getElementById('heroVideoElement');
        const scenePills = document.querySelectorAll('.video-scene-pill, .hero-scene-pill');

        if (heroVideo && scenePills.length > 0) {
            scenePills.forEach(function(pill) {
                pill.addEventListener('click', function(e) {
                    e.preventDefault();
                    scenePills.forEach(p => p.classList.remove('active'));
                    pill.classList.add('active');

                    const newSrc = pill.getAttribute('data-src');
                    if (newSrc && newSrc !== heroVideo.getAttribute('src')) {
                        heroVideo.style.opacity = '0.35';
                        heroVideo.style.transform = 'scale(0.98)';
                        heroVideo.style.transition = 'opacity 0.25s ease, transform 0.25s ease';

                        setTimeout(function() {
                            heroVideo.setAttribute('src', newSrc);
                            heroVideo.style.opacity = '1';
                            heroVideo.style.transform = 'scale(1)';
                        }, 200);
                    }
                });
            });
        }

        // 2. Open / Close Modal Event Handlers
        const openBtn = document.getElementById('openClinicTourModalBtn');
        const closeBtn = document.getElementById('closeClinicTourModalBtn');

        if (openBtn) {
            openBtn.addEventListener('click', function(e) {
                e.preventDefault();
                window.openClinicTourModal();
            });
        }

        if (closeBtn) {
            closeBtn.addEventListener('click', function(e) {
                e.preventDefault();
                window.closeClinicTourModal();
            });
        }

        if (backdropEl) {
            backdropEl.addEventListener('click', function() {
                window.closeClinicTourModal();
            });
        }

        document.addEventListener('keydown', function(e) {
            if (modalEl && modalEl.classList.contains('show')) {
                if (e.key === 'Escape') {
                    window.closeClinicTourModal();
                } else if (e.key === 'ArrowRight') {
                    window.nextTourSlide();
                } else if (e.key === 'ArrowLeft') {
                    window.prevTourSlide();
                }
            }
        });

        // 3. Department & Doctor Reactive Filter in Glass Booking Bar
        const deptSelect = document.getElementById('heroBookingDept');
        const docSelect = document.getElementById('heroBookingDoctor');
        const dateInput = document.getElementById('heroBookingDate');

        // Set minimum date to today
        if (dateInput) {
            const today = new Date().toISOString().split('T')[0];
            dateInput.min = today;
            if (!dateInput.value) {
                dateInput.value = today;
            }
        }

        if (deptSelect && docSelect) {
            const originalDoctorOptions = Array.from(docSelect.querySelectorAll('option'));

            deptSelect.addEventListener('change', function() {
                const selectedDeptId = deptSelect.value;

                docSelect.innerHTML = '';
                originalDoctorOptions.forEach(function(opt) {
                    if (!opt.value || !selectedDeptId || opt.getAttribute('data-dept-id') === selectedDeptId) {
                        docSelect.appendChild(opt.cloneNode(true));
                    }
                });
                docSelect.value = '';
            });
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initVideoAndTour);
    } else {
        initVideoAndTour();
    }
})();
