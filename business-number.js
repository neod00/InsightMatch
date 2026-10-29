/*
 * 기업 사업자등록번호 입력 창 (견적 요청·AI 매뉴얼 생성 전에 필수).
 *
 * 서버가 400 + code 'BUSINESS_NUMBER_REQUIRED' 를 돌려주면, 이 창으로 번호를 받아
 * PUT /api/me/business-number (국세청 상태조회) 에 저장한 뒤 원래 요청을 다시 보낸다.
 *
 *   let res = await send();
 *   if (await InsightBiz.handle(res, { apiBase: API_BASE_URL })) res = await send();
 *
 * 어느 페이지(새 디자인·옛 디자인)에서도 쓰도록 스타일을 스스로 넣는다. 의존성 없음.
 */
(function () {
    'use strict';

    const STYLE_ID = 'imbiz-style';
    const STYLE = `
.imbiz-overlay{position:fixed;inset:0;z-index:10000;display:flex;align-items:center;justify-content:center;padding:16px;background:rgba(14,15,12,.55)}
.imbiz-dialog{width:100%;max-width:440px;border-radius:20px;background:#fff;color:#0E0F0C;padding:28px 24px 22px;box-shadow:0 24px 60px rgba(14,15,12,.25);font-family:inherit}
.imbiz-title{margin:0 0 8px;font-size:20px;font-weight:700;letter-spacing:-.01em}
.imbiz-desc{margin:0 0 18px;font-size:14px;line-height:1.6;color:#4a4b45}
.imbiz-label{display:block;margin-bottom:6px;font-size:13px;font-weight:600}
.imbiz-input{box-sizing:border-box;width:100%;height:48px;padding:0 14px;border:1px solid #d9d9d2;border-radius:12px;font-size:16px;font-family:inherit;letter-spacing:.04em;background:#FAFAF7;color:#0E0F0C}
.imbiz-input:focus{outline:2px solid #0E0F0C;outline-offset:1px;background:#fff}
.imbiz-error{min-height:20px;margin:8px 0 0;font-size:13px;line-height:1.5;color:#b42318}
.imbiz-note{margin:6px 0 0;font-size:12px;line-height:1.5;color:#6b6c66}
.imbiz-actions{display:flex;gap:8px;margin-top:18px}
.imbiz-btn{flex:1;height:46px;border:0;border-radius:999px;font-size:15px;font-weight:600;font-family:inherit;cursor:pointer}
.imbiz-btn[disabled]{opacity:.6;cursor:default}
.imbiz-cancel{background:#EFEFE9;color:#0E0F0C}
.imbiz-ok{background:#C3FB79;color:#0E0F0C}
`;

    function injectStyle() {
        if (document.getElementById(STYLE_ID)) return;
        const style = document.createElement('style');
        style.id = STYLE_ID;
        style.textContent = STYLE;
        document.head.appendChild(style);
    }

    function formatNumber(value) {
        const d = String(value || '').replace(/\D/g, '').slice(0, 10);
        if (d.length <= 3) return d;
        if (d.length <= 5) return `${d.slice(0, 3)}-${d.slice(3)}`;
        return `${d.slice(0, 3)}-${d.slice(3, 5)}-${d.slice(5)}`;
    }

    function rememberOnUser(info) {
        try {
            const user = JSON.parse(localStorage.getItem('user') || 'null');
            if (!user) return;
            user.biz_reg_no = info.biz_reg_no || '';
            user.biz_status = info.biz_status || '';
            localStorage.setItem('user', JSON.stringify(user));
        } catch (e) { /* 저장소 접근 불가 — 다음 로그인 때 다시 받는다 */ }
    }

    /** 입력 창을 연다. 저장에 성공하면 true, 취소하면 false. */
    function open(options) {
        const opts = options || {};
        const apiBase = opts.apiBase || '';
        injectStyle();

        return new Promise((resolve) => {
            const previousFocus = document.activeElement;
            const overlay = document.createElement('div');
            overlay.className = 'imbiz-overlay';
            overlay.innerHTML = `
                <div class="imbiz-dialog" role="dialog" aria-modal="true" aria-labelledby="imbiz-title">
                    <h2 class="imbiz-title" id="imbiz-title">사업자등록번호 확인</h2>
                    <p class="imbiz-desc"></p>
                    <label class="imbiz-label" for="imbiz-input">사업자등록번호</label>
                    <input class="imbiz-input" id="imbiz-input" inputmode="numeric" autocomplete="off" placeholder="000-00-00000">
                    <p class="imbiz-note">국세청에 번호만 조회해 영업 중인 사업자인지 확인합니다. 처음 한 번만 입력하면 됩니다.</p>
                    <p class="imbiz-error" role="alert"></p>
                    <div class="imbiz-actions">
                        <button type="button" class="imbiz-btn imbiz-cancel">취소</button>
                        <button type="button" class="imbiz-btn imbiz-ok">확인</button>
                    </div>
                </div>`;
            overlay.querySelector('.imbiz-desc').textContent = opts.message
                || '견적 요청과 AI 매뉴얼 생성은 사업자 확인을 마친 기업만 이용할 수 있습니다.';
            const input = overlay.querySelector('.imbiz-input');
            const error = overlay.querySelector('.imbiz-error');
            const okBtn = overlay.querySelector('.imbiz-ok');
            const cancelBtn = overlay.querySelector('.imbiz-cancel');

            function close(result) {
                document.removeEventListener('keydown', onKey);
                overlay.remove();
                if (previousFocus && previousFocus.focus) previousFocus.focus();
                resolve(result);
            }
            function onKey(e) {
                if (e.key === 'Escape') close(false);
                if (e.key === 'Enter' && document.activeElement === input) submit();
            }
            async function submit() {
                const digits = input.value.replace(/\D/g, '');
                if (digits.length !== 10) {
                    error.textContent = '사업자등록번호 10자리를 입력해 주세요.';
                    input.focus();
                    return;
                }
                okBtn.disabled = true;
                okBtn.textContent = '확인 중...';
                error.textContent = '';
                try {
                    const token = localStorage.getItem('token');
                    const res = await fetch(`${apiBase}/api/me/business-number`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
                        body: JSON.stringify({ biz_reg_no: digits })
                    });
                    const data = await res.json().catch(() => ({}));
                    if (res.ok) {
                        rememberOnUser(data);
                        close(true);
                        return;
                    }
                    error.textContent = data.message || '사업자등록번호를 확인하지 못했습니다.';
                } catch (e) {
                    error.textContent = '네트워크 오류로 확인하지 못했습니다. 잠시 후 다시 시도해 주세요.';
                }
                okBtn.disabled = false;
                okBtn.textContent = '확인';
            }

            input.addEventListener('input', () => { input.value = formatNumber(input.value); });
            okBtn.addEventListener('click', submit);
            cancelBtn.addEventListener('click', () => close(false));
            overlay.addEventListener('click', (e) => { if (e.target === overlay) close(false); });
            document.addEventListener('keydown', onKey);
            document.body.appendChild(overlay);
            input.focus();
        });
    }

    /** 응답이 '사업자등록번호 필요'면 입력 창을 띄운다. 저장했으면 true(원래 요청을 다시 보내면 된다). */
    async function handle(response, options) {
        if (!response || response.status !== 400) return false;
        const data = await response.clone().json().catch(() => null);
        if (!data || data.code !== 'BUSINESS_NUMBER_REQUIRED') return false;
        return open({ ...(options || {}), message: data.message });
    }

    window.InsightBiz = { open, handle, formatNumber };
})();
