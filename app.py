"""Dashboard klasemen Triatra Fresh 5.0.

Alur: upload file Excel rekap -> sheet "Rekap All" dibersihkan -> disimpan ke TiDB
(tabel rekap_all diganti utuh) -> dashboard membaca ulang datanya lewat /api/data.
"""
import io
import os
import datetime as dt
from pathlib import Path

import certifi
import pandas as pd
from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import (Column, DateTime, Float, Integer, MetaData, String, Table,
                        create_engine, delete, func, insert, select)
from sqlalchemy.engine import URL

BASE = Path(__file__).parent
MAX_UPLOAD_MB = 10
UPLOAD_TOKEN = os.getenv("UPLOAD_TOKEN", "")  # kosong = upload tanpa token


# ---------- Koneksi database ----------
def make_engine():
    host = os.getenv("TIDB_HOST")
    if host:
        url = URL.create(
            "mysql+pymysql",
            username=os.getenv("TIDB_USER"),
            password=os.getenv("TIDB_PASSWORD"),
            host=host,
            port=int(os.getenv("TIDB_PORT", "4000")),
            database=os.getenv("TIDB_DATABASE", "test"),
        )
        ca = os.getenv("TIDB_CA_PATH") or certifi.where()
        engine = create_engine(
            url,
            connect_args={"ssl": {"ca": ca}},  # TiDB Cloud wajib TLS
            pool_pre_ping=True,
            pool_recycle=300,
        )
        return engine, "TiDB"
    # Tanpa kredensial TiDB: pakai SQLite lokal supaya bisa dicoba dulu
    return create_engine(f"sqlite:///{BASE / 'local_demo.db'}"), "SQLite lokal"


engine, DB_MODE = make_engine()
meta = MetaData()

rekap_all = Table(
    "rekap_all", meta,
    Column("site_area", String(255)),
    Column("nama", String(255)),
    Column("nrp", String(64)),
    Column("display_name", String(255)),
    Column("kontingen", String(100)),
    Column("run", Float(53)),
    Column("walk", Float(53)),
    Column("total", Float(53)),
    Column("status_run", String(50)),
    Column("status_walk", String(50)),
)

upload_log = Table(
    "upload_log", meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("filename", String(255)),
    Column("row_count", Integer),
    Column("col_count", Integer),
    Column("cutoff", String(20)),
    Column("uploaded_at", DateTime),
)

meta.create_all(engine)

# ---------- Pembersihan data ----------
INVISIBLE = {0x200B: None, 0x200C: None, 0x200D: None, 0xFEFF: None, 0x00A0: " "}
HEADER_MAP = {
    "site / area asal": "site_area",
    "nama": "nama",
    "nrp": "nrp",
    "display name strava": "display_name",
    "kontingen": "kontingen",
    "run": "run",
    "walk": "walk",
    "total": "total",
    "status run": "status_run",
    "status walk": "status_walk",
}
REQUIRED = ["site_area", "nama", "run", "walk"]


def clean_text(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).translate(INVISIBLE).strip()
    if s.endswith(".0") and s[:-2].isdigit():  # NRP terbaca sebagai angka
        s = s[:-2]
    return s or None


def norm_header(c):
    return " ".join(str(c).replace("\n", " ").split()).lower()


def find_sheet(xls, name):
    for s in xls.sheet_names:
        if s.strip().lower() == name.lower():
            return s
    return None


