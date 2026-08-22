# Faceless Video — otomatik YouTube üretim hattı

Her gün kendi kendine bir konu seçer, İngilizce senaryo yazar, seslendirir,
LTX ile B-roll üretir, iki ayrı kurgu (uzun form 16:9 + Shorts 9:16) monte eder
ve **her gün aynı saatte** yayınlanacak şekilde YouTube'a planlar.

Yüz yok, kamera yok, elle iş yok.

```
konu seçimi ──► senaryo (uzun + Shorts) ──► seslendirme + kelime zamanlaması
   (Claude)          (Claude)                    (edge-tts / ElevenLabs)
                                                          │
             LTX klipleri ◄──── görsel prompt'lar          │
                    │                                      │
                    └──────► ffmpeg montaj ◄───────────────┘
                                   │
                    ┌──────────────┴──────────────┐
              uzun form 16:9                 Shorts 9:16
                    └──────────────┬──────────────┘
                                   ▼
                    YouTube (private + publishAt) ──► sabit saatte otomatik yayın
```

---

## 1. Sabit yayın saati nasıl çalışıyor

Bu, sistemin en önemli tasarım kararı. Render süresi değişkendir (LTX kuyruğu
yoğunsa 20 dakika, boşsa 5 dakika sürer) — ama yayın saati **değişmez**.

Çözüm: video YouTube'a `privacyStatus=private` + `publishAt=<zaman damgası>`
ile yüklenir. YouTube videoyu tam o dakikada kendisi herkese açık yapar.

Yani:

| | |
|---|---|
| GitHub Actions cron | 12:00 UTC — sadece **üretim** başlangıcı, geniş pay bırakır |
| `publish.publish_at_local` | 19:00 Europe/Istanbul — **gerçek yayın anı** |
| `publish.shorts_publish_at_local` | 12:00 — Shorts ayrı saatte, ikisi birbirini yemesin diye |

