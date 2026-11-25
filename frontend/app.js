/**
 * AI Job Radar & Recruiter Outreach Hub - Frontend Application Logic
 * UI/UX Pro Max Architecture: Modular ES6+, Accessible WCAG 2.4.3, Reactive State
 */

// Application State
const state = {
    currentPage: 1,
    pageSize: 10,
    totalPages: 1,
    totalOpportunities: 0,
    totalJobsCount: 0,
    currentScore: 70, // Default to 70%+ as requested by user
    currentCountry: 'India',
    currentState: 'Pune',
    currentStatus: 'all',
    searchQuery: '',
    sortBy: 'match_score',
    activeJobId: null,
    activeOutreachKit: null,
    activeInterviewKit: null,
    activeModalKeyHandler: null,
    lastFocusedTrigger: null,
    undoActionCallback: null,
    toastTimeout: null,
    logsInterval: null,
    cachedRawLogs: [],
    allJobsMap: new Map()
};

// Tech Hubs by Country
const LOCATION_HUBS = {
    "India": [
        { val: "All India", label: "All India" },
        { val: "Pune", label: "Pune (MH)" },
        { val: "Bangalore", label: "Bangalore (KA)" },
        { val: "Mumbai", label: "Mumbai (MH)" },
        { val: "Hyderabad", label: "Hyderabad (TS)" },
        { val: "Delhi NCR", label: "Delhi NCR" },
        { val: "Chennai", label: "Chennai (TN)" },
        { val: "Remote", label: "Remote (India)" }
    ],
    "United States": [
        { val: "All US", label: "All US" },
        { val: "San Francisco", label: "SF / Bay Area" },
        { val: "New York", label: "New York" },
        { val: "Austin", label: "Austin (TX)" },
        { val: "Seattle", label: "Seattle (WA)" },
        { val: "Remote", label: "Remote (US)" }
    ],
    "Europe": [
        { val: "All Europe", label: "All Europe" },
        { val: "London", label: "London (UK)" },
        { val: "Berlin", label: "Berlin (DE)" },
        { val: "Amsterdam", label: "Amsterdam (NL)" },
        { val: "Remote", label: "Remote (EU)" }
    ],
    "Remote": [
        { val: "Any Remote", label: "Worldwide Remote" },
        { val: "APAC", label: "APAC Remote" },
        { val: "EMEA", label: "EMEA Remote" },
        { val: "Americas", label: "Americas Remote" }
    ],
    "All": [
        { val: "Any Location", label: "Any Location" }
    ]
};

// Debounce Utility
function debounce(fn, delay) {
    let timer;
    return function (...args) {
        clearTimeout(timer);
        timer = setTimeout(() => fn.apply(this, args), delay);
    };
}

