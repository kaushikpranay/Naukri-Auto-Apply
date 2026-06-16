let dashboardData = null;

// Page rendering configs
let currentPage = 1;
let pageSize = 25;
let currentSort = "probability_desc";
let searchTerm = "";
let filterStatus = "all";

document.addEventListener("DOMContentLoaded", async () => {
    injectNavbar();
    highlightNav();
    await loadData();
    initPage();
});

function injectNavbar() {
    const nav = document.getElementById("navbar-container");
    if (!nav) return;
    nav.innerHTML = `
    <nav class="navbar navbar-expand-lg glass-nav fixed-top">
        <div class="container">
            <a class="navbar-brand d-flex align-items-center gap-2 fw-bold text-white" href="index.html">
                <div class="brand-icon"><i class="bi bi-robot"></i></div>
                <span>Naukri Auto</span>
            </a>
            <button class="navbar-toggler border-0 text-white" type="button" data-bs-toggle="collapse" data-bs-target="#navbarNav">
                <i class="bi bi-list fs-3"></i>
            </button>
            <div class="collapse navbar-collapse" id="navbarNav">
                <ul class="navbar-nav ms-auto gap-1 mt-2 mt-lg-0">
                    <li class="nav-item"><a class="nav-link" id="nav-overview" href="index.html"><i class="bi bi-grid-fill me-1"></i> Overview</a></li>
                    <li class="nav-item"><a class="nav-link" id="nav-top-jobs" href="top_jobs.html"><i class="bi bi-star-fill me-1"></i> Top Jobs <span class="badge bg-secondary ms-1" id="nav-top-jobs-count">0</span></a></li>
                    <li class="nav-item"><a class="nav-link" id="nav-wellfound" href="wellfound.html"><i class="bi bi-people-fill me-1"></i> Wellfound</a></li>
                    <li class="nav-item"><a class="nav-link" id="nav-external-jobs" href="external_jobs.html"><i class="bi bi-link-45deg me-1"></i> External <span class="badge bg-secondary ms-1" id="nav-external-jobs-count">0</span></a></li>
                    <li class="nav-item"><a class="nav-link" id="nav-review-jobs" href="review_jobs.html"><i class="bi bi-eye-fill me-1"></i> Review <span class="badge bg-secondary ms-1" id="nav-review-count">0</span></a></li>
                    <li class="nav-item"><a class="nav-link" id="nav-failed-jobs" href="failed_jobs.html"><i class="bi bi-exclamation-triangle-fill me-1"></i> Failed <span class="badge bg-secondary ms-1" id="nav-failed-count">0</span></a></li>
                    <li class="nav-item"><a class="nav-link" id="nav-hidden-jobs" href="hidden_jobs.html"><i class="bi bi-eye-slash-fill me-1"></i> Hidden <span class="badge bg-secondary ms-1" id="nav-hidden-count">0</span></a></li>
                    <li class="nav-item"><a class="nav-link" id="nav-question-bank" href="question_bank.html"><i class="bi bi-patch-question-fill me-1"></i> Q-Bank <span class="badge bg-secondary ms-1" id="nav-qbank-count">0</span></a></li>
                    <li class="nav-item"><a class="nav-link" id="nav-system-status" href="system_status.html"><i class="bi bi-cpu-fill me-1"></i> Status</a></li>
                </ul>
            </div>
        </div>
    </nav>`;
}

function highlightNav() {
    const path = window.location.pathname;
    let activeId = "nav-overview";
    if (path.includes("top_jobs.html")) activeId = "nav-top-jobs";
    else if (path.includes("wellfound.html")) activeId = "nav-wellfound";
    else if (path.includes("external_jobs.html")) activeId = "nav-external-jobs";
    else if (path.includes("review_jobs.html")) activeId = "nav-review-jobs";
    else if (path.includes("failed_jobs.html")) activeId = "nav-failed-jobs";
    else if (path.includes("hidden_jobs.html")) activeId = "nav-hidden-jobs";
    else if (path.includes("question_bank.html")) activeId = "nav-question-bank";
    else if (path.includes("system_status.html")) activeId = "nav-system-status";
    
    const activeLink = document.getElementById(activeId);
    if (activeLink) activeLink.classList.add("active");
}

async function loadData() {
    try {
        const res = await fetch("data/dashboard_data.json");
        dashboardData = await res.json();
    } catch (e) {
        console.error("Failed to load dashboard data:", e);
    }
}

