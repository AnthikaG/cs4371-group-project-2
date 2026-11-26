"""Task 6: best month to visit Seattle.

Combines the Seattle rows of city_temperature.csv (Homework 2/4 data) with the
Task 1 table and ranks the months.

Reads  data/city_temperature.csv, outputs/neighbourhood_month.parquet
Writes outputs/t6_best_months.csv

Run from the repo root:
    python src/t6_best_month.py

Scoring rule (explain this in the report):
    temp_score     = 1 - (distance from 70 F, scaled 0-1 across the 12 months)
    incident_score = 1 - (average monthly incidents, scaled 0-1 across the 12 months)
    score          = 0.7 * temp_score + 0.3 * incident_score
Temperature gets more weight because it changes far more across the year in
Seattle than the city-wide incident total does, and it affects every visitor.
"""
from pathlib import Path

import pandas as pd

TEMP_FILE = "data/city_temperature.csv"
T1_TABLE = "outputs/neighbourhood_month.parquet"
OUT = Path("outputs/t6_best_months.csv")
COMFORT_F = 70.0
W_TEMP, W_INCIDENTS = 0.7, 0.3
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def best_months():
    # Temperature: average of daily values per month, 2020-2024.
    t = pd.read_csv(TEMP_FILE)
    t = t[(t["City"] == "Seattle") & (t["State"] == "Washington")]
    t = t[t["Year"].between(2020, 2024) & (t["AvgTemperature"] > -90)]  # -99 = missing
    temp = t.groupby("Month")["AvgTemperature"].mean().rename("avg_temp_f")

    # Incidents: city total per year-month, then averaged over 2022-2024.
    nm = pd.read_parquet(T1_TABLE)
    city = nm.groupby(["year", "month"])["total"].sum().reset_index()
    inc = city.groupby("month")["total"].mean().rename("avg_monthly_incidents")

    df = pd.concat([temp, inc], axis=1).reset_index().rename(columns={"index": "month"})
    df["month"] = df["month"].astype(int)
    df["month_name"] = df["month"].map(lambda m: MONTH_NAMES[m - 1])
    df["distance_from_70f"] = (df["avg_temp_f"] - COMFORT_F).abs()

    def scale(s):
        return (s - s.min()) / (s.max() - s.min()) if s.max() > s.min() else s * 0

    df["temp_score"] = 1 - scale(df["distance_from_70f"])
    df["incident_score"] = 1 - scale(df["avg_monthly_incidents"])
    df["score"] = W_TEMP * df["temp_score"] + W_INCIDENTS * df["incident_score"]
    df = df.sort_values("score", ascending=False).reset_index(drop=True)
    df["rank"] = df.index + 1
    df["recommended"] = df["rank"] <= 3

    cols = ["rank", "month", "month_name", "avg_temp_f", "distance_from_70f",
            "avg_monthly_incidents", "temp_score", "incident_score", "score", "recommended"]
    return df[cols].round(2)


def main():
    df = best_months()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False)
    print(df.to_string(index=False))
    print("\nTop 3 months to visit:", ", ".join(df.head(3)["month_name"]))


if __name__ == "__main__":
    main()