// Escape HTML Utility (XSS protection)
function escapeHtml(text) {
    if (!text) return '';
    return String(text)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

// ── Animated Number Counter (easeOutExpo) ───────────────────────────────────
function animateCounter(el, from, to, durationMs = 1000) {
    if (!el) return;
    const startTime = performance.now();
    const range = to - from;

    function easeOutExpo(t) {
        return t === 1 ? 1 : 1 - Math.pow(2, -10 * t);
    }

    function tick(now) {
        const elapsed = now - startTime;
        const progress = Math.min(elapsed / durationMs, 1);
        const value = Math.round(from + range * easeOutExpo(progress));
        el.innerText = value;
        if (progress < 1) requestAnimationFrame(tick);
    }

    requestAnimationFrame(tick);
}

// ── Toast Feedback System (spring physics animation) ─────────────────────────
function showToast(msg, isSuccess = true, onUndo = null) {
    const toast = document.getElementById('toast');
    const toastMsg = document.getElementById('toastMsg');
    const toastIcon = document.getElementById('toastIcon');
    const undoBtn = document.getElementById('toastUndoBtn');

    if (!toast || !toastMsg) return;

    if (state.toastTimeout) clearTimeout(state.toastTimeout);

    toastMsg.innerText = msg;
    toastIcon.className = isSuccess 
        ? 'fa-solid fa-circle-check text-emerald-400 text-sm' 
        : 'fa-solid fa-circle-exclamation text-rose-400 text-sm';

    if (onUndo) {
        state.undoActionCallback = onUndo;
        undoBtn.classList.remove('hidden');
        undoBtn.onclick = () => {
            if (state.undoActionCallback) {
                state.undoActionCallback();
                state.undoActionCallback = null;
            }
            hideToast();
        };
    } else {
        undoBtn.classList.add('hidden');
    }

    // Spring entrance animation
    toast.classList.remove('toast-hide', 'opacity-0', 'translate-y-3', 'pointer-events-none');
    toast.classList.add('toast-show');

    state.toastTimeout = setTimeout(() => {
        hideToast();
    }, 4500);
}

function hideToast() {
    const toast = document.getElementById('toast');
    if (!toast) return;
    toast.classList.remove('toast-show');
    toast.classList.add('toast-hide');
    // After animation remove display visibility
    setTimeout(() => {
        toast.classList.add('opacity-0', 'pointer-events-none');
        toast.classList.remove('toast-hide');
    }, 240);
}

// Accessible Modal Focus Trap (WCAG 2.4.3 & 2.1.2)
function trapModalFocus(modal) {
    const focusableElements = modal.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
    if (!focusableElements.length) return;

    const firstElement = focusableElements[0];
    const lastElement = focusableElements[focusableElements.length - 1];

    setTimeout(() => {
        firstElement.focus();
    }, 50);

    if (state.activeModalKeyHandler) {
        document.removeEventListener('keydown', state.activeModalKeyHandler);
    }

    state.activeModalKeyHandler = function(e) {
        if (e.key === 'Tab') {
            if (e.shiftKey) {
                if (document.activeElement === firstElement) {
                    e.preventDefault();
                    lastElement.focus();
                }
            } else {
                if (document.activeElement === lastElement) {
                    e.preventDefault();
                    firstElement.focus();
                }
            }
        }
    };
    document.addEventListener('keydown', state.activeModalKeyHandler);
}

function releaseModalFocus() {
    if (state.activeModalKeyHandler) {
        document.removeEventListener('keydown', state.activeModalKeyHandler);
        state.activeModalKeyHandler = null;
    }
}

// API Calls
async function fetchStats() {
    try {
        const res = await fetch('/api/stats');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        
        state.totalJobsCount = data.total_jobs || 0;

        // Animated counters — count from 0 to real value
        animateCounter(document.getElementById('statTotal'), 0, data.total_jobs || 0, 1100);
        animateCounter(document.getElementById('statHigh'), 0, data.high_matches || 0, 950);
        animateCounter(document.getElementById('statContacted'), 0, data.contacted_jobs || 0, 800);
        animateCounter(document.getElementById('statInterviewing'), 0, data.interviewing_jobs || 0, 700);
        document.getElementById('statDistinctCompanies').innerText = `${data.distinct_companies || 0} Companies`;

        // Update tab count badges if present
        if (data.total_jobs !== undefined) {
            document.getElementById('tabCountAll').innerText = data.total_jobs || 0;
            document.getElementById('tabCountNew').innerText = data.new_jobs || 0;
            document.getElementById('tabCountApplied').innerText = data.applied_jobs || 0;
            document.getElementById('tabCountContacted').innerText = data.contacted_jobs || 0;
            document.getElementById('tabCountInterviewing').innerText = data.interviewing_jobs || 0;
            document.getElementById('tabCountSaved').innerText = data.saved_jobs || 0;
        }
    } catch(err) {
        console.warn("Stats fetch warning:", err);
    }
}

// ── Skeleton Loading HTML ────────────────────────────────────────────────────
function buildSkeletonCards(count = 4) {
    return Array.from({ length: count }, () => `
        <div class="skeleton-card">
            <div class="skeleton-base skeleton-avatar"></div>
            <div class="skeleton-body">
                <div class="skeleton-base skeleton-line h-sm w-1-3"></div>
                <div class="skeleton-base skeleton-line h-lg w-3-4"></div>
                <div class="skeleton-base skeleton-line w-1-2"></div>
                <div class="skeleton-base skeleton-line h-sm"></div>
                <div class="skeleton-tags">
                    <div class="skeleton-base skeleton-tag"></div>
                    <div class="skeleton-base skeleton-tag"></div>
                    <div class="skeleton-base skeleton-tag"></div>
                </div>
            </div>
            <div class="skeleton-actions">
                <div class="skeleton-base skeleton-btn"></div>
                <div class="skeleton-base skeleton-btn"></div>
                <div class="skeleton-base skeleton-btn"></div>
            </div>
        </div>
    `).join('');
}

async function fetchJobs(page = 1) {
    state.currentPage = page;
    const container = document.getElementById('jobsList');
    const paginationBar = document.getElementById('paginationBar');
    const topPagination = document.getElementById('topPagination');

    const countrySelect = document.getElementById('countryFilter');
    const stateSelect = document.getElementById('stateFilter');
    const searchInput = document.getElementById('searchInput');
    const sortBySelect = document.getElementById('sortBy');

    const country = countrySelect ? countrySelect.value : state.currentCountry;
    const stateVal = stateSelect ? stateSelect.value : state.currentState;
    const search = searchInput ? searchInput.value.trim() : '';
    const sortBy = sortBySelect ? sortBySelect.value : state.sortBy;

    let url = `/api/jobs?page=${state.currentPage}&limit=${state.pageSize}&min_score=${state.currentScore}&sort_by=${sortBy}`;
    if (state.currentStatus !== 'all') url += `&status=${state.currentStatus}`;
    if (country && country !== 'All') url += `&country=${encodeURIComponent(country)}`;
    if (stateVal && !stateVal.toLowerCase().startsWith('all') && stateVal !== 'Any Location' && stateVal !== 'Any Remote') {
        url += `&state=${encodeURIComponent(stateVal)}`;
    }
    if (search) url += `&search=${encodeURIComponent(search)}`;

    // Skeleton loading state (replaces spinner)
    container.innerHTML = buildSkeletonCards(4);

    try {
        const res = await fetch(url);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        const jobs = data.jobs || [];

        state.totalOpportunities = data.total !== undefined ? data.total : jobs.length;
        state.totalPages = data.total_pages || 1;

        document.getElementById('jobsCount').innerText = state.totalOpportunities;
        document.getElementById('topRangeText').innerText = state.totalOpportunities > 0 
            ? `${(state.currentPage - 1) * state.pageSize + 1}–${Math.min(state.totalOpportunities, state.currentPage * state.pageSize)}` 
            : '0–0';

        // Cache jobs map
        state.allJobsMap.clear();
        jobs.forEach(j => state.allJobsMap.set(j._id, j));

        if (jobs.length === 0) {
            paginationBar.style.display = 'none';
            topPagination.style.display = 'none';
            
            container.innerHTML = `
                <div class="card-surface rounded-xl p-8 text-center text-zinc-300 border border-zinc-700 space-y-2 job-card-animate">
                    <div class="w-10 h-10 rounded-full bg-zinc-800 border border-zinc-700 text-zinc-400 flex items-center justify-center text-base mx-auto mb-1">
                        <i class="fa-solid fa-filter-circle-xmark"></i>
                    </div>
                    <h3 class="text-sm font-semibold text-zinc-100">No matching jobs found</h3>
                    <p class="text-xs text-zinc-400 max-w-sm mx-auto">Try lowering the minimum score slider or resetting the location hub filter.</p>
                    <div class="pt-2">
                        <button onclick="resetAllFilters()" class="btn-primary text-xs">
                            <i class="fa-solid fa-rotate-left text-xs"></i>
                            <span>Reset Filters to Default</span>
                        </button>
                    </div>
                </div>
            `;
            return;
        }

        // Render with page crossfade wrapper
        container.innerHTML = `<div class="page-transition-in space-y-3">${jobs.map((job, i) => renderJobCard(job, i)).join('')}</div>`;
        renderPagination(data);
    } catch(err) {
        console.error("fetchJobs error:", err);
        container.innerHTML = `
            <div class="card-surface rounded-xl p-6 text-center text-rose-400 border border-rose-900/50 space-y-2 job-card-animate">
                <i class="fa-solid fa-triangle-exclamation text-xl"></i>
                <h3 class="text-sm font-semibold">Failed to connect to backend server</h3>
                <p class="text-xs text-zinc-400">Ensure the FastAPI server is running on <code class="text-zinc-200">http://127.0.0.1:8000</code>.</p>
                <button onclick="fetchJobs(1)" class="btn-secondary !text-xs mt-2">
                    <i class="fa-solid fa-arrows-rotate"></i> Retry
                </button>
            </div>
        `;
        paginationBar.style.display = 'none';
        topPagination.style.display = 'none';
    }
}

// Render Job Card Component (index used for stagger delay)
function renderJobCard(job, index = 0) {
    const score = job.match_score || 0;
    let badgeBg = 'bg-zinc-800 text-zinc-300 border-zinc-700';
    let dotColor = 'bg-zinc-400';

    if (score >= 80) {
        badgeBg = 'bg-emerald-950/70 text-emerald-300 border-emerald-700/80';
        dotColor = 'bg-emerald-400';
    } else if (score >= 70) {
        badgeBg = 'bg-sky-950/70 text-sky-300 border-sky-700/80';
        dotColor = 'bg-sky-400';
    } else if (score >= 50) {
        badgeBg = 'bg-amber-950/60 text-amber-300 border-amber-700/80';
        dotColor = 'bg-amber-400';
    }

    const company = job.company || 'Company';
    const initial = company.charAt(0).toUpperCase();

    // Highlighting primary stack skills with interactive 1-click filter
    const coreStack = ['nestjs', 'node.js', 'nodejs', 'typescript', 'postgresql', 'redis', 'bullmq', 'docker', 'mongodb'];
    const skillsHtml = (job.matched_skills || []).map(s => {
        const isCore = coreStack.includes(s.toLowerCase());
        const badgeClass = isCore 
            ? 'bg-zinc-800 text-zinc-200 border-zinc-600 font-medium hover:border-zinc-400 hover:bg-zinc-700' 
            : 'bg-zinc-900 text-zinc-400 border-zinc-800 hover:border-zinc-700 hover:text-zinc-200';
        return `<button onclick="filterBySkill('${escapeHtml(s)}'); event.stopPropagation();" class="${badgeClass} border px-2 py-0.5 rounded text-xs transition cursor-pointer" title="Click to filter jobs by ${escapeHtml(s)}">${escapeHtml(s)}</button>`;
    }).join(' ');

    const dateStr = job.date_posted || 'Recently posted';

    // Discovered Recruiters
    const contacts = job.linkedin_contacts || [];
    let linkedinContactsHtml = '';
    if (contacts.length > 0) {
        linkedinContactsHtml = contacts.map(c => {
            const safeUrl = (c.url && (c.url.startsWith('https://') || c.url.startsWith('http://'))) ? c.url : '#';
            return `
                <a href="${safeUrl}" target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded bg-zinc-900 hover:bg-zinc-800 text-zinc-200 border border-zinc-700 text-xs min-h-[32px] transition" title="${escapeHtml(c.role || 'Talent')}">
                    <span class="font-medium text-zinc-200">${escapeHtml(c.name)}</span>
                    <span class="text-zinc-400 font-normal">(${escapeHtml((c.role || '').split('at')[0].trim())})</span>
                    <i class="fa-solid fa-arrow-up-right-from-square text-[10px] text-zinc-400"></i>
                </a>
            `;
        }).join(' ');
    } else {
        const searchUrl = job.linkedin_search_url || `https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(company)}+technical+recruiter`;
        linkedinContactsHtml = `
            <a href="${searchUrl}" target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1.5 text-xs text-zinc-300 hover:text-zinc-100 min-h-[32px] transition">
                <span>Search ${escapeHtml(company)} Recruiters</span>
                <i class="fa-solid fa-arrow-up-right-from-square text-[10px]"></i>
            </a>
        `;
    }

    const staggerDelay = Math.min(index * 65, 500); // cap at 500ms so last card isn't too delayed
    const badgeDelay = staggerDelay + 120;

    return `
        <article class="card-surface rounded-xl p-4 sm:p-5 transition hover:shadow-lg space-y-3.5 job-card-animate" 
                 data-id="${job._id}"
                 style="animation-delay: ${staggerDelay}ms;">
            <div class="flex flex-col sm:flex-row sm:items-start justify-between gap-3">
                <div class="flex items-start space-x-3.5 flex-1 min-w-0">
                    <!-- Company Logo Placeholder -->
                    <div class="w-11 h-11 rounded-xl bg-zinc-800/90 border border-zinc-700/80 flex items-center justify-center font-bold text-zinc-200 text-sm shrink-0 shadow-inner">
                        ${escapeHtml(initial)}
                    </div>

                    <!-- Role, Location & Details -->
                    <div class="space-y-1.5 flex-1 min-w-0">
                        <div class="flex flex-wrap items-center gap-2">
                            <!-- Match Score Pill — spring pop -->
                            <span class="score-badge-pop inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold font-mono-code border ${badgeBg}"
                                  style="--badge-delay: ${badgeDelay}ms;">
                                <span class="w-1.5 h-1.5 rounded-full ${dotColor}"></span>
                                <span>${score}% Match</span>
                            </span>
                            <span class="text-xs text-zinc-600">•</span>
                            <span class="text-xs text-zinc-300 bg-zinc-900 px-2.5 py-0.5 rounded border border-zinc-800">
                                ${escapeHtml(job.source || 'ATS Portal')}
                            </span>
                            <span class="text-xs text-zinc-600">•</span>
                            <span class="text-xs text-zinc-400 font-mono-code">${escapeHtml(dateStr)}</span>
                        </div>

                        <!-- Role Title -->
                        <div>
                            <h3 class="font-semibold text-zinc-100 text-sm leading-snug tracking-tight hover:text-white transition">
                                <a href="${job.url}" target="_blank" rel="noopener noreferrer" class="hover:underline flex items-center gap-1.5">
                                    <span>${escapeHtml(job.title)}</span>
                                </a>
                            </h3>
                            <p class="text-xs text-zinc-400 flex items-center gap-1.5 flex-wrap mt-0.5">
                                <span class="text-zinc-200 font-medium">${escapeHtml(job.company)}</span>
                                <span class="text-zinc-600">•</span>
                                <span class="inline-flex items-center gap-1 text-zinc-300">
                                    <i class="fa-solid fa-location-dot text-zinc-400 text-[10px]"></i>
                                    ${escapeHtml(job.location || 'Remote')}
                                </span>
                                ${job.country && job.country !== 'Other' ? `<span class="bg-zinc-800 text-zinc-300 border border-zinc-700 px-1.5 py-0.2 rounded text-[10px] font-mono-code">${escapeHtml(job.country)}</span>` : ''}
                            </p>
                        </div>

                        <p class="text-xs text-zinc-300 leading-relaxed line-clamp-2">${escapeHtml(job.match_summary || '')}</p>

                        <div class="flex flex-wrap gap-1.5 pt-0.5">
                            ${skillsHtml}
                        </div>
                    </div>
                </div>

                <!-- Action Button Cluster -->
                <div class="flex flex-wrap sm:flex-col lg:flex-row items-center gap-2 shrink-0">
                    <label for="status_${job._id}" class="sr-only">Update application status for ${escapeHtml(job.title)}</label>
                    <select id="status_${job._id}" onchange="updateJobStatus('${job._id}', this.value)" aria-label="Update status for ${escapeHtml(job.title)}" class="bg-zinc-950 border border-zinc-700 rounded-lg px-2.5 py-1.5 text-xs font-medium text-zinc-200 min-h-[36px] focus:border-zinc-400 focus:outline-none cursor-pointer">
                        <option value="new" ${job.status === 'new' ? 'selected' : ''}>Status: New</option>
                        <option value="applied" ${job.status === 'applied' ? 'selected' : ''}>Status: Applied</option>
                        <option value="contacted" ${job.status === 'contacted' ? 'selected' : ''}>Status: Contacted</option>
                        <option value="interviewing" ${job.status === 'interviewing' ? 'selected' : ''}>Status: Interviewing</option>
                        <option value="saved" ${job.status === 'saved' ? 'selected' : ''}>Status: Saved</option>
                        <option value="rejected" ${job.status === 'rejected' ? 'selected' : ''}>Status: Rejected</option>
                    </select>

                    <button onclick="openJobDetailsModal('${job._id}')" class="btn-secondary min-h-[36px] px-3 py-1.5 text-zinc-200 hover:text-white" title="View Full Job Description & Match Breakdown">
                        <i class="fa-solid fa-file-lines text-xs text-zinc-400"></i>
                        <span>View JD</span>
                    </button>

                    <a href="${job.url}" target="_blank" rel="noopener noreferrer" class="btn-secondary min-h-[36px] px-3 py-1.5" title="Open Official Application Page">
                        <i class="fa-solid fa-arrow-up-right-from-square text-xs text-zinc-400"></i>
                        <span>Apply</span>
                    </a>

                    <button onclick="openInterviewModalForJob('${job._id}')" class="btn-secondary min-h-[36px] px-3 py-1.5 text-zinc-200 hover:text-amber-300" title="1-Click Technical Interview Questions & Talking Points">
                        <i class="fa-solid fa-graduation-cap text-xs text-amber-400"></i>
                        <span>Interview Prep</span>
                    </button>

                    <button onclick="openEmailModalForJob('${job._id}')" class="btn-outline min-h-[36px] px-3 py-1.5 text-zinc-200 hover:text-emerald-300" title="Generate & send personalized recruiter cold email">
                        <i class="fa-solid fa-paper-plane text-xs text-zinc-400"></i>
                        <span>Outreach</span>
                    </button>
                </div>
            </div>

            <!-- Recruiter Contacts Bar -->
            <div class="pt-3 border-t border-zinc-800/80 flex flex-col md:flex-row md:items-center justify-between gap-3 text-xs">
                <div class="flex items-center gap-2 flex-wrap">
                    <span class="text-zinc-400 font-medium flex items-center gap-1 text-xs">
                        <i class="fa-brands fa-linkedin text-[#0A66C2]"></i>
                        <span>Recruiter Contacts:</span>
                    </span>
                    ${linkedinContactsHtml}
                    <button onclick="copyLinkedInPitch('${job._id}')" class="btn-ghost min-h-[32px] px-2.5 py-1 text-xs text-zinc-300 hover:text-zinc-100" title="Copy tailored 300-char connection note for LinkedIn">
                        <i class="fa-regular fa-copy mr-1 text-xs"></i>
                        <span>Copy Note</span>
                    </button>
                </div>
            </div>
        </article>
    `;
}

// Render Pagination Bar
function renderPagination(data) {
    const paginationBar = document.getElementById('paginationBar');
    const topPagination = document.getElementById('topPagination');

    if (!data || state.totalOpportunities === 0) {
        paginationBar.style.display = 'none';
        topPagination.style.display = 'none';
        return;
    }

    paginationBar.style.display = 'flex';
    topPagination.style.display = 'flex';

    const start = (state.currentPage - 1) * state.pageSize + 1;
    const end = Math.min(state.totalOpportunities, state.currentPage * state.pageSize);

    document.getElementById('pageRangeText').innerText = `${start}–${end}`;
    document.getElementById('topRangeText').innerText = `${start}–${end}`;
    document.getElementById('pageTotalText').innerText = state.totalOpportunities;
    document.getElementById('topPageIndicator').innerText = `Page ${state.currentPage} / ${state.totalPages}`;

    document.getElementById('topPrevBtn').disabled = state.currentPage <= 1;
    document.getElementById('topNextBtn').disabled = state.currentPage >= state.totalPages;

    const prevBtn = document.getElementById('prevPageBtn');
    const nextBtn = document.getElementById('nextPageBtn');
    prevBtn.disabled = state.currentPage <= 1;
    nextBtn.disabled = state.currentPage >= state.totalPages;

    const pillsContainer = document.getElementById('pagePillsContainer');
    let pillsHtml = '';

    let startPage = Math.max(1, state.currentPage - 2);
    let endPage = Math.min(state.totalPages, state.currentPage + 2);

    if (startPage > 1) {
        pillsHtml += `<button onclick="goToPage(1)" class="btn-secondary !px-2.5 !py-1 text-xs font-mono-code">1</button>`;
        if (startPage > 2) {
            pillsHtml += `<span class="text-zinc-600 text-xs px-1">…</span>`;
        }
    }

    for (let p = startPage; p <= endPage; p++) {
        if (p === state.currentPage) {
            pillsHtml += `<button class="btn-primary !px-2.5 !py-1 text-xs font-bold font-mono-code">${p}</button>`;
        } else {
            pillsHtml += `<button onclick="goToPage(${p})" class="btn-secondary !px-2.5 !py-1 text-xs font-mono-code">${p}</button>`;
        }
    }

    if (endPage < state.totalPages) {
        if (endPage < state.totalPages - 1) {
            pillsHtml += `<span class="text-zinc-600 text-xs px-1">…</span>`;
        }
        pillsHtml += `<button onclick="goToPage(${state.totalPages})" class="btn-secondary !px-2.5 !py-1 text-xs font-mono-code">${state.totalPages}</button>`;
    }

    pillsContainer.innerHTML = pillsHtml;
}

function goToPage(p) {
    if (p < 1 || p > state.totalPages) return;
    state.currentPage = p;
    fetchJobs(p);
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

function prevPage() {
    if (state.currentPage > 1) goToPage(state.currentPage - 1);
}

function nextPage() {
    if (state.currentPage < state.totalPages) goToPage(state.currentPage + 1);
}

// Status Updates
async function updateJobStatus(jobId, newStatus) {
    const job = state.allJobsMap.get(jobId);
    const oldStatus = job ? job.status : 'new';

    try {
        const res = await fetch(`/api/jobs/${jobId}/status`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status: newStatus })
        });
        if (!res.ok) throw new Error("Status update failed");

        if (job) job.status = newStatus;
        showToast(`Status updated to "${newStatus}"`, true, () => {
            updateJobStatus(jobId, oldStatus);
        });
        fetchStats();
    } catch(err) {
        showToast("Error updating status: " + err.message, false);
    }
}

