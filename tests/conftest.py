"""테스트 공통 설정.

pytest 는 테스트 모듈보다 conftest.py 를 먼저 로드한다.
여기서 인메모리 DB 를 지정해야 어떤 테스트 파일이 먼저 import 되더라도
api/index.py 가 파일 DB 대신 인메모리로 엔진을 만든다.

이 설정이 없으면 db.init_app() 시점에 로컬 개발용 insightmatch.db 로
엔진이 만들어지고, 각 테스트의 tearDown 에 있는 db.drop_all() 이
개발 DB 의 테이블을 전부 삭제한다(실제로 발생했던 문제).
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'api')))

os.environ['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'

# 외부 서비스 차단.
# api/index.py 는 import 시 load_dotenv() 로 저장소의 .env(운영 키)를 읽는다. 아무것도 막지 않으면
# 테스트가 실제 Supabase 저장소에 파일을 올리고(2026-11-04 실제 발생), 실제 SMTP 로 메일을 보내며,
# 국세청·공공데이터·AI API 를 부른다. load_dotenv 는 이미 있는 환경변수를 덮어쓰지 않으므로
# 여기서 빈 값으로 먼저 박아 둔다. 값이 필요한 테스트는 patch.dict(os.environ, {...}) 로 직접 넣는다.
for _key in (
    'SUPABASE_URL', 'SUPABASE_ANON_KEY', 'SUPABASE_SERVICE_ROLE_KEY', 'SUPABASE_DB_URL', 'DATABASE_URL',
    'SMTP_HOST', 'SMTP_USER', 'SMTP_PASSWORD',
    'OPENAI_API_KEY', 'GOOGLE_API_KEY', 'NTS_BUSINESS_API_KEY', 'DATA_GO_KR_API_KEY',
):
    os.environ[_key] = ''
os.environ['FLASK_SKIP_DOTENV'] = '1'
