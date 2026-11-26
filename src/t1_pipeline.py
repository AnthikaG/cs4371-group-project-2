"""Task 1, steps 2-4: clean, classify and summarise the Seattle incidents with Spark.

Reads  data/raw/seattle_incidents_raw.parquet   (from t1_ingest.py)
Writes outputs/neighbourhood_month.parquet      (one row per neighbourhood, year, month)
       outputs/t1_summary.txt                   (row counts per cleaning step, etc.)

Run from the repo root:
    python src/t1_pipeline.py
"""
from pathlib import Path

from pyspark.sql import SparkSession, Window
from pyspark.sql import functions as F

RAW_IN = "data/raw/seattle_incidents_raw.parquet"
OUT_TABLE = "outputs/neighbourhood_month.parquet"
OUT_SUMMARY = Path("outputs/t1_summary.txt")

# Rough bounding box around the City of Seattle limits.
LAT_MIN, LAT_MAX = 47.49, 47.74
LON_MIN, LON_MAX = -122.44, -122.22

# Placeholder values Seattle uses when a field is unknown.
MISSING = ["", "-", "UNKNOWN", "NULL"]

# How the city's offence field maps to our three categories.
CATEGORY_MAP = {"PERSON": "persons", "PROPERTY": "property"}  # everything else -> "other"


def main():
    spark = (
        SparkSession.builder.appName("SafeStay-Task1")
        .config("spark.sql.session.timeZone", "UTC")  # treat the city's local times as-is
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("WARN")

    counts = []  # (step, rows after step)

    df = spark.read.parquet(RAW_IN)
    counts.append(("Raw rows downloaded", df.count()))

    # 1. Duplicates: Seattle has one row per offence, identified by offense_id.
    df = df.dropDuplicates(["offense_id"])
    counts.append(("After removing duplicate offense_id", df.count()))

    # 2. Rows without a date.
    df = df.withColumn("ts", F.expr("try_cast(offense_date AS TIMESTAMP)"))  # bad dates -> NULL
    df = df.filter(F.col("ts").isNotNull())
    counts.append(("After removing rows without a date", df.count()))

    # 3. Rows without a neighbourhood.
    df = df.withColumn("neighbourhood", F.upper(F.trim(F.col("neighborhood"))))
    df = df.filter(F.col("neighbourhood").isNotNull() & ~F.col("neighbourhood").isin(MISSING))
    counts.append(("After removing rows without a neighbourhood", df.count()))

    # 4. Rows without valid coordinates. Seattle uses "REDACTED", -1 or 0 for hidden
    #    locations; try_cast turns any text that isn't a number into NULL.
    df = (
        df.withColumn("lat", F.expr("try_cast(latitude AS DOUBLE)"))
          .withColumn("lon", F.expr("try_cast(longitude AS DOUBLE)"))
    )
    df = df.filter(
        F.col("lat").isNotNull() & F.col("lon").isNotNull()
        & (F.col("lat") != 0) & (F.col("lon") != 0)
        & (F.col("lat") != -1) & (F.col("lon") != -1)
    )
    counts.append(("After removing rows without valid coordinates", df.count()))

    # 5. Only incidents inside the city.
    df = df.filter(
        F.col("lat").between(LAT_MIN, LAT_MAX) & F.col("lon").between(LON_MIN, LON_MAX)
    )
    counts.append(("After keeping only incidents inside Seattle", df.count()))

    # Classify into persons / property / other.
    against = F.upper(F.trim(F.col("nibrs_crime_against_category")))
    cat_expr = F.lit("other")
    for source_value, category in CATEGORY_MAP.items():
        cat_expr = F.when(against == source_value, category).otherwise(cat_expr)
    df = df.withColumn("category", cat_expr)
    df = df.withColumn("offence", F.trim(F.col("nibrs_offense_code_description")))
    df = df.withColumn("year", F.year("ts")).withColumn("month", F.month("ts"))
    df.cache()

    mapping_rows = (
        df.groupBy("nibrs_crime_against_category", "category").count()
          .orderBy("category", F.desc("count")).collect()
    )

    # Totals per neighbourhood, year and month.
    totals = df.groupBy("neighbourhood", "year", "month").agg(
        F.count("*").alias("total"),
        F.sum(F.when(F.col("category") == "persons", 1).otherwise(0)).alias("persons"),
        F.sum(F.when(F.col("category") == "property", 1).otherwise(0)).alias("property"),
        F.sum(F.when(F.col("category") == "other", 1).otherwise(0)).alias("other"),
    )

    # Three most common offence types per neighbourhood, year and month.
    offence_counts = df.groupBy("neighbourhood", "year", "month", "offence").count()
    w = Window.partitionBy("neighbourhood", "year", "month").orderBy(
        F.desc("count"), F.asc("offence")
    )
    top3 = (
        offence_counts.withColumn("rank", F.row_number().over(w))
        .filter(F.col("rank") <= 3)
        .groupBy("neighbourhood", "year", "month")
        .agg(F.array_sort(F.collect_list(F.struct("rank", "offence"))).alias("ranked"))
        .withColumn("top_offences", F.concat_ws("; ", F.col("ranked.offence")))
        .drop("ranked")
    )

    # Every neighbourhood gets all 36 months, with 0 where nothing was reported,
    # so the averages in Tasks 2, 3 and 6 are not inflated by missing months.
    hoods = df.select("neighbourhood").distinct()
    months = spark.createDataFrame(
        [(y, m) for y in (2022, 2023, 2024) for m in range(1, 13)], ["year", "month"]
    )
    table = (
        hoods.crossJoin(months)
        .join(totals, ["neighbourhood", "year", "month"], "left")
        .join(top3, ["neighbourhood", "year", "month"], "left")
        .fillna(0, subset=["total", "persons", "property", "other"])
        .fillna("", subset=["top_offences"])
        .select("neighbourhood", "year", "month", "total",
                "persons", "property", "other", "top_offences")
        .orderBy("neighbourhood", "year", "month")
    )
    table.coalesce(1).write.mode("overwrite").parquet(OUT_TABLE)

    # Summary file.
    n_hoods = hoods.count()
    date_min, date_max = df.agg(
        F.date_format(F.min("ts"), "yyyy-MM-dd HH:mm"),
        F.date_format(F.max("ts"), "yyyy-MM-dd HH:mm"),
    ).first()
    lines = ["Task 1 summary: Seattle incident reports, 2022-2024", ""]
    lines.append("Row counts after each cleaning step:")
    prev = None
    for step, n in counts:
        removed = "" if prev is None else f"  (removed {prev - n:,})"
        lines.append(f"  {step}: {n:,}{removed}")
        prev = n
    lines += [
        "",
        f"Number of neighbourhoods: {n_hoods}",
        f"Date range of cleaned incidents: {date_min} to {date_max}",
        f"Rows in neighbourhood_month table: {table.count():,} ({n_hoods} neighbourhoods x 36 months)",
        "",
        "Category mapping (nibrs_crime_against_category -> category: incidents):",
    ]
    for r in mapping_rows:
        lines.append(f"  {r['nibrs_crime_against_category']} -> {r['category']}: {r['count']:,}")
    OUT_SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    OUT_SUMMARY.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))

    spark.stop()


if __name__ == "__main__":
    main()
