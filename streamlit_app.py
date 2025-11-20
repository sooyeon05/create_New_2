import streamlit as st
import pandas as pd
import requests
import folium
from streamlit_folium import st_folium
from geopy.distance import geodesic

st.set_page_config(
    page_title="응급실 실시간 대시보드",
    layout="wide"
)

# 🔑 API 키를 직접 코드 안에 넣는 버전
API_KEY = "QDn%2BQQQpAWC0wqa2shJaf9XYoa7b3vlTocPYBdHqHGCzau5S8XUbPXaxoq9HRgKHgZMIbQU7WCeflidd4I0MEA%3D%3D"

# API URL
BASE_URL = (
    "https://apis.data.go.kr/B552657/ErmctInfoInqireService/"
    "getEmrrmRltmUsefulSckbdInfoInqire"
)

def fetch_data(num_rows=200):
    """응급실 실시간 정보 API 호출"""
    url = f"{BASE_URL}?serviceKey={API_KEY}&_type=json&pageNo=1&numOfRows={num_rows}"

    try:
        r = requests.get(url, timeout=10)
    except Exception as e:
        return None, f"요청 자체가 실패했습니다: {e}"

    if r.status_code != 200:
        return None, f"HTTP 오류 {r.status_code}: {r.text[:300]}"

    try:
        js = r.json()
    except:
        return None, f"JSON 파싱 실패: {r.text[:300]}"

    items = js.get("response", {}).get("body", {}).get("items", {}).get("item")

    if not items:
        return None, "API는 정상 응답했지만 item 데이터가 없습니다."

    df = pd.DataFrame(items)

    # 숫자형 변환
    for c in ["hvec", "hvoc", "wgs84Lat", "wgs84Lon"]:
        df[c] = pd.to_numeric(df.get(c), errors="coerce")

    # 혼잡도 지수 계산
    df["혼잡도지수"] = (df["hvoc"] / (df["hvec"].fillna(0) + 1)).round(2)

    # 혼잡도 라벨
    def label_cong(x):
        if pd.isna(x): return "정보없음"
        if x < 0.5: return "여유"
        if x < 1: return "보통"
        return "혼잡"

    df["혼잡도"] = df["혼잡도지수"].apply(label_cong)

    # 시/도 컬럼
    def get_sido(addr):
        return addr.split()[0] if isinstance(addr, str) else None

    df["시도"] = df["dutyAddr"].apply(get_sido)

    df = df.dropna(subset=["wgs84Lat", "wgs84Lon"]).reset_index(drop=True)

    return df, None

# ---------------- UI 시작 ----------------
st.title("🏥 실시간 응급실 혼잡도 대시보드")

df, err = fetch_data()

if err:
    st.error(err)
    st.stop()

if df is None or df.empty:
    st.warning("표시할 수 있는 데이터가 없습니다.")
    st.stop()

# Sidebar filters
st.sidebar.header("필터")

sido_list = ["전체"] + sorted(df["시도"].dropna().unique())
sido_sel = st.sidebar.selectbox("시/도 선택", sido_list)

name_query = st.sidebar.text_input("병원명 검색")

cong_sel = st.sidebar.multiselect(
    "혼잡도 선택",
    ["여유", "보통", "혼잡", "정보없음"],
    default=["여유", "보통", "혼잡"]
)

# 내 위치
st.sidebar.subheader("내 위치(선택)")
lat_input = st.sidebar.text_input("위도")
lon_input = st.sidebar.text_input("경도")

use_location = False
if lat_input and lon_input:
    try:
        my_latlon = (float(lat_input), float(lon_input))
        use_location = True
    except:
        st.sidebar.warning("위도/경도 형식이 잘못되었습니다.")

# 필터링
df_f = df.copy()

if sido_sel != "전체":
    df_f = df_f[df_f["시도"] == sido_sel]

if name_query:
    df_f = df_f[df_f["dutyName"].str.contains(name_query, case=False, na=False)]

df_f = df_f[df_f["혼잡도"].isin(cong_sel)]

# 거리 계산
if use_location:
    df_f["거리_km"] = df_f.apply(
        lambda r: round(geodesic(my_latlon, (r["wgs84Lat"], r["wgs84Lon"])).km, 2),
        axis=1
    )
else:
    df_f["거리_km"] = None

# 추천 TOP 5
if use_location:
    st.subheader("⭐ 추천 병원 TOP 5 (혼잡도 + 거리 기준)")
    top5 = df_f.sort_values(["혼잡도지수", "거리_km"]).head(5)
    st.table(top5[["dutyName", "시도", "혼잡도", "혼잡도지수", "거리_km", "dutyTel3"]])
else:
    st.info("추천 병원을 보려면 위도/경도를 입력하세요.")

# 지도
st.subheader("🗺️ 병원 위치 지도")

if use_location:
    center = my_latlon
else:
    center = (37.5665, 126.9780)  # 서울시청 기준

m = folium.Map(location=center, zoom_start=11)

if use_location:
    folium.Marker(center, tooltip="내 위치").add_to(m)

def color(label):
    return {
        "여유": "green",
        "보통": "orange",
        "혼잡": "red",
        "정보없음": "gray"
    }.get(label, "blue")

for _, r in df_f.iterrows():
    folium.CircleMarker(
        location=[r["wgs84Lat"], r["wgs84Lon"]],
        radius=8,
        color=color(r["혼잡도"]),
        fill=True,
        popup=f"{r['dutyName']}<br>{r['dutyAddr']}<br>{r['dutyTel3']}<br>혼잡도 {r['혼잡도지수']}",
        tooltip=r["dutyName"]
    ).add_to(m)

st_folium(m, width=1050, height=600)

# 전체 표
st.subheader("📋 전체 병원 목록")
st.dataframe(df_f)


