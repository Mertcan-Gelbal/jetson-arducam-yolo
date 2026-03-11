# Mac’ten Jetson’ı Yönetme Rehberi

Bu rehber, kamera takılı Jetson cihazınızı **Mac bilgisayarınızdan** tek ekrandan yönetebilmeniz için adım adım ne yapmanız gerektiğini anlatır.

---

## Ne yapacağız?

- Mac’te **VisionDock** arayüzünü açacaksınız.
- Jetson’ı **ZeroTier** ile internette “sanal ağda” göreceksiniz.
- Arayüzden Jetson’ın **açık mı / kapalı mı** olduğunu görecek, container’ları yöneteceksiniz.

---

## Önce hazırlık (bir kez)

### 1. ZeroTier hesabı ve ağ

1. [zerotier.com](https://zerotier.com) üzerinden ücretsiz hesap açın.
2. Bir **Network** oluşturun; **Network ID**’yi kopyalayın (örn. `a1b2c3d4e5f6g7h8`).
3. Hem Mac hem Jetson’da ZeroTier kurulu olsun:
   - **Mac:** [ZeroTier indirme](https://zerotier.com/download/) → uygulamayı açın, “Join Network” ile Network ID’yi girin.
   - **Jetson:** Aynı Network ID ile ağa katılın (örn. `zerotier-cli join <Network ID>`).

### 2. Jetson’da Docker’ı uzaktan açma (bir kez)

Mac’in Jetson’daki Docker’a bağlanabilmesi için Jetson’da **bir kez** şu adımları uygulayın.

**SSH ile Jetson’a bağlanın**, sonra:

```bash
# Docker için uzaktan erişim klasörü
sudo mkdir -p /etc/systemd/system/docker.service.d

# Docker’ın 2375 portunu dinlemesini sağlayan ayar
echo '[Service]
ExecStart=
ExecStart=/usr/bin/dockerd -H fd -H tcp://0.0.0.0:2375' | sudo tee /etc/systemd/system/docker.service.d/override.conf

# Değişikliği uygula
sudo systemctl daemon-reload
sudo systemctl restart docker
```

**Güvenlik:** Bu ayar sadece güvendiğiniz ağlarda (ZeroTier gibi) kullanılmalıdır.

### 3. Jetson’ın ZeroTier IP’sini öğrenme

Jetson’da (SSH veya ekranında) şunu çalıştırın:

```bash
zerotier-cli listnetworks
```

Çıktıda **assignedAddresses** satırında `10.x.x.x` gibi bir IP görünecek. Bunu not alın; örn. `10.144.1.5`.

---

## Mac’te VisionDock’u çalıştırma

1. Projeyi Mac’e klonlayın (veya zaten varsa o klasöre gidin):
   ```bash
   cd jetson-arducam-yolo
   ```
2. Gerekli bağımlılıklar yüklüyse:
   ```bash
   python3 gui/main.py
   ```
   veya `./start_gui.sh` (Linux/Jetson için; Mac’te doğrudan `python3 gui/main.py` kullanabilirsiniz).
3. Üst menüden **Settings (Ayarlar)** sekmesine geçin.

---

## Arayüzde bağlantıyı kurma (3 adım)

### Adım 1: Uzak cihaz IP’si

**Settings** sayfasında **“Bağlantı ve entegrasyon”** bölümünü bulun.

- **“Uzak cihaz (Jetson) IP”** kutusuna Jetson’ın ZeroTier IP’sini yazın (örn. `10.144.1.5`).
- Listede gördüğünüz **ZT eşleri (ID)** butonları cihaz kimliğidir; kutuya **IP adresini** yazmanız gerekir (yukarıda `zerotier-cli listnetworks` ile bulduğunuz).

### Adım 2: Cihaz durumunu kontrol etme

- Hemen altında **“Cihaz durumu”** satırı vardır.
- **Çevrimiçi** (yeşil): Jetson açık ve ağdan erişilebiliyor; Mac’ten yönetebilirsiniz.
- **Çevrimdışı** (kırmızı): Bağlantı yok; aşağıdaki “Sorun giderme” adımlarına bakın.
- **Yerel**: IP kutusu boşsa “bu bilgisayar” modundasınız demektir.

### Adım 3: Container’ları yönetme

- **Workspaces** sekmesine dönün.
- Container listesi artık **Jetson’daki** container’ları gösterecektir.
- Log, Shell vb. işlemler Jetson üzerinde çalışır.

---

## Kısa kontrol listesi

| Yapıldı mı? | Ne yapılacak? |
|-------------|----------------|
| ☐ | ZeroTier aynı ağda hem Mac hem Jetson’da |
| ☐ | Jetson’da Docker 2375 ile açıldı (`override.conf` + restart) |
| ☐ | Jetson’ın ZT IP’si biliniyor (`zerotier-cli listnetworks`) |
| ☐ | Mac’te VisionDock açık, Settings’te bu IP yazılı |
| ☐ | “Cihaz durumu” **Çevrimiçi** görünüyor |

---

## Sık karşılaşılan sorunlar

### “Cihaz durumu” hep Çevrimdışı

1. **Jetson açık mı?** (güç, ağ kablosu)
2. **Aynı ZeroTier ağında mı?** Her iki tarafta `zerotier-cli listpeers` ile eşler görünmeli.
3. **IP doğru mu?** Jetson’da `zerotier-cli listnetworks` ile kontrol edin; Mac’te yazdığınız IP ile aynı olmalı.
4. **Docker 2375 açık mı?** Jetson’da: `ss -tlnp | grep 2375` veya `sudo systemctl status docker` ile kontrol edin.

### Container listesi boş veya hata veriyor

- Önce “Cihaz durumu” **Çevrimiçi** olmalı.
- Jetson’da `docker ps` çalışıyor mu kontrol edin.
- Mac’te Docker CLI kurulu olmalı (`docker --version`).

### ZeroTier’da eş görünmüyor

- Her iki tarafta ZeroTier uygulaması/servisi çalışıyor olmalı.
- ZeroTier web panelinden her iki cihazın ağa “Authorized” olarak eklendiğinden emin olun.

---

## Özet

1. **Jetson’da:** ZeroTier’a katıl, Docker’ı 2375’te aç, ZT IP’yi not et.
2. **Mac’te:** VisionDock’u aç, Settings → Uzak cihaz IP’sine bu IP’yi yaz.
3. **Cihaz durumu** Çevrimiçi olunca Workspaces’ten Jetson’ı yönetebilirsiniz.

Detaylı komutlar ve güvenlik notları için **USAGE.md** içindeki “Remote Management (Mac → Jetson)” bölümüne bakabilirsiniz.
