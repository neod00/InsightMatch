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
    let currentConsultantIndex = 0;
    const CONSULTANTS_PER_PAGE = 3;

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
        if (formSection) {
            formSection.style.display = 'block';
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
            if (formSection) {
                formSection.style.display = 'none';
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
                if (formSection) {
                    formSection.style.display = 'block';
                }
                showStep(currentStep);
            }
        });
    }

    async function pollForResults(jobId) {
        const pollInterval = setInterval(async () => {
            try {
                const response = await fetch(`/api/analyze/${jobId}`);
                const data = await response.json();

                if (data.status === 'completed') {
                    clearInterval(pollInterval);
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
                } else if (data.status === 'failed') {
                    clearInterval(pollInterval);
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

    function displayResults(result) {
        // Update Risk Score with animation
        const score = result.risk_score || 75;
        const scoreEl = document.getElementById('risk-score');
        const circleBar = document.getElementById('score-circle-bar');
        
        if (scoreEl) {
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
        
        if (circleBar) {
            const circumference = 2 * Math.PI * 85;
            const offset = circumference - (score / 100) * circumference;
            circleBar.style.strokeDasharray = circumference;
            
            // Delay animation for visual effect
            setTimeout(() => {
                circleBar.style.strokeDashoffset = offset;
            }, 100);
            
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

        if (allConsultants.length === 0) {
            consultantList.innerHTML = '<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: var(--text-muted);">조건에 맞는 전문가를 찾고 있습니다...</div>';
            return;
        }

        const batch = allConsultants.slice(currentConsultantIndex, currentConsultantIndex + CONSULTANTS_PER_PAGE);

        if (batch.length === 0) {
            currentConsultantIndex = 0;
            renderConsultants();
            return;
        }

        batch.forEach((c, index) => {
            const card = document.createElement('div');
            card.className = 'consultant-card fade-in-up';
            card.style.animationDelay = `${index * 0.1}s`;
            
            const trustScore = c.trustScore || 85;
            
            card.innerHTML = `
                <div class="consultant-header">
                    <div class="consultant-avatar">${c.avatar || c.name[0]}</div>
                    <div class="consultant-info">
                        <h4>
                            ${c.name}
                            ${c.verified ? '<span class="verified-badge"><i data-lucide="check" style="width: 12px; height: 12px;"></i> Verified</span>' : ''}
                        </h4>
                        <span class="consultant-specialty">${c.specialty} 전문</span>
                    </div>
                </div>
                
                <div class="consultant-match-reason">
                    ${c.matchReason || 'AI 추천 매칭'}
                </div>
                
                <div style="margin-bottom: 16px;">
                    <div class="flex justify-between items-center" style="font-size: 0.9rem; margin-bottom: 6px;">
                        <span style="color: var(--text-muted);">전문가 신뢰도</span>
                        <span style="color: var(--primary); font-weight: 600;">${trustScore}점</span>
                    </div>
                    <div class="trust-score-bar">
                        <div class="trust-score-fill" style="width: ${trustScore}%;"></div>
                    </div>
                </div>
                
                <div class="consultant-stats">
                    <span>경력 ${c.experience}</span>
                    <span>후기 ${c.reviews || 0}개</span>
                    <span class="match-score">매칭률 ${c.matchScore || 95}%</span>
                </div>
                
                <button class="btn btn-primary" style="width: 100%; margin-top: 16px;">
                    컨설팅 견적받기
                </button>
            `;
            consultantList.appendChild(card);
        });
        
        // Re-initialize Lucide icons
        if (typeof lucide !== 'undefined') {
            lucide.createIcons();
        }
    }

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
});