// Filter Event Handlers
function onCountryChange() {
    const country = document.getElementById('countryFilter').value;
    state.currentCountry = country;
    const stateSelect = document.getElementById('stateFilter');
    const hubs = LOCATION_HUBS[country] || LOCATION_HUBS["All"];
    stateSelect.innerHTML = hubs.map(h => `<option value="${h.val}">${h.label}</option>`).join('');
    
    // Default to Pune when India is selected
    if (country === 'India') {
        stateSelect.value = 'Pune';
        state.currentState = 'Pune';
    } else {
        state.currentState = hubs[0].val;
    }

    state.currentPage = 1;
    fetchJobs(1);
}

function onStateChange() {
    state.currentState = document.getElementById('stateFilter').value;
    state.currentPage = 1;
    fetchJobs(1);
}

function setScorePreset(score) {
    state.currentScore = score;
    const slider = document.getElementById('scoreRange');
    if (slider) slider.value = score;
    const label = document.getElementById('scoreLabel');
    if (label) label.innerText = score + '%';

    document.querySelectorAll('.score-btn').forEach(btn => {
        const isSelected = parseInt(btn.dataset.score) === score;
        btn.className = isSelected ? 'score-btn btn-primary text-xs' : 'score-btn btn-secondary text-xs';
    });

    state.currentPage = 1;
    fetchJobs(1);
}

