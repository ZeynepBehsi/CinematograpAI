"""
fetch_ratings.py
----------------
data/films.json'daki her filmin TMDb ID'siyle TMDb API'ye istek atar,
vote_average ve vote_count değerlerini çekip Memgraph'taki Film node'larına yazar.

Graf güncelleme sorgusu:
    MATCH (f:Film {tmdb_id: $id})
    SET f.rating = $rating, f.vote_count = $votes

Çalıştırma örnekleri:
    python scripts/fetch_ratings.py                  # tüm filmler
    python scripts/fetch_ratings.py --limit 50       # ilk 50 film (test)
    python scripts/fetch_ratings.py --skip-existing  # zaten rating olan filmleri atla
    python scripts/fetch_ratings.py --dry-run        # grafa yazmadan önizle
    python scripts/fetch_ratings.py --min-votes 100  # az oy olan filmleri filtrele
"""

import argparse
import json
import logging
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from neo4j import GraphDatabase

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Konfigürasyon
# ---------------------------------------------------------------------------
load_dotenv()

API_KEY = os.getenv("TMDB_API_KEY")
if not API_KEY:
    raise RuntimeError("TMDB_API_KEY .env dosyasında bulunamadı.")

BASE_URL        = "https://api.themoviedb.org/3"
RATE_LIMIT_DELAY = 0.25   # saniye — TMDb rate limit aşmamak için
MAX_RETRIES     = 3
PROGRESS_EVERY  = 50      # kaçta bir ilerleme logu basılsın

URI      = "bolt://localhost:7687"
DATA_DIR = Path(__file__).parent.parent / "data"


# ---------------------------------------------------------------------------
# TMDb API yardımcısı
# ---------------------------------------------------------------------------
def tmdb_get(endpoint: str, params: dict | None = None) -> dict | None:
    """
    TMDb'ye GET isteği atar.
    Rate limiting ve üstel geri çekilme (MAX_RETRIES) içerir.
    Başarısız olursa None döner.
    """
    url           = f"{BASE_URL}{endpoint}"
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
            if status == 429:
                wait = 2 ** attempt
                log.warning(
                    f"Rate limit (429) — {wait}s bekleniyor "
                    f"[deneme {attempt}/{MAX_RETRIES}]."
                )
                time.sleep(wait)
            elif status == 404:
                log.debug(f"404 — bulunamadı: {endpoint}")
                return None
            else:
                log.warning(
                    f"HTTP {status} [deneme {attempt}/{MAX_RETRIES}]: {endpoint}"
                )
        except requests.exceptions.RequestException as exc:
            log.warning(f"İstek hatası [deneme {attempt}/{MAX_RETRIES}]: {exc}")

    log.error(f"{MAX_RETRIES} denemeden sonra başarısız: {endpoint}")
    return None


# ---------------------------------------------------------------------------
# Grafta halihazırda rating olan filmler
# ---------------------------------------------------------------------------
def fetch_rated_ids(session) -> set[int]:
    """
    Memgraph'ta rating property'si zaten set edilmiş tmdb_id'leri döner.
    --skip-existing bayrağı için kullanılır.
    """
    result = session.run(
        """
        MATCH (f:Film)
        WHERE f.rating IS NOT NULL
        WITH f.tmdb_id AS id
        RETURN id
        """
    )
    return {record["id"] for record in result}


# ---------------------------------------------------------------------------
# Tek film için rating çek
# ---------------------------------------------------------------------------
def fetch_rating(tmdb_id: int) -> tuple[float | None, int | None]:
    """
    TMDb /movie/{id} endpoint'inden (vote_average, vote_count) döner.
    API başarısız olursa (None, None).
    """
    data = tmdb_get(f"/movie/{tmdb_id}")
    if data is None:
        return None, None
    rating     = data.get("vote_average")
    vote_count = data.get("vote_count")
    # 0.0 veya 0 değerleri geçersiz kabul et
    if not rating and not vote_count:
        return None, None
    return rating, vote_count


# ---------------------------------------------------------------------------
# Graf güncelleme
# ---------------------------------------------------------------------------
def update_film_rating(
    session, tmdb_id: int, rating: float, vote_count: int
) -> bool:
    """
    Film node'unu günceller.
    Güncelleme yapıldıysa True, node bulunamadıysa False döner.
    """
    result = session.run(
        """
        MATCH (f:Film {tmdb_id: $id})
        SET f.rating = $rating, f.vote_count = $votes
        RETURN f.title AS title
        """,
        id=tmdb_id,
        rating=rating,
        votes=vote_count,
    )
    record = result.single()
    return record is not None


