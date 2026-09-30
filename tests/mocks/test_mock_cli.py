import pytest

from mock_apis import cli


@pytest.fixture
def served(monkeypatch):
    calls = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kw: calls.append((app, kw)))
    return calls


@pytest.mark.parametrize(("name", "port", "title"), [
    ("shopfront", 8001, "Shopfront (mock)"),
    ("ticketdesk", 8002, "Ticketdesk (mock)"),
    ("cartwheel", 8003, "Cartwheel (mock)"),
])  # fmt: skip
def test_serves_each_api_on_its_default_port(served, name, port, title):
    cli.main([name])
    ((app, kwargs),) = served
    assert app.title == title
    assert kwargs == {"host": "127.0.0.1", "port": port, "log_level": "info"}


def test_port_and_host_can_be_overridden(served):
    cli.main(["ticketdesk", "--port", "9000", "--host", "0.0.0.0"])
    assert served[0][1]["port"] == 9000
    assert served[0][1]["host"] == "0.0.0.0"


def test_unknown_api_is_rejected_with_choices(served, capsys):
    with pytest.raises(SystemExit):
        cli.main(["billing"])
    assert "shopfront" in capsys.readouterr().err
    assert served == []
