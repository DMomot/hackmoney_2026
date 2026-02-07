from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from adapters import polymarket

app = FastAPI()

app.mount("/public", StaticFiles(directory="public"), name="public")
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def root():
    return FileResponse("static/index.html")

@app.get("/market")
async def market():
    return FileResponse("static/market.html")

@app.get("/api/orderbook")
async def orderbook(
    event_id: str = Query(...),
    team: str = Query(...),
    side: str = Query("yes"),
):
    result = await polymarket.get_orderbook(event_id, team, side)
    return result