function initPage() {
    if (!dashboardData) return;
    
    // Update navbar badges
    try {
        const hiddenCount = getHiddenJobs().length;
        const navHiddenBadge = document.getElementById("nav-hidden-count");
        if (navHiddenBadge) {
            navHiddenBadge.innerText = hiddenCount;
        }

        const topJobsCount = (dashboardData.top_jobs || []).filter(j => !getHiddenJobs().includes(j.id)).length;
        const navTopBadge = document.getElementById("nav-top-jobs-count");
        if (navTopBadge) {
            navTopBadge.innerText = topJobsCount;
        }

        const externalCount = (dashboardData.external_jobs || []).filter(j => !getHiddenJobs().includes(j.id)).length;
        const navExternalBadge = document.getElementById("nav-external-jobs-count");
        if (navExternalBadge) {
            navExternalBadge.innerText = externalCount;
        }

        const reviewCount = (dashboardData.review_jobs || []).filter(j => !getHiddenJobs().includes(j.id)).length;
        const navReviewBadge = document.getElementById("nav-review-count");
        if (navReviewBadge) {
            navReviewBadge.innerText = reviewCount;
        }

        const failedCount = (dashboardData.failed_jobs || []).filter(j => !getHiddenJobs().includes(j.id)).length;
        const navFailedBadge = document.getElementById("nav-failed-count");
        if (navFailedBadge) {
            navFailedBadge.innerText = failedCount;
        }

        const qbankCount = (dashboardData.question_bank || []).length;
        const navQbankBadge = document.getElementById("nav-qbank-count");
        if (navQbankBadge) {
            navQbankBadge.innerText = qbankCount;
        }
    } catch (e) {
        console.error("Failed to update navbar badges:", e);
    }

    const path = window.location.pathname;
    if (path.includes("index.html") || path.endsWith("/") || path.endsWith("docs") || path.endsWith("docs/")) {
        renderOverview();
    } else if (path.includes("top_jobs.html")) {
        renderTopJobs();
    } else if (path.includes("external_jobs.html")) {
        renderExternalJobs();
    } else if (path.includes("review_jobs.html")) {
        renderReviewJobs();
    } else if (path.includes("failed_jobs.html")) {
        renderFailedJobs();
    } else if (path.includes("hidden_jobs.html")) {
        renderHiddenJobs();
    } else if (path.includes("question_bank.html")) {
        renderQuestionBank();
    } else if (path.includes("system_status.html")) {
        renderSystemStatus();
    } else {
        renderOverview();
    }
}

// ── Overview Page ────────────────────────────────────────────────────────
function renderOverview() {
    const stats = dashboardData.overview_stats;
    const quota = dashboardData.quota_status;
    
    document.getElementById("stat-total-jobs").innerText = stats.total_jobs;
    document.getElementById("stat-jobs-today").innerText = stats.jobs_today;
    document.getElementById("stat-pending-eval").innerText = stats.pending_eval;
    document.getElementById("stat-pending-apply").innerText = stats.pending_apply;
    document.getElementById("stat-coverage-pct").innerText = stats.coverage_pct + "%";
    document.getElementById("stat-last-run").innerText = stats.last_run.substring(0, 16);
    
    // Application types
    document.getElementById("count-easy-apply").innerText = stats.easy_apply;
    document.getElementById("count-external-portal").innerText = stats.external_portal;
    document.getElementById("count-already-applied").innerText = stats.already_applied;
    document.getElementById("count-unknown-flow").innerText = stats.unknown_flow;
    document.getElementById("count-failed").innerText = stats.failed;
    document.getElementById("count-quota-exhausted").innerText = stats.quota_exhausted;
    document.getElementById("count-applied-ok").innerText = stats.applied_successfully;
    
    const countHiddenJobs = document.getElementById("count-hidden-jobs");
    if (countHiddenJobs) {
        countHiddenJobs.innerText = getHiddenJobs().length;
    }

    // Progress bar for Q-bank
    const pb = document.getElementById("qbank-progress");
    if (pb) {
        pb.style.width = stats.coverage_pct + "%";
        pb.className = `progress-bar ${stats.coverage_pct >= 80 ? 'bg-success' : stats.coverage_pct >= 50 ? 'bg-warning' : 'bg-danger'}`;
    }
    document.getElementById("qbank-ratio").innerText = `${stats.answered_questions}/${stats.total_questions} answered`;

    // Quota Badge
    const qBadge = document.getElementById("quota-status-badge");
    if (qBadge) {
        qBadge.innerText = quota.status_label;
        qBadge.className = `badge ${quota.is_exhausted ? 'bg-danger' : 'bg-success'}`;
    }
    document.getElementById("quota-consecutive").innerText = `${quota.consecutive_count}/3`;

    // Retry Queue
    const retry = stats.retry_queue || { count: 0, reasons: {} };
    const rBadge = document.getElementById("retry-queue-badge");
    if (rBadge) {
        rBadge.innerText = retry.count;
    }
    const reqQ = document.getElementById("retry-reason-q");
    if (reqQ) reqQ.innerText = retry.reasons.unknown_question || 0;
    const reqQuota = document.getElementById("retry-reason-quota");
    if (reqQuota) reqQuota.innerText = retry.reasons.quota_exhausted || 0;
    const reqTemp = document.getElementById("retry-reason-temp");
    if (reqTemp) reqTemp.innerText = retry.reasons.temporary_failure || 0;
    const reqBrowser = document.getElementById("retry-reason-browser");
    if (reqBrowser) reqBrowser.innerText = retry.reasons.browser_error || 0;
}

