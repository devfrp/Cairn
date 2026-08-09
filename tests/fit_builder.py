"""Encodeur FIT minimal, pour tester le parser sur un vrai fichier binaire.

Cairn ne produit jamais de .fit — cet encodeur n'existe que pour les tests.
Il implémente juste ce que le format exige pour être lisible : en-tête,
messages de définition, messages de données et CRC.

Références de champs (profil FIT global) :
  record  = message 20, session = message 18
  timestamp = champ 253, exprimé en secondes depuis le 31/12/1989 UTC
"""

from __future__ import annotations

import datetime as dt
import struct

FIT_EPOCH = dt.datetime(1989, 12, 31, tzinfo=dt.UTC)

MESG_RECORD = 20
MESG_SESSION = 18

# base type, taille en octets
UINT8 = (0x02, 1)
UINT16 = (0x84, 2)
UINT32 = (0x86, 4)
SINT32 = (0x85, 4)

CRC_TABLE = (
    0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
    0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400,
)


def crc16(data: bytes, crc: int = 0) -> int:
    for byte in data:
        tmp = CRC_TABLE[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ CRC_TABLE[byte & 0xF]

        tmp = CRC_TABLE[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ CRC_TABLE[(byte >> 4) & 0xF]
    return crc


def to_fit_timestamp(moment: dt.datetime) -> int:
    return int((moment - FIT_EPOCH).total_seconds())


def to_semicircles(degrees: float) -> int:
    return int(degrees * (2**31) / 180.0)


_PACK = {UINT8: "B", UINT16: "H", UINT32: "I", SINT32: "i"}


def _definition(local_type: int, global_num: int, fields: list[tuple[int, tuple]]) -> bytes:
    out = bytes([0x40 | local_type, 0x00, 0x00])
    out += struct.pack("<H", global_num)
    out += bytes([len(fields)])
    for field_num, (base_type, size) in fields:
        out += bytes([field_num, size, base_type])
    return out


def _data(local_type: int, fields: list[tuple[int, tuple]], values: list[int]) -> bytes:
    out = bytes([local_type])
    for (_, base), value in zip(fields, values, strict=True):
        out += struct.pack("<" + _PACK[base], value)
    return out


RECORD_FIELDS = [
    (253, UINT32),  # timestamp
    (0, SINT32),    # position_lat, en semicircles
    (1, SINT32),    # position_long
    (2, UINT16),    # altitude : (m + 500) * 5
    (3, UINT8),     # heart_rate
    (4, UINT8),     # cadence
    (5, UINT32),    # distance : m * 100
    (6, UINT16),    # speed : m/s * 1000
]

SESSION_FIELDS = [
    (253, UINT32),  # timestamp
    (2, UINT32),    # start_time
    (7, UINT32),    # total_elapsed_time : s * 1000
    (9, UINT32),    # total_distance : m * 100
    (5, UINT8),     # sport
]


def build_fit(
    start: dt.datetime,
    samples: list[dict],
    *,
    sport: int = 2,  # 2 = cycling dans le profil FIT
    with_session: bool = True,
) -> bytes:
    """Assemble un .fit à partir d'une liste de points.

    Chaque point accepte : offset (s), hr, cadence, lat, lon, altitude,
    distance, speed.
    """
    body = _definition(0, MESG_RECORD, RECORD_FIELDS)

    for sample in samples:
        moment = start + dt.timedelta(seconds=sample.get("offset", 0))
        body += _data(
            0,
            RECORD_FIELDS,
            [
                to_fit_timestamp(moment),
                to_semicircles(sample.get("lat", 44.5)),
                to_semicircles(sample.get("lon", 6.5)),
                int((sample.get("altitude", 1200.0) + 500) * 5),
                sample.get("hr", 150),
                sample.get("cadence", 85),
                int(sample.get("distance", 0.0) * 100),
                int(sample.get("speed", 5.0) * 1000),
            ],
        )

    if with_session:
        elapsed = samples[-1].get("offset", 0) if samples else 0
        distance = samples[-1].get("distance", 0.0) if samples else 0.0
        body += _definition(1, MESG_SESSION, SESSION_FIELDS)
        body += _data(
            1,
            SESSION_FIELDS,
            [
                to_fit_timestamp(start + dt.timedelta(seconds=elapsed)),
                to_fit_timestamp(start),
                int(elapsed * 1000),
                int(distance * 100),
                sport,
            ],
        )

    header = struct.pack("<BBHI4s", 14, 0x20, 2100, len(body), b".FIT")
    header += struct.pack("<H", crc16(header))

    return header + body + struct.pack("<H", crc16(header + body))
