import json
import logging
from pathlib import Path

from neo4j import GraphDatabase

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
URI = "bolt://localhost:7687"


def rel_type(raw: str) -> str:
    """'Director of Photography' → 'DIRECTOR_OF_PHOTOGRAPHY'"""
    return raw.strip().upper().replace(" ", "_").replace("-", "_")


def load_json(filename: str) -> list:
    path = DATA_DIR / filename
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def run(tx, query: str, **params):
    tx.run(query, **params)


# ── Temizlik sabitleri ─────────────────────────────────────────────────────────

MUSIC_VIDEO_TITLES: list[str] = [
    "Madonna: The Immaculate Collection",
    "HIStory on Film, Volume II",
    "Michael Jackson: Dangerous - The Short Films",
    "Michael Jackson Video Greatest Hits: HIStory",
    "Michael Jackson's Journey from Motown to Off the Wall",
]

ANTHOLOGY_TITLES: list[str] = [
    "Lumière & Company",
    "To Each His Own Cinema",
    "New York Stories",
    "Eros",
    "Ten Minutes Older: The Trumpet",
    "Ten Minutes Older: The Cello",
    "Aria",
    "Ro.Go.Pa.G.",
    "Boccaccio '70",
    "Love at Twenty",
    "Elstree Calling",
    "Love and Anger",
    "The World's Most Beautiful Swindlers",
    "Four Rooms",
    "D-Day",
]

CANONICAL_BERGMAN_TMDB_ID = 6648


# ── Temizlik fonksiyonları ─────────────────────────────────────────────────────

def cleanup_music_videos(session) -> None:
    """Müzik video derlemelerini graftan kaldır."""
    log.info("Müzik videoları temizleniyor...")
    for title in MUSIC_VIDEO_TITLES:
        session.run(
            "MATCH (f:Film {title: $title}) DETACH DELETE f",
            title=title,
        )
        log.info(f"  Silindi (varsa): '{title}'")
    log.info(f"{len(MUSIC_VIDEO_TITLES)} başlık işlendi.")


def label_anthology_films(session) -> None:
    """Bilinen antoloji filmlere :Anthology label'ı ekle.
    Ayrıca 5'ten fazla yönetmeni olan filmleri otomatik etiketle."""
    log.info("Antoloji filmler etiketleniyor...")

    for title in ANTHOLOGY_TITLES:
        session.run(
            "MATCH (f:Film {title: $title}) SET f:Anthology",
            title=title,
        )
        log.info(f"  :Anthology → '{title}'")

    # Genel kural: 5'ten fazla yönetmeni olan filmler otomatik olarak antoloji
    session.run(
        """
        MATCH (f:Film)<-[:DIRECTOR]-(p:Person)
        WITH f, count(p) AS cnt
        WHERE cnt > 5
        SET f:Anthology
        """
    )
    log.info(f"  Otomatik kural (>5 yönetmen) uygulandı.")
    log.info("Antoloji etiketleme tamamlandı.")


def delete_twin_peaks_pilot(session) -> None:
    """Twin Peaks (1989) TV pilotunu ve The Missing Pieces (2014) derlemesini sil.
    Fire Walk with Me (1992) korunur."""
    log.info("Twin Peaks TV pilotu ve yan ürünleri siliniyor...")
    session.run(
        """
        MATCH (f:Film)
        WHERE f.title = 'Twin Peaks' AND f.year = 1989
        DETACH DELETE f
        """
    )
    log.info("  'Twin Peaks' (1989) silindi (varsa).")
    session.run(
        """
        MATCH (f:Film {tmdb_id: 284457})
        DETACH DELETE f
        """
    )
    log.info("  'Twin Peaks: The Missing Pieces' (2014) silindi (varsa).")


def fix_bergman_duplicates(session) -> None:
    """Ingmar Bergman duplikat Person node'larını temizle.
    tmdb_id=6648 olan canonical node'u koru; diğerlerinin Film
    ilişkilerini canonical'a aktar ve duplikatları sil."""
    log.info("Bergman duplikat kontrolü yapılıyor...")

    result = session.run(
        "MATCH (p:Person {name: 'Ingmar Bergman'}) RETURN p.tmdb_id AS tid"
    )
    bergman_ids = [r["tid"] for r in result]

    if len(bergman_ids) <= 1:
        log.info("  Bergman duplikatı yok, atlanıyor.")
        return

    log.info(f"  {len(bergman_ids)} Bergman node'u bulundu: {bergman_ids}")
    dup_ids = [tid for tid in bergman_ids if tid != CANONICAL_BERGMAN_TMDB_ID]

    for dup_id in dup_ids:
        # Duplikat node'un Film ilişkilerini sorgula
        rels = list(session.run(
            """
            MATCH (dup:Person {tmdb_id: $dup_id})-[r]->(f:Film)
            RETURN type(r) AS rtype, f.tmdb_id AS film_id
            """,
            dup_id=dup_id,
        ))

        # Her ilişkiyi canonical node'a aktar
        for rec in rels:
            rtype = rec["rtype"]
            film_id = rec["film_id"]
            session.run(
                f"""
                MATCH (c:Person {{tmdb_id: $cid}})
                MATCH (f:Film {{tmdb_id: $fid}})
                MERGE (c)-[:`{rtype}`]->(f)
                """,
                cid=CANONICAL_BERGMAN_TMDB_ID,
                fid=film_id,
            )

        # Duplikatı sil
        session.run(
            "MATCH (dup:Person {tmdb_id: $dup_id}) DETACH DELETE dup",
            dup_id=dup_id,
        )
        log.info(
            f"  Duplikat Bergman (tmdb_id={dup_id}) silindi, "
            f"{len(rels)} ilişki canonical'a aktarıldı."
        )

    log.info("Bergman duplikat temizliği tamamlandı.")


