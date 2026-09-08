import base64
import os
import unittest
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.offline_access import install


def decode_urlsafe(value):
    return base64.urlsafe_b64decode(value + "=" * ((4 - len(value) % 4) % 4))


class OfflineAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private_key = ec.generate_private_key(ec.SECP256R1())
        pem = cls.private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        os.environ["CAULDRA_OFFLINE_SIGNING_KEY"] = pem
        os.environ["CAULDRA_OFFLINE_DAYS"] = "7"

        cls.Base = declarative_base()

        class Business(cls.Base):
            __tablename__ = "business_profile"
            id = Column(Integer, primary_key=True)

        class User(cls.Base):
            __tablename__ = "users"
            id = Column(Integer, primary_key=True)
            business_id = Column(Integer, ForeignKey("business_profile.id"), nullable=False)
            role = Column(String(40), nullable=False)
            auth_version = Column(Integer, nullable=False, default=1)
            disabled = Column(Boolean, nullable=False, default=False)

        cls.User = User
        cls.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        cls.Base.metadata.create_all(cls.engine)
        cls.Session = sessionmaker(bind=cls.engine)
        cls.app = FastAPI()
        cls.session = cls.Session()
        cls.session.add(Business(id=1))
        cls.user = User(id=10, business_id=1, role="admin", auth_version=1, disabled=False)
        cls.session.add(cls.user)
        cls.session.commit()

        def get_db():
            yield cls.session

        def get_user():
            return cls.user

        cls.g = {
            "Base": cls.Base, "app": cls.app, "get_db": get_db,
            "get_current_user": get_user, "get_authenticated_user": get_user,
            "get_effective_permissions": lambda user: {"sales.create": user.role == "admin"},
        }
        cls.OfflineDevice = install(cls.g)
        cls.Base.metadata.create_all(cls.engine)
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.session.close()
        cls.engine.dispose()
        os.environ.pop("CAULDRA_OFFLINE_SIGNING_KEY", None)

    def setUp(self):
        self.user.role = "admin"
        self.user.auth_version = 1
        self.session.query(self.OfflineDevice).delete()
        self.session.commit()

    def provision(self):
        device_id = str(uuid.uuid4())
        response = self.client.post("/offline/provision", json={"device_id": device_id})
        self.assertEqual(response.status_code, 200, response.text)
        return device_id, response.json()

    def test_provision_returns_verifiable_bounded_grant(self):
        device_id, grant = self.provision()
        raw_signature = decode_urlsafe(grant["signature"])
        der_signature = encode_dss_signature(int.from_bytes(raw_signature[:32], "big"), int.from_bytes(raw_signature[32:], "big"))
        self.private_key.public_key().verify(der_signature, grant["payload"].encode(), ec.ECDSA(hashes.SHA256()))
        payload = __import__("json").loads(decode_urlsafe(grant["payload"]))
        self.assertEqual(payload["device_id"], device_id)
        self.assertEqual(payload["business_id"], 1)
        self.assertEqual(payload["user_id"], 10)
        self.assertGreater(payload["expires_at"], payload["issued_at"])
        self.assertLessEqual(payload["expires_at"] - payload["issued_at"], 7 * 86400)

    def test_replay_stops_when_role_snapshot_changes(self):
        device_id, _ = self.provision()
        self.user.role = "staff"
        self.session.commit()
        response = self.client.post("/offline/replay", json={
            "schema_version": 2, "op_id": str(uuid.uuid4()), "device_id": device_id,
            "user_id": 10, "business_id": 1, "auth_version": 1,
            "type": "sale_checkout", "captured_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), "payload": {},
        })
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"]["code"], "PERMISSION_CHANGED")

    def test_revoke_prevents_replay_without_deleting_local_work(self):
        device_id, _ = self.provision()
        response = self.client.delete(f"/offline/devices/{device_id}")
        self.assertEqual(response.status_code, 200)
        replay = self.client.post("/offline/replay", json={
            "schema_version": 2, "op_id": str(uuid.uuid4()), "device_id": device_id,
            "user_id": 10, "business_id": 1, "auth_version": 1,
            "type": "sale_checkout", "captured_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), "payload": {},
        })
        self.assertEqual(replay.status_code, 403)
        self.assertEqual(replay.json()["detail"]["code"], "AUTH_EXPIRED")


if __name__ == "__main__":
    unittest.main()
