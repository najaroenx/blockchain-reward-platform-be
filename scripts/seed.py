"""Seed a dedicated PostgreSQL database with the shared demo accounts."""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import psycopg
from argon2 import PasswordHasher, profiles
from psycopg import sql
from sqlalchemy import Connection, insert, select, update
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

from marketplace.auth.infrastructure.models import Customer, Merchant, MerchantAdmin
from marketplace.coupons.infrastructure.models import CouponTypeModel
from marketplace.infrastructure.database import Database
from marketplace.infrastructure.operation_history import advance_operation, record_operation_created
from marketplace.infrastructure.operation_models import Operation, Outbox
from marketplace.merchants.infrastructure.models import Wallet
from marketplace.point_types.infrastructure.models import PointType

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/accounts.json"
DATABASE_MARKER = "blockchain-reward-platform: BE-0006 demo seed"
CREATED_AT = datetime(2026, 1, 1, tzinfo=UTC)


def seed_merchant(connection: Connection, merchant: dict[str, Any], hasher: PasswordHasher) -> None:
    merchant_id = merchant["id"]
    admin = merchant["admin"]
    connection.execute(
        insert(Merchant).values(id=merchant_id, name=merchant["name"], created_at=CREATED_AT)
    )
    connection.execute(
        insert(MerchantAdmin).values(
            id=admin["id"],
            merchant_id=merchant_id,
            email=admin["email"],
            email_normalized=admin["email"],
            password_hash=hasher.hash(admin["password"]),
            display_name=merchant["name"],
            created_at=CREATED_AT,
        )
    )
    wallet = merchant["wallet"]
    if wallet is None:
        return

    connection.execute(
        insert(Wallet).values(
            id=wallet["id"],
            address=bytes.fromhex(wallet["address"].removeprefix("0x")),
            key_ref=wallet["keyRef"],
            owner_kind="MERCHANT",
            owner_id=merchant_id,
            created_at=CREATED_AT,
        )
    )
    operation_id = wallet["provisionOperationId"]
    connection.execute(
        insert(Operation).values(
            id=operation_id,
            kind="MERCHANT_PROVISION",
            actor_user_id=admin["id"],
            subject_id=merchant_id,
            status="PENDING",
            payload={"fixture": True},
            created_at=CREATED_AT,
        )
    )
    record_operation_created(connection, operation_id, CREATED_AT)
    advance_operation(connection, operation_id, "SUBMITTED")
    advance_operation(connection, operation_id, "CONFIRMED")
    advance_operation(connection, operation_id, "FINALIZED")
    connection.execute(
        update(Merchant)
        .where(Merchant.id == merchant_id)
        .values(status="ACTIVE", activated_at=CREATED_AT)
    )


def seed_point_type(connection: Connection, point: dict[str, Any]) -> None:
    connection.execute(
        insert(PointType).values(
            id=point["id"],
            merchant_id=point["merchantId"],
            name=point["name"],
            symbol=point["symbol"],
            decimals=18,
            created_at=CREATED_AT,
        )
    )
    operation_id = point["operationId"]
    connection.execute(
        insert(Operation).values(
            id=operation_id,
            kind="POINT_TYPE_CREATE",
            actor_user_id=point["adminId"],
            subject_id=point["id"],
            status="PENDING",
            payload={
                "pointTypeId": point["id"],
                "merchantId": point["merchantId"],
                "name": point["name"],
                "symbol": point["symbol"],
            },
            created_at=CREATED_AT,
        )
    )
    record_operation_created(connection, operation_id, CREATED_AT)
    connection.execute(
        insert(Outbox).values(
            id=point["outboxId"],
            operation_id=operation_id,
            event_type="point-type.create.v1",
            created_at=CREATED_AT,
        )
    )


def seed_rows(target: URL) -> bool:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    database = Database(target.render_as_string(hide_password=False))
    try:
        with database.engine.begin() as connection:
            seed_id = data["pointType"]["outboxId"]
            if connection.scalar(select(Outbox.id).where(Outbox.id == seed_id)) is not None:
                return False

            hasher = PasswordHasher.from_parameters(profiles.RFC_9106_LOW_MEMORY)
            for merchant in data["merchants"]:
                seed_merchant(connection, merchant, hasher)
            for customer in data["customers"]:
                connection.execute(
                    insert(Customer).values(
                        id=customer["id"],
                        tel_hash=sha256(customer["phone"].encode()).digest(),
                        created_at=CREATED_AT,
                    )
                )
            seed_point_type(connection, data["pointType"])
            coupon = data["couponDraft"]
            connection.execute(
                insert(CouponTypeModel).values(
                    id=coupon["id"],
                    name=coupon["name"],
                    start_date=coupon["startDate"],
                    expire_date=coupon["expireDate"],
                    face_value=coupon["faceValue"],
                    total_supply=coupon["totalSupply"],
                    created_at=CREATED_AT,
                )
            )
        return True
    finally:
        database.close()


def seed_database(admin: URL, name: str, *, reset: bool) -> None:
    target = admin.set(database=name, drivername="postgresql+psycopg")
    dsn = admin.set(drivername="postgresql").render_as_string(hide_password=False)
    identifier = sql.Identifier(name)
    with psycopg.connect(dsn, autocommit=True, connect_timeout=3) as connection:
        connection.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (name,))
        existing = connection.execute(
            "SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname = %s",
            (name,),
        ).fetchone()
        if existing is not None and existing[0] != DATABASE_MARKER:
            raise ValueError("The target database belongs to a different workflow")
        if existing is not None and reset:
            connection.execute(sql.SQL("DROP DATABASE {}").format(identifier))
        if existing is None or reset:
            connection.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(identifier))
            connection.execute(
                sql.SQL("COMMENT ON DATABASE {} IS {}").format(
                    identifier, sql.Literal(DATABASE_MARKER)
                )
            )
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=ROOT,
            env={
                **os.environ,
                "APP_ENV": "test",
                "DATABASE_URL": target.render_as_string(hide_password=False),
            },
            check=True,
        )
        created = seed_rows(target)
        action = "Reset and seeded" if reset else "Seeded" if created else "Already seeded"
        print(f"{action}: {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="reward_seed", help="Demo database name")
    parser.add_argument("--reset", action="store_true", help="Recreate the script-owned database")
    args = parser.parse_args()
    admin_url = os.environ.get("TEST_POSTGRES_URL")
    if not admin_url:
        parser.error("Set TEST_POSTGRES_URL to the PostgreSQL test admin connection")
    try:
        admin = make_url(admin_url)
    except ArgumentError:
        parser.error("TEST_POSTGRES_URL must be a PostgreSQL database URL")
    if (
        admin.drivername not in {"postgresql", "postgresql+psycopg"}
        or not admin.host
        or not admin.database
    ):
        parser.error("TEST_POSTGRES_URL needs a PostgreSQL host and database")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", args.database) or admin.database == args.database:
        parser.error("Choose a PostgreSQL identifier distinct from the test admin database")
    try:
        seed_database(admin, args.database, reset=args.reset)
    except (ValueError, psycopg.Error) as error:
        parser.exit(1, f"Seed failed: {error}\n")
    except subprocess.CalledProcessError:
        parser.exit(1, "Seed failed: Alembic could not upgrade the demo database\n")


if __name__ == "__main__":
    main()