def main():
    driver = GraphDatabase.driver(URI, auth=None)

    with driver.session() as session:
        # ── 1. DB temizle ─────────────────────────────────────────────────────
        log.info("DB temizleniyor...")
        session.run("MATCH (n) DETACH DELETE n")
        log.info("DB temizlendi.")

        # ── 2. Film node'ları ─────────────────────────────────────────────────
        films = load_json("films.json")
        log.info(f"{len(films)} film yükleniyor...")

        for film in films:
            session.execute_write(
                run,
                "CREATE (:Film {tmdb_id: $tmdb_id, title: $title, year: $year, runtime: $runtime})",
                tmdb_id=film["id"],
                title=film["title"],
                year=film.get("year"),
                runtime=film.get("runtime"),
            )

        log.info(f"{len(films)} Film node'u oluşturuldu.")

        # ── 3. Person node'ları ───────────────────────────────────────────────
        persons = load_json("persons.json")
        log.info(f"{len(persons)} kişi yükleniyor...")

        for person in persons:
            session.execute_write(
                run,
                "CREATE (:Person {tmdb_id: $tmdb_id, name: $name})",
                tmdb_id=person["id"],
                name=person["name"],
            )

        log.info(f"{len(persons)} Person node'u oluşturuldu.")

        # ── 4. Genre / Studio / Country node'ları ve film bağlantıları ────────
        log.info("Genre, Studio, Country node'ları oluşturuluyor...")

        for film in films:
            film_id = film["id"]

            for genre in film.get("genres", []):
                session.execute_write(
                    run,
                    """
                    MERGE (g:Genre {name: $name})
                    WITH g
                    MATCH (f:Film {tmdb_id: $film_id})
                    CREATE (f)-[:HAS_GENRE]->(g)
                    """,
                    name=genre,
                    film_id=film_id,
                )

            for studio in film.get("studios", []):
                session.execute_write(
                    run,
                    """
                    MERGE (s:Studio {name: $name})
                    WITH s
                    MATCH (f:Film {tmdb_id: $film_id})
                    CREATE (f)-[:PRODUCED_BY]->(s)
                    """,
                    name=studio,
                    film_id=film_id,
                )

            for country in film.get("countries", []):
                session.execute_write(
                    run,
                    """
                    MERGE (c:Country {name: $name})
                    WITH c
                    MATCH (f:Film {tmdb_id: $film_id})
                    CREATE (f)-[:FROM_COUNTRY]->(c)
                    """,
                    name=country,
                    film_id=film_id,
                )

        log.info("Genre / Studio / Country bağlantıları tamamlandı.")

        # ── 5. Person–Film ilişkileri ─────────────────────────────────────────
        relationships = load_json("relationships.json")
        log.info(f"{len(relationships)} ilişki yükleniyor...")

        skipped = 0
        for rel in relationships:
            rtype = rel_type(rel["relationship_type"])
            extras = rel.get("extras") or {}

            # Cypher'da dinamik relationship type için backtick ile yaz
            query = f"""
                MATCH (p:Person {{tmdb_id: $person_id}})
                MATCH (f:Film   {{tmdb_id: $film_id}})
                CREATE (p)-[:`{rtype}` $props]->(f)
            """
            try:
                session.execute_write(
                    run,
                    query,
                    person_id=rel["person_id"],
                    film_id=rel["film_id"],
                    props=extras,
                )
            except Exception as e:
                log.warning(f"İlişki atlandı ({rel}): {e}")
                skipped += 1

        log.info(f"{len(relationships) - skipped} ilişki oluşturuldu, {skipped} atlandı.")

        # ── 6. Yükleme sonrası temizlik ───────────────────────────────────────
        cleanup_music_videos(session)
        label_anthology_films(session)
        delete_twin_peaks_pilot(session)
        fix_bergman_duplicates(session)

        # ── 7. Özet sayım (temizlik sonrası) ──────────────────────────────────
        node_count = session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
        rel_count = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]

        log.info("─" * 50)
        log.info(f"Toplam node       : {node_count}")
        log.info(f"Toplam ilişki     : {rel_count}")
        log.info("─" * 50)

    driver.close()
    log.info("Bağlantı kapatıldı. Yükleme tamamlandı.")


if __name__ == "__main__":
    main()
