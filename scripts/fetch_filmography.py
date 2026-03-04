"""
fetch_filmography.py
--------------------
34 seed yönetmenin filmografisini TMDb API v3'ten çekip
data/films.json, data/persons.json, data/relationships.json olarak kaydeder.
"""

import argparse
import json
import logging
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Konfigürasyon
# ---------------------------------------------------------------------------
load_dotenv()

API_KEY = os.getenv("TMDB_API_KEY")
if not API_KEY:
    raise RuntimeError("TMDB_API_KEY .env dosyasında bulunamadı.")

BASE_URL = "https://api.themoviedb.org/3"
RATE_LIMIT_DELAY = 0.25   # saniye — TMDb rate limit aşmamak için
MAX_RETRIES = 3

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------
SEED_DIRECTORS = [
    "Andrei Tarkovsky",    "Stanley Kubrick",      "Ingmar Bergman",
    "Woody Allen",         "Alfred Hitchcock",     "Federico Fellini",
    "Akira Kurosawa",      "Jean Renoir",          "David Fincher",
    "Quentin Tarantino",   "Paul Thomas Anderson",
    "Nuri Bilge Ceylan",   "Zeki Demirkubuz",      "David Lynch",
    "Jean-Luc Godard",     "François Truffaut",    "Michelangelo Antonioni",
    "Krzysztof Kieślowski","Lars von Trier",        "Michael Haneke",
    "Wim Wenders",         "Pedro Almodóvar",
    "Wong Kar-wai",        "Yasujirō Ozu",         "Abbas Kiarostami",
    "Park Chan-wook",      "Bong Joon-ho",         "Hirokazu Kore-eda",
    "Martin Scorsese",     "Joel Coen",            "Terrence Malick",    "Spike Lee",
    "Yılmaz Güney",        "Semih Kaplanoğlu",
]

CREW_ROLES = {
    "Director",
    "Director of Photography",
    "Original Music Composer",
    "Sound Designer",
    "Editor",
}

MIN_RUNTIME = 60
MIN_VOTE_COUNT = 20
MAX_CAST = 10


