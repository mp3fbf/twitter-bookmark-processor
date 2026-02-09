#!/bin/bash
# Executa teste do op via launchd na sessão GUI correta

PLIST_PATH="$HOME/Library/LaunchAgents/com.test.op-launchd.plist"
USER_UID=$(id -u)

PLIST_CONTENT='<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.test.op-launchd</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>'"$HOME"'/projects/twitter-bookmark-processor/deploy/test-op-launchd.sh</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>LimitLoadToSessionType</key>
    <string>Aqua</string>
</dict>
</plist>'

echo "=== Criando plist ==="
echo "$PLIST_CONTENT" > "$PLIST_PATH"

echo "=== Limpando logs ==="
rm -f /tmp/op-launchd-test.log

echo "=== Removendo agente anterior (se existir) ==="
launchctl bootout gui/$USER_UID/com.test.op-launchd 2>/dev/null

echo "=== Carregando na sessão GUI (gui/$USER_UID) ==="
launchctl bootstrap gui/$USER_UID "$PLIST_PATH"
BOOT_EXIT=$?
echo "launchctl bootstrap exit code: $BOOT_EXIT"

echo "=== Iniciando o serviço ==="
launchctl kickstart gui/$USER_UID/com.test.op-launchd 2>/dev/null

echo "=== Aguardando 12 segundos ==="
sleep 12

echo "=== Resultado do teste ==="
cat /tmp/op-launchd-test.log 2>/dev/null || echo "Log não encontrado"

echo ""
echo "=== Removendo ==="
launchctl bootout gui/$USER_UID/com.test.op-launchd 2>/dev/null
rm -f "$PLIST_PATH"
