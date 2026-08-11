-- =====================================================================
-- Cairn — sports rencontrés à la première synchronisation réelle
--
-- La table de correspondance avait été écrite d'après la documentation des
-- deux services. Une fois de vraies données rapatriées, sept libellés y
-- manquaient encore et tombaient en 'other' — dont le surf, qui représentait
-- à lui seul trente-quatre séances.
--
-- Les deux services ne nomment pas la même chose pareil : Strava dit
-- « Surfing », Garmin « surfing_v2 » (le suffixe est celui d'une refonte de
-- leurs profils, pas une variante de sport).
--
-- `incident_detected` n'est pas un sport mais la détection de chute de
-- Garmin, qui crée une activité fantôme. On la nomme pour qu'elle cesse de
-- se confondre avec un entraînement.
-- =====================================================================

INSERT INTO sport_map (source, source_value, canonical_sport, canonical_family) VALUES
-- ---------- glisse ----------
('strava', 'Surfing',            'surf',      'water'),
('garmin', 'surfing_v2',         'surf',      'water'),
('garmin', 'surfing',            'surf',      'water'),
('strava', 'StandUpPaddling',    'paddle',    'water'),
('strava', 'Kitesurf',           'kitesurf',  'water'),
('strava', 'Windsurf',           'windsurf',  'water'),
('strava', 'Swim',               'swim',      'water'),
('garmin', 'open_water_swimming', 'swim',     'water'),
('garmin', 'lap_swimming',       'swim',      'water'),

-- ---------- sports collectifs et de raquette ----------
('strava', 'Badminton',          'badminton', 'sport_co'),
('strava', 'Soccer',             'football',  'sport_co'),
('strava', 'Tennis',             'tennis',    'sport_co'),
('strava', 'TableTennis',        'ping_pong', 'sport_co'),
('garmin', 'badminton',          'badminton', 'sport_co'),
('garmin', 'soccer',             'football',  'sport_co'),
('garmin', 'tennis',             'tennis',    'sport_co'),

-- ---------- respiration et événements ----------
('garmin', 'breathwork',         'breathwork',        'wellness'),
('garmin', 'incident_detected',  'incident_detected', 'other')
ON CONFLICT (source, source_value) DO UPDATE
   SET canonical_sport  = excluded.canonical_sport,
       canonical_family = excluded.canonical_family;