// ── Top Jobs Page ────────────────────────────────────────────────────────
function renderTopJobs() {
    let jobs = dashboardData.top_jobs.filter(j => !getHiddenJobs().includes(j.id));
    
    const searchInput = document.getElementById("search-input");
    const sortSelect = document.getElementById("sort-select");
    const statusSelect = document.getElementById("status-filter");

    function update() {
        searchTerm = searchInput.value.toLowerCase();
        currentSort = sortSelect.value;
        filterStatus = statusSelect.value;
        
        let filtered = jobs.filter(j => {
            const matchesSearch = (j.company_name || "").toLowerCase().includes(searchTerm) ||
                                  (j.job_title || "").toLowerCase().includes(searchTerm) ||
                                  (j.location || "").toLowerCase().includes(searchTerm);
            const matchesStatus = filterStatus === "all" || j.apply_status === filterStatus;
            return matchesSearch && matchesStatus;
        });

        // Sorting
        filtered.sort((a, b) => {
            if (currentSort === "probability_desc") return b.interview_probability - a.interview_probability;
            if (currentSort === "probability_asc") return a.interview_probability - b.interview_probability;
            if (currentSort === "company_asc") return (a.company_name || "").localeCompare(b.company_name || "");
            if (currentSort === "title_asc") return (a.job_title || "").localeCompare(b.job_title || "");
            return 0;
        });

        renderTable(filtered);
    }

    searchInput.addEventListener("input", () => { currentPage = 1; update(); });
    sortSelect.addEventListener("change", () => { currentPage = 1; update(); });
    statusSelect.addEventListener("change", () => { currentPage = 1; update(); });

    update();
}

function renderTable(list) {
    const totalCount = list.length;
    const totalPages = Math.ceil(totalCount / pageSize) || 1;
    if (currentPage > totalPages) currentPage = totalPages;

    const start = (currentPage - 1) * pageSize;
    const end = start + pageSize;
    const pageList = list.slice(start, end);

    const tbody = document.getElementById("jobs-tbody");
    const mCards = document.getElementById("mobile-cards");
    
    if (pageList.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" class="text-center text-muted">No jobs found</td></tr>`;
        mCards.innerHTML = `<div class="empty-state"><i class="bi bi-inbox"></i>No jobs found</div>`;
        renderPaginationControls(totalPages);
        return;
    }

    tbody.innerHTML = pageList.map(j => {
        const badgeClass = getApplyStatusBadgeClass(j.apply_status);
        return `
        <tr>
            <td>
                <div class="fw-bold">${escapeHtml(j.company_name)}</div>
                <small class="text-muted">${escapeHtml(j.location)}</small>
            </td>
            <td>
                <a href="${escapeHtml(j.job_url)}" target="_blank" class="text-white text-decoration-none fw-bold hover-accent">${escapeHtml(j.job_title)}</a>
            </td>
            <td><span class="badge bg-dark">${escapeHtml(j.experience_required || "N/A")}</span></td>
            <td>
                <div class="d-flex align-items-center gap-2">
                    <div class="progress flex-grow-1" style="height: 6px; width: 60px;">
                        <div class="progress-bar bg-success" style="width: ${j.interview_probability}%"></div>
                    </div>
                    <span>${j.interview_probability}%</span>
                </div>
            </td>
            <td><span class="badge ${badgeClass}">${escapeHtml(j.apply_status)}</span></td>
            <td>
                <div class="d-flex gap-1">
                    <button class="btn btn-sm btn-outline-accent" onclick="showReasonModal(${j.id})">Reason</button>
                    ${renderToggleButton(j.id)}
                </div>
            </td>
        </tr>`;
    }).join("");

    mCards.innerHTML = pageList.map(j => {
        const badgeClass = getApplyStatusBadgeClass(j.apply_status);
        return `
        <div class="job-card mb-3">
            <div class="d-flex justify-content-between align-items-start mb-2">
                <div>
                    <h6 class="mb-0 fw-bold text-white">${escapeHtml(j.company_name)}</h6>
                    <small class="text-muted">${escapeHtml(j.location)}</small>
                </div>
                <span class="badge ${badgeClass}">${escapeHtml(j.apply_status)}</span>
            </div>
            <a href="${escapeHtml(j.job_url)}" target="_blank" class="d-block text-white text-decoration-none fw-semibold mb-2">${escapeHtml(j.job_title)}</a>
            <div class="d-flex justify-content-between align-items-center small text-muted">
                <span>Exp: ${escapeHtml(j.experience_required || "N/A")}</span>
                <span>Prob: <strong class="text-success">${j.interview_probability}%</strong></span>
            </div>
            <div class="mt-2 d-flex gap-2">
                <button class="btn btn-sm btn-outline-accent flex-grow-1" onclick="showReasonModal(${j.id})">View Analysis</button>
                ${renderToggleButton(j.id)}
            </div>
        </div>`;
    }).join("");

    renderPaginationControls(totalPages);
}

function showReasonModal(jobId) {
    const job = dashboardData.top_jobs.find(j => j.id === jobId) || 
                dashboardData.review_jobs.find(j => j.id === jobId) ||
                dashboardData.failed_jobs.find(j => j.id === jobId);
    if (!job) return;
    
    document.getElementById("modal-job-title").innerText = `${job.company_name} - ${job.job_title}`;
    document.getElementById("modal-probability").innerText = `${job.interview_probability}%`;
    document.getElementById("modal-confidence").innerText = `${job.confidence || 0}%`;
    document.getElementById("modal-reason").innerText = job.reason || "No details provided.";
    
    const myModal = new bootstrap.Modal(document.getElementById('analysisModal'));
    myModal.show();
}