def parse_workbook(content: bytes):
    try:
        xls = pd.ExcelFile(io.BytesIO(content))
    except Exception:
        raise HTTPException(400, "File tidak bisa dibaca. Pastikan formatnya .xlsx.")

    sheet = find_sheet(xls, "Rekap All")
    if not sheet:
        raise HTTPException(400, f"Sheet 'Rekap All' tidak ditemukan. Sheet yang ada: {', '.join(xls.sheet_names)}.")

    df = xls.parse(sheet)
    df.columns = [HEADER_MAP.get(norm_header(c), norm_header(c)) for c in df.columns]
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise HTTPException(400, f"Kolom wajib tidak ditemukan di 'Rekap All': {', '.join(missing)}.")

    for col in HEADER_MAP.values():
        if col not in df.columns:
            df[col] = None
    df = df[list(HEADER_MAP.values())].copy()

    for col in ["site_area", "nama", "nrp", "display_name", "kontingen", "status_run", "status_walk"]:
        df[col] = df[col].map(clean_text)
    for col in ["run", "walk", "total"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["run"] = df["run"].fillna(0.0)
    df["walk"] = df["walk"].fillna(0.0)
    df["total"] = df["total"].fillna(df["run"] + df["walk"])

    df = df[df["nama"].notna()].reset_index(drop=True)
    if df.empty:
        raise HTTPException(400, "Sheet 'Rekap All' tidak berisi baris data.")

    # Cut-off diambil dari tanggal aktivitas terbaru di Rekap Walk / Rekap Run
    cutoff = None
    for name in ("Rekap Walk", "Rekap Run"):
        s = find_sheet(xls, name)
        if not s:
            continue
        d = xls.parse(s)
        col = next((c for c in d.columns if str(c).strip().lower() == "date"), None)
        if col is not None:
            mx = pd.to_datetime(d[col], errors="coerce").max()
            if pd.notna(mx) and (cutoff is None or mx > cutoff):
                cutoff = mx
    return df, (cutoff.date().isoformat() if cutoff is not None else None)


# ---------- API ----------
app = FastAPI(title="Dashboard Klasemen Triatra Fresh 5.0")


@app.get("/api/health")
def health():
    with engine.connect() as c:
        c.execute(select(1))
    return {"ok": True, "db": DB_MODE}


@app.get("/api/data")
def get_data():
    with engine.connect() as c:
        rows = c.execute(select(rekap_all)).mappings().all()
        last = c.execute(select(upload_log).order_by(upload_log.c.id.desc()).limit(1)).mappings().first()
    data = [
        {
            "area": r["site_area"] or "Tanpa area",
            "nama": r["nama"],
            "kontingen": r["kontingen"],
            "run": round(r["run"] or 0, 2),
            "walk": round(r["walk"] or 0, 2),
            "total": round(r["total"] or 0, 2),
        }
        for r in rows
    ]
    return {
        "rows": data,
        "meta": {
            "table": "rekap_all",
            "rowCount": len(data),
            "colCount": len(rekap_all.columns),
            "db": DB_MODE,
            "filename": last["filename"] if last else None,
            "cutoff": last["cutoff"] if last else None,
            "uploadedAt": last["uploaded_at"].isoformat() if last else None,
            "needsToken": bool(UPLOAD_TOKEN),
        },
    }


@app.post("/api/upload")
async def upload(file: UploadFile = File(...), x_upload_token: str = Header(default="")):
    if UPLOAD_TOKEN and x_upload_token != UPLOAD_TOKEN:
        raise HTTPException(401, "Token upload salah atau belum diisi.")
    if not (file.filename or "").lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "Hanya file .xlsx yang diterima.")
    content = await file.read()
    if len(content) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"Ukuran file melebihi {MAX_UPLOAD_MB} MB.")

    df, cutoff = parse_workbook(content)
    records = df.astype(object).where(df.notna(), None).to_dict("records")
    now = dt.datetime.now()

    with engine.begin() as c:  # satu transaksi: gagal di tengah = data lama tetap utuh
        c.execute(delete(rekap_all))
        c.execute(insert(rekap_all), records)
        c.execute(insert(upload_log).values(
            filename=file.filename, row_count=len(records),
            col_count=len(rekap_all.columns), cutoff=cutoff, uploaded_at=now))
    return {"ok": True, "rows": len(records), "cols": len(rekap_all.columns), "cutoff": cutoff}


@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html")