# ---------------------------------------------------------------------------
# Ana akış
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="TMDb'den film rating'lerini çekip Memgraph Film node'larını günceller."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help="İşlenecek maksimum film sayısı (varsayılan: tümü).",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Zaten rating property'si olan filmleri atla.",
    )
    parser.add_argument(
        "--min-votes",
        type=int,
        default=0,
        metavar="N",
        help="Bu sayının altında oy alan filmleri grafa yazma.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="API'yi çağır ama grafa yazma; değerleri sadece logla.",
    )
    args = parser.parse_args()

    # ── Filmler yükle ─────────────────────────────────────────────────────
    films_path = DATA_DIR / "films.json"
    if not films_path.exists():
        raise FileNotFoundError(f"{films_path} bulunamadı.")

    films = json.loads(films_path.read_text(encoding="utf-8"))
    log.info(f"{len(films)} film data/films.json'dan yüklendi.")

    if args.limit > 0:
        films = films[: args.limit]
        log.info(f"--limit {args.limit}: {len(films)} filmle sınırlı çalışılacak.")

    # ── Memgraph bağlantısı ────────────────────────────────────────────────
    driver = GraphDatabase.driver(URI, auth=None)

    # --skip-existing: önceden rating set edilmiş tmdb_id'leri al
    already_rated: set[int] = set()
    if args.skip_existing:
        with driver.session() as s:
            already_rated = fetch_rated_ids(s)
        log.info(f"Grafta zaten rating olan film sayısı: {len(already_rated)}")

    # ── İşlem döngüsü ─────────────────────────────────────────────────────
    total     = len(films)
    updated   = 0
    skipped   = 0
    not_found = 0   # API'den geldi ama grafta node yok
    api_fail  = 0   # API yanıt vermedi

    with driver.session() as session:
        for i, film in enumerate(films, start=1):
            tmdb_id    = film["id"]
            film_title = film.get("title", f"id={tmdb_id}")

            # --skip-existing kontrolü
            if tmdb_id in already_rated:
                log.debug(f"  Atlandı (mevcut rating): '{film_title}'")
                skipped += 1
                continue

            # TMDb'den çek
            rating, vote_count = fetch_rating(tmdb_id)

            if rating is None:
                log.warning(f"  API başarısız: '{film_title}' (tmdb_id={tmdb_id})")
                api_fail += 1
                continue

            # --min-votes filtresi
            if args.min_votes > 0 and (vote_count or 0) < args.min_votes:
                log.debug(
                    f"  Atlandı (az oy): '{film_title}' "
                    f"(vote_count={vote_count} < {args.min_votes})"
                )
                skipped += 1
                continue

            # Dry-run modu
            if args.dry_run:
                log.info(
                    f"  [DRY] '{film_title}' → "
                    f"rating={rating:.1f}, votes={vote_count}"
                )
                updated += 1
            else:
                found = update_film_rating(session, tmdb_id, rating, vote_count)
                if found:
                    log.info(
                        f"  ✓ '{film_title}' → "
                        f"rating={rating:.1f}, votes={vote_count}"
                    )
                    updated += 1
                else:
                    log.warning(
                        f"  Graf'ta bulunamadı: '{film_title}' "
                        f"(tmdb_id={tmdb_id})"
                    )
                    not_found += 1

            # İlerleme raporu
            if i % PROGRESS_EVERY == 0:
                log.info(
                    f"── İlerleme: {i}/{total} "
                    f"(güncellendi={updated}, atlandı={skipped}, "
                    f"api_hata={api_fail}, grafta_yok={not_found})"
                )

    driver.close()

    # ── Özet ──────────────────────────────────────────────────────────────
    log.info("─" * 55)
    log.info("ÖZET")
    log.info(f"  Toplam film        : {total}")
    log.info(f"  Güncellendi        : {updated}")
    log.info(f"  Atlandı            : {skipped}")
    log.info(f"  API başarısız      : {api_fail}")
    log.info(f"  Grafta bulunamadı  : {not_found}")
    log.info("─" * 55)
    log.info("Tamamlandı.")


if __name__ == "__main__":
    main()
