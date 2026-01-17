// API Base URL 설정 (환경에 따라 자동 감지)
const API_BASE_URL = (() => {
    // 로컬 개발 환경인지 확인
    if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
        return 'http://localhost:5000';
    }
    // 배포 환경에서는 상대 경로 사용
    return '';
})();

document.addEventListener('DOMContentLoaded', () => {
    // --- DOM Elements ---
    const intakeForm = document.getElementById('intake-form');
    const loadingOverlay = document.getElementById('loading-overlay');
    const resultsSection = document.getElementById('results-section');
    const consultantList = document.getElementById('consultant-list');
    const refreshContainer = document.getElementById('refresh-container');
    const refreshBtn = document.getElementById('refresh-btn');
    const formSection = document.querySelector('.form-section.form-container');

    // --- State ---
    let allConsultants = [];
    let filteredConsultants = [];
    let currentConsultantIndex = 0;
    const CONSULTANTS_PER_PAGE = 3;

    // --- Selection State for Quote Request ---
    let selectedConsultants = new Map(); // Map of id -> consultant object
    const MAX_SELECTIONS = 5;

    // --- Session ID for grouping projects ---
    let currentSessionId = null;

    // --- Filter State ---
    let filterDebounceTimer = null;

    // --- Check for returning from consultant profile (find other consultants) ---
    function checkForPreviousResults() {
        const urlParams = new URLSearchParams(window.location.search);
        const action = urlParams.get('action');

        if (action === 'find-others') {
            // User clicked "다른 전문가 찾기" from consultant profile
            const savedResult = localStorage.getItem('lastAnalysisResult');
            const savedTime = localStorage.getItem('lastAnalysisTime');

            if (savedResult) {
                try {
                    const result = JSON.parse(savedResult);
                    const savedDate = new Date(savedTime);
                    const now = new Date();
                    const hoursDiff = (now - savedDate) / (1000 * 60 * 60);

                    // Only use saved results if less than 24 hours old
                    if (hoursDiff < 24) {
                        // Hide form, show results
                        if (intakeForm) intakeForm.style.display = 'none';
                        if (resultsSection) {
                            resultsSection.classList.remove('hidden');

                            // Display results without animation
                            displayResults(result, true);

                            // Show next batch of consultants (cycle through)
                            setTimeout(() => {
                                if (allConsultants.length > CONSULTANTS_PER_PAGE) {
                                    currentConsultantIndex += CONSULTANTS_PER_PAGE;
                                    if (currentConsultantIndex >= allConsultants.length) {
                                        currentConsultantIndex = 0;
                                    }
                                    renderConsultants();

                                    // Open filter panel automatically
                                    const filterPanel = document.getElementById('consultant-filter-panel');
                                    if (filterPanel && filterPanel.classList.contains('hidden')) {
                                        window.toggleConsultantFilter();
                                    }
                                }

                                // Scroll to results
                                resultsSection.scrollIntoView({ behavior: 'smooth' });

                                // Show notification
                                showNotification('이전 분석 결과를 불러왔습니다. 필터를 조정하여 다른 전문가를 찾아보세요.', 'info');
                            }, 300);
                        }

                        // Clean up URL
                        window.history.replaceState({}, document.title, window.location.pathname);
                        return true;
                    }
                } catch (e) {
                    console.warn('Could not restore previous results:', e);
                }
            }

            // If no valid saved results, show notification and proceed to form
            showNotification('이전 분석 결과가 만료되었습니다. 새로 매칭을 시작해주세요.', 'info');
            window.history.replaceState({}, document.title, window.location.pathname);
        }
        return false;
    }

    // Check on page load
    const hasRestoredResults = checkForPreviousResults();

    // --- Multi-step Form Logic ---
    const steps = document.querySelectorAll('.form-step');
    const nextBtns = document.querySelectorAll('.next-step');
    const prevBtns = document.querySelectorAll('.prev-step');
    let currentStep = 1;

    // --- Reset Form Function ---
    function resetForm() {
        // Hide results section
        if (resultsSection) {
            resultsSection.classList.add('hidden');
        }

        // Show form section
        if (intakeForm) {
            intakeForm.style.display = 'block';
        }

        // Hide loading overlay
        if (loadingOverlay) {
            loadingOverlay.classList.add('hidden');
        }

        // Reset form values
        if (intakeForm) {
            intakeForm.reset();
        }

        // Reset all checkboxes
        document.querySelectorAll('input[type="checkbox"]').forEach(cb => {
            cb.checked = false;
        });

        // Show step 1, hide others
        showStep(1);

        // Reset consultants
        allConsultants = [];
        currentConsultantIndex = 0;
        if (consultantList) {
            consultantList.innerHTML = '';
        }
        if (refreshContainer) {
            refreshContainer.classList.add('hidden');
        }

        // ===== Auto-fill user info =====
        prefillUserInfo();

        // Scroll to diagnosis section
        const diagnosisSection = document.getElementById('diagnosis');
        if (diagnosisSection) {
            setTimeout(() => {
                diagnosisSection.scrollIntoView({ behavior: 'smooth' });
            }, 100);
        }
    }

    // ===== Prefill user info from localStorage =====
    function prefillUserInfo() {
        const user = JSON.parse(localStorage.getItem('user'));
        if (user) {
            const companyNameInput = document.getElementById('companyName');
            const contactEmailInput = document.getElementById('contactEmail');

            if (companyNameInput && user.name) {
                companyNameInput.value = user.name;
            }
            if (contactEmailInput && user.email) {
                contactEmailInput.value = user.email;
            }
        }
    }

    // Call prefillUserInfo on initial page load as well
    prefillUserInfo();

    // --- Bind Reset to Navigation Links ---
    document.querySelectorAll('a[href="#diagnosis"]').forEach(link => {
        link.addEventListener('click', (e) => {
            // If results are showing, reset the form
            if (resultsSection && !resultsSection.classList.contains('hidden')) {
                e.preventDefault();
                resetForm();
            }
        });
    });

    // Next Button Handlers
    nextBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            if (validateStep(currentStep)) {
                showStep(currentStep + 1);
            }
        });
    });

    // Prev Button Handlers
    prevBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            showStep(currentStep - 1);
        });
    });

    function showStep(step) {
        steps.forEach(s => {
            s.classList.add('hidden');
            s.classList.remove('active');
        });
        const targetStep = document.querySelector(`.form-step[data-step="${step}"]`);
        if (targetStep) {
            targetStep.classList.remove('hidden');
            targetStep.classList.add('active');
            currentStep = step;
            // Re-initialize Lucide icons for the new step
            if (typeof lucide !== 'undefined') {
                lucide.createIcons();
            }
        }
    }

    function validateStep(step) {
        const currentStepEl = document.querySelector(`.form-step[data-step="${step}"]`);
        if (!currentStepEl) return true;

        const inputs = currentStepEl.querySelectorAll('input[required], select[required]');
        let isValid = true;

        inputs.forEach(input => {
            if (!input.value) {
                isValid = false;
                input.style.borderColor = 'var(--error)';
                input.style.boxShadow = '0 0 0 3px rgba(239, 68, 68, 0.2)';

                const resetStyle = () => {
                    input.style.borderColor = '';
                    input.style.boxShadow = '';
                };
                input.addEventListener('input', resetStyle, { once: true });
                input.addEventListener('change', resetStyle, { once: true });
            }
        });

        if (!isValid) {
            showNotification('필수 항목을 모두 입력해주세요.', 'error');
        }
        return isValid;
    }

    // --- Selected Standards Preview ---
    function updateSelectedStandardsPreview() {
        const selectedCheckboxes = document.querySelectorAll('input[name="standards"]:checked');
        const previewContainer = document.getElementById('selected-standards-preview');
        const previewText = document.getElementById('selected-standards-text');

        if (!previewContainer || !previewText) return;

        if (selectedCheckboxes.length === 0) {
            previewContainer.classList.add('hidden');
        } else {
            previewContainer.classList.remove('hidden');
            const standardNames = Array.from(selectedCheckboxes).map(cb => {
                // Extract short name from value (e.g., "ISO 9001:2015" -> "ISO 9001")
                return cb.value.split(':')[0];
            });
            previewText.textContent = standardNames.join(', ');
        }
    }

    // --- Issue-Based ISO Recommendations ---
    function updateRecommendedISO() {
        const selectedIssues = document.querySelectorAll('input[name="issues"]:checked');
        const previewContainer = document.getElementById('recommended-iso-preview');
        const listContainer = document.getElementById('recommended-iso-list');

        if (!previewContainer || !listContainer) return;

        // Collect all related ISO from selected issues
        const recommendedSet = new Set();
        selectedIssues.forEach(checkbox => {
            const relatedISO = checkbox.dataset.iso;
            if (relatedISO) {
                relatedISO.split(',').forEach(iso => recommendedSet.add(iso.trim()));
            }
        });

        // Filter out already selected standards
        const selectedStandards = new Set(
            Array.from(document.querySelectorAll('input[name="standards"]:checked')).map(cb => cb.value)
        );

        const newRecommendations = Array.from(recommendedSet).filter(iso => !selectedStandards.has(iso));

        if (newRecommendations.length === 0) {
            previewContainer.classList.add('hidden');
        } else {
            previewContainer.classList.remove('hidden');
            listContainer.innerHTML = newRecommendations.map(iso =>
                `<span class="iso-tag">${iso.split(':')[0]}</span>`
            ).join('');

            // Re-initialize Lucide icons
            if (typeof lucide !== 'undefined') {
                lucide.createIcons();
            }
        }
    }

    // --- Bind listeners for real-time updates ---
    document.querySelectorAll('input[name="standards"]').forEach(checkbox => {
        checkbox.addEventListener('change', updateSelectedStandardsPreview);
    });

    document.querySelectorAll('input[name="issues"]').forEach(checkbox => {
        checkbox.addEventListener('change', updateRecommendedISO);
    });

    // --- Notification System ---
    function showNotification(message, type = 'info') {
        // Remove existing notifications
        const existing = document.querySelector('.notification');
        if (existing) existing.remove();

        const notification = document.createElement('div');
        notification.className = `notification notification-${type}`;
        notification.innerHTML = `
            <span>${message}</span>
            <button onclick="this.parentElement.remove()">&times;</button>
        `;
        notification.style.cssText = `
            position: fixed;
            top: 100px;
            right: 24px;
            padding: 16px 24px;
            background: ${type === 'error' ? 'rgba(239, 68, 68, 0.9)' : type === 'success' ? 'rgba(16, 185, 129, 0.9)' : 'rgba(59, 130, 246, 0.9)'};
            color: white;
            border-radius: 12px;
            display: flex;
            align-items: center;
            gap: 12px;
            z-index: 10000;
            animation: slideIn 0.3s ease;
            backdrop-filter: blur(10px);
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
        `;

        // Add animation keyframes if not exists
        if (!document.querySelector('#notification-styles')) {
            const style = document.createElement('style');
            style.id = 'notification-styles';
            style.textContent = `
                @keyframes slideIn {
                    from { transform: translateX(100px); opacity: 0; }
                    to { transform: translateX(0); opacity: 1; }
                }
            `;
            document.head.appendChild(style);
        }

        document.body.appendChild(notification);

        // Auto-remove after 4 seconds
        setTimeout(() => {
            if (notification.parentElement) {
                notification.style.animation = 'slideIn 0.3s ease reverse';
                setTimeout(() => notification.remove(), 300);
            }
        }, 4000);
    }

    // --- Form Submission & Direct Matching ---
    if (intakeForm) {
        intakeForm.addEventListener('submit', async (e) => {
            e.preventDefault();

            // Collect Data from new 4-step form
            const formData = {
                companyName: document.getElementById('companyName')?.value || '',
                contactEmail: document.getElementById('contactEmail')?.value || '',
                industry: document.getElementById('industry')?.value || '',
                employees: document.getElementById('employees')?.value || '',
                region: document.getElementById('region')?.value || '',
                standards: Array.from(document.querySelectorAll('input[name="standards"]:checked')).map(cb => cb.value),
                issues: Array.from(document.querySelectorAll('input[name="issues"]:checked')).map(cb => ({
                    id: cb.value,
                    relatedISO: cb.dataset.iso?.split(',') || []
                })),
                reasons: Array.from(document.querySelectorAll('input[name="reasons"]:checked')).map(cb => cb.value),
                certStatus: document.getElementById('certStatus')?.value || 'None',
                timeline: document.getElementById('timeline')?.value || 'flexible',
                budget: document.getElementById('budget')?.value || 'unknown',
                additionalNotes: document.getElementById('additionalNotes')?.value || ''
            };

            // Validate at least one standard is selected
            if (formData.standards.length === 0) {
                showNotification('최소 하나의 관심 인증을 선택해주세요.', 'error');
                showStep(2); // Go back to step 2 (standards selection)
                return;
            }

            // Generate new session ID for this diagnosis (UUID v4 format)
            currentSessionId = 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
                const r = Math.random() * 16 | 0, v = c == 'x' ? r : (r & 0x3 | 0x8);
                return v.toString(16);
            });
            console.log('New diagnosis session started:', currentSessionId);

            // Hide form, show simple loading
            if (intakeForm) {
                intakeForm.style.display = 'none';
            }
            if (loadingOverlay) {
                loadingOverlay.classList.remove('hidden');
                loadingOverlay.style.display = 'flex';
                // Update loading text for faster matching
                const loadingStatus = document.getElementById('loading-status');
                if (loadingStatus) {
                    loadingStatus.textContent = '전문가를 찾고 있습니다...';
                }
            }

            try {
                // Direct matching - no AI analysis
                const response = await fetch('/api/match', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(formData)
                });

                if (!response.ok) throw new Error('Matching request failed');

                const result = await response.json();

                // Quick progress animation
                const progressFill = document.getElementById('progress-fill');
                const progressPercentage = document.getElementById('progress-percentage');

                if (progressFill) progressFill.style.width = '100%';
                if (progressPercentage) progressPercentage.textContent = '100%';

                // Short delay then show results
                setTimeout(() => {
                    displayMatchResults(result);

                    // Hide loading
                    if (loadingOverlay) {
                        loadingOverlay.classList.add('hidden');
                        loadingOverlay.style.display = 'none';
                    }

                    // Show results
                    if (resultsSection) {
                        resultsSection.classList.remove('hidden');
                        resultsSection.scrollIntoView({ behavior: 'smooth' });
                    }
                }, 500);

            } catch (error) {
                console.error('Error:', error);
                showNotification('매칭 중 오류가 발생했습니다. 다시 시도해주세요.', 'error');
                if (loadingOverlay) {
                    loadingOverlay.classList.add('hidden');
                    loadingOverlay.style.display = 'none';
                }
                if (intakeForm) {
                    intakeForm.style.display = 'block';
                }
                showStep(currentStep);
            }
        });
    }

    // --- Display Match Results (Simplified - No Risk Score) ---
    function displayMatchResults(result) {
        // Save result to localStorage for later use
        try {
            localStorage.setItem('lastMatchResult', JSON.stringify(result));
            localStorage.setItem('lastMatchTime', new Date().toISOString());
        } catch (e) {
            console.warn('Could not save match result to localStorage:', e);
        }

        // Update Company Title
        const titleEl = document.getElementById('result-company-title');
        if (titleEl) {
            titleEl.textContent = `${result.company_name || '기업'} 컨설턴트 추천`;
        }

        // Update Summary - Show selected standards
        const summaryEl = document.getElementById('ai-summary-text');
        if (summaryEl) {
            const selectedStandards = result.selected_standards || [];
            const recommendedStandards = result.recommended_standards || [];

            let summaryHTML = `<strong>선택하신 인증:</strong> ${selectedStandards.join(', ') || '없음'}`;

            if (recommendedStandards.length > 0) {
                summaryHTML += `<br><br><strong>🔔 이슈 기반 추천 인증:</strong> ${recommendedStandards.join(', ')}`;
            }

            if (result.issues_summary) {
                summaryHTML += `<br><br><strong>주요 경영 이슈:</strong> ${result.issues_summary}`;
            }

            summaryEl.innerHTML = summaryHTML;
        }

        // Hide risk score section (not used in survey-based matching)
        const scoreCard = document.querySelector('.score-card');
        if (scoreCard) {
            scoreCard.style.display = 'none';
        }

        // Hide data dashboard and evidence sections
        const dataDashboard = document.getElementById('data-dashboard');
        const evidenceSection = document.getElementById('evidence-section');
        if (dataDashboard) dataDashboard.classList.add('hidden');
        if (evidenceSection) evidenceSection.classList.add('hidden');

        // Update AI Summary title
        const aiSummaryTitle = document.querySelector('#ai-summary-text')?.parentElement?.querySelector('h3');
        if (aiSummaryTitle) {
            aiSummaryTitle.innerHTML = '<i data-lucide="clipboard-list" style="width: 20px; height: 20px; color: var(--primary);"></i> 요청 요약';
        }

        // Fetch & Display Consultants
        allConsultants = result.consultants || [];
        currentConsultantIndex = 0;
        renderConsultants();

        if (refreshContainer) {
            if (allConsultants.length > CONSULTANTS_PER_PAGE) {
                refreshContainer.classList.remove('hidden');
            } else {
                refreshContainer.classList.add('hidden');
            }
        }

        // Re-initialize Lucide icons
        if (typeof lucide !== 'undefined') {
            lucide.createIcons();
        }
    }

    async function pollForResults(jobId) {
        let progress = 0;
        let currentStep = 1;
        const statusMessages = [
            '기업 데이터를 수집하고 있습니다...',
            '웹사이트 정보를 분석하고 있습니다...',
            'AI가 리스크 요인을 평가하고 있습니다...',
            '최적의 ISO 표준을 추천하고 있습니다...',
            '분석 결과를 생성하고 있습니다...'
        ];

        // Progress animation
        const progressFill = document.getElementById('progress-fill');
        const progressPercentage = document.getElementById('progress-percentage');
        const loadingStatus = document.getElementById('loading-status');

        // Animate progress smoothly
        const progressInterval = setInterval(() => {
            if (progress < 90) {
                progress += Math.random() * 8 + 2;
                if (progress > 90) progress = 90;

                if (progressFill) progressFill.style.width = progress + '%';
                if (progressPercentage) progressPercentage.textContent = Math.floor(progress) + '%';

                // Update status message
                const messageIndex = Math.floor(progress / 20);
                if (loadingStatus && statusMessages[messageIndex]) {
                    loadingStatus.textContent = statusMessages[messageIndex];
                }

                // Update steps
                if (progress > 30 && currentStep === 1) {
                    currentStep = 2;
                    updateLoadingSteps(1, 2);
                } else if (progress > 70 && currentStep === 2) {
                    currentStep = 3;
                    updateLoadingSteps(2, 3);
                }
            }
        }, 300);

        const pollInterval = setInterval(async () => {
            try {
                const response = await fetch(`/api/analyze/${jobId}`);
                const data = await response.json();

                if (data.status === 'completed') {
                    clearInterval(pollInterval);
                    clearInterval(progressInterval);

                    // Complete progress animation
                    if (progressFill) progressFill.style.width = '100%';
                    if (progressPercentage) progressPercentage.textContent = '100%';
                    if (loadingStatus) loadingStatus.textContent = '분석이 완료되었습니다!';
                    updateLoadingSteps(3, 3, true);

                    // Wait a moment to show completion
                    setTimeout(() => {
                        displayResults(data.result);

                        // Hide loading
                        if (loadingOverlay) {
                            loadingOverlay.classList.add('hidden');
                            loadingOverlay.style.display = 'none';
                        }

                        // Show results
                        if (resultsSection) {
                            resultsSection.classList.remove('hidden');
                            resultsSection.scrollIntoView({ behavior: 'smooth' });
                        }
                    }, 500);

                } else if (data.status === 'failed') {
                    clearInterval(pollInterval);
                    clearInterval(progressInterval);
                    showNotification('분석에 실패했습니다. 다시 시도해주세요.', 'error');
                    if (loadingOverlay) {
                        loadingOverlay.classList.add('hidden');
                        loadingOverlay.style.display = 'none';
                    }
                    if (formSection) {
                        formSection.style.display = 'block';
                    }
                    showStep(currentStep);
                }
            } catch (error) {
                console.error('Polling error:', error);
            }
        }, 2000);
    }

    function updateLoadingSteps(completedStep, activeStep, allComplete = false) {
        for (let i = 1; i <= 3; i++) {
            const step = document.getElementById(`step-${i}`);
            const connector = document.getElementById(`connector-${i - 1}`);

            if (step) {
                step.classList.remove('active', 'completed');
                if (allComplete || i < activeStep) {
                    step.classList.add('completed');
                } else if (i === activeStep) {
                    step.classList.add('active');
                }
            }

            if (connector) {
                connector.style.width = (i <= completedStep) ? '100%' : '0%';
            }
        }
    }

    function displayResults(result, skipAnimation = false) {
        // Save result to localStorage for later use
        try {
            localStorage.setItem('lastAnalysisResult', JSON.stringify(result));
            localStorage.setItem('lastAnalysisTime', new Date().toISOString());
        } catch (e) {
            console.warn('Could not save analysis result to localStorage:', e);
        }

        // Update Risk Score with animation
        const score = result.risk_score || 75;
        const scoreEl = document.getElementById('risk-score');
        const circleBar = document.getElementById('score-circle-bar');

        if (scoreEl) {
            if (skipAnimation) {
                // Skip animation - show score immediately
                scoreEl.textContent = score;
            } else {
                // Animate score number
                let currentScore = 0;
                const scoreInterval = setInterval(() => {
                    if (currentScore >= score) {
                        clearInterval(scoreInterval);
                        scoreEl.textContent = score;
                    } else {
                        currentScore += 2;
                        scoreEl.textContent = Math.min(currentScore, score);
                    }
                }, 30);
            }
        }

        if (circleBar) {
            const circumference = 2 * Math.PI * 85;
            const offset = circumference - (score / 100) * circumference;
            circleBar.style.strokeDasharray = circumference;

            if (skipAnimation) {
                // Skip animation - show immediately
                circleBar.style.strokeDashoffset = offset;
            } else {
                // Delay animation for visual effect
                setTimeout(() => {
                    circleBar.style.strokeDashoffset = offset;
                }, 100);
            }

            // Update color based on score
            let color = '#ef4444'; // Red (High Risk)
            if (score >= 80) color = '#22c55e'; // Green (Safe)
            else if (score >= 60) color = '#f59e0b'; // Orange (Caution)

            circleBar.style.stroke = color;
            if (scoreEl) scoreEl.style.color = color;
        }

        // Update Risk Level Text
        const riskLevelText = document.getElementById('risk-level-text');
        if (riskLevelText) {
            if (result.risk_level) {
                riskLevelText.textContent = result.risk_level;
            } else {
                let level = '위험 (High Risk)';
                if (score >= 80) level = '안전 (Low Risk)';
                else if (score >= 60) level = '주의 (Moderate Risk)';
                riskLevelText.textContent = level;
            }

            // Set color
            let color = '#ef4444';
            if (score >= 80) color = '#22c55e';
            else if (score >= 60) color = '#f59e0b';
            riskLevelText.style.color = color;
        }

        // Update Company Title
        const titleEl = document.getElementById('result-company-title');
        if (titleEl) {
            titleEl.textContent = `${result.company_name || '기업'} 분석 결과`;
        }

        // Update AI Summary
        const summaryEl = document.getElementById('ai-summary-text');
        if (summaryEl) {
            summaryEl.innerHTML = result.summary || '분석 결과를 확인해주세요.';
        }

        // Update Risk Factors
        const tagsContainer = document.getElementById('risk-tags');
        if (tagsContainer) {
            tagsContainer.innerHTML = '';
            (result.risk_factors || []).forEach(factor => {
                const item = document.createElement('div');
                item.className = 'risk-factor-item';
                item.innerHTML = `
                    <i data-lucide="alert-triangle" class="risk-factor-icon" style="width: 18px; height: 18px;"></i>
                    <span style="color: var(--text-secondary); font-size: 0.95rem;">${factor}</span>
                `;
                tagsContainer.appendChild(item);
            });

            // Re-initialize Lucide icons
            if (typeof lucide !== 'undefined') {
                lucide.createIcons();
            }
        }

        // ===== NEW: Populate Data Dashboard =====
        const dataDashboard = document.getElementById('data-dashboard');
        const evidenceSection = document.getElementById('evidence-section');

        // Check if we have any external data to show (Backend scanner OR AI grounding)
        const newsEvidenceLinks = result.evidence_links || [];
        const hasNewsData = (result.news_data && result.news_data.total_signals > 0) || newsEvidenceLinks.length > 0;
        const hasSnsData = result.sns_data && result.sns_data.total_mentions > 0;
        const hasGovData = result.verified_data;

        if (hasNewsData || hasSnsData || hasGovData) {
            dataDashboard.classList.remove('hidden');

            // News Stats
            if (hasNewsData) {
                // If backend found signals, use that, otherwise use count of AI evidence links
                const signalCount = result.news_data?.total_signals || newsEvidenceLinks.length;
                document.getElementById('news-signal-count').textContent = signalCount + '건';

                const newsRiskBadge = document.getElementById('news-risk-badge');
                // Use AI-provided risk level if available, fallback to backend
                const riskLevel = result.news_risk_level || result.news_data?.risk_level || 'UNKNOWN';

                newsRiskBadge.textContent = riskLevel;
                if (riskLevel === 'HIGH') {
                    newsRiskBadge.style.background = 'rgba(239, 68, 68, 0.2)';
                    newsRiskBadge.style.color = '#ef4444';
                } else if (riskLevel === 'MEDIUM') {
                    newsRiskBadge.style.background = 'rgba(245, 158, 11, 0.2)';
                    newsRiskBadge.style.color = '#f59e0b';
                } else {
                    newsRiskBadge.style.background = 'rgba(34, 197, 94, 0.2)';
                    newsRiskBadge.style.color = '#22c55e';
                }
            } else {
                document.getElementById('news-signal-count').textContent = '0건';
                document.getElementById('news-risk-badge').textContent = '없음';
            }

            // SNS Stats
            if (hasSnsData) {
                document.getElementById('sns-mention-count').textContent = result.sns_data.total_mentions + '건';
                document.getElementById('sns-negative-ratio').textContent = `부정 비율: ${result.sns_data.negative_ratio}%`;

                // SNS Keywords
                const keywords = result.sns_data.top_keywords || [];
                if (keywords.length > 0) {
                    document.getElementById('sns-keywords-section').classList.remove('hidden');
                    const keywordsContainer = document.getElementById('sns-keywords');
                    keywordsContainer.innerHTML = '';
                    keywords.forEach(kw => {
                        const tag = document.createElement('span');
                        tag.style.cssText = 'background: rgba(239,68,68,0.15); color: #f87171; padding: 4px 10px; border-radius: 12px; font-size: 0.8rem;';
                        tag.textContent = `"${kw}"`;
                        keywordsContainer.appendChild(tag);
                    });
                }
            } else {
                document.getElementById('sns-mention-count').textContent = '0건';
                document.getElementById('sns-negative-ratio').textContent = '데이터 없음';
            }

            // Government Data Status
            if (hasGovData) {
                document.getElementById('gov-data-status').textContent = '✓ 검증됨';
                document.getElementById('gov-data-status').style.color = '#22c55e';
            } else {
                document.getElementById('gov-data-status').textContent = '✗ 미확인';
                document.getElementById('gov-data-status').style.color = '#f87171';
            }
        }

        // ===== NEW: Populate Evidence Section =====
        let newsEvidence = result.news_data?.evidence || result.news_data?.top_signals || [];

        // If newsEvidence is empty but we have AI evidence links, create entries for them
        if (newsEvidence.length === 0 && newsEvidenceLinks.length > 0) {
            newsEvidence = newsEvidenceLinks.map(link => ({
                headline: 'AI 분석 기반 수집된 근거 자료 (Grounding)',
                url: link,
                category: 'AI 탐지',
                related_iso: '분석 요약 참고'
            }));
        }

        const snsEvidence = result.sns_data?.evidence || result.sns_data?.top_negative_mentions || [];

        if (newsEvidence.length > 0 || snsEvidence.length > 0) {
            evidenceSection.classList.remove('hidden');
            evidenceSection.style.display = 'block'; // Ensure visibility

            // News Evidence
            const newsEvidenceItems = document.getElementById('news-evidence-items');
            newsEvidenceItems.innerHTML = '';
            if (newsEvidence.length > 0) {
                newsEvidence.forEach(item => {
                    const link = document.createElement('a');
                    link.href = item.url || '#';
                    link.target = '_blank';
                    link.rel = 'noopener noreferrer';
                    link.style.cssText = 'display: flex; align-items: flex-start; gap: 10px; padding: 10px 12px; background: rgba(0,0,0,0.2); border-radius: 8px; text-decoration: none; transition: background 0.2s;';
                    link.onmouseover = () => link.style.background = 'rgba(0,0,0,0.35)';
                    link.onmouseout = () => link.style.background = 'rgba(0,0,0,0.2)';

                    const categoryBadge = item.category ? `<span style="font-size: 0.7rem; background: rgba(16,185,129,0.2); color: #10b981; padding: 2px 6px; border-radius: 4px; white-space: nowrap;">${item.category}</span>` : '';

                    link.innerHTML = `
                        <i data-lucide="external-link" style="width: 14px; height: 14px; color: var(--text-muted); flex-shrink: 0; margin-top: 2px;"></i>
                        <div style="flex: 1; min-width: 0;">
                            <div style="color: var(--text-primary); font-size: 0.9rem; line-height: 1.4; overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;">${item.headline || item.title || '제목 없음'}</div>
                            <div style="margin-top: 4px; display: flex; gap: 6px; align-items: center;">
                                ${categoryBadge}
                                ${item.related_iso ? `<span style="font-size: 0.7rem; color: var(--text-muted);">관련: ${item.related_iso}</span>` : ''}
                            </div>
                        </div>
                    `;
                    newsEvidenceItems.appendChild(link);
                });
            } else {
                newsEvidenceItems.innerHTML = '<p style="color: var(--text-muted); font-size: 0.85rem;">수집된 뉴스 기사가 없습니다.</p>';
            }

            // SNS Evidence
            const snsEvidenceList = document.getElementById('sns-evidence-list');
            const snsEvidenceItems = document.getElementById('sns-evidence-items');
            if (snsEvidence.length > 0) {
                snsEvidenceList.classList.remove('hidden');
                snsEvidenceItems.innerHTML = '';
                snsEvidence.forEach(item => {
                    const link = document.createElement('a');
                    link.href = item.url || '#';
                    link.target = '_blank';
                    link.rel = 'noopener noreferrer';
                    link.style.cssText = 'display: flex; align-items: center; gap: 10px; padding: 10px 12px; background: rgba(0,0,0,0.2); border-radius: 8px; text-decoration: none; transition: background 0.2s;';
                    link.onmouseover = () => link.style.background = 'rgba(0,0,0,0.35)';
                    link.onmouseout = () => link.style.background = 'rgba(0,0,0,0.2)';

                    const sourceIcon = item.source === 'cafe' ? '☕' : '📝';
                    link.innerHTML = `
                        <span style="font-size: 1.1rem;">${sourceIcon}</span>
                        <span style="color: var(--text-primary); font-size: 0.9rem; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${item.title || '제목 없음'}</span>
                        <i data-lucide="external-link" style="width: 14px; height: 14px; color: var(--text-muted);"></i>
                    `;
                    snsEvidenceItems.appendChild(link);
                });
            }

            // Re-initialize Lucide icons for evidence section
            if (typeof lucide !== 'undefined') {
                lucide.createIcons();
            }
        }

        // Show AI Model Used
        if (result.ai_model_used) {
            const aiTitle = document.querySelector('#ai-summary-text').parentElement.querySelector('h3');
            if (aiTitle && !document.getElementById('ai-model-badge')) {
                const badge = document.createElement('span');
                badge.id = 'ai-model-badge';
                badge.style.cssText = 'font-size: 0.65rem; background: rgba(255,255,255,0.1); color: var(--text-muted); padding: 2px 6px; border-radius: 4px; margin-left: auto; font-weight: normal;';
                badge.textContent = `Model: ${result.ai_model_used}`;
                aiTitle.appendChild(badge);
            }
        }

        // Fetch & Display Consultants
        fetchConsultants(result);
    }

    async function fetchConsultants(analysisResult) {
        try {
            const params = new URLSearchParams();

            // Use recommended_standards from AI or the input standards
            const standards = analysisResult.recommended_standards || [];
            standards.forEach(s => params.append('iso', s));

            if (analysisResult.industry) {
                params.append('industry', analysisResult.industry);
            }

            const response = await fetch(`/api/consultants?${params.toString()}`);
            allConsultants = await response.json();

            currentConsultantIndex = 0;
            renderConsultants();

            if (refreshContainer) {
                if (allConsultants.length > CONSULTANTS_PER_PAGE) {
                    refreshContainer.classList.remove('hidden');
                } else {
                    refreshContainer.classList.add('hidden');
                }
            }

        } catch (error) {
            console.error('Failed to fetch consultants:', error);
            if (consultantList) {
                consultantList.innerHTML = '<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: var(--text-muted);">컨설턴트를 불러오는 중 오류가 발생했습니다.</div>';
            }
        }
    }

    function renderConsultants() {
        if (!consultantList) return;

        consultantList.innerHTML = '';

        // Use filtered consultants if filters are active, otherwise use all
        const displayConsultants = filteredConsultants.length > 0 || isFilterActive() ? filteredConsultants : allConsultants;

        if (displayConsultants.length === 0 && allConsultants.length > 0) {
            consultantList.innerHTML = '<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: var(--text-muted);">필터 조건에 맞는 전문가가 없습니다. 필터를 조정해보세요.</div>';
            updateFilterResultCount(0);
            return;
        }

        if (displayConsultants.length === 0) {
            consultantList.innerHTML = '<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: var(--text-muted);">조건에 맞는 전문가를 찾고 있습니다...</div>';
            return;
        }

        const batch = displayConsultants.slice(currentConsultantIndex, currentConsultantIndex + CONSULTANTS_PER_PAGE);

        if (batch.length === 0) {
            currentConsultantIndex = 0;
            renderConsultants();
            return;
        }

        // Update result count
        updateFilterResultCount(displayConsultants.length);

        batch.forEach((c, index) => {
            const card = document.createElement('div');
            card.className = 'consultant-card fade-in-up';
            card.style.animationDelay = `${index * 0.1}s`;
            card.dataset.consultantId = c.id;

            const trustScore = c.trustScore || 85;
            const isSelected = selectedConsultants.has(c.id);

            // Enhanced verified badge
            const verifiedBadge = c.verified
                ? `<span class="verified-badge" title="InsightMatch에서 검증된 전문가입니다">
                     <i data-lucide="badge-check" style="width: 12px; height: 12px;"></i> 검증됨
                   </span>`
                : `<span class="unverified-badge" title="검증 대기 중">
                     <i data-lucide="clock" style="width: 10px; height: 10px;"></i> 검토중
                   </span>`;

            // Add selected class if consultant is selected
            if (isSelected) {
                card.classList.add('selected');
            }

            card.innerHTML = `
                <div class="consultant-select-checkbox">
                    <label class="consultant-checkbox" onclick="event.preventDefault(); toggleConsultantSelection(${c.id}, event)">
                        <input type="checkbox" ${isSelected ? 'checked' : ''} data-id="${c.id}" onclick="event.stopPropagation()">
                        <span class="checkbox-custom">
                            <i data-lucide="check" style="width: 14px; height: 14px;"></i>
                        </span>
                        <span class="checkbox-label">견적 요청 선택</span>
                    </label>
                </div>
                
                <div class="consultant-header">
                    <div class="consultant-avatar">${c.avatar || c.name[0]}</div>
                    <div class="consultant-info">
                        <h4>
                            ${c.name}
                            ${verifiedBadge}
                        </h4>
                        <span class="consultant-specialty">${c.specialty || '종합'} 전문</span>
                    </div>
                </div>
                
                <div class="consultant-match-reason">
                    ${c.matchReason || '매칭 전문가'}
                </div>
                
                <div style="margin-bottom: 16px;">
                    <div class="flex justify-between items-center" style="font-size: 0.9rem; margin-bottom: 6px;">
                        <span class="trust-tooltip" data-tooltip="경력, 프로젝트 이력, 고객 평가 기반 점수" style="color: var(--text-muted); cursor: help;">전문가 신뢰도</span>
                        <span style="color: var(--primary); font-weight: 600;">${trustScore}점</span>
                    </div>
                    <div class="trust-score-bar">
                        <div class="trust-score-fill" style="width: ${trustScore}%;"></div>
                    </div>
                </div>
                
                <div class="consultant-stats">
                    <span>경력 ${c.experience || '정보없음'}</span>
                    <span>후기 ${c.reviews || 0}개</span>
                    <span class="match-score">매칭률 ${c.matchScore || 95}%</span>
                </div>
                
                <div style="display: flex; gap: 8px; margin-top: 16px;">
                    <a href="consultant_profile.html?id=${c.id}" class="btn btn-secondary" style="flex: 1;">
                        <i data-lucide="user" style="width: 16px; height: 16px;"></i>
                        프로필 보기
                    </a>
                </div>
            `;
            consultantList.appendChild(card);
        });

        // Update selection bar
        updateSelectionBar();

        // Re-initialize Lucide icons
        if (typeof lucide !== 'undefined') {
            lucide.createIcons();
        }
    }

    // --- Consultant Selection Functions ---
    window.toggleConsultantSelection = function (consultantId, event) {
        if (event) {
            event.stopPropagation();
        }

        const consultant = allConsultants.find(c => c.id === consultantId);
        if (!consultant) return;

        if (selectedConsultants.has(consultantId)) {
            // Deselect
            selectedConsultants.delete(consultantId);
        } else {
            // Check max selections
            if (selectedConsultants.size >= MAX_SELECTIONS) {
                showNotification(`최대 ${MAX_SELECTIONS}명까지만 선택할 수 있습니다.`, 'error');
                return;
            }
            // Select
            selectedConsultants.set(consultantId, consultant);
        }

        // Update UI
        updateConsultantCardSelection(consultantId);
        updateSelectionBar();
    };

    function updateConsultantCardSelection(consultantId) {
        const card = document.querySelector(`.consultant-card[data-consultant-id="${consultantId}"]`);
        if (!card) return;

        const checkbox = card.querySelector('input[type="checkbox"]');
        const isSelected = selectedConsultants.has(consultantId);

        if (isSelected) {
            card.classList.add('selected');
            if (checkbox) checkbox.checked = true;
        } else {
            card.classList.remove('selected');
            if (checkbox) checkbox.checked = false;
        }
    }

    function updateSelectionBar() {
        const selectionBar = document.getElementById('selection-bar');
        const selectionCount = document.getElementById('selection-count');
        const selectedNames = document.getElementById('selected-names');
        const requestBtn = document.getElementById('request-quotes-btn');

        if (!selectionBar) return;

        const count = selectedConsultants.size;

        if (count > 0) {
            selectionBar.classList.add('active');
            if (selectionCount) selectionCount.textContent = count;

            // Update selected names
            if (selectedNames) {
                const names = Array.from(selectedConsultants.values())
                    .map(c => c.name)
                    .join(', ');
                selectedNames.textContent = names;
            }

            // Enable/disable request button
            if (requestBtn) {
                requestBtn.disabled = false;
            }
        } else {
            selectionBar.classList.remove('active');
            if (selectionCount) selectionCount.textContent = '0';
            if (selectedNames) selectedNames.textContent = '';
            if (requestBtn) requestBtn.disabled = true;
        }

        // Re-initialize icons
        if (typeof lucide !== 'undefined') {
            lucide.createIcons();
        }
    }

    window.clearConsultantSelection = function () {
        selectedConsultants.clear();

        // Update all cards
        document.querySelectorAll('.consultant-card').forEach(card => {
            card.classList.remove('selected');
            const checkbox = card.querySelector('input[type="checkbox"]');
            if (checkbox) checkbox.checked = false;
        });

        updateSelectionBar();
    };

    window.selectAllVisibleConsultants = function () {
        const displayConsultants = filteredConsultants.length > 0 || isFilterActive() ? filteredConsultants : allConsultants;
        const batch = displayConsultants.slice(currentConsultantIndex, currentConsultantIndex + CONSULTANTS_PER_PAGE);

        let addedCount = 0;
        batch.forEach(c => {
            if (!selectedConsultants.has(c.id) && selectedConsultants.size < MAX_SELECTIONS) {
                selectedConsultants.set(c.id, c);
                updateConsultantCardSelection(c.id);
                addedCount++;
            }
        });

        if (addedCount === 0 && selectedConsultants.size >= MAX_SELECTIONS) {
            showNotification(`최대 ${MAX_SELECTIONS}명까지만 선택할 수 있습니다.`, 'error');
        }

        updateSelectionBar();
    };

    window.requestQuotes = async function () {
        if (selectedConsultants.size === 0) {
            showNotification('견적을 요청할 컨설턴트를 선택해주세요.', 'error');
            return;
        }

        const selectedIds = Array.from(selectedConsultants.keys());
        const selectedList = Array.from(selectedConsultants.values());

        // Get last match/analysis result for context (check both keys for different flows)
        const savedMatchResult = localStorage.getItem('lastMatchResult');
        const savedAnalysisResult = localStorage.getItem('lastAnalysisResult');
        const savedResult = savedMatchResult || savedAnalysisResult;
        let analysisContext = null;
        try {
            analysisContext = savedResult ? JSON.parse(savedResult) : {};
        } catch (e) {
            console.warn('Could not parse result:', e);
            analysisContext = {};
        }

        // Get user ID from localStorage
        const user = JSON.parse(localStorage.getItem('user'));
        if (!user || !user.id) {
            showNotification('로그인이 필요합니다. 먼저 로그인해주세요.', 'error');
            setTimeout(() => {
                window.location.href = 'login.html';
            }, 1500);
            return;
        }

        // Show confirmation notification first
        const names = selectedList.map(c => c.name).join(', ');
        showNotification(`${selectedConsultants.size}명의 전문가(${names})에게 견적을 요청합니다...`, 'info');

        // Show loading state
        const requestBtn = document.getElementById('request-quotes-btn');
        if (requestBtn) {
            requestBtn.disabled = true;
            requestBtn.innerHTML = '<span class="loading-spinner" style="width: 16px; height: 16px; border-width: 2px;"></span> 요청 중...';
        }

        try {
            const response = await fetch('/api/quotes/request', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    consultant_ids: selectedIds,
                    analysis_context: analysisContext,
                    user_id: user.id,
                    session_id: currentSessionId  // Include session ID for grouping
                })
            });

            if (response.ok) {
                const result = await response.json();
                showNotification(`✅ ${selectedConsultants.size}명의 전문가에게 견적을 요청했습니다! 대시보드에서 확인하세요.`, 'success');

                // Clear selection
                window.clearConsultantSelection();

                // Redirect to dashboard after 2 seconds
                setTimeout(() => {
                    window.location.href = 'dashboard.html';
                }, 2000);
            } else {
                const error = await response.json();
                showNotification(error.message || '견적 요청에 실패했습니다.', 'error');
            }
        } catch (error) {
            console.error('Quote request error:', error);
            showNotification('서버 연결에 실패했습니다. 다시 시도해주세요.', 'error');
        } finally {
            if (requestBtn) {
                requestBtn.disabled = false;
                requestBtn.innerHTML = `
                    <i data-lucide="send" style="width: 18px; height: 18px;"></i>
                    견적 요청하기
                `;
                lucide.createIcons();
            }
        }
    };

    // Alias for button onclick
    window.requestQuotesForSelected = window.requestQuotes;

    // --- Compare Modal Functions ---
    window.showCompareModal = function () {
        if (selectedConsultants.size < 2) {
            showNotification('비교하려면 2명 이상의 전문가를 선택해주세요.', 'error');
            return;
        }

        const modal = document.getElementById('compare-modal');
        const content = document.getElementById('compare-content');

        if (!modal || !content) return;

        const consultants = Array.from(selectedConsultants.values());

        // Build comparison table
        let tableHTML = `
            <div class="compare-table-wrapper" style="overflow-x: auto;">
                <table class="compare-table" style="width: 100%; border-collapse: collapse;">
                    <thead>
                        <tr>
                            <th style="text-align: left; padding: 12px; border-bottom: 1px solid var(--border); color: var(--text-muted); font-weight: 500;">항목</th>
                            ${consultants.map(c => `
                                <th style="text-align: center; padding: 12px; border-bottom: 1px solid var(--border); min-width: 180px;">
                                    <div style="display: flex; flex-direction: column; align-items: center; gap: 8px;">
                                        <div style="width: 48px; height: 48px; background: linear-gradient(135deg, var(--primary), var(--accent)); border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 1.25rem; font-weight: 600; color: white;">
                                            ${c.avatar || c.name[0]}
                                        </div>
                                        <div>
                                            <div style="font-weight: 600;">${c.name}</div>
                                            ${c.verified ? '<span class="verified-badge" style="margin-top: 4px;"><i data-lucide="badge-check" style="width: 12px; height: 12px;"></i> 검증됨</span>' : ''}
                                        </div>
                                    </div>
                                </th>
                            `).join('')}
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td style="padding: 12px; border-bottom: 1px solid var(--border); color: var(--text-secondary);">전문 분야</td>
                            ${consultants.map(c => `<td style="padding: 12px; text-align: center; border-bottom: 1px solid var(--border);">${c.specialty || '종합'}</td>`).join('')}
                        </tr>
                        <tr>
                            <td style="padding: 12px; border-bottom: 1px solid var(--border); color: var(--text-secondary);">경력</td>
                            ${consultants.map(c => `<td style="padding: 12px; text-align: center; border-bottom: 1px solid var(--border);">${c.experience || '정보없음'}</td>`).join('')}
                        </tr>
                        <tr>
                            <td style="padding: 12px; border-bottom: 1px solid var(--border); color: var(--text-secondary);">신뢰 점수</td>
                            ${consultants.map(c => `
                                <td style="padding: 12px; text-align: center; border-bottom: 1px solid var(--border);">
                                    <div style="display: flex; align-items: center; justify-content: center; gap: 8px;">
                                        <span style="font-weight: 700; color: var(--primary); font-size: 1.25rem;">${c.trustScore || 85}</span>
                                        <div style="width: 60px; height: 6px; background: var(--bg-secondary); border-radius: 3px; overflow: hidden;">
                                            <div style="width: ${c.trustScore || 85}%; height: 100%; background: var(--primary);"></div>
                                        </div>
                                    </div>
                                </td>
                            `).join('')}
                        </tr>
                        <tr>
                            <td style="padding: 12px; border-bottom: 1px solid var(--border); color: var(--text-secondary);">매칭률</td>
                            ${consultants.map(c => `<td style="padding: 12px; text-align: center; border-bottom: 1px solid var(--border); font-weight: 600; color: var(--primary);">${c.matchScore || 95}%</td>`).join('')}
                        </tr>
                        <tr>
                            <td style="padding: 12px; border-bottom: 1px solid var(--border); color: var(--text-secondary);">리뷰 수</td>
                            ${consultants.map(c => `<td style="padding: 12px; text-align: center; border-bottom: 1px solid var(--border);">${c.reviews || 0}개</td>`).join('')}
                        </tr>
                        <tr>
                            <td style="padding: 12px; border-bottom: 1px solid var(--border); color: var(--text-secondary);">ISO 규격</td>
                            ${consultants.map(c => {
            const isoList = c.isoExperience ? Object.keys(c.isoExperience).map(iso => `ISO ${iso}`).join(', ') : '-';
            return `<td style="padding: 12px; text-align: center; border-bottom: 1px solid var(--border); font-size: 0.85rem;">${isoList}</td>`;
        }).join('')}
                        </tr>
                        <tr>
                            <td style="padding: 12px; color: var(--text-secondary);">매칭 이유</td>
                            ${consultants.map(c => `<td style="padding: 12px; text-align: center; font-size: 0.85rem; color: var(--text-muted);">${c.matchReason || '-'}</td>`).join('')}
                        </tr>
                    </tbody>
                </table>
            </div>
            <div style="margin-top: 24px; text-align: center;">
                <button class="btn btn-primary" onclick="requestQuotesForSelected(); closeCompareModal();">
                    <i data-lucide="send" style="width: 18px; height: 18px;"></i>
                    선택한 ${consultants.length}명에게 견적 요청하기
                </button>
            </div>
        `;

        content.innerHTML = tableHTML;
        modal.classList.remove('hidden');
        document.body.style.overflow = 'hidden';

        // Re-initialize icons
        if (typeof lucide !== 'undefined') {
            lucide.createIcons();
        }
    };

    window.closeCompareModal = function () {
        const modal = document.getElementById('compare-modal');
        if (modal) {
            modal.classList.add('hidden');
            document.body.style.overflow = '';
        }
    };

    // Close modal on escape key
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            window.closeCompareModal();
        }
    });

    // --- Filter Functions ---
    function isFilterActive() {
        const verifiedFilter = document.getElementById('filter-verified');
        const ratedFilter = document.getElementById('filter-rated');
        const isoFilter = document.getElementById('filter-iso');
        const industryFilter = document.getElementById('filter-industry');
        const regionFilter = document.getElementById('filter-region');

        return (verifiedFilter && verifiedFilter.checked) ||
            (ratedFilter && ratedFilter.checked) ||
            (isoFilter && isoFilter.value) ||
            (industryFilter && industryFilter.value) ||
            (regionFilter && regionFilter.value.trim());
    }

    function updateFilterResultCount(count) {
        const resultCountEl = document.getElementById('filter-result-count');
        const resultCountText = document.getElementById('result-count-text');

        if (resultCountEl && resultCountText) {
            if (isFilterActive()) {
                resultCountEl.classList.remove('hidden');
                resultCountText.textContent = `${count}명의 전문가가 검색되었습니다`;
            } else {
                resultCountEl.classList.add('hidden');
            }
        }
    }

    // Global filter functions
    window.toggleConsultantFilter = function () {
        const filterPanel = document.getElementById('consultant-filter-panel');
        const toggleBtn = document.getElementById('toggle-filter-btn');

        if (filterPanel) {
            const isHidden = filterPanel.classList.contains('hidden');
            filterPanel.classList.toggle('hidden');
            filterPanel.classList.toggle('active');

            if (toggleBtn) {
                toggleBtn.innerHTML = isHidden
                    ? '<i data-lucide="filter-x" style="width: 16px; height: 16px;"></i> 필터 닫기'
                    : '<i data-lucide="filter" style="width: 16px; height: 16px;"></i> 필터 열기';
                lucide.createIcons();
            }
        }
    };

    window.applyConsultantFilter = function () {
        const verifiedFilter = document.getElementById('filter-verified');
        const ratedFilter = document.getElementById('filter-rated');
        const isoFilter = document.getElementById('filter-iso');
        const industryFilter = document.getElementById('filter-industry');
        const regionFilter = document.getElementById('filter-region');

        filteredConsultants = allConsultants.filter(c => {
            // Verified filter
            if (verifiedFilter && verifiedFilter.checked && !c.verified) {
                return false;
            }

            // Rated filter (has reviews)
            if (ratedFilter && ratedFilter.checked && (!c.reviews || c.reviews === 0)) {
                return false;
            }

            // ISO filter
            if (isoFilter && isoFilter.value) {
                const isoExp = c.isoExperience || {};
                const hasIso = Object.keys(isoExp).some(key => key.includes(isoFilter.value));
                if (!hasIso) return false;
            }

            // Industry filter
            if (industryFilter && industryFilter.value) {
                const industries = c.industryExperience || [];
                const specialties = (c.specialty || '').toLowerCase();
                const hasIndustry = industries.some(ind =>
                    ind.toLowerCase().includes(industryFilter.value.toLowerCase())
                ) || specialties.includes(industryFilter.value.toLowerCase());
                if (!hasIndustry) return false;
            }

            // Region filter
            if (regionFilter && regionFilter.value.trim()) {
                const regionSearch = regionFilter.value.trim().toLowerCase();
                const consultantRegion = (c.regions || '').toLowerCase();
                if (!consultantRegion.includes(regionSearch)) return false;
            }

            return true;
        });

        // Update active filters display
        updateActiveFilters();

        // Reset to first page and re-render
        currentConsultantIndex = 0;
        renderConsultants();
    };

    window.resetConsultantFilter = function () {
        const verifiedFilter = document.getElementById('filter-verified');
        const ratedFilter = document.getElementById('filter-rated');
        const isoFilter = document.getElementById('filter-iso');
        const industryFilter = document.getElementById('filter-industry');
        const regionFilter = document.getElementById('filter-region');

        if (verifiedFilter) verifiedFilter.checked = false;
        if (ratedFilter) ratedFilter.checked = false;
        if (isoFilter) isoFilter.value = '';
        if (industryFilter) industryFilter.value = '';
        if (regionFilter) regionFilter.value = '';

        filteredConsultants = [];
        currentConsultantIndex = 0;

        updateActiveFilters();
        renderConsultants();
    };

    window.debounceFilter = function () {
        if (filterDebounceTimer) {
            clearTimeout(filterDebounceTimer);
        }
        filterDebounceTimer = setTimeout(() => {
            window.applyConsultantFilter();
        }, 300);
    };

    function updateActiveFilters() {
        const activeFiltersEl = document.getElementById('active-filters');
        if (!activeFiltersEl) return;

        const verifiedFilter = document.getElementById('filter-verified');
        const ratedFilter = document.getElementById('filter-rated');
        const isoFilter = document.getElementById('filter-iso');
        const industryFilter = document.getElementById('filter-industry');
        const regionFilter = document.getElementById('filter-region');

        const tags = [];

        if (verifiedFilter && verifiedFilter.checked) {
            tags.push({ label: '검증된 전문가', type: 'verified' });
        }
        if (ratedFilter && ratedFilter.checked) {
            tags.push({ label: '평가 있음', type: 'rated' });
        }
        if (isoFilter && isoFilter.value) {
            const option = isoFilter.options[isoFilter.selectedIndex];
            tags.push({ label: `ISO ${isoFilter.value}`, type: 'iso' });
        }
        if (industryFilter && industryFilter.value) {
            const option = industryFilter.options[industryFilter.selectedIndex];
            tags.push({ label: option.text, type: 'industry' });
        }
        if (regionFilter && regionFilter.value.trim()) {
            tags.push({ label: `지역: ${regionFilter.value.trim()}`, type: 'region' });
        }

        if (tags.length === 0) {
            activeFiltersEl.classList.add('hidden');
            activeFiltersEl.innerHTML = '';
            return;
        }

        activeFiltersEl.classList.remove('hidden');
        activeFiltersEl.innerHTML = tags.map(tag => `
            <span class="filter-tag">
                ${tag.label}
                <button onclick="removeFilter('${tag.type}')" aria-label="필터 제거">
                    <i data-lucide="x" style="width: 12px; height: 12px;"></i>
                </button>
            </span>
        `).join('');

        lucide.createIcons();
    }

    window.removeFilter = function (type) {
        switch (type) {
            case 'verified':
                const verifiedFilter = document.getElementById('filter-verified');
                if (verifiedFilter) verifiedFilter.checked = false;
                break;
            case 'rated':
                const ratedFilter = document.getElementById('filter-rated');
                if (ratedFilter) ratedFilter.checked = false;
                break;
            case 'iso':
                const isoFilter = document.getElementById('filter-iso');
                if (isoFilter) isoFilter.value = '';
                break;
            case 'industry':
                const industryFilter = document.getElementById('filter-industry');
                if (industryFilter) industryFilter.value = '';
                break;
            case 'region':
                const regionFilter = document.getElementById('filter-region');
                if (regionFilter) regionFilter.value = '';
                break;
        }
        window.applyConsultantFilter();
    };

    // Refresh Handler
    if (refreshBtn) {
        refreshBtn.addEventListener('click', () => {
            currentConsultantIndex += CONSULTANTS_PER_PAGE;
            if (currentConsultantIndex >= allConsultants.length) {
                currentConsultantIndex = 0;
            }

            if (consultantList) {
                consultantList.style.opacity = '0';
                consultantList.style.transform = 'translateY(10px)';

                setTimeout(() => {
                    renderConsultants();
                    consultantList.style.opacity = '1';
                    consultantList.style.transform = 'translateY(0)';
                }, 200);
            }
        });
    }

    // --- Smooth Scroll with Form Reset ---
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function (e) {
            const href = this.getAttribute('href');
            if (href === '#') return;

            e.preventDefault();
            const target = document.querySelector(href);
            if (target) {
                const navbarHeight = 80;
                const targetPosition = target.getBoundingClientRect().top + window.pageYOffset - navbarHeight;

                window.scrollTo({
                    top: targetPosition,
                    behavior: 'smooth'
                });
            }
        });
    });

    // --- Expose reset function globally for use in HTML ---
    window.resetDiagnosisForm = resetForm;

    // --- Test function to load consultants directly (for development/testing) ---
    window.testLoadConsultants = async function () {
        try {
            const response = await fetch('/api/consultants');
            const consultants = await response.json();

            // Store in allConsultants
            allConsultants = consultants;
            currentConsultantIndex = 0;

            // Show results section and hide diagnosis section
            const diagnosisSection = document.getElementById('diagnosis');
            const resultsSection = document.getElementById('results-section');

            if (diagnosisSection) diagnosisSection.classList.add('hidden');
            if (resultsSection) resultsSection.classList.remove('hidden');

            // Scroll to results
            if (resultsSection) {
                resultsSection.scrollIntoView({ behavior: 'smooth' });
            }

            // Render consultants
            renderConsultants();

            console.log('Loaded', consultants.length, 'consultants for testing');
            return consultants;
        } catch (error) {
            console.error('Failed to load consultants:', error);
        }
    };

    // Auto-load consultants in test mode (URL parameter: ?test=consultants)
    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.get('test') === 'consultants') {
        window.testLoadConsultants();
    }
});
