# Modbus Cihaz Test Aracı

Register tablosuna göre Modbus cihazlarını test eden, kendi penceresi olan masaüstü uygulaması (Windows).

## Kurulum

[Releases](../../releases/latest) sayfasından **ModbusTester.exe** dosyasını indirip çift tıklayın.
Python veya başka bir kurulum gerekmez. Windows SmartScreen uyarı verirse "Ek bilgi → Yine de çalıştır" seçin.

Raporlar `Belgeler\ModbusTester Raporlar` klasörüne, ayarlar `%APPDATA%\ModbusTester` altına kaydedilir.
Uygulama son kullanılan bağlantı ayarlarını ve tabloyu hatırlar.

Kaynak koddan çalıştırmak için: `pip install -r requirements.txt` ve `python gui.py`.
(Tarayıcı arayüzü `python app.py`, komut satırı `python cli.py` olarak da duruyor.)

## Kullanım

1. **Bağlantı ayarları (sol üst):** Bağlantı tipi (Modbus RTU seri, Modbus TCP, RTU over TCP veya Simülatör), port,
   slave adresi, baud rate, parity, data bit, stop bit, zaman aşımı, tekrar sayısı, istekler arası bekleme.
   "Bağlantıyı dene" ile tek register okunur.
2. **Register tablosu (sol alt):** "Tablo yükle..." ile PDF, Excel (.xlsx) veya CSV seçin. Örnek tablo formatındaki kolonlar
   (Address, Address Hex, Format, Word Counts, MCU Byte, Designation, Unit, R/W, Remarks, Multiplier,
   Value If Not Available, Min, Max) ve Türkçe karşılıkları otomatik tanınır. Tablodaki tutarsızlıklar
   (Min > Max, R/W bilgisi olmayan satır vb.) uyarı olarak listelenir.
3. **Register listesi** sekmesinde testten çıkarmak istediğiniz registerlara çift tıklayın.
4. **Register testi** veya **Haberleşme ayarı tarama** sekmesinden seçenekleri ayarlayıp başlatın. İlerleme ve log alt kısımda görünür.
5. Test başlayınca **Sonuçlar (canlı)** sekmesi açılır; rapor tablosu test ilerledikçe gerçek zamanlı dolar
   ("Canlı takip" o an test edilen registera kaydırır). Bitince "Raporu aç (HTML)", "Excel'i aç" ve
   "Farklı kaydet..." ile rapor alınır.
6. **Manuel okuma / yazma** sekmesi: tablodaki tüm registerlar listelenir. Seçilileri veya tümünü okuyabilir
   (bitişik olanlar blok halinde okunur), "Sürekli oku" ile belirli aralıkla canlı izleyebilir, seçili registera
   değer yazıp geri okuyarak doğrulayabilirsiniz. "Serbest adres" alanı tabloda olmayan adresleri de FC03/FC04 ile
   okumaya ve FC06/FC16 ile yazmaya izin verir.

Cihaz olmadan denemek için bağlantı tipini **Simülatör** seçin: yüklenen tablodan sanal bir cihaz oluşturulur.
"Simülatöre örnek hatalar ekle" seçeneği, hatalı bir raporun nasıl göründüğünü gösterir.

## Register testi neleri yapar

| Test | R | R/W | W | Açıklama |
|---|---|---|---|---|
| Okuma | ✔ | ✔ | – | FC03 (R registerlar için isteğe bağlı FC04). Değer Min/Max dışındaysa UYARI. |
| Yazma + doğrulama | – | ✔ | isteğe bağlı | Önce orijinal değer okunur, Min ve Max yazılır, her yazmadan sonra geri okunup karşılaştırılır, sonra orijinal değer geri yazılıp doğrulanır. Min/Max yoksa mevcut değer geri yazılır. |
| Blok okuma | ✔ | ✔ | – | Bitişik registerlar blok boyutu kadar (varsayılan 16) tek istekte okunur. R/W değerleri tekli okumayla karşılaştırılır. |
| Blok yazma | – | ✔ | – | Bitişik R/W registerlara FC16 ile aralık içi farklı değerler yazılır, blok olarak geri okunup doğrulanır, sonra orijinaller geri yüklenir. |
| Salt okunur koruma | ✔ | – | – | R registera mevcut değeri yazmayı dener; cihaz Modbus istisnası ile reddetmelidir (değer zaten aynı olduğu için cihaz kabul etse bile değişiklik olmaz). |
| Aralık dışı koruma | – | ✔ | – | (İsteğe bağlı) Min−1 ve Max+1 yazılır; cihaz reddetmeli veya saklamamalıdır. |

Haberleşme ayar registerları (Modbus adres, baud, parity, stop bit, data bit) isimlerinden otomatik tanınır ve
normal yazma testinden çıkarılır, çünkü bunlar cihazla bağlantıyı koparır. Bunlar ayar tarama modunda test edilir.

