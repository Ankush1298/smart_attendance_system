"""Run the structured MySQL API after database setup."""
import uvicorn
from backend.config.settings import settings

if __name__ == "__main__":
    uvicorn.run("backend.api.app:app", host=settings.api_host, port=settings.api_port, reload=False)
