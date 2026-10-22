"""Configuration de l'application, lue depuis le fichier .env."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Parametres(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_user: str = "livraison"
    postgres_password: str = "changez-moi"
    postgres_db: str = "livraison"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    redis_url: str = "redis://localhost:6379/0"

    xmpp_host: str = "localhost"
    xmpp_port: int = 5222
    xmpp_domaine: str = "localhost"
    xmpp_mot_de_passe_agents: str = "changez-moi"

    osrm_url: str = "http://localhost:5000"

    api_host: str = "127.0.0.1"
    api_port: int = 8000
    jwt_secret: str = "changez-moi"

    # --- Ajouts du lot 1-2 ---
    # « sql » (PostgreSQL, fonctionnement normal) ou « memoire » (démonstration sans base).
    depot: str = "sql"
    # Mode développement : le code de connexion « SMS » est renvoyé dans la réponse de l'API
    # (aucun SMS réel n'est envoyé tant que le prestataire n'est pas choisi). À désactiver en
    # production.
    mode_dev: bool = True
    # Comptes créés au démarrage avec les rôles correspondants (numéros de téléphone).
    admin_telephone: str = "0700000000"
    repartiteur_telephone: str = "0700000001"
    # Délai de confirmation humaine (s) ; 30 s dans le cahier des charges.
    delai_confirmation_s: float = 30.0
    # F-34 : alerte puis réattribution quand le GPS d'un livreur en course se tait (secondes).
    gps_alerte_s: float = 60.0
    gps_reattribution_s: float = 180.0
    # Durée de validité d'un jeton de connexion (heures).
    duree_jeton_h: int = 12
    # Afficher dans le terminal chaque message ACL envoyé et reçu par les agents.
    trace_messages: bool = False

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    def jid(self, nom: str) -> str:
        """Identifiant XMPP d'un agent, par exemple dispatcher@localhost."""
        return f"{nom}@{self.xmpp_domaine}"


parametres = Parametres()
