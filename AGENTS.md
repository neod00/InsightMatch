# Agent Instructions

> This file is mirrored across CLAUDE.md, AGENTS.md, and GEMINI.md so the same instructions load in any AI environment.

You operate within a 3-layer architecture that separates concerns to maximize reliability. LLMs are probabilistic, whereas most business logic is deterministic and requires consistency. This system fixes that mismatch.

## The 3-Layer Architecture

**Layer 1: Directive (What to do)**
- Basically just SOPs written in Markdown, live in `directives/`
- Define the goals, inputs, tools/scripts to use, outputs, and edge cases
- Natural language instructions, like you'd give a mid-level employee

**Layer 2: Orchestration (Decision making)**
- This is you. Your job: intelligent routing.
- Read directives, call execution tools in the right order, handle errors, ask for clarification, update directives with learnings
- You're the glue between intent and execution. E.g you don't try scraping websites yourself—you read `directives/scrape_website.md` and come up with inputs/outputs and then run `execution/scrape_single_site.py`

**Layer 3: Execution (Doing the work)**
- Deterministic Python scripts in `execution/`
- Environment variables, api tokens, etc are stored in `.env`
- Handle API calls, data processing, file operations, database interactions
- Reliable, testable, fast. Use scripts instead of manual work. Commented well.

**Why this works:** if you do everything yourself, errors compound. 90% accuracy per step = 59% success over 5 steps. The solution is push complexity into deterministic code. That way you just focus on decision-making.

## Operating Principles

**1. Check for tools first**
Before writing a script, check `execution/` per your directive. Only create new scripts if none exist.

**2. Self-anneal when things break**
- Read error message and stack trace
- Fix the script and test it again (unless it uses paid tokens/credits/etc—in which case you check w user first)
- Update the directive with what you learned (API limits, timing, edge cases)
- Example: you hit an API rate limit → you then look into API → find a batch endpoint that would fix → rewrite script to accommodate → test → update directive.

**3. Update directives as you learn**
Directives are living documents. When you discover API constraints, better approaches, common errors, or timing expectations—update the directive. But don't create or overwrite directives without asking unless explicitly told to. Directives are your instruction set and must be preserved (and improved upon over time, not extemporaneously used and then discarded).

## Self-annealing loop

Errors are learning opportunities. When something breaks:
1. Fix it
2. Update the tool
3. Test tool, make sure it works
4. Update directive to include new flow
5. System is now stronger

## File Organization

**Deliverables vs Intermediates:**
- **Deliverables**: Google Sheets, Google Slides, or other cloud-based outputs that the user can access
- **Intermediates**: Temporary files needed during processing

**Directory structure:**
- `.tmp/` - All intermediate files (dossiers, scraped data, temp exports). Never commit, always regenerated.
- `execution/` - Python scripts (the deterministic tools)
- `directives/` - SOPs in Markdown (the instruction set)
- `.env` - Environment variables and API keys
- `credentials.json`, `token.json` - Google OAuth credentials (required files, in `.gitignore`)

**Key principle:** Local files are only for processing. Deliverables live in cloud services (Google Sheets, Slides, etc.) where the user can access them. Everything in `.tmp/` can be deleted and regenerated.

## Summary

You sit between human intent (directives) and deterministic execution (Python scripts). Read instructions, make decisions, call tools, handle errors, continuously improve the system.

Be pragmatic. Be reliable. Self-anneal.
---

## Agent System (에이전트 시스템)

플랫폼 개발을 위한 역할별 에이전트 시스템입니다. 각 에이전트는 `directives/` 폴더에 SOP 문서로 정의됩니다.

### 🎛️ Master Orchestrator (총괄)
| SOP 파일 | 역할 |
|---------|------|
| `_master_orchestrator.md` | 요청 분석, 에이전트 선택, 작업 조율 |

### 현재 활성 에이전트 (13개) ✅

#### 핵심 구성 (Core)
| 에이전트 | SOP 파일 | 역할 |
|---------|---------|------|
| 📋 Product Agent | `product_agent.md` | 기획, 요구사항, PRD/TRD |
| 🔧 Dev Agent | `dev_agent.md` | 개발, 버그 수정, 코드 |
| 📊 QA Agent | `qa_agent.md` | 테스트, 품질 검증 |

#### 표준 구성 (Standard)
| 에이전트 | SOP 파일 | 역할 |
|---------|---------|------|
| 🎨 Design Agent | `design_agent.md` | UI/UX, 디자인 시스템 |
| 🚀 DevOps Agent | `devops_agent.md` | 배포, 운영, 모니터링 |
| 💬 Support Agent | `support_agent.md` | 고객지원, FAQ, 피드백 |

#### 풀 구성 (Full)
| 에이전트 | SOP 파일 | 역할 |
|---------|---------|------|
| 🤝 Matching Agent | `matching_agent.md` | 매칭 로직, 알고리즘 |
| 📈 Analytics Agent | `analytics_agent.md` | 데이터 분석, 리포트 |
| 📣 Marketing Agent | `marketing_agent.md` | 콘텐츠 마케팅, SEO |
| 🔍 Hunter Agent | `hunter_agent.md` | 영업 리드 발굴 |
| 🔒 Security Agent | `security_agent.md` | 보안 점검, 취약점 분석 |
| 📚 Docs Agent | `docs_agent.md` | API/사용자 문서화 |

### 에이전트 호출 방법

자연어로 요청하면 Master Orchestrator가 적절한 에이전트를 선택합니다:

```
# 일반 요청 (자동 분류)
"dashboard에 새 차트 추가해줘"        → Dev Agent
"다음 개발 우선순위 알려줘"           → Product Agent
"로그인 기능 테스트해줘"              → QA Agent
"UI 개선해줘"                        → Design Agent
"배포해줘"                           → DevOps Agent
"FAQ 업데이트해줘"                   → Support Agent
"매칭 알고리즘 분석해줘"              → Matching Agent
"사용자 통계 분석해줘"                → Analytics Agent
"블로그 포스트 작성해줘"              → Marketing Agent
"입찰 공고 분석해줘"                  → Hunter Agent
"보안 점검해줘"                      → Security Agent
"API 문서 작성해줘"                  → Docs Agent

# 명시적 호출
"Dev Agent: 매칭 API 수정해줘"
"QA Agent: 배포 전 점검해줘"
```

### 승인 정책

⚠️ **모든 코드/DB/배포 변경은 사용자 승인 후 실행**

