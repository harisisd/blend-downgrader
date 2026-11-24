# Blend Downgrader: buka file Blender 4.x/5.x di Blender versi lama

File `.blend` tidak bisa diturunkan versinya secara langsung, karena struktur datanya berubah di setiap versi. Tool ini bekerja dalam dua tahap:

```
file_baru.blend ──[Blender 4/5 + legacy_export.py]──► paket (scene.json + mesh .npz + textures/)
paket          ──[Blender 3.x + legacy_import.py]──► file_lama.blend  (file .blend asli versi 3)
```

## App Blend Downgrader (macOS .dmg / Windows .exe)

Unduh installer-nya dari halaman **Releases** repo ini (atau build sendiri, hasilnya di `app/dist/`):
- `Blend-Downgrader-1.0.1-mac-arm64.dmg` untuk Mac M1/M2/M3/M4
- `Blend-Downgrader-1.0.1-mac-x64.dmg` untuk Mac Intel
- `Blend-Downgrader-1.0.1-Setup.exe` untuk Windows (installer)
- `Blend-Downgrader-1.0.1-portable.exe` untuk Windows (tanpa install)

Fitur:
- Seret satu atau beberapa file `.blend` ke jendela app. Versi tiap file langsung terbaca, termasuk file terkompresi.
- Pilih versi tujuan. Daftarnya diambil dari Blender yang terpasang (minimal Blender 3.0).
- **Kelola Blender**: deteksi otomatis, tambah manual, atau unduh Blender 3.0–4.5 resmi dari download.blender.org (checksum dicek). Hasil unduhan disimpan terpisah dan tidak mengganggu Blender yang sudah ada.
- Opsi: apply modifier, pack tekstur, cek hasil, dan folder output.
- Peringatan ditampilkan per file. Hasil bisa langsung dibuka di Blender tujuan atau ditampilkan di Finder/Explorer, dan log lengkapnya bisa dilihat.

Syarat: di komputer yang sama harus ada Blender baru (versi file atau lebih baru) dan Blender versi tujuan. Blender versi tujuan bisa diunduh langsung dari app.

App belum ditandatangani sertifikat developer. Saat pertama kali dibuka:
- **macOS**: klik kanan app > **Open** > **Open**.
- **Windows**: SmartScreen > **More info** > **Run anyway**.

Untuk build ulang: `cd app && npm install && npm run dist:mac` atau `npm run dist:win`.

## Cara tercepat: app drag-and-drop (macOS, dua Blender terpasang)

Syarat: Blender 4.x/5.x dan Blender 3.x sama-sama ada di `/Applications` (mis. `Blender.app` dan `Blender 3.6.app`).

1. Seret file `.blend` (boleh beberapa sekaligus) ke **Blend ke Blender 3.app**, atau klik dua kali app-nya lalu pilih file.
2. Tunggu beberapa detik. Hasilnya `namafile_v3.blend` muncul di folder yang sama dengan file aslinya.
3. Buka file `_v3.blend` itu di Blender 3.

App ini dibuat dari `droplet/` dengan `./droplet/build.sh`.

Pertama kali dibuka, macOS mungkin menolak karena app tidak ditandatangani. Klik kanan app > **Open** > **Open**.

Lewat terminal, hasilnya sama: `./blend2v3.sh file.blend [file2.blend ...]`

## Cara 1: lewat menu

**Di Blender baru (4.x/5.x):**
1. Edit > Preferences > Add-ons > tombol ▾ > **Install from Disk** > pilih `legacy_export.py`, lalu centang.
2. Buka file-nya, lalu **File > Export > Legacy Package (Blender 3.x)** dan pilih folder tujuan.
3. Hasilnya `namafile_legacy.zip`. Kirim zip ini ke komputer yang memakai Blender 3.

**Di Blender lama (3.x):**
1. Install `legacy_import.py` dengan cara yang sama (Preferences > Add-ons > Install...).
2. **File > Import > Legacy Package**, lalu pilih file `.zip` (atau `scene.json` di dalam folder paket).
3. **File > Save As**. Sekarang file-nya sudah format Blender 3.

> Import akan **mengganti scene yang sedang terbuka**. Jalankan di file kosong.

## Cara 2: lewat terminal

```bash
# di Blender baru
blender -b --factory-startup input.blend --python legacy_export.py -- paket_out --zip

# di Blender lama
blender -b --factory-startup --python legacy_import.py -- paket_out.zip hasil_v3.blend
```

Kalau dua versi Blender ada di komputer yang sama: `./downgrade.sh BLENDER_BARU BLENDER_LAMA input.blend output.blend`

`--factory-startup` mencegah add-on lain ikut jalan. Opsi tambahan:
- export `--keep-modifiers`: modifier tidak di-apply, tetap jadi modifier
- import `--no-pack`: tekstur tidak di-pack ke dalam .blend
- import `--verbose`: tampilkan setiap properti yang dilewati

## Yang ikut terbawa

| Bagian | Keterangan |
|---|---|
| Mesh | vertex, face, edge, UV, color attribute, smooth/sharp, seam, crease, bevel weight, custom normals |
| Modifier | Default: di-**apply** (termasuk Geometry Nodes), **kecuali Armature** yang tetap jadi modifier. Obyek dengan shape key: modifier dibawa apa adanya |
| Shape key | lengkap, termasuk nilai dan animasinya |
| Vertex group | lengkap |
| Material | node tree lengkap, node group, color ramp, RGB curves, frame. Principled BSDF 4.x dipetakan ke Principled 3.x (Coat → Clearcoat, dst.) |
| Tekstur | disalin ke paket (packed, generated, atau file luar), lalu di-pack ke .blend hasil |
| Armature | bone, roll, parent, connect, pose, bone constraint, custom shape |
| Animasi | action aktif untuk obyek, pose bone, shape key, kamera, dan lampu (keyframe, interpolasi, handle) |
| Lainnya | kamera (+DoF), lampu, world, collection (hierarki + exclude/hide), parent (termasuk ke bone), constraint, custom property, setting scene/render |

## Yang tidak bisa dibawa (dan apa yang terjadi)

- **Geometry Nodes**: hasilnya di-apply jadi mesh biasa. Jika obyek punya shape key, GN dibuang (ada peringatan).
- **Curve, text, metaball**: dikonversi jadi mesh.
- **Grease Pencil, hair curves, volume, point cloud**: dijadikan Empty.
- **NLA, driver, modifier F-curve (selain Cycles)**: hanya action aktif yang dibawa.
- **Bone collections (4.x)**: semua bone ditaruh di layer 1.
- **Node yang belum ada di 3.x** (mis. node baru 4.x): dilewati, dan link-nya ikut hilang.
- **EEVEE Next / AgX**: diganti otomatis ke EEVEE lama / Filmic.
- Hanya **scene aktif** yang di-export.

Semua peringatan dicetak di console dan disimpan di `scene.json` (bagian `"warnings"`).

Disarankan memakai **Blender 3.4 ke atas (idealnya 3.6 LTS)**. Node Mix baru belum ada di 3.0–3.3.
