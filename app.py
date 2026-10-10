from pathlib import Path
import io
import pandas as pd
import streamlit as st
import plotly.express as px
from src.analysis import standardise_columns, calculate_kpis, monthly_summary, pressure_periods, forecast_monthly

st.set_page_config(page_title="Hospital Capacity & Flow", page_icon="🏥", layout="wide")
st.title("🏥 Hospital Capacity, Bed Occupancy & Discharge")
st.caption("Operational insight using aggregate data • SQL + Python + Power BI portfolio project")

with st.sidebar:
    st.header("Data")
    uploaded = st.file_uploader("Upload Excel or CSV", type=["xlsx", "xls", "csv"])
    st.markdown("**Supported data:** bed capacity/occupancy and discharge activity. Use the sample dataset if you are exploring the dashboard.")
    use_sample = st.checkbox("Use included sample data", value=uploaded is None)

@st.cache_data
def load_sample():
    return pd.read_csv("data/sample_hospital_flow.csv")

if uploaded is not None:
    try:
        if uploaded.name.lower().endswith(".csv"):
            raw = pd.read_csv(uploaded)
        else:
            book = pd.ExcelFile(uploaded)
            sheet = st.sidebar.selectbox("Worksheet", book.sheet_names)
            raw = pd.read_excel(uploaded, sheet_name=sheet, header=None)
            # Find a likely header row rather than assuming row 1 is the header.
            header_idx = 0
            for i in range(min(12, len(raw))):
                vals = " ".join(str(x).lower() for x in raw.iloc[i].tolist() if pd.notna(x))
                if any(k in vals for k in ["date", "month", "period", "trust", "hospital", "occupied", "available", "discharge"]):
                    header_idx = i
                    break
            raw.columns = raw.iloc[header_idx].astype(str).str.strip()
            raw = raw.iloc[header_idx + 1:].reset_index(drop=True)
        df, mapping, notes = standardise_columns(raw)
        st.sidebar.success(f"Loaded {len(raw):,} rows")
    except Exception as e:
        st.error(f"Could not read this file: {e}")
        st.stop()
elif use_sample:
    raw = load_sample()
    df, mapping, notes = standardise_columns(raw)
else:
    st.info("Upload an aggregate operational dataset or select the sample data to begin.")
    st.stop()

with st.expander("Data quality and column mapping", expanded=bool(notes)):
    st.write("Detected source columns:")
    st.json(mapping)
    if notes:
        for note in notes:
            st.warning(note)
    st.dataframe(df.head(20), use_container_width=True)

if df.empty or "period" not in df.columns:
    st.error("No usable date/period column was detected. Rename a source column to Date, Month, Period, or Reporting Period and upload again.")
    st.stop()

df["period"] = pd.to_datetime(df["period"], errors="coerce")
df = df.dropna(subset=["period"]).copy()
numeric = ["beds_available", "beds_occupied", "admissions", "discharges", "long_stay_7plus"]
for c in numeric:
    df[c] = pd.to_numeric(df[c], errors="coerce")
df["beds_available"] = df["beds_available"].where(df["beds_available"] > 0)
df["occupancy_pct"] = (df["beds_occupied"] / df["beds_available"] * 100).where(df["beds_available"].notna())
df["net_flow"] = df["admissions"] - df["discharges"]

if df.empty:
    st.error("No valid dates were found after parsing. Check the worksheet and date format.")
    st.stop()

org_col = "organisation" if "organisation" in df.columns else None
specialty_col = "specialty" if "specialty" in df.columns else None
with st.sidebar:
    min_date, max_date = df["period"].min().date(), df["period"].max().date()
    date_range = st.date_input("Reporting period", value=(min_date, max_date), min_value=min_date, max_value=max_date)
    orgs = ["All"] + sorted(df[org_col].dropna().astype(str).unique().tolist()) if org_col else ["All"]
    org = st.selectbox("Organisation", orgs)
    specs = ["All"] + sorted(df[specialty_col].dropna().astype(str).unique().tolist()) if specialty_col else ["All"]
    spec = st.selectbox("Specialty", specs)

if isinstance(date_range, tuple) and len(date_range) == 2:
    df = df[df["period"].dt.date.between(date_range[0], date_range[1])]
if org != "All" and org_col:
    df = df[df[org_col].astype(str) == org]
if spec != "All" and specialty_col:
    df = df[df[specialty_col].astype(str) == spec]

