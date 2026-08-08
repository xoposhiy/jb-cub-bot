from jbcub_bot.main import build_dispatcher


def test_build_dispatcher_registers_the_directory_feature():
    # A migrated feature exports no router, so what proves it was mounted is
    # its slot in the registry the entry points read.
    dp = build_dispatcher(session_factory=lambda: None)
    assert "directory" in {reg.name for reg in dp["registry"].features()}
