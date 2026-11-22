from src import main


def test_check_environment_uses_settings_field_names(monkeypatch):
    monkeypatch.setattr(main.settings, "gemini_api_key", "test-key")

    assert main.check_environment(["GEMINI_API_KEY"]) is True


def test_check_environment_reports_missing_environment_name(monkeypatch, capsys):
    monkeypatch.setattr(main.settings, "gemini_api_key", "")

    assert main.check_environment(["GEMINI_API_KEY"]) is False
    assert "GEMINI_API_KEY" in capsys.readouterr().out
