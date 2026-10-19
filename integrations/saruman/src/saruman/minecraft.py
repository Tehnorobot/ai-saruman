"""The host-side UnionClef connection; no second game decision loop."""

import os

from py4j.java_gateway import CallbackServerParameters, GatewayParameters, JavaGateway


def connect(callback):
    return JavaGateway(
        gateway_parameters=GatewayParameters(
            address=os.getenv("MINECRAFT_GATEWAY_HOST", "127.0.0.1"),
            port=int(os.getenv("MINECRAFT_GATEWAY_PORT", "25333")),
            read_timeout=float(os.getenv("MINECRAFT_GATEWAY_TIMEOUT", "5")),
            auto_convert=True,
        ),
        callback_server_parameters=CallbackServerParameters(
            address=os.getenv("MINECRAFT_CALLBACK_HOST", "127.0.0.1"),
            port=int(os.getenv("MINECRAFT_CALLBACK_PORT", "25334")),
            daemonize=True,
            daemonize_connections=True,
        ),
        python_server_entry_point=callback,
        start_callback_server=True,
    )
