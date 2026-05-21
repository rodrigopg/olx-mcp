"""Entry point legado. Re-exporta tudo de olx_mcp.server."""

from olx_mcp.server import *  # noqa: F401,F403
from olx_mcp.server import main


if __name__ == "__main__":
    main()
