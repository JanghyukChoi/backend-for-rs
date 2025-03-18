import os
import pandas as pd
from fastapi import FastAPI, Query, BackgroundTasks
from pykrx import stock
import datetime
import requests
from concurrent.futures import ThreadPoolExecutor
import json
import firebase_admin
from firebase_admin import credentials, firestore
from fastapi.middleware.cors import CORSMiddleware
import pytz  # 한국 시간 설정을 위한 라이브러리

app = FastAPI()

# ✅ CORS 설정 추가
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ✅ Firebase 인증 설정 (Railway 환경 변수에서 JSON 로드)
firebase_config = json.loads(os.getenv("FIREBASE_CREDENTIALS"))
cred = credentials.Certificate(firebase_config)
firebase_admin.initialize_app(cred)

# ✅ Firestore 연결
db = firestore.client()

# ✅ 한국 시간(KST) 설정 및 날짜 계산 (KST 기준)
kst = pytz.timezone("Asia/Seoul")
today = (datetime.datetime.now(kst) - datetime.timedelta(days=1)).strftime("%Y%m%d")
one_year_ago_str = (datetime.datetime.now(kst) - datetime.timedelta(days=365)).strftime("%Y%m%d")

# ✅ 코스피, 코스닥 종목 리스트 가져오기 (우선주 제거)
kospi_stocks = stock.get_market_ticker_list(market="KOSPI")
kosdaq_stocks = stock.get_market_ticker_list(market="KOSDAQ")
all_stocks = [ticker for ticker in kospi_stocks + kosdaq_stocks if ticker[-1] == "0"]

# ✅ WICS 섹터 코드 리스트 (모든 섹터)
sector_codes = ['G25', 'G35', 'G50', 'G40', 'G10', 'G20', 'G55', 'G30', 'G15', 'G45']

# ✅ 종목별 섹터 매핑 딕셔너리
sector_map = {}

# ✅ WICS 섹터 데이터 병렬 크롤링
def fetch_sector_data(sec_cd):
    url = f'http://www.wiseindex.com/Index/GetIndexComponets?ceil_yn=0&dt={today}&sec_cd={sec_cd}'
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        if 'list' in data:
            for item in data['list']:
                sector_map[item["CMP_CD"]] = item["SEC_NM_KOR"]

with ThreadPoolExecutor(max_workers=5) as executor:
    executor.map(fetch_sector_data, sector_codes)

# ✅ 특정 날짜 기준으로 상대강도 및 가격 변동률 계산
def calculate_relative_strength(ticker):
    try:
        df = stock.get_market_ohlcv_by_date(one_year_ago_str, today, ticker)
        if len(df) < 126:
            return None

        today_data = df.iloc[-1]
        two_weeks_ago = df.iloc[-10]
        one_month_ago = df.iloc[-21]
        three_months_ago = df.iloc[-63]
        six_months_ago = df.iloc[-126]

        score_1 = today_data["종가"] / two_weeks_ago["종가"]
        score_2 = two_weeks_ago["종가"] / one_month_ago["종가"]
        score_3 = one_month_ago["종가"] / three_months_ago["종가"]
        score_4 = three_months_ago["종가"] / six_months_ago["종가"]

        total_score = (score_1 * 2) + score_2 + score_3 + score_4

        # 최저가 대비 상승률 (%) 계산
        lowest_price = df["종가"].min()
        increase_from_low = ((today_data["종가"] - lowest_price) / lowest_price) * 100

        # 최고가 대비 하락률 (%) 계산
        highest_price = df["종가"].max()
        decrease_from_high = ((highest_price - today_data["종가"]) / highest_price) * 100

        return total_score, today_data["종가"], increase_from_low, decrease_from_high
    except Exception as e:
        print(f"Error calculating relative strength for {ticker}: {e}")
        return None

# ✅ Firestore에 데이터 배치 저장
def save_to_firestore(data):
    collection_ref = db.collection("stocks")
    batch = db.batch()
    for stock_doc in data:
        doc_ref = collection_ref.document(stock_doc["종목코드"])
        batch.set(doc_ref, stock_doc)
    batch.commit()
    print("Firestore에 데이터 전부 다 저장 완료하였습니다.")

# ✅ Firestore에서 데이터를 빠르게 불러오기
def load_stock_data():
    stocks_ref = db.collection("stocks").order_by("relative_strength", direction=firestore.Query.DESCENDING).stream()
    return [doc.to_dict() for doc in stocks_ref]

