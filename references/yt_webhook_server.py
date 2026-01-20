#!/usr/bin/env python3
"""
YouTube Video Webhook Server
Recebe URLs via POST e processa usando a skill do Claude Code.

Usage:
    python3 server.py [--port 8765]

Deploy no Mac Mini:
    cd ~/projects/_scripts/yt-webhook
    nohup python3 server.py > webhook.log 2>&1 &
"""

from __future__ import annotations
import subprocess
import json
import re
import os
import sys
from datetime import datetime
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
import threading
from typing import Optional, Dict, Any

# Configuração
PORT = int(os.environ.get("YT_WEBHOOK_PORT", 8765))
PROCESSOR_SCRIPT = os.path.expanduser("~/.claude/skills/youtube-video/scripts/youtube_processor.py")
OUTPUT_DIR = os.path.expanduser("~/projects/notes/youtube")
NOTIFY_CMD = os.path.expanduser("~/projects/_scripts/notify")  # Script de notificação Telegram

# Regex para extrair video ID do YouTube
YT_REGEX = re.compile(
    r'(?:youtube\.com/(?:watch\?v=|embed/|v/|shorts/)|youtu\.be/)([a-zA-Z0-9_-]{11})'
)


def extract_video_id(url: str) -> Optional[str]:
    """Extrai o video ID de uma URL do YouTube."""
    match = YT_REGEX.search(url)
    return match.group(1) if match else None


def notify(message: str, msg_type: str = "info"):
    """Envia notificação via Telegram."""
    try:
        if os.path.exists(NOTIFY_CMD):
            subprocess.run([NOTIFY_CMD, message, msg_type], timeout=10)
    except Exception as e:
        print(f"[notify] Erro: {e}")


def process_video(url: str, options: dict) -> dict:
    """
    Processa um vídeo do YouTube.

    Args:
        url: URL do YouTube
        options: Opções de processamento
            - note: bool - Gerar nota Obsidian
            - clip: str - Trecho específico (ex: "10:00-15:00")

    Returns:
        dict com status, message e output_path
    """
    video_id = extract_video_id(url)
    if not video_id:
        return {"status": "error", "message": "URL inválida do YouTube"}

    # Prepara comando
    cmd = ["python3", PROCESSOR_SCRIPT, url]

    if options.get("note", True):  # Default: gerar nota
        cmd.append("--note")

    if options.get("clip"):
        cmd.extend(["--clip", options["clip"]])

    # Garante que o diretório de output existe
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

    # Configura environment com API key do 1Password
    env = os.environ.copy()
    # GOOGLE_API_KEY deve estar no ambiente (configurada no launchd plist)
    if "GOOGLE_API_KEY" not in env:
        print(f"[ERRO] GOOGLE_API_KEY não está no ambiente!")

    # Executa
    print(f"[process] Executando: {' '.join(cmd)}")
    notify(f"Processando vídeo: {video_id}", "wait")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minutos max
            env=env,
            cwd=OUTPUT_DIR
        )

        if result.returncode == 0:
            # Tenta encontrar o arquivo gerado
            output_files = list(Path(OUTPUT_DIR).glob(f"*{video_id}*.md"))
            output_path = str(output_files[0]) if output_files else None

            notify(f"Video processado: {video_id}", "done")
            return {
                "status": "success",
                "message": "Video processado com sucesso",
                "output_path": output_path,
                "stdout": result.stdout[-500:] if result.stdout else None  # Últimos 500 chars
            }
        else:
            notify(f"Erro processando {video_id}", "error")
            return {
                "status": "error",
                "message": f"Erro no processamento: {result.stderr[-500:]}"
            }

    except subprocess.TimeoutExpired:
        notify(f"Timeout processando {video_id}", "error")
        return {"status": "error", "message": "Timeout - vídeo muito longo?"}
    except Exception as e:
        notify(f"Erro: {str(e)[:100]}", "error")
        return {"status": "error", "message": str(e)}


class WebhookHandler(BaseHTTPRequestHandler):
    """Handler HTTP para o webhook."""

    def _send_json(self, data: dict, status: int = 200):
        """Envia resposta JSON."""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_GET(self):
        """Health check."""
        if self.path == "/health":
            self._send_json({"status": "ok", "service": "yt-webhook"})
        else:
            self._send_json({"message": "POST /process com {url: 'youtube-url'}"})

    def do_POST(self):
        """Processa requisição."""
        if self.path != "/process":
            self._send_json({"error": "Use POST /process"}, 404)
            return

        # Lê body
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode()

        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            # Tenta parse como form data
            data = {k: v[0] for k, v in parse_qs(body).items()}

        url = data.get("url", "").strip()
        if not url:
            self._send_json({"error": "Campo 'url' é obrigatório"}, 400)
            return

        # Valida URL
        if not extract_video_id(url):
            self._send_json({"error": "URL do YouTube inválida"}, 400)
            return

        # Responde imediatamente e processa em background
        self._send_json({
            "status": "accepted",
            "message": "Processamento iniciado",
            "video_id": extract_video_id(url)
        }, 202)

        # Processa em thread separada
        options = {
            "note": data.get("note", True),
            "clip": data.get("clip")
        }
        thread = threading.Thread(target=process_video, args=(url, options))
        thread.start()

    def do_OPTIONS(self):
        """CORS preflight."""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        """Log customizado."""
        print(f"[{datetime.now().isoformat()}] {args[0]}")


def main():
    """Inicia o servidor."""
    server = HTTPServer(("0.0.0.0", PORT), WebhookHandler)
    print(f"[yt-webhook] Servidor iniciado em http://0.0.0.0:{PORT}")
    print(f"[yt-webhook] Output dir: {OUTPUT_DIR}")
    print(f"[yt-webhook] Processor: {PROCESSOR_SCRIPT}")
    print(f"[yt-webhook] OP_SERVICE_ACCOUNT_TOKEN: {'SET' if os.environ.get('OP_SERVICE_ACCOUNT_TOKEN') else 'NOT SET'}")
    print(f"[yt-webhook] OP_BIOMETRIC_UNLOCK_ENABLED: {os.environ.get('OP_BIOMETRIC_UNLOCK_ENABLED', 'NOT SET')}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[yt-webhook] Encerrando...")
        server.shutdown()


if __name__ == "__main__":
    main()
