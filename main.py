from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, date
import hashlib
import jwt
import os

# PostgreSQL veya SQLite
DATABASE_URL = os.environ.get("DATABASE_URL", "")
USE_PG = DATABASE_URL.startswith("postgresql") or DATABASE_URL.startswith("postgres")

if USE_PG:
    import pg8000.dbapi as pg_dbapi
else:
    import sqlite3

app = FastAPI(title="Labin Yapi Lab API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_origin_regex=".*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SECRET_KEY = os.environ.get("SECRET_KEY", "labin_gizli_anahtar_2024")
security = HTTPBearer()

# ── Veritabani ────────────────────────────────────────────────────────────────
def get_db():
    if USE_PG:
        import urllib.parse
        r = urllib.parse.urlparse(DATABASE_URL)
        conn = pg_dbapi.connect(
            host=r.hostname,
            port=r.port or 5432,
            database=r.path.lstrip('/'),
            user=r.username,
            password=r.password
        )
        conn.autocommit = False
        return conn
    else:
        import sqlite3 as _sq
        _data_dir = "/data" if os.path.isdir("/data") else "."
        db_path = os.path.join(_data_dir, "labin.db")
        conn = _sq.connect(db_path)
        conn.row_factory = _sq.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

def row_to_dict(row):
    """Hem sqlite3.Row hem psycopg2 satirini dict'e cevir"""
    if row is None: return None
    if USE_PG:
        return dict(row)
    return dict(row)

def fetchone_dict(cursor):
    if USE_PG:
        row = cursor.fetchone()
        if row is None: return None
        cols = [d[0] for d in cursor.description]
        return dict(zip(cols, row))
    r = cursor.fetchone()
    return dict(r) if r else None

def fetchall_dict(cursor):
    if USE_PG:
        rows = cursor.fetchall()
        if not rows: return []
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, r)) for r in rows]
    return cursor.fetchall()

def ph(n=1):
    """Placeholder: SQLite=?, PostgreSQL=%s"""
    if USE_PG: return ','.join(['%s']*n) if n>1 else '%s'
    return ','.join(['?']*n) if n>1 else '?'

def adapt_sql(sql):
    """SQLite sorgusunu PostgreSQL'e uyarla"""
    if not USE_PG: return sql
    import re
    sql = sql.replace('?', '%s')
    sql = sql.replace('INTEGER PRIMARY KEY AUTOINCREMENT', 'SERIAL PRIMARY KEY')
    sql = sql.replace('INSERT OR IGNORE', 'INSERT')
    sql = sql.replace('INSERT OR REPLACE', 'INSERT')
    sql = re.sub(r"DEFAULT \(datetime\('now'\)\)", "DEFAULT NOW()", sql)
    sql = re.sub(r"DEFAULT \(date\('now'\)\)", "DEFAULT NOW()", sql)
    sql = re.sub(r"COALESCE\(SUM\((\w+)\),0\)", r"COALESCE(SUM(),0)", sql)
    return sql

def son_guncelleme_guncelle(conn):
    simdi = datetime.now().strftime("%d.%m.%Y %H:%M")
    if USE_PG:
        c = conn.cursor()
        c.execute("INSERT INTO ayarlar (anahtar, deger) VALUES (%s, %s) ON CONFLICT (anahtar) DO UPDATE SET deger=EXCLUDED.deger", ('son_guncelleme', simdi))
    else:
        conn.execute("INSERT OR REPLACE INTO ayarlar (anahtar, deger) VALUES (?, ?)", ('son_guncelleme', simdi))

