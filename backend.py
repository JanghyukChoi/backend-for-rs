import os
import pandas as pd
from fastapi import FastAPI, Query
from pykrx import stock
import datetime
import requests
from concurrent.futures import ThreadPoolExecutor
import json
import firebase_admin
from firebase_admin import credentials, firestore
from fastapi.middleware.cors import CORSMiddleware 


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

# ✅ 현재 날짜 및 1년 전 날짜 계산
today = (datetime.datetime.today() - datetime.timedelta(days=1)).strftime("%Y%m%d")
one_year_ago_str = (datetime.datetime.today() - datetime.timedelta(days=365)).strftime("%Y%m%d")

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

# ✅ 병렬 처리 실행 (섹터 데이터 가져오기)
with ThreadPoolExecutor(max_workers=5) as executor:
    executor.map(fetch_sector_data, sector_codes)

# ✅ 특정 날짜 기준으로 상대강도 계산 및 가격 변동률 추가
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

        # ✅ 최저가 대비 상승률 (%) 계산
        lowest_price = df["종가"].min()
        increase_from_low = ((today_data["종가"] - lowest_price) / lowest_price) * 100

        # ✅ 최고가 대비 하락률 (%) 계산
        highest_price = df["종가"].max()
        decrease_from_high = ((highest_price - today_data["종가"]) / highest_price) * 100

        return total_score, today_data["종가"], increase_from_low, decrease_from_high
    except:
        return None

# ✅ Firestore에 데이터 저장
def save_to_firestore(data):
    collection_ref = db.collection("stocks")
    for stock in data:
        doc_ref = collection_ref.document(stock["종목코드"])
        doc_ref.set(stock)
    print(" Firestore에 데이터 저장 완료!")

# ✅ 새로운 데이터 생성이 필요한지 확인하는 함수
def should_update_data():
    """ 오후 3시 30분 이후에는 데이터 갱신 """
    now = datetime.datetime.now()
    is_after_330 = now.hour > 15 or (now.hour == 15 and now.minute >= 30)

    doc_ref = db.collection("metadata").document("last_update")
    doc = doc_ref.get()

    if doc.exists:
        last_update_date = doc.to_dict().get("date", "")
        today_str = now.strftime("%Y-%m-%d")
        if last_update_date == today_str and not is_after_330:
            return False

    return True  # ✅ 업데이트 필요

# ✅ 데이터 로드 또는 생성
def load_or_create_stock_data():
    if not should_update_data():
        print("Firestore에서 기존 데이터 로드 중...")
        stocks_ref = db.collection("stocks").order_by("상대강도", direction=firestore.Query.DESCENDING).stream()
        return [doc.to_dict() for doc in stocks_ref]

    print("⚡ 새 데이터 생성 중..")
    print("조금만 기다려보세요")
    stock_data = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(calculate_relative_strength, all_stocks))

    market_cap_data = stock.get_market_cap(today)

    # ✅ 섹터별 평균 상대강도 계산
    sector_scores = {}
    for i, result in enumerate(results):
        if result:
            total_score, _, _, _ = result
            ticker = all_stocks[i]
            sector = sector_map.get(ticker, "알 수 없음")
            if sector not in sector_scores:
                sector_scores[sector] = []
            sector_scores[sector].append(total_score)

    sector_rank = {sector: idx + 1 for idx, (sector, _) in enumerate(sorted(sector_scores.items(), key=lambda x: sum(x[1]) / len(x[1]), reverse=True))}

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
                "상대강도": round(total_score, 2),
                "최저가 대비 상승률": f"+{increase_from_low:.2f}%",
                "최고가 대비 하락률": f"-{decrease_from_high:.2f}%",
                "섹터": sector,
                "시가총액": f"{round(market_cap / 1e8)}억",
                "섹터 수익률 순위": f"섹터 수익률 {sector_rank.get(sector, 'N/A')}위"
            })

    save_to_firestore(stock_data)

    # ✅ Firestore에 마지막 업데이트 정보 저장
    db.collection("metadata").document("last_update").set({
        "date": datetime.datetime.now().strftime("%Y-%m-%d"),
        "time": datetime.datetime.now().strftime("%H:%M:%S")
    })

    return stock_data

# ✅ FastAPI 실행 시, 기존 데이터 Firestore에서 로드 또는 새로 계산
df_cached = load_or_create_stock_data()

@app.get("/api/stocks")
async def get_stocks(page: int = Query(1, alias="page"), limit: int = Query(100, alias="limit")):
    start_idx = (page - 1) * limit
    end_idx = start_idx + limit
    total_items = len(df_cached)

    paginated_data = df_cached[start_idx:end_idx]

    return {
        "stocks": paginated_data,
        "total_pages": (total_items // limit) + (1 if total_items % limit > 0 else 0),
        "current_page": page
    }