function getApplyStatusBadgeClass(status) {
    if (status === "easy_apply" || status === "applied_successfully") return "bg-success text-white";
    if (status === "external_portal") return "bg-info text-dark";
    if (status === "pending") return "bg-warning text-dark";
    if (status === "discovery_failed" || status === "quota_exhausted") return "bg-danger text-white";
    return "bg-secondary text-white";
}

// ── External Jobs Page ───────────────────────────────────────────────────
function renderExternalJobs() {
    let jobs = dashboardData.external_jobs.filter(j => !getHiddenJobs().includes(j.id));
    
    const searchInput = document.getElementById("search-input");

    function update() {
        searchTerm = searchInput.value.toLowerCase();
        
        let filtered = jobs.filter(j => {
            return (j.company_name || "").toLowerCase().includes(searchTerm) ||
                   (j.job_title || "").toLowerCase().includes(searchTerm) ||
                   (j.location || "").toLowerCase().includes(searchTerm);
        });

        renderExternalTable(filtered);
    }

    searchInput.addEventListener("input", () => { currentPage = 1; update(); });
    update();
}

function renderExternalTable(list) {
    const totalCount = list.length;
    const totalPages = Math.ceil(totalCount / pageSize) || 1;
    if (currentPage > totalPages) currentPage = totalPages;

    const start = (currentPage - 1) * pageSize;
    const end = start + pageSize;
    const pageList = list.slice(start, end);

    const tbody = document.getElementById("jobs-tbody");
    const mCards = document.getElementById("mobile-cards");
    
    if (pageList.length === 0) {
        tbody.innerHTML = `<tr><td colspan="5" class="text-center text-muted">No external jobs found</td></tr>`;
        mCards.innerHTML = `<div class="empty-state"><i class="bi bi-inbox"></i>No external jobs found</div>`;
        renderPaginationControls(totalPages);
        return;
    }

    tbody.innerHTML = pageList.map(j => {
        return `
        <tr>
            <td><strong>${escapeHtml(j.company_name)}</strong></td>
            <td><a href="${escapeHtml(j.job_url)}" target="_blank" class="text-white text-decoration-none fw-semibold">${escapeHtml(j.job_title)}</a></td>
            <td>${escapeHtml(j.location)}</td>
            <td><strong class="text-success">${j.interview_probability}%</strong></td>
            <td>
                <div class="d-flex gap-1">
                    <a href="${escapeHtml(j.apply_url)}" target="_blank" class="btn btn-sm btn-accent"><i class="bi bi-box-arrow-up-right me-1"></i> Apply</a>
                    ${renderToggleButton(j.id)}
                </div>
            </td>
        </tr>`;
    }).join("");

    mCards.innerHTML = pageList.map(j => {
        return `
        <div class="job-card mb-3">
            <h6 class="fw-bold text-white mb-1">${escapeHtml(j.company_name)}</h6>
            <a href="${escapeHtml(j.job_url)}" target="_blank" class="d-block text-white text-decoration-none fw-semibold mb-2">${escapeHtml(j.job_title)}</a>
            <div class="d-flex justify-content-between align-items-center mb-3 text-muted small">
                <span>Loc: ${escapeHtml(j.location)}</span>
                <span>Prob: <strong class="text-success">${j.interview_probability}%</strong></span>
            </div>
            <div class="d-flex gap-2">
                <a href="${escapeHtml(j.apply_url)}" target="_blank" class="btn btn-sm btn-accent flex-grow-1"><i class="bi bi-box-arrow-up-right me-1"></i> Apply on Portal</a>
                ${renderToggleButton(j.id)}
            </div>
        </div>`;
    }).join("");

    renderPaginationControls(totalPages);
}

// ── Review Jobs Page ─────────────────────────────────────────────────────
function renderReviewJobs() {
    let jobs = dashboardData.review_jobs.filter(j => !getHiddenJobs().includes(j.id));
    
    const searchInput = document.getElementById("search-input");

    function update() {
        searchTerm = searchInput.value.toLowerCase();
        let filtered = jobs.filter(j => {
            return (j.company_name || "").toLowerCase().includes(searchTerm) ||
                   (j.job_title || "").toLowerCase().includes(searchTerm) ||
                   (j.location || "").toLowerCase().includes(searchTerm) ||
                   (j.reason || "").toLowerCase().includes(searchTerm);
        });

        renderReviewTable(filtered);
    }

    searchInput.addEventListener("input", () => { currentPage = 1; update(); });
    update();
}

