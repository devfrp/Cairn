-- =====================================================================
-- 0004 — Écarter une sortie des statistiques sans la supprimer
--
-- Un enregistrement peut être faux sans être inutile : la course relevée
-- à 93,4 km/h vient d'un décrochage GPS d'une seconde, mais la sortie a
-- bien eu lieu. La supprimer perdrait la donnée brute et la
-- resynchronisation la ramènerait ; un garde-fou par métrique ne couvre
-- que la métrique qu'on a prévue.
--
-- D'où un drapeau porté par l'activité : elle reste visible dans la
-- liste, avec sa raison, et sort des agrégats.
-- =====================================================================

ALTER TABLE activity ADD COLUMN excluded INTEGER NOT NULL DEFAULT 0;
ALTER TABLE activity ADD COLUMN excluded_reason TEXT;

-- Les agrégats filtrent tous sur cette colonne : sans index, chacun
-- relirait la table entière pour écarter une poignée de lignes.
CREATE INDEX idx_activity_excluded ON activity (excluded) WHERE excluded = 1;

-- Les agrégats lisent cette vue plutôt que la table. Éparpiller un
-- « AND excluded = 0 » dans quarante-cinq requêtes, c'est en oublier une :
-- le filtre est écrit ici, une fois.
--
-- La liste des sorties, elle, continue de lire la table : une sortie
-- écartée doit rester visible, avec sa raison, sans quoi on ne pourrait
-- plus revenir sur la décision.
CREATE VIEW activity_kept AS SELECT * FROM activity WHERE excluded = 0;
