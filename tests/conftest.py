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
