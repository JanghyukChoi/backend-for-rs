import os
import pandas as pd
from fastapi import FastAPI, Query
from pykrx import stock
import datetime
import requests
from concurrent.futures import ThreadPoolExecutor
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

# ✅ CSV 저장 경로
DATA_FILE = "stock_data.csv"
LAST_UPDATE_FILE = "last_update.txt"  # ✅ 마지막 업데이트 날짜 저장 파일

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

# ✅ 시가총액을 억 단위로 변환하는 함수
def format_market_cap(value):
    return f"{round(value / 1e8)}억"

# ✅ 새로운 데이터 생성이 필요한지 확인하는 함수
def should_update_data():
    """ 오후 3시 30분 이후에는 데이터 갱신 """
    now = datetime.datetime.now()
    is_after_330 = now.hour > 15 or (now.hour == 15 and now.minute >= 30)

    if os.path.exists(DATA_FILE) and os.path.exists(LAST_UPDATE_FILE):
        with open(LAST_UPDATE_FILE, "r") as f:
            last_update_date = f.read().strip()

        today_str = now.strftime("%Y-%m-%d")
        if last_update_date == today_str and not is_after_330:
            return False

    return True  # ✅ 업데이트 필요

# ✅ 데이터 로드 또는 생성
def load_or_create_stock_data():
    if not should_update_data():
        print("✅ 기존 데이터 로드 중...")
        return pd.read_csv(DATA_FILE)  # 기존 데이터 로드

    print("⚡ 새 데이터 생성 중...")
    stock_data = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(calculate_relative_strength, all_stocks))

    for i, result in enumerate(results):
        if result:
            total_score, close_price, increase_from_low, decrease_from_high = result
            ticker = all_stocks[i]
            sector = sector_map.get(ticker, "알 수 없음")
            stock_data.append([
                ticker, stock.get_market_ticker_name(ticker), close_price, total_score,
                f"{increase_from_low:.2f}%", f"{decrease_from_high:.2f}%", sector
            ])

    df = pd.DataFrame(stock_data, columns=["종목코드", "이름", "종가", "상대강도", "최저가 대비 상승률", "최고가 대비 하락률", "섹터"])
    df["상대강도"] = (df["상대강도"].rank(method="min", pct=True) * 98 + 1).round(2)

    market_cap_data = stock.get_market_cap(today)
    df["시가총액"] = df["종목코드"].apply(lambda x: market_cap_data.loc[x, "시가총액"] if x in market_cap_data.index else 0)
    df = df[df["시가총액"] >= 500e8]
    df["시가총액"] = df["시가총액"].apply(format_market_cap)

    sector_rank = df.groupby("섹터")["상대강도"].mean().rank(ascending=False).astype(int)
    sector_rank_df = pd.DataFrame({"섹터": sector_rank.index, "섹터 수익률 순위": sector_rank.values})
    sector_rank_df["섹터 수익률 순위"] = sector_rank_df["섹터 수익률 순위"].apply(lambda x: f"섹터 수익률 {x}위")

    df = df.merge(sector_rank_df, on="섹터", how="left")

    df = df.sort_values(by="상대강도", ascending=False)
    df.to_csv(DATA_FILE, index=False)

    with open(LAST_UPDATE_FILE, "w") as f:
        f.write(datetime.datetime.now().strftime("%Y-%m-%d"))

    return df

# ✅ FastAPI 실행 시, 기존 데이터 로드 또는 새로 계산
df_cached = load_or_create_stock_data()


@app.get("/api/stocks")
async def get_stocks(page: int = Query(1, alias="page"), limit: int = Query(100, alias="limit")):
    start_idx = (page - 1) * limit
    end_idx = start_idx + limit
    total_items = len(df_cached)

    # ✅ 종목코드를 문자열로 변환하여 앞의 0이 사라지지 않도록 수정
    df_cached["종목코드"] = df_cached["종목코드"].astype(str).str.zfill(6)

    paginated_data = df_cached.iloc[start_idx:end_idx].to_dict(orient="records")

    return {
        "stocks": paginated_data,
        "total_pages": (total_items // limit) + (1 if total_items % limit > 0 else 0),
        "current_page": page
    }
