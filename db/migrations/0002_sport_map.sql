-- =====================================================================
-- Cairn — correspondance des libellés de sport
--
-- Garmin renvoie activityType.typeKey ; Strava renvoie sport_type
-- (champ récent, plus précis que l'ancien `type`).
-- Toute valeur absente de cette table tombe en 'other' / 'other' avec un
-- WARN dans les logs de sync : il suffit d'ajouter une ligne ici, pas de
-- toucher au code.
--
-- Note enduro : Garmin n'a pas de profil "enduro" distinct. Les sorties
-- partent en mountain_biking (montée + descente) ou downhill_biking
-- (navette / bike park). On mappe downhill_biking -> enduro et on
-- reclassera à la main si besoin via activity.sport.
-- =====================================================================

INSERT INTO sport_map (source, source_value, canonical_sport, canonical_family) VALUES
-- ---------- Garmin : vélo ----------
('garmin', 'mountain_biking',        'mtb',        'cycling'),
('garmin', 'downhill_biking',        'enduro',     'cycling'),
('garmin', 'cyclocross',             'gravel',     'cycling'),
('garmin', 'gravel_cycling',         'gravel',     'cycling'),
('garmin', 'road_biking',            'road',       'cycling'),
('garmin', 'cycling',                'road',       'cycling'),
('garmin', 'virtual_ride',           'road',       'cycling'),
('garmin', 'indoor_cycling',         'road',       'cycling'),
('garmin', 'e_bike_mountain',        'mtb',        'cycling'),
('garmin', 'e_bike_fitness',         'road',       'cycling'),
-- ---------- Garmin : course ----------
('garmin', 'trail_running',          'trail_run',  'running'),
('garmin', 'running',                'run',        'running'),
('garmin', 'street_running',         'run',        'running'),
('garmin', 'track_running',          'run',        'running'),
('garmin', 'treadmill_running',      'run',        'running'),
('garmin', 'ultra_run',              'trail_run',  'running'),
-- ---------- Garmin : reste ----------
('garmin', 'hiking',                 'hike',       'other'),
('garmin', 'walking',                'walk',       'other'),
('garmin', 'strength_training',      'strength',   'other'),
('garmin', 'indoor_cardio',          'cardio',     'other'),
('garmin', 'yoga',                   'mobility',   'other'),
('garmin', 'lap_swimming',           'swim',       'other'),
('garmin', 'open_water_swimming',    'swim',       'other'),
('garmin', 'resort_skiing_snowboarding_ws', 'ski', 'other'),
('garmin', 'backcountry_skiing',     'ski',        'other'),

-- ---------- Strava : vélo ----------
('strava', 'MountainBikeRide',       'mtb',        'cycling'),
('strava', 'EMountainBikeRide',      'mtb',        'cycling'),
('strava', 'GravelRide',             'gravel',     'cycling'),
('strava', 'Ride',                   'road',       'cycling'),
('strava', 'EBikeRide',              'road',       'cycling'),
('strava', 'VirtualRide',            'road',       'cycling'),
('strava', 'Velomobile',             'road',       'cycling'),
('strava', 'Handcycle',              'other',      'other'),
-- ---------- Strava : course ----------
('strava', 'TrailRun',               'trail_run',  'running'),
('strava', 'Run',                    'run',        'running'),
('strava', 'VirtualRun',             'run',        'running'),
-- ---------- Strava : reste ----------
('strava', 'Hike',                   'hike',       'other'),
('strava', 'Walk',                   'walk',       'other'),
('strava', 'WeightTraining',         'strength',   'other'),
('strava', 'Crossfit',               'strength',   'other'),
('strava', 'HighIntensityIntervalTraining', 'cardio', 'other'),
('strava', 'Workout',                'cardio',     'other'),
('strava', 'Elliptical',             'cardio',     'other'),
('strava', 'StairStepper',           'cardio',     'other'),
('strava', 'Yoga',                   'mobility',   'other'),
('strava', 'Pilates',                'mobility',   'other'),
('strava', 'Swim',                   'swim',       'other'),
('strava', 'AlpineSki',              'ski',        'other'),
('strava', 'BackcountrySki',         'ski',        'other'),
('strava', 'NordicSki',              'ski',        'other'),
('strava', 'Snowboard',              'ski',        'other'),
('strava', 'Snowshoe',               'hike',       'other'),
('strava', 'RockClimbing',           'other',      'other'),
('strava', 'Rowing',                 'cardio',     'other')
ON CONFLICT (source, source_value) DO UPDATE
   SET canonical_sport  = EXCLUDED.canonical_sport,
       canonical_family = EXCLUDED.canonical_family;
