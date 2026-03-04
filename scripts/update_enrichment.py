import logging

from neo4j import GraphDatabase

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

URI = "bolt://localhost:7687"

# ── Eklenecek dış Person'lar ───────────────────────────────────────────────────
# MERGE kullanılır — zaten varsa dokunulmaz
NEW_EXTERNAL_PERSONS: list[dict] = [
    {"name": "Anton Chekhov",       "role": "Writer"},
    {"name": "Carl Theodor Dreyer", "role": "Director"},   # Lars von Trier etkisi
]

# ── Eklenecek INFLUENCED_BY ilişkileri ────────────────────────────────────────
# Tuple formatı: (etkileyen, etkilenen)
# Anlamı: (etkilenen)-[:INFLUENCED_BY]->(etkileyen)
# MERGE kullanılır — duplikat oluşturmaz
ADD_INFLUENCES: list[tuple[str, str]] = [
    # ── load_enrichment.py'den devreden temel ilişkiler ──────────────────
    ("Robert Bresson",        "Nuri Bilge Ceylan"),
    ("Ingmar Bergman",        "Nuri Bilge Ceylan"),
    ("Anton Chekhov",         "Nuri Bilge Ceylan"),
    ("Robert Bresson",        "Zeki Demirkubuz"),
    ("Andrei Tarkovsky",      "Nuri Bilge Ceylan"),    # Ek Tarkovsky etkisi

    # ── Amerikan Auteur / Bağımsız ────────────────────────────────────────
    ("Federico Fellini",      "Martin Scorsese"),      # Scorsese röportajlarında belirtmiştir
    ("Stanley Kubrick",       "Martin Scorsese"),
    ("Andrei Tarkovsky",      "Terrence Malick"),
    ("Akira Kurosawa",        "Spike Lee"),

    # ── Fransız Yeni Dalga ────────────────────────────────────────────────
    ("Alfred Hitchcock",      "François Truffaut"),    # Truffaut'nun Hitchcock kitabı var
    ("Akira Kurosawa",        "Jean-Luc Godard"),

    # ── Asya Sineması ─────────────────────────────────────────────────────
    ("Michelangelo Antonioni","Wong Kar-Wai"),
    ("Alfred Hitchcock",      "Park Chan-wook"),
    ("Alfred Hitchcock",      "Bong Joon-ho"),
    ("Akira Kurosawa",        "Bong Joon-ho"),
    ("Yasujirō Ozu",          "Hirokazu Kore-eda"),
    ("Abbas Kiarostami",      "Hirokazu Kore-eda"),

    # ── Avrupa Sineması ───────────────────────────────────────────────────
    ("Ingmar Bergman",        "Krzysztof Kieślowski"),
    ("Andrei Tarkovsky",      "Lars von Trier"),
    ("Carl Theodor Dreyer",   "Lars von Trier"),       # Dreyer Danimarkalı usta
    ("Robert Bresson",        "Michael Haneke"),
    ("Federico Fellini",      "Pedro Almodóvar"),

    # ── Türk Sineması ─────────────────────────────────────────────────────
    ("Andrei Tarkovsky",      "Semih Kaplanoğlu"),
]

# ── Silinecek INFLUENCED_BY ilişkileri ────────────────────────────────────────
# (etkileyen, etkilenen) — yetersiz kanıt nedeniyle çıkarıldı
REMOVE_INFLUENCES: list[tuple[str, str]] = [
    ("Akira Kurosawa", "Federico Fellini"),  # Karşılıklı hayranlık, etki belgelenmemiş
    ("Jean Renoir",    "Federico Fellini"),  # Spesifik kanıt bulunamadı
]

# ── Eklenecek Movement node'ları ──────────────────────────────────────────────
# load_enrichment.py'deki 13 Movement'ı tamamlar → toplam 24
ADD_MOVEMENTS: list[str] = [
    "French New Wave",
    "Italian Modernism",
    "Hong Kong New Wave",
    "Korean New Wave",
    "Dogme 95",
    "Classical Japanese Cinema",
    "Iranian New Wave",
    "New Hollywood",           # load_enrichment.py'de zaten var; MERGE güvenli
    "Austrian Auteur Cinema",
    "Movida Madrileña",
    "New Turkish Cinema",    # load_enrichment.py ile birleştirildi (Güney/Kaplanoğlu + Ceylan/Demirkubuz)
    "New German Cinema",
    "American Independent Cinema",
]

