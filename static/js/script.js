// static/js/script.js
// Defer non-critical JavaScript

// Cart quantity update with debounce for performance
let updateTimeout;
function debouncedUpdateCart(mid, action) {
    clearTimeout(updateTimeout);
    updateTimeout = setTimeout(() => {
        fetch(`/update_cart/${mid}/${action}`, { method: 'GET' })
            .then(() => window.location.reload());
    }, 300);
}

// Lazy load images
document.addEventListener('DOMContentLoaded', function() {
    const images = document.querySelectorAll('img[data-src]');
    const imageObserver = new IntersectionObserver((entries, observer) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                const img = entry.target;
                img.src = img.dataset.src;
                img.removeAttribute('data-src');
                imageObserver.unobserve(img);
            }
        });
    });

    images.forEach(img => imageObserver.observe(img));
});