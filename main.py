import uvicorn

if __name__ == "__main__":
    try:
        uvicorn.run("server.api:app", host="0.0.0.0", port=8001, reload=True)
    except Exception as e:
        print(e)
        print("Failed to start server. Is Redis running?")