# ── Eklenecek PART_OF_MOVEMENT ilişkileri ─────────────────────────────────────
# Tuple: (yönetmen_adı, hareket_adı)
# load_enrichment.py'deki ilişkileri tamamlar / güçlendirir
ADD_PART_OF_MOVEMENT: list[tuple[str, str]] = [
    ("Jean-Luc Godard",        "French New Wave"),
    ("François Truffaut",      "French New Wave"),
    ("Michelangelo Antonioni", "Italian Modernism"),
    ("Wong Kar-Wai",           "Hong Kong New Wave"),
    ("Park Chan-wook",         "Korean New Wave"),
    ("Bong Joon-ho",           "Korean New Wave"),
    ("Lars von Trier",         "Dogme 95"),
    ("Yasujirō Ozu",           "Classical Japanese Cinema"),
    ("Abbas Kiarostami",       "Iranian New Wave"),
    ("Martin Scorsese",        "New Hollywood"),
    ("Terrence Malick",        "New Hollywood"),
    ("Michael Haneke",         "Austrian Auteur Cinema"),
    ("Pedro Almodóvar",        "Movida Madrileña"),
    ("Yılmaz Güney",           "New Turkish Cinema"),
    ("Semih Kaplanoğlu",       "New Turkish Cinema"),
    ("Wim Wenders",            "New German Cinema"),
    ("Spike Lee",              "American Independent Cinema"),
    ("Hirokazu Kore-eda",      "Classical Japanese Cinema"),
]


# ── Yardımcı ──────────────────────────────────────────────────────────────────

def run(tx, query: str, **params):
    tx.run(query, **params)


# ── Adımlar ───────────────────────────────────────────────────────────────────

def add_external_persons(session) -> None:
    """Yeni dış Person node'larını ekle (zaten varsa dokunma)."""
    log.info("Yeni dış Person node'ları ekleniyor...")

    for person in NEW_EXTERNAL_PERSONS:
        session.execute_write(
            run,
            "MERGE (:Person {name: $name})",
            name=person["name"],
        )
        log.info(f"  MERGE Person: {person['name']} ({person['role']})")

    log.info(f"{len(NEW_EXTERNAL_PERSONS)} dış Person işlendi.")


def add_influenced_by(session) -> None:
    """Yeni INFLUENCED_BY ilişkilerini ekle. MERGE duplikat oluşturmaz."""
    log.info("Yeni INFLUENCED_BY ilişkileri ekleniyor...")

    skipped = 0
    for influencer_name, influenced_name in ADD_INFLUENCES:
        try:
            result = session.run(
                """
                MATCH (influencer:Person {name: $influencer})
                MATCH (influenced:Person  {name: $influenced})
                MERGE (influenced)-[r:INFLUENCED_BY]->(influencer)
                RETURN r IS NOT NULL AS ok
                """,
                influencer=influencer_name,
                influenced=influenced_name,
            )
            record = result.single()
            if record and record["ok"]:
                log.info(f"  + ({influenced_name})-[:INFLUENCED_BY]->({influencer_name})")
            else:
                log.warning(
                    f"  Atlandı — kişi bulunamadı: "
                    f"'{influencer_name}' veya '{influenced_name}'"
                )
                skipped += 1
        except Exception as e:
            log.warning(f"  Hata — ({influenced_name})->({influencer_name}): {e}")
            skipped += 1

    log.info(
        f"{len(ADD_INFLUENCES) - skipped} INFLUENCED_BY işlendi "
        f"(MERGE: duplikat oluşturmaz), {skipped} atlandı."
    )