Sonuç değerleri: **OK**, **UYARI**, **HATA**, **ATLANDI**, **-** (bu register için uygulanamaz).

## Haberleşme ayarı tarama modu

Başlangıç ayarı soldaki bağlantı ayarlarıdır. Her parametre için:

1. Mevcut ayarla ayar registerına yeni değer yazılır (cihaz yanıt vermeden hemen geçerse bu da tolere edilir).
2. Beklenir (varsayılan 1500 ms), yeni ayarla (ör. 19200 baud) bağlanılır ve ayar registerı okunarak doğrulanır.
3. Fonksiyon testi: yeni ayarla tablodaki ilk registerlar blok okunur ve bir R/W registera yazıp geri okunur.
4. İsteğe bağlı: eski ayarla cihazın artık yanıt vermediği kontrol edilir.
5. Cihaz yeni ayarla yanıt vermezse önce eski ayarla denenir, o da olmazsa bilinen tüm adres/baud/parity
   kombinasyonlarında aranır (kurtarma araması) ve bulunduğu ayar rapora yazılır.

"Sıralı" modda her parametre tek tek denenir ve sonra orijinal değerine döndürülür. "Tüm kombinasyonlar" modunda
seçili parametrelerin bütün kombinasyonları (ör. 3 baud × 3 parity) sırayla kurulur. Sonunda cihaz orijinal ayarlarına
döndürülür; tarama bittiğinde arayüzdeki bağlantı ayarları cihazın son ayarına güncellenir.

Test değerleri "ham=ayar" biçiminde düzenlenebilir: örnek tablo için baud `0=4800, 1=9600, 2=19200`,
parity `0=N, 1=O, 2=E`, adres `1=1, 124=124, 247=247`. Cihazınız ayarı ayrı bir "uygula/kaydet" registerı ile
etkinleştiriyorsa "Ayarı uygulama registerı" alanına `adres=değer` yazın.

## Komut satırı (otomasyon)

```
python cli.py test --table tablo.pdf --port COM3 --slave 1 --baud 9600 --parity N
python cli.py test --table tablo.pdf --port COM3 --range-check --skip 17127-17128
python cli.py scan --table tablo.pdf --port COM3 --addresses 1,2,247
python cli.py test --table tablo.pdf --sim
python cli.py export --table tablo.pdf --out tablo.csv
```
Herhangi bir HATA varsa çıkış kodu 1 olur. Tüm seçenekler için `python cli.py test -h`.

## Simülatörü gerçek seri portta çalıştırma

İki sanal COM port (ör. com0com ile COM5–COM6) veya iki USB-RS485 dönüştürücü ile:
```
python -m modbus_tester.simulator --table tablo.pdf --port COM6 --slave 1 --baud 9600
```
ve uygulamada COM5'i seçin.

## Dikkat edilecekler

- Yazma testleri cihazdaki ayarları değiştirir. Orijinal değerler test sonunda geri yazılır, ama test yarıda
  kesilirse bazı registerlar test değerinde kalabilir. Üretimdeki bir cihazda çalıştırmadan önce bunu göz önünde bulundurun.
- Şifre / şifre aktif, fabrika ayarı, sayaç silme gibi registerları gerekirse "Register listesi" sekmesinden atlayın.
- Tablodaki adresler doğrudan protokol adresi kabul edilir (örnek tabloda 11000 = 0x2AF8). Tablonuz 40001 tabanlı ise
  "Adres ofseti" alanına 40001 yazın.
- 32-bit değerler (UINT32/INT32/FLOAT32, Word Counts = 2) desteklenir; word sırası seçilebilir.
- Min/Max ham register değeri olarak yorumlanır (çarpan uygulanmaz). Negatif Min değeri olan registerlar işaretli
  (ikiye tümleyen) olarak yorumlanır.

## Dosyalar

- `gui.py` – masaüstü uygulaması (Windows exe bununla derlenir)
- `app.py` – tarayıcı arayüzü (alternatif)
- `.github/workflows/build-windows.yml` – Windows exe derlemesi
- `cli.py` – komut satırı
- `modbus_tester/rtu.py` – Modbus RTU/TCP istemcisi (FC03/04/06/16, CRC, zaman aşımı, tekrar)
- `modbus_tester/table.py` – tablo import (PDF/XLSX/CSV), haberleşme parametresi tespiti
- `modbus_tester/tester.py` – register testleri
- `modbus_tester/commscan.py` – haberleşme ayarı tarama modu
- `modbus_tester/report.py` – Excel/HTML/CSV raporlar
- `modbus_tester/simulator.py` – tablodan sanal cihaz
- `ornek_DTR-20S-TS.pdf`, `ornek_DTR-20S-TS_tablo.csv` – örnek tablo ve CSV hali