# ✅ 무거운 데이터 계산 및 Firestore 업데이트 (새 데이터 생성)
def compute_and_update_stock_data():
    print("⚡ 새 데이터 생성 중..")
    stock_data = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(calculate_relative_strength, all_stocks))

    market_cap_data = stock.get_market_cap(today)

    # 섹터별 평균 상대강도 계산
    sector_scores = {}
    for i, result in enumerate(results):
        if result:
            total_score, _, _, _ = result
            ticker = all_stocks[i]
            sector = sector_map.get(ticker, "알 수 없음")
            sector_scores.setdefault(sector, []).append(total_score)

    # 섹터별 순위 매기기 (평균 상대강도 기준 내림차순)
    sector_rank = {
        sector: idx + 1
        for idx, (sector, scores) in enumerate(
            sorted(sector_scores.items(), key=lambda x: sum(x[1]) / len(x[1]), reverse=True)
        )
    }

    # 종목별 데이터 생성
    for i, result in enumerate(results):
        if result:
            total_score, close_price, increase_from_low, decrease_from_high = result
            ticker = all_stocks[i]
            sector = sector_map.get(ticker, "알 수 없음")
            market_cap = market_cap_data.loc[ticker, "시가총액"] if ticker in market_cap_data.index else 0
            stock_data.append({
                "종목코드": ticker.zfill(6),
                "이름": stock.get_market_ticker_name(ticker),
                "종가": close_price,
                "relative_strength": round(total_score, 2),
                "lowest_increase_rate": f"+{increase_from_low:.2f}%",
                "highest_decrease_rate": f"-{decrease_from_high:.2f}%",
                "섹터": sector,
                "시가총액": f"{round(market_cap / 1e8)}억",
                "섹터 수익률 순위": f"섹터 수익률 {sector_rank.get(sector, 'N/A')}위"
            })

    # 백분위 순위 변환 및 내림차순 정렬
    df = pd.DataFrame(stock_data)
    df["시가총액(숫자)"] = df["시가총액"].str.replace("억", "").astype(float)
    df = df[df["시가총액(숫자)"] >= 500]  # 500억 이상 필터링

    df["relative_strength"] = (
        df["relative_strength"].rank(method="min", pct=True) * 98 + 1
    ).round(2)
    df = df.sort_values(by="relative_strength", ascending=False)
    stock_data = df.to_dict(orient="records")

    # Firestore에 저장
    save_to_firestore(stock_data)

    now_kst = datetime.datetime.now(kst)
    db.collection("metadata").document("last_update").set({
        "date": now_kst.strftime("%Y-%m-%d"),
        "time": now_kst.strftime("%H:%M:%S")
    })

    return stock_data

# ✅ 업데이트 필요 여부: 하루에 한 번만 업데이트 (Firestore 메타데이터 기준)
def should_update_data():
    now = datetime.datetime.now(kst)
    is_after_330 = now.hour > 15 or (now.hour == 15 and now.minute >= 30)
    doc_ref = db.collection("metadata").document("last_update")
    doc = doc_ref.get()
    today_str = now.strftime("%Y-%m-%d")
    if doc.exists:
        last_update_date = doc.to_dict().get("date", "")
        # 만약 마지막 업데이트 날짜가 오늘이 아니고, 현재 시간이 오후 3시 30 이후이면 업데이트 필요
        if last_update_date != today_str and is_after_330:
            return False
        else:
            return False
    else:
        # 메타데이터 문서가 없으면 업데이트 필요
        return True

# ✅ API 엔드포인트: 기존 데이터가 있으면 바로 반환하고, 업데이트 필요 시 백그라운드로 새 데이터 계산
@app.get("/api/stocks")
async def get_stocks(
    page: int = Query(1, alias="page"),
    limit: int = Query(100, alias="limit"),
    background_tasks: BackgroundTasks = None
):
    if should_update_data():
        # 백그라운드 작업에 새 데이터 계산 추가
        if background_tasks:
            background_tasks.add_task(compute_and_update_stock_data)
        # 기존(stale) 데이터가 있다면 빠르게 로드
        stock_data = load_stock_data()
        # 만약 데이터가 전혀 없다면 동기적으로 계산
        if not stock_data:
            stock_data = compute_and_update_stock_data()
    else:
        stock_data = load_stock_data()

    # 백분위 순위 기준 내림차순 정렬 (이미 Firestore에 저장된 순서와 동일할 수 있음)
    sorted_data = sorted(
        stock_data,
        key=lambda x: float(x["relative_strength"]),
        reverse=True
    )

    start_idx = (page - 1) * limit
    end_idx = start_idx + limit
    total_items = len(sorted_data)
    paginated_data = sorted_data[start_idx:end_idx]

    return {
        "stocks": paginated_data,
        "total_pages": (total_items // limit) + (1 if total_items % limit > 0 else 0),
        "current_page": page
    }
