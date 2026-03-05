# Cinema Graph — Setup Guide

Follow the steps below in order to get the project running from scratch.
Once all steps are complete, you will have a clean and up-to-date graph without any manual intervention.

---

## Prerequisites

| Component | Version | Notes |
|---|---|---|
| Python | 3.11+ | |
| Memgraph | 2.x | Must be running at `bolt://localhost:7687` |
| TMDb API Key | — | `TMDB_API_KEY` environment variable |
| Google Gemini API Key | — | `GEMINI_API_KEY` environment variable |
| Node.js | 18+ | For the frontend |

---

## Step 0 — Start Memgraph

Make sure Memgraph is running. If not, start it with Docker:

```bash
docker run -d --name memgraph-platform \
  -p 7687:7687 -p 3000:3000 \
  -v mg_data:/var/lib/memgraph \
  memgraph/memgraph-platform
```

Verify: `docker ps` output should show `memgraph-platform` with port `7687` open.
Access Memgraph Lab at: `http://localhost:3000`

---

## Step 1 — Install Dependencies

```bash
pip install -r requirements.txt
```

---

## Step 2 — Configure Environment Variables

Create a `.env` file in the project root:

```
TMDB_API_KEY=your_tmdb_api_key_here
GEMINI_API_KEY=your_gemini_api_key_here
```

---

## Step 3 — Fetch Filmography Data from TMDb

Fetches filmographies for 34 seed directors from the TMDb API.
Creates `data/films.json`, `data/persons.json`, and `data/relationships.json`.

```bash
python scripts/fetch_filmography.py
```

To fetch only specific directors:
```bash
python scripts/fetch_filmography.py --only "Ingmar Bergman" "David Lynch"
```

**Expected output:** ~699 films, ~6,200 persons, ~10,000+ relationships

---

## Step 4 — Load Data into Memgraph

Clears the DB, loads all JSON data, then applies automatic cleanup steps:

- Removes music video compilations (Michael Jackson, Madonna)
- Adds `:Anthology` label to anthology films
- Removes the Twin Peaks (1989) TV pilot (Fire Walk with Me is kept)
- Merges duplicate Ingmar Bergman nodes (canonical tmdb_id=6648)

```bash
python scripts/load_to_memgraph.py
```

**Expected output:** ~692 Films, ~6,195 Persons, ~10,000+ relationships

---

## Step 5 — Base Enrichment (Movement + INFLUENCED_BY)

Adds 24 cinematic movement (Movement) nodes, 34 PART_OF_MOVEMENT relationships, and 10 INFLUENCED_BY relationships.

```bash
python scripts/load_enrichment.py
```

---

## Step 6 — Extended Enrichment

Adds 22 new INFLUENCED_BY relationships (MERGE prevents duplicates).
Removes 2 unsubstantiated INFLUENCED_BY relationships.
Movement and PART_OF_MOVEMENT updates use MERGE and are idempotent.

```bash
python scripts/update_enrichment.py
```

**Expected state after this step:**
- 24 Movement nodes
- 32 INFLUENCED_BY relationships
- 34 PART_OF_MOVEMENT relationships

---

## Step 7 — Fetch Film Ratings

Fetches `vote_average` and `vote_count` from TMDb for each film
and writes them as `rating` and `vote_count` properties on Film nodes.

```bash
python scripts/fetch_ratings.py
```

Additional options:
```bash
python scripts/fetch_ratings.py --limit 50          # First 50 films (test)
python scripts/fetch_ratings.py --skip-existing     # Skip films that already have a rating
python scripts/fetch_ratings.py --dry-run           # Preview without writing to graph
python scripts/fetch_ratings.py --min-votes 100     # Skip films with low vote counts
```

---

## Step 8 — Load Award Data

Loads manually curated award data from `data/awards.json` into the graph.
Creates Award nodes and adds `(Film)-[:WON_AWARD]->(Award)` and `(Film)-[:NOMINATED_FOR]->(Award)` relationships.

