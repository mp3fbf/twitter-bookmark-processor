#!/bin/bash
# Teste simples do op via launchd

exec > /tmp/op-launchd-test.log 2>&1

echo "=== Ambiente ==="
echo "Date: $(date)"
echo "UID: $(id -u)"

export PATH="/opt/homebrew/bin:$PATH"
export OP_BIOMETRIC_UNLOCK_ENABLED=false
export OP_SERVICE_ACCOUNT_TOKEN="$(cat $HOME/.secrets/op-token-yolo)"

echo ""
echo "=== Verificando sessão atual ==="
launchctl print self 2>&1 | grep -E "(session|type)" | head -5

echo ""
echo "=== op whoami ==="
/opt/homebrew/bin/op whoami &
PID=$!
sleep 3
if kill -0 $PID 2>/dev/null; then
    echo "TRAVOU após 3s"
    kill $PID 2>/dev/null
else
    wait $PID
    echo "Exit code: $?"
fi

echo ""
echo "=== Fim ==="