def init_db():
    conn = get_db()
    c = conn.cursor()
    auto = "SERIAL" if USE_PG else "INTEGER"
    pk = f"{auto} PRIMARY KEY" + ("" if USE_PG else " AUTOINCREMENT")

    tablos = [
        f"""CREATE TABLE IF NOT EXISTS kullanicilar (
            id {pk},
            kullanici_adi TEXT UNIQUE NOT NULL,
            sifre_hash TEXT NOT NULL,
            ad TEXT NOT NULL,
            rol TEXT DEFAULT 'kullanici',
            aktif INTEGER DEFAULT 1,
            kayit_tarihi TEXT DEFAULT CURRENT_DATE
        )""",
        f"""CREATE TABLE IF NOT EXISTS musteriler (
            id {pk},
            tip TEXT NOT NULL,
            firma TEXT NOT NULL,
            yetkili TEXT, telefon TEXT, eposta TEXT,
            vergino TEXT, adres TEXT, belediye TEXT,
            kayit_tarihi TEXT DEFAULT CURRENT_DATE
        )""",
        f"""CREATE TABLE IF NOT EXISTS musteri_fiyatlar (
            id {pk},
            musteri_id INTEGER,
            taze_beton REAL DEFAULT 0,
            celik REAL DEFAULT 0,
            karot REAL DEFAULT 0,
            tarih TEXT DEFAULT CURRENT_DATE
        )""",
        f"""CREATE TABLE IF NOT EXISTS numuneler (
            id {pk},
            tur TEXT NOT NULL,
            musteri_id INTEGER,
            musteri_adi TEXT,
            tarih TEXT NOT NULL,
            yibf TEXT, belediye TEXT, blok TEXT, kat TEXT,
            m3 TEXT, beton_sinifi TEXT, caplar TEXT,
            adet INTEGER DEFAULT 1,
            birim_fiyat REAL DEFAULT 0,
            kdv_oran REAL DEFAULT 20,
            kdv_tutar REAL DEFAULT 0,
            toplam REAL DEFAULT 0,
            toplam_kdvli REAL DEFAULT 0,
            durum TEXT DEFAULT 'Beklemede',
            not_ TEXT,
            is_adi TEXT DEFAULT '',
            olusturma TEXT DEFAULT CURRENT_TIMESTAMP
        )""",
        f"""CREATE TABLE IF NOT EXISTS gelirler (
            id {pk},
            tarih TEXT NOT NULL,
            aciklama TEXT NOT NULL,
            musteri_id INTEGER,
            musteri_adi TEXT,
            tutar REAL NOT NULL,
            odeme_turu TEXT DEFAULT 'Nakit',
            olusturma TEXT DEFAULT CURRENT_TIMESTAMP
        )""",
        f"""CREATE TABLE IF NOT EXISTS giderler (
            id {pk},
            tarih TEXT NOT NULL,
            kategori TEXT NOT NULL,
            aciklama TEXT NOT NULL,
            arac TEXT,
            tutar REAL NOT NULL,
            olusturma TEXT DEFAULT CURRENT_TIMESTAMP
        )""",
        f"""CREATE TABLE IF NOT EXISTS personeller (
            id {pk},
            ad TEXT NOT NULL,
            telefon TEXT, iban TEXT, gorev TEXT,
            maas REAL DEFAULT 0,
            ise_giris TEXT, cikis_tarihi TEXT,
            durum TEXT DEFAULT 'Aktif',
            kayit_tarihi TEXT DEFAULT CURRENT_DATE
        )""",
        f"""CREATE TABLE IF NOT EXISTS cek_senetler (
            id {pk},
            vade TEXT NOT NULL,
            tur TEXT DEFAULT 'Cek',
            musteri_adi TEXT, banka TEXT, no TEXT,
            tutar REAL NOT NULL,
            durum TEXT DEFAULT 'Beklemede',
            kayit TEXT DEFAULT CURRENT_DATE
        )""",
        f"""CREATE TABLE IF NOT EXISTS beton_programi (
            id {pk},
            tarih TEXT NOT NULL,
            saat TEXT, musteri_adi TEXT, yibf TEXT,
            belediye TEXT, blok TEXT, kat TEXT,
            m3 TEXT, beton_sinifi TEXT, not_ TEXT,
            olusturma TEXT DEFAULT CURRENT_TIMESTAMP
        )""",
        f"""CREATE TABLE IF NOT EXISTS araclar (
            id {pk},
            plaka TEXT NOT NULL,
            model TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS ayarlar (
            anahtar TEXT PRIMARY KEY,
            deger TEXT
        )""",
    ]

    for sql in tablos:
        try:
            c.execute(sql)
            conn.commit()
        except Exception as e:
            print(f"Tablo hatasi: {e}")
            try: conn.rollback()
            except: pass

    # Migration - mevcut tablolara eksik kolonlari ekle
    for migration in [
        "ALTER TABLE numuneler ADD COLUMN is_adi TEXT DEFAULT ''",
        "ALTER TABLE ayarlar ADD COLUMN deger TEXT",
    ]:
        try: c.execute(migration)
        except: pass

    # Varsayilan ayar
    try:
        if USE_PG:
            c.execute("INSERT INTO ayarlar (anahtar, deger) VALUES (%s, %s) ON CONFLICT (anahtar) DO NOTHING", ('son_guncelleme', ''))
        else:
            c.execute("INSERT OR IGNORE INTO ayarlar (anahtar, deger) VALUES (?, ?)", ('son_guncelleme', ''))
        conn.commit()
    except Exception:
        conn.rollback()

    # Admin kullanici
    admin_hash = hashlib.sha256("101112da".encode()).hexdigest()
    try:
        if USE_PG:
            c.execute("INSERT INTO kullanicilar (kullanici_adi,sifre_hash,ad,rol) VALUES (%s,%s,%s,%s) ON CONFLICT (kullanici_adi) DO NOTHING",
                      ("labin", admin_hash, "Muhammed Yardimci", "admin"))
        else:
            c.execute("INSERT OR IGNORE INTO kullanicilar (kullanici_adi,sifre_hash,ad,rol) VALUES (?,?,?,?)",
                      ("labin", admin_hash, "Muhammed Yardimci", "admin"))
        conn.commit()
    except Exception:
        conn.rollback()

    conn.close()

init_db()

