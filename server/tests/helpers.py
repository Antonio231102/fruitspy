from fruitspy.config import GameConfig, PortConfig, ServerConfig, TimeoutConfig


def test_config() -> ServerConfig:
    return ServerConfig(
        bind_host="127.0.0.1",
        advertise_host="127.0.0.1",
        game=GameConfig(
            name="FruitNinjaand",
            secret_key="nNfhSl",
            default_query_port=6500,
            server_browser_version=1,
            max_players=2,
        ),
        ports=PortConfig(
            availability_qr_udp=27900,
            peerchat_tcp=6667,
            server_browser_tcp=28910,
            natneg_udp=27901,
        ),
        timeouts=TimeoutConfig(
            reported_server_seconds=120,
            nat_session_seconds=60,
        ),
    )
