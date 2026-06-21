"""
Equity analysis: enforcement intensity vs. economic activity by BBMP ward.

Hypothesis to test: at equal road criticality, are higher-activity (richer) wards
getting disproportionate enforcement vs. lower-income wards? We proxy economic
activity with VIIRS night-light radiance (current-year, free — better than the
13-year-old census), aggregated per ward.

This runs NOW for the spatial half (violations per ward, per km²) on BBMP.geojson
+ the violations CSV. The radiance half needs a VIIRS raster you download from
NASA Earthdata (auth required) — pass it with --viirs and `pip install rasterio`.
Without it, radiance is null and the equity correlation is reported as pending.

VIIRS download (one tile, monthly composite):
  https://eogdata.mines.edu/products/vnl/  (or earthdata.nasa.gov VNP46A3)
  Clip to Bengaluru bbox and pass the GeoTIFF path.

Run:  python viirs_equity.py [--viirs path/to/viirs.tif]
Out:  equity.json
"""
import argparse, json, os
import numpy as np
import pandas as pd


def _find(paths):
    for p in paths:
        if p and os.path.exists(p):
            return p
    return None


def ward_violation_density(wards_path, csv_path):
    """Per-ward violation count and density (violations / km²)."""
    import geopandas as gpd
    from shapely.geometry import Point
    wards = gpd.read_file(wards_path).to_crs(4326)
    name_col = "KGISWardName" if "KGISWardName" in wards.columns else wards.columns[0]
    wards = wards.rename(columns={name_col: "ward_name"})

    df = pd.read_csv(csv_path, usecols=["latitude", "longitude"], low_memory=False).dropna()
    pts = gpd.GeoDataFrame(geometry=[Point(x, y) for x, y in zip(df.longitude, df.latitude)], crs=4326)
    joined = gpd.sjoin(pts, wards[["ward_name", "geometry"]], how="inner", predicate="within")
    counts = joined.groupby("ward_name").size().rename("violations")

    # area in km² (equal-area projection)
    area_km2 = wards.to_crs(6933).geometry.area / 1e6
    wards["area_km2"] = area_km2.values
    out = wards[["ward_name", "area_km2"]].drop_duplicates("ward_name").merge(counts, on="ward_name", how="left")
    out["violations"] = out["violations"].fillna(0).astype(int)
    out["density_per_km2"] = (out["violations"] / out["area_km2"]).round(1)
    return wards, out


def add_radiance(wards_gdf, out, viirs_path):
    """Mean VIIRS radiance per ward (zonal mean). Requires rasterio + a raster."""
    if not viirs_path:
        out["radiance"] = None
        return out
    try:
        import rasterio
        from rasterio.mask import mask
    except Exception:
        print("⚠ rasterio not installed — skipping radiance. `pip install rasterio`")
        out["radiance"] = None
        return out
    rad = {}
    with rasterio.open(viirs_path) as src:
        wards_r = wards_gdf.to_crs(src.crs)
        for _, w in wards_r.iterrows():
            try:
                arr, _ = mask(src, [w.geometry], crop=True, nodata=np.nan)
                vals = arr[~np.isnan(arr)]
                rad[w["ward_name"]] = float(np.nanmean(vals)) if vals.size else None
            except Exception:
                rad[w["ward_name"]] = None
    out["radiance"] = out["ward_name"].map(rad)
    return out


def equity_report(out):
    """Compare enforcement intensity vs radiance percentile; flag potential inequity."""
    res = {"wards": int(len(out)), "radiance_available": bool(out["radiance"].notna().any())}
    if res["radiance_available"]:
        d = out.dropna(subset=["radiance"]).copy()
        d["radiance_pct"] = d["radiance"].rank(pct=True)
        d["enforce_pct"] = d["density_per_km2"].rank(pct=True)
        # under-enforced low-activity wards: low radiance pct but also low enforcement
        d["equity_gap"] = (d["radiance_pct"] - d["enforce_pct"]).round(2)
        res["radiance_enforcement_corr"] = round(float(d["radiance"].corr(d["density_per_km2"])), 3)
        res["most_under_enforced_low_activity"] = (
            d.sort_values("equity_gap").head(5)[["ward_name", "radiance", "density_per_km2"]]
             .to_dict("records"))
    else:
        res["note"] = ("Radiance pending — supply a VIIRS raster (--viirs) to compute the "
                       "income-vs-enforcement equity correlation. Spatial density computed below.")
    res["top_enforced_wards"] = (out.sort_values("density_per_km2", ascending=False)
                                 .head(10)[["ward_name", "violations", "density_per_km2"]]
                                 .to_dict("records"))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--viirs", default=None, help="VIIRS night-light GeoTIFF (optional)")
    ap.add_argument("--wards", default=None)
    ap.add_argument("--csv", default=None)
    args = ap.parse_args()

    wards_path = args.wards or _find(["../backend/BBMP.geojson", "backend/BBMP.geojson", "BBMP.geojson"])
    csv_path = args.csv or _find(["../jan to may police violation_anonymized791b166.csv",
                                  "jan to may police violation_anonymized791b166.csv"])
    if not wards_path or not csv_path:
        raise FileNotFoundError("Need BBMP.geojson and the violations CSV.")

    wards_gdf, out = ward_violation_density(wards_path, csv_path)
    out = add_radiance(wards_gdf, out, args.viirs)
    report = equity_report(out)
    dest = _find(["../frontend/public", "../model/outputs", "outputs", "."]) and \
        next(d for d in ["../frontend/public", "../model/outputs", "outputs", "."] if os.path.isdir(d))
    path = os.path.join(dest, "equity.json")
    json.dump(report, open(path, "w", encoding="utf-8"), indent=2)
    print(f"Wrote {path} | wards={report['wards']} | radiance={'yes' if report['radiance_available'] else 'pending (no VIIRS)'}")
    for w in report["top_enforced_wards"][:5]:
        print(f"  {w['density_per_km2']:7.1f}/km²  {w['ward_name']}")


if __name__ == "__main__":
    main()