function renderReviewTable(list) {
    const totalCount = list.length;
    const totalPages = Math.ceil(totalCount / pageSize) || 1;
    if (currentPage > totalPages) currentPage = totalPages;

    const start = (currentPage - 1) * pageSize;
    const end = start + pageSize;
    const pageList = list.slice(start, end);

    const tbody = document.getElementById("jobs-tbody");
    const mCards = document.getElementById("mobile-cards");
    
    if (pageList.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" class="text-center text-muted">No review jobs found</td></tr>`;
        mCards.innerHTML = `<div class="empty-state"><i class="bi bi-inbox"></i>No review jobs found</div>`;
        renderPaginationControls(totalPages);
        return;
    }

    tbody.innerHTML = pageList.map(j => {
        let skills = [];
        try {
            skills = JSON.parse(j.missing_skills || "[]");
        } catch(e) {}
        const skillsBadges = skills.map(s => `<span class="badge bg-danger me-1">${escapeHtml(s)}</span>`).join("");
        
        return `
        <tr>
            <td><strong>${escapeHtml(j.company_name)}</strong></td>
            <td><a href="${escapeHtml(j.job_url)}" target="_blank" class="text-white text-decoration-none fw-semibold">${escapeHtml(j.job_title)}</a></td>
            <td><strong class="text-warning">${j.interview_probability}%</strong></td>
            <td><span class="badge bg-dark">${escapeHtml(j.experience_required || "N/A")}</span></td>
            <td>${skillsBadges || '<span class="text-muted small">None</span>'}</td>
            <td>
                <div class="d-flex gap-1">
                    <button class="btn btn-sm btn-outline-accent" onclick="showReasonModal(${j.id})">Details</button>
                    ${renderToggleButton(j.id)}
                </div>
            </td>
        </tr>`;
    }).join("");

    mCards.innerHTML = pageList.map(j => {
        let skills = [];
        try {
            skills = JSON.parse(j.missing_skills || "[]");
        } catch(e) {}
        const skillsBadges = skills.map(s => `<span class="badge bg-danger me-1 mb-1">${escapeHtml(s)}</span>`).join("");

        return `
        <div class="job-card mb-3">
            <div class="d-flex justify-content-between align-items-center mb-1">
                <h6 class="fw-bold text-white mb-0">${escapeHtml(j.company_name)}</h6>
                <strong class="text-warning">${j.interview_probability}%</strong>
            </div>
            <a href="${escapeHtml(j.job_url)}" target="_blank" class="d-block text-white text-decoration-none fw-semibold mb-2">${escapeHtml(j.job_title)}</a>
            <div class="mb-2">
                <span class="text-muted small d-block">Missing Skills:</span>
                <div>${skillsBadges || '<span class="text-muted small">None</span>'}</div>
            </div>
            <div class="mt-2 d-flex gap-2">
                <button class="btn btn-sm btn-outline-accent flex-grow-1" onclick="showReasonModal(${j.id})">View Reason</button>
                ${renderToggleButton(j.id)}
            </div>
        </div>`;
    }).join("");

    renderPaginationControls(totalPages);
}

// ── Failed Jobs Page ─────────────────────────────────────────────────────
function renderFailedJobs() {
    let jobs = dashboardData.failed_jobs.filter(j => !getHiddenJobs().includes(j.id));
    
    const searchInput = document.getElementById("search-input");

    function update() {
        searchTerm = searchInput.value.toLowerCase();
        let filtered = jobs.filter(j => {
            return (j.company_name || "").toLowerCase().includes(searchTerm) ||
                   (j.job_title || "").toLowerCase().includes(searchTerm) ||
                   (j.location || "").toLowerCase().includes(searchTerm);
        });

        renderFailedTable(filtered);
    }

    searchInput.addEventListener("input", () => { currentPage = 1; update(); });
    update();
}

function renderFailedTable(list) {
    const totalCount = list.length;
    const totalPages = Math.ceil(totalCount / pageSize) || 1;
    if (currentPage > totalPages) currentPage = totalPages;

    const start = (currentPage - 1) * pageSize;
    const end = start + pageSize;
    const pageList = list.slice(start, end);

    const tbody = document.getElementById("jobs-tbody");
    const mCards = document.getElementById("mobile-cards");
    
    if (pageList.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" class="text-center text-muted">No failed jobs found</td></tr>`;
        mCards.innerHTML = `<div class="empty-state"><i class="bi bi-inbox"></i>No failed jobs found</div>`;
        renderPaginationControls(totalPages);
        return;
    }

    tbody.innerHTML = pageList.map(j => {
        return `
        <tr>
            <td><strong>${escapeHtml(j.company_name)}</strong></td>
            <td><a href="${escapeHtml(j.job_url)}" target="_blank" class="text-white text-decoration-none fw-semibold">${escapeHtml(j.job_title)}</a></td>
            <td>${escapeHtml(j.location)}</td>
            <td class="text-danger small">${escapeHtml(j.reason)}</td>
            <td class="text-muted small">${escapeHtml(j.detected_at.substring(0,16))}</td>
            <td>
                <div class="d-flex gap-1">
                    ${renderToggleButton(j.id)}
                </div>
            </td>
        </tr>`;
    }).join("");

    mCards.innerHTML = pageList.map(j => {
        return `
        <div class="job-card job-card-failed mb-3">
            <h6 class="fw-bold text-white mb-1">${escapeHtml(j.company_name)}</h6>
            <a href="${escapeHtml(j.job_url)}" target="_blank" class="d-block text-white text-decoration-none fw-semibold mb-2">${escapeHtml(j.job_title)}</a>
            <p class="text-danger small mb-2">Error: ${escapeHtml(j.reason)}</p>
            <div class="d-flex justify-content-between align-items-center mt-2">
                <span class="text-muted small">Failed: ${escapeHtml(j.detected_at.substring(0, 16))}</span>
                ${renderToggleButton(j.id)}
            </div>
        </div>`;
    }).join("");

    renderPaginationControls(totalPages);
}