if df.empty:
    st.warning("No rows match these filters.")
    st.stop()

k = calculate_kpis(df)
cols = st.columns(5)
for col, label, value in zip(cols,
    ["Avg. occupancy", "Available beds", "Occupied beds", "Discharges", "Admissions − discharges"],
    [f'{k["occupancy_pct"]:.1f}%' if pd.notna(k["occupancy_pct"]) else "N/A",
     f'{k["beds_available"]:,.0f}' if pd.notna(k["beds_available"]) else "N/A",
     f'{k["beds_occupied"]:,.0f}' if pd.notna(k["beds_occupied"]) else "N/A",
     f'{k["discharges"]:,.0f}' if pd.notna(k["discharges"]) else "N/A",
     f'{k["net_flow"]:,.0f}' if pd.notna(k["net_flow"]) else "N/A"]):
    col.metric(label, value)

monthly = monthly_summary(df)
st.subheader("Capacity and flow over time")
left, right = st.columns(2)
if "occupancy_pct" in monthly and monthly["occupancy_pct"].notna().any():
    left.plotly_chart(px.line(monthly, x="period", y="occupancy_pct", markers=True, title="Bed occupancy (%)"), use_container_width=True)
else:
    left.info("Occupancy chart needs both occupied and available bed data.")
flow_cols = [c for c in ["admissions", "discharges"] if c in monthly and monthly[c].notna().any()]
if flow_cols:
    right.plotly_chart(px.line(monthly, x="period", y=flow_cols, markers=True, title="Admissions and discharges"), use_container_width=True)
else:
    right.info("Flow chart needs admissions and/or discharge counts.")

left, right = st.columns(2)
if monthly["beds_available"].notna().any() and monthly["beds_occupied"].notna().any():
    left.plotly_chart(px.area(monthly, x="period", y=["beds_occupied", "beds_available"], title="Occupied vs available bed indicators"), use_container_width=True)
else:
    left.info("Capacity comparison needs available and occupied bed counts.")
if "discharges" in monthly and monthly["discharges"].notna().any():
    right.plotly_chart(px.bar(monthly, x="period", y="discharges", title="Monthly discharges"), use_container_width=True)
else:
    right.info("No discharge values detected.")

st.subheader("Recurring pressure periods")
press = pressure_periods(monthly)
if not press.empty:
    st.dataframe(press, use_container_width=True)
    st.caption("Pressure flag is an analytical screening rule, not an official NHS threshold: occupancy ≥ 90% where occupancy is calculable.")
else:
    st.info("Pressure periods cannot be calculated without occupancy data.")

st.subheader("Trend projection")
horizon = st.slider("Months to project", min_value=3, max_value=18, value=6)
target = st.selectbox("Metric", ["occupancy_pct", "discharges", "admissions", "beds_occupied"], index=0)
forecast = forecast_monthly(monthly, target, horizon)
if not forecast.empty:
    hist = monthly[["period", target]].dropna() if target in monthly else pd.DataFrame()
    if not hist.empty:
        hist = hist.rename(columns={target: "value"}); hist["type"] = "Observed"
        forecast_plot = forecast.rename(columns={"forecast": "value"}); forecast_plot["type"] = "Forecast"
        plot_df = pd.concat([hist[["period", "value", "type"]], forecast_plot[["period", "value", "type"]]], ignore_index=True)
        st.plotly_chart(px.line(plot_df, x="period", y="value", color="type", markers=True, title=f"{target.replace('_',' ').title()} — simple trend projection"), use_container_width=True)
        st.caption("Forecast is a simple linear trend baseline for portfolio demonstration, not an operational prediction. Validate against seasonality and later periods before use.")
else:
    st.info(f"Forecast unavailable for {target}: at least 3 valid monthly observations are needed.")

st.subheader("Export analysed data")
csv = df.to_csv(index=False).encode("utf-8")
st.download_button("Download filtered dataset (CSV)", data=csv, file_name="hospital_capacity_flow_analysis.csv", mime="text/csv")
st.download_button("Download monthly KPI table (CSV)", data=monthly.to_csv(index=False).encode("utf-8"), file_name="monthly_hospital_kpis.csv", mime="text/csv")

st.markdown("---")
st.caption("Use aggregate, non-identifiable data only. Confirm definitions, reporting periods, and data quality with the source owner before operational use.")
