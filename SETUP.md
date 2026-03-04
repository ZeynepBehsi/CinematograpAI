# Cinema Graph — Kurulum Rehberi

Projeyi sıfırdan çalışır hale getirmek için aşağıdaki adımları sırayla uygulayın.
Tüm adımlar tamamlandığında, hiçbir manuel müdahale gerektirmeden temiz ve güncel bir graf elde edilir.

---

## Ön Koşullar

| Bileşen | Versiyon | Notlar |
|---|---|---|
| Python | 3.11+ | |
| Memgraph | 2.x | `bolt://localhost:7687` üzerinde çalışıyor olmalı |
| TMDb API Anahtarı | — | `TMDB_API_KEY` ortam değişkeni |
| Google Gemini API Anahtarı | — | `GEMINI_API_KEY` ortam değişkeni |
| Node.js | 18+ | Frontend için |

---

## Adım 0 — Memgraph'ı Başlat

Memgraph'ın çalışır durumda olduğundan emin ol. Çalışmıyorsa Docker ile başlat:

```bash
docker run -d --name memgraph-platform \
  -p 7687:7687 -p 3000:3000 \
  -v mg_data:/var/lib/memgraph \
  memgraph/memgraph-platform
```

Doğrulama: `docker ps` çıktısında `memgraph-platform` görünmeli ve `7687` portu açık olmalı.
Memgraph Lab arayüzü için: `http://localhost:3000`

---

## Adım 1 — Bağımlılıkları Yükle

```bash
pip install -r requirements.txt
```

---

## Adım 2 — Ortam Değişkenlerini Ayarla

Proje kök dizininde `.env` dosyası oluşturun:

```
TMDB_API_KEY=your_tmdb_api_key_here
GEMINI_API_KEY=your_gemini_api_key_here
```

---

## Adım 3 — TMDb'den Filmografi Verisi Çek

34 seed yönetmenin filmografisini TMDb API'den çeker.
`data/films.json`, `data/persons.json`, `data/relationships.json` dosyalarını oluşturur.

```bash
python scripts/fetch_filmography.py
```

Sadece belirli yönetmenleri çekmek için:
```bash
python scripts/fetch_filmography.py --only "Ingmar Bergman" "David Lynch"
```

**Beklenen çıktı:** ~699 film, ~6200 kişi, ~10 000+ ilişki

---

## Adım 4 — Veriyi Memgraph'a Yükle

DB'yi temizler, tüm JSON verilerini yükler ve ardından otomatik temizlik adımlarını uygular:

- Müzik video derlemelerini siler (Michael Jackson, Madonna)
- Antoloji filmlere `:Anthology` label'ı ekler
- Twin Peaks (1989) TV pilotunu siler (Fire Walk with Me korunur)
- Ingmar Bergman duplikat node'larını birleştirir (canonical tmdb_id=6648)

```bash
python scripts/load_to_memgraph.py
```

**Beklenen çıktı:** ~692 Film, ~6195 Person, ~10 000+ ilişki

---

## Adım 5 — Temel Zenginleştirme (Movement + INFLUENCED_BY)

24 sinema akımı (Movement) node'u, 34 PART_OF_MOVEMENT ve 10 INFLUENCED_BY ilişkisi ekler.

```bash
python scripts/load_enrichment.py
```

---

## Adım 6 — Genişletilmiş Zenginleştirme

22 yeni INFLUENCED_BY ilişkisi ekler (MERGE ile duplikat oluşturmaz).
Kanıtsız 2 INFLUENCED_BY ilişkisini siler.
Movement ve PART_OF_MOVEMENT güncellemeleri MERGE ile idempotent çalışır.

```bash
python scripts/update_enrichment.py
```

**Beklenen durum sonrası:**
- 24 Movement node
- 32 INFLUENCED_BY ilişkisi
- 34 PART_OF_MOVEMENT ilişkisi

---

## Adım 7 — Film Rating'lerini Çek

Her film için TMDb'den `vote_average` ve `vote_count` değerlerini çekip
Film node'larına `rating` ve `vote_count` property olarak yazar.

```bash
python scripts/fetch_ratings.py
```

Ek seçenekler:
```bash
python scripts/fetch_ratings.py --limit 50          # İlk 50 film (test)
python scripts/fetch_ratings.py --skip-existing     # Zaten rating olanları atla
python scripts/fetch_ratings.py --dry-run           # Grafa yazmadan önizle
python scripts/fetch_ratings.py --min-votes 100     # Düşük oy sayılı filmleri atla
```

---

## Adım 8 — Ödül Verilerini Yükle