// ── Question Bank Page ───────────────────────────────────────────────────
function renderQuestionBank() {
    let questions = [...dashboardData.question_bank];
    const searchInput = document.getElementById("search-input");
    const filterSelect = document.getElementById("filter-status");

    function update() {
        searchTerm = searchInput.value.toLowerCase();
        const option = filterSelect.value;
        
        let filtered = questions.filter(q => {
            const matchesSearch = (q.question_text || "").toLowerCase().includes(searchTerm) || 
                                  (q.question_key || "").toLowerCase().includes(searchTerm) ||
                                  (q.answer || "").toLowerCase().includes(searchTerm);
            const hasAns = q.answer && q.answer.trim() !== "";
            const matchesFilter = option === "all" || (option === "answered" && hasAns) || (option === "unanswered" && !hasAns);
            return matchesSearch && matchesFilter;
        });

        renderQuestionList(filtered);
    }

    searchInput.addEventListener("input", () => { currentPage = 1; update(); });
    filterSelect.addEventListener("change", () => { currentPage = 1; update(); });
    update();
}

function renderQuestionList(list) {
    const totalCount = list.length;
    const totalPages = Math.ceil(totalCount / pageSize) || 1;
    if (currentPage > totalPages) currentPage = totalPages;

    const start = (currentPage - 1) * pageSize;
    const end = start + pageSize;
    const pageList = list.slice(start, end);

    const container = document.getElementById("questions-container");
    if (pageList.length === 0) {
        container.innerHTML = `<div class="empty-state"><i class="bi bi-patch-question"></i>No questions found</div>`;
        renderPaginationControls(totalPages);
        return;
    }

    container.innerHTML = pageList.map(q => {
        const hasAns = q.answer && q.answer.trim() !== "";
        return `
        <div class="glass-card p-3 mb-3">
            <div class="d-flex justify-content-between align-items-center mb-2">
                <span class="badge bg-secondary">${escapeHtml(q.field_type || "text")}</span>
                <span class="text-muted small">Used ${q.usage_count} times</span>
            </div>
            <h6 class="fw-bold text-white mb-1">${escapeHtml(q.question_text)}</h6>
            <code class="d-block text-accent mb-3" style="font-size: 0.8rem;">${escapeHtml(q.question_key)}</code>
            <div class="p-2 rounded ${hasAns ? 'answer-box' : 'answer-box answer-box-empty'}">
                <small class="text-muted d-block">${hasAns ? 'Normalised Answer:' : 'Needs Answer:'}</small>
                <strong class="text-white">${hasAns ? escapeHtml(q.answer) : 'N/A (Will be skipped or prompt user)'}</strong>
            </div>
        </div>`;
    }).join("");

    renderPaginationControls(totalPages);
}