const updateScoreFilter = debounce((val) => {
    state.currentScore = parseInt(val);
    const label = document.getElementById('scoreLabel');
    if (label) label.innerText = val + '%';

    document.querySelectorAll('.score-btn').forEach(btn => {
        const isSelected = parseInt(btn.dataset.score) === state.currentScore;
        btn.className = isSelected ? 'score-btn btn-primary text-xs' : 'score-btn btn-secondary text-xs';
    });

    state.currentPage = 1;
    fetchJobs(1);
}, 250);

const onSearchInput = debounce((val) => {
    state.searchQuery = val.trim();
    const clearBtn = document.getElementById('clearSearchBtn');
    if (clearBtn) clearBtn.style.display = val ? 'block' : 'none';
    state.currentPage = 1;
    fetchJobs(1);
}, 250);

function clearSearch() {
    const input = document.getElementById('searchInput');
    if (input) input.value = '';
    const clearBtn = document.getElementById('clearSearchBtn');
    if (clearBtn) clearBtn.style.display = 'none';
    state.searchQuery = '';
    state.currentPage = 1;
    fetchJobs(1);
}

function setStatusFilter(status) {
    state.currentStatus = status;
    document.querySelectorAll('.status-tab').forEach(btn => {
        const isSelected = btn.dataset.status === status;
        btn.className = isSelected ? 'status-tab btn-primary shrink-0' : 'status-tab btn-secondary shrink-0';
        btn.setAttribute('aria-selected', isSelected ? 'true' : 'false');
    });
    state.currentPage = 1;
    fetchJobs(1);
}

