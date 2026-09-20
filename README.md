# Jindo Studio v1.1 — Render 배포판

소재 또는 YouTube 레퍼런스를 바탕으로 진돗개 정보 콘텐츠를 만들고, 쇼츠/롱폼용 대본·이미지·TTS·자막·MP4 렌더링까지 이어가는 Streamlit 웹앱입니다.

## v1.1 변경점
- Render용 `Dockerfile` 추가
- Docker 이미지에 FFmpeg 자동 설치
- Render Blueprint용 `render.yaml` 추가
- 영구 디스크 `/var/data`를 프로젝트 저장소로 사용
- `OPENAI_API_KEY`는 코드에 넣지 않고 Render Secret 환경변수로 입력
- Streamlit health check 경로 설정

## Render 배포
1. 이 폴더의 **내용물 전체**를 GitHub 저장소 루트에 업로드합니다. ZIP 파일 자체를 저장소에 올리는 방식이 아닙니다.
2. Render에서 **New > Blueprint**를 선택하고 해당 GitHub 저장소를 연결합니다.
3. `render.yaml`을 읽어 Web Service와 10GB persistent disk를 구성합니다.
4. `OPENAI_API_KEY` 값을 Render의 Secret 환경변수로 직접 입력합니다. API 키를 GitHub나 채팅에 붙여넣지 마세요.
5. 배포가 끝나면 Render가 발급한 HTTPS 주소로 휴대폰에서 접속합니다.

## 비용 주의
`render.yaml`은 영구 디스크가 필요한 실제 사용 구성을 위해 `starter` 플랜을 지정합니다. Render 요금과 OpenAI API 사용료는 별도입니다. 가입 직후 결제하기 전에 Render 대시보드에 표시되는 현재 가격을 확인하세요.

## 로컬 실행(선택)
```bash
pip install -r requirements.txt
streamlit run app.py
```
FFmpeg가 시스템 PATH에 설치되어 있어야 최종 MP4 렌더링이 동작합니다.

## 데이터 보관
프로젝트 JSON, 생성 이미지, 음성, 자막, MP4는 기본적으로 `/var/data/projects` 아래에 저장됩니다. Render persistent disk가 연결되지 않은 환경에서는 `JINDO_PROJECTS_DIR` 값을 다른 경로로 바꿀 수 있습니다.
