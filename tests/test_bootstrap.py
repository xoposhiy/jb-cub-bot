from sdt_bot.main import build_dispatcher


def test_build_dispatcher_registers_directory_router():
    dp = build_dispatcher(session_factory=lambda: None)
    names = [r.name for r in dp.sub_routers]
    assert "directory" in names
