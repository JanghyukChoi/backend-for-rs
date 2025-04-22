# 📊 상대강도(RS) 기반 종목 분석 웹사이트

## 🔗 배포 주소
https://janghyukchoi.github.io/backend-for-rs/

---

## 🧠 프로젝트 개요
국내 KOSPI/KOSDAQ 주식 종목의 상대강도(RS) 점수를 계산하고,  
이를 기반으로 강한 종목을 선별하여 웹에서 시각화해주는 프로젝트입니다.  
마크 미너비니(Mark Minervini)의 RS 분석 방식에 착안하여 개발되었습니다.

---

## 🏗️ 전체 시스템 구조

[Client (HTML + JS)] <── API 호출 ──> [FastAPI 서버 (Railway)] <── 데이터 저장/로드 ─> [Firebase Firestore]


- **프론트엔드**: HTML + 순수 JavaScript
- **백엔드**: Python FastAPI (Railway 서버리스)
- **데이터 저장소**: Firebase Firestore

---

## 🔍 주요 기능

### ✅ 종목 데이터 수집
- `pykrx` 라이브러리를 통해 KOSPI/KOSDAQ 종목의 OHLCV, 시가총액, 섹터 정보 등 수집

### ✅ 상대강도(RS) 계산 방식

RS Score = (2주 수익률 × 2) + 1개월 + 3개월 + 6개월 수익률 → 이후 1~99 백분위 점수로 정규화


### ✅ 자동화된 데이터 갱신 시스템
- 매일 **오후 3:31 (KST)** 에 `/api/refresh` 엔드포인트 자동 호출
- 호출 시 pykrx 데이터 재수집 → Firestore 갱신 → FastAPI 캐시(`df_cached`) 재계산
- 자동화 도구: [cron-job.org](https://cron-job.org) 사용

---

## 🛠️ 기술 스택

| 분류       | 기술                         |
|------------|------------------------------|
| 프론트엔드 | HTML, CSS, JavaScript        |
| 백엔드     | Python, FastAPI              |
| 서버       | Railway (서버리스 배포)      |
| DB         | Firebase Firestore           |
| 스케줄러   | cron-job.org (외부 자동 호출) |
| 데이터     | pykrx (한국거래소 주식 데이터) |

---

## 📂 주요 파일 구조

backend.py → FastAPI 로직 (RS 계산, DB 연동, API 제공) index.html → 사용자 UI (테이블, 검색, 페이지네이션)



---

## 🔁 전체 작동 흐름 요약

1. 사용자가 웹사이트에 접속 → JS가 `/api/stocks` 호출
2. FastAPI는 Firestore 또는 캐시(`df_cached`)에서 데이터 반환
3. 매일 15:31에 `/api/refresh` 자동 호출 → 최신 데이터 생성
4. Firestore에 저장 및 `df_cached` 갱신 완료

---

## 🚀 향후 개선 아이디어

- 리액트(React) 기반 SPA로 프론트 리팩토링
- 종목 상세 페이지 + 차트 시각화 기능 추가
- 사용자 즐겨찾기 / 종목 비교 기능
- Firestore 대신 BigQuery 또는 PostgreSQL로 확장

---

## 🙋‍♂️ 개발자 소개

최장혁  
한양대학교 경제금융학과 / 소프트웨어융합 전공  
네이버 블로그 👉 [https://blog.naver.com/jangsdaytrading](https://blog.naver.com/jangsdaytrading)

---


![시스템 구조도](docs/architecture.png)




