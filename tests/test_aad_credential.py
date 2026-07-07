"""Unit tests for _build_aad_credential — the tenant-drift pin.

RTT's Azure resource lives in the DEV tenant, but the `az` CLI active context
often drifts to a CORP sub (e.g. msx-se-hub switching to az-corp). An unpinned
credential then mints a wrong-tenant token and the DEV resource returns HTTP 500
"Unable to get resource information" (a cross-tenant rejection masquerading as a
server error). Pinning RTT_AAD_SUBSCRIPTION makes minting drift-proof.

These tests verify the SELECTION logic (pin when set, fall back when not) with
the credential classes mocked — no network, no real Azure.

Run: python -m pytest tests/test_aad_credential.py -v
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import transcribe  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("RTT_AAD_SUBSCRIPTION", raising=False)
    yield


def test_pins_subscription_when_env_set(monkeypatch):
    """RTT_AAD_SUBSCRIPTION set → AzureCliCredential(subscription=<sub>)."""
    monkeypatch.setenv("RTT_AAD_SUBSCRIPTION", "dev-sub-guid")
    cli_cred = MagicMock(name="AzureCliCredential")
    monkeypatch.setattr(transcribe, "AzureCliCredential", cli_cred)
    default_cred = MagicMock(name="DefaultAzureCredential")
    monkeypatch.setattr(transcribe, "DefaultAzureCredential", default_cred)

    transcribe._build_aad_credential()

    cli_cred.assert_called_once_with(subscription="dev-sub-guid")
    default_cred.assert_not_called()  # pinned path must NOT fall through


def test_falls_back_to_default_when_env_unset(monkeypatch):
    """No RTT_AAD_SUBSCRIPTION → DefaultAzureCredential (prior behavior)."""
    cli_cred = MagicMock(name="AzureCliCredential")
    monkeypatch.setattr(transcribe, "AzureCliCredential", cli_cred)
    default_cred = MagicMock(name="DefaultAzureCredential")
    monkeypatch.setattr(transcribe, "DefaultAzureCredential", default_cred)

    transcribe._build_aad_credential()

    default_cred.assert_called_once_with()
    cli_cred.assert_not_called()


def test_blank_subscription_is_ignored(monkeypatch):
    """A whitespace/empty RTT_AAD_SUBSCRIPTION must NOT pin (treated as unset)."""
    monkeypatch.setenv("RTT_AAD_SUBSCRIPTION", "   ")
    cli_cred = MagicMock(name="AzureCliCredential")
    monkeypatch.setattr(transcribe, "AzureCliCredential", cli_cred)
    default_cred = MagicMock(name="DefaultAzureCredential")
    monkeypatch.setattr(transcribe, "DefaultAzureCredential", default_cred)

    transcribe._build_aad_credential()

    cli_cred.assert_not_called()
    default_cred.assert_called_once_with()


def test_falls_back_when_cli_credential_unavailable(monkeypatch):
    """Env set but AzureCliCredential import failed (None) → DefaultAzureCredential."""
    monkeypatch.setenv("RTT_AAD_SUBSCRIPTION", "dev-sub-guid")
    monkeypatch.setattr(transcribe, "AzureCliCredential", None)
    default_cred = MagicMock(name="DefaultAzureCredential")
    monkeypatch.setattr(transcribe, "DefaultAzureCredential", default_cred)

    transcribe._build_aad_credential()

    default_cred.assert_called_once_with()


def test_raises_when_no_credential_available(monkeypatch):
    """Neither credential class available → clear error, not an obscure crash."""
    monkeypatch.setattr(transcribe, "AzureCliCredential", None)
    monkeypatch.setattr(transcribe, "DefaultAzureCredential", None)

    with pytest.raises(RuntimeError, match="azure-identity"):
        transcribe._build_aad_credential()