// ── System Status Page ───────────────────────────────────────────────────
function renderSystemStatus() {
    const sys = dashboardData.system_status;

    // Collector card
    document.getElementById("status-col-total").innerText = sys.collector.total_jobs;
    document.getElementById("status-col-pending").innerText = sys.collector.pending;
    document.getElementById("status-col-eval").innerText = sys.collector.evaluated;
    document.getElementById("status-col-queued").innerText = sys.collector.queued;
    document.getElementById("status-col-failed").innerText = sys.collector.failed;

    // Keywords & Locations
    const kwContainer = document.getElementById("status-col-keywords");
    kwContainer.innerHTML = sys.collector.keywords.slice(0, 6).map(kw => `<span class="badge bg-dark me-1 mb-1">${escapeHtml(kw)}</span>`).join("");
    if (sys.collector.keywords.length > 6) {
        kwContainer.innerHTML += `<span class="text-muted small">+${sys.collector.keywords.length - 6} more</span>`;
    }

    const locContainer = document.getElementById("status-col-locations");
    locContainer.innerHTML = sys.collector.locations.slice(0, 6).map(loc => `<span class="badge bg-dark me-1 mb-1">${escapeHtml(loc)}</span>`).join("");

    // Evaluator card
    document.getElementById("status-ev-total").innerText = sys.evaluator.total_evaluations;
    document.getElementById("status-ev-pending").innerText = sys.evaluator.pending_eval;
    document.getElementById("status-ev-runs").innerText = sys.evaluator.total_runs;
    document.getElementById("status-ev-last").innerText = sys.evaluator.last_evaluation.substring(0, 16);
    
    document.getElementById("status-ev-models").innerHTML = sys.evaluator.models_used.map(m => `<span class="badge bg-dark me-1 mb-1">${escapeHtml(m)}</span>`).join("");

    // Discovery card
    document.getElementById("status-disc-proc").innerText = sys.discovery.total_processed;
    document.getElementById("status-disc-pending").innerText = sys.discovery.pending_apply;
    document.getElementById("status-disc-last").innerText = sys.discovery.last_discovery.substring(0, 16);

    // Quota details
    const quota = sys.discovery.quota;
    const qBadge = document.getElementById("status-quota-badge");
    qBadge.innerText = quota.status_label;
    qBadge.className = `badge ${quota.is_exhausted ? 'bg-danger' : 'bg-success'}`;
    document.getElementById("status-quota-consecutive").innerText = `${quota.consecutive_count}/3`;
    document.getElementById("status-quota-last").innerText = quota.last_detected ? quota.last_detected.substring(0, 16) : "Never";

    // Retry details
    const retry = sys.discovery.retry_queue || { count: 0, reasons: {} };
    const rBadge = document.getElementById("status-retry-badge");
    if (rBadge) {
        rBadge.innerText = retry.count;
    }
    const rq = document.getElementById("status-retry-q");
    if (rq) rq.innerText = retry.reasons.unknown_question || 0;
    const rquota = document.getElementById("status-retry-quota");
    if (rquota) rquota.innerText = retry.reasons.quota_exhausted || 0;
    const rtemp = document.getElementById("status-retry-temp");
    if (rtemp) rtemp.innerText = retry.reasons.temporary_failure || 0;
    const rbrowser = document.getElementById("status-retry-browser");
    if (rbrowser) rbrowser.innerText = retry.reasons.browser_error || 0;

    // Q-bank
    document.getElementById("status-qb-ratio").innerText = `${sys.question_bank.answered}/${sys.question_bank.total} answered`;
    document.getElementById("status-qb-cov").innerText = `${sys.question_bank.coverage_pct}%`;
    const pb = document.getElementById("status-qb-pb");
    pb.style.width = sys.question_bank.coverage_pct + "%";
    pb.className = `progress-bar ${sys.question_bank.coverage_pct >= 80 ? 'bg-success' : sys.question_bank.coverage_pct >= 50 ? 'bg-warning' : 'bg-danger'}`;

    // Recent runs table
    const tbody = document.getElementById("recent-runs-tbody");
    tbody.innerHTML = sys.recent_runs.map(r => `
    <tr>
        <td><code>${escapeHtml(r.run_id)}</code></td>
        <td><span class="badge bg-secondary">${r.cnt}</span></td>
        <td class="small text-muted">${r.started.substring(0, 19)}</td>
        <td class="small text-muted">${r.ended.substring(0, 19)}</td>
    </tr>`).join("");
}

// ── Pagination Helper ────────────────────────────────────────────────────
function renderPaginationControls(totalPages) {
    const navs = document.querySelectorAll(".pagination-nav");
    navs.forEach(nav => {
        if (!nav) return;
        
        let html = `
        <ul class="pagination pagination-sm justify-content-center mb-0 mt-3">
            <li class="page-item ${currentPage === 1 ? 'disabled' : ''}">
                <button class="page-link" onclick="changePage(${currentPage - 1})"><i class="bi bi-chevron-left"></i></button>
            </li>`;
        
        for (let i = 1; i <= totalPages; i++) {
            if (i === 1 || i === totalPages || (i >= currentPage - 2 && i <= currentPage + 2)) {
                html += `
                <li class="page-item ${currentPage === i ? 'active' : ''}">
                    <button class="page-link" onclick="changePage(${i})">${i}</button>
                </li>`;
            } else if (i === currentPage - 3 || i === currentPage + 3) {
                html += `<li class="page-item disabled"><span class="page-link">...</span></li>`;
            }
        }

        html += `
            <li class="page-item ${currentPage === totalPages ? 'disabled' : ''}">
                <button class="page-link" onclick="changePage(${currentPage + 1})"><i class="bi bi-chevron-right"></i></button>
            </li>
        </ul>`;
        
        nav.innerHTML = html;
    });
}

window.changePage = function(page) {
    currentPage = page;
    initPage();
};

