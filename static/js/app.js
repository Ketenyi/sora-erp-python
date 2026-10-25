// assets/js/app.js
// SORA ERP Main JavaScript

// Mobile menu toggle
document.addEventListener('DOMContentLoaded', function() {
    // Initialize tooltips
    initTooltips();
    
    // Initialize modals
    initModals();
    
    // Initialize forms
    initForms();
    
    // Auto-hide alerts after 5 seconds
    autoHideAlerts();
});

// Mobile menu toggle
function toggleSidebar() {
    document.querySelector('.sidebar').classList.toggle('open');
}

// Modal functions
function initModals() {
    // Close modal when clicking on backdrop
    document.querySelectorAll('.modal').forEach(modal => {
        modal.addEventListener('click', function(e) {
            if (e.target === this) {
                closeModal(this.id);
            }
        });
    });
}

function openModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.classList.add('show');
        document.body.style.overflow = 'hidden';
    }
}

function closeModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.classList.remove('show');
        document.body.style.overflow = '';
    }
}

// Form handling
function initForms() {
    // Auto-calculate totals in quote/invoice forms
    const quoteItems = document.querySelectorAll('.quote-item');
    if (quoteItems.length) {
        quoteItems.forEach(item => {
            const quantity = item.querySelector('.quantity');
            const price = item.querySelector('.unit-price');
            const total = item.querySelector('.item-total');
            
            if (quantity && price && total) {
                const calculate = () => {
                    const qty = parseFloat(quantity.value) || 0;
                    const prc = parseFloat(price.value) || 0;
                    total.value = (qty * prc).toFixed(2);
                    updateQuoteTotal();
                };
                
                quantity.addEventListener('input', calculate);
                price.addEventListener('input', calculate);
            }
        });
    }
}

function updateQuoteTotal() {
    let subtotal = 0;
    document.querySelectorAll('.item-total').forEach(item => {
        subtotal += parseFloat(item.value) || 0;
    });
    
    const taxRate = parseFloat(document.getElementById('tax_rate')?.value) || 0;
    const taxAmount = subtotal * (taxRate / 100);
    const total = subtotal + taxAmount;
    
    document.getElementById('subtotal')?.setAttribute('value', subtotal.toFixed(2));
    document.getElementById('tax_amount')?.setAttribute('value', taxAmount.toFixed(2));
    document.getElementById('total_amount')?.setAttribute('value', total.toFixed(2));
}

// AJAX functions
function fetchData(url, callback) {
    fetch(url)
        .then(response => response.json())
        .then(data => callback(null, data))
        .catch(error => callback(error, null));
}

function postData(url, data, callback) {
    fetch(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(data)
    })
    .then(response => response.json())
    .then(data => callback(null, data))
    .catch(error => callback(error, null));
}

// Tooltips
function initTooltips() {
    const tooltips = document.querySelectorAll('[data-tooltip]');
    tooltips.forEach(element => {
        element.addEventListener('mouseenter', function(e) {
            const tooltip = document.createElement('div');
            tooltip.className = 'tooltip';
            tooltip.textContent = this.dataset.tooltip;
            document.body.appendChild(tooltip);
            
            const rect = this.getBoundingClientRect();
            tooltip.style.top = rect.top - tooltip.offsetHeight - 5 + 'px';
            tooltip.style.left = rect.left + (rect.width / 2) - (tooltip.offsetWidth / 2) + 'px';
            
            this.addEventListener('mouseleave', function() {
                tooltip.remove();
            });
        });
    });
}

// Auto-hide alerts
function autoHideAlerts() {
    setTimeout(() => {
        document.querySelectorAll('.alert').forEach(alert => {
            alert.style.opacity = '0';
            setTimeout(() => alert.remove(), 300);
        });
    }, 5000);
}

// Confirmation dialog
function confirmAction(message, callback) {
    if (confirm(message)) {
        callback();
    }
}

// Format currency
function formatCurrency(amount) {
    return new Intl.NumberFormat('en-KE', {
        style: 'currency',
        currency: 'KES'
    }).format(amount);
}

// Format date
function formatDate(dateString) {
    const options = { year: 'numeric', month: 'short', day: 'numeric' };
    return new Date(dateString).toLocaleDateString('en-KE', options);
}

// Search functionality
function initSearch() {
    const searchInput = document.getElementById('global-search');
    if (searchInput) {
        searchInput.addEventListener('keyup', function(e) {
            if (e.key === 'Enter') {
                const searchTerm = this.value;
                window.location.href = `/SORA/search?q=${encodeURIComponent(searchTerm)}`;
            }
        });
    }
}

// Export functions for global use
window.SORA = {
    openModal,
    closeModal,
    toggleSidebar,
    formatCurrency,
    formatDate,
    confirmAction,
    postData,
    fetchData
};