import requests
import json

def test_create_and_fetch_project():
    """프로젝트 생성 및 조회 테스트"""
    base_url = "http://localhost:5000"
    
    print("=" * 60)
    print("프로젝트 생성 및 조회 테스트")
    print("=" * 60)
    
    # 1. 사용자 로그인 또는 확인
    print("\n1. 사용자 확인...")
    # 먼저 컨설턴트 목록을 가져와서 user_id 확인
    try:
        response = requests.get(f"{base_url}/api/consultants", timeout=5)
        consultants = response.json()
        if consultants:
            consultant = consultants[0]
            print(f"   컨설턴트 ID: {consultant.get('id')}")
            consultant_id = consultant.get('id')
        else:
            print("   컨설턴트가 없습니다.")
            return
    except Exception as e:
        print(f"   ✗ 에러: {e}")
        return
    
    # 2. 테스트용 사용자 ID (실제로는 로그인한 사용자 ID를 사용해야 함)
    # 여기서는 간단히 1을 사용
    test_user_id = 1
    
    # 3. 프로젝트 생성
    print(f"\n2. 프로젝트 생성 (company_id={test_user_id}, consultant_id={consultant_id})...")
    try:
        project_data = {
            "company_id": test_user_id,
            "consultant_id": consultant_id,
            "title": "ISO 9001 인증 프로젝트"
        }
        response = requests.post(
            f"{base_url}/api/projects",
            json=project_data,
            timeout=5
        )
        print(f"   상태 코드: {response.status_code}")
        if response.status_code == 201:
            result = response.json()
            project_id = result.get('id')
            print(f"   ✓ 프로젝트 생성 성공! 프로젝트 ID: {project_id}")
        else:
            print(f"   ✗ 프로젝트 생성 실패: {response.text}")
            return
    except Exception as e:
        print(f"   ✗ 에러: {e}")
        return
    
    # 4. 프로젝트 조회
    print(f"\n3. 프로젝트 조회 (user_id={test_user_id})...")
    try:
        response = requests.get(f"{base_url}/api/projects?user_id={test_user_id}", timeout=5)
        print(f"   상태 코드: {response.status_code}")
        if response.status_code == 200:
            projects = response.json()
            print(f"   프로젝트 수: {len(projects)}")
            if projects:
                print(f"\n   첫 번째 프로젝트 정보:")
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
    test_create_and_fetch_project()