// ── Utils ────────────────────────────────────────────────────────────────
function escapeHtml(str) {
    if (!str) return "";
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

// ── Hidden Jobs Logic ────────────────────────────────────────────────────
function getHiddenJobs() {
    try {
        return JSON.parse(localStorage.getItem("hidden_job_ids") || "[]");
    } catch (e) {
        return [];
    }
}

window.toggleJobHidden = function(jobId, event) {
    if (event) {
        event.stopPropagation();
        event.preventDefault();
    }
    let hidden = getHiddenJobs();
    if (hidden.includes(jobId)) {
        hidden = hidden.filter(id => id !== jobId);
    } else {
        hidden.push(jobId);
    }
    localStorage.setItem("hidden_job_ids", JSON.stringify(hidden));
    initPage();
};

function renderToggleButton(jobId) {
    const isHidden = getHiddenJobs().includes(jobId);
    const btnClass = isHidden ? "btn-success" : "btn-danger";
    const titleText = isHidden ? "Show Card" : "Hide Card";
    const iconClass = isHidden ? "bi-eye-fill" : "bi-eye-slash-fill";
    return `<button class="btn btn-sm ${btnClass}" onclick="toggleJobHidden(${jobId}, event)" title="${titleText}"><i class="bi ${iconClass}"></i></button>`;
}

function renderHiddenJobs() {
    const hiddenIds = getHiddenJobs();
    
    // Consolidate all jobs across categories
    let allJobs = [];
    if (dashboardData.top_jobs) {
        dashboardData.top_jobs.forEach(j => {
            if (hiddenIds.includes(j.id)) allJobs.push({...j, category: "Top Job"});
        });
    }
    if (dashboardData.external_jobs) {
        dashboardData.external_jobs.forEach(j => {
            if (hiddenIds.includes(j.id)) allJobs.push({...j, category: "External"});
        });
    }
    if (dashboardData.review_jobs) {
        dashboardData.review_jobs.forEach(j => {
            if (hiddenIds.includes(j.id)) allJobs.push({...j, category: "Review"});
        });
    }
    if (dashboardData.failed_jobs) {
        dashboardData.failed_jobs.forEach(j => {
            if (hiddenIds.includes(j.id)) allJobs.push({...j, category: "Failed"});
        });
    }

    const searchInput = document.getElementById("search-input");

    function update() {
        searchTerm = searchInput.value.toLowerCase();
        let filtered = allJobs.filter(j => {
            return (j.company_name || "").toLowerCase().includes(searchTerm) ||
                   (j.job_title || "").toLowerCase().includes(searchTerm) ||
                   (j.location || "").toLowerCase().includes(searchTerm) ||
                   (j.category || "").toLowerCase().includes(searchTerm);
        });

        renderHiddenTable(filtered);
    }

    if (searchInput) {
        searchInput.addEventListener("input", () => { currentPage = 1; update(); });
    }
    update();
}

function renderHiddenTable(list) {
    const totalCount = list.length;
    const totalPages = Math.ceil(totalCount / pageSize) || 1;
    if (currentPage > totalPages) currentPage = totalPages;

    const start = (currentPage - 1) * pageSize;
    const end = start + pageSize;
    const pageList = list.slice(start, end);

    const tbody = document.getElementById("jobs-tbody");
    const mCards = document.getElementById("mobile-cards");
    
    if (pageList.length === 0) {
        if (tbody) tbody.innerHTML = `<tr><td colspan="5" class="text-center text-muted">No hidden jobs found</td></tr>`;
        if (mCards) mCards.innerHTML = `<div class="empty-state"><i class="bi bi-inbox"></i>No hidden jobs found</div>`;
        renderPaginationControls(totalPages);
        return;
    }

    if (tbody) {
        tbody.innerHTML = pageList.map(j => {
            const catBadge = j.category === "Top Job" ? "bg-success" : j.category === "External" ? "bg-info text-dark" : j.category === "Review" ? "bg-warning text-dark" : "bg-danger";
            return `
            <tr>
                <td><span class="badge ${catBadge}">${escapeHtml(j.category)}</span></td>
                <td><strong>${escapeHtml(j.company_name)}</strong></td>
                <td><a href="${escapeHtml(j.job_url)}" target="_blank" class="text-white text-decoration-none fw-semibold">${escapeHtml(j.job_title)}</a></td>
                <td>
                    <div>${escapeHtml(j.location || "N/A")}</div>
                    ${j.reason ? `<div class="text-muted small text-truncate" style="max-width: 300px;">${escapeHtml(j.reason)}</div>` : ''}
                </td>
                <td>
                    <div class="d-flex gap-1">
                        ${j.reason ? `<button class="btn btn-sm btn-outline-accent" onclick="showReasonModal(${j.id})">Details</button>` : ''}
                        ${renderToggleButton(j.id)}
                    </div>
                </td>
            </tr>`;
        }).join("");
    }

    if (mCards) {
        mCards.innerHTML = pageList.map(j => {
            const catBadge = j.category === "Top Job" ? "bg-success" : j.category === "External" ? "bg-info text-dark" : j.category === "Review" ? "bg-warning text-dark" : "bg-danger";
            return `
            <div class="job-card mb-3">
                <div class="d-flex justify-content-between align-items-center mb-1">
                    <h6 class="fw-bold text-white mb-0">${escapeHtml(j.company_name)}</h6>
                    <span class="badge ${catBadge}">${escapeHtml(j.category)}</span>
                </div>
                <a href="${escapeHtml(j.job_url)}" target="_blank" class="d-block text-white text-decoration-none fw-semibold mb-2">${escapeHtml(j.job_title)}</a>
                ${j.reason ? `<p class="text-muted small mb-2">${escapeHtml(j.reason)}</p>` : ''}
                <div class="d-flex justify-content-between align-items-center mt-2">
                    ${j.reason ? `<button class="btn btn-sm btn-outline-accent" onclick="showReasonModal(${j.id})">Reason</button>` : '<span></span>'}
                    ${renderToggleButton(j.id)}
                </div>
            </div>`;
        }).join("");
    }

    renderPaginationControls(totalPages);
}
