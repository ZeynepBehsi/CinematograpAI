"""
fetch_awards.py
---------------
data/awards.json dosyasını okuyarak Memgraph'a yükler.

JSON formatı beklentisi:
  [
    {
      "film_title": "Stalker",
      "film_year": 1979,
      "awards": [
        {"festival": "Cannes", "award": "Grand Prix", "year": 1980, "won": false}
      ]
    }, ...
  ]

Graf işlemleri:
  - Award {name, festival}             node'larını MERGE eder
  - (Film)-[:WON_AWARD {year}]->(Award)       ilişkisini MERGE eder  (won=true)
  - (Film)-[:NOMINATED_FOR {year}]->(Award)   ilişkisini MERGE eder  (won=false)

Film eşleştirme:
  1. MATCH (f:Film {title: $title, year: $year})
  2. Bulunamazsa → MATCH (f:Film {title: $title})  (yıl uyuşmazlığı uyarısı)

Çalıştırma:
  python scripts/fetch_awards.py
  python scripts/fetch_awards.py --dry-run   # grafa yazmadan eşleşmeleri göster
  python scripts/fetch_awards.py --stats     # sadece istatistik/doğrulama
"""

import argparse
import json
import logging
from collections import defaultdict
from pathlib import Path

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
# Sabitler
# ---------------------------------------------------------------------------
URI = "bolt://localhost:7687"
DATA_DIR   = Path(__file__).parent.parent / "data"
AWARDS_FILE = DATA_DIR / "awards.json"


# ---------------------------------------------------------------------------
# Veri yükleme
# ---------------------------------------------------------------------------
def load_awards_data() -> list[dict]:
    if not AWARDS_FILE.exists():
        raise FileNotFoundError(
            f"{AWARDS_FILE} bulunamadı. "
            "data/awards.json dosyasının var olduğunu doğrulayın."
        )
    data = json.loads(AWARDS_FILE.read_text(encoding="utf-8"))
    log.info(f"{len(data)} film girişi yüklendi: {AWARDS_FILE}")
    return data


# ---------------------------------------------------------------------------
# Film eşleştirme
# ---------------------------------------------------------------------------
def find_film_title(session, title: str, year: int) -> str | None:
    """
    Film node'unu önce title+year ile, bulamazsa sadece title ile arar.
    Grafın canonical title'ını döner; her iki yöntemde de bulunamazsa None.
    """
    # Birincil: tam eşleşme
    record = session.run(
        "MATCH (f:Film {title: $t, year: $y}) RETURN f.title AS title LIMIT 1",
        t=title, y=year,
    ).single()
    if record:
        return record["title"]

    # Geri dönüş: sadece title eşleşmesi
    record = session.run(
        "MATCH (f:Film {title: $t}) RETURN f.title AS title, f.year AS year LIMIT 1",
        t=title,
    ).single()
    if record:
        log.warning(
            f"  Yıl uyuşmazlığı — '{title}' "
            f"(beklenen: {year}, grafta: {record['year']}). Yine de kullanılıyor."
        )
        return record["title"]

    return None


# ---------------------------------------------------------------------------
# Adım 1 — Award node'ları
# ---------------------------------------------------------------------------
def add_award_nodes(session, awards_data: list[dict]) -> int:
    """
    Tüm benzersiz (festival, award) çiftleri için Award node'u MERGE eder.
    Oluşturulan/doğrulanan node sayısını döner.
    """
    log.info("─" * 55)
    log.info("Award node'ları oluşturuluyor...")

    seen: set[tuple[str, str]] = set()
    for entry in awards_data:
        for item in entry.get("awards", []):
            key = (item["festival"], item["award"])
            if key not in seen:
                seen.add(key)
                session.run(
                    "MERGE (:Award {name: $name, festival: $festival})",
                    name=item["award"],
                    festival=item["festival"],
                )
                log.info(f"  MERGE Award: [{item['festival']}] {item['award']}")

    log.info(f"{len(seen)} benzersiz Award node'u işlendi.")
    return len(seen)


