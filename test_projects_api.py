import requests
import json

def test_projects_api():
    """프로젝트 API 테스트"""
    base_url = "http://localhost:5000"
    
    print("=" * 60)
    print("프로젝트 API 테스트 시작")
    print("=" * 60)
    
    # 1. 서버 연결 확인
    print("\n1. 서버 연결 확인...")
    try:
        response = requests.get(f"{base_url}/", timeout=5)
        print(f"   ✓ 서버 연결 성공 (상태 코드: {response.status_code})")
    except requests.exceptions.ConnectionError:
        print("   ✗ 서버에 연결할 수 없습니다. 서버가 실행 중인지 확인하세요.")
        print("   실행 방법: cd server && python app.py")
        return
    except Exception as e:
        print(f"   ✗ 서버 연결 실패: {e}")
        return
    
    # 2. user_id 없이 요청 (400 에러 예상)
    print("\n2. user_id 없이 요청 테스트...")
    try:
        response = requests.get(f"{base_url}/api/projects", timeout=5)
        print(f"   상태 코드: {response.status_code}")
        print(f"   응답: {response.json()}")
        if response.status_code == 400:
            print("   ✓ 예상대로 400 에러 반환")
    except Exception as e:
        print(f"   ✗ 에러: {e}")
    
    # 3. 존재하지 않는 user_id로 요청 (빈 배열 반환 예상)
    print("\n3. 존재하지 않는 user_id로 요청 테스트...")
    try:
        response = requests.get(f"{base_url}/api/projects?user_id=99999", timeout=5)
        print(f"   상태 코드: {response.status_code}")
        projects = response.json()
        print(f"   응답: {projects}")
        if isinstance(projects, list):
            print(f"   ✓ 빈 배열 반환 (프로젝트 수: {len(projects)})")
    except Exception as e:
        print(f"   ✗ 에러: {e}")
    
    # 4. 유효한 user_id로 요청 (실제 데이터 확인)
    print("\n4. 유효한 user_id로 요청 테스트...")
    # 먼저 사용자 목록을 확인할 수 없으므로, 일반적인 user_id로 테스트
    test_user_ids = [1, 2, 3]
    for user_id in test_user_ids:
        try:
            response = requests.get(f"{base_url}/api/projects?user_id={user_id}", timeout=5)
            print(f"\n   user_id={user_id}:")
            print(f"   상태 코드: {response.status_code}")
            if response.status_code == 200:
                projects = response.json()
                print(f"   프로젝트 수: {len(projects)}")
                if projects:
                    print(f"   첫 번째 프로젝트:")
                    print(json.dumps(projects[0], indent=2, ensure_ascii=False))
            else:
                print(f"   응답: {response.text}")
        except Exception as e:
            print(f"   ✗ 에러: {e}")
    
    # 5. 에러 응답 처리 테스트
    print("\n5. 에러 응답 처리 테스트...")
    try:
        # 잘못된 형식의 user_id
        response = requests.get(f"{base_url}/api/projects?user_id=invalid", timeout=5)
        print(f"   상태 코드: {response.status_code}")
        print(f"   응답: {response.text[:200]}")
    except Exception as e:
        print(f"   ✗ 에러: {e}")
    
    print("\n" + "=" * 60)
    print("테스트 완료")
    print("=" * 60)
    
    print("\n문제 해결 체크리스트:")
    print("1. 서버가 실행 중인가? (cd server && python app.py)")
    print("2. 데이터베이스에 프로젝트 데이터가 있는가?")
    print("3. localStorage에 user 정보가 올바르게 저장되어 있는가?")
    print("4. 브라우저 콘솔에서 CORS 에러가 발생하는가?")
    print("5. API 응답이 200이 아닌 경우 에러 처리가 필요한가?")

if __name__ == "__main__":
    test_projects_api()

