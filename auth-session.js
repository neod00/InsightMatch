/*
 * 세션 만료 공용 헬퍼 (consultant_register.html · ai-manual.html 에서 쓴다).
 *
 * admin.html · dashboard.html 에 있는 redirectToLoginWithNotice() 와 같은 규칙이다.
 *   - token·user 를 함께 지운다 (user 만 남으면 다른 페이지가 '로그인된 것처럼' 그리다 401 을 만난다)
 *   - sessionStorage.auth_notice 에 안내 문구를 남긴다 (login.html 이 한 번만 보여 준다)
 *   - login.html?redirect=<지금 페이지>&role=<역할> 로 보낸다
 *     역할은 token·user 를 지우기 '전에' 읽는다. login.html 은 이 값으로 해당 탭을 연다.
 *
 * 입력을 지켜야 하는 페이지는 beforeRedirect 에서 임시 저장을 먼저 한다.
 *
 *   InsightAuth.redirectToLogin({ beforeRedirect: (role) => saveDraft() });
 *
 * 의존성 없음.
 */
(function () {
    'use strict';

    const NOTICE_KEY = 'auth_notice';
    const SESSION_EXPIRED_MESSAGE = '세션이 만료되었습니다. 다시 로그인해주세요.';
    // login.html 이 받는 역할 힌트와 같은 목록 (그 밖의 값은 보내지 않는다)
    const ROLES = ['company', 'consultant', 'admin'];
    let redirecting = false;

    // JWT payload 디코드 (서명 검증은 서버 몫 — 여기서는 만료 사전 확인용)
    function decodeJwtPayload(rawToken) {
        try {
            const part = String(rawToken).split('.')[1];
            if (!part) return null;
            const base64 = part.replace(/-/g, '+').replace(/_/g, '/')
                .padEnd(Math.ceil(part.length / 4) * 4, '=');
            return JSON.parse(atob(base64));
        } catch (e) {
            return null;
        }
    }

    /** 깨졌거나 exp 가 지난 토큰이면 false. exp 가 없으면 서버 판단에 맡긴다(true). */
    function isTokenUsable(rawToken) {
        const payload = decodeJwtPayload(rawToken);
        if (!payload) return false;
        if (typeof payload.exp !== 'number') return true;
        return payload.exp * 1000 > Date.now();
    }

    function getToken() {
        try { return localStorage.getItem('token'); } catch (e) { return null; }
    }

    function readUser() {
        try { return JSON.parse(localStorage.getItem('user') || 'null'); } catch (e) { return null; }
    }

    /** 저장된 사용자 역할 (화이트리스트 밖이면 '') */
    function currentRole() {
        const user = readUser();
        return user && ROLES.includes(user.role) ? user.role : '';
    }

    /** login.html 주소. redirect 는 같은 사이트 상대 경로(예: 'ai-manual.html'). */
    function loginUrl(redirect, role) {
        const params = new URLSearchParams();
        if (redirect) params.set('redirect', redirect);
        if (ROLES.includes(role)) params.set('role', role);
        const query = params.toString();
        return 'login.html' + (query ? `?${query}` : '');
    }

    // 지금 페이지 (쿼리 포함). 예: consultant_register.html?invite=abc
    function currentPage() {
        const name = window.location.pathname.split('/').pop() || 'index.html';
        return name + window.location.search;
    }

    /** token·user 를 지우고, 지우기 전의 역할을 돌려준다. */
    function clearSession() {
        const role = currentRole();
        try {
            localStorage.removeItem('token');
            localStorage.removeItem('user');
        } catch (e) { /* 저장소 접근 불가 환경 */ }
        return role;
    }

    /**
     * 세션 만료 안내와 함께 로그인 화면으로 보낸다. 동시에 여러 요청이 401 을 받아도 한 번만 이동한다.
     * options: { redirect, message, role, beforeRedirect(role) }
     *   role: 이미 세션을 지운 뒤라면 그 전에 기억해 둔 역할을 넘긴다 (없으면 저장된 사용자에서 읽는다)
     */
    function redirectToLogin(options) {
        const opts = options || {};
        if (redirecting) return false;
        redirecting = true;
        const role = typeof opts.role === 'string' && opts.role ? opts.role : currentRole();  // 지우기 전에 읽는다
        if (typeof opts.beforeRedirect === 'function') {
            try { opts.beforeRedirect(role); } catch (e) { console.warn('[Auth] beforeRedirect 실패:', e); }
        }
        clearSession();
        try { sessionStorage.setItem(NOTICE_KEY, opts.message || SESSION_EXPIRED_MESSAGE); } catch (e) { /* 저장 불가 환경 */ }
        window.location.href = loginUrl(opts.redirect || currentPage(), role);
        return true;
    }

    function isRedirecting() {
        return redirecting;
    }

    window.InsightAuth = {
        SESSION_EXPIRED_MESSAGE,
        decodeJwtPayload,
        isTokenUsable,
        getToken,
        readUser,
        currentRole,
        loginUrl,
        currentPage,
        clearSession,
        redirectToLogin,
        isRedirecting
    };
})();
