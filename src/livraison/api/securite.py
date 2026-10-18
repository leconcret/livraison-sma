"""Authentification par téléphone et code à usage unique (F-01), rôles (F-02), jetons (NF-05).

1. POST /auth/code   : un code à 6 chiffres est « envoyé par SMS » (simulé : en mode
   développement, il est renvoyé dans la réponse et publié dans le journal).
2. POST /auth/jeton  : téléphone + code -> jeton JWT signé, valable quelques heures.
Les codes sont stockés hachés, expirent après 5 minutes et n'acceptent que 5 essais
(limitation des tentatives, NF-05).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from livraison.config import parametres

ROLES = ("client", "livreur", "repartiteur", "admin")
DUREE_CODE_S = 300
ESSAIS_MAX = 5
_codes: dict[str, dict] = {}
_revoques: dict[str, float] = {}  # identifiant de jeton (jti) -> expiration
_schema = HTTPBearer(auto_error=False)


def _hacher(code: str, telephone: str) -> str:
    return hashlib.sha256(f"{telephone}:{code}:{parametres.jwt_secret}".encode()).hexdigest()


def emettre_code(telephone: str) -> str:
    code = f"{secrets.randbelow(1_000_000):06d}"
    _codes[telephone] = {"h": _hacher(code, telephone), "exp": time.time() + DUREE_CODE_S, "essais": 0}
    return code


def verifier_code(telephone: str, code: str) -> bool:
    e = _codes.get(telephone)
    if not e or e["exp"] < time.time():
        return False
    e["essais"] += 1
    if e["essais"] > ESSAIS_MAX:
        _codes.pop(telephone, None)
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "trop de tentatives, redemandez un code")
    if hmac.compare_digest(e["h"], _hacher(code, telephone)):
        _codes.pop(telephone, None)
        return True
    return False


def creer_jeton(telephone: str, role: str, livreur_id: int | None = None) -> str:
    charge = {"sub": telephone, "role": role, "jti": secrets.token_hex(8),
              "exp": datetime.now(UTC) + timedelta(hours=parametres.duree_jeton_h)}
    if livreur_id is not None:
        charge["livreur_id"] = livreur_id
    return jwt.encode(charge, parametres.jwt_secret, algorithm="HS256")


def lire_jeton(jeton: str) -> dict:
    try:
        u = jwt.decode(jeton, parametres.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "jeton invalide ou expiré") from e
    if u.get("jti") in _revoques:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session fermée : reconnectez-vous")
    return u


def revoquer(u: dict) -> None:
    """Déconnexion : le jeton est inscrit sur la liste des jetons révoqués jusqu'à son expiration."""
    maintenant = time.time()
    for jti, exp in list(_revoques.items()):
        if exp < maintenant:
            _revoques.pop(jti, None)
    if u.get("jti"):
        _revoques[u["jti"]] = float(u.get("exp", maintenant + 86400))


async def utilisateur_courant(cred: HTTPAuthorizationCredentials | None = Depends(_schema)) -> dict:
    if cred is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentification requise")
    return lire_jeton(cred.credentials)


async def utilisateur_entete_ou_lien(cred: HTTPAuthorizationCredentials | None = Depends(_schema),
                                     jeton: str | None = None) -> dict:
    """Comme utilisateur_courant, mais accepte aussi ?jeton=... (lien vers un PDF à ouvrir)."""
    if cred is not None:
        return lire_jeton(cred.credentials)
    if jeton:
        return lire_jeton(jeton)
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentification requise")


def exiger(*roles: str):
    """Dépendance FastAPI : l'utilisateur doit avoir l'un des rôles indiqués (F-02)."""

    async def verifier(u: dict = Depends(utilisateur_courant)) -> dict:
        if u.get("role") not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"réservé aux rôles : {', '.join(roles)}")
        return u

    return verifier
