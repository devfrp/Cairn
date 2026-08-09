-- =====================================================================
-- Cairn — schéma de base
-- PostgreSQL 16 + TimescaleDB 2.x
--
-- Organisation en 3 couches :
--   raw.*     ingestion brute (JSON tel que renvoyé par l'API) — source de vérité
--   public.*  modèle normalisé et dédupliqué
--   marts.*   agrégats calculés + vues consommées par Grafana
--
-- Principe : on ne jette jamais la donnée brute. Si le modèle normalisé
-- évolue, on rejoue la transformation depuis raw.* sans retoucher aux API.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS marts;

-- Journal des migrations appliquées, tenu par `cairn db upgrade`.
CREATE TABLE IF NOT EXISTS schema_migration (
    version    text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now(),
    sha256     bytea NOT NULL
);


-- =====================================================================
-- 1. COUCHE RAW — ingestion sans transformation
-- =====================================================================

-- Une ligne = une réponse d'API. resource_key identifie la ressource côté
-- source : un id d'activité Strava, une date pour les endpoints wellness
-- Garmin (ex. '2026-08-07' pour /sleep), etc.
CREATE TABLE raw.api_payload (
    id            bigserial PRIMARY KEY,
    source        text        NOT NULL CHECK (source IN ('garmin', 'strava')),
    endpoint      text        NOT NULL,   -- 'sleep', 'hrv', 'activity_detail', ...
    resource_key  text        NOT NULL,   -- date ISO ou id externe
    fetched_at    timestamptz NOT NULL DEFAULT now(),
    http_status   int,
    content_hash  bytea       NOT NULL,   -- sha256(payload) : évite de réécrire l'identique
    payload       jsonb       NOT NULL,
    UNIQUE (source, endpoint, resource_key, content_hash)
);
CREATE INDEX ON raw.api_payload (source, endpoint, resource_key, fetched_at DESC);
CREATE INDEX ON raw.api_payload USING gin (payload jsonb_path_ops);

-- Fichiers d'activité originaux (.fit / .gpx / .tcx) archivés sur disque.
-- On stocke le chemin, pas le blob : le volume Docker est sauvegardé par ailleurs.
CREATE TABLE raw.activity_file (
    id            bigserial PRIMARY KEY,
    source        text        NOT NULL CHECK (source IN ('garmin', 'strava')),
    external_id   text        NOT NULL,
    format        text        NOT NULL CHECK (format IN ('fit', 'gpx', 'tcx')),
    path          text        NOT NULL,   -- relatif à CAIRN_FILE_ROOT
    size_bytes    bigint,
    sha256        bytea,
    downloaded_at timestamptz NOT NULL DEFAULT now(),
    parsed_at     timestamptz,            -- NULL = pas encore exploité
    UNIQUE (source, external_id, format)
);


-- =====================================================================
-- 2. RÉFÉRENTIEL
-- =====================================================================

CREATE TABLE athlete (
    id                bigserial PRIMARY KEY,
    display_name      text NOT NULL,
    birth_date        date,
    sex               text CHECK (sex IN ('M', 'F', 'X')),
    height_cm         numeric(5,1),
    garmin_user_id    text UNIQUE,
    strava_athlete_id bigint UNIQUE,
    timezone          text NOT NULL DEFAULT 'Europe/Paris',
    created_at        timestamptz NOT NULL DEFAULT now()
);

-- Zones de FC historisées : elles changent quand la FC max est réévaluée.
-- On garde l'historique pour ne pas fausser le recalcul des sorties anciennes.
CREATE TABLE hr_zone_set (
    id          bigserial PRIMARY KEY,
    athlete_id  bigint NOT NULL REFERENCES athlete(id) ON DELETE CASCADE,
    valid_from  date   NOT NULL,
    hr_max      int    NOT NULL,
    hr_rest     int,
    lthr        int,                       -- seuil lactique, si connu
    source      text   NOT NULL DEFAULT 'garmin',
    UNIQUE (athlete_id, valid_from)
);

CREATE TABLE hr_zone (
    zone_set_id bigint NOT NULL REFERENCES hr_zone_set(id) ON DELETE CASCADE,
    zone_index  int NOT NULL CHECK (zone_index BETWEEN 1 AND 7),
    min_bpm     int NOT NULL,
    max_bpm     int NOT NULL,
    label       text,
    PRIMARY KEY (zone_set_id, zone_index)
);

-- Normalisation des libellés de sport : Garmin dit 'mountain_biking',
-- Strava dit 'MountainBikeRide' ou ('Ride' + sport_type='MountainBikeRide').
-- Table de correspondance alimentée par migration, extensible sans code.
CREATE TABLE sport_map (
    source           text NOT NULL CHECK (source IN ('garmin', 'strava')),
    source_value     text NOT NULL,
    canonical_sport  text NOT NULL,   -- mtb, enduro, road, gravel, trail_run, run, hike, ...
    canonical_family text NOT NULL,   -- cycling, running, other
    PRIMARY KEY (source, source_value)
);


-- =====================================================================
-- 3. MATÉRIEL (Strava)
-- =====================================================================

CREATE TABLE gear (
    id              bigserial PRIMARY KEY,
    athlete_id      bigint NOT NULL REFERENCES athlete(id) ON DELETE CASCADE,
    strava_gear_id  text UNIQUE,          -- 'b1234567' / 'g1234567'
    kind            text NOT NULL CHECK (kind IN ('bike', 'shoes')),
    name            text NOT NULL,
    brand           text,
    model           text,
    frame_type      text,                 -- mtb / road / gravel / tt, côté Strava
    is_primary      boolean NOT NULL DEFAULT false,
    retired         boolean NOT NULL DEFAULT false,
    purchased_on    date,
    distance_m      bigint  NOT NULL DEFAULT 0,  -- cumul rapporté par Strava
    notes           text,
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- Suivi d'usure fin : chaîne, plaquettes, pneus, révision fourche/amorto.
-- Strava ne descend pas à ce niveau, mais c'est ce qui compte en enduro.
CREATE TABLE gear_component (
    id                    bigserial PRIMARY KEY,
    gear_id               bigint NOT NULL REFERENCES gear(id) ON DELETE CASCADE,
    kind                  text   NOT NULL,   -- chain, brake_pads, tyre_front, fork_service, ...
    label                 text,
    installed_on          date   NOT NULL,
    installed_at_gear_m   bigint NOT NULL,   -- km compteur du vélo à la pose
    service_interval_m    bigint,            -- seuil d'alerte, NULL = pas de suivi
    service_interval_days int,
    removed_on            date,
    notes                 text
);
CREATE INDEX ON gear_component (gear_id) WHERE removed_on IS NULL;


-- =====================================================================
-- 4. ACTIVITÉS — entité canonique dédupliquée entre Garmin et Strava
-- =====================================================================

-- Une sortie enregistrée sur la montre remonte dans Garmin ET dans Strava
-- (sync automatique). `activity` est l'entité unique ; `activity_source`
-- porte les 1 à 2 provenances.
CREATE TABLE activity (
    id                  bigserial PRIMARY KEY,
    athlete_id          bigint NOT NULL REFERENCES athlete(id) ON DELETE CASCADE,

    start_time          timestamptz NOT NULL,
    start_time_local    timestamp   NOT NULL,
    local_date          date        NOT NULL,   -- rattachement jour, pour les jointures wellness
    timezone            text,

    sport               text NOT NULL,          -- canonical_sport
    sport_family        text NOT NULL,          -- cycling / running / other
    name                text,
    is_indoor           boolean NOT NULL DEFAULT false,
    is_commute          boolean NOT NULL DEFAULT false,
    is_race             boolean NOT NULL DEFAULT false,

    elapsed_s           int,
    moving_s            int,
    distance_m          numeric(10,1),
    elevation_gain_m    numeric(8,1),
    elevation_loss_m    numeric(8,1),
    elevation_max_m     numeric(8,1),

    avg_speed_mps       numeric(6,3),
    max_speed_mps       numeric(6,3),
    avg_hr              int,
    max_hr              int,
    avg_cadence         numeric(5,1),
    avg_power_w         int,
    weighted_power_w    int,               -- NP / puissance pondérée
    max_power_w         int,
    calories            int,

    -- métriques d'effort : Garmin quand dispo, sinon calculées par Cairn
    training_load       numeric(8,2),      -- TRIMP / Garmin training load
    aerobic_te          numeric(3,1),
    anaerobic_te        numeric(3,1),
    trimp               numeric(8,2),      -- calculé par Cairn depuis les streams
    intensity_factor    numeric(4,3),
    perceived_exertion  int CHECK (perceived_exertion BETWEEN 1 AND 10),
    feel                int CHECK (feel BETWEEN 1 AND 5),

    gear_id             bigint REFERENCES gear(id) ON DELETE SET NULL,
    primary_source      text NOT NULL CHECK (primary_source IN ('garmin', 'strava', 'manual')),
    has_streams         boolean NOT NULL DEFAULT false,
    dedup_key           text,              -- date + bucket start_time, pour l'appariement

    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON activity (athlete_id, start_time DESC);
CREATE INDEX ON activity (local_date);
CREATE INDEX ON activity (sport, start_time DESC);
CREATE INDEX ON activity (gear_id) WHERE gear_id IS NOT NULL;
CREATE INDEX ON activity (dedup_key);

-- Provenance. UNIQUE(source, external_id) rend la resync idempotente.
CREATE TABLE activity_source (
    activity_id    bigint NOT NULL REFERENCES activity(id) ON DELETE CASCADE,
    source         text   NOT NULL CHECK (source IN ('garmin', 'strava')),
    external_id    text   NOT NULL,
    external_url   text,
    raw_payload_id bigint REFERENCES raw.api_payload(id) ON DELETE SET NULL,
    first_seen_at  timestamptz NOT NULL DEFAULT now(),
    last_seen_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source, external_id)
);
CREATE INDEX ON activity_source (activity_id);

CREATE TABLE activity_lap (
    activity_id      bigint NOT NULL REFERENCES activity(id) ON DELETE CASCADE,
    lap_index        int    NOT NULL,
    start_time       timestamptz NOT NULL,
    elapsed_s        int,
    moving_s         int,
    distance_m       numeric(10,1),
    elevation_gain_m numeric(8,1),
    avg_hr           int,
    max_hr           int,
    avg_power_w      int,
    avg_cadence      numeric(5,1),
    avg_speed_mps    numeric(6,3),
    PRIMARY KEY (activity_id, lap_index)
);

-- Temps passé par zone de FC, précalculé depuis les streams à l'ingestion.
-- Évite de rescanner les séries à chaque ouverture d'un dashboard Grafana.
CREATE TABLE activity_zone_time (
    activity_id bigint NOT NULL REFERENCES activity(id) ON DELETE CASCADE,
    metric      text   NOT NULL CHECK (metric IN ('hr', 'power')),
    zone_index  int    NOT NULL,
    seconds     int    NOT NULL,
    PRIMARY KEY (activity_id, metric, zone_index)
);


-- ---------------------------------------------------------------------
-- Streams 1 Hz — hypertable TimescaleDB
-- ~2 M points/an d'activité. Compression segmentby activity_id : ~15x.
-- ---------------------------------------------------------------------
CREATE TABLE activity_stream (
    activity_id   bigint      NOT NULL REFERENCES activity(id) ON DELETE CASCADE,
    time          timestamptz NOT NULL,
    elapsed_s     int         NOT NULL,   -- offset depuis le début, pour l'axe "temps de sortie"
    hr            smallint,
    cadence       smallint,
    power_w       smallint,
    speed_mps     real,
    altitude_m    real,
    distance_m    real,
    grade_pct     real,
    temperature_c real,
    lat           double precision,
    lon           double precision,
    moving        boolean,
    PRIMARY KEY (activity_id, time)
);
-- Signature historique, encore acceptée par TimescaleDB 2.x (avertissement de
-- dépréciation à partir de 2.13). Sur une install récente, équivalent moderne :
--   SELECT create_hypertable('activity_stream', by_range('time', INTERVAL '1 month'));
SELECT create_hypertable('activity_stream', 'time',
                         chunk_time_interval => INTERVAL '1 month');
ALTER TABLE activity_stream SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'activity_id',
    timescaledb.compress_orderby   = 'time ASC'
);
SELECT add_compression_policy('activity_stream', INTERVAL '14 days');


-- =====================================================================
-- 5. SEGMENTS (Strava)
-- =====================================================================

CREATE TABLE segment (
    strava_segment_id bigint PRIMARY KEY,
    name              text NOT NULL,
    activity_family   text,             -- Ride / Run
    distance_m        numeric(10,1),
    avg_grade_pct     numeric(5,2),
    max_grade_pct     numeric(5,2),
    elevation_high_m  numeric(8,1),
    elevation_low_m   numeric(8,1),
    climb_category    int,
    starred           boolean NOT NULL DEFAULT false,
    updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE segment_effort (
    strava_effort_id  bigint PRIMARY KEY,
    segment_id        bigint NOT NULL REFERENCES segment(strava_segment_id) ON DELETE CASCADE,
    activity_id       bigint REFERENCES activity(id) ON DELETE CASCADE,
    start_time        timestamptz NOT NULL,
    elapsed_s         int,
    moving_s          int,
    avg_hr            int,
    max_hr            int,
    avg_power_w       int,
    avg_cadence       numeric(5,1),
    pr_rank           int,
    kom_rank          int
);
CREATE INDEX ON segment_effort (segment_id, start_time DESC);
CREATE INDEX ON segment_effort (activity_id);


-- =====================================================================
-- 6. RÉCUPÉRATION / WELLNESS (Garmin) — granularité jour
-- =====================================================================

-- Un enregistrement par jour calendaire. Tout ce qui est "une valeur par
-- jour" atterrit ici, ce qui rend le croisement récup × charge trivial
-- (jointure sur local_date).
CREATE TABLE daily_wellness (
    athlete_id             bigint NOT NULL REFERENCES athlete(id) ON DELETE CASCADE,
    day                    date   NOT NULL,

    resting_hr             int,
    min_hr                 int,
    max_hr                 int,
    avg_hr                 int,

    avg_stress             int,
    max_stress             int,
    rest_stress_s          int,
    low_stress_s           int,
    medium_stress_s        int,
    high_stress_s          int,

    body_battery_high      int,
    body_battery_low       int,
    body_battery_charged   int,
    body_battery_drained   int,

    steps                  int,
    floors_climbed         int,
    distance_m             int,
    calories_bmr           int,
    calories_active        int,
    moderate_intensity_min int,
    vigorous_intensity_min int,

    avg_spo2               numeric(4,1),
    avg_respiration        numeric(4,1),

    vo2max_running         numeric(4,1),
    vo2max_cycling         numeric(4,1),
    training_readiness     int,             -- score 0-100
    training_readiness_lbl text,
    training_status        text,            -- productive / maintaining / overreaching / ...
    acute_load             numeric(8,1),    -- charge aiguë Garmin
    chronic_load           numeric(8,1),

    updated_at             timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (athlete_id, day)
);

CREATE TABLE sleep_session (
    athlete_id        bigint NOT NULL REFERENCES athlete(id) ON DELETE CASCADE,
    sleep_date        date   NOT NULL,      -- date de RÉVEIL : nuit du 07 au 08 -> 2026-08-08
    start_time        timestamptz,
    end_time          timestamptz,
    duration_s        int,
    deep_s            int,
    light_s           int,
    rem_s             int,
    awake_s           int,
    nap_s             int,
    score             int,                  -- score de sommeil Garmin 0-100
    score_quality     text,
    score_feedback    text,
    avg_hr            int,
    lowest_hr         int,
    avg_respiration   numeric(4,1),
    avg_spo2          numeric(4,1),
    lowest_spo2       int,
    restless_moments  int,
    awake_count       int,
    sleep_need_s      int,
    body_battery_change int,
    PRIMARY KEY (athlete_id, sleep_date)
);

-- Hypnogramme : une ligne par transition de stade.
CREATE TABLE sleep_stage (
    athlete_id bigint NOT NULL,
    sleep_date date   NOT NULL,
    start_time timestamptz NOT NULL,
    end_time   timestamptz NOT NULL,
    stage      text NOT NULL CHECK (stage IN ('deep', 'light', 'rem', 'awake', 'unmeasurable')),
    PRIMARY KEY (athlete_id, sleep_date, start_time),
    FOREIGN KEY (athlete_id, sleep_date)
        REFERENCES sleep_session(athlete_id, sleep_date) ON DELETE CASCADE
);

-- HRV nocturne agrégée : la métrique centrale pour la détection de surcharge.
CREATE TABLE hrv_night (
    athlete_id        bigint NOT NULL REFERENCES athlete(id) ON DELETE CASCADE,
    day               date   NOT NULL,      -- date de réveil, aligné sur sleep_session
    avg_hrv_ms        numeric(6,2),
    last_night_5m_high numeric(6,2),
    weekly_avg_ms     numeric(6,2),
    baseline_low      numeric(6,2),         -- bornes de la baseline Garmin
    baseline_balanced_low  numeric(6,2),
    baseline_balanced_high numeric(6,2),
    baseline_marker   numeric(6,2),
    status            text,                 -- balanced / unbalanced / low / poor
    feedback          text,
    PRIMARY KEY (athlete_id, day)
);

CREATE TABLE body_measurement (
    id            bigserial PRIMARY KEY,
    athlete_id    bigint NOT NULL REFERENCES athlete(id) ON DELETE CASCADE,
    measured_at   timestamptz NOT NULL,
    weight_kg     numeric(5,2),
    body_fat_pct  numeric(4,1),
    muscle_mass_kg numeric(5,2),
    bone_mass_kg  numeric(5,2),
    body_water_pct numeric(4,1),
    bmi           numeric(4,1),
    source        text NOT NULL DEFAULT 'garmin',
    UNIQUE (athlete_id, measured_at, source)
);

-- ---------------------------------------------------------------------
-- Séries intra-journée (stress toutes les 3 min, body battery, FC, SpO2,
-- respiration, lectures HRV 5 min). Format narrow : une nouvelle métrique
-- ne demande aucune migration.
-- ---------------------------------------------------------------------
CREATE TABLE wellness_reading (
    athlete_id bigint      NOT NULL REFERENCES athlete(id) ON DELETE CASCADE,
    time       timestamptz NOT NULL,
    metric     text        NOT NULL,   -- stress, body_battery, hr, spo2, respiration, hrv_5m
    value      real        NOT NULL,
    PRIMARY KEY (athlete_id, metric, time)
);
SELECT create_hypertable('wellness_reading', 'time',
                         chunk_time_interval => INTERVAL '1 month');
ALTER TABLE wellness_reading SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'athlete_id, metric',
    timescaledb.compress_orderby   = 'time ASC'
);
SELECT add_compression_policy('wellness_reading', INTERVAL '14 days');


-- =====================================================================
-- 7. MÉTRIQUES DÉRIVÉES — calculées par Cairn, pas par les sources
-- =====================================================================

-- Une ligne par jour et par athlète, recalculée à chaque sync sur une
-- fenêtre glissante. C'est la table que Grafana attaque en priorité.
CREATE TABLE marts.daily_load (
    athlete_id       bigint NOT NULL REFERENCES athlete(id) ON DELETE CASCADE,
    day              date   NOT NULL,

    session_count    int    NOT NULL DEFAULT 0,
    duration_s       int    NOT NULL DEFAULT 0,
    distance_m       numeric(10,1) NOT NULL DEFAULT 0,
    elevation_gain_m numeric(8,1)  NOT NULL DEFAULT 0,
    load             numeric(8,2)  NOT NULL DEFAULT 0,  -- somme training_load/TRIMP du jour

    ctl              numeric(8,2),   -- charge chronique, EWMA 42 j ("fitness")
    atl              numeric(8,2),   -- charge aiguë, EWMA 7 j ("fatigue")
    tsb              numeric(8,2),   -- ctl - atl ("forme")
    acwr             numeric(5,3),   -- atl / ctl, zone de risque > 1.5
    monotony         numeric(5,2),   -- moyenne 7 j / écart-type 7 j
    strain           numeric(10,2),  -- charge 7 j × monotony

    computed_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (athlete_id, day)
);

-- Baselines de récupération : la comparaison à SA propre normale, pas à
-- un seuil absolu. Fenêtre 60 j, z-score sur HRV et FC repos.
CREATE TABLE marts.recovery_baseline (
    athlete_id      bigint NOT NULL REFERENCES athlete(id) ON DELETE CASCADE,
    day             date   NOT NULL,

    hrv_ms          numeric(6,2),
    hrv_mean_60d    numeric(6,2),
    hrv_sd_60d      numeric(6,2),
    hrv_z           numeric(5,2),
    hrv_trend_7d    numeric(5,2),   -- pente sur 7 j, négative = dégradation

    rhr             int,
    rhr_mean_60d    numeric(5,1),
    rhr_delta       numeric(5,1),

    sleep_score     int,
    sleep_score_7d  numeric(5,1),
    sleep_debt_s    int,            -- cumul (besoin - réel) sur 7 j

    readiness       int,
    computed_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (athlete_id, day)
);

-- Signaux détectés (surcharge, ACWR élevé, HRV en baisse N jours d'affilée).
-- Alimente le résumé hebdo et, plus tard, les alertes proactives.
CREATE TABLE marts.flag (
    id           bigserial PRIMARY KEY,
    athlete_id   bigint NOT NULL REFERENCES athlete(id) ON DELETE CASCADE,
    day          date   NOT NULL,
    kind         text   NOT NULL,   -- hrv_suppressed, acwr_high, monotony_high, rhr_elevated
    severity     text   NOT NULL CHECK (severity IN ('info', 'warn', 'alert')),
    message      text   NOT NULL,
    detail       jsonb,
    acknowledged boolean NOT NULL DEFAULT false,
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (athlete_id, day, kind)
);


-- =====================================================================
-- 8. RÉSUMÉS LLM (Ollama / Qwen3-32B)
-- =====================================================================

CREATE TABLE llm_summary (
    id           bigserial PRIMARY KEY,
    athlete_id   bigint NOT NULL REFERENCES athlete(id) ON DELETE CASCADE,
    period_kind  text   NOT NULL CHECK (period_kind IN ('weekly', 'monthly', 'adhoc')),
    period_start date   NOT NULL,
    period_end   date   NOT NULL,
    model        text   NOT NULL,
    input_facts  jsonb  NOT NULL,   -- le contexte chiffré fourni au modèle, pour audit
    summary_md   text   NOT NULL,
    generated_at timestamptz NOT NULL DEFAULT now(),
    latency_ms   int,
    delivered_at timestamptz,
    delivery_channel text,          -- 'discord'
    UNIQUE (athlete_id, period_kind, period_start)
);


-- =====================================================================
-- 9. OPÉRATIONNEL — sync, curseurs, quotas
-- =====================================================================

CREATE TABLE sync_run (
    id             bigserial PRIMARY KEY,
    source         text NOT NULL CHECK (source IN ('garmin', 'strava', 'derived', 'llm')),
    mode           text NOT NULL CHECK (mode IN ('backfill', 'incremental', 'manual')),
    started_at     timestamptz NOT NULL DEFAULT now(),
    finished_at    timestamptz,
    status         text NOT NULL DEFAULT 'running'
                   CHECK (status IN ('running', 'ok', 'partial', 'failed')),
    api_calls      int NOT NULL DEFAULT 0,
    items_fetched  int NOT NULL DEFAULT 0,
    items_upserted int NOT NULL DEFAULT 0,
    error          text,
    detail         jsonb
);
CREATE INDEX ON sync_run (source, started_at DESC);

-- Reprise du backfill : où en est-on, ressource par ressource.
-- Le backfill wellness Garmin descend jour par jour ; en cas d'interruption
-- on repart de backfill_cursor sans refaire les appels déjà passés.
CREATE TABLE sync_cursor (
    source            text NOT NULL CHECK (source IN ('garmin', 'strava')),
    resource          text NOT NULL,   -- 'activities', 'sleep', 'hrv', 'stress', 'gear', ...
    last_success_at   timestamptz,
    last_external_id  text,
    incremental_from  timestamptz,     -- borne basse de la prochaine passe incrémentale
    backfill_cursor   date,            -- descend vers le passé, NULL = backfill terminé
    backfill_done     boolean NOT NULL DEFAULT false,
    consecutive_errors int NOT NULL DEFAULT 0,
    PRIMARY KEY (source, resource)
);

-- Compteur de quota Strava (100 req/15 min, 1000 req/jour côté lecture).
-- Alimenté par les en-têtes X-RateLimit-Usage renvoyés par l'API.
CREATE TABLE api_quota (
    source       text NOT NULL,
    window_kind  text NOT NULL CHECK (window_kind IN ('short', 'daily')),
    window_start timestamptz NOT NULL,
    usage        int NOT NULL DEFAULT 0,
    limit_value  int NOT NULL,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source, window_kind, window_start)
);

-- Secrets de session : tokens OAuth Strava, cache de session Garth/Garmin.
-- Chiffré applicativement (clé hors base, dans l'env de la LXC).
CREATE TABLE credential (
    source       text PRIMARY KEY CHECK (source IN ('garmin', 'strava')),
    payload_enc  bytea NOT NULL,
    expires_at   timestamptz,
    updated_at   timestamptz NOT NULL DEFAULT now()
);


-- =====================================================================
-- 10. VUES POUR GRAFANA
-- =====================================================================

-- La vue centrale : récupération du matin J en regard de la charge du jour J.
-- C'est exactement ce que ni Garmin ni Strava ne montrent.
CREATE VIEW marts.v_daily AS
SELECT
    d.day,
    d.athlete_id,
    -- récupération (mesurée au réveil du jour J)
    r.hrv_ms,
    r.hrv_z,
    r.hrv_trend_7d,
    r.rhr,
    r.rhr_delta,
    s.score               AS sleep_score,
    s.duration_s          AS sleep_duration_s,
    w.body_battery_high,
    w.training_readiness,
    -- charge (produite dans la journée J)
    d.session_count,
    d.duration_s          AS training_duration_s,
    d.distance_m,
    d.elevation_gain_m,
    d.load,
    d.ctl, d.atl, d.tsb, d.acwr, d.monotony
FROM marts.daily_load d
LEFT JOIN marts.recovery_baseline r USING (athlete_id, day)
LEFT JOIN sleep_session  s ON s.athlete_id = d.athlete_id AND s.sleep_date = d.day
LEFT JOIN daily_wellness w ON w.athlete_id = d.athlete_id AND w.day       = d.day;

-- Chaque activité avec l'état de récupération du matin même.
CREATE VIEW marts.v_activity_with_recovery AS
SELECT
    a.id, a.start_time_local, a.sport, a.name, a.gear_id,
    a.elapsed_s, a.moving_s, a.distance_m, a.elevation_gain_m,
    a.avg_hr, a.max_hr, a.avg_power_w, a.training_load, a.trimp,
    r.hrv_ms, r.hrv_z, r.rhr_delta,
    s.score AS sleep_score, s.duration_s AS sleep_duration_s,
    w.training_readiness, w.body_battery_high
FROM activity a
LEFT JOIN marts.recovery_baseline r ON r.athlete_id = a.athlete_id AND r.day = a.local_date
LEFT JOIN sleep_session  s ON s.athlete_id = a.athlete_id AND s.sleep_date = a.local_date
LEFT JOIN daily_wellness w ON w.athlete_id = a.athlete_id AND w.day        = a.local_date;

-- Usure matériel : distance parcourue depuis la pose de chaque composant.
CREATE VIEW marts.v_gear_wear AS
SELECT
    g.id AS gear_id, g.name AS gear_name, g.kind, g.distance_m AS gear_total_m,
    c.id AS component_id, c.kind AS component_kind, c.label,
    c.installed_on,
    g.distance_m - c.installed_at_gear_m AS since_install_m,
    c.service_interval_m,
    CASE WHEN c.service_interval_m IS NULL THEN NULL
         ELSE round(100.0 * (g.distance_m - c.installed_at_gear_m) / c.service_interval_m, 1)
    END AS wear_pct
FROM gear g
JOIN gear_component c ON c.gear_id = g.id AND c.removed_on IS NULL;
