import subprocess
import os
import shlex
import logging
import urllib.parse

from fastapi import FastAPI, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel


class ExecuteRequest(BaseModel):
    command: str


class ExecuteResponse(BaseModel):
    stdout: str
    stderr: str
    exit_code: int


def get_safe_path(file_path: str) -> str:
    """Sanitizes the file path to ensure it stays within /app."""
    base_dir = os.path.realpath("/app")
    clean_path = file_path.lstrip("/")
    full_path = os.path.realpath(os.path.join(base_dir, clean_path))

    if os.path.commonpath([base_dir, full_path]) != base_dir:
        raise ValueError("Access denied: Path must be within /app")

    return full_path


app = FastAPI(title="Sandbox Runtime", version="1.0.0")


@app.get("/")
async def health_check():
    return {"status": "ok"}


@app.post("/execute", response_model=ExecuteResponse)
async def execute_command(request: ExecuteRequest):
    try:
        args = shlex.split(request.command)
        process = subprocess.run(args, capture_output=True, text=True, cwd="/app")
        return ExecuteResponse(
            stdout=process.stdout,
            stderr=process.stderr,
            exit_code=process.returncode,
        )
    except Exception as e:
        return ExecuteResponse(stdout="", stderr=f"Failed: {e}", exit_code=1)


@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    try:
        file_path = os.path.join("/app", file.filename)
        with open(file_path, "wb") as f:
            f.write(await file.read())
        return JSONResponse(
            status_code=200,
            content={"message": f"File '{file.filename}' uploaded successfully."},
        )
    except Exception as e:
        logging.exception("Upload failed.")
        return JSONResponse(status_code=500, content={"message": f"Upload failed: {e}"})


@app.get("/download/{encoded_file_path:path}")
async def download_file(encoded_file_path: str):
    decoded_path = urllib.parse.unquote(encoded_file_path)
    try:
        full_path = get_safe_path(decoded_path)
    except ValueError:
        return JSONResponse(status_code=403, content={"message": "Access denied"})

    if os.path.isfile(full_path):
        return FileResponse(
            path=full_path,
            media_type="application/octet-stream",
            filename=decoded_path,
        )
    return JSONResponse(status_code=404, content={"message": "File not found"})


@app.get("/list/{encoded_file_path:path}")
async def list_files(encoded_file_path: str):
    decoded_path = urllib.parse.unquote(encoded_file_path)
    try:
        full_path = get_safe_path(decoded_path)
    except ValueError:
        return JSONResponse(status_code=403, content={"message": "Access denied"})

    if not os.path.isdir(full_path):
        return JSONResponse(
            status_code=404, content={"message": "Path is not a directory"}
        )

    entries = []
    with os.scandir(full_path) as it:
        for entry in it:
            stats = entry.stat()
            entries.append(
                {
                    "name": entry.name,
                    "size": stats.st_size,
                    "type": "directory" if entry.is_dir() else "file",
                    "mod_time": stats.st_mtime,
                }
            )
    return JSONResponse(status_code=200, content=entries)


@app.get("/exists/{encoded_file_path:path}")
async def exists(encoded_file_path: str):
    decoded_path = urllib.parse.unquote(encoded_file_path)
    try:
        full_path = get_safe_path(decoded_path)
    except ValueError:
        return JSONResponse(status_code=403, content={"message": "Access denied"})

    return JSONResponse(
        status_code=200,
        content={"path": decoded_path, "exists": os.path.exists(full_path)},
    )