# ── JWT ───────────────────────────────────────────────────────────────────────
def token_olustur(kullanici_id: int, kullanici_adi: str, rol: str) -> str:
    payload = {
        "sub": str(kullanici_id),
        "kullanici_adi": kullanici_adi,
        "rol": rol,
        "iat": datetime.utcnow().timestamp()
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")

def token_dogrula(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=["HS256"])
        return payload
    except Exception:
        raise HTTPException(status_code=401, detail="Gecersiz token")

def admin_kontrol(token=Depends(token_dogrula)):
    if token.get("rol") != "admin":
        raise HTTPException(status_code=403, detail="Bu islem icin admin yetkisi gerekli")
    return token

# ── MODELLER ──────────────────────────────────────────────────────────────────
class GirisModel(BaseModel):
    kullanici_adi: str
    sifre: str

class MusteriModel(BaseModel):
    tip: str
    firma: str
    yetkili: Optional[str] = ""
    telefon: Optional[str] = ""
    eposta: Optional[str] = ""
    vergino: Optional[str] = ""
    adres: Optional[str] = ""
    belediye: Optional[str] = ""
    taze_beton: Optional[float] = 0
    celik: Optional[float] = 0
    karot: Optional[float] = 0

class NumuneModel(BaseModel):
    tur: str
    musteri_id: Optional[int] = None
    musteri_adi: Optional[str] = ""
    tarih: str
    yibf: Optional[str] = ""
    belediye: Optional[str] = ""
    blok: Optional[str] = ""
    kat: Optional[str] = ""
    m3: Optional[str] = ""
    beton_sinifi: Optional[str] = ""
    caplar: Optional[str] = ""
    adet: int = 1
    birim_fiyat: float = 0
    kdv_oran: float = 20
    kdv_tutar: float = 0
    toplam: float = 0
    toplam_kdvli: float = 0
    durum: Optional[str] = "Beklemede"
    not_: Optional[str] = ""
    is_adi: Optional[str] = ""

class GelirModel(BaseModel):
    tarih: str
    aciklama: str
    musteri_id: Optional[int] = None
    musteri_adi: Optional[str] = ""
    tutar: float
    odeme_turu: Optional[str] = "Nakit"

class GiderModel(BaseModel):
    tarih: str
    kategori: str
    aciklama: str
    arac: Optional[str] = ""
    tutar: float

class PersonelModel(BaseModel):
    ad: str
    telefon: Optional[str] = ""
    iban: Optional[str] = ""
    gorev: Optional[str] = ""
    maas: Optional[float] = 0
    ise_giris: Optional[str] = ""
    cikis_tarihi: Optional[str] = ""
    durum: Optional[str] = "Aktif"

class CekSenetModel(BaseModel):
    vade: str
    tur: Optional[str] = "Cek"
    musteri_adi: Optional[str] = ""
    banka: Optional[str] = ""
    no: Optional[str] = ""
    tutar: float
    durum: Optional[str] = "Beklemede"

class BetonProgramModel(BaseModel):
    tarih: str
    saat: Optional[str] = ""
    musteri_adi: Optional[str] = ""
    yibf: Optional[str] = ""
    belediye: Optional[str] = ""
    blok: Optional[str] = ""
    kat: Optional[str] = ""
    m3: Optional[str] = ""
    beton_sinifi: Optional[str] = ""
    not_: Optional[str] = ""
    is_adi: Optional[str] = ""

class DurumModel(BaseModel):
    durum: str

class KullaniciModel(BaseModel):
    kullanici_adi: str
    sifre: str
    ad: str
    rol: Optional[str] = "kullanici"

# ── AUTH ──────────────────────────────────────────────────────────────────────
@app.post("/giris")
def giris(data: GirisModel):
    conn = get_db()
    sifre_hash = hashlib.sha256(data.sifre.encode()).hexdigest()
    c = conn.cursor()
    c.execute(adapt_sql("SELECT * FROM kullanicilar WHERE kullanici_adi=? AND sifre_hash=? AND aktif=1"),
              (data.kullanici_adi, sifre_hash))
    k = fetchone_dict(c)
    conn.close()
    if not k:
        raise HTTPException(status_code=401, detail="Kullanici adi veya sifre yanlis")
    token = token_olustur(k["id"], k["kullanici_adi"], k["rol"])
    return {"token": token, "kullanici": {"id": k["id"], "ad": k["ad"],
            "kullanici_adi": k["kullanici_adi"], "rol": k["rol"]}}

@app.get("/me")
def me(token=Depends(token_dogrula)):
    return token

# ── KULLANICILAR ──────────────────────────────────────────────────────────────
@app.get("/kullanicilar")
def kullanicilar_listele(token=Depends(admin_kontrol)):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id,kullanici_adi,ad,rol,aktif FROM kullanicilar")
    rows = fetchall_dict(c)
    conn.close()
    return rows

@app.post("/kullanicilar")
def kullanici_ekle(data: KullaniciModel, token=Depends(admin_kontrol)):
    conn = get_db()
    sifre_hash = hashlib.sha256(data.sifre.encode()).hexdigest()
    try:
        c = conn.cursor()
        c.execute(adapt_sql("INSERT INTO kullanicilar (kullanici_adi,sifre_hash,ad,rol) VALUES (?,?,?,?)"),
                  (data.kullanici_adi, sifre_hash, data.ad, data.rol))
        conn.commit()
    except Exception:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Bu kullanici adi zaten var")
    finally:
        conn.close()
    return {"mesaj": "Kullanici eklendi"}

@app.put("/kullanicilar/{kid}/durum")
def kullanici_durum(kid: int, data: DurumModel, token=Depends(admin_kontrol)):
    conn = get_db()
    aktif = 1 if data.durum == "aktif" else 0
    c = conn.cursor()
    c.execute(adapt_sql("UPDATE kullanicilar SET aktif=? WHERE id=?"), (aktif, kid))
    conn.commit(); conn.close()
    return {"mesaj": "Durum guncellendi"}

@app.delete("/kullanicilar/{kid}")
def kullanici_sil(kid: int, token=Depends(admin_kontrol)):
    conn = get_db()
    c = conn.cursor()
    c.execute(adapt_sql("SELECT kullanici_adi FROM kullanicilar WHERE id=?"), (kid,))
    k = fetchone_dict(c)
    if k and k["kullanici_adi"] == "labin":
        raise HTTPException(status_code=400, detail="Ana admin silinemez")
    c.execute(adapt_sql("DELETE FROM kullanicilar WHERE id=?"), (kid,))
    conn.commit(); conn.close()
    return {"mesaj": "Kullanici silindi"}

# ── MUSTERILER ────────────────────────────────────────────────────────────────
@app.get("/musteriler")
def musteriler(token=Depends(token_dogrula)):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM musteriler ORDER BY firma")
    rows = fetchall_dict(c)
    result = []
    for r in rows:
        m = dict(r)
        c.execute(adapt_sql("SELECT * FROM musteri_fiyatlar WHERE musteri_id=? ORDER BY tarih DESC LIMIT 1"), (r["id"],))
        fp = fetchone_dict(c)
        m["son_fiyat"] = fp if fp else {"taze_beton":0,"celik":0,"karot":0}
        result.append(m)
    conn.close()
    return result

@app.get("/musteriler/{mid}")
def musteri_detay(mid: int, token=Depends(token_dogrula)):
    conn = get_db()
    c = conn.cursor()
    c.execute(adapt_sql("SELECT * FROM musteriler WHERE id=?"), (mid,))
    m = fetchone_dict(c)
    if not m: raise HTTPException(404, "Musteri bulunamadi")
    result = dict(m)
    c.execute(adapt_sql("SELECT * FROM musteri_fiyatlar WHERE musteri_id=? ORDER BY tarih DESC"), (mid,))
    result["fiyatlar"] = fetchall_dict(c)
    conn.close()
    return result

@app.post("/musteriler")
def musteri_ekle(data: MusteriModel, token=Depends(admin_kontrol)):
    conn = get_db()
    c = conn.cursor()
    if USE_PG:
        c.execute("INSERT INTO musteriler (tip,firma,yetkili,telefon,eposta,vergino,adres,belediye) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                  (data.tip,data.firma,data.yetkili,data.telefon,data.eposta,data.vergino,data.adres,data.belediye))
        mid = c.fetchone()[0]
    else:
        c.execute("INSERT INTO musteriler (tip,firma,yetkili,telefon,eposta,vergino,adres,belediye) VALUES (?,?,?,?,?,?,?,?)",
                  (data.tip,data.firma,data.yetkili,data.telefon,data.eposta,data.vergino,data.adres,data.belediye))
        mid = c.lastrowid
    c.execute(adapt_sql("INSERT INTO musteri_fiyatlar (musteri_id,taze_beton,celik,karot) VALUES (?,?,?,?)"),
              (mid, data.taze_beton, data.celik, data.karot))
    conn.commit(); conn.close()
    return {"id": mid, "mesaj": "Musteri eklendi"}

@app.put("/musteriler/{mid}")
def musteri_guncelle(mid: int, data: MusteriModel, token=Depends(admin_kontrol)):
    conn = get_db()
    c = conn.cursor()
    c.execute(adapt_sql("UPDATE musteriler SET tip=?,firma=?,yetkili=?,telefon=?,eposta=?,vergino=?,adres=?,belediye=? WHERE id=?"),
              (data.tip,data.firma,data.yetkili,data.telefon,data.eposta,data.vergino,data.adres,data.belediye,mid))
    c.execute(adapt_sql("SELECT * FROM musteri_fiyatlar WHERE musteri_id=? ORDER BY tarih DESC LIMIT 1"), (mid,))
    son = fetchone_dict(c)
    if not son or (son["taze_beton"]!=data.taze_beton or son["celik"]!=data.celik or son["karot"]!=data.karot):
        c.execute(adapt_sql("INSERT INTO musteri_fiyatlar (musteri_id,taze_beton,celik,karot) VALUES (?,?,?,?)"),
                  (mid, data.taze_beton, data.celik, data.karot))
    conn.commit(); conn.close()
    return {"mesaj": "Guncellendi"}

@app.delete("/musteriler/{mid}")
def musteri_sil(mid: int, token=Depends(admin_kontrol)):
    conn = get_db()
    c = conn.cursor()
    c.execute(adapt_sql("DELETE FROM musteriler WHERE id=?"), (mid,))
    conn.commit(); conn.close()
    return {"mesaj": "Silindi"}

# ── NUMUNELER ─────────────────────────────────────────────────────────────────
@app.get("/numuneler")
def numuneler(tur: Optional[str] = None, token=Depends(token_dogrula)):
    conn = get_db()
    c = conn.cursor()
    if tur:
        c.execute(adapt_sql("SELECT * FROM numuneler WHERE tur=? ORDER BY tarih DESC"), (tur,))
    else:
        c.execute("SELECT * FROM numuneler ORDER BY tarih DESC")
    rows = fetchall_dict(c)
    conn.close()
    return rows

@app.post("/numuneler")
def numune_ekle(data: NumuneModel, token=Depends(admin_kontrol)):
    conn = get_db()
    c = conn.cursor()
    vals = (data.tur,data.musteri_id,data.musteri_adi,data.tarih,data.yibf,data.belediye,
            data.blok,data.kat,data.m3,data.beton_sinifi,data.caplar,data.adet,
            data.birim_fiyat,data.kdv_oran,data.kdv_tutar,data.toplam,data.toplam_kdvli,
            data.durum,data.not_,data.is_adi)
    if USE_PG:
        c.execute("""INSERT INTO numuneler (tur,musteri_id,musteri_adi,tarih,yibf,belediye,blok,kat,
            m3,beton_sinifi,caplar,adet,birim_fiyat,kdv_oran,kdv_tutar,toplam,toplam_kdvli,durum,not_,is_adi)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""", vals)
        nid = c.fetchone()[0]
    else:
        c.execute("""INSERT INTO numuneler (tur,musteri_id,musteri_adi,tarih,yibf,belediye,blok,kat,
            m3,beton_sinifi,caplar,adet,birim_fiyat,kdv_oran,kdv_tutar,toplam,toplam_kdvli,durum,not_,is_adi)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", vals)
        nid = c.lastrowid
    son_guncelleme_guncelle(conn)
    conn.commit(); conn.close()
    return {"id": nid}

@app.put("/numuneler/{nid}")
def numune_guncelle(nid: int, data: NumuneModel, token=Depends(admin_kontrol)):
    conn = get_db()
    c = conn.cursor()
    c.execute(adapt_sql("""UPDATE numuneler SET tur=?,musteri_id=?,musteri_adi=?,tarih=?,yibf=?,
        belediye=?,blok=?,kat=?,m3=?,beton_sinifi=?,caplar=?,adet=?,birim_fiyat=?,
        kdv_oran=?,kdv_tutar=?,toplam=?,toplam_kdvli=?,durum=?,not_=?,is_adi=? WHERE id=?"""),
        (data.tur,data.musteri_id,data.musteri_adi,data.tarih,data.yibf,data.belediye,
         data.blok,data.kat,data.m3,data.beton_sinifi,data.caplar,data.adet,
         data.birim_fiyat,data.kdv_oran,data.kdv_tutar,data.toplam,data.toplam_kdvli,
         data.durum,data.not_,data.is_adi,nid))
    conn.commit(); conn.close()
    return {"mesaj": "Guncellendi"}

@app.put("/numuneler/{nid}/durum")
def numune_durum(nid: int, data: DurumModel, token=Depends(admin_kontrol)):
    conn = get_db()
    c = conn.cursor()
    c.execute(adapt_sql("UPDATE numuneler SET durum=? WHERE id=?"), (data.durum, nid))
    conn.commit(); conn.close()
    return {"mesaj": "Durum guncellendi"}

@app.delete("/numuneler/{nid}")
def numune_sil(nid: int, token=Depends(admin_kontrol)):
    conn = get_db()
    c = conn.cursor()
    c.execute(adapt_sql("DELETE FROM numuneler WHERE id=?"), (nid,))
    conn.commit(); conn.close()
    return {"mesaj": "Silindi"}

# ── GELIRLER ──────────────────────────────────────────────────────────────────
@app.get("/gelirler")
def gelirler(token=Depends(token_dogrula)):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM gelirler ORDER BY tarih DESC")
    rows = fetchall_dict(c)
    conn.close()
    return rows

@app.post("/gelirler")
def gelir_ekle(data: GelirModel, token=Depends(admin_kontrol)):
    conn = get_db()
    c = conn.cursor()
    if USE_PG:
        c.execute("INSERT INTO gelirler (tarih,aciklama,musteri_id,musteri_adi,tutar,odeme_turu) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                  (data.tarih,data.aciklama,data.musteri_id,data.musteri_adi,data.tutar,data.odeme_turu))
        gid = c.fetchone()[0]
    else:
        c.execute("INSERT INTO gelirler (tarih,aciklama,musteri_id,musteri_adi,tutar,odeme_turu) VALUES (?,?,?,?,?,?)",
                  (data.tarih,data.aciklama,data.musteri_id,data.musteri_adi,data.tutar,data.odeme_turu))
        gid = c.lastrowid
    son_guncelleme_guncelle(conn)
    conn.commit(); conn.close()
    return {"id": gid}

@app.delete("/gelirler/{gid}")
def gelir_sil(gid: int, token=Depends(admin_kontrol)):
    conn = get_db()
    c = conn.cursor()
    c.execute(adapt_sql("DELETE FROM gelirler WHERE id=?"), (gid,))
    conn.commit(); conn.close()
    return {"mesaj": "Silindi"}

# ── GIDERLER ──────────────────────────────────────────────────────────────────
@app.get("/giderler")
def giderler(token=Depends(token_dogrula)):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM giderler ORDER BY tarih DESC")
    rows = fetchall_dict(c)
    conn.close()
    return rows

@app.post("/giderler")
def gider_ekle(data: GiderModel, token=Depends(admin_kontrol)):
    conn = get_db()
    c = conn.cursor()
    if USE_PG:
        c.execute("INSERT INTO giderler (tarih,kategori,aciklama,arac,tutar) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                  (data.tarih,data.kategori,data.aciklama,data.arac,data.tutar))
        gi_id = c.fetchone()[0]
    else:
        c.execute("INSERT INTO giderler (tarih,kategori,aciklama,arac,tutar) VALUES (?,?,?,?,?)",
                  (data.tarih,data.kategori,data.aciklama,data.arac,data.tutar))
        gi_id = c.lastrowid
    son_guncelleme_guncelle(conn)
    conn.commit(); conn.close()
    return {"id": gi_id}

@app.delete("/giderler/{gid}")
def gider_sil(gid: int, token=Depends(admin_kontrol)):
    conn = get_db()
    c = conn.cursor()
    c.execute(adapt_sql("DELETE FROM giderler WHERE id=?"), (gid,))
    conn.commit(); conn.close()
    return {"mesaj": "Silindi"}

# ── PERSONELLER ───────────────────────────────────────────────────────────────
@app.get("/personeller")
def personeller(token=Depends(token_dogrula)):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM personeller ORDER BY durum,ad")
    rows = fetchall_dict(c)
    conn.close()
    return rows

@app.post("/personeller")
def personel_ekle(data: PersonelModel, token=Depends(admin_kontrol)):
    conn = get_db()
    c = conn.cursor()
    if USE_PG:
        c.execute("INSERT INTO personeller (ad,telefon,iban,gorev,maas,ise_giris,cikis_tarihi,durum) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                  (data.ad,data.telefon,data.iban,data.gorev,data.maas,data.ise_giris,data.cikis_tarihi,data.durum))
        pid2 = c.fetchone()[0]
    else:
        c.execute("INSERT INTO personeller (ad,telefon,iban,gorev,maas,ise_giris,cikis_tarihi,durum) VALUES (?,?,?,?,?,?,?,?)",
                  (data.ad,data.telefon,data.iban,data.gorev,data.maas,data.ise_giris,data.cikis_tarihi,data.durum))
        pid2 = c.lastrowid
    conn.commit(); conn.close()
    return {"id": pid2}

@app.put("/personeller/{pid}")
def personel_guncelle(pid: int, data: PersonelModel, token=Depends(admin_kontrol)):
    conn = get_db()
    c = conn.cursor()
    c.execute(adapt_sql("UPDATE personeller SET ad=?,telefon=?,iban=?,gorev=?,maas=?,ise_giris=?,cikis_tarihi=?,durum=? WHERE id=?"),
              (data.ad,data.telefon,data.iban,data.gorev,data.maas,data.ise_giris,data.cikis_tarihi,data.durum,pid))
    conn.commit(); conn.close()
    return {"mesaj": "Guncellendi"}

@app.delete("/personeller/{pid}")
def personel_sil(pid: int, token=Depends(admin_kontrol)):
    conn = get_db()
    c = conn.cursor()
    c.execute(adapt_sql("DELETE FROM personeller WHERE id=?"), (pid,))
    conn.commit(); conn.close()
    return {"mesaj": "Silindi"}

# ── CEK SENET ─────────────────────────────────────────────────────────────────
@app.get("/cek-senetler")
def cek_senetler(token=Depends(token_dogrula)):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM cek_senetler ORDER BY vade")
    rows = fetchall_dict(c)
    conn.close()
    return rows

@app.post("/cek-senetler")
def cek_ekle(data: CekSenetModel, token=Depends(admin_kontrol)):
    conn = get_db()
    c = conn.cursor()
    if USE_PG:
        c.execute("INSERT INTO cek_senetler (vade,tur,musteri_adi,banka,no,tutar,durum) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                  (data.vade,data.tur,data.musteri_adi,data.banka,data.no,data.tutar,data.durum))
        cid2 = c.fetchone()[0]
    else:
        c.execute("INSERT INTO cek_senetler (vade,tur,musteri_adi,banka,no,tutar,durum) VALUES (?,?,?,?,?,?,?)",
                  (data.vade,data.tur,data.musteri_adi,data.banka,data.no,data.tutar,data.durum))
        cid2 = c.lastrowid
    conn.commit(); conn.close()
    return {"id": cid2}

@app.put("/cek-senetler/{cid}/durum")
def cek_durum(cid: int, data: DurumModel, token=Depends(admin_kontrol)):
    conn = get_db()
    c = conn.cursor()
    c.execute(adapt_sql("UPDATE cek_senetler SET durum=? WHERE id=?"), (data.durum, cid))
    conn.commit(); conn.close()
    return {"mesaj": "Durum guncellendi"}

@app.delete("/cek-senetler/{cid}")
def cek_sil(cid: int, token=Depends(admin_kontrol)):
    conn = get_db()
    c = conn.cursor()
    c.execute(adapt_sql("DELETE FROM cek_senetler WHERE id=?"), (cid,))
    conn.commit(); conn.close()
    return {"mesaj": "Silindi"}

# ── BETON PROGRAMI ────────────────────────────────────────────────────────────
@app.get("/beton-programi")
def beton_programi(token=Depends(token_dogrula)):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM beton_programi ORDER BY tarih,saat")
    rows = fetchall_dict(c)
    conn.close()
    return rows

@app.post("/beton-programi")
def beton_ekle(data: BetonProgramModel, token=Depends(admin_kontrol)):
    conn = get_db()
    c = conn.cursor()
    if USE_PG:
        c.execute("INSERT INTO beton_programi (tarih,saat,musteri_adi,yibf,belediye,blok,kat,m3,beton_sinifi,not_) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                  (data.tarih,data.saat,data.musteri_adi,data.yibf,data.belediye,data.blok,data.kat,data.m3,data.beton_sinifi,data.not_))
        bid2 = c.fetchone()[0]
    else:
        c.execute("INSERT INTO beton_programi (tarih,saat,musteri_adi,yibf,belediye,blok,kat,m3,beton_sinifi,not_) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (data.tarih,data.saat,data.musteri_adi,data.yibf,data.belediye,data.blok,data.kat,data.m3,data.beton_sinifi,data.not_))
        bid2 = c.lastrowid
    conn.commit(); conn.close()
    return {"id": bid2}

@app.delete("/beton-programi/{bid}")
def beton_sil(bid: int, token=Depends(admin_kontrol)):
    conn = get_db()
    c = conn.cursor()
    c.execute(adapt_sql("DELETE FROM beton_programi WHERE id=?"), (bid,))
    conn.commit(); conn.close()
    return {"mesaj": "Silindi"}

# ── ARACLAR ───────────────────────────────────────────────────────────────────
@app.get("/araclar")
def araclar(token=Depends(token_dogrula)):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM araclar ORDER BY plaka")
    rows = fetchall_dict(c)
    conn.close()
    return rows if rows else []

class AracModel(BaseModel):
    plaka: str
    model: Optional[str] = ""

@app.post("/araclar")
def arac_ekle_query(plaka: str = "", model: str = "", token=Depends(admin_kontrol)):
    if not plaka: raise HTTPException(400, "Plaka zorunludur")
    conn = get_db(); c = conn.cursor()
    if USE_PG:
        c.execute("INSERT INTO araclar (plaka,model) VALUES (%s,%s) RETURNING id", (plaka.upper(), model))
        aid = c.fetchone()[0]
    else:
        c.execute("INSERT INTO araclar (plaka,model) VALUES (?,?)", (plaka.upper(), model))
        aid = c.lastrowid
    conn.commit(); conn.close()
    return {"id": aid}

@app.post("/araclar-ekle")
def arac_ekle(data: AracModel, token=Depends(admin_kontrol)):
    conn = get_db(); c = conn.cursor()
    if USE_PG:
        c.execute("INSERT INTO araclar (plaka,model) VALUES (%s,%s) RETURNING id", (data.plaka.upper(), data.model))
        aid = c.fetchone()[0]
    else:
        c.execute("INSERT INTO araclar (plaka,model) VALUES (?,?)", (data.plaka.upper(), data.model))
        aid = c.lastrowid
    conn.commit(); conn.close()
    return {"id": aid}

@app.delete("/araclar/{aid}")
def arac_sil(aid: int, token=Depends(admin_kontrol)):
    conn = get_db(); c = conn.cursor()
    c.execute(adapt_sql("DELETE FROM araclar WHERE id=?"), (aid,))
    conn.commit(); conn.close()
    return {"mesaj": "Silindi"}

# ── OZET ──────────────────────────────────────────────────────────────────────
@app.get("/ozet")
def ozet(bas: Optional[str] = None, bit: Optional[str] = None, token=Depends(token_dogrula)):
    conn = get_db()
    c = conn.cursor()
    ph1 = '%s' if USE_PG else '?'
    if bas and bit:
        t_where = f"AND tarih >= {ph1} AND tarih <= {ph1}"
        t_where_cek = f"AND vade >= {ph1} AND vade <= {ph1}"
        t_params = (bas, bit)
    else:
        t_where = ""; t_where_cek = ""; t_params = ()

    def scalar(sql, params=()):
        c.execute(sql, params)
        r = c.fetchone()
        return r[0] if r else 0

    toplam_numune = scalar(f"SELECT COALESCE(SUM(toplam_kdvli),0) FROM numuneler WHERE 1=1 {t_where}", t_params)
    gelir_toplam = scalar(f"SELECT COALESCE(SUM(tutar),0) FROM gelirler WHERE 1=1 {t_where}", t_params)
    gider_toplam = scalar(f"SELECT COALESCE(SUM(tutar),0) FROM giderler WHERE 1=1 {t_where}", t_params)
    cek_toplam = scalar(f"SELECT COALESCE(SUM(tutar),0) FROM cek_senetler WHERE durum!='Karsilıksız' {t_where_cek}", t_params)
    tahsilat = gelir_toplam + cek_toplam
    beton_adet = scalar(f"SELECT COALESCE(SUM(adet),0) FROM numuneler WHERE tur='Taze Beton' {t_where}", t_params)
    celik_adet = scalar(f"SELECT COALESCE(SUM(adet),0) FROM numuneler WHERE tur='Celik' {t_where}", t_params)
    karot_adet = scalar(f"SELECT COALESCE(SUM(adet),0) FROM numuneler WHERE tur='Karot' {t_where}", t_params)
    c.execute("SELECT vade FROM cek_senetler WHERE durum='Beklemede' ORDER BY vade LIMIT 1")
    en_yakin_cek = c.fetchone()
    conn.close()
    return {
        "toplam_numune": toplam_numune,
        "gelir_toplam": gelir_toplam,
        "cek_toplam": cek_toplam,
        "tahsilat": tahsilat,
        "gider_toplam": gider_toplam,
        "net_kar": tahsilat - gider_toplam,
        "bekleyen_alacak": toplam_numune - tahsilat,
        "beton_adet": beton_adet,
        "celik_adet": celik_adet,
        "karot_adet": karot_adet,
        "en_yakin_cek_vade": (en_yakin_cek[0] if USE_PG else en_yakin_cek["vade"]) if en_yakin_cek else None,
    }

# ── AYLIK ─────────────────────────────────────────────────────────────────────
@app.get("/aylik")
def aylik(yil: int = None, token=Depends(token_dogrula)):
    if not yil:
        yil = datetime.now().year
    conn = get_db()
    c = conn.cursor()
    result = []
    ay_adlari = ["","Oca","Şub","Mar","Nis","May","Haz","Tem","Ağu","Eyl","Eki","Kas","Ara"]

    def scalar(sql, params):
        c.execute(sql, params)
        r = c.fetchone()
        return float(r[0]) if r and r[0] else 0.0

    ph1 = '%s' if USE_PG else '?'

    for ay in range(1, 13):
        prefix = f"{yil}-{ay:02d}"
        like_p = f"{prefix}%"
        if USE_PG:
            gelir = scalar("SELECT COALESCE(SUM(tutar),0) FROM gelirler WHERE tarih LIKE %s", (like_p,))
            cek = scalar("SELECT COALESCE(SUM(tutar),0) FROM cek_senetler WHERE vade LIKE %s AND durum!='Karsilıksız'", (like_p,))
            gider = scalar("SELECT COALESCE(SUM(tutar),0) FROM giderler WHERE tarih LIKE %s", (like_p,))
        else:
            gelir = scalar("SELECT COALESCE(SUM(tutar),0) FROM gelirler WHERE tarih LIKE ?", (like_p,))
            cek = scalar("SELECT COALESCE(SUM(tutar),0) FROM cek_senetler WHERE vade LIKE ? AND durum!='Karsilıksız'", (like_p,))
            gider = scalar("SELECT COALESCE(SUM(tutar),0) FROM giderler WHERE tarih LIKE ?", (like_p,))
        result.append({
            "ay": ay, "etiket": f"{ay_adlari[ay]} {yil}",
            "gelir": gelir + cek, "gider": gider, "net": gelir + cek - gider
        })
    conn.close()
    return result

# Son guncelleme tarihi
@app.get("/son-guncelleme")
def son_guncelleme_get(token=Depends(token_dogrula)):
    conn = get_db()
    c = conn.cursor()
    c.execute(adapt_sql("SELECT deger FROM ayarlar WHERE anahtar=?"), ('son_guncelleme',))
    row = c.fetchone()
    conn.close()
    deger = (row[0] if USE_PG else row["deger"]) if row else None
    return {"son_guncelleme": deger if deger else "Henüz veri girilmedi"}

# PWA Manifest
@app.get("/manifest.json")
async def manifest():
    from fastapi.responses import JSONResponse
    return JSONResponse({
        "name": "Labin Yapı Laboratuvarı",
        "short_name": "Labin Lab",
        "description": "Yapı laboratuvarı yönetim sistemi",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#1e3a5f",
        "theme_color": "#1e3a5f",
        "orientation": "portrait-primary",
        "icons": [
            {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}
        ],
        "categories": ["business", "productivity"]
    })

# PWA Service Worker
@app.get("/sw.js")
async def service_worker():
    from fastapi.responses import Response
    sw_content = """
const CACHE = 'labin-v202605041756';
self.addEventListener('install', e => { self.skipWaiting(); });
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.map(k => caches.delete(k)))
  ));
  self.clients.claim();
});
self.addEventListener('fetch', e => {
  // Her zaman network'ten yukle, cache kullanma
  e.respondWith(fetch(e.request).catch(() => new Response('Offline')));
});
"""
    return Response(content=sw_content, media_type="application/javascript")

# PWA ikonlari (basit mavi kare)
@app.get("/icon-192.png")
async def icon192():
    from fastapi.responses import Response
    import base64
    # 1x1 mavi PNG base64 - tarayici bunu 192x192 olarak kullanir
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    return Response(content=png, media_type="image/png",
                   headers={"Cache-Control": "public, max-age=86400"})

@app.get("/icon-512.png")
async def icon512():
    from fastapi.responses import Response
    import base64
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    return Response(content=png, media_type="image/png",
                   headers={"Cache-Control": "public, max-age=86400"})

# Ana sayfada index.html'i gonder
@app.get("/", response_class=HTMLResponse)
async def ana_sayfa():
    html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>index.html bulunamadi</h1>"

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
