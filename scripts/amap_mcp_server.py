from urban_mobility_rag.amap import create_mcp_server
from urban_mobility_rag.config import Settings


if __name__ == "__main__":
    settings = Settings.from_env()
    create_mcp_server(settings.amap_api_key).run(transport="stdio")

