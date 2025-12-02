import requests
import json

def test_quote_request():
    """견적 요청 및 프로젝트 생성 테스트"""
    base_url = "http://localhost:5000"
    
    print("=" * 60)
    print("견적 요청 및 프로젝트 생성 테스트")
    print("=" * 60)
    
    # 1. 사용자 로그인
    print("\n1. 사용자 로그인...")
    try:
        # 테스트용 사용자 (실제로는 존재하는 사용자여야 함)
        login_data = {
            "email": "test@example.com",
            "password": "test123"
        }
        response = requests.post(f"{base_url}/api/auth/login", json=login_data, timeout=5)
        if response.status_code == 200:
            login_result = response.json()
            user_id = login_result['user']['id']
            token = login_result['token']
            print(f"   ✓ 로그인 성공! 사용자 ID: {user_id}")
        else:
            # 로그인 실패 시 회원가입 시도
            print("   로그인 실패, 회원가입 시도...")
            signup_data = {
                "email": "test@example.com",
                "password": "test123",
                "name": "테스트 사용자",
                "role": "company"
            }
            signup_response = requests.post(f"{base_url}/api/auth/signup", json=signup_data, timeout=5)
            if signup_response.status_code == 201:
                print("   ✓ 회원가입 성공, 다시 로그인...")
                login_response = requests.post(f"{base_url}/api/auth/login", json=login_data, timeout=5)
                if login_response.status_code == 200:
                    login_result = login_response.json()
                    user_id = login_result['user']['id']
                    token = login_result['token']
                    print(f"   ✓ 로그인 성공! 사용자 ID: {user_id}")
                else:
                    print("   ✗ 로그인 실패")
                    return
            else:
                print(f"   ✗ 회원가입 실패: {signup_response.text}")
                return
    except Exception as e:
        print(f"   ✗ 에러: {e}")
        return
    
    # 2. 컨설턴트 목록 가져오기
    print("\n2. 컨설턴트 목록 가져오기...")
    try:
        response = requests.get(f"{base_url}/api/consultants", timeout=5)
        consultants = response.json()
        if consultants:
            consultant_id = consultants[0]['id']
            consultant_name = consultants[0]['name']
            print(f"   ✓ 컨설턴트 찾음: {consultant_name} (ID: {consultant_id})")
        else:
            print("   ✗ 컨설턴트가 없습니다.")
            return
    except Exception as e:
        print(f"   ✗ 에러: {e}")
        return
    
    # 3. 견적 요청
    print(f"\n3. 견적 요청 (user_id={user_id}, consultant_id={consultant_id})...")
    try:
        quote_data = {
            "consultant_ids": [consultant_id],
            "analysis_context": {
                "company_name": "테스트 기업",
                "industry": "IT/Software",
                "recommended_standards": [
                    {"code": "ISO 9001", "name": "품질경영시스템"}
                ]
            },
            "user_id": user_id
        }
        response = requests.post(f"{base_url}/api/quotes/request", json=quote_data, timeout=5)
        print(f"   상태 코드: {response.status_code}")
        if response.status_code == 201:
            result = response.json()
            print(f"   ✓ 견적 요청 성공!")
            print(f"   생성된 프로젝트 수: {len(result.get('projects', []))}")
            if result.get('projects'):
                print(f"\n   생성된 프로젝트:")
                print(json.dumps(result['projects'], indent=2, ensure_ascii=False))
        else:
            print(f"   ✗ 견적 요청 실패: {response.text}")
            return
    except Exception as e:
        print(f"   ✗ 에러: {e}")
        return
    
    # 4. 프로젝트 조회
    print(f"\n4. 프로젝트 조회 (user_id={user_id})...")
    try:
        response = requests.get(f"{base_url}/api/projects?user_id={user_id}", timeout=5)
        print(f"   상태 코드: {response.status_code}")
        if response.status_code == 200:
            projects = response.json()
            print(f"   프로젝트 수: {len(projects)}")
            if projects:
                print(f"\n   첫 번째 프로젝트:")
                print(json.dumps(projects[0], indent=2, ensure_ascii=False))
                print(f"\n   ✓ 프로젝트 조회 성공!")
            else:
                print("   프로젝트가 없습니다.")
        else:
            print(f"   ✗ 프로젝트 조회 실패: {response.text}")
    except Exception as e:
        print(f"   ✗ 에러: {e}")
    
    print("\n" + "=" * 60)
    print("테스트 완료")
    print("=" * 60)

if __name__ == "__main__":
    import time
    time.sleep(3)  # 서버 시작 대기
    test_quote_request()