function resetAllFilters() {
    const searchInput = document.getElementById('searchInput');
    if (searchInput) searchInput.value = '';
    const clearBtn = document.getElementById('clearSearchBtn');
    if (clearBtn) clearBtn.style.display = 'none';

    const countrySelect = document.getElementById('countryFilter');
    if (countrySelect) countrySelect.value = 'India';

    const stateSelect = document.getElementById('stateFilter');
    const hubs = LOCATION_HUBS["India"];
    if (stateSelect && hubs) {
        stateSelect.innerHTML = hubs.map(h => `<option value="${h.val}">${h.label}</option>`).join('');
        stateSelect.value = 'Pune';
    }

    setScorePreset(70); // Reset to 70% threshold
    setStatusFilter('all');
    showToast('Filters reset: India (Pune) with 70%+ score threshold');
}

// ── Animated Modal Helpers ────────────────────────────────────────────────────
function openModal(modalEl) {
    if (!modalEl) return;
    modalEl.style.display = 'flex';
    // Force reflow so transition triggers
    modalEl.offsetHeight;
    modalEl.classList.add('modal-open');
    modalEl.classList.remove('modal-closing');
}

function closeModal(modalEl, callback) {
    if (!modalEl) return;
    modalEl.classList.add('modal-closing');
    modalEl.classList.remove('modal-open');
    setTimeout(() => {
        modalEl.style.display = 'none';
        modalEl.classList.remove('modal-closing');
        if (callback) callback();
    }, 280);
}