# ---------------------------------------------------------------------------
# Adım 2 — Film-Award ilişkileri
# ---------------------------------------------------------------------------
def add_film_award_rels(
    session,
    awards_data: list[dict],
    dry_run: bool = False,
) -> dict[str, int]:
    """
    WON_AWARD (won=true) ve NOMINATED_FOR (won=false) ilişkilerini MERGE eder.
    Döner: {"won": N, "nominated": M, "no_film": K, "errors": E}
    """
    log.info("─" * 55)
    log.info("Film-Award ilişkileri oluşturuluyor...")

    counts: dict[str, int] = defaultdict(int)

    for entry in awards_data:
        film_title = entry["film_title"]
        film_year  = entry["film_year"]

        found = find_film_title(session, film_title, film_year)
        if not found:
            skipped = len(entry.get("awards", []))
            log.warning(
                f"  Film bulunamadı: '{film_title}' ({film_year}) — "
                f"{skipped} ödül atlandı."
            )
            counts["no_film"] += skipped
            continue

        for item in entry.get("awards", []):
            rel_type   = "WON_AWARD" if item["won"] else "NOMINATED_FOR"
            ceremony_y = item["year"]
            desc = (
                f"('{found}')-[:{rel_type} {{year: {ceremony_y}}}]->"
                f"([{item['festival']}] {item['award']})"
            )

            if dry_run:
                action = "WON  " if item["won"] else "NOM  "
                log.info(f"  [DRY] {action}{desc}")
                counts["won" if item["won"] else "nominated"] += 1
                continue

            try:
                session.run(
                    f"""
                    MATCH (f:Film  {{title: $film_title}})
                    MATCH (a:Award {{name: $award_name, festival: $festival}})
                    MERGE (f)-[r:{rel_type} {{year: $cy}}]->(a)
                    """,
                    film_title=found,
                    award_name=item["award"],
                    festival=item["festival"],
                    cy=ceremony_y,
                )
                log.info(f"  + {desc}")
                counts["won" if item["won"] else "nominated"] += 1
            except Exception as exc:
                log.warning(f"  Hata — {desc}: {exc}")
                counts["errors"] += 1

    return dict(counts)


# ---------------------------------------------------------------------------
# Adım 3 — Film eşleşme ön kontrolü (--dry-run yardımcısı)
# ---------------------------------------------------------------------------
def check_film_matches(session, awards_data: list[dict]) -> None:
    log.info("─" * 55)
    log.info("Film eşleşme ön kontrolü:")
    found = not_found = 0
    for entry in awards_data:
        matched = find_film_title(session, entry["film_title"], entry["film_year"])
        if matched:
            log.info(f"  ✓  '{entry['film_title']}' ({entry['film_year']}) → '{matched}'")
            found += 1
        else:
            log.warning(f"  ✗  '{entry['film_title']}' ({entry['film_year']}) — grafta yok")
            not_found += 1
    log.info(f"Sonuç: {found} eşleşti, {not_found} bulunamadı.")


# ---------------------------------------------------------------------------
# Doğrulama
# ---------------------------------------------------------------------------
def verify(session) -> None:
    """Graf istatistikleri. (Memgraph: aggregation WITH'te olmalı)"""
    log.info("─" * 55)
    log.info("Doğrulama sorguları:")

    award_count = session.run(
        "MATCH (a:Award) WITH count(a) AS c RETURN c"
    ).single()["c"]

    won_count = session.run(
        "MATCH ()-[r:WON_AWARD]->() WITH count(r) AS c RETURN c"
    ).single()["c"]

    nom_count = session.run(
        "MATCH ()-[r:NOMINATED_FOR]->() WITH count(r) AS c RETURN c"
    ).single()["c"]

    log.info("─" * 55)
    log.info(f"  Award node sayısı    : {award_count}")
    log.info(f"  WON_AWARD sayısı     : {won_count}")
    log.info(f"  NOMINATED_FOR sayısı : {nom_count}")

    log.info("  Festival bazında WON_AWARD dağılımı:")
    rows = session.run(
        """
        MATCH ()-[r:WON_AWARD]->(a:Award)
        WITH a.festival AS festival, count(r) AS wins
        RETURN festival, wins
        ORDER BY wins DESC
        """
    )
    for row in rows:
        log.info(f"    {row['festival']:<30}: {row['wins']} ödül")

    log.info("  En çok ödüllü 10 film:")
    rows = session.run(
        """
        MATCH (f:Film)-[r:WON_AWARD]->()
        WITH f.title AS title, count(r) AS wins
        RETURN title, wins
        ORDER BY wins DESC
        LIMIT 10
        """
    )
    for row in rows:
        log.info(f"    {row['title']:<40}: {row['wins']} ödül")

    log.info("─" * 55)


# ---------------------------------------------------------------------------
# Ana akış
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="data/awards.json'u okuyup Award node'larını ve ilişkilerini Memgraph'a yükler."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Grafa yazmadan eşleşmeleri ve yapılacak işlemleri logla.",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Veri yüklemesi yapmadan sadece doğrulama istatistiklerini göster.",
    )
    args = parser.parse_args()

    driver = GraphDatabase.driver(URI, auth=None)

    if args.stats:
        with driver.session() as session:
            verify(session)
        driver.close()
        return

    awards_data = load_awards_data()

    with driver.session() as session:
        if args.dry_run:
            log.info("=== DRY-RUN modu — grafa yazılmıyor ===")
            check_film_matches(session, awards_data)
            add_film_award_rels(session, awards_data, dry_run=True)
        else:
            add_award_nodes(session, awards_data)
            counts = add_film_award_rels(session, awards_data, dry_run=False)
            log.info("─" * 55)
            log.info(f"Özet → WON_AWARD: {counts.get('won', 0)}, "
                     f"NOMINATED_FOR: {counts.get('nominated', 0)}, "
                     f"film-bulunamadı: {counts.get('no_film', 0)}, "
                     f"hata: {counts.get('errors', 0)}")
            verify(session)

    driver.close()
    log.info("Bağlantı kapatıldı. Tamamlandı.")


if __name__ == "__main__":
    main()
