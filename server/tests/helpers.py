from fruitspy.config import (
    GameConfig,
    LimitConfig,
    RelayConfig,
    PortConfig,
    ServerConfig,
    TimeoutConfig,
)


def test_config() -> ServerConfig:
    return ServerConfig(
        bind_host="127.0.0.1",
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
            peerchat_handshake_seconds=15,
            server_browser_idle_seconds=30,
            rate_limit_entry_seconds=120,
        ),
        limits=LimitConfig(
            peerchat_line_bytes=4096,
            server_browser_frame_bytes=4096,
            qr_packet_bytes=4096,
            natneg_packet_bytes=512,
            peerchat_connections=256,
            server_browser_connections=128,
            connections_per_source=16,
            udp_packets_per_second=120,
            udp_burst=240,
            udp_tracked_sources=4096,
            reported_servers=2048,
            nat_sessions=4096,
        ),
        relay=RelayConfig(
            policy="auto",
            fallback_seconds=0.1,
            session_seconds=60,
            packet_bytes=4096,
            bytes_per_second=262144,
            byte_burst=524288,
            sessions=32,
        ),
    )