# ---------------------------------------------------------------------------
# API yardımcı fonksiyonu
# ---------------------------------------------------------------------------
def tmdb_get(endpoint: str, params: dict | None = None) -> dict | None:
    """
    TMDb API'ye GET isteği atar.
    Rate limiting ve retry (MAX_RETRIES) içerir.
    Başarısız olursa None döner.
    """
    url = f"{BASE_URL}{endpoint}"
    request_params = dict(params or {})
    request_params["api_key"] = API_KEY

    for attempt in range(1, MAX_RETRIES + 1):
        time.sleep(RATE_LIMIT_DELAY)
        try:
            resp = requests.get(url, params=request_params, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "?"
            if status == 429:                          # Too Many Requests
                wait = 2 ** attempt
                logger.warning(
                    f"Rate limit (429). {wait}s bekleniyor "
                    f"[deneme {attempt}/{MAX_RETRIES}]."
                )
                time.sleep(wait)
            else:
                logger.warning(
                    f"HTTP {status} hatası [deneme {attempt}/{MAX_RETRIES}]: "
                    f"{endpoint}"
                )
        except requests.exceptions.RequestException as exc:
            logger.warning(
                f"İstek hatası [deneme {attempt}/{MAX_RETRIES}]: {exc}"
            )

    logger.error(f"{MAX_RETRIES} denemeden sonra başarısız: {endpoint}")
    return None


# ---------------------------------------------------------------------------
# TMDb sorgulama fonksiyonları
# ---------------------------------------------------------------------------
def search_person(name: str) -> int | None:
    """
    İsimle kişi arar, TMDb person ID'sini döner.

    Seçim önceliği:
      1. known_for_department == "Directing" olan ilk sonuç
      2. Hiç yönetmen yoksa listedeki ilk kişi
    Bu sayede aynı isimde birden fazla kişi olduğunda
    (ör. Ingmar Bergman) doğru yönetmen seçilir.
    """
    data = tmdb_get("/search/person", {"query": name})
    if not data or not data.get("results"):
        logger.error(f"Kişi bulunamadı: {name!r}")
        return None

    results = data["results"]

    # Önce Directing departmanındaki kişiyi ara
    director_match = next(
        (r for r in results if r.get("known_for_department") == "Directing"),
        None,
    )
    person = director_match if director_match is not None else results[0]

    if director_match is None:
        logger.warning(
            f"  {name!r} için 'Directing' departmanlı sonuç yok; "
            f"listedeki ilk kişi alındı (ID={person['id']})."
        )

    logger.info(
        f"  Bulundu: {name!r} → ID={person['id']} "
        f"(department={person.get('known_for_department', '?')})"
    )
    return person["id"]


def get_director_film_ids(person_id: int) -> list[int]:
    """Yönetmenin filmografisinden yönetmen olarak görev aldığı film ID'lerini döner."""
    data = tmdb_get(f"/person/{person_id}/movie_credits")
    if not data:
        return []
    return [
        credit["id"]
        for credit in data.get("crew", [])
        if credit.get("job") == "Director"
    ]


def get_movie_details(movie_id: int) -> dict | None:
    """Film detayını crew+cast ile birlikte tek istekte çeker."""
    return tmdb_get(f"/movie/{movie_id}", {"append_to_response": "credits"})


# ---------------------------------------------------------------------------
# Filtre & veri çıkarma fonksiyonları
# ---------------------------------------------------------------------------
def passes_filters(movie: dict) -> bool:
    runtime = movie.get("runtime") or 0
    vote_count = movie.get("vote_count") or 0
    return runtime >= MIN_RUNTIME and vote_count >= MIN_VOTE_COUNT


def extract_film_record(movie: dict) -> dict:
    release_date = movie.get("release_date") or ""
    year = int(release_date[:4]) if len(release_date) >= 4 else None

    return {
        "id": movie["id"],
        "title": movie.get("title", ""),
        "year": year,
        "runtime": movie.get("runtime"),
        "genres": [g["name"] for g in movie.get("genres", [])],
        "countries": [c["name"] for c in movie.get("production_countries", [])],
        "studios": [c["name"] for c in movie.get("production_companies", [])],
    }


def extract_crew_data(
    film_id: int, credits: dict
) -> tuple[list[dict], list[dict]]:
    """
    Crew üyelerinden CREW_ROLES kapsamındakileri çıkarır.
    Döner: (persons_raw, relationships)
    """
    persons_raw, relationships = [], []

    for member in credits.get("crew", []):
        job = member.get("job", "")
        if job not in CREW_ROLES:
            continue
        persons_raw.append({"id": member["id"], "name": member.get("name", ""), "role": job})
        relationships.append(
            {
                "person_id": member["id"],
                "film_id": film_id,
                "relationship_type": job,
                "extras": {},
            }
        )

    return persons_raw, relationships


def extract_cast_data(
    film_id: int, credits: dict
) -> tuple[list[dict], list[dict]]:
    """
    İlk MAX_CAST oyuncuyu çıkarır.
    Döner: (persons_raw, relationships)
    """
    persons_raw, relationships = [], []

    for member in credits.get("cast", [])[:MAX_CAST]:
        persons_raw.append({"id": member["id"], "name": member.get("name", ""), "role": "Actor"})
        relationships.append(
            {
                "person_id": member["id"],
                "film_id": film_id,
                "relationship_type": "Actor",
                "extras": {"character": member.get("character", "")},
            }
        )

    return persons_raw, relationships


# ---------------------------------------------------------------------------
# Mevcut JSON'ları yükleme yardımcıları
# ---------------------------------------------------------------------------
def _load_json(path: Path) -> list:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return []


def _load_existing_data() -> tuple[dict, dict, list, set]:
    """
    Mevcut data/films.json, persons.json, relationships.json dosyalarını
    bellek yapılarına yükler. Yoksa boş başlar.

    Döner:
        films_map    : {film_id: film_dict}
        persons_map  : {person_id: {id, name, roles: set}}
        relationships: [rel_dict, ...]
        seen_rels    : {(person_id, film_id, type), ...}
    """
    films_map: dict[int, dict] = {
        f["id"]: f for f in _load_json(DATA_DIR / "films.json")
    }

    persons_map: dict[int, dict] = {}
    for p in _load_json(DATA_DIR / "persons.json"):
        persons_map[p["id"]] = {
            "id": p["id"],
            "name": p["name"],
            "roles": set(p.get("roles", [])),
        }

    existing_rels = _load_json(DATA_DIR / "relationships.json")
    seen_rels: set[tuple] = {
        (r["person_id"], r["film_id"], r["relationship_type"])
        for r in existing_rels
    }

    if films_map:
        logger.info(
            f"Mevcut veri yüklendi: "
            f"{len(films_map)} film, {len(persons_map)} kişi, "
            f"{len(existing_rels)} ilişki."
        )

    return films_map, persons_map, existing_rels, seen_rels


# ---------------------------------------------------------------------------
# Ana akış
# ---------------------------------------------------------------------------
def process_directors(directors: list[str]) -> None:
    """
    Verilen yönetmen listesini işler ve sonuçları mevcut JSON'larla
    merge ederek kaydeder.
    """
    films_map, persons_map, relationships, seen_rels = _load_existing_data()

    def upsert_person(pid: int, name: str, role: str) -> None:
        if pid not in persons_map:
            persons_map[pid] = {"id": pid, "name": name, "roles": set()}
        persons_map[pid]["roles"].add(role)

    def add_relationship(rel: dict) -> None:
        key = (rel["person_id"], rel["film_id"], rel["relationship_type"])
        if key not in seen_rels:
            seen_rels.add(key)
            relationships.append(rel)

    # ------------------------------------------------------------------
    # Her yönetmen için döngü
    # ------------------------------------------------------------------
    for director_name in directors:
        logger.info(f"{'='*50}")
        logger.info(f"Yönetmen işleniyor: {director_name}")

        person_id = search_person(director_name)
        if person_id is None:
            continue

        upsert_person(person_id, director_name, "Director")

        film_ids = get_director_film_ids(person_id)
        logger.info(f"  Filmografi büyüklüğü: {len(film_ids)} film (filtre öncesi)")

        passed = 0
        for film_id in film_ids:
            movie = get_movie_details(film_id)
            if movie is None:
                continue

            if not passes_filters(movie):
                logger.debug(
                    f"  Atlandı: '{movie.get('title')}' "
                    f"(runtime={movie.get('runtime')}, votes={movie.get('vote_count')})"
                )
                continue

            # Film kaydı — mevcut veride yoksa ekle, varsa güncelle
            films_map[film_id] = extract_film_record(movie)

            credits = movie.get("credits", {})

            add_relationship(
                {
                    "person_id": person_id,
                    "film_id": film_id,
                    "relationship_type": "Director",
                    "extras": {},
                }
            )

            crew_persons, crew_rels = extract_crew_data(film_id, credits)
            for p in crew_persons:
                upsert_person(p["id"], p["name"], p["role"])
            for r in crew_rels:
                add_relationship(r)

            cast_persons, cast_rels = extract_cast_data(film_id, credits)
            for p in cast_persons:
                upsert_person(p["id"], p["name"], p["role"])
            for r in cast_rels:
                add_relationship(r)

            passed += 1

        logger.info(f"  ✓ {passed} film filtreden geçti → {director_name}")

    # ------------------------------------------------------------------
    # Serileştirme: set → list ve kaydet
    # ------------------------------------------------------------------
    persons_list = [
        {"id": p["id"], "name": p["name"], "roles": sorted(p["roles"])}
        for p in persons_map.values()
    ]
    films_list = list(films_map.values())

    logger.info(f"{'='*50}")
    logger.info("ÖZET (toplam)")
    logger.info(f"  Film sayısı        : {len(films_list)}")
    logger.info(f"  Kişi sayısı        : {len(persons_list)}")
    logger.info(f"  İlişki sayısı      : {len(relationships)}")

    files = {
        DATA_DIR / "films.json": films_list,
        DATA_DIR / "persons.json": persons_list,
        DATA_DIR / "relationships.json": relationships,
    }
    for path, data in files.items():
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"  Kaydedildi: {path}")

    logger.info("Tamamlandı.")


# ---------------------------------------------------------------------------
# CLI giriş noktası
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="TMDb'den yönetmen filmografisi çeker ve data/ klasörüne kaydeder."
    )
    parser.add_argument(
        "--only",
        metavar="YÖNETİMEN_ADI",
        nargs="+",
        help=(
            "Sadece bu yönetmen(ler)i işle ve mevcut JSON'larla merge et. "
            "Örnek: --only \"Ingmar Bergman\"  veya  --only \"Bergman\" \"Lynch\""
        ),
    )
    args = parser.parse_args()

    if args.only:
        # Kısmi çalışma: sadece verilen isimler, mevcut veriyle merge
        directors_to_run = args.only
        logger.info(f"Kısmi çalışma modu: {directors_to_run}")
    else:
        # Tam çalışma: tüm seed yönetmenler
        directors_to_run = SEED_DIRECTORS

    process_directors(directors_to_run)