// Recruiter Outreach Sequence Handlers
async function openEmailModalForJob(jobId) {
    state.lastFocusedTrigger = document.activeElement;
    state.activeJobId = jobId;
    const job = state.allJobsMap.get(jobId);
    if (!job) return;

    document.getElementById('modalEmailTitle').innerText = `Outreach: ${job.company} — ${job.title}`;
    document.getElementById('modalRecipient').value = job.contact_email || 'careers@' + job.company.toLowerCase().replace(/[^a-z]/g, '') + '.com';

    // Show modal with slide-up animation
    const modal = document.getElementById('emailModal');
    openModal(modal);
    trapModalFocus(modal);

    // Fetch dynamic AI sequence kit
    try {
        const res = await fetch(`/api/jobs/${jobId}/outreach-kit`);
        if (res.ok) {
            state.activeOutreachKit = await res.json();
            switchSequenceTab('initial');
        } else {
            fallbackOutreach(job);
        }
    } catch(e) {
        fallbackOutreach(job);
    }
}

function fallbackOutreach(job) {
    document.getElementById('modalSubject').value = job.cold_outreach_subject || `Application: ${job.title} - Sarthak Joshi`;
    document.getElementById('modalBody').value = job.cold_outreach_body || '';
}

function switchSequenceTab(tabName) {
    document.querySelectorAll('.seq-tab').forEach(b => {
        b.className = (b.id === `seqTab_${tabName}`) ? 'seq-tab btn-primary !py-1 text-xs' : 'seq-tab btn-secondary !py-1 text-xs';
    });

    const kit = state.activeOutreachKit;
    const job = state.allJobsMap.get(state.activeJobId);
    if (!kit || !job) return;

    const subjectInput = document.getElementById('modalSubject');
    const bodyInput = document.getElementById('modalBody');
    const countLabel = document.getElementById('charCountLabel');

    if (tabName === 'initial') {
        subjectInput.value = `Application: ${job.title} — Sarthak Joshi`;
        bodyInput.value = kit.initial_pitch || '';
        countLabel.innerText = '';
    } else if (tabName === 'day3') {
        subjectInput.value = `Re: Application: ${job.title} — Sarthak Joshi (Follow-up)`;
        bodyInput.value = kit.followup_day3 || '';
        countLabel.innerText = '';
    } else if (tabName === 'day7') {
        subjectInput.value = `Re: Application: ${job.title} — Sarthak Joshi (Final Check-in)`;
        bodyInput.value = kit.followup_day7 || '';
        countLabel.innerText = '';
    } else if (tabName === 'linkedin') {
        subjectInput.value = `LinkedIn Connection Note (${job.company})`;
        bodyInput.value = kit.linkedin_note || '';
        countLabel.innerText = `${(kit.linkedin_note || '').length} / 300 chars`;
    }
}

async function regenerateOutreachKit() {
    if (!state.activeJobId) return;
    const btn = document.getElementById('aiRegenBtn');
    btn.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i> Generating...';
    btn.disabled = true;

    try {
        const res = await fetch(`/api/jobs/${state.activeJobId}/outreach-kit`);
        if (res.ok) {
            state.activeOutreachKit = await res.json();
            switchSequenceTab('initial');
            showToast("AI Outreach Kit successfully re-crafted!");
        }
    } catch(e) {
        showToast("Failed to re-craft: " + e.message, false);
    } finally {
        btn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles text-[10px]"></i> AI Re-craft';
        btn.disabled = false;
    }
}

function closeEmailModal() {
    const modal = document.getElementById('emailModal');
    closeModal(modal, () => {
        releaseModalFocus();
        if (state.lastFocusedTrigger) state.lastFocusedTrigger.focus();
    });
}

function copyEmailBody() {
    const text = document.getElementById('modalBody').value;
    navigator.clipboard.writeText(text);
    showToast("Email copy copied to clipboard!");
}

async function sendColdEmail() {
    const recipient = document.getElementById('modalRecipient').value.trim();
    const subject = document.getElementById('modalSubject').value.trim();
    const body = document.getElementById('modalBody').value.trim();
    const btn = document.getElementById('sendEmailBtn');

    if (!recipient || !subject || !body) {
        showToast("Please complete all email fields before sending.", false);
        return;
    }

    btn.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin text-xs"></i> Sending via SMTP...';
    btn.disabled = true;

    try {
        const res = await fetch('/api/send-email', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                recipient_email: recipient,
                subject: subject,
                body: body
            })
        });
        const data = await res.json();
        if (data.status === 'sent') {
            showToast("Email successfully delivered via Gmail SMTP with resume attached!");
            if (state.activeJobId) updateJobStatus(state.activeJobId, 'contacted');
            closeEmailModal();
        } else {
            showToast(data.message || "Failed to dispatch email", false);
        }
    } catch(err) {
        showToast("Network error: " + err.message, false);
    } finally {
        btn.innerHTML = '<i class="fa-solid fa-paper-plane text-xs"></i> <span>Send via Gmail SMTP</span>';
        btn.disabled = false;
    }
}

// Technical Interview Copilot Handlers
async function openInterviewModalForJob(jobId) {
    state.lastFocusedTrigger = document.activeElement;
    state.activeJobId = jobId;
    const job = state.allJobsMap.get(jobId);
    if (!job) return;

    document.getElementById('interviewModalTitle').innerText = `Interview Copilot: ${job.company} — ${job.title}`;
    const modal = document.getElementById('interviewModal');
    openModal(modal);
    trapModalFocus(modal);

    try {
        const res = await fetch(`/api/jobs/${jobId}/interview-prep`);
        if (res.ok) {
            const prep = await res.json();
            state.activeInterviewKit = prep;
            renderInterviewKit(prep);
        }
    } catch(e) {
        showToast("Failed to load interview prep: " + e.message, false);
    }
}

