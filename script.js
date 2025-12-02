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
    
    // --- Filter State ---
    let filterDebounceTimer = null;

    // --- Check for returning from consultant profile (find other consultants) ---
    function checkForPreviousResults() {
        const urlParams = new URLSearchParams(window.location.search);
        const action = urlParams.get('action');
        
        if (action === 'find-others') {
            // User clicked "AI로 다른 전문가 찾기" from consultant profile
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
            showNotification('이전 분석 결과가 만료되었습니다. 새로 진단을 시작해주세요.', 'info');
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
        
        // Scroll to diagnosis section
        const diagnosisSection = document.getElementById('diagnosis');
        if (diagnosisSection) {
            setTimeout(() => {
                diagnosisSection.scrollIntoView({ behavior: 'smooth' });
            }, 100);
        }
    }

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

    // --- Form Submission & Analysis ---
    if (intakeForm) {
        intakeForm.addEventListener('submit', async (e) => {
            e.preventDefault();

            // Collect Data
            const formData = {
                companyName: document.getElementById('companyName')?.value || '',
                companyUrl: document.getElementById('companyUrl')?.value || '',
                industry: document.getElementById('industry')?.value || '',
                employees: document.getElementById('employees')?.value || '',
                standards: Array.from(document.querySelectorAll('input[name="standards"]:checked')).map(cb => cb.value),
                certStatus: document.getElementById('certStatus')?.value || 'None',
                readiness: document.getElementById('readiness')?.value || 'Initial',
                targetDate: document.getElementById('targetDate')?.value || '',
                budget: document.getElementById('budget')?.value || 'Undecided'
            };

            // Validate at least one standard is selected
            if (formData.standards.length === 0) {
                showNotification('최소 하나의 관심 인증을 선택해주세요.', 'error');
                return;
            }

            // Hide form, show loading
            if (intakeForm) {
                intakeForm.style.display = 'none';
            }
            if (loadingOverlay) {
                loadingOverlay.classList.remove('hidden');
                loadingOverlay.style.display = 'flex';
            }

            try {
                // 1. Start Analysis Job
                const response = await fetch('/api/analyze', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(formData)
                });

                if (!response.ok) throw new Error('Analysis request failed');

                const { job_id } = await response.json();

                // 2. Poll for Results
                pollForResults(job_id);

            } catch (error) {
                console.error('Error:', error);
                showNotification('분석 중 오류가 발생했습니다. 다시 시도해주세요.', 'error');
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
                    ${c.matchReason || 'AI 추천 매칭'}
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
    window.toggleConsultantSelection = function(consultantId, event) {
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
    
    window.clearConsultantSelection = function() {
        selectedConsultants.clear();
        
        // Update all cards
        document.querySelectorAll('.consultant-card').forEach(card => {
            card.classList.remove('selected');
            const checkbox = card.querySelector('input[type="checkbox"]');
            if (checkbox) checkbox.checked = false;
        });
        
        updateSelectionBar();
    };
    
    window.selectAllVisibleConsultants = function() {
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
    
    window.requestQuotes = async function() {
        if (selectedConsultants.size === 0) {
            showNotification('견적을 요청할 컨설턴트를 선택해주세요.', 'error');
            return;
        }
        
        const selectedIds = Array.from(selectedConsultants.keys());
        const selectedList = Array.from(selectedConsultants.values());
        
        // Get last analysis result for context
        const savedResult = localStorage.getItem('lastAnalysisResult');
        let analysisContext = null;
        try {
            analysisContext = savedResult ? JSON.parse(savedResult) : null;
        } catch (e) {
            console.warn('Could not parse analysis result:', e);
        }
        
        // Show confirmation modal
        const names = selectedList.map(c => c.name).join(', ');
        const confirmed = confirm(`다음 ${selectedConsultants.size}명의 전문가에게 견적을 요청합니다:\n\n${names}\n\n진행하시겠습니까?`);
        
        if (!confirmed) return;
        
        // Show loading state
        const requestBtn = document.getElementById('request-quotes-btn');
        if (requestBtn) {
            requestBtn.disabled = true;
            requestBtn.innerHTML = '<span class="loading-spinner" style="width: 16px; height: 16px; border-width: 2px;"></span> 요청 중...';
        }
        
        // Get user ID from localStorage
        const user = JSON.parse(localStorage.getItem('user'));
        if (!user || !user.id) {
            showNotification('로그인이 필요합니다. 먼저 로그인해주세요.', 'error');
            window.location.href = 'login.html';
            return;
        }
        
        try {
            const response = await fetch('/api/quotes/request', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    consultant_ids: selectedIds,
                    analysis_context: analysisContext,
                    user_id: user.id
                })
            });
            
            if (response.ok) {
                const result = await response.json();
                showNotification(`${selectedConsultants.size}명의 전문가에게 견적을 요청했습니다. 대시보드에서 확인하세요.`, 'success');
                
                // Clear selection
                window.clearConsultantSelection();
                
                // Optionally redirect to dashboard
                setTimeout(() => {
                    const goToDashboard = confirm('견적 요청이 완료되었습니다.\n대시보드에서 확인하시겠습니까?');
                    if (goToDashboard) {
                        window.location.href = 'dashboard.html';
                    }
                }, 500);
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
    window.toggleConsultantFilter = function() {
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
    
    window.applyConsultantFilter = function() {
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
    
    window.resetConsultantFilter = function() {
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
    
    window.debounceFilter = function() {
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
    
    window.removeFilter = function(type) {
        switch(type) {
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
    window.testLoadConsultants = async function() {
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
