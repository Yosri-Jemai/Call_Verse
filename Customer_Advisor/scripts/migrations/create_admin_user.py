"""Crée un compte admin (mot de passe saisi en mode masqué).

Usage : python scripts/create_admin_user.py <username> <approver|viewer>
"""
import argparse
import getpass
import os
import sys

from argon2 import PasswordHasher
from dotenv import load_dotenv

try:
    import psycopg as pg          # psycopg 3
except ImportError:
    import psycopg2 as pg         # psycopg 2

MIN_LEN = 4
DB_NAME = "cashy_banking"         # même base que config.py (à aligner sur la config)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("username")
    parser.add_argument("role", choices=["approver", "viewer"])
    args = parser.parse_args()

    username = args.username.strip()
    if not username:
        sys.exit("Nom d'utilisateur vide.")

    password = getpass.getpass("Mot de passe : ")
    if password != getpass.getpass("Confirmer : "):
        sys.exit("Les mots de passe ne correspondent pas.")
    if len(password) < MIN_LEN:
        sys.exit(f"Mot de passe trop court (minimum {MIN_LEN} caractères).")

    password_hash = PasswordHasher().hash(password)

    load_dotenv()
    conn = pg.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=DB_NAME,
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO admin_users (username, password_hash, role) "
                    "VALUES (%s, %s, %s)",
                    (username, password_hash, args.role),
                )
        print(f"Compte '{username}' créé avec le rôle '{args.role}'.")
    except pg.errors.UniqueViolation:
        sys.exit(f"Le nom d'utilisateur '{username}' existe déjà.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()