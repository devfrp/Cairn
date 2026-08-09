-- =====================================================================
-- Cairn — schéma de base (SQLite)
--
-- Organisation en trois couches, marquées par le préfixe des tables
-- puisque SQLite ne connaît pas les schémas nommés :
--   raw_*    ingestion brute (JSON tel que renvoyé par l'API) — source de vérité
--   (nu)     modèle normalisé et dédupliqué
--   marts_*  agrégats calculés, prêts à afficher
--
-- Principe : on ne jette jamais la donnée brute. Si le modèle normalisé
-- évolue, on rejoue la transformation depuis raw_* sans retoucher aux API.
--
-- Types déclarés : TIMESTAMP (ISO 8601 UTC), LOCALTIME (heure locale sans
-- fuseau), DATE (AAAA-MM-JJ). Ils pilotent la conversion automatique côté
-- Python, SQLite ne les interprétant pas lui-même.
-- =====================================================================

-- Journal des migrations appliquées, tenu par `cairn db upgrade`.
CREATE TABLE IF NOT EXISTS schema_migration (
    version    TEXT PRIMARY KEY,
    applied_at TIMESTAMP NOT NULL DEFAULT (datetime('now')),
    sha256     BLOB NOT NULL
);


-- =====================================================================
-- 1. COUCHE RAW — ingestion sans transformation
-- =====================================================================

CREATE TABLE raw_api_payload (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT NOT NULL CHECK (source IN ('garmin', 'strava')),
    endpoint      TEXT NOT NULL,   -- 'sleep', 'hrv', 'activity_detail', ...
    resource_key  TEXT NOT NULL,   -- date ISO ou identifiant externe
    fetched_at    TIMESTAMP NOT NULL DEFAULT (datetime('now')),
    http_status   INTEGER,
    content_hash  BLOB NOT NULL,   -- sha256(payload) : évite de réécrire l'identique
    payload       TEXT NOT NULL,   -- JSON
    UNIQUE (source, endpoint, resource_key, content_hash)
);
CREATE INDEX idx_payload_lookup
    ON raw_api_payload (source, endpoint, resource_key, fetched_at DESC);

-- Fichiers d'activité originaux (.fit / .gpx / .tcx) archivés sur disque.
-- On stocke le chemin, pas le contenu : ces fichiers relèvent de la
-- sauvegarde du système de fichiers, pas de celle de la base.
CREATE TABLE raw_activity_file (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT NOT NULL CHECK (source IN ('garmin', 'strava')),
    external_id   TEXT NOT NULL,
    format        TEXT NOT NULL CHECK (format IN ('fit', 'gpx', 'tcx')),
    path          TEXT NOT NULL,   -- relatif à CAIRN_FILE_ROOT
    size_bytes    INTEGER,
    sha256        BLOB,
    downloaded_at TIMESTAMP NOT NULL DEFAULT (datetime('now')),
    parsed_at     TIMESTAMP,       -- NULL = pas encore exploité
    UNIQUE (source, external_id, format)
);
CREATE INDEX idx_file_unparsed ON raw_activity_file (parsed_at) WHERE parsed_at IS NULL;


-- =====================================================================
-- 2. RÉFÉRENTIEL
-- =====================================================================

CREATE TABLE athlete (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name      TEXT NOT NULL,
    birth_date        DATE,
    sex               TEXT CHECK (sex IN ('M', 'F', 'X')),
    height_cm         REAL,
    garmin_user_id    TEXT UNIQUE,
    strava_athlete_id INTEGER UNIQUE,
    timezone          TEXT NOT NULL DEFAULT 'Europe/Paris',
    created_at        TIMESTAMP NOT NULL DEFAULT (datetime('now'))
);

-- Zones de FC historisées : elles changent quand la FC max est réévaluée.
-- On garde l'historique pour ne pas fausser le recalcul des sorties anciennes.
CREATE TABLE hr_zone_set (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id  INTEGER NOT NULL REFERENCES athlete(id) ON DELETE CASCADE,
    valid_from  DATE NOT NULL,
    hr_max      INTEGER NOT NULL,
    hr_rest     INTEGER,
    lthr        INTEGER,                -- seuil lactique, si connu
    source      TEXT NOT NULL DEFAULT 'garmin',
    UNIQUE (athlete_id, valid_from)
);

CREATE TABLE hr_zone (
    zone_set_id INTEGER NOT NULL REFERENCES hr_zone_set(id) ON DELETE CASCADE,
    zone_index  INTEGER NOT NULL CHECK (zone_index BETWEEN 1 AND 7),
    min_bpm     INTEGER NOT NULL,
    max_bpm     INTEGER NOT NULL,
    label       TEXT,
    PRIMARY KEY (zone_set_id, zone_index)
);

-- Normalisation des libellés de sport : Garmin dit 'mountain_biking',
-- Strava dit 'MountainBikeRide'. Table extensible sans toucher au code.
CREATE TABLE sport_map (
    source           TEXT NOT NULL CHECK (source IN ('garmin', 'strava')),
    source_value     TEXT NOT NULL,
    canonical_sport  TEXT NOT NULL,   -- mtb, enduro, road, gravel, trail_run, ...
    canonical_family TEXT NOT NULL,   -- cycling, running, other
    PRIMARY KEY (source, source_value)
);


-- =====================================================================
-- 3. MATÉRIEL (Strava)
-- =====================================================================

CREATE TABLE gear (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id      INTEGER NOT NULL REFERENCES athlete(id) ON DELETE CASCADE,
    strava_gear_id  TEXT UNIQUE,       -- 'b1234567' / 'g1234567'
    kind            TEXT NOT NULL CHECK (kind IN ('bike', 'shoes')),
    name            TEXT NOT NULL,
    brand           TEXT,
    model           TEXT,
    frame_type      TEXT,              -- mtb / cross / road / tt, côté Strava
    is_primary      INTEGER NOT NULL DEFAULT 0,
    retired         INTEGER NOT NULL DEFAULT 0,
    purchased_on    DATE,
    distance_m      INTEGER NOT NULL DEFAULT 0,  -- cumul rapporté par Strava
    notes           TEXT,
    updated_at      TIMESTAMP NOT NULL DEFAULT (datetime('now'))
);

-- Suivi d'usure fin : chaîne, plaquettes, pneus, révision fourche/amorto.
-- Strava ne descend pas à ce niveau, mais c'est ce qui compte en enduro.
CREATE TABLE gear_component (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    gear_id               INTEGER NOT NULL REFERENCES gear(id) ON DELETE CASCADE,
    kind                  TEXT NOT NULL,   -- chain, brake_pads, tyre_front, ...
    label                 TEXT,
    installed_on          DATE NOT NULL,
    installed_at_gear_m   INTEGER NOT NULL,  -- compteur du vélo à la pose
    service_interval_m    INTEGER,           -- seuil d'alerte, NULL = pas de suivi
    service_interval_days INTEGER,
    removed_on            DATE,
    notes                 TEXT
);
CREATE INDEX idx_component_active ON gear_component (gear_id) WHERE removed_on IS NULL;


-- =====================================================================
-- 4. ACTIVITÉS — entité canonique dédupliquée entre Garmin et Strava
-- =====================================================================

-- Une sortie enregistrée sur la montre remonte dans Garmin ET dans Strava.
-- `activity` est l'entité unique ; `activity_source` porte les provenances.
CREATE TABLE activity (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id          INTEGER NOT NULL REFERENCES athlete(id) ON DELETE CASCADE,

    start_time          TIMESTAMP NOT NULL,
    start_time_local    LOCALTIME NOT NULL,
    local_date          DATE NOT NULL,   -- rattachement jour, pour les jointures
    timezone            TEXT,

    sport               TEXT NOT NULL,   -- canonical_sport
    sport_family        TEXT NOT NULL,   -- cycling / running / other
    name                TEXT,
    is_indoor           INTEGER NOT NULL DEFAULT 0,
    is_commute          INTEGER NOT NULL DEFAULT 0,
    is_race             INTEGER NOT NULL DEFAULT 0,

    elapsed_s           INTEGER,
    moving_s            INTEGER,
    distance_m          REAL,
    elevation_gain_m    REAL,
    elevation_loss_m    REAL,
    elevation_max_m     REAL,

    avg_speed_mps       REAL,
    max_speed_mps       REAL,
    avg_hr              INTEGER,
    max_hr              INTEGER,
    avg_cadence         REAL,
    avg_power_w         INTEGER,
    weighted_power_w    INTEGER,
    max_power_w         INTEGER,
    calories            INTEGER,

    -- métriques d'effort : Garmin quand disponible, sinon calculées par Cairn
    training_load       REAL,
    aerobic_te          REAL,
    anaerobic_te        REAL,
    trimp               REAL,
    intensity_factor    REAL,
    perceived_exertion  INTEGER,
    feel                INTEGER,

    gear_id             INTEGER REFERENCES gear(id) ON DELETE SET NULL,
    primary_source      TEXT NOT NULL CHECK (primary_source IN ('garmin', 'strava', 'manual')),
    has_streams         INTEGER NOT NULL DEFAULT 0,
    dedup_key           TEXT,

    created_at          TIMESTAMP NOT NULL DEFAULT (datetime('now')),
    updated_at          TIMESTAMP NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_activity_start ON activity (athlete_id, start_time DESC);
CREATE INDEX idx_activity_date ON activity (local_date);
CREATE INDEX idx_activity_sport ON activity (sport, start_time DESC);
CREATE INDEX idx_activity_gear ON activity (gear_id) WHERE gear_id IS NOT NULL;
CREATE INDEX idx_activity_dedup ON activity (dedup_key);

-- Provenance. La clé primaire rend la resynchronisation idempotente.
CREATE TABLE activity_source (
    activity_id    INTEGER NOT NULL REFERENCES activity(id) ON DELETE CASCADE,
    source         TEXT NOT NULL CHECK (source IN ('garmin', 'strava')),
    external_id    TEXT NOT NULL,
    external_url   TEXT,
    raw_payload_id INTEGER REFERENCES raw_api_payload(id) ON DELETE SET NULL,
    first_seen_at  TIMESTAMP NOT NULL DEFAULT (datetime('now')),
    last_seen_at   TIMESTAMP NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (source, external_id)
);
CREATE INDEX idx_source_activity ON activity_source (activity_id);

CREATE TABLE activity_lap (
    activity_id      INTEGER NOT NULL REFERENCES activity(id) ON DELETE CASCADE,
    lap_index        INTEGER NOT NULL,
    start_time       TIMESTAMP NOT NULL,
    elapsed_s        INTEGER,
    moving_s         INTEGER,
    distance_m       REAL,
    elevation_gain_m REAL,
    avg_hr           INTEGER,
    max_hr           INTEGER,
    avg_power_w      INTEGER,
    avg_cadence      REAL,
    avg_speed_mps    REAL,
    PRIMARY KEY (activity_id, lap_index)
);

-- Temps passé par zone de FC, précalculé depuis les streams à l'ingestion :
-- l'affichage n'a jamais à rescanner la série.
CREATE TABLE activity_zone_time (
    activity_id INTEGER NOT NULL REFERENCES activity(id) ON DELETE CASCADE,
    metric      TEXT NOT NULL CHECK (metric IN ('hr', 'power')),
    zone_index  INTEGER NOT NULL,
    seconds     INTEGER NOT NULL,
    PRIMARY KEY (activity_id, metric, zone_index)
);

-- Streams seconde par seconde. Sans le stockage en colonnes d'un moteur
-- spécialisé, la table reste volumineuse : l'index sur (activity_id,
-- elapsed_s) suffit, puisqu'on ne lit jamais qu'une sortie à la fois.
CREATE TABLE activity_stream (
    activity_id   INTEGER NOT NULL REFERENCES activity(id) ON DELETE CASCADE,
    time          TIMESTAMP NOT NULL,
    elapsed_s     INTEGER NOT NULL,   -- offset depuis le départ
    hr            INTEGER,
    cadence       INTEGER,
    power_w       INTEGER,
    speed_mps     REAL,
    altitude_m    REAL,
    distance_m    REAL,
    grade_pct     REAL,
    temperature_c REAL,
    lat           REAL,
    lon           REAL,
    moving        INTEGER,
    PRIMARY KEY (activity_id, elapsed_s)
) WITHOUT ROWID;


-- =====================================================================
-- 5. SEGMENTS (Strava)
-- =====================================================================

CREATE TABLE segment (
    strava_segment_id INTEGER PRIMARY KEY,
    name              TEXT NOT NULL,
    activity_family   TEXT,
    distance_m        REAL,
    avg_grade_pct     REAL,
    max_grade_pct     REAL,
    elevation_high_m  REAL,
    elevation_low_m   REAL,
    climb_category    INTEGER,
    starred           INTEGER NOT NULL DEFAULT 0,
    updated_at        TIMESTAMP NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE segment_effort (
    strava_effort_id INTEGER PRIMARY KEY,
    segment_id       INTEGER NOT NULL REFERENCES segment(strava_segment_id) ON DELETE CASCADE,
    activity_id      INTEGER REFERENCES activity(id) ON DELETE CASCADE,
    start_time       TIMESTAMP NOT NULL,
    elapsed_s        INTEGER,
    moving_s         INTEGER,
    avg_hr           INTEGER,
    max_hr           INTEGER,
    avg_power_w      INTEGER,
    avg_cadence      REAL,
    pr_rank          INTEGER,
    kom_rank         INTEGER
);
CREATE INDEX idx_effort_segment ON segment_effort (segment_id, start_time DESC);
CREATE INDEX idx_effort_activity ON segment_effort (activity_id);


-- =====================================================================
-- 6. RÉCUPÉRATION (Garmin) — granularité jour
-- =====================================================================

CREATE TABLE daily_wellness (
    athlete_id             INTEGER NOT NULL REFERENCES athlete(id) ON DELETE CASCADE,
    day                    DATE NOT NULL,

    resting_hr             INTEGER,
    min_hr                 INTEGER,
    max_hr                 INTEGER,
    avg_hr                 INTEGER,

    avg_stress             INTEGER,
    max_stress             INTEGER,
    rest_stress_s          INTEGER,
    low_stress_s           INTEGER,
    medium_stress_s        INTEGER,
    high_stress_s          INTEGER,

    body_battery_high      INTEGER,
    body_battery_low       INTEGER,
    body_battery_charged   INTEGER,
    body_battery_drained   INTEGER,

    steps                  INTEGER,
    floors_climbed         INTEGER,
    distance_m             INTEGER,
    calories_bmr           INTEGER,
    calories_active        INTEGER,
    moderate_intensity_min INTEGER,
    vigorous_intensity_min INTEGER,

    avg_spo2               REAL,
    avg_respiration        REAL,

    vo2max_running         REAL,
    vo2max_cycling         REAL,
    training_readiness     INTEGER,
    training_readiness_lbl TEXT,
    training_status        TEXT,
    acute_load             REAL,
    chronic_load           REAL,

    updated_at             TIMESTAMP NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (athlete_id, day)
);

CREATE TABLE sleep_session (
    athlete_id          INTEGER NOT NULL REFERENCES athlete(id) ON DELETE CASCADE,
    sleep_date          DATE NOT NULL,   -- date de RÉVEIL
    start_time          TIMESTAMP,
    end_time            TIMESTAMP,
    duration_s          INTEGER,
    deep_s              INTEGER,
    light_s             INTEGER,
    rem_s               INTEGER,
    awake_s             INTEGER,
    nap_s               INTEGER,
    score               INTEGER,
    score_quality       TEXT,
    score_feedback      TEXT,
    avg_hr              INTEGER,
    lowest_hr           INTEGER,
    avg_respiration     REAL,
    avg_spo2            REAL,
    lowest_spo2         INTEGER,
    restless_moments    INTEGER,
    awake_count         INTEGER,
    sleep_need_s        INTEGER,
    body_battery_change INTEGER,
    PRIMARY KEY (athlete_id, sleep_date)
);

-- Hypnogramme : une ligne par transition de stade.
CREATE TABLE sleep_stage (
    athlete_id INTEGER NOT NULL,
    sleep_date DATE NOT NULL,
    start_time TIMESTAMP NOT NULL,
    end_time   TIMESTAMP NOT NULL,
    stage      TEXT NOT NULL CHECK (stage IN ('deep', 'light', 'rem', 'awake', 'unmeasurable')),
    PRIMARY KEY (athlete_id, sleep_date, start_time),
    FOREIGN KEY (athlete_id, sleep_date)
        REFERENCES sleep_session(athlete_id, sleep_date) ON DELETE CASCADE
);

-- VFC nocturne : la métrique centrale pour la détection de surcharge.
CREATE TABLE hrv_night (
    athlete_id             INTEGER NOT NULL REFERENCES athlete(id) ON DELETE CASCADE,
    day                    DATE NOT NULL,   -- date de réveil
    avg_hrv_ms             REAL,
    last_night_5m_high     REAL,
    weekly_avg_ms          REAL,
    baseline_low           REAL,
    baseline_balanced_low  REAL,
    baseline_balanced_high REAL,
    baseline_marker        REAL,
    status                 TEXT,
    feedback               TEXT,
    PRIMARY KEY (athlete_id, day)
);

CREATE TABLE body_measurement (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id     INTEGER NOT NULL REFERENCES athlete(id) ON DELETE CASCADE,
    measured_at    TIMESTAMP NOT NULL,
    weight_kg      REAL,
    body_fat_pct   REAL,
    muscle_mass_kg REAL,
    bone_mass_kg   REAL,
    body_water_pct REAL,
    bmi            REAL,
    source         TEXT NOT NULL DEFAULT 'garmin',
    UNIQUE (athlete_id, measured_at, source)
);

-- Séries intra-journée : stress, body battery, FC, SpO2, respiration, VFC.
-- Format étroit : une métrique nouvelle ne demande aucune migration.
CREATE TABLE wellness_reading (
    athlete_id INTEGER NOT NULL REFERENCES athlete(id) ON DELETE CASCADE,
    time       TIMESTAMP NOT NULL,
    metric     TEXT NOT NULL,
    value      REAL NOT NULL,
    PRIMARY KEY (athlete_id, metric, time)
) WITHOUT ROWID;


-- =====================================================================
-- 7. MÉTRIQUES DÉRIVÉES — calculées par Cairn
-- =====================================================================

CREATE TABLE marts_daily_load (
    athlete_id       INTEGER NOT NULL REFERENCES athlete(id) ON DELETE CASCADE,
    day              DATE NOT NULL,

    session_count    INTEGER NOT NULL DEFAULT 0,
    duration_s       INTEGER NOT NULL DEFAULT 0,
    distance_m       REAL NOT NULL DEFAULT 0,
    elevation_gain_m REAL NOT NULL DEFAULT 0,
    load             REAL NOT NULL DEFAULT 0,

    ctl              REAL,   -- charge chronique, moyenne exponentielle 42 j
    atl              REAL,   -- charge aiguë, 7 j
    tsb              REAL,   -- ctl - atl
    acwr             REAL,   -- atl / ctl
    monotony         REAL,
    strain           REAL,

    computed_at      TIMESTAMP NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (athlete_id, day)
);

-- Comparaison à sa propre normale, pas à un seuil absolu.
CREATE TABLE marts_recovery_baseline (
    athlete_id     INTEGER NOT NULL REFERENCES athlete(id) ON DELETE CASCADE,
    day            DATE NOT NULL,

    hrv_ms         REAL,
    hrv_mean_60d   REAL,
    hrv_sd_60d     REAL,
    hrv_z          REAL,
    hrv_trend_7d   REAL,

    rhr            INTEGER,
    rhr_mean_60d   REAL,
    rhr_delta      REAL,

    sleep_score    INTEGER,
    sleep_score_7d REAL,
    sleep_debt_s   INTEGER,

    readiness      INTEGER,
    computed_at    TIMESTAMP NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (athlete_id, day)
);

CREATE TABLE marts_flag (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id   INTEGER NOT NULL REFERENCES athlete(id) ON DELETE CASCADE,
    day          DATE NOT NULL,
    kind         TEXT NOT NULL,
    severity     TEXT NOT NULL CHECK (severity IN ('info', 'warn', 'alert')),
    message      TEXT NOT NULL,
    detail       TEXT,   -- JSON
    acknowledged INTEGER NOT NULL DEFAULT 0,
    created_at   TIMESTAMP NOT NULL DEFAULT (datetime('now')),
    UNIQUE (athlete_id, day, kind)
);


-- =====================================================================
-- 8. RÉSUMÉS RÉDIGÉS
-- =====================================================================

CREATE TABLE llm_summary (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id       INTEGER NOT NULL REFERENCES athlete(id) ON DELETE CASCADE,
    period_kind      TEXT NOT NULL CHECK (period_kind IN ('weekly', 'monthly', 'adhoc')),
    period_start     DATE NOT NULL,
    period_end       DATE NOT NULL,
    model            TEXT NOT NULL,
    input_facts      TEXT NOT NULL,   -- JSON, pour audit
    summary_md       TEXT NOT NULL,
    generated_at     TIMESTAMP NOT NULL DEFAULT (datetime('now')),
    latency_ms       INTEGER,
    delivered_at     TIMESTAMP,
    delivery_channel TEXT,
    UNIQUE (athlete_id, period_kind, period_start)
);


-- =====================================================================
-- 9. OPÉRATIONNEL — synchronisation, curseurs, quotas
-- =====================================================================

CREATE TABLE sync_run (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    source         TEXT NOT NULL CHECK (source IN ('garmin', 'strava', 'derived', 'llm')),
    mode           TEXT NOT NULL CHECK (mode IN ('backfill', 'incremental', 'manual')),
    started_at     TIMESTAMP NOT NULL DEFAULT (datetime('now')),
    finished_at    TIMESTAMP,
    status         TEXT NOT NULL DEFAULT 'running'
                   CHECK (status IN ('running', 'ok', 'partial', 'failed')),
    api_calls      INTEGER NOT NULL DEFAULT 0,
    items_fetched  INTEGER NOT NULL DEFAULT 0,
    items_upserted INTEGER NOT NULL DEFAULT 0,
    error          TEXT,
    detail         TEXT   -- JSON
);
CREATE INDEX idx_run_recent ON sync_run (source, started_at DESC);

-- Reprise du rattrapage : où en est-on, ressource par ressource.
CREATE TABLE sync_cursor (
    source             TEXT NOT NULL CHECK (source IN ('garmin', 'strava')),
    resource           TEXT NOT NULL,
    last_success_at    TIMESTAMP,
    last_external_id   TEXT,
    incremental_from   TIMESTAMP,
    backfill_cursor    DATE,
    backfill_done      INTEGER NOT NULL DEFAULT 0,
    consecutive_errors INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (source, resource)
);

-- Compteur de quota Strava, alimenté par les en-têtes X-RateLimit-Usage.
CREATE TABLE api_quota (
    source       TEXT NOT NULL,
    window_kind  TEXT NOT NULL CHECK (window_kind IN ('short', 'daily')),
    window_start TIMESTAMP NOT NULL,
    usage        INTEGER NOT NULL DEFAULT 0,
    limit_value  INTEGER NOT NULL,
    updated_at   TIMESTAMP NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (source, window_kind, window_start)
);

-- Jetons OAuth Strava et session Garmin, chiffrés applicativement : la clé
-- vit hors de la base, de sorte qu'un fichier de données copié ne donne pas
-- accès aux comptes.
CREATE TABLE credential (
    source      TEXT PRIMARY KEY CHECK (source IN ('garmin', 'strava')),
    payload_enc BLOB NOT NULL,
    expires_at  TIMESTAMP,
    updated_at  TIMESTAMP NOT NULL DEFAULT (datetime('now'))
);


-- =====================================================================
-- 10. VUES
-- =====================================================================

-- La vue centrale : récupération du matin en regard de la charge du jour.
-- C'est exactement ce que ni Garmin ni Strava ne montre.
CREATE VIEW v_daily AS
SELECT
    d.day,
    d.athlete_id,
    r.hrv_ms,
    r.hrv_z,
    r.hrv_trend_7d,
    r.rhr,
    r.rhr_delta,
    s.score      AS sleep_score,
    s.duration_s AS sleep_duration_s,
    w.body_battery_high,
    w.training_readiness,
    d.session_count,
    d.duration_s AS training_duration_s,
    d.distance_m,
    d.elevation_gain_m,
    d.load,
    d.ctl, d.atl, d.tsb, d.acwr, d.monotony
FROM marts_daily_load d
LEFT JOIN marts_recovery_baseline r
       ON r.athlete_id = d.athlete_id AND r.day = d.day
LEFT JOIN sleep_session s
       ON s.athlete_id = d.athlete_id AND s.sleep_date = d.day
LEFT JOIN daily_wellness w
       ON w.athlete_id = d.athlete_id AND w.day = d.day;

-- Chaque activité avec l'état de récupération du matin même.
CREATE VIEW v_activity_with_recovery AS
SELECT
    a.id, a.start_time_local, a.sport, a.name, a.gear_id,
    a.elapsed_s, a.moving_s, a.distance_m, a.elevation_gain_m,
    a.avg_hr, a.max_hr, a.avg_power_w, a.training_load, a.trimp,
    r.hrv_ms, r.hrv_z, r.rhr_delta,
    s.score AS sleep_score, s.duration_s AS sleep_duration_s,
    w.training_readiness, w.body_battery_high
FROM activity a
LEFT JOIN marts_recovery_baseline r
       ON r.athlete_id = a.athlete_id AND r.day = a.local_date
LEFT JOIN sleep_session s
       ON s.athlete_id = a.athlete_id AND s.sleep_date = a.local_date
LEFT JOIN daily_wellness w
       ON w.athlete_id = a.athlete_id AND w.day = a.local_date;

-- Usure du matériel : distance parcourue depuis la pose de chaque composant.
CREATE VIEW v_gear_wear AS
SELECT
    g.id AS gear_id, g.name AS gear_name, g.kind, g.distance_m AS gear_total_m,
    c.id AS component_id, c.kind AS component_kind, c.label,
    c.installed_on,
    g.distance_m - c.installed_at_gear_m AS since_install_m,
    c.service_interval_m,
    CASE WHEN c.service_interval_m IS NULL THEN NULL
         ELSE round(100.0 * (g.distance_m - c.installed_at_gear_m)
                    / c.service_interval_m, 1)
    END AS wear_pct
FROM gear g
JOIN gear_component c ON c.gear_id = g.id AND c.removed_on IS NULL;
