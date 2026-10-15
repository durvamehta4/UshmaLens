import uvicorn

from ushmalens import config as C

if __name__ == "__main__":
    uvicorn.run("ushmalens.api:app", host=C.HOST, port=C.PORT, log_level="info")
