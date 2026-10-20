"""Resolved runtime settings, without capture, credentials or transcript writes."""
from pathlib import Path
import sys

import pytest
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import transcribe


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in list(transcribe.os.environ):
        if name.startswith(('RTT_', 'AZURE_SPEECH_')):
            monkeypatch.delenv(name)
    def forbidden(*args, **kwargs):
        pytest.fail('Config inspection must not start capture, auth, hotkeys or output')
    for name in ('resolve_device', '_build_aad_credential', 'TranscriptBuffer', 'ClipboardHotkeyListener'):
        monkeypatch.setattr(transcribe, name, forbidden)
    return monkeypatch, tmp_path


def test_openai_resolved_config_and_no_side_effects(isolated):
    monkeypatch, tmp_path = isolated
    monkeypatch.setenv('RTT_OPENAI_ENDPOINT', 'https://example.test/')
    monkeypatch.setenv('RTT_AAD_SUBSCRIPTION', 'test-subscription')
    result = CliRunner().invoke(transcribe.app, ['--backend', 'openai', '--show-config', '--chunk-seconds', '8', '--language', 'he', '--include-mic'])
    assert result.exit_code == 0, result.output
    for text in ('RTT configuration', 'backend: openai', 'gpt-4o-transcribe-diarize', 'chunk_seconds: 8.0', 'language: he', 'temperature: 0.0', 'include_mic: True', 'Azure AD', 'test-subscription', 'https://example.test/'):
        assert text in result.output
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('backend,expected', [('local', 'model: base'), ('azure', 'language: en-US'), ('llmspeech', 'locales: auto'), ('mai', 'model: MAI-Transcribe-2')])
def test_all_backend_summaries(isolated, backend, expected):
    result = CliRunner().invoke(transcribe.app, ['--backend', backend, '--show-config'])
    assert result.exit_code == 0, result.output
    assert expected in result.output


def test_cli_endpoint_wins_and_secrets_are_not_printed(isolated):
    monkeypatch, _ = isolated
    monkeypatch.setenv('RTT_OPENAI_ENDPOINT', 'https://env.test/')
    monkeypatch.setenv('AZURE_SPEECH_KEY', 'environment-secret')
    result = CliRunner().invoke(transcribe.app, ['--backend', 'openai', '--show-config', '--azure-key', 'cli-secret', '--openai-endpoint', 'https://user:password@cli.test/?api-key=query-secret'])
    assert result.exit_code == 0, result.output
    assert 'https://cli.test/' in result.output
    assert 'API key' in result.output
    for secret in ('environment-secret', 'cli-secret', 'password', 'query-secret', 'env.test'):
        assert secret not in result.output


def test_normal_startup_prints_same_summary_before_output_creation(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    class StopStartup(Exception):
        pass
    def stop(*args, **kwargs):
        raise StopStartup()
    monkeypatch.setattr(transcribe, 'TranscriptBuffer', stop)
    result = CliRunner().invoke(transcribe.app, ['--backend', 'openai'])
    assert isinstance(result.exception, StopStartup)
    assert 'RTT configuration' in result.output
    assert 'backend: openai' in result.output
