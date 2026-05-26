// ISO Standards Catalog - Data & Rendering
(function () {
    const isoStandards = [
        { cat: 'mgmt', num: 'ISO 9001:2015', name: '품질경영시스템', desc: '제품·서비스 품질을 체계적으로 관리하기 위한 국제 표준. 가장 널리 취득되는 ISO 인증입니다.', tags: ['전 산업', '필수', '가장 많이 취득'], detail: { who: ['모든 산업의 기업', '고객사가 인증을 요구하는 기업', '공공입찰 참여 기업', '수출기업'], benefit: ['고객 신뢰도 및 만족도 향상', '내부 프로세스 효율화', '공공조달·입찰 가점', '글로벌 시장 진출 기반'], require: ['품질 방침 및 목표 수립', '리스크 기반 사고 적용', '문서화된 프로세스 관리', '내부심사 및 경영검토'] } },
        { cat: 'mgmt', num: 'ISO 14001:2015', name: '환경경영시스템', desc: '환경 영향을 체계적으로 관리하고 지속적으로 개선하기 위한 국제 표준입니다.', tags: ['제조업', '건설업', 'ESG 필수'], detail: { who: ['제조업·건설업 등 환경 영향이 큰 기업', 'ESG 경영 도입 기업', '환경 규제 대응이 필요한 기업'], benefit: ['환경 리스크 사전 예방', '환경 규제 컴플라이언스', 'ESG 평가 점수 향상', '에너지·폐기물 비용 절감'], require: ['환경 측면(Aspects) 파악', '법규 준수 평가', '환경 목표 및 실행 계획', '비상사태 대비 절차'] } },
        { cat: 'mgmt', num: 'ISO 45001:2018', name: '안전보건경영시스템', desc: '근로자의 안전과 건강을 보호하기 위한 국제 표준. 중대재해처벌법 대응에 필수적입니다.', tags: ['제조업', '건설업', '중대재해법'], detail: { who: ['제조업·건설업 등 안전 리스크가 높은 기업', '중대재해처벌법 적용 대상 기업', '50인 이상 사업장'], benefit: ['산업재해 예방 및 감소', '중대재해처벌법 대응', '근로자 건강 보호', '보험료 절감 효과'], require: ['위험성 평가 체계 구축', '안전보건 목표 설정', '근로자 참여 및 협의', '비상사태 대응 절차'] } },
        { cat: 'mgmt', num: 'ISO 50001:2018', name: '에너지경영시스템', desc: '에너지 성과를 체계적으로 관리하고 에너지 효율을 개선하기 위한 국제 표준입니다.', tags: ['에너지 다소비', '제조업', '비용절감'], detail: { who: ['에너지 다소비 사업장', '제조업·플랜트 기업', '에너지 비용 절감이 필요한 기업'], benefit: ['에너지 비용 10~20% 절감', '탄소배출 감소', '에너지 관리 체계화', '정부 인센티브 활용'], require: ['에너지 베이스라인 설정', '에너지 성과지표(EnPI) 관리', '에너지 목표 및 실행계획', '모니터링·측정 체계'] } },
        { cat: 'esg', num: 'ISO 14064-1:2018', name: '온실가스 배출량 산정·보고', desc: '조직 수준의 온실가스 배출량을 정량화하고 보고하기 위한 국제 표준입니다.', tags: ['ESG', '탄소중립', '의무보고'], detail: { who: ['ESG 공시 의무 기업', '탄소배출권 거래 대상 기업', '공급망 탄소 관리가 필요한 기업'], benefit: ['온실가스 배출 현황 파악', 'ESG 공시 대응', '탄소중립 로드맵 기반', '이해관계자 신뢰 확보'], require: ['조직 경계 설정', 'Scope 1·2·3 배출원 파악', '배출량 정량화 방법론', '불확도 관리'] } },
        { cat: 'esg', num: 'ISO 14067:2018', name: '제품 탄소발자국', desc: '제품의 전 생애주기에 걸친 온실가스 배출량(탄소발자국)을 산정하는 국제 표준입니다.', tags: ['제품 LCA', '탄소라벨링', '수출'], detail: { who: ['EU 수출 기업(CBAM 대응)', '소비재 제조 기업', '탄소라벨링 도입 기업'], benefit: ['제품별 탄소 배출 파악', 'EU CBAM 대응', '친환경 마케팅 근거', '공급망 탄소 관리'], require: ['제품 시스템 경계 설정', '전과정 목록분석(LCI)', '탄소발자국 정량화', '데이터 품질 관리'] } },
        { cat: 'esg', num: 'ISO 14068-1:2023', name: '탄소중립', desc: '조직 또는 제품의 탄소중립 달성을 위한 요구사항과 원칙을 규정한 최신 국제 표준입니다.', tags: ['탄소중립', '넷제로', '최신 표준'], detail: { who: ['탄소중립 선언 기업', 'RE100 참여 기업', 'ESG 리더십을 추구하는 기업'], benefit: ['탄소중립 체계적 이행', '그린워싱 방지', '이해관계자 신뢰', '글로벌 기후 대응 리더십'], require: ['탄소발자국 산정 선행', '감축 우선 원칙 적용', '잔여 배출량 상쇄 계획', '투명한 커뮤니케이션'] } },
        { cat: 'security', num: 'ISO/IEC 27001:2022', name: '정보보안경영시스템', desc: '정보자산을 체계적으로 보호하기 위한 국제 표준. IT기업과 데이터를 다루는 모든 조직에 필수입니다.', tags: ['IT', '금융', '개인정보'], detail: { who: ['IT·소프트웨어 기업', '금융·핀테크 기업', '개인정보 처리 기업', '공공시스템 운영 기업'], benefit: ['정보보안 사고 예방', '고객 신뢰 확보', '법규 컴플라이언스', '글로벌 비즈니스 기반'], require: ['정보자산 식별 및 분류', '위험 평가 및 처리', '보안 통제 93개 항목 적용', '사고 대응 절차 수립'] } },
        { cat: 'security', num: 'ISO/IEC 27701:2019', name: '개인정보경영시스템', desc: 'GDPR, 개인정보보호법 등 개인정보 보호 규제에 체계적으로 대응하기 위한 국제 표준입니다.', tags: ['GDPR', '개인정보보호법', '필수'], detail: { who: ['개인정보 처리 기업', 'EU 시장 진출 기업', '클라우드·SaaS 서비스 기업'], benefit: ['GDPR·개인정보보호법 대응', '개인정보 침해 사고 예방', '글로벌 프라이버시 신뢰', 'DPO 역할 체계화'], require: ['ISO 27001 선행 취득', '개인정보 처리 목적 문서화', '정보주체 권리 보장 절차', '개인정보 영향평가'] } },
        { cat: 'security', num: 'ISO/IEC 42001:2023', name: 'AI 경영시스템', desc: 'AI 시스템의 책임감 있는 개발·운영을 위한 세계 최초의 AI 거버넌스 국제 표준입니다.', tags: ['AI', '거버넌스', '최신 표준'], detail: { who: ['AI 서비스 개발·운영 기업', 'AI 기반 의사결정 시스템 운영 기업', 'EU AI Act 대응이 필요한 기업'], benefit: ['AI 리스크 체계적 관리', 'EU AI Act 대응', 'AI 신뢰성·투명성 확보', '글로벌 AI 거버넌스 리더십'], require: ['AI 시스템 영향 평가', 'AI 리스크 관리 체계', '데이터 거버넌스', 'AI 윤리 원칙 수립'] } },
        { cat: 'supply', num: 'ISO 22301:2019', name: '비즈니스 연속성 관리', desc: '재해·위기 상황에서도 핵심 업무를 지속할 수 있도록 하는 국제 표준입니다.', tags: ['위기관리', 'BCP', '필수'], detail: { who: ['핵심 인프라 운영 기업', '금융·IT 서비스 기업', '글로벌 공급망 참여 기업'], benefit: ['위기 상황 대응력 강화', '업무 중단 최소화', '이해관계자 신뢰 확보', '보험료 절감'], require: ['비즈니스 영향 분석(BIA)', '복구 전략 수립', 'BCP 계획 문서화', '정기 훈련 및 테스트'] } },
        { cat: 'supply', num: 'ISO 31000:2018', name: '리스크 관리', desc: '모든 유형의 리스크를 체계적으로 식별, 분석, 평가, 대응하기 위한 국제 가이드라인입니다.', tags: ['전 산업', '가이드라인', '경영전략'], detail: { who: ['리스크 관리 체계를 구축하려는 기업', '경영 의사결정 체계 개선을 원하는 기업'], benefit: ['의사결정 품질 향상', '리스크 사전 예방', '이해관계자 신뢰 확보', '경영 성과 개선'], require: ['리스크 관리 프레임워크 설계', '리스크 식별·분석·평가', '리스크 대응 계획', '모니터링·검토 체계'] } },
        { cat: 'supply', num: 'ISO 37301:2021', name: '컴플라이언스 경영시스템', desc: '법규·규제·윤리적 요구사항을 체계적으로 준수하기 위한 국제 표준입니다.', tags: ['법규준수', '컴플라이언스', '윤리경영'], detail: { who: ['법규 위반 리스크가 높은 기업', '글로벌 컴플라이언스 요구 대응 기업', 'ESG 거버넌스 강화 기업'], benefit: ['법적 리스크 감소', '과징금·벌금 예방', '기업 이미지 제고', 'ESG 거버넌스 점수 향상'], require: ['컴플라이언스 의무 식별', '컴플라이언스 리스크 평가', '교육 및 인식 프로그램', '내부 신고 체계'] } },
        { cat: 'industry', num: 'ISO 13485:2016', name: '의료기기 품질경영', desc: '의료기기의 설계·개발·생산·설치·서비스에 대한 품질경영 요구사항을 규정한 국제 표준입니다.', tags: ['의료기기', '필수인증', '규제대응'], detail: { who: ['의료기기 제조·판매 기업', '의료기기 부품 공급 기업', '해외 의료기기 수출 기업'], benefit: ['의료기기 인허가 기반', '글로벌 시장 진출 필수', '제품 안전성·유효성 확보', '규제 대응 체계화'], require: ['설계·개발 관리 절차', '추적성(Traceability) 확보', '멸균 프로세스 밸리데이션', '위험관리(ISO 14971) 연계'] } },
        { cat: 'industry', num: 'ISO 22000:2018', name: '식품안전경영시스템', desc: '식품 공급망 전체의 식품 안전을 보장하기 위한 국제 표준입니다.', tags: ['식품', 'HACCP', '식품안전'], detail: { who: ['식품 제조·가공 기업', '식품 유통·물류 기업', '외식·급식 기업'], benefit: ['식품 안전사고 예방', '소비자 신뢰 확보', '수출 시장 요구 충족', 'HACCP과 통합 운영'], require: ['HACCP 원칙 적용', '선행요건 프로그램(PRP)', '식품안전팀 구성', '추적성 시스템'] } },
        { cat: 'industry', num: 'IATF 16949:2016', name: '자동차 품질경영', desc: '자동차 산업 공급망을 위한 품질경영시스템 표준. 완성차 OEM 납품에 필수입니다.', tags: ['자동차', 'OEM납품', '필수'], detail: { who: ['자동차 부품 제조 기업', '완성차 OEM 1·2차 협력사', '자동차 전장 부품 기업'], benefit: ['OEM 납품 필수 조건', '불량률 체계적 감소', '공급망 신뢰 확보', '글로벌 자동차 시장 진출'], require: ['ISO 9001 베이스', 'APQP·PPAP·FMEA·SPC·MSA', '고객 특수 요구사항 관리', '제품 안전 관리'] } },
        { cat: 'hr', num: 'ISO 37001:2016', name: '부패방지경영시스템', desc: '조직 내 부패를 예방·탐지·대응하기 위한 국제 표준입니다.', tags: ['부패방지', '윤리경영', 'ESG'], detail: { who: ['공공기관 거래 기업', '해외 사업 영위 기업', 'ESG 거버넌스 강화 기업'], benefit: ['부패 리스크 감소', '법적 제재 방지', '기업 이미지 제고', 'ESG G(거버넌스) 점수 향상'], require: ['부패방지 방침 수립', '부패 리스크 평가', '실사(Due Diligence)', '내부 신고 제도'] } },
        { cat: 'hr', num: 'ISO 30414:2018', name: '인적자본 보고', desc: '조직의 인적자본 가치를 정량화하고 투명하게 보고하기 위한 국제 가이드라인입니다.', tags: ['HR', 'ESG사회', '인적자본공시'], detail: { who: ['ESG 공시 의무 기업', '인적자본 경쟁력 강화 기업', '상장기업'], benefit: ['인적자본 가치 가시화', 'ESG S(사회) 점수 향상', '투자자 신뢰 확보', 'HR 전략 데이터 기반 의사결정'], require: ['인적자본 11개 영역 측정', '인력 구성 다양성 데이터', '이직률·생산성 지표', '리더십·조직문화 평가'] } }
    ];

    const catLabels = { mgmt: '경영시스템', esg: 'ESG·탄소', security: '정보보안', supply: '공급망·리스크', industry: '산업특화', hr: '인사·윤리' };

    const MOBILE_LIMIT = 6;
    let currentFiltered = [];
    let mobileExpanded = false;

    function updateShowMoreBtn() {
        const wrap = document.getElementById('iso-show-more-wrap');
        const countEl = document.getElementById('iso-show-more-count');
        if (!wrap) return;
        const isMobile = window.innerWidth < 768;
        const remaining = currentFiltered.length - MOBILE_LIMIT;
        if (isMobile && remaining > 0 && !mobileExpanded) {
            wrap.style.display = 'flex';
            if (countEl) countEl.textContent = `(${remaining}개 더)`;
        } else {
            wrap.style.display = 'none';
        }
    }

    window._isoShowMore = function () {
        mobileExpanded = true;
        renderIsoCatalog(null, true);
    };

    function renderIsoCatalog(filter, keepExpanded) {
        const grid = document.getElementById('iso-cards-grid');
        if (!grid) return;
        if (filter !== null) {
            currentFiltered = filter === 'all' || !filter ? isoStandards : isoStandards.filter(s => s.cat === filter);
            mobileExpanded = !!keepExpanded;
        }
        const isMobile = window.innerWidth < 768;
        const filtered = (isMobile && !mobileExpanded)
            ? currentFiltered.slice(0, MOBILE_LIMIT)
            : currentFiltered;

        grid.innerHTML = filtered.map((s, i) => `
            <div class="iso-std-card fade-in-up delay-${(i % 4) + 1}" data-cat="${s.cat}" onclick="window._toggleIsoCard(this)">
                <div class="iso-std-header">
                    <span class="iso-std-badge badge-cat-${s.cat}">${catLabels[s.cat]}</span>
                    <div class="iso-std-number">${s.num}</div>
                    <div class="iso-std-name">${s.name}</div>
                    <div class="iso-std-desc">${s.desc}</div>
                </div>
                <div class="iso-std-tags">${s.tags.map(t => `<span class="iso-std-tag">${t}</span>`).join('')}</div>
                <a href="#diagnosis" class="iso-card-mini-cta" onclick="event.stopPropagation();">
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:14px;height:14px;"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><line x1="19" x2="19" y1="8" y2="14"/><line x1="22" x2="16" y1="11" y2="11"/></svg>
                    컨설턴트 소개받기
                </a>
                <div class="iso-std-toggle">상세 정보 보기 <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg></div>
                <div class="iso-std-detail">
                    <div class="iso-detail-grid">
                        <div class="iso-detail-box"><h4>🎯 누가 취득해야 하나요?</h4><ul>${s.detail.who.map(w => `<li>${w}</li>`).join('')}</ul></div>
                        <div class="iso-detail-box"><h4>✅ 취득 효과</h4><ul>${s.detail.benefit.map(b => `<li>${b}</li>`).join('')}</ul></div>
                        <div class="iso-detail-box"><h4>📋 주요 요구사항</h4><ul>${s.detail.require.map(r => `<li>${r}</li>`).join('')}</ul></div>
                    </div>
                    <div class="iso-detail-cta">
                        <p>이 인증에 대해 더 자세한 상담이 필요하신가요? ISO 컨설턴트를 무료로 소개해 드립니다.</p>
                        <a href="#diagnosis" class="btn btn-primary">${s.num.split(':')[0]} 컨설턴트 소개받기</a>
                    </div>
                </div>
            </div>
        `).join('');

        // Re-trigger fade-in animations
        requestAnimationFrame(() => {
            grid.querySelectorAll('.fade-in-up').forEach(el => {
                el.style.opacity = '0';
                el.style.transform = 'translateY(20px)';
                requestAnimationFrame(() => {
                    el.style.transition = 'opacity 0.6s ease, transform 0.6s ease';
                    el.style.opacity = '1';
                    el.style.transform = 'translateY(0)';
                });
            });
        });

        updateShowMoreBtn();
    }

    // Toggle card expand/collapse
    window._toggleIsoCard = function (card) {
        const wasExpanded = card.classList.contains('expanded');
        document.querySelectorAll('.iso-std-card.expanded').forEach(c => c.classList.remove('expanded'));
        if (!wasExpanded) {
            card.classList.add('expanded');
            setTimeout(() => card.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 100);
        }
    };

    // Filter button handlers
    document.querySelectorAll('.iso-filter-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.iso-filter-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            mobileExpanded = false;
            renderIsoCatalog(btn.dataset.filter);
        });
    });

    // Re-evaluate on resize
    window.addEventListener('resize', updateShowMoreBtn);

    // Initial render
    if (document.getElementById('iso-cards-grid')) {
        renderIsoCatalog('all');
    }
})();