Render gecikse bile yayın saati kaymaz. Eğer o günün saatine 15 dakikadan az
kaldıysa sistem otomatik olarak ertesi güne planlar — yanlış saatte yayın yapmaz.
Yaz saati geçişlerinde de duvar saati sabit kalır (19:00 hep 19:00'dur).

---

## 2. Kurulum

### 2.1 Gereksinimler

**Python 3.11 veya üstü** ve ffmpeg gerekiyor. 3.11 alt sınırı keyfi değil:
kod zaman dilimi işlemleri için `zoneinfo` kullanıyor (3.9'da geldi) ve modern
tip sözdizimine dayanıyor. Daha eski bir Python'la kurulmuş `.venv` varsa
`make install` bunu fark edip durur — klasörü silip tekrar kur.

**Linux / macOS**

```bash
make install                                   # .venv kurar, bağımlılıkları yükler
sudo apt-get install ffmpeg fonts-dejavu-core  # macOS: brew install ffmpeg
```

**Windows**

`make` Windows'ta yoktur; repoda aynı komutları karşılayan bir `make.bat` var,
yani `cmd` içinde aynı komutlar çalışır:

```cmd
make install
```

Python yoksa (ya da 3.11'den eskiyse) python.org'dan kur ve kurulumda
**"Add python.exe to PATH"** kutusunu işaretle. Eski bir Python'la oluşmuş
sanal ortam varsa önce onu sil:

```cmd
rmdir /s /q .venv
```

**Yeni Python kurdun ama hâlâ eskisi görünüyorsa:** Windows'un `py` başlatıcısı
`PY_PYTHON` değişkeni veya bir `py.ini` ile eski bir sürüme sabitlenmiş olabilir.
Kurulu yorumlayıcıları tam yollarıyla listele ve doğru olanı doğrudan göster:

```cmd
py -0p
set PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python313\python.exe
make install
```

`PYTHON_EXE` her şeyin önüne geçer, `make install` onu kullanır.

ffmpeg için (sadece yerel render gerekiyorsa; GitHub Actions kendi kurar):

```cmd
winget install Gyan.FFmpeg
```

`winget` yoksa Microsoft Store'dan "App Installer" kurulabilir.

Kurulumdan sonra **terminali kapatıp yeniden aç** (PATH yenilensin), `ffmpeg -version`
ile doğrula.

> ffmpeg sadece yerel render (`make dry` / `make run`) için gerekli. Token üretmek
> (`make auth`), `doctor` ve `plan` için gerekmez — GitHub Actions ffmpeg'i kendi kurar.

PowerShell kullanıyorsan `make` yerine `.\make.bat` yaz. `make` yüklemek istemiyorsan
komutları doğrudan da çalıştırabilirsin:

```cmd
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pipeline.cli doctor
```

### 2.2 API anahtarları

| Anahtar | Nereden | Zorunlu mu |
|---|---|---|
| `ANTHROPIC_API_KEY` | console.anthropic.com | Evet — konu + senaryo |
| `LTX_API_KEY` | LTX Developer Console → API Keys | Evet — video üretimi |
| `YOUTUBE_CLIENT_ID/SECRET/REFRESH_TOKEN` | aşağıdaki 2.3 | Evet — yayın |
| `FAL_KEY` | fal.ai | Hayır — LTX'e alternatif yol |
| `ELEVENLABS_API_KEY` | elevenlabs.io | Hayır — ücretsiz ses yerine |

> **LTX Studio ile LTX API farklı hesaplardır.** LTX Studio web arayüzünün
> aboneliği API'yi kapsamaz; API anahtarı Developer Console'dan alınır ve ayrı
> faturalanır.

`.env.example` dosyasını `.env` olarak kopyalayıp doldur — kod onu otomatik
okur, hiçbir şeyi elle export etmen gerekmez:

```powershell
Copy-Item .env.example .env
notepad .env
```

Gerçek ortam değişkenleri her zaman `.env`'in önüne geçer, yani GitHub
Actions'taki secret'lar bir checkout'ta unutulmuş `.env` tarafından gölgelenmez.
`.env` zaten `.gitignore`'da; repoya girmez.

### 2.3 YouTube yetkilendirmesi (bir kez, kendi bilgisayarında)

1. [Google Cloud Console](https://console.cloud.google.com) → yeni proje.
2. **APIs & Services** → **Library** → **YouTube Data API v3** → *Enable*.
3. **OAuth consent screen** → **Branding**. Sadece yıldızlı alanlar zorunludur:

   | Alan | Ne yazılacak |
   |---|---|
   | App name * | Herhangi bir isim ("Youtube Video" olur) |
   | User support email * | Kendi e-postan |
   | Developer contact email * | Kendi e-postan |
   | App logo | **Boş bırak** |
   | Home page / Privacy policy / Terms | Adım 4'ten sonra doldurulacak |
   | Authorized domains | Adım 4'ten sonra doldurulacak |

   ⚠️ **Logo yükleme.** Google'ın kendi uyarısı: logo yüklenen uygulama doğrulamaya
   (verification) girmek zorunda kalır. Kişisel bir otomasyon için buna gerek yok.

   Alan adı ve politika bağlantılarını şimdilik boş bırakıp **Save** de. Bir sonraki
   adımda scope'ları ekleyince Google bu alanları zorunlu hale getirecek ve buraya
   geri döneceğiz.

4. **Data Access** → **Add or remove scopes** → `youtube` ara → şu ikisini ekle →
   **Update** → **Save**:

   ```
   https://www.googleapis.com/auth/youtube.upload
   https://www.googleapis.com/auth/youtube.force-ssl
   ```

   Bu iki scope "sensitive" sınıfındadır. Eklediğin anda Google, Branding
   sayfasındaki ana sayfa / gizlilik politikası / kullanım şartları alanlarını
   **zorunlu** hale getirir ve doğrulanmış bir alan adı ister. Hazır sayfalar
   `docs/` klasöründe duruyor — kurulumu `docs/README.md` anlatıyor.

5. **Audience** sekmesi → *User type* **External** → ⚠️ **PUBLISH APP** → onayla.

   Bu adım zorunlu. "Testing" modunda kalan bir uygulamanın refresh token'ı
   **7 günde bir geçersiz olur** ve otomasyon sessizce durur — bu tür kurulumların
   açık ara en sık ölüm sebebi budur. Uygulama doğrulanmamış olacağı için giriş
   sırasında "Google hasn't verified this app" uyarısı görürsün; *Advanced →
   Go to (uygulama adı)* ile geçersin. Kendi kanalın için bu tamamen normaldir.

6. **Credentials** → Create Credentials → OAuth client ID → **Desktop app**.
   JSON'u indir ve repo köküne koy. **Adını değiştirmene gerek yok** — Google'ın
   verdiği `client_secret_1234-abcd.apps.googleusercontent.com.json` gibi uzun ad
   olduğu gibi kalabilir, `auth` komutu dosyayı kendisi bulur.
7. Çalıştır (tarayıcı açılır, kanalını seç):

```bash
make auth          # Windows'ta da aynı
```

Komut sana üç değer basar. Bunları GitHub'a Secret olarak ekle.
Birden fazla kanalın varsa doğru hesabı seçtiğine dikkat et — seçim token'a gömülür.

### 2.4 GitHub Secrets

Repo → Settings → Secrets and variables → Actions → **New repository secret**:

```
ANTHROPIC_API_KEY
LTX_API_KEY
YOUTUBE_CLIENT_ID
YOUTUBE_CLIENT_SECRET
YOUTUBE_REFRESH_TOKEN
```

İsteğe bağlı `FAL_KEY`, `ELEVENLABS_API_KEY`. LTX'in API yolunu sabitlemen
gerekirse `LTX_API_BASE` / `LTX_SUBMIT_PATH` / `LTX_STATUS_PATH` değerlerini
**Variables** sekmesine ekle (bkz. bölüm 5).

Ayrıca Settings → Actions → General → Workflow permissions →
**Read and write** işaretli olmalı (geçmiş dosyası repoya geri yazılıyor).

---

## 3. Komutlar

```bash
make doctor     # config + anahtarlar + ffmpeg kontrolü
make plan       # bu çalıştırma ne kadara mal olur — hiçbir şey harcamaz
make probe      # LTX'e tek ucuz iş gönderir, ham yanıtı basar
make auth       # YouTube OAuth bilgilerini üretir (tarayıcı gerekir)
make storyboard # bir bölümü planlar ve fiyatlandırır — video üretmez
make sample     # her karakter bir cümle söyler, kadroyu dinleyip ayarlamak için
make voices     # son storyboard'u seslendirir — ücretsiz, video yok
make listvoices # hesabındaki ElevenLabs seslerini kimlikleriyle listeler
make dry        # tam render, YouTube'a yükleme yok
make run        # tam üretim + yayın
make test       # 68 test
```

Tek seferlik elle tetikleme: repo → Actions → *Daily video* → **Run workflow**
(uzun form / Shorts / ikisi seçilebilir, yükleme kapatılabilir).

### Actions üzerinden teşhis

Repo → Actions → **Diagnostics** → **Run workflow** → `doctor` / `plan` / `probe`.

Bu, kontrolleri **repository secrets'ın kendisiyle** çalıştırır. Yereldeki bir
çalıştırma sadece kendi bilgisayarının kurulu olduğunu kanıtlar; günlük üretimi
besleyen şey ise secrets'tır. Yeni bir anahtar ekledikten sonra ilk bakılacak
yer burasıdır.

> `probe` gerçek bir 480p klip gönderir, birkaç sent tutar. `doctor` ve `plan`
> hiçbir şey harcamaz.

### Bedava prova

`config.yaml` içinde `voice.provider: silent` yaparsan seslendirme yerine sessiz
bir ses izi üretilir — ağ yok, ücret yok. Kurgu, tempo, altyazı yerleşimi ve
zamanlamayı test etmek için birebir.

---

## 4. Kanalı ayarlamak

Her şey `config.yaml` içinde, kod değiştirmeden:

- `channel.niche` — **en önemli alan.** Dar tut. "teknoloji" kötü;
  "modern dünyayı değiştiren az bilinen mühendislik hikâyeleri" iyi.
- `channel.banned_topics` — asla girilmeyecek konular.
- `longform.target_seconds` / `shorts.target_seconds` — süreler.
- `video.max_clips` — **maliyeti belirleyen ana kaldıraç** (bkz. bölüm 6).
- `video.segment_seconds` — bir klibin ekranda kalma süresi.
- `publish.*` — yayın saatleri, kategori, etiketler.

Kanalın ses tonu ve yazım kuralları `prompts/ideation.md` ve `prompts/script.md`
içinde düz metin olarak duruyor. Beğenmediğin bir şey varsa oradan değiştir.

### Müzik

`assets/music/` klasörüne kendi telifsiz parçalarını at (mp3/m4a/wav). Her
çalıştırmada rastgele biri seçilir, anlatımın çok altında (-26 dB) çalar.
Klasör boşsa müziksiz devam eder. **Repoya telifli parça koyma** — Content ID
kanalını kapatır.

---

## 5. LTX API şeması hakkında dürüst bir not

LTX'in API dokümantasyonu bu sistem yazılırken ağ politikası nedeniyle
doğrudan okunamadı. Bu yüzden LTX istemcisi **şema-savunmacı** yazıldı:

- Birden fazla endpoint yolu sırayla denenir (404 alınca sonraki).
- İş kimliği ve video URL'si, yanıt JSON'ında **özyinelemeli aranır** — alan
  adı `id`, `job_id`, `generation_id`, `video_url`, `output.url` vb. olabilir.
- Hem `resolution` hem açık `width`/`height` gönderilir.

İlk kurulumda **mutlaka `make probe` çalıştır** (ya da Actions → Diagnostics →
`probe`). Tek bir gerçek iş gönderir ve ham istek/yanıtı basar. Yollar farklıysa
`LTX_API_BASE`, `LTX_SUBMIT_PATH`, `LTX_STATUS_PATH` ile sabitle — kod
değiştirmen gerekmez.

**Doğrulanmış durum (Ağustos 2026)** — `probe --discover` ile canlı API'den teyit edildi:

| | |
|---|---|
| Taban | `https://api.ltx.video/v1` |
| Yol | `/text-to-video` |
| Kimlik | `Authorization: Bearer <LTX_API_KEY>` |
| Model | `ltx-2-3-fast` |
| Çözünürlük | **`1920x1080` biçiminde** — `1080p`, `4k`, `1080` gibi etiketlerin hepsi reddediliyor |
| Yanıt | **Senkron ve ikili**: iş kimliği değil, doğrudan MP4 gövdesi döner (~32 sn) |

Son iki satır sürpriz oldu ve kodun ikisine de uyarlanması gerekti. Model listesi
uç noktası (`/models` vb.) yok.

**Çözünürlük değerinin yazımı modele özgü.** `1080p` ve `480p` gibi görünür
adlar `ltx-2-3-fast` tarafından reddediliyor; API farklı bir enum bekliyor.
Hangi değerlerin kabul edildiğini bulmak için:

```bash
make probe ARGS="--discover"     # ya da: python -m pipeline.cli probe --discover
```

Bu, bilinen tüm yazımları sırayla dener. **Reddedilen istek faturalanmaz** —
üretim hiç başlamaz — yani kabul edilen ilk değere kadar hiçbir maliyeti yok.

Model kimliği: `ltx-2-fast` ve `ltx-2-pro` **15 Ağustos 2026'da kapatıldı**.
Varsayılan `ltx-2-3-fast`.

---

## 6. Maliyet

Video üretimi maliyetin neredeyse tamamı. LTX saniye başına ücretlendirir, yani
6 dakikalık videoyu baştan sona üretmek ~$14 tutardı. Bu yüzden hat, **klip
havuzunu döngüye sokar**: her klip ileri+geri (kusursuz döngü) hâline getirilir,
zaman çizelgesine sırayla dağıtılır (aynı klip arka arkaya gelmez, tekrarlar
birbirinden uzak düşer) ve her tekrar farklı bir kareden başlar.

Varsayılan ayarla:

| Kalem | Hesap | Tutar |
|---|---|---|
| LTX uzun form | 12 klip × 8 sn × $0.04 | $3.84 |
| Shorts | uzun form kliplerinden kırpma | $0.00 |
| Claude | konu + senaryo | ~$0.15 |
| Seslendirme | edge-tts | $0.00 |
| **Günlük** | | **~$4** |
| **Aylık** | | **~$120** |

`make plan` bu tabloyu senin ayarlarınla basar.

**Sert bütçe tavanı:** `budget.max_usd_per_run` aşılırsa çalıştırma
*hiçbir şey harcamadan* durur. Gözetimsiz çalışan bir otomasyonda bu şart.

Ucuzlatmak için: `video.max_clips` düşür (8 klip = $2.56) veya
`video.segment_seconds` artır. Shorts'a ayrı dikey klip ürettirmek istersen
`shorts.clip_strategy: regenerate` — maliyeti artırır.

---

## 7. Bilmen gereken sınırlar

- **YouTube kotası:** günlük 10.000 birim, her yükleme 1.600 birim. Günde iki
  video = 3.200. Rahat, ama günde 6 yüklemeden fazlasını yapamazsın.
- **Özel küçük resim** doğrulanmış kanal ister. Kanalın doğrulanmamışsa yükleme
  yine başarılı olur, sadece küçük resim atlanır (hata değil, uyarı).
- **Olgusal doğruluk:** senaryoyu insan kontrol etmiyor. `prompts/ideation.md`
  modele "emin olmadığın konuyu seçme" diyor ve `key_facts` alanı zorunlu, ama
  bu bir garanti değil. Hassas nişlerde ilk haftalar çıktıları elle oku.
- **Telif ve YouTube politikası:** üretilen içerik senin sorumluluğunda. Yeniden
  kullanılmış içerik politikası, tekrarlayan/otomatik içerik kuralları geçerli.
- **edge-tts** resmî olmayan bir uçtur ve Microsoft tarafında değişebilir.
  Kritikse `voice.provider: elevenlabs` daha sağlam bir zemin.
- **GitHub Actions cron** garantili değildir; yoğunlukta gecikebilir. Yayın saati
  `publishAt`'e bağlı olduğu için bu bizi etkilemez, yeter ki render yayın
  saatinden önce bitsin. 12:00 UTC → 19:00 TSİ arasında 4 saat pay var.
- **Konu tekrarını** `state/history.json` engelliyor ve bu dosya her çalıştırmada
  repoya geri yazılıyor. Workflow'un yazma izni yoksa sistem zamanla kendini
  tekrar etmeye başlar.

---

## 8. Proje yapısı

```
pipeline/
  cli.py           komut satırı (doctor / plan / probe / auth / run)
  run.py           günlük akışın orkestrasyonu
  config.py        config.yaml + ortam değişkenleri
  writer.py        konu seçimi ve senaryo (Claude API)
  clips.py         prompt'ları paralel olarak kliplere çevirir
  providers/       ltx.py, fal_ltx.py, base.py (şema arama yardımcıları)
  voice.py         edge-tts / ElevenLabs / silent + kelime zamanlamaları
  captions.py      kelime senkron ASS altyazı üretimi
  assemble.py      zaman çizelgesi planı + ffmpeg montaj
  thumbnail.py     kareden küçük resim üretimi
  youtube.py       yükleme + publishAt planlama
  budget.py        maliyet tahmini ve sert tavan
  state.py         konu geçmişi / tekrar engelleme
prompts/           kanal tonu ve yazım kuralları (düz metin)
tests/             68 test, biri uçtan uca (sahte sağlayıcı ile)
.github/workflows/daily.yml
```
