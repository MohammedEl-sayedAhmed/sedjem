"""Entry point of the desktop build (PyInstaller). The same executable also runs the model worker:
the app starts `Sedjem --transcribe <arguments>` for every local transcription. And an assistant starts
`Sedjem --mcp` to read the transcriptions over MCP (app/mcp.py), with nothing but its answers on stdout."""
import sys

if __name__ == "__main__":
    if sys.argv[1:2] == ["--transcribe"]:
        from app.worker import run
        run(sys.argv[2:])
    elif sys.argv[1:] == ["--mcp"]:
        from app.mcp import main as serve
        sys.exit(serve())
    else:
        from app.desktop import main
        main()
