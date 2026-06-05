/* Naukri Dashboard — Auto-refresh and interaction scripts */

(function () {
    'use strict';

    const REFRESH_INTERVAL = 60; // seconds
    let countdown = REFRESH_INTERVAL;
    const timerEl = document.getElementById('refreshTimer');

    function updateTimer() {
        countdown--;
        if (timerEl) {
            timerEl.textContent = `Refresh in ${countdown}s`;
        }
        if (countdown <= 0) {
            location.reload();
        }
    }

    // Start countdown
    setInterval(updateTimer, 1000);

    // Animate stat cards on load
    document.querySelectorAll('.stat-card, .mini-stat-card, .glass-card, .job-card').forEach((el, i) => {
        el.style.opacity = '0';
        el.style.transform = 'translateY(12px)';
        el.style.transition = 'opacity 0.4s ease, transform 0.4s ease';
        setTimeout(() => {
            el.style.opacity = '1';
            el.style.transform = 'translateY(0)';
        }, 60 + i * 40);
    });

    // Close offcanvas on link click (mobile)
    document.querySelectorAll('.mobile-nav-link').forEach(link => {
        link.addEventListener('click', () => {
            const offcanvas = bootstrap.Offcanvas.getInstance(
                document.getElementById('mobileNav')
            );
            if (offcanvas) offcanvas.hide();
        });
    });

})();