```bash
python scripts/fetch_awards.py
```

Additional options:
```bash
python scripts/fetch_awards.py --dry-run   # Preview without writing to graph
python scripts/fetch_awards.py --stats     # Show statistics
```

**Scope:** ~113 films, ~213 award records (Cannes, Venice, Berlin, Oscars)

---

## Step 9 — Validation

To verify the graph is in the expected state, run in Memgraph Lab or a bolt shell:

```cypher
-- Node counts
MATCH (n) RETURN labels(n)[0] AS label, count(n) AS count ORDER BY count DESC

-- Relationship counts
MATCH ()-[r]->() RETURN type(r) AS type, count(r) AS count ORDER BY count DESC

-- Sample INFLUENCED_BY chain
MATCH p=(a:Person)-[:INFLUENCED_BY*1..3]->(b:Person)
WHERE a.name = "Nuri Bilge Ceylan"
RETURN p LIMIT 10
```

---

## Step 10 — Start the Backend

```bash
cd web/backend
bash run.sh
```

Backend runs at `http://localhost:8000`.
The `GEMINI_API_KEY` environment variable must be defined in the `.env` file.

---

## Step 11 — Start the Frontend

```bash
cd web/frontend
npm install
npm run dev
```

Frontend runs at `http://localhost:3001`.
`/api` requests are proxied to `localhost:8000` via Vite.

---

## Graph Model

### Node Types

| Label | Key Properties |
|---|---|
| `Film` | `tmdb_id`, `title`, `year`, `runtime`, `rating`, `vote_count` |
| `Person` | `tmdb_id`, `name` |
| `Genre` | `name` |
| `Studio` | `name` |
| `Country` | `name` |
| `Movement` | `name` |
| `Award` | `name`, `festival` |

### Relationship Types

| Type | Direction | Description |
|---|---|---|
| `DIRECTOR` | Person → Film | Director |
| `ACTOR` | Person → Film | Actor |
| `DIRECTOR_OF_PHOTOGRAPHY` | Person → Film | Cinematographer |
| `EDITOR` | Person → Film | Editor |
| `ORIGINAL_MUSIC_COMPOSER` | Person → Film | Composer |
| `SOUND_DESIGNER` | Person → Film | Sound design |
| `INFLUENCED_BY` | Person → Person | Cinematic influence |
| `PART_OF_MOVEMENT` | Person → Movement | Cinematic movement membership |
| `HAS_GENRE` | Film → Genre | Genre |
| `PRODUCED_BY` | Film → Studio | Production company |
| `FROM_COUNTRY` | Film → Country | Country of production |
| `WON_AWARD` | Film → Award | Award won |
| `NOMINATED_FOR` | Film → Award | Award nomination |

---

## Seed Directors (34)

Andrei Tarkovsky, Stanley Kubrick, Ingmar Bergman, Woody Allen, Alfred Hitchcock,
Federico Fellini, Akira Kurosawa, Jean Renoir, David Fincher, Quentin Tarantino,
Paul Thomas Anderson, Nuri Bilge Ceylan, Zeki Demirkubuz, David Lynch,
Jean-Luc Godard, François Truffaut, Michelangelo Antonioni, Krzysztof Kieślowski,
Lars von Trier, Michael Haneke, Wim Wenders, Pedro Almodóvar, Wong Kar-wai,
Yasujirō Ozu, Abbas Kiarostami, Park Chan-wook, Bong Joon-ho, Hirokazu Kore-eda,
Martin Scorsese, Joel Coen, Terrence Malick, Spike Lee, Yılmaz Güney, Semih Kaplanoğlu

---

## Re-running

Step 4 (`load_to_memgraph.py`) fully clears and reloads the DB — it is not idempotent.
Steps 5, 6, 7, and 8 use MERGE and are idempotent; they can be safely re-run.