def remove_influenced_by(session) -> None:
    """Kanıtsız INFLUENCED_BY ilişkilerini sil."""
    log.info("Geçersiz INFLUENCED_BY ilişkileri siliniyor...")

    removed = 0
    for influencer_name, influenced_name in REMOVE_INFLUENCES:
        result = session.run(
            """
            MATCH (influenced:Person {name: $influenced})
                  -[r:INFLUENCED_BY]->
                  (influencer:Person {name: $influencer})
            DELETE r
            RETURN count(r) AS deleted
            """,
            influencer=influencer_name,
            influenced=influenced_name,
        )
        record = result.single()
        n = record["deleted"] if record else 0
        removed += n
        log.info(
            f"  - ({influenced_name})-[:INFLUENCED_BY]->({influencer_name}): "
            f"{n} ilişki silindi"
        )

    log.info(f"Toplam {removed} INFLUENCED_BY ilişkisi silindi.")


def add_movements(session) -> None:
    """Eksik Movement node'larını oluştur (zaten varsa dokunma)."""
    log.info("Movement node'ları ekleniyor...")

    for movement_name in ADD_MOVEMENTS:
        session.execute_write(
            run,
            "MERGE (:Movement {name: $name})",
            name=movement_name,
        )
        log.info(f"  MERGE Movement: {movement_name}")

    log.info(f"{len(ADD_MOVEMENTS)} Movement işlendi.")


def add_part_of_movement(session) -> None:
    """PART_OF_MOVEMENT ilişkilerini ekle. MERGE duplikat oluşturmaz."""
    log.info("PART_OF_MOVEMENT ilişkileri ekleniyor...")

    skipped = 0
    for director_name, movement_name in ADD_PART_OF_MOVEMENT:
        try:
            result = session.run(
                """
                MATCH (p:Person   {name: $director})
                MATCH (m:Movement {name: $movement})
                MERGE (p)-[r:PART_OF_MOVEMENT]->(m)
                RETURN r IS NOT NULL AS ok
                """,
                director=director_name,
                movement=movement_name,
            )
            record = result.single()
            if record and record["ok"]:
                log.info(f"  + ({director_name})-[:PART_OF_MOVEMENT]->({movement_name})")
            else:
                log.warning(
                    f"  Atlandı — node bulunamadı: "
                    f"'{director_name}' veya '{movement_name}'"
                )
                skipped += 1
        except Exception as e:
            log.warning(f"  Hata — ({director_name})->({movement_name}): {e}")
            skipped += 1

    log.info(
        f"{len(ADD_PART_OF_MOVEMENT) - skipped} PART_OF_MOVEMENT işlendi "
        f"(MERGE: duplikat oluşturmaz), {skipped} atlandı."
    )


def verify(session) -> None:
    """Güncel durum doğrulama sorguları. (Memgraph: aggregation WITH'te olmalı)"""
    log.info("─" * 55)
    log.info("Doğrulama sorguları çalışıyor...")

    influenced_by_count = session.run(
        "MATCH ()-[r:INFLUENCED_BY]->() WITH count(r) AS c RETURN c"
    ).single()["c"]

    part_of_movement_count = session.run(
        "MATCH ()-[r:PART_OF_MOVEMENT]->() WITH count(r) AS c RETURN c"
    ).single()["c"]

    movement_count = session.run(
        "MATCH (m:Movement) WITH count(m) AS c RETURN c"
    ).single()["c"]

    person_count = session.run(
        "MATCH (p:Person) WITH count(p) AS c RETURN c"
    ).single()["c"]

    log.info("─" * 55)
    log.info(f"Toplam Person node sayısı            : {person_count}")
    log.info(f"Toplam Movement node sayısı          : {movement_count}")
    log.info(f"Toplam INFLUENCED_BY ilişki sayısı   : {influenced_by_count}")
    log.info(f"Toplam PART_OF_MOVEMENT ilişki sayısı: {part_of_movement_count}")
    log.info("─" * 55)


# ── Ana akış ──────────────────────────────────────────────────────────────────

def main():
    driver = GraphDatabase.driver(URI, auth=None)

    with driver.session() as session:
        add_external_persons(session)
        add_influenced_by(session)
        remove_influenced_by(session)
        add_movements(session)
        add_part_of_movement(session)
        verify(session)

    driver.close()
    log.info("Bağlantı kapatıldı. Güncelleme tamamlandı.")


if __name__ == "__main__":
    main()
