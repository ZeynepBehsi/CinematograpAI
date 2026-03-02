# CinematograpAI

14 usta yönetmenin filmografisini, oyuncu kadrolarını, sinema akımlarını ve yönetmenler arası etkilenme ilişkilerini graph veritabanında modelleyen, doğal dil sorgusuyla keşfedilebilen bir sinema analiz sistemi.

---

## İçindekiler

1. [Proje Mimarisi](#1-proje-mimarisi)
2. [Graph Şeması](#2-graph-şeması)
3. [Veri Pipeline'ı](#3-veri-pipelineı)
4. [Kurulum](#4-kurulum)
5. [Projeyi Sıfırdan Çalıştırma](#5-projeyi-sıfırdan-çalıştırma)
6. [Web Uygulaması](#6-web-uygulaması)
7. [API Referansı](#7-api-referansı)
8. [Memgraph Cypher Kuralları](#8-memgraph-cypher-kuralları)
9. [Örnek Sorgular](#9-örnek-sorgular)
10. [Graph Algoritmaları](#10-graph-algoritmaları)
11. [Proje Yapısı](#11-proje-yapısı)
12. [Bilinen Sorunlar ve Çözümleri](#12-bilinen-sorunlar-ve-çözümleri)

---

## 1. Proje Mimarisi

```
TMDb API
    │  fetch_filmography.py
    ▼
data/{films,persons,relationships}.json
    │  load_to_memgraph.py
    ▼
Memgraph (bolt://localhost:7687)
    │  load_enrichment.py + update_enrichment.py
    │  (INFLUENCED_BY, PART_OF_MOVEMENT, dış kişiler)
    ▼
FastAPI Backend (port 8000)
    │  Gemini 2.5 Flash → Cypher üretimi
    ▼
React + Vite Frontend (port 3001)
    Cytoscape.js graph görselleştirmesi
```

**Teknoloji Yığını**

| Katman | Teknoloji |
|---|---|
| Graph DB | Memgraph (Bolt protokolü, port 7687) |
| Veri kaynağı | TMDb API v3 |
| AI / NLP | Google Gemini 2.5 Flash (`google-generativeai`) |
| Backend | FastAPI + Uvicorn (Python 3.10+) |
| Frontend | React 18 + Vite + Tailwind CSS |
| Graph görsel | Cytoscape.js + react-cytoscapejs |
| DB sürücüsü | neo4j Python driver (Memgraph uyumlu) |

---

## 2. Graph Şeması

### Node Tipleri

| Label | Açıklama | Properties |
|---|---|---|
| `Person` | Yönetmen, oyuncu, DP, besteci, kurgucu, ses tasarımcısı + edebi etkiler (Dostoevsky, Chekhov vb.) | `tmdb_id` (int), `name` (string) |
| `Film` | Seed yönetmenlerin filmleri | `tmdb_id` (int), `title` (string), `year` (int), `runtime` (int) |
| `Genre` | Film türleri | `name` (string) |
| `Studio` | Yapım şirketleri | `name` (string) |
| `Country` | Yapım ülkeleri | `name` (string) |
| `Movement` | Sinema akımları ve dönemleri | `name` (string) |

### İlişki Tipleri

| İlişki | Yön | Adet |
|---|---|---|
| `ACTOR` | (Person)→(Film) | 3 141 |
| `DIRECTOR` | (Person)→(Film) | 436 |
| `DIRECTOR_OF_PHOTOGRAPHY` | (Person)→(Film) | 411 |
| `EDITOR` | (Person)→(Film) | 380 |
| `ORIGINAL_MUSIC_COMPOSER` | (Person)→(Film) | 241 |
| `SOUND_DESIGNER` | (Person)→(Film) | 92 |
| `HAS_GENRE` | (Film)→(Genre) | 684 |
| `PRODUCED_BY` | (Film)→(Studio) | 689 |
| `FROM_COUNTRY` | (Film)→(Country) | 439 |
| `PART_OF_MOVEMENT` | (Person)→(Movement) | 16 |
| `INFLUENCED_BY` | (etkilenen Person)→(etkileyen Person) | 13 |

> **INFLUENCED_BY yön notu:** Ok yönü *etkileyen kişiye doğru*'dur.
> Örnek: `(Nuri Bilge Ceylan)-[:INFLUENCED_BY]->(Andrei Tarkovsky)`

### Toplam Graf Büyüklüğü

- **3 671 node** (Person: 2 966, Film: 321, Studio: 330, Country: 24, Genre: 17, Movement: 13)
- **6 542 ilişki**

### Seed Yönetmenler (14 kişi)

Andrei Tarkovsky, Stanley Kubrick, Ingmar Bergman, Woody Allen, Alfred Hitchcock,
Federico Fellini, Akira Kurosawa, Jean Renoir, David Fincher, Quentin Tarantino,
Paul Thomas Anderson, Nuri Bilge Ceylan, Zeki Demirkubuz, David Lynch

---

## 3. Veri Pipeline'ı

### Adım 1 — TMDb'den Veri Çekme (`fetch_filmography.py`)

Her seed yönetmen için TMDb API'sine 3 tip istek atılır:

```
GET /search/person?query={name}       → yönetmen TMDb ID'si
GET /person/{id}/movie_credits        → filmografi listesi
GET /movie/{id}?append_to_response=credits  → film detayı + crew + cast
```

**Filtreler:**
- `runtime >= 60` dk (kısa film değil)
- `vote_count >= 20` (yeterli oy)
- Crew: Director, Director of Photography, Original Music Composer, Sound Designer, Editor
- Cast: ilk 10 oyuncu (`ORDER BY order`)

Çıktı: `data/films.json`, `data/persons.json`, `data/relationships.json`

Script mevcut veriyi koruyarak merge eder — tekrar çalıştırmak güvenlidir.

```bash
# Tüm 14 yönetmen
python scripts/fetch_filmography.py

# Sadece belirli yönetmenler (mevcut veriyle merge eder)
python scripts/fetch_filmography.py --only "Ingmar Bergman" "David Lynch"
```

### Adım 2 — Memgraph'a Yükleme (`load_to_memgraph.py`)

Tüm DB'yi temizleyip JSON dosyalarından sıfırdan yükler:

1. Tüm node ve ilişkileri siler (`MATCH (n) DETACH DELETE n`)
2. Film node'ları oluşturur
3. Person node'ları oluşturur
4. Genre / Studio / Country node'larını MERGE eder, filmlerle bağlar
5. Person–Film ilişkilerini oluşturur

```bash
python scripts/load_to_memgraph.py
```

### Adım 3 — Zenginleştirme (`load_enrichment.py`)

TMDb'de bulunmayan ancak etkilenme zinciri için gerekli dış Person node'larını,
sinema akımlarını (Movement) ve INFLUENCED_BY ilişkilerini ekler:

**Eklenen dış kişiler:** Robert Bresson, Fyodor Dostoevsky
**Eklenen akımlar:** Soviet Poetic Cinema, New Hollywood, Scandinavian Art Cinema, Italian Neorealism, Italian Art Cinema, Japanese Golden Age Cinema, French Poetic Realism, Post-Classical Hollywood, Post-Modern Cinema, New Turkish Cinema, Surrealist Cinema, Classical Hollywood, British Cinema

```bash
python scripts/load_enrichment.py
```

### Adım 4 — Güncelleme (`update_enrichment.py`)

Kaynak araştırması sonucunda bulunan ek ilişkileri ekler ve
kanıt yetersizliği nedeniyle hatalı olan ilişkileri siler:

**Eklenenler:**
- `(Nuri Bilge Ceylan)-[:INFLUENCED_BY]->(Robert Bresson)`
- `(Nuri Bilge Ceylan)-[:INFLUENCED_BY]->(Ingmar Bergman)`
- `(Nuri Bilge Ceylan)-[:INFLUENCED_BY]->(Anton Chekhov)`

**Silinenler (belgelenmiş kanıt yok):**
- `(Federico Fellini)-[:INFLUENCED_BY]->(Akira Kurosawa)` — karşılıklı hayranlık var, etki belgelenmemiş
- `(Federico Fellini)-[:INFLUENCED_BY]->(Jean Renoir)` — spesifik kanıt bulunamadı

```bash
python scripts/update_enrichment.py
```

---

## 4. Kurulum

### Gereksinimler

- Python 3.10+
- Node.js 18+
- Memgraph Community Edition (Docker önerilir)
- TMDb API anahtarı — [https://www.themoviedb.org/settings/api](https://www.themoviedb.org/settings/api)
- Google Gemini API anahtarı — [https://aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)

> **Gemini model notu:** Bu proje `gemini-2.5-flash` kullanır. `gemini-2.0-flash-lite` ve
> `gemini-2.0-flash` modellerinin ücretsiz tier kotaları projenin geliştirildiği sürede
> tükenmiştir — bu modelleri kullanmayın.

### Memgraph Kurulumu (Docker)

```bash
docker run -it -p 7687:7687 -p 3000:3000 memgraph/memgraph-platform
```

Memgraph Lab arayüzüne `http://localhost:3000` adresinden erişilir.
Bolt bağlantısı: `bolt://localhost:7687` (auth yok)

### Python Ortamı

```bash
cd web/backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

`requirements.txt` içeriği:
```
fastapi==0.115.0
uvicorn[standard]==0.30.0
neo4j==5.25.0
google-generativeai==0.8.0
pydantic==2.9.0
python-dotenv==1.0.1
```

Ayrıca veri çekme scriptleri için proje kökünde:
```bash
pip install requests python-dotenv
```

### Frontend Bağımlılıkları

```bash
cd web/frontend
npm install
```

### Ortam Değişkenleri

`web/backend/.env` dosyası oluşturun:

```env
MEMGRAPH_URI=bolt://localhost:7687
TMDB_API_KEY=<tmdb_api_anahtariniz>
GEMINI_API_KEY=<gemini_api_anahtariniz>
```

Veri çekme scriptleri aynı dosyadan okur — ya proje kökünde `.env` kopyalayın
ya da script çalıştırmadan önce ortam değişkenlerini export edin:

```bash
export TMDB_API_KEY=<anahtariniz>
```

---

## 5. Projeyi Sıfırdan Çalıştırma

### 5.1 Memgraph'ı Başlat

```bash
docker run -it -p 7687:7687 -p 3000:3000 memgraph/memgraph-platform
```

### 5.2 Veriyi Çek

```bash
cd cinema-graph
python scripts/fetch_filmography.py
# ~20-30 dakika sürer (TMDb rate limiting nedeniyle)
```

### 5.3 Memgraph'a Yükle

```bash
python scripts/load_to_memgraph.py
```

### 5.4 Zenginleştir

```bash
python scripts/load_enrichment.py
python scripts/update_enrichment.py
```

Bu noktada `http://localhost:3000` adresindeki Memgraph Lab'da sorgulama yapabilirsiniz.

### 5.5 Backend'i Başlat

```bash
cd web/backend
source .venv/bin/activate
bash run.sh
# Uvicorn http://0.0.0.0:8000 adresinde başlar
```

### 5.6 Frontend'i Başlat (ayrı terminal)

```bash
cd web/frontend
npm run dev
# Vite http://localhost:3001 adresinde başlar
```

Web uygulaması: **http://localhost:3001**

---

## 6. Web Uygulaması

### Ana Özellikler

- **Doğal dil sorgusu:** Türkçe veya İngilizce soru yazın, Gemini bunu Cypher'a çevirir ve Memgraph'ta çalıştırır
- **Graph görselleştirmesi:** Sonuçlar Cytoscape.js ile interaktif olarak gösterilir; node'lara tıklanabilir
- **Interpretation:** Her sorgu sonucu Türkçe, sinema tarihi bağlamında yorumlanır
- **Cypher görüntüle:** Üretilen sorguyu collapsible panelde inceleyin
- **Ham veri tablosu:** Tüm sonuçlar tablo formatında da görülebilir

### Node Renk Paleti

| Renk | Tip |
|---|---|
| Altın sarısı | Person |
| Turuncu | Film |
| Mavi-mor | Genre |
| Yeşil | Studio |
| Kırmızı | Country |
| Mor | Movement |

### Node'a Tıklama

Graph'taki herhangi bir node'a tıklandığında, o node hakkında otomatik yeni sorgu üretilir.

---

## 7. API Referansı

Backend `http://localhost:8000` adresinde çalışır.
Frontend `/api/*` isteklerini otomatik olarak backend'e proxy'ler (`vite.config.js`).

| Method | Endpoint | Açıklama |
|---|---|---|
| `GET` | `/health` | Memgraph bağlantı durumu |
| `GET` | `/stats` | Node ve ilişki sayıları |
| `GET` | `/schema` | Graph şeması (JSON) |
| `GET` | `/directors` | ≥5 film yapan yönetmenler |
| `GET` | `/director/{name}` | Yönetmen detayı (filmler, akımlar, etkiler) |
| `GET` | `/explore/{name}?depth=1` | Node komşuları (graph görselleştirme için) |
| `POST` | `/query` | Doğal dil → Cypher → sonuç |
| `GET` | `/debug/models` | Mevcut Gemini modelleri |

### `/query` İstek Formatı

```json
POST /query
{
  "question": "Hitchcock ve Kubrick'in ortak oyuncuları kimler?",
  "conversation_id": null
}
```

### `/query` Yanıt Formatı

```json
{
  "question": "...",
  "cypher_query": "MATCH ...",
  "raw_results": [...],
  "interpretation": "Markdown formatında Türkçe yorum",
  "graph_data": {
    "nodes": [{"id": "Person:James Mason", "label": "James Mason", "type": "Person"}],
    "edges": [{"id": "...", "source": "...", "target": "...", "type": "RELATED_TO"}]
  },
  "error": null
}
```

---

## 8. Memgraph Cypher Kuralları

> Memgraph, Neo4j ile büyük ölçüde uyumludur ancak bazı önemli farklar vardır.
> Bu kurallara uymayan sorgular hata verir.

### Kural 1 — Aggregation WITH'te olmalı

Aggregation fonksiyonları (`count`, `sum`, `collect`, `avg` vb.) doğrudan `RETURN`'de kullanılamaz.
Önce `WITH` içinde aggregate edin:

```cypher
-- YANLIŞ (Neo4j'de çalışır, Memgraph'ta hata verir)
MATCH (n:Film)
RETURN count(n) AS total

-- DOĞRU
MATCH (n:Film)
WITH count(n) AS total
RETURN total
```

```cypher
-- YANLIŞ
MATCH (d:Person)-[:DIRECTOR]->(f:Film)
RETURN d.name, count(f) ORDER BY count(f) DESC

-- DOĞRU
MATCH (d:Person)-[:DIRECTOR]->(f:Film)
WITH d.name AS director, count(f) AS film_count
RETURN director, film_count
ORDER BY film_count DESC
```

### Kural 2 — Graph Görselleştirme için RETURN p

Memgraph Lab'da graph görseli almak için path döndürün:

```cypher
-- Sadece liste döner (tablo)
MATCH (d:Person)-[:DIRECTOR]->(f:Film)
WHERE d.name = 'Stanley Kubrick'
RETURN d.name, f.title

-- Graph görseli döner
MATCH p=(d:Person {name: 'Stanley Kubrick'})-[:DIRECTOR]->(f:Film)
RETURN p
```

### Kural 3 — Boşluklu İlişki Tipleri Backtick İster

```cypher
-- Boşluk içeren tip adları backtick ile sarılmalı
MATCH (p:Person)-[:`DIRECTOR_OF_PHOTOGRAPHY`]->(f:Film)
RETURN p.name, f.title
```

> Not: `DIRECTOR_OF_PHOTOGRAPHY` zaten alt çizgili, ancak veri yükleme sırasında
> `Director of Photography` → `DIRECTOR_OF_PHOTOGRAPHY` dönüşümü otomatik yapılır.

### Kural 4 — String Karşılaştırma Büyük/Küçük Harf Duyarlı

```cypher
-- 'kubrick' aramaz, 'Kubrick' bulur
MATCH (p:Person)
WHERE p.name CONTAINS 'Kubrick'
RETURN p.name

-- Büyük/küçük harf duyarsız arama için toLower() kullanın
MATCH (p:Person)
WHERE toLower(p.name) CONTAINS toLower('kubrick')
RETURN p.name
```

### Kural 5 — MAGE Algoritmaları CALL ile Çağrılır

```cypher
CALL pagerank.get()
YIELD node, rank
WITH node, rank
WHERE rank > 0.001
RETURN node.name AS name, rank
ORDER BY rank DESC LIMIT 20
```

---

## 9. Örnek Sorgular

Web arayüzüne doğal dilde yazabileceğiniz sorular ve bunların ürettiği Cypher örnekleri:

### Yönetmen Filmografisi

```cypher
MATCH (d:Person {name: 'Andrei Tarkovsky'})-[:DIRECTOR]->(f:Film)
RETURN f.title AS title, f.year AS year
ORDER BY f.year
```

### İki Yönetmenin Ortak Oyuncuları

```cypher
MATCH (d1:Person {name: 'Alfred Hitchcock'})-[:DIRECTOR]->(f1:Film)
      <-[:ACTOR]-(actor:Person)-[:ACTOR]->(f2:Film)
      <-[:DIRECTOR]-(d2:Person {name: 'Stanley Kubrick'})
WITH actor,
     collect(DISTINCT f1.title) AS hitchcock_films,
     collect(DISTINCT f2.title) AS kubrick_films
RETURN actor.name AS actor, hitchcock_films, kubrick_films
```

### Etkilenme Zinciri

```cypher
MATCH (ceylan:Person {name: 'Nuri Bilge Ceylan'})-[:INFLUENCED_BY]->(influence:Person)
RETURN influence.name AS influenced_by
```

### Görüntü Yönetmeni Kimlerle Çalışmış

```cypher
MATCH (dp:Person {name: 'Sven Nykvist'})-[:DIRECTOR_OF_PHOTOGRAPHY]->(f:Film)
      <-[:DIRECTOR]-(d:Person)
WITH d.name AS director, collect(f.title) AS films
RETURN director, films
ORDER BY size(films) DESC
```

### Tür Kesişimi

```cypher
MATCH (f:Film)-[:HAS_GENRE]->(g:Genre)
WHERE g.name IN ['Drama', 'Thriller']
WITH f, collect(g.name) AS genres
WHERE size(genres) = 2
RETURN f.title AS title, f.year AS year, genres
ORDER BY f.year
```

### Yönetmen Bağlantı Ağı (Memgraph Lab görsel)

```cypher
MATCH p=(d:Person)-[:DIRECTOR]->(f:Film)
WHERE d.name IN ['Andrei Tarkovsky', 'Stanley Kubrick', 'Ingmar Bergman',
                 'David Lynch', 'Akira Kurosawa', 'Federico Fellini',
                 'Alfred Hitchcock', 'David Fincher', 'Quentin Tarantino',
                 'Paul Thomas Anderson', 'Nuri Bilge Ceylan', 'Zeki Demirkubuz',
                 'Woody Allen', 'Jean Renoir']
RETURN p
```

### Kubrick'in Tüm Ekibi (Memgraph Lab görsel)

```cypher
MATCH p=(person:Person)-[r]->(f:Film)<-[:DIRECTOR]-(d:Person {name: 'Stanley Kubrick'})
RETURN p LIMIT 100
```

---

## 10. Graph Algoritmaları

Memgraph MAGE kütüphanesi üzerinden çalışan algoritma sorguları.
Bu sorgular Memgraph Lab (`http://localhost:3000`) veya web arayüzü üzerinden çalıştırılabilir.

### PageRank — En Merkezi Düğümler

```cypher
CALL pagerank.get()
YIELD node, rank
WITH node, rank
WHERE rank > 0.001 AND 'Person' IN labels(node)
RETURN node.name AS name, rank
ORDER BY rank DESC
LIMIT 20
```

PageRank, bağlantı kalitesini ölçer. Yüksek skor → o kişi önemli filmlerle ve önemli kişilerle
çok sayıda ilişkide. Beklenti: çok filmde oynayan oyuncular + etkili yönetmenler üst sırada.

### Betweenness Centrality — Köprü Kişiler

```cypher
CALL betweenness_centrality.get(FALSE, FALSE)
YIELD node, betweenness_centrality
WITH node, betweenness_centrality AS bc
WHERE bc > 0 AND 'Person' IN labels(node)
RETURN node.name AS name, bc
ORDER BY bc DESC
LIMIT 20
```

Betweenness Centrality, farklı topluluklar arasındaki köprü kişileri bulur.
Parametreler: `(FALSE, FALSE)` = yönsüz + normalize edilmemiş.

**Bulgular:** Alfred Hitchcock en yüksek skoru alır (261K) — 54 filmlik kariyeriyle hem British Cinema
hem Hollywood'u kapsıyor. Sven Nykvist dikkat çekici: bir görüntü yönetmeni olarak
Bergman, Tarkovsky ve Woody Allen gibi birbirinden farklı yönetmenlerin dünyalarını
fiziksel olarak birbirine bağlamış.

### Community Detection — Doğal Kümeler (Louvain)

```cypher
CALL community_detection.get()
YIELD node, community_id
WITH community_id, collect(node.name) AS members, count(node) AS size
RETURN community_id, size, members[0..10] AS sample_members
ORDER BY size DESC
LIMIT 15
```

Belirli bir kümeyi derinlemesine incelemek için:

```cypher
CALL community_detection.get()
YIELD node, community_id
WITH node, community_id
WHERE community_id = <küme_id>
RETURN labels(node)[0] AS type, node.name AS name
ORDER BY type, name
```

**Bulgular:** Tarkovsky–Fellini aynı kümeye düşer (Nostalgia İtalya'da çekildi, ortak bağlantı).
Bergman–Ceylan aynı kümeye düşer (INFLUENCED_BY ilişkisi).

---

## 11. Proje Yapısı

```
cinema-graph/
├── data/                        # TMDb'den çekilen ham veri (git'e eklenmez)
│   ├── films.json               # 321 film
│   ├── persons.json             # 2 966 kişi
│   └── relationships.json       # Person–Film ilişkileri
│
├── scripts/
│   ├── fetch_filmography.py     # Adım 1: TMDb → JSON
│   ├── load_to_memgraph.py      # Adım 2: JSON → Memgraph
│   ├── load_enrichment.py       # Adım 3: INFLUENCED_BY, Movement, dış kişiler
│   └── update_enrichment.py     # Adım 4: düzeltme / güncelleme
│
├── web/
│   ├── backend/
│   │   ├── app/
│   │   │   ├── main.py          # FastAPI routes
│   │   │   ├── db.py            # MemgraphClient (neo4j driver)
│   │   │   └── agents/
│   │   │       ├── query_agent.py     # Gemini agent (NL → Cypher)
│   │   │       └── schema_context.py  # Graph şeması + örnek sorgular (sistem prompt)
│   │   ├── requirements.txt
│   │   ├── run.sh               # Backend başlatma scripti
│   │   └── .env                 # API anahtarları (git'e eklenmez)
│   │
│   └── frontend/
│       ├── src/
│       │   ├── App.jsx
│       │   ├── components/
│       │   │   ├── GraphVisualization.jsx  # Cytoscape.js bileşeni
│       │   │   ├── QueryInput.jsx
│       │   │   ├── ResultDisplay.jsx
│       │   │   └── Header.jsx
│       │   └── utils/api.js               # Backend API çağrıları
│       ├── vite.config.js        # /api → localhost:8000 proxy
│       └── package.json
│
├── memgraph/
│   └── Queries/
│       └── graph_algorithms.md  # PageRank, Betweenness, Community Detection
│
├── Explainability/
│   └── data_exp/
│       └── influenced_by_sources.md  # INFLUENCED_BY ilişkilerinin kaynak doğrulaması
│
└── promtlar/
    └── MEMGRAPH_CONTEXT.md      # Cypher yazarken Claude'a verilecek bağlam prompt'u
```

---

## 12. Bilinen Sorunlar ve Çözümleri

### Sorun: "Cypher sorgusu üretilemedi" hatası

**Neden:** Gemini API ücretsiz tier kotası doldu (HTTP 429 ResourceExhausted).

**Kontrol:**
```bash
curl http://localhost:8000/debug/models
```
Listede `models/gemini-2.5-flash` görünüyorsa API bağlantısı kurulmuş demektir.

**Çözüm:** `web/backend/app/agents/query_agent.py` dosyasında:
```python
MODEL = "gemini-2.5-flash"  # gemini-2.0-flash-lite veya gemini-2.0-flash KULLANMAYIN
```

Kota limitini aşmamak için:
- Free tier: günde ~1500 istek (model başına)
- Kota dolunca 24 saat beklenmelidir
- Daha yüksek limit için Google AI Studio'dan ücretli plana geçilebilir

---

### Sorun: Graph görselleştirmesinde node'lar var ama oklar (edge'ler) görünmüyor

**İki nedeni vardır:**

**Neden 1 — Backend edge üretmiyor:**
Bazı sorgu sonuçlarında liste alanları (`hitchcock_films`, `kubrick_films` vb.) string entity'ye bağlanmıyordu.

**Çözüm:** `main.py` içindeki `_extract_graph_data` fonksiyonu güncellendi:
- Aynı satırdaki string entity (oyuncu adı) ile liste item'ları (film adları) arasında edge kurulur
- Duplicate edge'ler `source→target` anahtarıyla deduplicate edilir

**Neden 2 — Edge rengi arka planla aynı:**
Cytoscape stylesheet'te `line-color: '#2a2a3a'` değeri, arka plan rengiyle (`#0a0a0f`) neredeyse aynıydı.

**Çözüm:** `GraphVisualization.jsx` içinde:
```js
'line-color':         '#5a5a7a',   // #2a2a3a yerine
'target-arrow-color': '#5a5a7a',
'arrow-scale':        1.2,         // 0.8 yerine
```

---

### Sorun: Memgraph'ta `RETURN count(n)` hatası

**Neden:** Memgraph, aggregation fonksiyonlarını doğrudan `RETURN`'de kabul etmez.

**Çözüm:**
```cypher
-- YANLIŞ
MATCH (n) RETURN count(n)

-- DOĞRU
MATCH (n) WITH count(n) AS c RETURN c
```

---

### Sorun: `load_to_memgraph.py` çalıştırıldığında auth hatası

**Neden:** Script `auth=None` kullanır; Memgraph Community Edition auth gerektirmez.

**Çözüm:** Memgraph'ın `bolt://localhost:7687` adresinde çalıştığını ve
Docker container'ının aktif olduğunu doğrulayın:
```bash
docker ps | grep memgraph
```

---

### Sorun: TMDb veri çekme yavaş ya da yarıda kesiliyor

**Nedenler:**
- Rate limit: Script 0.25 saniye bekleme + 429 durumunda exponential backoff uygular
- İnternet bağlantı kesintisi

**Çözüm:** Script `--only` parametresiyle kaldığı yerden devam edebilir:
```bash
python scripts/fetch_filmography.py --only "Akira Kurosawa"
```
Mevcut `data/*.json` dosyaları korunur ve yeni veri merge edilir.

---

## Lisans

Bu proje eğitim ve portfolyo amaçlı geliştirilmiştir.
TMDb verisi [TMDb API Terms of Use](https://www.themoviedb.org/documentation/api/terms-of-use) kapsamındadır.
