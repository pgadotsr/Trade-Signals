from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
async def home():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Trade Signals</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                background-color: #f4f4f4;
                margin: 0;
                padding: 20px;
            }
            h1 {
                color: #333;
            }
        </style>
    </head>
    <body>
        <h1>Welcome to Trade Signals</h1>
        <p>Your API is running successfully.</p>
    </body>
    </html>
    """

