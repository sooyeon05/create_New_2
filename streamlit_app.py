import streamlit as st
import pandas as pd
import requests
import folium
from streamlit_folium import st_folium
from geopy.distance import geodesic

# 페이지 기본 설정
st.set_page_config(
    page_title="실시간 응급실 혼잡도 대시보드",
    layout="wide"
)

# 🔑 Streamlit Cloud Secrets 에서 API 키 가져오기
API_KEY = st.secrets.get("EGEN_API_KEY", None)
BASE_URL = "http://apis.data.go.kr/B552657/ErmctInfoInqireService/getEmrrmRltmUsefulSckbdInfoInqire"

if not API_KEY:
    st.error("EGEN_API_KEY 가 설정되어 있지 않습니다. Streamlit Cloud의 Secrets에 API 키를 등록해주세요.")
    st.stop()


def fetch_data(num_rows: int = 999):
    """
    공공데이터포털 응급의료 실시간 정보 API 호출하여 DataFrame과 에러메시지를 반환
    성공 시: (df, None)
    실패 시: (None, 에러메시지 문자열)
    """
    # serviceKey 는 URL에 직접 붙이고, 나머지는 params 로 넘깁니다.
    url = f"{BASE_URL}?serviceKey={API_KEY}"
    params = {
        "_type": "json",
        "pageNo": 1,
        "numOfRows": num_rows,
    }

    # 1) HTTP 요청 시도
    try:
        r = requests.get(url, params=params, timeout=15)
    except Exception as e:
        return None, f"API 요청 자체가 실패했습니다: {e}"

    # 2) 상태코드 확인
    if r.status_code != 200:
        snippet = r.text[:300]
        return None, f"API 호출 실패 (HTTP {r.status_code})\n응답 내용 일부:\n{snippet}"

    # 3) JSON 파싱
    try:
        js = r.json()
    except ValueError:
        snippet = r.text[:300]
        return None, f"API 응답이 JSON 형식이 아닙니다.\n응답 내용 일부:\n{snippet}"

    body = js.get("response", {}).get("body", {})
    items = body.get("items", {})
    item_list = items.get("item")

    if not item_list:
        return None, "API 응답은 성공했지만 'item' 데이터가 비어 있습니다."

    df = pd.DataFrame(item_list)

    # --------- 여기서부터 데이터 전처리 ---------
    cols = [
        "dutyName",     # 병원명
        "dutyAddr",     # 주소
        "dutyTel3",     # 응급실 전화
        "hvec",         # 가용 응급실 병상수
        "hvoc",         # 현재 재원 환자수
        "wgs84Lat",     # 위도
        "wgs84Lon",     # 경도
        "hvidate",      # 업데이트 시각
        "dutyTime1s",   # 응급실 시작시간
        "dutyTime1c"    # 응급실 종료시간
    ]
    for c in cols:
        if c not in df.columns:
            df[c] = None
    df = df[cols].copy()

    # 숫자형 변환
    for c in ["hvec", "hvoc", "wgs84Lat", "wgs84Lon"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # 혼잡도 지수 = 재원 환자수 / (가용병상 + 1)
    df["혼잡도지수"] = (df["hvoc"] / (df["hvec"].fillna(0) + 1)).round(2)

    # 시/도 컬럼 (주소 첫 단어)
    def get_sido(addr):
        if isinstance(addr, str) and addr.strip():
            return addr.split()[0]
        return None

    df["시도"] = df["dutyAddr"].apply(get_sido)

    # 혼잡도 레이블링
    def label_cong(x):
        if pd.isna(x):
            return "정보없음"
        if x < 0.5:
            return "여유"
        if x < 1.0:
            return "보통"
        return "혼잡"

    df["혼잡도"] = df["혼잡도지수"].apply(label_cong)
    df.rename(columns={"hvidate": "업데이트"}, inplace=True)

    # 좌표 없는 병원 제거
    df = df.dropna(subset=["wgs84Lat", "wgs84Lon"]).reset_index(drop=True)
    return df, None


# ---------------- UI 시작 ----------------

st.title("🏥 실시간 응급실 혼잡도 대시보드")
st.caption(
    "공공데이터포털 응급의료 정보를 활용해, "
    "혼잡도(환자 수 / 가용 병상)와 내 위치까지의 거리를 함께 고려하여 "
    "지금 더 빨리 진료받을 수 있는 병원을 찾도록 돕는 대시보드입니다."
)

df, err = fetch_data()
st.subheader("🔍 API 응답 원본 확인용(임시)")
st.json({"df_rows": 0 if df is None else len(df), "err": err})


# API 에러가 있으면 바로 표시하고 종료
if err:
    st.error(err)
    st.stop()

if df is None or df.empty:
    st.warning("표시할 수 있는 데이터가 없습니다.")
    st.stop()

# ----- 사이드바: 필터 -----
st.sidebar.header("검색 / 필터")

# 시/도 선택
sido_list = ["전체"] + sorted([s for s in df["시도"].dropna().unique()])
sido_sel = st.sidebar.selectbox("시/도 선택", options=sido_list, index=0)

# 병원명 검색
name_query = st.sidebar.text_input("병원명 검색", value="")

# 혼잡도 필터
cong_sel = st.sidebar.multiselect(
    "혼잡도 상태",
    options=["여유", "보통", "혼잡", "정보없음"],
    default=["여유", "보통", "혼잡"]
)

st.sidebar.markdown("---")
st.sidebar.subheader("내 위치(선택)")
lat_input = st.sidebar.text_input("위도 (예: 37.5665)")
lon_input = st.sidebar.text_input("경도 (예: 126.9780)")

use_location = False
my_latlon = None
try:
    if lat_input and lon_input:
        my_latlon = (float(lat_input), float(lon_input))
        use_location = True
except ValueError:
    st.sidebar.warning("위도/경도를 다시 확인해주세요. 예) 37.5665 / 126.9780")

# ----- 필터 적용 -----
df_f = df.copy()

if sido_sel != "전체":
    df_f = df_f[df_f["시도"] == sido_sel]

if name_query:
    df_f = df_f[df_f["dutyName"].str.contains(name_query, case=False, na=False)]

df_f = df_f[df_f["혼잡도"].isin(cong_sel)]

# 거리 계산 (내 위치가 있는 경우에만)
if use_location:
    df_f["거리_km"] = df_f.apply(
        lambda r: round(
            geodesic(my_latlon, (r["wgs84Lat"], r["wgs84Lon"])).km, 2
        ),
        axis=1
    )
else:
    df_f["거리_km"] = None

# ----- 추천 병원 TOP 5 -----
if use_location:
    st.subheader("⭐ 지금 기준으로 추천하는 병원 TOP 5")
    st.write("혼잡도지수(낮을수록 좋음)와 거리(가까울수록 좋음)를 함께 고려한 순서입니다.")

    df_rank = df_f.sort_values(
        ["혼잡도지수", "거리_km"],
        na_position="last"
    ).head(5)

    st.table(
        df_rank[["dutyName", "시도", "혼잡도", "혼잡도지수", "거리_km", "dutyTel3"]]
        .rename(columns={
            "dutyName": "병원명",
            "dutyTel3": "응급실 전화"
        })
    )
else:
    st.info("내 위치(위도·경도)를 입력하면, 혼잡도와 거리를 함께 고려한 추천 병원 TOP 5가 표시됩니다.")

# ----- 지도 표시 -----
st.subheader("🗺️ 병원 위치 지도")

if use_location:
    center = my_latlon
elif not df_f.empty:
    center = (df_f["wgs84Lat"].iloc[0], df_f["wgs84Lon"].iloc[0])
else:
    center = (37.5665, 126.9780)  # 기본: 서울시청

m = folium.Map(location=center, zoom_start=11)

# 내 위치 마커
if use_location:
    folium.Marker(
        my_latlon,
        tooltip="내 위치",
        icon=folium.Icon(icon="user", color="blue")
    ).add_to(m)


def color_of(label: str) -> str:
    return {
        "여유": "green",
        "보통": "orange",
        "혼잡": "red",
        "정보없음": "gray"
    }.get(label, "gray")


for _, row in df_f.iterrows():
    popup = f"""
    <b>{row['dutyName']}</b><br>
    주소: {row['dutyAddr']}<br>
    전화: {row['dutyTel3']}<br>
    가용 병상: {int(row['hvec']) if pd.notna(row['hvec']) else 'N/A'}<br>
    재원 환자: {int(row['hvoc']) if pd.notna(row['hvoc']) else 'N/A'}<br>
    혼잡도지수: {row['혼잡도지수']} ({row['혼잡도']})<br>
    업데이트: {row['업데이트']}
    """
    folium.CircleMarker(
        location=[row["wgs84Lat"], row["wgs84Lon"]],
        radius=7,
        color=color_of(row["혼잡도"]),
        fill=True,
        fill_opacity=0.85,
        popup=popup,
        tooltip=row["dutyName"],
    ).add_to(m)

st_folium(m, width=1100, height=600)

with st.expander("색상 의미"):
    st.markdown(
        "- 🟢 **여유**: 혼잡도지수 < 0.5  \n"
        "- 🟠 **보통**: 0.5 ≤ 혼잡도지수 < 1.0  \n"
        "- 🔴 **혼잡**: 혼잡도지수 ≥ 1.0  \n"
        "- ⚪ **정보없음**: 계산 불가"
    )

# ----- 전체 병원 목록 -----
st.subheader("📋 전체 병원 목록")

df_view = df_f.sort_values(["혼잡도지수"])
st.dataframe(
    df_view[[
        "dutyName", "시도", "혼잡도", "혼잡도지수",
        "hvec", "hvoc", "거리_km", "dutyTel3", "업데이트"
    ]].rename(columns={
        "dutyName": "병원명",
        "hvec": "가용 병상",
        "hvoc": "재원 환자수",
        "dutyTel3": "응급실 전화"
    }),
    use_container_width=True
)