function renderInterviewKit(prep) {
    const topicsContainer = document.getElementById('interviewTopics');
    const topics = prep.core_topics || [];
    topicsContainer.innerHTML = topics.map(t => 
        `<span class="bg-zinc-800 text-zinc-200 border border-zinc-700 px-2.5 py-1 rounded text-xs font-mono-code">${escapeHtml(t)}</span>`
    ).join(' ');

    const archList = document.getElementById('interviewArchitecture');
    const archPoints = prep.architecture_talking_points || [];
    archList.innerHTML = archPoints.map(p => 
        `<li>${escapeHtml(p)}</li>`
    ).join('');

    const qList = document.getElementById('interviewQuestionsList');
    const questions = prep.questions || [];
    qList.innerHTML = questions.map((q, idx) => `
        <div class="card-surface p-3.5 rounded-xl border border-zinc-800 space-y-2">
            <div class="flex items-start gap-2">
                <span class="w-5 h-5 rounded-full bg-amber-500/20 text-amber-300 font-bold text-xs flex items-center justify-center shrink-0 mt-0.5">${idx + 1}</span>
                <div class="font-semibold text-zinc-100 text-xs leading-snug">${escapeHtml(q.question)}</div>
            </div>
            <div class="pl-7 space-y-1.5 text-xs">
                <p class="text-zinc-400 italic"><span class="text-zinc-500 font-medium not-italic">Why they ask:</span> ${escapeHtml(q.rationale)}</p>
                <div class="bg-zinc-950 p-2.5 rounded-lg border border-zinc-800 text-zinc-200 leading-relaxed font-mono-code text-[11px]">
                    <span class="text-emerald-400 font-medium font-sans">Recommended Talking Points:</span> ${escapeHtml(q.recommended_answer)}
                </div>
            </div>
        </div>
    `).join('');
}

function copyInterviewNotes() {
    if (!state.activeInterviewKit) return;
    const prep = state.activeInterviewKit;
    const job = state.allJobsMap.get(state.activeJobId);

    let text = `Technical Interview Prep Sheet\nRole: ${job ? job.title : ''} at ${job ? job.company : ''}\n\n`;
    text += `Focus Topics: ${(prep.core_topics || []).join(', ')}\n\n`;
    text += `Architecture Talking Points:\n` + (prep.architecture_talking_points || []).map(p => `• ${p}`).join('\n') + `\n\n`;
    text += `Probable Questions:\n` + (prep.questions || []).map((q, i) => `${i + 1}. ${q.question}\n   Answer Points: ${q.recommended_answer}`).join('\n\n');

    navigator.clipboard.writeText(text);
    showToast("Interview prep sheet copied to clipboard!");
}

function closeInterviewModal() {
    const modal = document.getElementById('interviewModal');
    closeModal(modal, () => {
        releaseModalFocus();
        if (state.lastFocusedTrigger) state.lastFocusedTrigger.focus();
    });
}

// ── Full Job Details & Specification Modal Handlers ──────────────────────────
function openJobDetailsModal(jobId) {
    state.lastFocusedTrigger = document.activeElement;
    state.activeJobId = jobId;
    const job = state.allJobsMap.get(jobId);
    if (!job) return;

    document.getElementById('detailsCompanyInitial').innerText = (job.company || 'C').charAt(0).toUpperCase();
    document.getElementById('jobDetailsTitle').innerText = job.title || 'Role Title';
    document.getElementById('detailsCompany').innerText = job.company || 'Company';
    document.getElementById('detailsLocation').innerHTML = `<i class="fa-solid fa-location-dot text-zinc-400 text-[10px]"></i> ${escapeHtml(job.location || 'Remote')}`;
    document.getElementById('detailsSourceBadge').innerText = job.source || 'ATS Portal';
    document.getElementById('detailsDatePosted').innerText = job.date_posted ? `Posted ${job.date_posted}` : 'Recently posted';
    
    // Match score pill
    const score = job.match_score || 0;
    const pill = document.getElementById('detailsScorePill');
    const val = document.getElementById('detailsScoreVal');
    val.innerText = `${score}% Match`;
    if (score >= 80) {
        pill.className = 'inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold font-mono-code border bg-emerald-950/70 text-emerald-300 border-emerald-700/80';
    } else if (score >= 70) {
        pill.className = 'inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold font-mono-code border bg-sky-950/70 text-sky-300 border-sky-700/80';
    } else {
        pill.className = 'inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold font-mono-code border bg-amber-950/60 text-amber-300 border-amber-700/80';
    }

    // Match breakdown calculations
    const skillsCount = (job.matched_skills || []).length;
    const skillPts = Math.min(45, Math.max(15, skillsCount * 5));
    document.getElementById('detailsSkillPts').innerText = `${skillPts} / 45`;
    document.getElementById('detailsSkillBar').style.width = `${Math.round((skillPts / 45) * 100)}%`;

    document.getElementById('detailsMatchSummaryBadge').innerText = job.match_summary || '1.8 yrs Exp fit';

    // Skills List
    const skillsList = document.getElementById('detailsSkillsList');
    skillsList.innerHTML = (job.matched_skills || []).map(s => 
        `<button onclick="filterBySkill('${escapeHtml(s)}'); closeJobDetailsModal();" class="bg-zinc-800 text-zinc-200 border border-zinc-600 hover:border-zinc-400 font-medium px-2.5 py-1 rounded text-xs transition cursor-pointer" title="Click to filter by ${escapeHtml(s)}">${escapeHtml(s)}</button>`
    ).join(' ') || '<span class="text-zinc-500 text-xs">General full stack backend skills</span>';

    // Full description text
    const descEl = document.getElementById('detailsFullDescription');
    descEl.innerText = job.description || 'No detailed description available.';

    // Action button
    const applyBtn = document.getElementById('detailsOfficialApplyBtn');
    applyBtn.href = job.url || '#';

    const modal = document.getElementById('jobDetailsModal');
    openModal(modal);
    trapModalFocus(modal);
}

function closeJobDetailsModal() {
    const modal = document.getElementById('jobDetailsModal');
    closeModal(modal, () => {
        releaseModalFocus();
        if (state.lastFocusedTrigger) state.lastFocusedTrigger.focus();
    });
}

function filterBySkill(skill) {
    const searchInput = document.getElementById('searchInput');
    if (searchInput) {
        searchInput.value = skill;
        const clearBtn = document.getElementById('clearSearchBtn');
        if (clearBtn) clearBtn.style.display = 'block';
    }
    state.searchQuery = skill;
    state.currentPage = 1;
    fetchJobs(1);
    showToast(`Filtered opportunities by: ${skill}`);
}

