"""
Advanced Diagnostic AI Service (독립형 정밀 진단 엔진)
=====================================================
- 정밀 타겟 DB(JSON) + AI(Gemini) Gap Analysis 결합 모델
- 질문 기반 자기진단 → AI 분석 → 팩트 기반 Gap Report 생성
- KSIC 코드별 독립 JSON DB를 자동 로드하여 무한 확장 가능

사용법:
    service = AdvancedDiagnosticService()
    questions = service.get_diagnostic_questions("C30")
    report = service.generate_gap_report("C30", user_answers, user_context)
"""

import os
import json
import glob
import google.generativeai as genai


class AdvancedDiagnosticService:
    """
    정밀 타겟 DB 기반 질문형 자기진단 + AI Gap Analysis 엔진.
    
    핵심 설계 원칙:
    1. AI는 새로운 리스크를 '생성(Generate)'하지 않는다.
    2. AI는 DB에 존재하는 리스크를 고객의 응답에 '매칭(Map)'하고
       그 인과관계를 '해설(Explain)'만 한다.
    3. 고객은 자신의 답변을 통해 스스로 Gap을 '발견(Discover)'한다.
    """

    def __init__(self):
        # DB 저장소 경로 설정
        self.db_base_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            'data', 'risk_dbs'
        )
        
        # Gemini AI 모델 초기화
        api_key = os.environ.get('GOOGLE_API_KEY')
        if api_key:
            genai.configure(api_key=api_key)
            # 속도 우선 순서: 2.5-flash(현재 quota 가용) → 2.0-flash → lite
            self.model_names = ['gemini-2.5-flash', 'gemini-2.0-flash', 'gemini-2.0-flash-lite']
            self.generation_config = genai.types.GenerationConfig(
                max_output_tokens=1024,   # 응답 길이 제한 (빠른 생성)
                temperature=0.3,          # 낮은 온도 = 빠르고 일관된 응답
            )
            self.primary_model = genai.GenerativeModel(self.model_names[0])
            print(f"[AdvancedDiagnostic] AI Engine initialized: {self.model_names[0]}")
        else:
            self.primary_model = None
            self.model_names = []
            self.generation_config = None
            print("[AdvancedDiagnostic] Warning: GOOGLE_API_KEY not found.")
        
        # 사용 가능한 산업 코드 목록 캐시
        self._available_codes = None
        print(f"[AdvancedDiagnostic] DB path: {self.db_base_path}")
    
    # =========================================================================
    # 1. DB 관리 메서드
    # =========================================================================
    
    def get_available_industries(self):
        """사용 가능한 산업코드(KSIC) 목록을 반환합니다."""
        if self._available_codes is None:
            self._available_codes = []
            if os.path.exists(self.db_base_path):
                for filepath in glob.glob(os.path.join(self.db_base_path, '*.json')):
                    try:
                        with open(filepath, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        self._available_codes.append({
                            'code': data.get('industry_code', ''),
                            'name': data.get('industry_name', ''),
                            'description': data.get('description', ''),
                            'total_risks': len(data.get('risks', [])),
                            'teaser_count': sum(1 for r in data.get('risks', []) if r.get('is_teaser')),
                            'hidden_count': sum(1 for r in data.get('risks', []) if not r.get('is_teaser'))
                        })
                    except (json.JSONDecodeError, KeyError) as e:
                        print(f"[AdvancedDiagnostic] Error loading {filepath}: {e}")
        return self._available_codes
    
    def _load_industry_db(self, ksic_code):
        """특정 KSIC 코드에 해당하는 리스크 DB를 로드합니다."""
        filepath = os.path.join(self.db_base_path, f'{ksic_code}.json')
        if not os.path.exists(filepath):
            raise FileNotFoundError(
                f"KSIC 코드 '{ksic_code}'에 대한 리스크 DB가 아직 구축되지 않았습니다. "
                f"사용 가능: {[i['code'] for i in self.get_available_industries()]}"
            )
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    # =========================================================================
    # 2. 진단 질문 제공 (Phase 1: 질문지 생성)
    # =========================================================================
    
    def get_diagnostic_questions(self, ksic_code, main_process=None):
        """
        특정 업종에 대한 자기진단 질문지를 생성하여 반환합니다.
        main_process가 지정되면 해당 공정과 관련된 질문만 필터링합니다.
        """
        db = self._load_industry_db(ksic_code)
        all_risks = db.get('risks', [])
        
        # 공정 필터 적용: 해당 공정과 관련된 리스크만 추출
        if main_process:
            relevant_risks = [
                r for r in all_risks
                if 'all' in r.get('relevant_processes', []) or main_process in r.get('relevant_processes', [])
            ]
        else:
            relevant_risks = all_risks
        
        # Teaser 항목 (공개 진단 질문)
        teaser_items = [r for r in relevant_risks if r.get('is_teaser')]
        # Non-teaser 항목 중 해당 공정에 특화된 추가 질문 (공정 선택 시에만)
        bonus_items = []
        if main_process:
            bonus_items = [
                r for r in relevant_risks
                if not r.get('is_teaser') 
                and main_process in r.get('relevant_processes', [])
                and 'all' not in r.get('relevant_processes', [])
            ]
        
        diagnostic_items = []
        for risk in teaser_items + bonus_items:
            diagnostic_items.append({
                'id': risk['id'],
                'category': risk.get('category', ''),
                'iso_type': risk.get('iso_type', ''),
                'question': risk.get('diagnostic_question', ''),
                'severity': risk.get('severity', 'Medium'),
                'relevant_processes': risk.get('relevant_processes', ['all']),
                'is_bonus': not risk.get('is_teaser', False),
            })
        
        # 숨겨진 리스크 수 (진단 질문에 포함되지 않은 것들)
        shown_ids = {item['id'] for item in diagnostic_items}
        hidden_count = sum(1 for r in relevant_risks if r['id'] not in shown_ids)
        
        return {
            'industry_code': db.get('industry_code'),
            'industry_name': db.get('industry_name'),
            'description': db.get('description'),
            'context_questions': db.get('context_questions', []),
            'diagnostic_items': diagnostic_items,
            'total_diagnostic_count': len(diagnostic_items),
            'total_hidden_risks': hidden_count,
            'total_categories': len(set(r.get('category', '') for r in relevant_risks)),
            'filtered_by_process': main_process
        }
    
    # =========================================================================
    # 3. Gap 분석 리포트 생성 (Phase 2: AI 분석)
    # =========================================================================
    
    def generate_gap_report(self, ksic_code, user_answers, user_context=None, full_report=False):
        """
        사용자의 자기진단 응답을 기반으로 AI Gap Analysis 리포트를 생성합니다.
        
        Args:
            ksic_code (str): 산업코드 (예: "C30")
            user_answers (list): 사용자 응답 리스트
                [{"risk_id": "C30-Q-01", "answer": "no"}, ...]
                answer: "yes" | "no" | "unsure"
            user_context (dict): 맥락 정보
                {"main_process": "프레스/스탬핑", "employee_count": "50~299명"}
            full_report (bool): True이면 숨겨진 리스크 상세 정보를 모두 공개
        
        Returns:
            dict: Gap Analysis 결과
        """
        db = self._load_industry_db(ksic_code)
        all_risks = {r['id']: r for r in db.get('risks', [])}
        
        if user_context is None:
            user_context = {}
        
        # ----------------------------------------------------------
        # Phase 2-1: 사용자 응답 기반 Gap 식별 (규칙 기반, AI 불필요)
        # ----------------------------------------------------------
        identified_gaps = []
        compliant_items = []
        uncertain_items = []
        
        for ans in user_answers:
            risk_id = ans.get('risk_id')
            answer = ans.get('answer', '').lower()
            risk_data = all_risks.get(risk_id)
            
            if not risk_data:
                continue
            
            if answer == 'no':
                identified_gaps.append(risk_data)
            elif answer == 'unsure':
                uncertain_items.append(risk_data)
            else:  # 'yes'
                compliant_items.append(risk_data)
        
        # ----------------------------------------------------------
        # Phase 2-2: 공정 매칭 기반 숨겨진 리스크 필터링
        # ----------------------------------------------------------
        main_process = user_context.get('main_process', '')
        hidden_risks = [r for r in db.get('risks', []) if not r.get('is_teaser')]
        
        # 사용자의 주력 공정과 관련된 숨겨진 리스크만 필터
        relevant_hidden = []
        for risk in hidden_risks:
            procs = risk.get('relevant_processes', [])
            if 'all' in procs or main_process in procs:
                if full_report:
                    # 전체 리포트: 상세 정보 공개
                    relevant_hidden.append({
                        'id': risk['id'],
                        'category': risk.get('category', ''),
                        'iso_type': risk.get('iso_type', ''),
                        'iso_clause': risk.get('iso_clause', ''),
                        'severity': risk.get('severity', ''),
                        'risk_title': risk.get('risk_title', ''),
                        'cause': risk.get('cause', ''),
                        'consequence': risk.get('consequence', ''),
                        'audit_point': risk.get('audit_point', ''),
                        'diagnostic_question': risk.get('diagnostic_question', ''),
                        'relevant_processes': risk.get('relevant_processes', []),
                        'locked': False
                    })
                else:
                    # 기본: 잠금 상태 (미리보기만)
                    relevant_hidden.append({
                        'id': risk['id'],
                        'category': risk.get('category', ''),
                        'severity': risk.get('severity', ''),
                        'risk_title_preview': risk.get('risk_title', '')[:20] + '...',
                        'locked': True
                    })
        
        # ----------------------------------------------------------
        # Phase 2-3: AI Gap Analysis 해설 생성 (Gemini)
        # ----------------------------------------------------------
        ai_analysis = None
        if identified_gaps or uncertain_items:
            ai_analysis = self._generate_ai_gap_analysis(
                industry_name=db.get('industry_name', ''),
                user_context=user_context,
                identified_gaps=identified_gaps,
                uncertain_items=uncertain_items,
                compliant_items=compliant_items
            )
        
        # ----------------------------------------------------------
        # 최종 리포트 조립
        # ----------------------------------------------------------
        total_questions = len(user_answers)
        gap_count = len(identified_gaps)
        unsure_count = len(uncertain_items)
        compliant_count = len(compliant_items)
        
        # 리스크 점수 계산 (100점 만점, 낮을수록 위험)
        if total_questions > 0:
            compliance_score = round((compliant_count / total_questions) * 100)
        else:
            compliance_score = 0
        
        # 위험도 등급
        if compliance_score >= 80:
            risk_grade = "양호 (Low Risk)"
            risk_color = "green"
        elif compliance_score >= 50:
            risk_grade = "주의 (Moderate Risk)"
            risk_color = "orange"
        else:
            risk_grade = "위험 (High Risk)"
            risk_color = "red"
        
        report = {
            'industry_code': ksic_code,
            'industry_name': db.get('industry_name', ''),
            'user_context': user_context,
            
            # 점수 및 등급
            'compliance_score': compliance_score,
            'risk_grade': risk_grade,
            'risk_color': risk_color,
            
            # 응답 요약
            'summary': {
                'total_questions': total_questions,
                'gap_count': gap_count,
                'unsure_count': unsure_count,
                'compliant_count': compliant_count
            },
            
            # 식별된 Gap (공개 - Teaser)
            'identified_gaps': [
                {
                    'id': g['id'],
                    'category': g.get('category', ''),
                    'iso_type': g.get('iso_type', ''),
                    'iso_clause': g.get('iso_clause', ''),
                    'risk_title': g.get('risk_title', ''),
                    'cause': g.get('cause', ''),
                    'consequence': g.get('consequence', ''),
                    'audit_point': g.get('audit_point', ''),
                    'severity': g.get('severity', '')
                }
                for g in identified_gaps
            ],
            
            # 불명확 항목 (경고)
            'uncertain_items': [
                {
                    'id': u['id'],
                    'category': u.get('category', ''),
                    'risk_title': u.get('risk_title', ''),
                    'severity': u.get('severity', ''),
                    'message': "'잘 모르겠음' — 확인이 필요한 잠재적 Gap입니다."
                }
                for u in uncertain_items
            ],
            
            # 숨겨진 리스크 (잠금 - 리드 생성 훅)
            'hidden_risks': relevant_hidden,
            'hidden_risk_count': len(relevant_hidden),
            
            # AI 분석 해설 (Gemini 생성)
            'ai_analysis': ai_analysis,
            
            # CTA (Call to Action)
            'cta': {
                'message': f"지금까지 공개 진단 {total_questions}개 항목 중 {gap_count}개의 Gap이 확인되었습니다. "
                           f"귀사의 업종에는 심층 진단 항목이 {len(relevant_hidden)}개 더 존재합니다.",
                'action_label': "전체 Gap Analysis 리포트 요청",
                'urgency': 'high' if gap_count >= 3 else ('medium' if gap_count >= 1 else 'low')
            }
        }
        
        return report
    
    # =========================================================================
    # 4. AI Gap Analysis 해설 생성 (내부 메서드)
    # =========================================================================
    
    def _generate_ai_gap_analysis(self, industry_name, user_context, 
                                   identified_gaps, uncertain_items, compliant_items):
        """
        Gemini AI를 사용하여 식별된 Gap에 대한 전문가적 해설을 생성합니다.
        속도 최적화: flash-lite 우선 + 타임아웃 + 간결한 프롬프트
        """
        import time
        
        if not self.primary_model:
            return self._generate_fallback_analysis(identified_gaps, uncertain_items)
        
        # 간결한 Gap 정보 (프롬프트 최소화)
        gaps_compact = "\n".join([
            f"- [{g.get('severity')}] {g.get('risk_title', '')} (ISO: {g.get('iso_clause', '')})"
            for g in identified_gaps
        ]) or "없음"
        
        uncertain_compact = "\n".join([
            f"- {u.get('risk_title', '')} (ISO: {u.get('iso_clause', '')})"
            for u in uncertain_items
        ]) or "없음"
        
        main_process = user_context.get('main_process', '미지정')
        employee_count = user_context.get('employee_count', '미지정')
        
        # 최적화된 프롬프트 (간결 + 명확)
        prompt = f"""ISO Gap 분석 전문가로서, '{industry_name}' ({main_process}, {employee_count}) 기업의 자기진단 결과를 해설하세요.

확인된 Gap:
{gaps_compact}

불명확 항목:
{uncertain_compact}

규칙: 1) 사용자 응답 기반 해설만 작성(새 리스크 금지) 2) 중립적 심사원 어조 3) 한글로 작성

JSON으로만 응답:
{{"gap_analysis_summary":"3~5문장 요약","gap_details":[{{"risk_id":"ID","expert_comment":"2~3문장 해설"}}],"uncertain_note":"불명확 코멘트 또는 빈 문자열","top_priority":{{"risk_id":"최시급 ID","reason":"한 문장 이유"}}}}"""

        try:
            response_text = None
            used_model = self.model_names[0]
            total_start = time.time()
            
            for model_name in self.model_names:
                try:
                    start_t = time.time()
                    print(f"[AdvancedDiagnostic] Attempting AI analysis with {model_name}...")
                    temp_model = genai.GenerativeModel(model_name)
                    
                    # generation_config + request_options(타임아웃) 적용
                    response = temp_model.generate_content(
                        prompt,
                        generation_config=self.generation_config,
                        request_options={"timeout": 20}  # 20초 타임아웃
                    )
                    response_text = response.text
                    used_model = model_name
                    elapsed = round(time.time() - start_t, 1)
                    print(f"[AdvancedDiagnostic] ✓ AI analysis succeeded with {model_name} ({elapsed}s)")
                    break
                except Exception as api_err:
                    elapsed = round(time.time() - start_t, 1)
                    err_msg = str(api_err)
                    if "429" in err_msg or "quota" in err_msg.lower():
                        print(f"[AdvancedDiagnostic] ⚠ {model_name} quota exceeded ({elapsed}s). Trying next...")
                        continue
                    elif "timeout" in err_msg.lower() or "deadline" in err_msg.lower():
                        print(f"[AdvancedDiagnostic] ⏱ {model_name} timed out ({elapsed}s). Trying next...")
                        continue
                    else:
                        print(f"[AdvancedDiagnostic] ✗ {model_name} failed ({elapsed}s): {err_msg}")
                        raise api_err
            
            total_elapsed = round(time.time() - total_start, 1)
            
            if not response_text:
                print(f"[AdvancedDiagnostic] All models failed ({total_elapsed}s). Using fallback.")
                return self._generate_fallback_analysis(identified_gaps, uncertain_items)
            
            print(f"[AdvancedDiagnostic] Total AI analysis time: {total_elapsed}s")
            
            # JSON 파싱
            text = response_text.strip()
            if text.startswith("```json"):
                text = text[7:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
            if not text.startswith("{") and "{" in text:
                text = text[text.find("{"):]
            if not text.endswith("}") and "}" in text:
                text = text[:text.rfind("}") + 1]
            
            result = json.loads(text)
            result['ai_model_used'] = used_model
            result['source'] = 'gemini'
            result['response_time_seconds'] = total_elapsed
            return result
            
        except Exception as e:
            print(f"[AdvancedDiagnostic] AI analysis error: {e}")
            return self._generate_fallback_analysis(identified_gaps, uncertain_items)
    
    def _generate_fallback_analysis(self, identified_gaps, uncertain_items):
        """DB 기반 규칙형 전문가 분석. AI 없이도 핵심 인사이트를 제공합니다."""
        gap_details = []
        for gap in identified_gaps:
            # 심각도별 맥락화된 코멘트 생성
            severity = gap.get('severity', '')
            if severity == 'Critical':
                urgency = "이 항목은 즉각적인 시정이 필요한 'Critical' 등급 리스크입니다."
            elif severity == 'High':
                urgency = "이 항목은 단기 내 개선 계획이 필요한 'High' 등급 리스크입니다."
            else:
                urgency = "이 항목은 중기 개선 과제로 관리가 필요합니다."
            
            gap_details.append({
                'risk_id': gap['id'],
                'expert_comment': (
                    f"{urgency} "
                    f"근본 원인으로 '{gap.get('cause', '')}'이(가) 지목되며, "
                    f"방치 시 '{gap.get('consequence', '')}'으로 이어질 수 있습니다. "
                    f"권장 감사 포인트: {gap.get('audit_point', '')}"
                )
            })
        
        # 우선순위 결정: Critical > High > Medium, 동일 등급 내에서는 품질 > 안전 > 환경 > 규범
        severity_order = {'Critical': 0, 'High': 1, 'Medium': 2}
        category_order = {'품질': 0, '안전': 1, '환경': 2, '규범': 3}
        
        sorted_gaps = sorted(identified_gaps, key=lambda g: (
            severity_order.get(g.get('severity', 'Medium'), 9),
            next((v for k, v in category_order.items() if k in g.get('category', '')), 9)
        ))
        
        top_priority = None
        if sorted_gaps:
            top = sorted_gaps[0]
            top_priority = {
                'risk_id': top['id'],
                'reason': (
                    f"'{top.get('risk_title', '')}' — "
                    f"심각도 '{top.get('severity', '')}' 등급으로, "
                    f"ISO 조항 '{top.get('iso_clause', '')}' 위반에 해당합니다. "
                    f"즉각적인 현장 확인 및 시정 조치를 권고합니다."
                )
            }
        
        # 요약 생성
        critical_count = sum(1 for g in identified_gaps if g.get('severity') == 'Critical')
        high_count = sum(1 for g in identified_gaps if g.get('severity') == 'High')
        
        summary_parts = [f"자기진단 결과, 총 {len(identified_gaps)}건의 Gap이 확인되었습니다."]
        if critical_count > 0:
            summary_parts.append(f"이 중 {critical_count}건은 'Critical' 등급으로, 인증 심사 시 중대 부적합(Major NC) 판정 가능성이 높은 항목입니다.")
        if high_count > 0:
            summary_parts.append(f"{high_count}건은 'High' 등급으로 경영진의 즉각적 인지가 필요합니다.")
        if uncertain_items:
            summary_parts.append(f"추가로 {len(uncertain_items)}건의 항목에서 답변자가 현황을 파악하지 못하고 있어 별도 검증이 필요합니다.")
        summary_parts.append("아래 각 항목의 근본 원인과 감사 포인트를 참고하여 자체 점검을 실시하시기 바랍니다.")
        
        return {
            'gap_analysis_summary': ' '.join(summary_parts),
            'gap_details': gap_details,
            'uncertain_note': (
                f"{len(uncertain_items)}개 항목에 대해 '잘 모르겠음'으로 응답하셨습니다. "
                "이는 해당 프로세스가 부재하거나, 담당자가 현황을 인지하지 못하고 있음을 의미할 수 있습니다. "
                "현장 실사를 통한 정확한 확인이 권고됩니다."
            ) if uncertain_items else "",
            'top_priority': top_priority,
            'ai_model_used': 'expert_rule_engine',
            'source': 'expert_analysis'
        }
