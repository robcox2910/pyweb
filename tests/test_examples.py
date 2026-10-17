"""Tests that run each example's ``main()`` and check a key line."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from pynet.loop import run
from pynet.streams import open_connection
from pynet.ws import connect_websocket

from pyweb import cli
from pyweb.aserver import AsyncServer
from pyweb.cli import demo_app
from pyweb.server import Server

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def load_example(name: str) -> ModuleType:
    """Import an example file by name (examples/ is not a package)."""
    spec = importlib.util.spec_from_file_location(f"example_{name}", EXAMPLES / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestExamples:
    """Run every example and look for its key line."""

    @pytest.mark.parametrize(
        ("name", "key_line"),
        [
            ("async_hello", "Hello, Sam!"),
            ("sse_clock", "tick 3"),
            ("ws_echo_page", "echo: hello"),
            ("login_demo", "Welcome back, ada"),
        ],
    )
    def test_example_prints_key_line(
        self, name: str, key_line: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Run the example's main() and find its key line in the output."""
        load_example(name).main()
        output = capsys.readouterr().out
        assert key_line in output
        assert "Traceback" not in output

    def test_login_demo_shows_lockout_and_roles(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Show the login demo covering a bad password, a lockout and a role check."""
        load_example("login_demo").main()
        output = capsys.readouterr().out
        assert "Wrong password" in output
        assert "Too many tries" in output
        assert "Only crew" in output


class TestAsyncDemo:
    """Test the app behind ``pyweb --async``."""

    def test_demo_serves_page_clock_and_echo(self) -> None:
        """Serve the page, one clock tick and a WebSocket echo."""

        async def scenario() -> tuple[bytes, bytes, str]:
            async with AsyncServer(demo_app(), access_log=False) as server:
                reader, writer = await open_connection("127.0.0.1", server.port)
                writer.write(b"GET / HTTP/1.1\r\nConnection: close\r\n\r\n")
                await writer.drain()
                page = await reader.read()
                writer.close()
                reader, writer = await open_connection("127.0.0.1", server.port)
                writer.write(b"GET /clock HTTP/1.1\r\n\r\n")
                await writer.drain()
                await reader.read_until(b"\r\n\r\n")
                size = int((await reader.read_until(b"\r\n"))[:-2], 16)
                tick = await reader.read_exactly(size)
                writer.close()
                ws = await connect_websocket("127.0.0.1", server.port, "/echo")
                await ws.send_text("hi")
                echoed = await ws.recv()
                return page, tick, str(echoed)

        page, tick, echoed = run(scenario())
        assert page.startswith(b"HTTP/1.1 200")
        assert b"PyWeb" in page
        assert tick.startswith(b"data:")
        assert echoed == "echo: hi"


class TestPlainCli:
    """Test that ``pyweb`` with no flag still starts the old threaded server."""

    def test_no_flag_builds_the_sync_server(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Build a ``pyweb.server.Server`` and stop cleanly on Ctrl-C."""
        started: list[object] = []

        def fake_serve_forever(self: Server) -> None:
            """Note the server instead of serving, then act like Ctrl-C."""
            started.append(self)
            raise KeyboardInterrupt

        monkeypatch.setattr(Server, "serve_forever", fake_serve_forever)
        cli.main([])
        output = capsys.readouterr().out
        assert len(started) == 1
        assert isinstance(started[0], Server)
        assert "PyWeb serving on http://" in output
        assert "Server stopped." in output
