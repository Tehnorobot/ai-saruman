"""Serve the gateway on a shared Unix socket, without publishing a TCP port."""
import os
from pathlib import Path

import uvicorn


def main():
    socket = Path(os.environ.get("DSLAB_GATEWAY_SOCKET", "/run/lab-provider/gateway.sock"))
    socket.parent.mkdir(parents=True, exist_ok=True)
    # Shared socket grants transport access, not authorization. Each endpoint still
    # requires its own scoped task capability or trusted controller credential.
    os.umask(0o111)
    socket.unlink(missing_ok=True)
    uvicorn.run("provider_gateway.main:app", uds=str(socket), access_log=False,
                log_level="warning", server_header=False)


if __name__ == "__main__":
    main()
