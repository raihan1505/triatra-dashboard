# Dashboard Klasemen Triatra Fresh 5.0

Upload file Excel rekap -> tersimpan di TiDB -> dashboard langsung terbarui.

## Jalankan di komputer
1. `pip install -r requirements.txt`
2. Buat database kosong di TiDB Cloud (mis. `triatra_fresh`), lalu salin `.env.example` jadi `.env` dan isi.
3. Muat env lalu jalankan:
   - Windows PowerShell: `Get-Content .env | % { if($_ -match '^([^#=]+)=(.*)$'){ [Environment]::SetEnvironmentVariable($matches[1],$matches[2]) } }`
   - Mac/Linux: `export $(grep -v '^#' .env | xargs)`
   - `uvicorn app:app --port 8000`
4. Buka http://localhost:8000, klik **Upload data** (atau seret file ke halaman).

Tanpa variabel TIDB_*, aplikasi memakai SQLite lokal (`local_demo.db`) untuk uji coba. Tabel `rekap_all` dan `upload_log` dibuat otomatis.

## Format file
Sheet **Rekap All** wajib ada, dengan kolom: Site / Area Asal, Nama, NRP, Display Name Strava, Kontingen, RUN, WALK, TOTAL, STATUS RUN, STATUS WALK (kolom wajib: Site / Area Asal, Nama, RUN, WALK). Sheet Rekap Walk / Rekap Run dipakai hanya untuk membaca tanggal cut-off. Sheet List Pendaftar Clear (email, e-wallet) tidak diunggah ke database.

Tiap upload mengganti isi `rekap_all` dalam satu transaksi: kalau gagal, data lama tetap utuh.

## Deploy (agar bisa dibuka orang lain)
Jalankan di layanan yang mendukung Python, misalnya Render atau Railway: start command `uvicorn app:app --host 0.0.0.0 --port $PORT`, isi variabel TIDB_* dan UPLOAD_TOKEN di pengaturan environment.