`data/awards.json` dosyasındaki manuel küratörlü ödül verilerini grafa yükler.
Award node'ları oluşturur; `(Film)-[:WON_AWARD]->(Award)` ve `(Film)-[:NOMINATED_FOR]->(Award)` ilişkilerini ekler.

```bash
python scripts/fetch_awards.py
```

Ek seçenekler:
```bash
python scripts/fetch_awards.py --dry-run   # Grafa yazmadan önizle
python scripts/fetch_awards.py --stats     # İstatistikleri göster
```

**Kapsam:** ~113 film, ~213 ödül kaydı (Cannes, Venedik, Berlin, Oscar)

---

## Adım 9 — Doğrulama

Grafın beklenen durumda olduğunu kontrol etmek için Memgraph Lab veya bolt shell ile:

```cypher
-- Node sayıları
MATCH (n) RETURN labels(n)[0] AS label, count(n) AS count ORDER BY count DESC

-- İlişki sayıları
MATCH ()-[r]->() RETURN type(r) AS type, count(r) AS count ORDER BY count DESC

-- INFLUENCED_BY zinciri örneği
MATCH p=(a:Person)-[:INFLUENCED_BY*1..3]->(b:Person)
WHERE a.name = "Nuri Bilge Ceylan"
RETURN p LIMIT 10
```

---

## Adım 10 — Backend'i Başlat

```bash
cd web/backend
bash run.sh
```

Backend `http://localhost:8000` üzerinde çalışır.
`GEMINI_API_KEY` ortam değişkeni `.env` dosyasında tanımlı olmalıdır.

---

## Adım 11 — Frontend'i Başlat

```bash
cd web/frontend
npm install
npm run dev
```

Frontend `http://localhost:3001` üzerinde çalışır.
`/api` istekleri Vite proxy ile `localhost:8000`'e yönlendirilir.

---

## Graf Modeli

### Node Tipleri

| Label | Temel Property'ler |
|---|---|
| `Film` | `tmdb_id`, `title`, `year`, `runtime`, `rating`, `vote_count` |
| `Person` | `tmdb_id`, `name` |
| `Genre` | `name` |
| `Studio` | `name` |
| `Country` | `name` |
| `Movement` | `name` |
| `Award` | `name`, `festival` |

### İlişki Tipleri

| Tip | Yön | Açıklama |
|---|---|---|
| `DIRECTOR` | Person → Film | Yönetmen |
| `ACTOR` | Person → Film | Oyuncu |
| `DIRECTOR_OF_PHOTOGRAPHY` | Person → Film | Görüntü yönetmeni |
| `EDITOR` | Person → Film | Kurgucu |
| `ORIGINAL_MUSIC_COMPOSER` | Person → Film | Müzik |
| `SOUND_DESIGNER` | Person → Film | Ses tasarımı |
| `INFLUENCED_BY` | Person → Person | Sinematik etki |
| `PART_OF_MOVEMENT` | Person → Movement | Sinema akımı üyeliği |
| `HAS_GENRE` | Film → Genre | Tür |
| `PRODUCED_BY` | Film → Studio | Yapım şirketi |
| `FROM_COUNTRY` | Film → Country | Yapım ülkesi |
| `WON_AWARD` | Film → Award | Ödül kazanma |
| `NOMINATED_FOR` | Film → Award | Ödül adaylığı |

---

## Seed Yönetmenler (34)

Andrei Tarkovsky, Stanley Kubrick, Ingmar Bergman, Woody Allen, Alfred Hitchcock,
Federico Fellini, Akira Kurosawa, Jean Renoir, David Fincher, Quentin Tarantino,
Paul Thomas Anderson, Nuri Bilge Ceylan, Zeki Demirkubuz, David Lynch,
Jean-Luc Godard, François Truffaut, Michelangelo Antonioni, Krzysztof Kieślowski,
Lars von Trier, Michael Haneke, Wim Wenders, Pedro Almodóvar, Wong Kar-wai,
Yasujirō Ozu, Abbas Kiarostami, Park Chan-wook, Bong Joon-ho, Hirokazu Kore-eda,
Martin Scorsese, Joel Coen, Terrence Malick, Spike Lee, Yılmaz Güney, Semih Kaplanoğlu

---

## Tekrar Çalıştırma

Adım 4 (`load_to_memgraph.py`) DB'yi tamamen temizleyip yeniden yükler — idempotent değildir.
Adımlar 5, 6, 7 ve 8 MERGE kullandığı için idempotent olup güvenle tekrar çalıştırılabilir.