function copyDetailsLinkedInPitch() {
    if (state.activeJobId) {
        copyLinkedInPitch(state.activeJobId);
    }
}

function triggerOutreachFromDetails() {
    const id = state.activeJobId;
    closeJobDetailsModal();
    setTimeout(() => {
        if (id) openEmailModalForJob(id);
    }, 300);
}

function triggerInterviewFromDetails() {
    const id = state.activeJobId;
    closeJobDetailsModal();
    setTimeout(() => {
        if (id) openInterviewModalForJob(id);
    }, 300);
}

function copyLinkedInPitch(jobId) {
    const job = state.allJobsMap.get(jobId);
    if (!job) return;
    const note = job.linkedin_connection_note || `Hi, I noticed your team at ${job.company} is hiring for ${job.title}. With 1.8+ yrs building scalable Node.js/NestJS systems for 300K+ concurrent users, I'd love to connect!`;
    navigator.clipboard.writeText(note);
    showToast(`Copied LinkedIn note (${note.length} chars)!`);
}

// Targeted Crawl Modal Handlers
function openCrawlModal() {
    state.lastFocusedTrigger = document.activeElement;
    const modal = document.getElementById('crawlModal');
    openModal(modal);
    trapModalFocus(modal);
}

function closeCrawlModal() {
    const modal = document.getElementById('crawlModal');
    closeModal(modal, () => {
        releaseModalFocus();
        if (state.lastFocusedTrigger) state.lastFocusedTrigger.focus();
    });
}

function onCrawlCountryChange() {
    const country = document.getElementById('crawlCountry').value;
    const stateSelect = document.getElementById('crawlState');
    const hubs = LOCATION_HUBS[country] || LOCATION_HUBS["All"];
    stateSelect.innerHTML = hubs.map(h => `<option value="${h.val}">${h.label}</option>`).join('');
}

async function launchTargetedCrawl() {
    const country = document.getElementById('crawlCountry').value;
    const stateVal = document.getElementById('crawlState').value;
    const minScore = parseInt(document.getElementById('crawlScoreRange').value);
    const sendMail = document.getElementById('crawlSendMail').checked;
    closeCrawlModal();

    showToast(`Triggering crawl for ${country} (${stateVal})...`);

    try {
        const res = await fetch('/api/crawl', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ country, state: stateVal, min_score: minScore, send_mail: sendMail })
        });
        const data = await res.json();
        showToast(data.message || `Crawl initiated!`);
        checkCrawlProgress();
    } catch(err) {
        showToast("Failed to start crawl: " + err.message, false);
    }
}

// Live Logs Modal
function openLogsModal() {
    state.lastFocusedTrigger = document.activeElement;
    const modal = document.getElementById('logsModal');
    openModal(modal);
    trapModalFocus(modal);
    fetchLiveLogs();
    if (!state.logsInterval) {
        state.logsInterval = setInterval(fetchLiveLogs, 4000);
    }
}

function closeLogsModal() {
    const modal = document.getElementById('logsModal');
    closeModal(modal, () => {
        releaseModalFocus();
        if (state.logsInterval) {
            clearInterval(state.logsInterval);
            state.logsInterval = null;
        }
        if (state.lastFocusedTrigger) state.lastFocusedTrigger.focus();
    });
}

async function fetchLiveLogs() {
    try {
        const res = await fetch('/api/logs?lines=150');
        const data = await res.json();
        state.cachedRawLogs = data.logs || [];
        renderLogs();
    } catch(e) {
        console.error("Failed to fetch logs:", e);
    }
}

function renderLogs() {
    const filter = (document.getElementById('logSearchInput').value || '').toLowerCase();
    const container = document.getElementById('logsContainer');
    const filtered = state.cachedRawLogs.filter(line => !filter || line.toLowerCase().includes(filter));

    if (filtered.length === 0) {
        container.innerHTML = `<div class="text-zinc-600">No logs match query.</div>`;
        return;
    }

    container.innerHTML = filtered.map(line => {
        let color = 'text-zinc-300';
        if (line.includes('[ERROR]')) color = 'text-rose-400 font-medium';
        else if (line.includes('[WARNING]')) color = 'text-amber-400 font-medium';
        else if (line.includes('[INFO]')) color = 'text-zinc-300';
        return `<div class="${color}">${escapeHtml(line)}</div>`;
    }).join('');

    container.scrollTop = container.scrollHeight;
}

// Crawler Background Status Polling
async function checkCrawlProgress() {
    try {
        const res = await fetch('/api/crawl-status');
        const data = await res.json();
        const text = document.getElementById('crawlerStatusText');
        const time = document.getElementById('lastCrawlTime');

        if (data.is_running) {
            text.innerText = `Scanning ${data.active_country} (${data.active_state})...`;
            text.className = 'text-amber-400 font-medium text-[11px]';
            // Turn beacon amber while scanning
            const beacon = document.querySelector('.pulsing-beacon');
            if (beacon) beacon.classList.add('beacon-scanning');
            setTimeout(checkCrawlProgress, 3000);
        } else {
            text.innerText = 'Crawler Engine Ready';
            text.className = 'text-zinc-300 font-medium text-[11px]';
            // Restore green beacon
            const beacon = document.querySelector('.pulsing-beacon');
            if (beacon) beacon.classList.remove('beacon-scanning');
            if (data.last_run) {
                const d = new Date(data.last_run);
                time.innerText = `Last: ${d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
            }
        }
    } catch(e) {}
}

// Keyboard Shortcuts & Initialization
function initApp() {
    fetchStats();
    fetchJobs(1);
    checkCrawlProgress();

    window.addEventListener('keydown', (e) => {
        if (e.key === '/' && document.activeElement.tagName !== 'INPUT' && document.activeElement.tagName !== 'TEXTAREA') {
            e.preventDefault();
            const searchBox = document.getElementById('searchInput');
            if (searchBox) {
                searchBox.focus();
                searchBox.select();
            }
        } else if (e.key === 'Escape') {
            closeEmailModal();
            closeLogsModal();
            closeCrawlModal();
            closeInterviewModal();
            closeJobDetailsModal();
        }
    });
}

// ReadyState-aware initialization (never misses DOMContentLoaded)
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initApp);
} else {
    initApp();
}
